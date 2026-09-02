# sqlreport - 轻量 SQL 报表工具
# Copyright (c) 2026 zetsubouk
# SPDX-License-Identifier: MIT

"""参数层（纯函数，无状态）：占位符转义/校验/替换 + 报表双格式归一化。

esc/build_values/substitute 自 server.py v0.1 原样迁移，正则与行为不得改动
（见 docs/DESIGN-v0.2.md §8 工程约定）；normalize_report 为双格式兼容唯一入口。
"""
import re

PH_RE = re.compile(r"\{\{([\w.]+)\}\}")


def esc(v):
    """拼接前转义（工具面向可信 SQL 作者，仅防参数值注入）"""
    s = str(v)
    return s.replace("'", "''")


def build_values(sql, params, given):
    """按参数定义校验输入，返回 {占位符: 已转义值}

    sql 参数保留 v0.1 签名兼容（当前未使用，占位符在 substitute 阶段消费）。
    date/number 参数带 range=True 时支持【精确↔范围】双模式：
      精确模式提交 {id} → {{id}}；
      范围模式提交 {id, id_2} → 展开 {{id_begin}}/{{id_begin}} 与 {{id.begin}}/{{id.end}}。
    """
    values = {}
    for p in params:
        pid = p["id"]
        g = given.get(pid, "").strip() if isinstance(given.get(pid), str) else str(given.get(pid, ""))
        t = p.get("type", "text")
        if t in ("daterange", "numrange"):
            g2 = given.get(pid + "_2", "").strip() if isinstance(given.get(pid + "_2"), str) else ""
            a, b = ("_begin", "_end") if t == "daterange" else ("_min", "_max")
            if g:
                values[pid + a] = esc(g)
                values[pid + "." + a[1:]] = esc(g)
            if g2:
                values[pid + b] = esc(g2)
                values[pid + "." + b[1:]] = esc(g2)
        elif t in ("date", "number") and p.get("range"):
            g2 = given.get(pid + "_2", "").strip() if isinstance(given.get(pid + "_2"), str) else ""
            if g2:
                a, b = ("_begin", "_end") if t == "date" else ("_min", "_max")
                if g:
                    values[pid + a] = esc(g)
                    values[pid + "." + a[1:]] = esc(g)
                values[pid + b] = esc(g2)
                values[pid + "." + b[1:]] = esc(g2)
            elif g != "":
                values[pid] = esc(g)
        elif g != "":
            values[pid] = esc(g)
    return values


def substitute(sql, values):
    """已填参数直接替换；未填占位符所在整行丢弃（实现可选条件）"""
    def rep(m):
        k = m.group(1)
        if k in values:
            return values[k]
        return "\x00DROP"
    lines = [ln for ln in PH_RE.sub(rep, sql).splitlines() if "\x00DROP" not in ln]
    return "\n".join(lines)


def normalize_report(report, ds_names=None):
    """报表 JSON 归一化为 datasets 数组（双格式兼容的唯一入口）。

    - 有 datasets → 原样使用（校验 name 唯一；ds_names 提供时校验数据源存在且未禁用）
    - 无 datasets 但有 sql → 视为 [{"name": "main", ...}]（旧格式兼容，零迁移）
    返回 [{"name":..., "ds":..., "sql":...}, ...]；不合法抛 ValueError（中文提示）。
    """
    raw = report.get("datasets")
    if raw:
        datasets = [{"name": str(d.get("name") or f"ds{i + 1}"),
                     "ds": d.get("ds", ""), "sql": d.get("sql", "")}
                    for i, d in enumerate(raw)]
    elif report.get("sql"):
        datasets = [{"name": "main", "ds": report.get("ds", ""), "sql": report["sql"]}]
    else:
        raise ValueError("报表缺少 SQL 定义（需要 sql 或 datasets）")
    names = [d["name"] for d in datasets]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError("数据集名称重复: " + ", ".join(dup))
    if ds_names is not None:
        for d in datasets:
            if d["ds"] not in ds_names:
                raise ValueError(f"数据集「{d['name']}」引用的数据源不存在或已禁用: {d['ds']}")
    return datasets


_BLOCK_TYPES = ("table", "pivot")
_PIVOT_REQUIRED = ("dataset", "row", "value")


def normalize_blocks(report):
    """报表 blocks 数组归一化（决策 D3/D10）：无 blocks → [{"type": "table"}]（零迁移）。

    块类型白名单 table/pivot；pivot 必填 dataset/row/value（保存期校验，中文 ValueError）。
    执行期才校验列名/列型（save 时无数据集结果）。原块对象原样透传（不改结构）。
    """
    blocks = report.get("blocks")
    if not blocks:
        return [{"type": "table"}]
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            raise ValueError("blocks 每项必须是对象")
        t = b.get("type")
        if t not in _BLOCK_TYPES:
            raise ValueError(f"不支持的块类型: {t}")
        if t == "pivot":
            missing = [k for k in _PIVOT_REQUIRED if not b.get(k)]
            if missing:
                raise ValueError("pivot 块缺少必填键: " + ", ".join(missing))
        out.append(b)
    return out
