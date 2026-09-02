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
