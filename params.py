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
                values[pid + "." + a[1:]] = esc(g)  # 同时支持 {{d_begin}} 与 {{d.begin}}
            if g2:
                values[pid + b] = esc(g2)
                values[pid + "." + b[1:]] = esc(g2)
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
