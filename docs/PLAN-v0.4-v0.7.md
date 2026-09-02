# 统计与分析报表开发计划（v0.4 → v0.7）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不引入任何图表的前提下，把 sqlreport 从"取数工具"升级为"统计与分析报表工具"：Python 侧纯函数聚合引擎 + 分析块化报表 Schema + 交付加固（真 xlsx / 打印 / 鉴权 / 分组）。

**Architecture:** 新增 `src/sqlreport/analytics.py` 纯函数分析层（对齐 `params.py` 风格：输入输出同构、无 IO 无状态）；`server.py` 的 `_execute_report` 在合并/截断之后调用分析层，`/q` 响应只增不改（新增 `total_row`/`summary`/`blocks` 键，旧键与旧报表零迁移）；v0.5 拆出 `views_report.py` 解决 server.py 膨胀。

**Tech Stack:** Python 3.10 标准库（unittest / http.server / sqlite3 / zipfile / hmac），零新依赖；前端 vanilla JS（沿用 server.py 内联 PAGE 模板）。

**参考文档:** `docs/DEVELOPMENT.md`（开发规范）、`docs/DESIGN-v0.2.md` §8（工程约定）、`PLAN.md`（本计划承接其 P1/P2 项）、`CHANGELOG.md`。

**测试基架速记（写测试前必读）:** `tests/test_server.py` 的 `req()` 返回 `(status, data)`；报表保存用基架现成辅助 `self.save_report(rec, rid)`（rid 显式指定，`/save` 缺省 rid 会生成随机 id 且 name 不参与 URL 定位）；URL 一律用 ASCII id（字面 CJK 路径会被 http.client 请求行 ASCII 编码拒绝，CJK 支持是 Task 22 的 `unquote` 改造内容）。

---

## 0. 范围与不做清单

**做**：合计行、KPI 摘要区、Top N+其他归并、占比/累计占比列、时间分桶、透视表（小计/总计/占比）、对比差值（环比语义）、数值分箱统计表、保存视图、真 .xlsx 导出、打印样式、token 鉴权、报表分组目录、server 视图拆分。

**不做（本轮锁死，PR 不接受）**：任何图表/图形渲染（含 sparkline、in-cell 数据条）、拖拽式 BI 设计器、跨库 SQL 下推、定时调度组件、SQL 层分页（用户已拍板不做）、新第三方依赖。

## 1. 架构决策（锁定，实现中不得偏离）

| # | 决策 | 理由 |
|---|------|------|
| D1 | 分析能力 = 纯函数层 `analytics.py`，统一签名前缀 `(cols, rows, coltypes, ...)`，输出同构 | 零成本单测；与 params.py 哲学一致；不触碰数据源与报表文件 |
| D2 | 所有分析在「最终返回行集」（合并+截断后）上进行；缓存命中时基于缓存行重算。**豁免**：含非 table 块（pivot/hist）的报表不使用结果缓存（见 Task 11 Step 3.5），因缓存仅存主表行、命中路径拿不到各 dataset 原始结果 | 缓存 schema 零变更；口径诚实（分析范围=所见范围=导出范围）；多块报表的缓存豁免是显式锁定决策 |
| D3 | 报表 JSON 顶层新增可选键 `total / summary / top_n / share / bucket`（v0.4）、`blocks`（v0.5）、`views`（v0.6）；缺省完全兼容 | 复刻 `normalize_report` 双格式兼容思路，旧报表零迁移 |
| D4 | `/q` 响应只增不改：所有响应固定含 `total_row`（None 或行数组）与 `summary`（数组，缺省 `[]`）；`blocks`（v0.5 起固定输出）；旧键 `{columns, rows, coltypes, truncated, cached, elapsed_ms}` 含义不变 | 响应形状稳定，前端无需分支猜测；旧前端忽略新键不受影响 |
| D5 | 分析执行顺序：`bucket → compare(M3) → top_n → share → total/summary` | 占比列依赖排序后的行序；合计/摘要基于归并后的行 |
| D6 | 新增根目录 shim `analytics.py` / `xlsx.py`（薄 shim，仿根 `params.py` 的 5 行转发形态） | 与 tests 现有 `from params import ...` 导入约定一致 |
| D7 | 版本号只改两处：`src/sqlreport/__init__.py` 与 `pyproject.toml`；M1 中一次性把 `nav()` 硬编码 "v0.3.0" 改为读 `__version__` | 消除三处同步的隐患 |
| D8 | 包内导入风格统一 `from sqlreport.x import ...`（与现有 `server.py` 头部一致） | 保证 `pip install -e .` 与 shim 两种运行方式都可用 |
| D9 | `coltypes` 三态：`num / date / str`；分析函数只信任 `num` 标记，单元格值仍用 `_to_num` 防御性转换 | 类型标记可能被报表 `columns` 覆盖写错 |
| D10 | `_save` 报表保存采用**显式键白名单**（现状如此）；每个新顶层键落地时必须同步加入白名单并做最小结构校验 | 现实约束：白名单外的键会被静默丢弃（Task 5/11/15/17 均含此步） |

## 2. 文件结构总览

| 文件 | 动作 | 职责 |
|------|------|------|
| `src/sqlreport/analytics.py` | 新建（M1） | 纯函数分析层：total_row / summary_metrics / top_n_rows / add_share_columns / bucket_column / pivot(M2) / diff_merge(M3) / bin_numeric(M3) |
| `analytics.py`（根） | 新建（M1） | 薄 shim，转发 `sqlreport.analytics` |
| `src/sqlreport/params.py` | 修改（M2/M3） | 追加 `normalize_blocks`、白名单校验 |
| `src/sqlreport/views_report.py` | 新建（M2） | 页面模板与渲染函数（PAGE/nav/page/esc_html/_rel_time + 列表/编辑器/查看页），自 server.py 迁出 |
| `src/sqlreport/xlsx.py` | 新建（M4） | 手写最小 xlsx 写出器（zip + inlineStr） |
| `xlsx.py`（根） | 新建（M4） | 薄 shim，转发 `sqlreport.xlsx` |
| `src/sqlreport/server.py` | 修改（各里程碑） | `_execute_report` 集成分析层；`/q` 响应扩展；`_save` 白名单扩展；`_export` 扩展；路由加 token/分组/unquote |
| `tests/test_analytics.py` | 新建（M1） | analytics 纯函数单测 |
| `tests/test_server.py` | 修改（M1 起） | 集成测试沿用现有 ServerTestCase 基架 |
| `tests/test_xlsx.py` | 新建（M4） | xlsx 写出器单测 |
| `src/sqlreport/__init__.py`、`pyproject.toml`、`CHANGELOG.md` | 修改 | 版本收尾（每个里程碑一次） |

## 3. 里程碑总览

| 里程碑 | 版本 | 内容 | 对应旧 PLAN.md |
|--------|------|------|----------------|
| M1 统计基础 | v0.4.0 | analytics 骨架 + 合计行 + KPI 摘要 + Top N/占比 + 时间分桶 + 服务端/查看页/导出集成 + 点列头排序 + 口径回显 | P1-1、P1-3 |
| M2 交叉分析 | v0.5.0 | pivot 透视表 + blocks 化 Schema + 查看页/导出多块渲染 + server.py 视图拆分 | P1-2 |
| M3 对比与维度 | v0.6.0 | diff_merge 对比差值（环比语义）+ 数值分箱表 + 保存视图 | 新增 |
| M4 交付加固 | v0.7.0 | 真 .xlsx 导出 + 打印样式 + token 鉴权 + 报表分组目录 | P2-2、P2-3、P2-5 |

**依赖关系**：M1 是所有里程碑的前置；M4 依赖 M1+M2（Task 19 的 xlsx 导出按 blocks 构造 sheet）；M2 与 M3 互相独立可调序。每个里程碑结束执行「版本收尾任务」，全部通过后才进入下一个。

**常用命令**（均在项目根 `/Users/zetsubouk/Documents/TraeWork/SqlReport` 执行）：

```bash
python -m unittest discover -s tests -v     # 全量测试
python -m unittest tests.test_analytics -v  # 单文件
python -m py_compile src/sqlreport/*.py     # 语法检查
python server.py 8765                       # 启动服务（改代码后必须重启再验证）
```

