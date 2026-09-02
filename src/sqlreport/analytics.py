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


_PIVOT_AGGS = ("sum", "count", "avg", "max", "min")


def pivot(cols, rows, coltypes, row, col, value, agg="sum",
          row_total=True, col_total=True, max_cols=50):
    """透视表：以 row 列为行维度、col 列为列维度、value 列按 agg 聚合生成交叉表。

    - 列头：col 维度去重、按首现顺序；去重后（行维度同理）> max_cols 抛中文 ValueError
      （提示先在 SQL 层归类维度列）。
    - 行序：row 维度值排序——列全为数值则数值升序，否则按 str 排序；排序并列保持首现顺序
      （sorted 稳定）；维度 None 与 "" 归并为 ""。
    - 数据格：value 列按 agg（sum/count/avg/max/min）聚合；count 计非空值数；无数据格记 ""；
      value 列非 num（且 agg≠count）抛 ValueError。
    - row_total=True 追加「合计」列；col_total=True 追加「总计」行。
      avg 已知口径：合计/总计为基于底层原始值重算的均值，格内均值之和 ≠ 该均值。
    返回 (pcols, prows, ptypes)，不改入参。"""
    if agg not in _PIVOT_AGGS:
        raise ValueError(f"透视表不支持的聚合方式: {agg}")
    ri = _col_index(cols, row, "pivot ")
    ci = _col_index(cols, col, "pivot ")
    vi = _col_index(cols, value, "pivot ")
    if coltypes[vi] not in _NUM_COLTYPES and agg != "count":
        raise ValueError(f"透视表 value 列必须是数值列: {value}")
    # 维度归并（None 与 "" → ""）与首现序去重
    col_vals, seen_c = [], set()
    row_keys, seen_r = [], set()
    for r in rows:
        cv = "" if (ci >= len(r) or r[ci] in ("", None)) else r[ci]
        if cv not in seen_c:
            seen_c.add(cv)
            col_vals.append(cv)
        rv = "" if (ri >= len(r) or r[ri] in ("", None)) else r[ri]
        if rv not in seen_r:
            seen_r.add(rv)
            row_keys.append(rv)
    if len(col_vals) > max_cols:
        raise ValueError(f"透视表列数超过上限 {max_cols}，请在 SQL 层先归类维度列")
    if len(row_keys) > max_cols:
        raise ValueError(f"透视表行数超过上限 {max_cols}，请在 SQL 层先归类维度列")
    # 行序：全数值→数值升序；否则按 str；sorted 稳定保持首现序
    if all(_to_num(k) is not None for k in row_keys):
        row_order = sorted(row_keys, key=_to_num)
    else:
        row_order = sorted(row_keys, key=str)
    # 聚合：raw 存原始数值（avg 合计/总计需重算）；count 计非空值数
    raw, cnt = {}, {}
    row_raw, row_cnt = {}, {}
    col_raw, col_cnt = {}, {}
    grand_raw, grand_cnt = [], 0
    for r in rows:
        rv = "" if (ri >= len(r) or r[ri] in ("", None)) else r[ri]
        cv = "" if (ci >= len(r) or r[ci] in ("", None)) else r[ci]
        v = r[vi] if vi < len(r) else None
        if agg == "count":
            if v not in ("", None):
                key = (rv, cv)
                cnt[key] = cnt.get(key, 0) + 1
                row_cnt[rv] = row_cnt.get(rv, 0) + 1
                col_cnt[cv] = col_cnt.get(cv, 0) + 1
                grand_cnt += 1
        else:
            n = _to_num(v)
            if n is not None:
                key = (rv, cv)
                raw.setdefault(key, []).append(n)
                row_raw.setdefault(rv, []).append(n)
                col_raw.setdefault(cv, []).append(n)
                grand_raw.append(n)

    def _agg(vals):
        if agg == "sum":
            return sum(vals)
        if agg == "avg":
            return sum(vals) / len(vals)
        if agg == "max":
            return max(vals)
        return min(vals)

    def _cell(vals):
        return _agg(vals) if vals is not None else ""

    def _total(vals, n):
        if agg == "count":
            return n if n else ""
        return _cell(vals)

    pcols = [row] + list(col_vals)
    ptypes = [coltypes[ri]] + ["num"] * len(col_vals)
    prows = []
    for rv in row_order:
        cells = [_total(raw.get((rv, cv)), cnt.get((rv, cv))) for cv in col_vals]
        if row_total:
            cells.append(_total(row_raw.get(rv), row_cnt.get(rv)))
        prows.append([rv] + cells)
    if col_total:
        trow = ["总计"] + [_total(col_raw.get(cv), col_cnt.get(cv)) for cv in col_vals]
        if row_total:
            trow.append(_total(grand_raw if grand_raw else None, grand_cnt))
        prows.append(trow)
    if row_total:
        pcols = pcols + ["合计"]
        ptypes = ptypes + ["num"]
    return pcols, prows, ptypes


