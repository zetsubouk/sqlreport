# sqlreport - 轻量 SQL 报表工具
# Copyright (c) 2026 zetsubouk
# SPDX-License-Identifier: MIT

"""分析层（纯函数，无状态无 IO）：合计行/摘要指标/Top N/占比列/时间分桶/透视表/对比差值/分箱。

与 params.py 同风格：输入输出同构（cols, rows, coltypes），不触碰数据源与报表文件。
所有分析基于「最终返回行集」（docs/PLAN-v0.4-v0.7.md 决策 D2）。
"""
import datetime
import re

_NUM_COLTYPES = ("num",)


def _to_num(v):
    """单元格值 → float；空值/非数值返回 None（bool 不视为数值）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None


def _col_index(cols, name, what):
    if name not in cols:
        raise ValueError(f"{what}列不存在: {name}")
    return cols.index(name)


def total_row(cols, rows, coltypes, label="合计", label_col=0):
    """数值列求和生成合计行；无任何数值列返回 None。label 写入 label_col 单元格。
    派生列（名称以「占比%」「累计%」结尾）同样按数值列求和——占比列合计≈100，属预期口径。"""
    sums = [0.0] * len(cols)
    has_num = False
    for i, t in enumerate(coltypes):
        if t not in _NUM_COLTYPES:
            continue
        for r in rows:
            n = _to_num(r[i]) if i < len(r) else None
            if n is not None:
                sums[i] += n
                has_num = True
    if not has_num:
        return None
    out = ["" if t not in _NUM_COLTYPES else sums[i] for i, t in enumerate(coltypes)]
    if 0 <= label_col < len(cols):
        out[label_col] = label
    return out


_FN_NAMES = {"sum": "合计", "avg": "平均", "count": "计数", "max": "最大", "min": "最小"}


def summary_metrics(cols, rows, coltypes, metrics):
    """KPI 摘要指标。metrics: [{"col": 列名, "fn": "sum|avg|count|max|min", "label": 可选}]
    返回 [{"label", "value"}]；未知列跳过。
    count 计该列非空值个数（任意列型可用）；sum/avg/max/min 仅对 coltypes=num 列生效。"""
    out = []
    for m in metrics or []:
        col, fn = m.get("col", ""), m.get("fn", "sum")
        if col not in cols:
            continue
        i = cols.index(col)
        cells = [r[i] if i < len(r) else None for r in rows]
        if fn == "count":
            n = sum(1 for c in cells if c not in ("", None))
            out.append({"label": m.get("label") or col + "计数", "value": n})
            continue
        if coltypes[i] not in _NUM_COLTYPES:
            continue
        nums = [v for v in (_to_num(c) for c in cells) if v is not None]
        if not nums:
            continue
        if fn == "sum":
            val = sum(nums)
        elif fn == "avg":
            val = sum(nums) / len(nums)
        elif fn == "max":
            val = max(nums)
        elif fn == "min":
            val = min(nums)
        else:
            continue
        out.append({"label": m.get("label") or col + _FN_NAMES.get(fn, fn), "value": val})
    return out


def top_n_rows(cols, rows, coltypes, col, n=10, others="其他"):
    """按数值列 col 降序保留前 n 行，其余归并为 1 行（首列写 others 标签，数值列求和，其余留空）。
    行数 ≤ n 时按原行序浅拷贝返回。排序值并列时保持原行序（sorted 稳定）。
    col 缺失或非数值列抛 ValueError。"""
    i = _col_index(cols, col, "top_n ")
    if coltypes[i] not in _NUM_COLTYPES:
        raise ValueError(f"top_n 列必须是数值列: {col}")
    if len(rows) <= n:
        return list(rows)
    def key(r):
        v = _to_num(r[i]) if i < len(r) else None
        return v if v is not None else float("-inf")
    srt = sorted(rows, key=key, reverse=True)
    keep, rest = srt[:n], srt[n:]
    merged = [""] * len(cols)
    for j, t in enumerate(coltypes):
        if t not in _NUM_COLTYPES:
            continue
        s = 0.0
        for r in rest:
            v = _to_num(r[j]) if j < len(r) else None
            if v is not None:
                s += v
        merged[j] = s
    merged[0] = others
    return keep + [merged]


def add_share_columns(cols, rows, coltypes, col, digits=1):
    """追加「{col}占比%」「{col}累计%」两列。要求 rows 已按 col 降序（配合 top_n_rows 使用）。
    累计列基于四舍五入后的占比累加，末行可能与 100 有 ±0.1 舍入差（已知口径）。
    返回新 (cols, rows, coltypes)，不改入参；分母为 0 记 0.0。col 缺失或非数值列抛 ValueError。"""
    i = _col_index(cols, col, "share ")
    if coltypes[i] not in _NUM_COLTYPES:
        raise ValueError(f"share 列必须是数值列: {col}")
    total = 0.0
    for r in rows:
        v = _to_num(r[i]) if i < len(r) else None
        if v is not None:
            total += v
    cum = 0.0
    nrows = []
    for r in rows:
        v = _to_num(r[i]) if i < len(r) else None
        v = v if v is not None else 0.0
        share = round(v / total * 100, digits) if total else 0.0
        cum += share
        nrows.append(list(r) + [share, round(cum, digits)])
    return cols + [col + "占比%", col + "累计%"], nrows, list(coltypes) + ["num", "num"]


_DATE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def bucket_column(cols, rows, col, unit):
    """把日期列值替换为分桶标签：month→YYYY-MM，quarter→YYYYQn，week→ISO 年-W周号，day→原样返回 rows。
    值须以 YYYY-MM-DD 或 YYYY/MM/DD 开头；无法解析/日期非法的值原样保留。
    返回新 rows（month/quarter/week 模式不改入参行；day 模式返回原行对象列表）。"""
    if unit not in ("day", "week", "month", "quarter"):
        raise ValueError(f"bucket 单位不支持: {unit}")
    if unit == "day":
        return list(rows)
    i = _col_index(cols, col, "bucket ")
    out = []
    for r in rows:
        r2 = list(r)
        if i < len(r2):
            m = _DATE_RE.match(str(r2[i]).strip())
            if m:
                try:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    dt = datetime.date(y, mo, d)          # 校验日期合法性；非法抛 ValueError 原样保留
                    if unit == "month":
                        r2[i] = f"{y}-{mo:02d}"
                    elif unit == "quarter":
                        r2[i] = f"{y}Q{(mo - 1) // 3 + 1}"
                    else:
                        iso = dt.isocalendar()
                        r2[i] = f"{iso[0]}-W{iso[1]:02d}"
                except ValueError:
                    pass  # 非法日期原样保留
        out.append(r2)
    return out