---

# M1 — 统计基础（v0.4.0）

### Task 1: analytics.py 骨架 + `_to_num` + `total_row`

**Files:**
- Create: `src/sqlreport/analytics.py`
- Create: `analytics.py`（根 shim）
- Create: `tests/test_analytics.py`

- [ ] **Step 1: 写失败测试**（`tests/test_analytics.py`）

```python
#!/usr/bin/env python3
"""analytics.py 纯函数单元测试（零依赖）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import total_row, _to_num

COLS = ["区域", "金额", "单数"]
TYPES = ["str", "num", "num"]
ROWS = [["华东", 100.5, 2], ["华北", 200.0, 3], ["华东", 50.0, 1]]


class TestToNum(unittest.TestCase):
    def test_num_types(self):
        self.assertEqual(_to_num(3), 3.0)
        self.assertEqual(_to_num(2.5), 2.5)
        self.assertIsNone(_to_num(None))
        self.assertIsNone(_to_num(True))
        self.assertIsNone(_to_num("abc"))

    def test_str_with_comma(self):
        self.assertEqual(_to_num("1,234.5"), 1234.5)


class TestTotalRow(unittest.TestCase):
    def test_sum_num_cols(self):
        r = total_row(COLS, ROWS, TYPES)
        self.assertEqual(r[0], "合计")
        self.assertEqual(r[1], 350.5)
        self.assertEqual(r[2], 6.0)

    def test_label_and_label_col(self):
        r = total_row(COLS, ROWS, TYPES, label="总计", label_col=1)
        self.assertEqual(r[1], "总计")

    def test_no_num_cols_returns_none(self):
        self.assertIsNone(total_row(["a"], [["x"]], ["str"]))

    def test_input_not_mutated(self):
        snapshot = [list(r) for r in ROWS]
        total_row(COLS, ROWS, TYPES)
        self.assertEqual(ROWS, snapshot)

    def test_bad_cell_skipped(self):
        rows = [["华东", "bad", 1], ["华北", 10.0, 2]]
        r = total_row(COLS, rows, TYPES)
        self.assertEqual(r[1], 10.0)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m unittest tests.test_analytics -v
```
预期：FAIL/ERROR（`ModuleNotFoundError: No module named 'analytics'`）

- [ ] **Step 3: 最小实现**（`src/sqlreport/analytics.py`）

```python
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
```

根 shim `analytics.py`（原样照抄，注意只改包名）：

```python
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "src"))
import sqlreport.analytics as _src
_sys.modules[__name__] = _src
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m unittest tests.test_analytics -v
```
预期：全部 PASS；再跑 `python -m py_compile src/sqlreport/*.py` 无输出。

- [ ] **Step 5: Commit**

```bash
git add src/sqlreport/analytics.py analytics.py tests/test_analytics.py
git commit -m "feat(analytics): 分析层骨架与合计行 total_row"
```

### Task 2: `summary_metrics` 摘要指标

**Files:**
- Modify: `src/sqlreport/analytics.py`
- Modify: `tests/test_analytics.py`

**口径锁定：`count` = 该列非空值个数（`r[i] not in ("", None)`），与数值类型无关（可对任意列计数，如文本 ID 列）；sum/avg/max/min 仅对 coltypes=num 的列生效。**

- [ ] **Step 1: 追加失败测试**

```python
from analytics import summary_metrics

class TestSummaryMetrics(unittest.TestCase):
    def test_sum_avg(self):
        out = summary_metrics(COLS, ROWS, TYPES,
                              [{"col": "金额", "fn": "sum"}, {"col": "金额", "fn": "avg"}])
        self.assertEqual(out[0], {"label": "金额合计", "value": 350.5})
        self.assertAlmostEqual(out[1]["value"], 116.8333, places=3)

    def test_count_counts_non_empty_on_any_type(self):
        out = summary_metrics(["区域"], [["华东"], ["华北"], [None]], ["str"],
                              [{"col": "区域", "fn": "count"}])
        self.assertEqual(out[0]["value"], 2)
        out2 = summary_metrics(["id"], [["O1"], [""], [None]], ["str"],
                               [{"col": "id", "fn": "count"}])
        self.assertEqual(out2[0]["value"], 1)

    def test_unknown_col_skipped(self):
        out = summary_metrics(COLS, ROWS, TYPES, [{"col": "不存在"}])
        self.assertEqual(out, [])

    def test_custom_label(self):
        out = summary_metrics(COLS, ROWS, TYPES, [{"col": "金额", "label": "销售额"}])
        self.assertEqual(out[0]["label"], "销售额")

    def test_empty_metrics(self):
        self.assertEqual(summary_metrics(COLS, ROWS, TYPES, None), [])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m unittest tests.test_analytics -v
```
预期：ImportError（`summary_metrics` 未定义）

- [ ] **Step 3: 实现**（追加到 analytics.py）

```python
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
```

- [ ] **Step 4: 跑测试确认通过**（同 Task 1 Step 4）
- [ ] **Step 5: Commit**

```bash
git add src/sqlreport/analytics.py tests/test_analytics.py
git commit -m "feat(analytics): KPI 摘要指标 summary_metrics"
```

### Task 3: `top_n_rows` + `add_share_columns`

**Files:**
- Modify: `src/sqlreport/analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: 追加失败测试**

```python
from analytics import top_n_rows, add_share_columns

class TestTopNRows(unittest.TestCase):
    def test_merge_rest(self):
        rows = [["A", 50.0], ["B", 30.0], ["C", 15.0], ["D", 5.0]]
        out = top_n_rows(["区域", "金额"], rows, ["str", "num"], "金额", n=2)
        self.assertEqual(out[0], ["A", 50.0])
        self.assertEqual(out[1], ["B", 30.0])
        self.assertEqual(out[2], ["其他", 20.0])

    def test_fewer_than_n_unchanged(self):
        rows = [["A", 50.0]]
        self.assertEqual(top_n_rows(["区域", "金额"], rows, ["str", "num"], "金额", n=10), rows)

    def test_non_num_col_rejected(self):
        with self.assertRaises(ValueError):
            top_n_rows(["区域", "金额"], [["A", 1.0]], ["str", "num"], "区域")

    def test_missing_col_rejected(self):
        with self.assertRaises(ValueError):
            top_n_rows(["区域", "金额"], [["A", 1.0]], ["str", "num"], "不存在")


class TestShareColumns(unittest.TestCase):
    def test_share_and_cumulative(self):
        cols, rows, types = add_share_columns(["区域", "金额"],
                                              [["A", 60.0], ["B", 30.0], ["其他", 10.0]],
                                              ["str", "num"], "金额")
        self.assertEqual(cols, ["区域", "金额", "金额占比%", "金额累计%"])
        self.assertEqual(rows[0], ["A", 60.0, 60.0, 60.0])
        self.assertEqual(rows[1], ["B", 30.0, 30.0, 90.0])
        self.assertEqual(rows[2], ["其他", 10.0, 10.0, 100.0])
        self.assertEqual(types, ["str", "num", "num", "num"])

    def test_zero_total(self):
        _, rows, _ = add_share_columns(["c", "v"], [["A", 0.0]], ["str", "num"], "v")
        self.assertEqual(rows[0][2], 0.0)

    def test_input_not_mutated(self):
        rows = [["A", 60.0]]
        add_share_columns(["区域", "金额"], rows, ["str", "num"], "金额")
        self.assertEqual(rows, [["A", 60.0]])
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（追加到 analytics.py）

```python
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
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit**

```bash
git add src/sqlreport/analytics.py tests/test_analytics.py
git commit -m "feat(analytics): Top N 归并与占比/累计占比列"
```

### Task 4: `bucket_column` 时间分桶

**Files:**
- Modify: `src/sqlreport/analytics.py`（头部追加 `import datetime`、`import re`）
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: 追加失败测试**

```python
from analytics import bucket_column

