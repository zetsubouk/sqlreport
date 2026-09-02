# sqlreport - 轻量 SQL 报表工具
# Copyright (c) 2026 zetsubouk
# SPDX-License-Identifier: MIT

"""分析层（纯函数，无状态无 IO）：合计行/摘要指标/Top N/占比列/时间分桶/透视表/对比差值/分箱。

与 params.py 同风格：输入输出同构（cols, rows, coltypes），不触碰数据源与报表文件。
所有分析基于「最终返回行集」（docs/PLAN-v0.4-v0.7.md 决策 D2）。
"""

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
