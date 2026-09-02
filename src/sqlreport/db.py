# sqlreport - 轻量 SQL 报表工具
# Copyright (c) 2026 zetsubouk
# SPDX-License-Identifier: MIT

"""数据层：数据源存取（mtime 懒加载 + 原子写）/ 建连（超时）/ 查询（fetchmany 分批 + 行硬顶）
/ 只读 SQL 校验 / 跨源合并引擎（union/lookup）/ TTL+LRU 查询缓存。

设计依据 docs/DESIGN-v0.2.md §3.2/§3.3/§3.4。
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DS_FILE = os.path.join(BASE, "datasources.json")
REPORTS_DIR = os.path.join(BASE, "reports")

MAX_ROWS_FETCH = 100_000   # 单数据集 fetch 硬顶（内存峰值预算 ~100MB，见 DESIGN §3.3）
LOOKUP_RIGHT_MAX = 100_000  # lookup 右表（建 dict 侧）行数上限
FETCH_BATCH = 1000         # fetchmany 批大小
DEFAULT_TIMEOUT = 30       # 数据源缺省超时（秒）


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _connect_cfg(ds):
    """按数据源配置建连（含超时），供 connect() 与 test_connection() 复用。"""
    t = ds["type"]
    timeout = int(ds.get("timeout") or DEFAULT_TIMEOUT)
    if t == "sqlite":
        path = ds["path"]
        if not os.path.isabs(path):
            path = os.path.join(BASE, path)
        return sqlite3.connect(path, timeout=5)
    if t == "mysql":
        import pymysql
        return pymysql.connect(host=ds["host"], port=int(ds.get("port", 3306)),
                               user=ds["user"], password=ds["password"],
                               database=ds["database"], charset="utf8mb4",
                               connect_timeout=5, read_timeout=timeout, write_timeout=timeout)
    if t == "sqlserver":
        import pyodbc
        dsn = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={ds['host']},{ds.get('port', 1433)};"
               f"DATABASE={ds['database']};UID={ds['user']};PWD={ds['password']}")
        cnxn = pyodbc.connect(dsn, timeout=timeout)
        cnxn.timeout = timeout  # 查询超时（秒）
        return cnxn
    raise ValueError(f"不支持的数据源类型: {t}")


class DatasourceStore:
    """datasources.json 存取：mtime 懒加载免重启 + 临时文件 os.replace 原子写 + 锁串行化。

    兼容规则：键名 `_` 前缀等价禁用（旧语义保留）；`enabled: false` 与其等价，
    两者任一存在即视为禁用（is_enabled）。
    """

    def __init__(self, path=DS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._cache = None
        self._mtime = None

    # ---- 读 ----
    def _load(self):
        """mtime 变化才重读解析（调用方需持锁或接受读放大的低频竞态）。"""
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            mtime = None
        if self._cache is None or mtime != self._mtime:
            self._cache = load_json(self.path) if mtime is not None else {}
            self._mtime = mtime
        return self._cache

    def load(self):
        with self._lock:
            return dict(self._load())

    def get(self, name):
        with self._lock:
            return self._load().get(name)

    def is_enabled(self, name, cfg=None):
        if cfg is None:
            cfg = self.get(name)
        if cfg is None:
            return False
        return not name.startswith("_") and cfg.get("enabled", True) is not False

    def visible_names(self):
        """可用数据源名单（旧 `_` 前缀隐藏 + enabled:false 双重兼容）。"""
        with self._lock:
            dss = self._load()
        return [n for n, c in dss.items() if self.is_enabled(n, c)]

    # ---- 写 ----
    def _write(self, dss):
        """同目录临时文件 + os.replace 原子替换（调用方需持锁）。"""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dss, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        self._cache = dss
        self._mtime = os.stat(self.path).st_mtime

    def save(self, name, cfg):
        with self._lock:
            dss = self._load()
            dss[name] = cfg
            self._write(dss)

    def delete(self, name):
        with self._lock:
            dss = self._load()
            if name not in dss:
                raise ValueError(f"数据源不存在: {name}")
            del dss[name]
            self._write(dss)

    def toggle(self, name, enabled):
        """启用/禁用。禁用写 enabled:false；对旧 `_` 前缀条目启用时去除前缀并置 enabled:true。"""
        with self._lock:
            dss = self._load()
            if name not in dss:
                raise ValueError(f"数据源不存在: {name}")
            cfg = dict(dss[name])
            if enabled and name.startswith("_"):
                del dss[name]
                name = name[1:]
            cfg["enabled"] = bool(enabled)
            dss[name] = cfg
            self._write(dss)
            return name

    # ---- 辅助 ----
    def referenced_by(self, name):
        """扫描 reports/*.json，返回引用该数据源的报表 id 列表（删除前引用检查）。"""
        refs = []
        if not os.path.isdir(REPORTS_DIR):
            return refs
        for fn in sorted(os.listdir(REPORTS_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                r = load_json(os.path.join(REPORTS_DIR, fn))
            except Exception:
                continue  # 损坏文件不阻塞引用检查
            used = r.get("ds") == name or any(
                d.get("ds") == name for d in r.get("datasets", []))
            if used:
                refs.append(fn[:-5])
        return refs

    def test_connection(self, ds):
        """仅建连不执行 SQL。返回 (ok, ms, error)。"""
        t0 = time.time()
        try:
            conn = _connect_cfg(ds)
            conn.close()
            return True, int((time.time() - t0) * 1000), ""
        except Exception as e:
            return False, int((time.time() - t0) * 1000), str(e)


def connect(ds_name):
    """按名称建连；不存在或已禁用抛 ValueError（中文提示）。"""
    ds = DS_STORE.get(ds_name)
    if ds is None or ds_name.startswith("_"):
        raise ValueError(f"数据源不存在: {ds_name}")
    if not DS_STORE.is_enabled(ds_name, ds):
        raise ValueError(f"数据源已禁用: {ds_name}")
    return _connect_cfg(ds)


def _is_num(v):
    """宽松数字判定：整数/小数/科学计数（含正负号）。"""
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", v))


def _is_date(v):
    """宽松日期判定：YYYY-MM-DD[ HH:MM[:SS]] 或 ISO 格式。"""
    return bool(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{1,2}(?::\d{1,2}(?:\.\d+)?)?)?", v))


def _column_types(cols, rows):
    """逐列推断类型（P0-2）：扫描每列前 20 个非空值，全数字 → num，全日期 → date，否则 str。"""
    types = []
    for i in range(len(cols)):
        samples = [r[i] for r in rows[:20] if r[i] != ""]
        if samples and all(_is_num(v) for v in samples):
            types.append("num")
        elif samples and all(_is_date(v) for v in samples):
            types.append("date")
        else:
            types.append("str")
    return types


def sql_is_readonly(sql):
    """只读校验：去注释后必须恰一条语句，且首词为 SELECT/WITH。"""
    s = re.sub(r"--[^\n]*", " ", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    stmts = [x for x in s.split(";") if x.strip()]
    if len(stmts) != 1:
        return False
    head = stmts[0].lstrip().split(None, 1)
    return bool(head) and head[0].upper() in ("SELECT", "WITH")


def run_query(ds_name, sql, values=None):
    """执行只读查询，返回 (columns, rows, truncated, coltypes)。

    fetchmany 分批拉取，硬顶 MAX_ROWS_FETCH 行；值统一转字符串、None → ""（延续 v0.1）。
    coltypes 为逐列类型标签（num/date/str，P0-2）。values 参数保留接口兼容。
    """
    if not sql_is_readonly(sql):
        raise ValueError("仅允许只读查询（单条 SELECT/WITH 语句）")
    conn = connect(ds_name)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows, truncated = [], False
        while True:
            batch = cur.fetchmany(FETCH_BATCH)
            if not batch:
                break
            for r in batch:
                rows.append(tuple("" if v is None else str(v) for v in r))
            if len(rows) >= MAX_ROWS_FETCH:
                rows = rows[:MAX_ROWS_FETCH]
                truncated = True
                break
        return cols, rows, truncated, _column_types(cols, rows)
    finally:
        conn.close()


def merge_union(results):
    """纵向合并（union）：以第一个数据集列序为基准，按列名对齐拼接，缺失列填 ""。

    results: [(columns, rows, coltypes), ...]；逐数据集流式转换，峰值内存 ≈ 最大单数据集行数。
    列类型取第一个数据集对应的标签（缺失列保持基表类型）。
    """
    if not results:
        return [], [], []
    base_cols = list(results[0][0])
    base_types = list(results[0][2])
    idx0 = {c: i for i, c in enumerate(base_cols)}
    merged = []
    for cols, rows, _types in results:
        idx = {c: i for i, c in enumerate(cols)}
        order = [(idx[c] if c in idx else None) for c in base_cols]
        for r in rows:
            merged.append(tuple("" if i is None else r[i] for i in order))
    return base_cols, merged, base_types


def merge_lookup(base_cols, base_rows, right_cols, right_rows, on, cols=None,
                 base_types=None, right_types=None):
    """横向关联（lookup，类似 left join 取值）：右表建 dict 哈希，左表逐行 O(n) 查。

    - on: 关联键列名（可多列）；cols: 从右表取的列（缺省 = 右表除键外全部列）
    - 右表行数上限 LOOKUP_RIGHT_MAX，超限报错（提示 SQL 层先聚合/过滤）
    - 右表重复键取首条（dict 后写不覆盖）；列类型缺省视为 str
    """
    on = [k for k in (on or []) if k]
    if not on:
        raise ValueError("lookup 关联需要指定 on 关联键（可多列）")
    if len(right_rows) > LOOKUP_RIGHT_MAX:
        raise ValueError(f"关联表超 {LOOKUP_RIGHT_MAX} 行，请在 SQL 层先聚合/过滤")
    bidx = {c: i for i, c in enumerate(base_cols)}
    ridx = {c: i for i, c in enumerate(right_cols)}
    for k in on:
        if k not in bidx:
            raise ValueError(f"主数据集缺少关联键列: {k}")
        if k not in ridx:
            raise ValueError(f"关联数据集缺少关联键列: {k}")
    take = [c for c in (cols or [c for c in right_cols if c not in on]) if c]
    for c in take:
        if c not in ridx:
            raise ValueError(f"关联数据集缺少取值列: {c}")
    right_map = {}
    for r in right_rows:
        key = tuple(r[ridx[k]] for k in on)
        if key not in right_map:
            right_map[key] = tuple(r[ridx[c]] for c in take)
    out_rows = []
    pad = ("",) * len(take)
    for r in base_rows:
        vals = right_map.get(tuple(r[bidx[k]] for k in on), pad)
        out_rows.append(tuple(r) + vals)
    base_types = (base_types or ["str"] * len(base_cols))
    right_types = (right_types or ["str"] * len(right_cols))
    out_types = list(base_types) + [right_types[ridx[c]] for c in take]
    return base_cols + list(take), out_rows, out_types


class QueryCache:
    """TTL + LRU 进程内缓存（惰性过期，无后台线程）。key = f"{rid}:{sha1(canonical(params))}"

    预算（DESIGN §3.4）：单条目 ≤ 20000 行、条目数 ≤ 50、总行数 ≤ 100000（LRU 尾部驱逐）。
    """

    MAX_ENTRIES = 50
    MAX_ENTRY_ROWS = 20_000
    MAX_TOTAL_ROWS = 100_000

    def __init__(self):
        self._lock = threading.Lock()
        self._data = OrderedDict()  # key -> [expire_at, columns, rows, truncated, coltypes]

    def make_key(self, rid, values):
        """参数 canonical 化：sorted(values.items()) 后 JSON 序列化再 sha1。"""
        canonical = json.dumps(sorted(values.items()), ensure_ascii=False)
        return f"{rid}:{hashlib.sha1(canonical.encode('utf-8')).hexdigest()}"

    def get(self, key):
        """命中且未过期返回 (columns, rows, truncated, coltypes) 并刷新 LRU 位置；否则删除过期条目返回 None。"""
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expire_at, cols, rows, truncated, coltypes = item
            if now > expire_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return cols, rows, truncated, coltypes

    def put(self, key, columns, rows, ttl, truncated=False, coltypes=None):
        """写入缓存；ttl<=0 不缓存，单条目超限不缓存，超预算 LRU 驱逐。
        truncated/coltypes 一并缓存，避免命中后丢失状态（对 DESIGN §3.4 的补充）。"""
        if ttl <= 0 or len(rows) > self.MAX_ENTRY_ROWS:
            return
        with self._lock:
            self._data[key] = (time.time() + ttl, columns, rows, truncated, coltypes)
            self._data.move_to_end(key)
            total = sum(len(v[2]) for v in self._data.values())
            while total > self.MAX_TOTAL_ROWS and len(self._data) > 1:
                _, (_, _, r, _, _) = self._data.popitem(last=False)
                total -= len(r)
            while len(self._data) > self.MAX_ENTRIES:
                self._data.popitem(last=False)

    def invalidate(self, rid):
        """清除指定报表的全部缓存条目（/save 保存成功后调用）。"""
        with self._lock:
            prefix = rid + ":"
            for k in [k for k in self._data if k.startswith(prefix)]:
                del self._data[k]


# 模块级单例：数据源存取 与 查询缓存
DS_STORE = DatasourceStore(DS_FILE)
CACHE = QueryCache()
