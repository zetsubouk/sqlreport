#!/usr/bin/env python3
"""analytics.py 纯函数单元测试（零依赖）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlreport.analytics import (total_row, _to_num, summary_metrics, top_n_rows,
                       add_share_columns, bucket_column, pivot, diff_merge, bin_numeric)

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


class TestDiffMerge(unittest.TestCase):
    def test_diff_and_rate(self):
        base = [["华东", 110.0], ["华北", 90.0]]
        right = [["华东", 100.0]]
        cols, rows, types = diff_merge(["区域"], base, ["区域"], right, ["区域"], "金额",
                                       label="上月")
        self.assertEqual(cols, ["区域", "金额", "金额(上月)差值", "金额(上月)增长率%"])
        self.assertEqual(rows[0], ["华东", 110.0, 10.0, 10.0])
        self.assertEqual(rows[1], ["华北", 90.0, "", ""])   # 右侧缺失

    def test_metric_in_cols_no_duplicate(self):
        base = [["华东", 110.0]]
        right = [["华东", 100.0]]
        cols, rows, types = diff_merge(["区域", "金额"], base, ["区域", "金额"], right,
                                       ["区域"], "金额", label="上月",
                                       base_types=["str", "num"])
        self.assertEqual(cols, ["区域", "金额", "金额(上月)差值", "金额(上月)增长率%"])
        self.assertEqual(rows[0], ["华东", 110.0, 10.0, 10.0])
        self.assertEqual(types, ["str", "num", "num", "num"])

    def test_zero_right_metric_blank_rate(self):
        base = [["A", 100.0]]
        right = [["A", 0.0]]
        _, rows, _ = diff_merge(["区域"], base, ["区域"], right, ["区域"], "金额", label="上月")
        self.assertEqual(rows[0][-2], 100.0)   # diff = 100 - 0
        self.assertEqual(rows[0][-1], "")      # r 为 0 → 增长率留空

    def test_str_key_alignment(self):
        # 键取 str 元组：右侧 "1" 与 base 1 视为同键
        base = [[1, 110.0]]
        right = [["1", 100.0]]
        _, rows, _ = diff_merge(["区域"], base, ["区域"], right, ["区域"], "金额", label="上月")
        self.assertEqual(rows[0][-2], 10.0)

    def test_metric_must_be_num(self):
        with self.assertRaises(ValueError):
            diff_merge(["区域", "金额"], [["华东", "x"]], ["区域", "金额"], [["华东", 1.0]],
                       ["区域"], "金额", label="上月", base_types=["str", "str"])

    def test_missing_on_rejected(self):
        with self.assertRaises(ValueError):
            diff_merge(["区域"], [["华东", 1.0]], ["区域"], [["华东", 1.0]], ["不存在"], "金额")

    def test_right_missing_on_rejected(self):
        with self.assertRaises(ValueError):
            diff_merge(["区域"], [["华东", 1.0]], ["其他"], [["华东", 1.0]], ["区域"], "金额")

    def test_input_not_mutated(self):
        base = [["华东", 110.0], ["华北", 90.0]]
        snapshot = [list(r) for r in base]
        diff_merge(["区域"], base, ["区域"], [["华东", 100.0]], ["区域"], "金额", label="上月")
        self.assertEqual(base, snapshot)


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

    def test_null_rows_skipped(self):
        rows = [[1.0], [None], [""], [2.0]]
        _, out, _ = bin_numeric(["v"], rows, ["num"], "v", bins=2)
        self.assertEqual(sum(r[1] for r in out), 2)

    def test_single_interval_when_hi_equals_lo(self):
        cols, out, _ = bin_numeric(["v"], [[5.0], [5.0], [None]], ["num"], "v", bins=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], 2)
        self.assertAlmostEqual(out[0][2], 100.0)

    def test_last_bin_right_closed(self):
        rows = [[1.0], [10.0]]
        _, out, _ = bin_numeric(["v"], rows, ["num"], "v", bins=2)
        self.assertEqual(sum(r[1] for r in out), 2)   # 上界 10 落在末箱（右闭）

    def test_non_num_col_rejected(self):
        with self.assertRaises(ValueError):
            bin_numeric(["v"], [["a"]], ["str"], "v")

    def test_input_not_mutated(self):
        rows = [[1.0], [2.0]]
        snapshot = [list(r) for r in rows]
        bin_numeric(["v"], rows, ["num"], "v", bins=2)
        self.assertEqual(rows, snapshot)
