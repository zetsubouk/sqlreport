#!/usr/bin/env python3
"""analytics.py 纯函数单元测试（零依赖）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import total_row, _to_num, summary_metrics, top_n_rows, add_share_columns

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