class TestBucketColumn(unittest.TestCase):
    def test_month(self):
        rows = [["2026-01-05"], ["2026/02/11 10:00"], ["bad"], ["2026-01-31"]]
        out = bucket_column(["dt"], rows, "dt", "month")
        self.assertEqual([r[0] for r in out], ["2026-01", "2026-02", "bad", "2026-01"])

    def test_quarter_and_week(self):
        self.assertEqual(bucket_column(["dt"], [["2026-04-02"]], "dt", "quarter")[0][0], "2026Q2")
        self.assertEqual(bucket_column(["dt"], [["2026-01-01"]], "dt", "week")[0][0], "2026-W01")

    def test_day_unchanged_and_shared_rows(self):
        rows = [["2026-01-05"]]
        out = bucket_column(["dt"], rows, "dt", "day")
        self.assertEqual(out[0][0], "2026-01-05")
        out[0][0] = "x"          # day 模式返回原行对象，调用方不得再改 —— 锁定该行为
        self.assertEqual(rows[0][0], "x")

    def test_bad_unit_rejected(self):
        with self.assertRaises(ValueError):
            bucket_column(["dt"], [["2026-01-01"]], "dt", "year")

    def test_invalid_date_kept(self):
        out = bucket_column(["dt"], [["2026-13-40"]], "dt", "month")
        self.assertEqual(out[0][0], "2026-13-40")

    def test_input_not_mutated(self):
        rows = [["2026-01-05"]]
        bucket_column(["dt"], rows, "dt", "month")
        self.assertEqual(rows[0][0], "2026-01-05")
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**（追加到 analytics.py；文件头补 `import datetime`、`import re`）

```python
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
                    if unit == "month":
                        r2[i] = f"{y}-{mo:02d}"
                    elif unit == "quarter":
                        r2[i] = f"{y}Q{(mo - 1) // 3 + 1}"
                    else:
                        iso = datetime.date(y, mo, d).isocalendar()
                        r2[i] = f"{iso[0]}-W{iso[1]:02d}"
                except ValueError:
                    pass  # 非法日期原样保留
        out.append(r2)
    return out
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit**

```bash
git add src/sqlreport/analytics.py tests/test_analytics.py
git commit -m "feat(analytics): 时间分桶 bucket_column"
```

### Task 5: 服务端集成 — `_execute_report` 应用分析配置 + `_save` 白名单

**Files:**
- Modify: `src/sqlreport/server.py`（`_execute_report` 约 L1358、`_save` 约 L1119、import 头部）
- Modify: `tests/test_server.py`

**配置 Schema（报表 JSON 顶层可选键，决策 D3/D5/D10）：**

```jsonc
{
  "bucket": {"col": "下单日期", "unit": "month"},        // 先分桶
  "top_n":  {"col": "金额", "n": 10, "others": "其他"},  // 再取 Top N
  "share":  {"col": "金额"},                             // 再加占比列（依赖行序）
  "total":  {"label": "合计", "label_col": 0},           // 合计行（可省略 → 默认）
  "summary": [{"col": "金额", "fn": "sum", "label": "销售额"}]  // KPI 指标
}
```

- [ ] **Step 1: 写失败集成测试**（`tests/test_server.py` 追加；**用基架现成辅助 `self.save_report(rec, rid)`，rid 用 ASCII**）

```python
class AnalysisOnQuery(ServerTestCase):
    def _save_report(self, extra):
        rec = {"name": "分析报表", "ds": "demo",
               "sql": "SELECT order_id, region, amount, dt FROM orders ORDER BY order_id"}
        rec.update(extra)
        self.save_report(rec, "analysis1")

    def _q(self):
        st, body = self.req("POST", "/q/analysis1", "page=1",
                            "application/x-www-form-urlencoded")
        self.assertEqual(st, 200)
        return json.loads(body)

    def test_total_and_summary_in_q(self):
        self._save_report({
            "total": {"label": "合计"},
            "summary": [{"col": "amount", "fn": "sum", "label": "销售额"},
                        {"col": "order_id", "fn": "count", "label": "单数"}]})
        j = self._q()
        self.assertEqual(j["total_row"][0], "合计")
        self.assertAlmostEqual(j["total_row"][2], 1470.8)   # amount 是第 3 列
        self.assertEqual(j["summary"][0]["label"], "销售额")
        self.assertAlmostEqual(j["summary"][0]["value"], 1470.8)
        self.assertEqual(j["summary"][1], {"label": "单数", "value": 5})

    def test_top_n_and_share(self):
        self._save_report({"top_n": {"col": "amount", "n": 2}, "share": {"col": "amount"}})
        j = self._q()
        self.assertEqual(len(j["rows"]), 3)          # 2 行 + 其他
        self.assertEqual(j["rows"][2][0], "其他")
        self.assertEqual(j["columns"][-2:], ["amount占比%", "amount累计%"])

    def test_bucket_month(self):
        self._save_report({"bucket": {"col": "dt", "unit": "month"}})
        j = self._q()
        self.assertEqual(j["rows"][0][3], "2026-01")  # dt 是第 4 列

    def test_analysis_on_cache_hit(self):
        self._save_report({"total": {"label": "合计"}, "cache_ttl": 60})
        self._q()                                     # 第一次：写入缓存
        j = self._q()                                 # 第二次：命中缓存
        self.assertTrue(j["cached"])
        self.assertAlmostEqual(j["total_row"][2], 1470.8)

    def test_no_analysis_keys_backward_compat(self):
        self._save_report({})
        j = self._q()
        self.assertIsNone(j.get("total_row"))         # 决策 D4：固定键，缺省 None/[]
        self.assertEqual(j.get("summary"), [])
        self.assertEqual(len(j["rows"]), 5)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m unittest tests.test_server -v
```
预期：新增用例 FAIL（响应缺 `total_row` 等键，或 `/save` 丢弃分析键）

- [ ] **Step 3: 实现** — `server.py` 四处改动：

1. **import 头部**：`from sqlreport.analytics import total_row, summary_metrics, top_n_rows, add_share_columns, bucket_column`。
2. **`_save` 白名单（决策 D10，缺失则配置被静默丢弃）**：在 `_save` 构造 `rec` 的白名单处追加 5 个键，最小结构校验：

```python
        if isinstance(data.get("total"), dict):
            rec["total"] = {"label": str(data["total"].get("label", "合计")),
                            "label_col": int(data["total"].get("label_col", 0) or 0)}
        if isinstance(data.get("summary"), list):
            rec["summary"] = [m for m in data["summary"]
                              if isinstance(m, dict) and m.get("col")]
        for k in ("top_n", "share", "bucket"):
            if isinstance(data.get(k), dict) and data[k].get("col"):
                rec[k] = data[k]
```

3. **`_execute_report` 收敛单一 return（决策 D2/D4）**：现状是缓存命中早退（约 L1371-1376）+ 末尾 return（约 L1397）双路。改造为：命中路径把 `hit` 解包为 `cols/rows/truncated/coltypes` 并置 `cached=True`，未命中路径执行查询/合并/截断后 `CACHE.put` 并置 `cached=False`，两路汇合到唯一 return（`hit`/`key` 等变量名沿用现文件，确保 `ttl=0` 即 `key` 为空时 `cached=False` 不 NameError）。汇合后调用分析管道：

```python
        # ---- 分析管道（D2/D5：基于最终返回行集；缓存命中时同样重算，代价 O(n)）----
        if isinstance(r.get("bucket"), dict) and r["bucket"].get("col") in cols:
            rows = bucket_column(cols, rows, r["bucket"]["col"], r["bucket"].get("unit", "month"))
        if isinstance(r.get("compare"), dict):   # M3 Task 15 落地处，M1 先留空位注释
            pass
        if isinstance(r.get("top_n"), dict) and "col" in r["top_n"]:
            try:
                rows = top_n_rows(cols, rows, coltypes, r["top_n"]["col"],
                                  n=int(r["top_n"].get("n", 10)),
                                  others=str(r["top_n"].get("others", "其他")))
            except ValueError:
                pass  # 配置列缺失/类型不符时静默跳过，不阻断出数
        if isinstance(r.get("share"), dict) and "col" in r["share"]:
            try:
                cols, rows, coltypes = add_share_columns(cols, rows, coltypes, r["share"]["col"])
            except ValueError:
                pass
        tr_cfg = r.get("total")
        if isinstance(tr_cfg, dict):
            total = total_row(cols, rows, coltypes,
                              label=str(tr_cfg.get("label", "合计")),
                              label_col=int(tr_cfg.get("label_col", 0) or 0))
        else:
            total = None
        summary = summary_metrics(cols, rows, coltypes, r.get("summary")) if r.get("summary") else []
        return {"columns": cols, "rows": rows, "coltypes": coltypes, "truncated": truncated,
                "cached": cached, "elapsed_ms": elapsed_ms, "total_row": total, "summary": summary}
