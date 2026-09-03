#!/usr/bin/env python3
"""params.py 纯函数单元测试（零依赖，python3 -m unittest discover tests）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlreport.params import esc, build_values, substitute, normalize_report, normalize_blocks


class TestEsc(unittest.TestCase):
    def test_single_quote_doubled(self):
        self.assertEqual(esc("x' OR '1'='1"), "x'' OR ''1''=''1")

    def test_plain_value_unchanged(self):
        self.assertEqual(esc("华东"), "华东")

    def test_none_coerced(self):
        self.assertEqual(esc(None), "None")


class TestBuildValues(unittest.TestCase):
    def test_text_filled(self):
        v = build_values("", [{"id": "region", "type": "text"}], {"region": "华东"})
        self.assertEqual(v, {"region": "华东"})

    def test_text_blank_skipped(self):
        self.assertEqual(build_values("", [{"id": "region"}], {"region": ""}), {})

    def test_daterange_expands_both_styles(self):
        p = [{"id": "d", "type": "daterange"}]
        v = build_values("", p, {"d": "2026-01-01", "d_2": "2026-01-31"})
        self.assertEqual(v.get("d_begin"), "2026-01-01")
        self.assertEqual(v.get("d.begin"), "2026-01-01")
        self.assertEqual(v.get("d_end"), "2026-01-31")
        self.assertEqual(v.get("d.end"), "2026-01-31")

    def test_daterange_partial_end_only(self):
        p = [{"id": "d", "type": "daterange"}]
        v = build_values("", p, {"d": "", "d_2": "2026-01-31"})
        self.assertNotIn("d_begin", v)
        self.assertEqual(v.get("d_end"), "2026-01-31")

    def test_numrange_expands(self):
        p = [{"id": "n", "type": "numrange"}]
        v = build_values("", p, {"n": "10", "n_2": "99"})
        self.assertEqual(v.get("n_min"), "10")
        self.assertEqual(v.get("n.max"), "99")

    def test_value_escaped_on_fill(self):
        v = build_values("", [{"id": "x", "type": "text"}], {"x": "a'b"})
        self.assertEqual(v["x"], "a''b")


class TestSubstitute(unittest.TestCase):
    def test_replaces_filled(self):
        sql = "SELECT * FROM t WHERE region = '{{region}}'"
        out = substitute(sql, {"region": "华东"})
        self.assertEqual(out, "SELECT * FROM t WHERE region = '华东'")

    def test_drops_whole_line_when_unfilled(self):
        sql = "SELECT * FROM t\nWHERE region = '{{region}}'\nAND x = 1"
        out = substitute(sql, {})
        self.assertNotIn("region", out)
        self.assertIn("AND x = 1", out)

    def test_optional_line_dropped_whole_line(self):
        # 文档约定：条件务必独立成行；含未填占位符的整行丢弃
        sql = "SELECT * FROM t WHERE a = 1 AND b = '{{p}}'"
        out = substitute(sql, {})
        self.assertEqual(out, "")

    def test_optional_condition_isolated_to_own_line(self):
        sql = "SELECT * FROM t\nWHERE a = 1\nAND b = '{{p}}'\nORDER BY a"
        out = substitute(sql, {})
        self.assertNotIn("b = ", out)
        self.assertIn("WHERE a = 1", out)
        self.assertIn("ORDER BY a", out)

    def test_dotted_placeholder(self):
        sql = "dt >= '{{d.begin}}' AND dt <= '{{d.end}}'"
        out = substitute(sql, {"d.begin": "2026-01-01", "d.end": "2026-01-31"})
        self.assertIn("2026-01-01", out)
        self.assertIn("2026-01-31", out)

    def test_no_placeholder_unchanged(self):
        sql = "SELECT 1"
        self.assertEqual(substitute(sql, {}), sql)


class TestNormalizeReport(unittest.TestCase):
    def test_legacy_single_sql(self):
        d = normalize_report({"name": "r", "ds": "a", "sql": "SELECT 1"})
        self.assertEqual(d, [{"name": "main", "ds": "a", "sql": "SELECT 1"}])

    def test_datasets_passthrough(self):
        ds = [{"name": "x", "ds": "a", "sql": "S1"}, {"name": "y", "ds": "b", "sql": "S2"}]
        self.assertEqual(normalize_report({"datasets": ds}), ds)

    def test_missing_name_auto(self):
        d = normalize_report({"datasets": [{"ds": "a", "sql": "S"}]})
        self.assertEqual(d[0]["name"], "ds1")

    def test_missing_both_raises(self):
        with self.assertRaises(ValueError):
            normalize_report({"name": "r"})

    def test_duplicate_names_raise(self):
        ds = [{"name": "x", "sql": "S1"}, {"name": "x", "sql": "S2"}]
        with self.assertRaises(ValueError):
            normalize_report({"datasets": ds})

    def test_ds_names_validation(self):
        ds = [{"name": "x", "ds": "ghost", "sql": "S"}]
        with self.assertRaises(ValueError):
            normalize_report({"datasets": ds}, ds_names={"real"})


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

    def test_hist_valid(self):
        r = {"blocks": [{"type": "hist", "col": "amount", "bins": 5}]}
        self.assertEqual(normalize_blocks(r), r["blocks"])

    def test_hist_missing_col_rejected(self):
        with self.assertRaises(ValueError):
            normalize_blocks({"blocks": [{"type": "hist", "bins": 5}]})


if __name__ == "__main__":
    unittest.main()