def diff_merge(base_cols, base_rows, right_cols, right_rows, on, metric, label="",
               base_types=None):
    """对比差值（环比语义）：以 on 列为键对齐 base 与 right，为 base 追加
    「{metric}({label})差值」「{metric}({label})增长率%」两列。

    - 对齐复用 db.merge_lookup 的哈希思路但独立实现（保持本模块无 db 依赖）：右侧建 dict，
      键取 str 元组，重复键取首条（后写不覆盖）；行序保持 base 首现序，右侧缺失键的行
      差值/增长率留空 ""（左对齐口径，同 merge_lookup）。
    - rate = round((b-r)/r*100, 1)；r 为 0 或缺失（含 b 缺失）时增长率/差值留空 ""。
    - on 列在 base/right 任一缺失抛中文 ValueError；base_types 提供时校验 metric 列须为 num。
    - metric 列名不在 base_cols 时视为 base 末列（比较场景主表末列为指标列，见 server 集成）。
    返回 (cols, rows, types)，不改入参。"""
    on = [k for k in (on or []) if k]
    if not on:
        raise ValueError("compare 需要指定 on 关联键（可多列）")
    bidx, ridx = [], []
    for k in on:
        if k not in base_cols:
            raise ValueError(f"compare 主表缺少关联键列: {k}")
        if k not in right_cols:
            raise ValueError(f"compare 右侧数据集缺少关联键列: {k}")
        bidx.append(base_cols.index(k))
        ridx.append(right_cols.index(k))
    bmi = base_cols.index(metric) if metric in base_cols else len(base_cols)
    rmi = right_cols.index(metric) if metric in right_cols else len(right_cols)
    if base_types is not None and bmi < len(base_types) and base_types[bmi] != "num":
        raise ValueError(f"compare metric 列必须是数值列: {metric}")
    # 右侧哈希：键取 str 元组；重复键取首条
    right_map = {}
    for r in right_rows:
        key = tuple(str(r[i]) for i in ridx)
        if key not in right_map:
            right_map[key] = _to_num(r[rmi]) if rmi < len(r) else None
    out = []
    for r in base_rows:
        key = tuple(str(r[i]) for i in bidx)
        bv = _to_num(r[bmi]) if bmi < len(r) else None
        rv = right_map.get(key)
        if bv is None or rv is None:
            diff = rate = ""
        else:
            diff = bv - rv
            rate = round((bv - rv) / rv * 100, 1) if rv else ""
        row = [r[i] for i in range(len(base_cols))]
        if metric not in base_cols:
            row.append(r[bmi] if bmi < len(r) else "")
        row.append(diff)
        row.append(rate)
        out.append(row)
    out_cols = list(base_cols)
    if metric not in base_cols:
        out_cols.append(metric)
    out_cols += [f"{metric}({label})差值", f"{metric}({label})增长率%"]
    out_types = list(base_types) if base_types is not None else ["str"] * len(base_cols)
    if metric not in base_cols:
        out_types.append("num")
    out_types += ["num", "num"]
    return out_cols, out, out_types


def _bin_num(v):
    """分箱边界标签：整数值去小数尾（1.0→1），否则保留（含 6 位舍入去浮点噪声）。"""
    v = round(v, 6)
    return str(int(v)) if float(v).is_integer() else str(v)


def bin_numeric(cols, rows, coltypes, col, bins=10):
    """数值等宽分箱统计表：把数值列 col 划分为 bins 个等宽区间，返回 [区间, 计数, 占比%] 表。

    - 空值行不计入（不报错）；列值全为空（无有效数值）抛中文 ValueError。
    - hi == lo 退化为单区间；左闭右开、末箱右闭；区间标签 "a ~ b"。
    返回 (cols, rows, types) = (["区间","计数","占比%"], [[区间,计数,占比%],...], ["str","num","num"])，
    不改入参。"""
    i = _col_index(cols, col, "bin ")
    if coltypes[i] not in _NUM_COLTYPES:
        raise ValueError(f"bin 列必须是数值列: {col}")
    vals = [_to_num(r[i]) for r in rows if i < len(r)]
    vals = [v for v in vals if v is not None]
    if not vals:
        raise ValueError(f"分箱列无有效数值: {col}")
    lo, hi = min(vals), max(vals)
    if hi == lo:
        bounds = [(lo, hi, True)]
    else:
        n = max(int(bins), 1)
        width = (hi - lo) / n
        bounds = [(lo + k * width, lo + (k + 1) * width, False) for k in range(n)]
        bounds[-1] = (bounds[-1][0], bounds[-1][1], True)  # 末箱右闭
    total = len(vals)
    out = []
    for a, b, right_closed in bounds:
        if right_closed:
            cnt = sum(1 for v in vals if a <= v <= b)
        else:
            cnt = sum(1 for v in vals if a <= v < b)
        pct = round(cnt / total * 100, 1)
        out.append([f"{_bin_num(a)} ~ {_bin_num(b)}", cnt, pct])
    return ["区间", "计数", "占比%"], out, ["str", "num", "num"]