```

（`cached`/`elapsed_ms`/`truncated` 用收敛后两路共有的局部变量；share 派生列「占比%/累计%」会被 total_row 一并求和（≈100），属预期口径——已在 Task 1 docstring 记录，查看端口径回显随 Task 6 展示。）

4. **`_execute_report` 的返回值消费方检查**：确认 `_query`（`/q`）与 `_export` 都直接透传 `_execute_report` 的 dict（现状如此），无需额外改动。

- [ ] **Step 4: 跑全量测试确认通过**

```bash
python -m unittest discover -s tests -v
```
预期：全部 PASS（含既有回归用例——`test_no_analysis_keys_backward_compat` 与 `test_analysis_on_cache_hit` 保证旧行为与缓存路径不变）

- [ ] **Step 5: Commit**

```bash
git add src/sqlreport/server.py tests/test_server.py
git commit -m "feat(server): /q 集成分析管道(total/summary/top_n/share/bucket)并扩展 /save 白名单"
```

### Task 6: 查看页渲染 — KPI 区 + 合计行 + 数值格式化 + 口径回显

**Files:**
- Modify: `src/sqlreport/server.py`（`_viewer` 的内联 JS/CSS：`tblShow`/`tblDraw`、`run()` 调用点）

- [ ] **Step 1: 实现**（纯前端，无后端改动）

1. **KPI 区**：`tblShow`（锚点 `out._st={`）中，`j.summary` 存在时在表格容器前插入 KPI 卡片。**复用查看页/列表页现有类 `.stat` 及其内部 `.v`/`.k`**（现 CSS 已有 `.stat .v`/`.stat .k`；容器用现有 `.stat-grid`，若查看页无该容器则新增 `<div id="kpi" class="stat-grid"></div>`）：

```js
  var kpi=document.getElementById('kpi');
  if(kpi){kpi.innerHTML=(j.summary||[]).map(function(m){
    var v=(m.value==null)?'':(typeof m.value==='number'
      ?m.value.toLocaleString('zh-CN',{maximumFractionDigits:2}):m.value);
    return '<div class="stat"><div class="v">'+escHtml(String(v))+'</div><div class="k">'+escHtml(m.label)+'</div></div>';
  }).join('')||(j.summary?'':'');}
```

2. **合计行**：`tblShow` 存入 `out._st.total=j.total_row||null`；`tblDraw` 渲染 `<table>` 时追加 `<tfoot>` 输出 `total` 行，分页下每页底部都渲染（客户端分页每页重绘，Excel 心智模型）。
3. **数值列格式化（新增，现状没有）**：`tblDraw` 单元格渲染处（锚点 `escHtml(v)`）按 `ct[i]==='num'` 分型：数值列右对齐 + `toLocaleString('zh-CN',{maximumFractionDigits:2})`；**注意仅对 num 列格式化，字符串型 ID（如订单号）永不加千分位**（保留 v0.3 教训）。
4. **口径回显**：`tblShow` 签名扩展为 `tblShow(out,j,defPage,given)`，`given` 为本次提交的表单值对象；**同步修改 `run()` 内的调用点**（约 L1084 附近，把已收集的参数对象传入）。状态行最前追加 `参数：k=v · k=v`（空值参数跳过）；行数/耗时/缓存/截断提示已存在，仅在其后补一句 `基于 N 行计算`（与行数提示合并）。

- [ ] **Step 2: 编译 + 重启验证**（项目规则：改 `src/sqlreport/*.py` 后必须重启本地服务再验证）

```bash
python -m py_compile src/sqlreport/*.py
python server.py 8765   # 非阻塞启动
```
浏览器验证：用 demo 库建一张带 `total`+`summary`+`top_n` 的报表，`/r/{id}` 查看——KPI 卡、合计行、参数回显、Top N 归并、数值列格式化全部生效；无分析配置的旧报表页面无变化（KPI 区空、无合计行）。

- [ ] **Step 3: 全量测试**
- [ ] **Step 4: Commit**

```bash
git add src/sqlreport/server.py
git commit -m "feat(viewer): KPI 摘要区/合计行/数值列格式化/参数口径回显"
```

### Task 7: 点列头排序（P1-3，纯前端）

**Files:**
- Modify: `src/sqlreport/server.py`（`tblDraw`/`tblShow` 内联 JS）

- [ ] **Step 1: 实现**

`out._st` 增加 `sort:{i:索引,desc:bool}` 状态；`tblDraw` 渲染 `<th>` 时绑定点击，按 coltypes 比较（`ct[i]==='num'` 数值比较，否则 `String(a).localeCompare(b,'zh-CN')`），排序作用于**当前数据集全量行**再分页（与 P1-3 既有判断一致）。再次点击同列切换升降序，点其他列重置为降序。表头加排序指示符（▲/▼ 文本，非图形）。

- [ ] **Step 2: 编译 + 重启验证**：点击列头排序/翻转/切列，数值列按数值排（10 > 9 > 2），中文列按拼音；分页后排序保持。
- [ ] **Step 3: 全量测试 + Commit**

```bash
git add src/sqlreport/server.py
git commit -m "feat(viewer): 点列头排序(num/str 分型比较)"
```

### Task 8: 导出同步（合计行 + 摘要 + 数值格式）

**Files:**
- Modify: `src/sqlreport/server.py`（`_export`，约 L1428）
- Modify: `tests/test_server.py`

- [ ] **Step 1: 写失败测试**

```python
    def test_export_contains_total(self):
        self._save_report({"total": {"label": "合计"}})   # _save_report 复用 Task 5 辅助（提升到 ServerTestCase）
        st, body = self.req("GET", "/r/analysis1/export")
        self.assertEqual(st, 200)
        self.assertIn("合计", body)
        self.assertIn("1470.8", body.replace(",", ""))    # xls 为 HTML 文本
```

- [ ] **Step 2: 确认失败 → 实现**：`_export` 中利用 `_execute_report` 返回的 `result["total_row"]` 与 `result["summary"]`：
  - xls：表格后追加 `<tr>` 合计行；摘要渲染为表格上方 `<p>` 文本行（`label: value`，分号分隔），零样式纯文本。
  - **数值单元格格式（新增，现状是裸 `<td>{v}</td>`）**：对 `coltypes[i]=='num'` 且值可被 `float()` 解析的单元格，输出 `<td style="mso-number-format:0.00;">原始数字</td>`（Excel 中为真数值）；其余单元格维持 HTML 转义。
  - csv：数据行后追加合计行（保持与页面所见一致）。
- [ ] **Step 3: 全量测试 + Commit**

```bash
git add src/sqlreport/server.py tests/test_server.py
git commit -m "feat(export): 导出附带合计行/KPI 摘要与数值单元格格式"
```

### Task 9: M1 版本收尾（v0.4.0）

**Files:**
- Modify: `src/sqlreport/server.py`（`nav()` 硬编码版本，约 L262）
- Modify: `src/sqlreport/__init__.py`、`pyproject.toml`、`CHANGELOG.md`

- [ ] **Step 1: `nav()` 改读版本**：`server.py` 头部追加 `from sqlreport import __version__`；`nav()` 中 `v0.3.0` 字面量替换为 `f"v{__version__}"`（决策 D7）。
- [ ] **Step 2: 版本号**：`__init__.py` → `"0.4.0"`；`pyproject.toml` → `version = "0.4.0"`；`CHANGELOG.md` 补 v0.4.0 条目（合计行/KPI 摘要/Top N/占比/分桶/排序/数值格式化/导出同步）。
- [ ] **Step 3: 验证**：`python -m unittest discover -s tests -v` 全绿；重启服务冒烟（列表页版本显示 v0.4.0、旧报表回归正常）。
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: v0.4.0 版本收尾"
```

---

# M2 — 交叉分析（v0.5.0）

### Task 10: `pivot` 透视表纯函数

**Files:**
- Modify: `src/sqlreport/analytics.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: 追加失败测试**

```python
from analytics import pivot

P_COLS = ["区域", "产品", "金额"]
P_TYPES = ["str", "str", "num"]
P_ROWS = [["华东", "A", 100.0], ["华东", "B", 50.0], ["华北", "A", 30.0]]

class TestPivot(unittest.TestCase):
    def test_basic_sum_with_totals(self):
        pc, pr, pt = pivot(P_COLS, P_ROWS, P_TYPES, "区域", "产品", "金额")
        self.assertEqual(pc, ["区域", "A", "B", "合计"])
        self.assertEqual(pr[0], ["华东", 100.0, 50.0, 150.0])
        self.assertEqual(pr[1], ["华北", 30.0, "", 30.0])
        self.assertEqual(pr[-1][0], "总计")
        self.assertEqual(pr[-1][-1], 180.0)

    def test_agg_avg_count(self):
        rows = [["R", "A", 10.0], ["R", "A", 30.0]]
        _, pr, _ = pivot(P_COLS, rows, P_TYPES, "区域", "产品", "金额",
                         agg="avg", row_total=False, col_total=False)
        self.assertEqual(pr[0][1], 20.0)
        _, pr2, _ = pivot(P_COLS, rows, P_TYPES, "区域", "产品", "金额",
                          agg="count", row_total=False, col_total=False)
        self.assertEqual(pr2[0][1], 2)

    def test_too_many_cols_rejected(self):
        rows = [[f"k{i}", "A", 1.0] for i in range(51)]
        with self.assertRaises(ValueError):
            pivot(P_COLS, rows, P_TYPES, "区域", "产品", "金额", max_cols=50)

    def test_value_must_be_num(self):
        with self.assertRaises(ValueError):
            pivot(P_COLS, P_ROWS, P_TYPES, "区域", "产品", "产品")

    def test_empty_dim_normalized(self):
        rows = [["", "A", 1.0], [None, "A", 2.0]]
        _, pr, _ = pivot(["r", "c", "v"], rows, ["str", "str", "num"],
                         "r", "c", "v", row_total=False, col_total=False)
        self.assertEqual(pr[0][0], "")   # None 与 "" 归并为空串

    def test_input_not_mutated(self):
        snapshot = [list(r) for r in P_ROWS]
        pivot(P_COLS, P_ROWS, P_TYPES, "区域", "产品", "金额")
        self.assertEqual(P_ROWS, snapshot)
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**（追加到 analytics.py）。**行为规格（全部锁定）**：

- 列头：col 维度去重、**按首现顺序**；去重后 > `max_cols` 抛中文 ValueError（提示先在 SQL 层归类）。
- 行序：row 维度值排序——列全为数值则数值升序，否则按 `str` 排序；排序并列时保持首现顺序（sorted 稳定）；维度 `None` 与 `""` 归并为 `""`。
- 数据格：`value` 列按 `agg`（sum/count/avg/max/min）聚合，`count` 计非空值数；无数据格记 `""`；`value` 列非 num（且 agg≠count）抛 ValueError。
- `row_total=True` 追加「合计」列；`col_total=True` 追加「总计」行；avg 的已知口径（格内均值之总计较均值≠均值）写入 docstring。
- 返回 `(pcols, prows, ptypes)`，不改入参。

- [ ] **Step 4: 确认通过 → Step 5: Commit**

```bash
git commit -m "feat(analytics): 透视表 pivot(小计/总计/聚合)"
```

### Task 11: blocks 化 Schema — `normalize_blocks` + `/q` 多块响应

**Files:**
- Modify: `src/sqlreport/params.py`（追加 `normalize_blocks`）
- Modify: `src/sqlreport/server.py`（`_execute_report`、`_save` 白名单）
- Modify: `tests/test_params.py`、`tests/test_server.py`

**Schema（决策 D3/D4/D10）：** 报表顶层可选 `"blocks": [...]`；块类型 `table`（可选 `"dataset"` 与 `"title"`）与 `pivot`（必填 `dataset/row/value`，可选 `col/agg/row_total/col_total/max_cols/title`）。无 `blocks` → `[{"type": "table"}]`（零迁移）。M1 的分析键继续只作用于主表块（第一个 table 块）。**保存期校验必填键；执行期才校验列名/列型**（save 时无数据集结果）。

- [ ] **Step 1: params 失败测试**

```python
from params import normalize_blocks

class TestNormalizeBlocks(unittest.TestCase):
    def test_default_single_table(self):
        self.assertEqual(normalize_blocks({}), [{"type": "table"}])

    def test_valid_blocks_passthrough(self):
        r = {"blocks": [{"type": "table", "title": "明细"},
                        {"type": "pivot", "dataset": "a", "row": "region", "value": "amount"}]}
        self.assertEqual(normalize_blocks(r), r["blocks"])

    def test_bad_type_rejected(self):
        with self.assertRaises(ValueError):
            normalize_blocks({"blocks": [{"type": "chart"}]})

    def test_pivot_missing_required_rejected(self):
        with self.assertRaises(ValueError):
            normalize_blocks({"blocks": [{"type": "pivot", "dataset": "a", "row": "r"}]})
```

- [ ] **Step 2: server 集成测试**

```python
    def test_q_blocks_pivot(self):
        rec = {"name": "交叉", "ds": "demo",
               "sql": "SELECT region, amount FROM orders",
               "blocks": [{"type": "pivot", "dataset": "main", "row": "region",
                            "value": "amount", "agg": "sum", "title": "区域汇总"}]}
        self.save_report(rec, "pivot1")
        st, body = self.req("POST", "/q/pivot1", "page=1", "application/x-www-form-urlencoded")
        self.assertEqual(st, 200)
        j = json.loads(body)
        b = j["blocks"][0]
        self.assertEqual(b["type"], "pivot")
        self.assertEqual(b["columns"], ["region", "合计"])   # 单维汇总锁定形态（见下）
        self.assertAlmostEqual([r for r in b["rows"] if r[0] == "华东"][0][1], 350.5)
        # 顶层键仍为主表（向后兼容，决策 D4）
        self.assertEqual(j["columns"], ["region", "amount"])
```

**单维汇总锁定形态**：pivot 块 `col` 缺省时为 GROUP BY 单维表——列头 `[row, "合计"]`，无「合计」列（row_total 强制 False），col_total 可选出「总计」行；实现为 server 侧注入常量维度值 `"合计"` 调用 pivot（`row_total=False`），**不引入 `__all__` 之类合成列名**。

- [ ] **Step 3: 实现**：`normalize_blocks` 校验类型白名单与 pivot 必填键（中文 ValueError）；`_execute_report` 构建 `blocks` 结果数组（table 块 = 主结果 + M1 分析管道；pivot 块 = 对指定 dataset 的原始结果跑 `pivot`，块级结构 `{"type","title","columns","rows","coltypes"}`）；`/q` 返回固定新增 `"blocks"`，顶层键取第一个 table 块内容（保持 D4）。`_save` 白名单追加 `blocks`（经 `normalize_blocks` 校验后存入）。
- [ ] **Step 3.5: 缓存交互（锁定，防执行期缺口）**：**含非 table 块（pivot/hist）的报表不使用结果缓存**——`_execute_report` 检测到非 table 块时将 `cache_ttl` 视为 0（不读不写缓存）。理由：缓存里只有主表行，命中路径拿不到 pivot/hist 所需的各 dataset 原始结果，强行收敛会出现 NameError 或块静默消失。
- [ ] **Step 4: 全量测试（含「无 blocks 的旧报表兼容用例」：`/q` 响应 `blocks == [{"type":"table",...主表...}]` 且顶层键不变；**缓存降级用例**：`cache_ttl>0` + pivot 块，两次 `/q` 均 blocks 完整且 `cached=False`）→ Step 5: Commit**

```bash
git commit -m "feat(schema): blocks 分析块化与 /q 多块响应"
```

### Task 12: 查看页多块渲染 + 导出多块 + 编辑器 blocks 配置

**Files:**
- Modify: `src/sqlreport/server.py`（`_viewer` JS、`_export`、编辑器表单）
- Modify: `tests/test_server.py`

- [ ] **Step 1: 查看页**：`tblShow` 改为消费 `j.blocks`：每个块渲染 `标题 + 表格`（table 块走现有分页表格；pivot/hist 块静态表 + 合计行置底样式 `class="total-row"`），`j.blocks` 缺省/为单 table 块时走旧路径（D4 兼容）。
- [ ] **Step 2: 导出**：`_export`（xls）按 blocks 顺序输出：每块一个 `<h3>标题</h3>` + 表格段；csv 仍只导主表（文档注明）。
- [ ] **Step 3: 编辑器**：`_editor` 加「分析块（JSON，可选）」textarea，`/save` 时 `json.loads` + `normalize_blocks` 校验 + 合并入报表 JSON（与 M1 分析键同机制）。
- [ ] **Step 4: 重启浏览器验证**：单块旧报表不变；含 pivot 块报表的查看与导出正确。
- [ ] **Step 5: 全量测试 + Commit**

```bash
git commit -m "feat(viewer,export,editor): blocks 多块渲染/导出/编辑器配置"
```

### Task 13: server.py 视图拆分（无行为变更重构）

**Files:**
- Create: `src/sqlreport/views_report.py`
- Modify: `src/sqlreport/server.py`

- [ ] **Step 1: 迁移**：把 PAGE/`nav`/`page`/`esc_html`/`_rel_time` 及列表/编辑器/查看页的模板字符串与页面拼装函数**整体**迁入 `views_report.py`（含 `_list_reports` 页面渲染用到的 `_rel_time`；文件头 `from sqlreport import __version__` 等，导入风格 D8）；`server.py` 顶部按实际引用 `from sqlreport.views_report import PAGE, nav, page, esc_html, _rel_time`（引用点不改名，只改来源）。`Handler` 路由与业务方法**一律不动**。
- [ ] **Step 2: 验证**：`python -m py_compile src/sqlreport/*.py`；全量测试；重启冒烟三个页面（列表/编辑器/查看页）视觉与行为不变。
- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(server): 视图模板拆分至 views_report.py"
```

### Task 14: M2 版本收尾（v0.5.0）

同 Task 9 模式：版本号两处 + CHANGELOG（透视表/blocks/多块导出/视图拆分）+ 全量测试 + 冒烟 + `git commit -m "chore: v0.5.0 版本收尾"`。

---

# M3 — 对比与维度（v0.6.0）

### Task 15: `diff_merge` 对比差值（环比语义）

**Files:**
- Modify: `src/sqlreport/analytics.py`
- Modify: `tests/test_analytics.py`、`tests/test_server.py`

- [ ] **Step 1: 失败测试（核心断言）**

```python
from analytics import diff_merge

class TestDiffMerge(unittest.TestCase):
    def test_diff_and_rate(self):
        base = [["华东", 110.0], ["华北", 90.0]]
        right = [["华东", 100.0]]
        cols, rows, types = diff_merge(["区域"], base, ["区域"], right, ["区域"], "金额",
                                       label="上月")
        self.assertEqual(cols, ["区域", "金额", "金额(上月)差值", "金额(上月)增长率%"])
        self.assertEqual(rows[0], ["华东", 110.0, 10.0, 10.0])
        self.assertEqual(rows[1], ["华北", 90.0, "", ""])   # 右侧缺失
```

- [ ] **Step 2: 实现**（要点：inner 对齐，键取 `str` 元组；`rate=round((b-r)/r*100,1)`，`r` 为 0/缺失时留空；metric 列须 num，on 列缺失抛中文 ValueError；对齐复用 `db.merge_lookup` 的哈希思路但独立实现，保持 analytics 无 db 依赖；行序保持 base 首现序，键并列稳定）。
- [ ] **Step 3: 报表集成**：顶层可选键 `"compare": {"dataset": "ds_last", "on": ["region"], "metric": "amount", "label": "上月"}`——主表 = datasets 中第一个数据集，右侧取 `dataset` 指定的另一数据集结果；接入 Task 5 预留的 `compare` 管道位（bucket 之后、top_n 之前，决策 D5）。SQL 作者在两个数据集里自写本月/上月 SQL（工具只做对齐与差值，不做日期偏移推导——YAGNI）。`_save` 白名单追加 `compare`（校验 dict + dataset/on/metric 必填）。
- [ ] **Step 4: 集成测试（两个数据集 report 走 `/q`，断言差值/增长率列；**兼容对**：无 `compare` 键时响应与 M2 完全一致）→ Step 5: Commit**

```bash
git commit -m "feat(analytics,server): 对比差值 diff_merge 与 compare 键"
```

### Task 16: `bin_numeric` 数值分箱统计表

**Files:**
- Modify: `src/sqlreport/analytics.py`、`tests/test_analytics.py`

- [ ] **Step 1: 失败测试**

```python
from analytics import bin_numeric

class TestBin(unittest.TestCase):
    def test_equal_width(self):
        rows = [[1.0], [2.0], [3.0], [10.0]]
        cols, out, types = bin_numeric(["v"], rows, ["num"], "v", bins=3)
        self.assertEqual(cols, ["区间", "计数", "占比%"])
        self.assertEqual(types, ["str", "num", "num"])
        self.assertEqual(sum(r[1] for r in out), 4)
        self.assertAlmostEqual(sum(r[2] for r in out), 100.0, delta=0.3)

    def test_all_empty_rejected(self):
        with self.assertRaises(ValueError):
            bin_numeric(["v"], [[None]], ["num"], "v")
```

- [ ] **Step 2: 实现**（等宽分箱；`hi==lo` 退化为单区间；左闭右开、末箱右闭；区间标签 `"a ~ b"`；空值行不计入但**不报错**，仅全空列抛 ValueError）。作为 `blocks` 新类型 `{"type": "hist", "col": ..., "bins": 10}` 接入 `normalize_blocks` + 查看页/导出（沿用 Task 12 机制）。
- [ ] **Step 3: 全量测试（**兼容对**：无 hist 块的报表行为不变）→ Commit**

```bash
git commit -m "feat(analytics): 数值分箱统计表 bin_numeric"
```

### Task 17: 保存视图（saved views）

**Files:**
- Modify: `src/sqlreport/server.py`（`_viewer`、`_save` 白名单）、`tests/test_server.py`

- [ ] **Step 1: Schema**：报表顶层 `"views": [{"name": "本月", "params": {"d": "2026-09-01", "d_2": "2026-09-30"}}]`。
- [ ] **Step 2: 实现**：`_viewer` 顶部渲染快捷链接 `<a href="/r/{id}?{urlencode(params)}">name</a>`（URL 即状态，视图=预填参数的链接，无后端存储）；`_save` 白名单追加 `views`（校验 `name` 非空、`params` 为字典）。
- [ ] **Step 3: 集成测试（**兼容对**：无 `views` 键的查看页无快捷区）+ 浏览器冒烟（点击视图链接参数自动填充并查询）→ Commit**

```bash
git commit -m "feat(viewer): 保存视图快捷链接"
```

### Task 18: M3 版本收尾（v0.6.0）

同 Task 9 模式：版本号 + CHANGELOG（对比差值/分箱/保存视图）+ 全量测试 + 冒烟 + `git commit -m "chore: v0.6.0 版本收尾"`。

---

# M4 — 交付加固（v0.7.0）

### Task 19: 真 .xlsx 导出（手写最小 xlsx）

**Files:**
- Create: `src/sqlreport/xlsx.py`
- Create: `xlsx.py`（根 shim，仿根 `params.py`，决策 D6）
- Create: `tests/test_xlsx.py`
- Modify: `src/sqlreport/server.py`（`_export`）、`tests/test_server.py`

- [ ] **Step 1: 失败测试**

```python
#!/usr/bin/env python3
"""xlsx.py 单元测试：zip 结构与单元格编码"""
import io, os, sys, unittest, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xlsx import write_xlsx

class TestXlsx(unittest.TestCase):
    def test_zip_structure_and_cells(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "数据", "columns": ["名", "值"],
                     "rows": [["a<b", 1.5], ["x", "y"]]}], buf)
        z = zipfile.ZipFile(buf)
        names = set(z.namelist())
        self.assertIn("[Content_Types].xml", names)
        self.assertIn("xl/workbook.xml", names)
        self.assertIn("xl/worksheets/sheet1.xml", names)
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("t=\"inlineStr\"", sheet)          # 字符串内联
        self.assertIn("&lt;b&gt;", sheet)                # XML 转义
        self.assertNotIn('t="s"', sheet)                 # 不用共享字符串表
        self.assertIn("<v>1.5</v>", sheet)               # 数值单元格

    def test_sheet_name_sanitized(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "a/b:c*d?e[f]g" + "x" * 40, "columns": ["c"], "rows": []}], buf)
        wb = zipfile.ZipFile(buf).read("xl/workbook.xml").decode()
        self.assertNotIn("/", wb.split('name="')[1].split('"')[0])
```

- [ ] **Step 2: 实现 `src/sqlreport/xlsx.py` + 根 shim**（完整骨架，zip 条目顺序固定保证可测）：

```python
"""手写最小 xlsx 写出器（zip + inlineStr），零依赖（决策：PLAN.md P2-3 首选方案）。"""
import zipfile
from xml.sax.saxutils import escape

_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
       '{SHEETS}'
       '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
       '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId0" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
         '</Relationships>')
_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="1"><font/></fonts><fills count="2"><fill><patternFill patternType="none"/></fill>'
           '<fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders>'
           '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf/></cellXfs></styleSheet>')

_BAD_SHEET = set('\\/*?:[]')


def _letter(n):
    """0-based 列号 → A/B/.../AA"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_name(name, used):
    s = "".join("_" if c in _BAD_SHEET else c for c in str(name or "Sheet"))[:31] or "Sheet"
    base, k = s, 1
    while s in used:
        k += 1
        s = f"{base[:28]}-{k}"
    used.add(s)
    return s


def _cell(ref, v):
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def write_xlsx(sheets, fp):
    """sheets: [{"name", "columns", "rows"}] → xlsx 写入文件对象。
    调用方负责把数值列先转为 float/int（见 server 集成），本函数按 Python 类型分型。"""
    used = set()
    names = [_sheet_name(s.get("name"), used) for s in sheets]
    wb_sheets = "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                        for i, n in enumerate(names))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets>{wb_sheets}</sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
               + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               + '</Relationships>')
    ct = _CT.replace("{SHEETS}", "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets))))
    with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets):
            cols = list(s.get("columns") or [])
            rows = list(s.get("rows") or [])
            head = "".join(_cell(f"{_letter(j)}1", c) for j, c in enumerate(cols))
            body = "".join("<row r=\"%d\">" % (r + 2) + "".join(
                _cell(f"{_letter(j)}{r + 2}", v) for j, v in enumerate(row)) + "</row>"
                for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f'<sheetData><row r="1">{head}</row>{body}</sheetData></worksheet>')
```

- [ ] **Step 3: `_export` 接入**：`fmt == "xlsx"` 时，用 M2 的 blocks 构造 sheets（每块一个 sheet：**顶层 `summary` 数组** → 「摘要」sheet 的 label/value 两列表（注意 summary 是 `/q` 响应顶层键，不是 blocks 块类型）；table/pivot/hist 块 → 各自 columns/rows + 合计行）。**关键：`run_query` 返回的单元格值全是字符串（db.py `str(v)` 约定），构造 sheets 时必须按块级 `coltypes` 把 num 列单元格经 `analytics._to_num` 转为 float（转不了的保留字符串），否则 Excel 中整列为文本（绿三角）**。`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`，文件名 `{报表名}.xlsx`。默认 `export_format` 仍 `xls`（存量报表不变，**兼容对**：不传 format 时行为与 v0.6 完全一致）。
- [ ] **Step 4: 集成测试**（`/r/{id}/export?format=xlsx` → zipfile 读回断言 sheet 名与单元格，**并断言 num 列输出 `<v>` 数值单元格而非 inlineStr**）→ **Step 5: Excel/LibreOffice 实开验证一次 → Step 6: 全量测试 + Commit**

```bash
git commit -m "feat(export): 手写零依赖 xlsx 导出"
```

### Task 20: 打印样式（Ctrl+P 即交付物）

**Files:**
- Modify: `src/sqlreport/views_report.py`（M2 拆分后 PAGE 的 CSS 末尾追加）

- [ ] **Step 1: 追加 CSS**

```css
@media print {
  .nav, .btn, .pbar, form { display: none !important; }
  body { background: #fff; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { border: 1px solid #999; }
  .stat { display: inline-block; margin-right: 24px; }
}
```
（类名以拆分后实际 CSS 为准：`.nav`/`.btn`/`.pbar` 均为现有类；KPI 卡打印时保留数值。）

- [ ] **Step 2: 浏览器打印预览冒烟（查看页 Ctrl+P：参数表单/按钮隐藏，表格完整）→ Commit**

```bash
git commit -m "feat(viewer): 打印样式"
```

### Task 21: token 鉴权（P2-2，可选开关）

**Files:**
- Modify: `src/sqlreport/server.py`、`tests/test_server.py`

- [ ] **Step 1: 失败测试**（测试文件头部补 `import hashlib, hmac, time`）

```python
    def test_token_required_when_enabled(self):
        rec = {"name": "鉴权报表", "ds": "demo", "sql": "SELECT region FROM orders"}
        self.save_report(rec, "auth1")
        with open(server.CONFIG_FILE, "w") as f:
            json.dump({"auth": "token", "token_secret": "s3cret"}, f)
        st, _ = self.req("GET", "/r/auth1")
        self.assertEqual(st, 401)
        st, _ = self.req("POST", "/q/auth1", "page=1", "application/x-www-form-urlencoded")
        self.assertEqual(st, 401)                          # 数据出口同样拦截
        t = hmac.new(b"s3cret", b"auth1" + time.strftime("%Y%m%d").encode(),
                     hashlib.sha256).hexdigest()[:16]
        st, _ = self.req("GET", f"/r/auth1?t={t}")
        self.assertEqual(st, 200)

    def test_admin_surface_locked_in_token_mode(self):
        # 管理面（/edit、/save、/delete_report、/preview）在 auth=token 时纳入 _check_admin。
        # 注意：测试基架从 127.0.0.1 连接，_check_admin 的"admin_password 空=仅本机"会对本机恒放行，
        # 因此本用例必须显式配置 admin_password 走 HTTP Basic，否则管理面"被拒"断言必然失败。
        rec = {"name": "鉴权报表", "ds": "demo", "sql": "SELECT region FROM orders"}
        self.save_report(rec, "auth1")
        with open(server.CONFIG_FILE, "w") as f:
            json.dump({"auth": "token", "token_secret": "s3cret", "admin_password": "pw"}, f)
        st, _ = self.req("GET", "/edit/auth1")
        self.assertEqual(st, 401)
        st, _ = self.req("POST", "/save", json.dumps(rec), "application/json")
        self.assertEqual(st, 401)
        st, _ = self.req("POST", "/delete_report", "id=auth1",
                         "application/x-www-form-urlencoded")
        self.assertEqual(st, 401)
        st, _ = self.req("POST", "/preview", json.dumps(rec), "application/json")
        self.assertEqual(st, 401)
        # 带 Basic 头放行
        auth = "Basic " + base64.b64encode(b"admin:pw").decode()
        st, _ = self.req("GET", "/edit/auth1", auth=auth)
        self.assertEqual(st, 200)
```

- [ ] **Step 2: 实现**：`load_config` 读取 `auth/token_secret`（缺省 `off`，行为同现在——**兼容对**：`auth=off` 四态回归用例）；新增 `_mk_token(secret, rid)` 与 `_check_token(rid, args)`（`hmac-sha256(secret, rid+YYYYMMDD)[:16]`，纯 stdlib；**token 基于 rid 而非 name**）；`off` 模式零开销直通。校验点覆盖**三类数据出口**：`/r/{id}`（查看页）、`/q/{id}`（查询接口）、`/r/{id}/export`（导出）——缺一即为绕过点。**管理面范围取舍（锁定，修正失实声明）**：**现状 `_check_admin` 仅在 `/datasources*` 路径调用，`/edit/{id}`、`/save`、`/delete_report`、`/preview` 并无任何保护**（管理面裸露是既有接受的风险，`auth=off` 时维持现状不变）。为使 token 部署形态（语义=对外只读分享）真正成立，`auth=token` 时把 `/edit/{id}`、`/save`、`/delete_report`、`/preview` **一并纳入 `_check_admin`**（admin_password 空=仅本机 / 非空=HTTP Basic）——否则持有报表 id 的外部用户无需凭证即可打开、修改甚至**删除**报表（`/delete_report` 比编辑更危险），"只读分享"落空。`auth=token` 时 `_viewer` 在页面顶部展示「带 token 的分享链接」只读输入框。
- [ ] **Step 3: 集成测试（off/无 token/有 token/错误 token 四态 + `test_admin_surface_locked_in_token_mode` 覆盖 /edit、/save、/delete_report、/preview 无认证被拒 + 带 Basic 放行）→ Step 4: Commit**

```bash
git commit -m "feat(server): 可选 token 鉴权(HMAC, 覆盖 r/q/export)"
```

### Task 22: 报表分组目录（P2-5）+ M4 版本收尾（v0.7.0）

**Files:**
- Modify: `src/sqlreport/server.py`（路由解析、`_list_reports`、`_save` 路径、`do_GET`/`do_POST` 的 path 处理）
- Modify: `src/sqlreport/db.py`（`referenced_by`，约 L150）
- Modify: `tests/test_server.py`

- [ ] **Step 1: 失败测试**（**测试文件头部补 `import urllib.parse`**，`tests/test_server.py` 当前模块级未 import 它）：

```python
    def test_grouped_report_routes(self):
        os.makedirs(os.path.join(self.reports_dir, "sales"), exist_ok=True)
        with open(os.path.join(self.reports_dir, "sales", "daily.json"), "w") as f:
            json.dump({"name": "销售日报", "ds": "demo", "sql": "SELECT region FROM orders"}, f)
        st, _ = self.req("GET", "/r/sales/daily")
        self.assertEqual(st, 200)
        st, body = self.req("POST", "/q/sales/daily", "page=1", "application/x-www-form-urlencoded")
        self.assertEqual(st, 200)
        # CJK 分组：路径百分号编码 + 服务端 unquote
        os.makedirs(os.path.join(self.reports_dir, "销售"), exist_ok=True)
        # …写入 销售/日报.json 后：
        st, _ = self.req("GET", "/r/" + urllib.parse.quote("销售") + "/" + urllib.parse.quote("日报"))
        self.assertEqual(st, 200)

    def test_export_suffix_priority(self):
        # 消歧：/r/{id}/export 恒为导出；分组名或报表 id 为 export/edit 时路径歧义，应被拒绝或按保留字处理
        rec = {"name": "歧义报表", "ds": "demo", "sql": "SELECT region FROM orders"}
        self.save_report(rec, "grp1")
        os.makedirs(os.path.join(self.reports_dir, "grp1"), exist_ok=True)
        st, _ = self.req("GET", "/r/grp1/export")   # 三段路径末尾是保留字 export → 按导出处理（报表 grp1 不存在 → 404 亦属预期，只要不误当分组报表）
        self.assertEqual(st, 404)

    def test_referenced_by_finds_grouped_report(self):
        # 在分组子目录放引用 demo 数据源的报表，删除数据源时引用检查必须能发现（防误删）
```

- [ ] **Step 2: 实现**：
  - `do_GET`/`do_POST` 的 path 统一 `urllib.parse.unquote`（现状无 unquote，CJK 路径百分号编码后无法匹配路由）；
  - 路由解析支持两形态 `/r/{group}/{id}` 与 `/r/{id}`（`/q`、`/r/{...}/export`、`/edit/{...}` 同步支持）；
  - **路由消歧规则（锁定）**：三段路径 `/r/{a}/{b}` 中，当 `{b}` 为保留字 `export`（或 `{a}` 段为 `edit` 时）按原有语义解析（`/r/{id}/export` 恒为导出、`/edit/{id}` 恒为编辑器）；分组名与报表 id 均禁止使用 `export`/`edit` 作名字（`/save` 保存时校验拒绝，中文报错）。测试 `test_export_suffix_priority` 守护此规则；
  - ID 唯一性校验扩展到「分组/ID」；编辑器保存目标路径带分组；列表页按分组折叠展示；**根目录报表完全兼容（兼容对用例）**；
  - `db.referenced_by`（现状 `os.listdir` 非递归）改为递归扫描分组子目录，否则数据源删除的引用检查会漏掉分组报表（防误删红线）。
- [ ] **Step 3: 全量测试 + 冒烟（分组报表查看/查询/导出/删除数据源引用提示）**
- [ ] **Step 4: 版本收尾**：版本号 0.7.0 两处 + CHANGELOG（xlsx/打印/token/分组）→ `git commit -m "chore: v0.7.0 版本收尾"`。

---

## 4. 测试策略（全程有效）

1. **纯函数层**（analytics/xlsx）：unittest 直测，覆盖正常/边界（空表、全空列、0 分母、非数值混入、入参不被修改）/拒绝路径（中文 ValueError 文案断言）。
2. **集成层**：沿用 `tests/test_server.py` 的 ServerTestCase（真实 HTTP + 临时 SQLite），**每个新配置键都必须有「生效用例 + 缺省兼容用例」成对出现**（M1 见 Task 5；M3 的 compare/views 见 Task 15/17；M4 的 format/auth/分组见 Task 19/21/22）。
3. **回归红线**：每个 Task 完成后全量 `python -m unittest discover -s tests -v` 必须全绿；`test_no_analysis_keys_backward_compat`、`test_analysis_on_cache_hit` 等兼容/路径守护用例永远不许删。
4. **人工冒烟**：涉及页面/导出的 Task，重启服务后浏览器走查（项目规则：改 `src/sqlreport/*.py` 必须重启）。

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| `_execute_report` 早退路径（缓存命中）绕过分析管道 | Task 5 强制收敛单一 return；`test_analysis_on_cache_hit` 为唯一守护用例，禁止删 |
| `/save` 白名单遗漏新键导致配置静默丢失 | 决策 D10 写入流程：每个新顶层键的任务内都含「白名单 + 结构校验 + 保存回读断言」步骤 |
| pivot 大列头拖垮页面 | `max_cols=50` 硬顶 + 中文报错引导 SQL 层归类（沿用 P1-2 设计） |
| `run_query` 全字符串化导致 xlsx/导出数值变文本 | Task 19 按 coltypes 用 `_to_num` 转数值单元格 + 测试断言 `<v>`；Task 8 同理处理 `mso-number-format` |
| xlsx 兼容性（Excel 打不开） | 测试断言 zip 结构与单元格编码；Excel/LibreOffice 实开验证为 Task 19 验收项；超预算则降级引 openpyxl 并在 DEVLOG 记录决策（沿用 P2-3 预案） |
| token 校验遗漏出口（如 export） | Task 21 明确三类出口全覆盖 + 四态测试 |
| CJK 路径/分组不可用或误删检查漏报 | Task 22 补 `unquote` + 编码路径用例；`referenced_by` 改递归并加防误删用例 |
| server.py 拆分引入行为漂移 | Task 13 定性为"无行为变更重构"：先迁模板后迁函数，路由不动，全量测试 + 三页面冒烟 |
| 分析在截断后行集上进行导致口径误解 | 查看端口径回显显示"基于 N 行计算"（Task 6），口径=所见=导出 |

## 6. 节奏

M1（v0.4.0）→ M2（v0.5.0）与 M3（v0.6.0）互相独立可按客户反馈调序 → M4（v0.7.0，依赖 M1+M2）；每个里程碑版本收尾全绿后才进入下一个。每任务一个 commit，遵循 conventional commits。
