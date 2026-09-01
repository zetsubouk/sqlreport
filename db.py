"""数据层：数据源存取（mtime 懒加载 + 原子写）/ 建连（超时）/ 查询（fetchmany 分批 + 行硬顶）
/ 只读 SQL 校验 / TTL+LRU 查询缓存。

设计依据 docs/DESIGN-v0.2.md §3.2/§3.3/§3.4；跨源合并引擎 merge_union/merge_lookup 随 T03 加入。
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict

BASE = os.path.dirname(os.path.abspath(__file__))
DS_FILE = os.path.join(BASE, "datasources.json")
REPORTS_DIR = os.path.join(BASE, "reports")

MAX_ROWS_FETCH = 100_000   # 单数据集 fetch 硬顶（内存峰值预算 ~100MB，见 DESIGN §3.3）
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
    """执行只读查询，返回 (columns, rows, truncated)。

    fetchmany 分批拉取，硬顶 MAX_ROWS_FETCH 行；值统一转字符串、None → ""（延续 v0.1）。
    values 参数保留接口兼容（占位符替换已在上层 substitute 完成）。
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
        return cols, rows, truncated
    finally:
        conn.close()


class QueryCache:
    """TTL + LRU 进程内缓存（惰性过期，无后台线程）。key = f"{rid}:{sha1(canonical(params))}"

    预算（DESIGN §3.4）：单条目 ≤ 20000 行、条目数 ≤ 50、总行数 ≤ 100000（LRU 尾部驱逐）。
    """

    MAX_ENTRIES = 50
    MAX_ENTRY_ROWS = 20_000
    MAX_TOTAL_ROWS = 100_000

    def __init__(self):
        self._lock = threading.Lock()
        self._data = OrderedDict()  # key -> [expire_at, columns, rows]

    def make_key(self, rid, values):
        """参数 canonical 化：sorted(values.items()) 后 JSON 序列化再 sha1。"""
        canonical = json.dumps(sorted(values.items()), ensure_ascii=False)
        return f"{rid}:{hashlib.sha1(canonical.encode('utf-8')).hexdigest()}"

    def get(self, key):
        """命中且未过期返回 (columns, rows) 并刷新 LRU 位置；否则删除过期条目返回 None。"""
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expire_at, cols, rows = item
            if now > expire_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return cols, rows

    def put(self, key, columns, rows, ttl):
        """写入缓存；ttl<=0 不缓存，单条目超限不缓存，超预算 LRU 驱逐。"""
        if ttl <= 0 or len(rows) > self.MAX_ENTRY_ROWS:
            return
        with self._lock:
            self._data[key] = (time.time() + ttl, columns, rows)
            self._data.move_to_end(key)
            total = sum(len(v[2]) for v in self._data.values())
            while total > self.MAX_TOTAL_ROWS and len(self._data) > 1:
                _, (_, _, r) = self._data.popitem(last=False)
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
