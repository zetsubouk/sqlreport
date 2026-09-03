#!/usr/bin/env python3
"""db.py 数据层单元测试：只读校验 / union / lookup / QueryCache / DatasourceStore"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class TestSqlIsReadonly(unittest.TestCase):
    def test_select_ok(self):
        self.assertTrue(db.sql_is_readonly("SELECT * FROM t"))

    def test_with_ok(self):
        self.assertTrue(db.sql_is_readonly("WITH x AS (SELECT 1) SELECT * FROM x"))

    def test_leading_whitespace_ok(self):
        self.assertTrue(db.sql_is_readonly("\n  select a from t"))

    def test_insert_rejected(self):
        self.assertFalse(db.sql_is_readonly("INSERT INTO t VALUES (1)"))

    def test_update_rejected(self):
        self.assertFalse(db.sql_is_readonly("UPDATE t SET a = 1"))

    def test_delete_rejected(self):
        self.assertFalse(db.sql_is_readonly("DELETE FROM t"))

    def test_multi_statement_rejected(self):
        self.assertFalse(db.sql_is_readonly("SELECT 1; SELECT 2"))

    def test_trailing_semicolon_single_statement_ok(self):
        # 单条语句 + 尾分号视为单语句（设计仅拒绝多语句）
        self.assertTrue(db.sql_is_readonly("SELECT 1;"))

    def test_drop_rejected(self):
        self.assertFalse(db.sql_is_readonly("DROP TABLE t"))

    def test_line_comment_stripped(self):
        self.assertTrue(db.sql_is_readonly("-- comment\nSELECT * FROM t"))

    def test_block_comment_stripped(self):
        self.assertTrue(db.sql_is_readonly("/* c */ SELECT * FROM t"))

    def test_hidden_write_after_comment_rejected(self):
        self.assertFalse(db.sql_is_readonly("-- hi\nDELETE FROM t"))


class TestStripSemicolon(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(db.strip_semicolon("SELECT 1"), "SELECT 1")

    def test_trailing_semicolon(self):
        self.assertEqual(db.strip_semicolon("SELECT 1;"), "SELECT 1")

    def test_multiple_trailing_semicolons(self):
        self.assertEqual(db.strip_semicolon("SELECT 1;;  "), "SELECT 1")

    def test_none(self):
        self.assertEqual(db.strip_semicolon(None), "")

    def test_wrapped_subquery_passes_readonly(self):
        # Bug#3：尾分号 + 包一层 SELECT 不再被只读校验误判为多语句
        inner = db.strip_semicolon("SELECT a FROM t;")
        self.assertTrue(db.sql_is_readonly(f"SELECT * FROM ({inner}) t WHERE 1=0"))


class TestMergeUnion(unittest.TestCase):
    def test_align_by_name(self):
        left = (["id", "amt"], [("1", "10")], ["str", "num"])
        right = (["amt", "id"], [("20", "2")], ["num", "str"])
        cols, rows, coltypes = db.merge_union([left, right])
        self.assertEqual(cols, ["id", "amt"])
        self.assertEqual(rows, [("1", "10"), ("2", "20")])
        self.assertEqual(coltypes, ["str", "num"])  # 类型取第一个数据集

    def test_missing_col_filled_empty(self):
        a = (["id", "amt"], [("1", "10")], ["str", "num"])
        b = (["id"], [("2",)], ["str"])
        _, rows, coltypes = db.merge_union([a, b])
        self.assertEqual(rows, [("1", "10"), ("2", "")])
        self.assertEqual(coltypes, ["str", "num"])

    def test_extra_right_col_dropped(self):
        a = (["id"], [("1",)], ["str"])
        b = (["id", "extra"], [("2", "x")], ["num", "str"])
        cols, rows, _ = db.merge_union([a, b])
        self.assertEqual(cols, ["id"])
        self.assertEqual(rows, [("1",), ("2",)])

    def test_empty(self):
        self.assertEqual(db.merge_union([]), ([], [], []))


class TestMergeLookup(unittest.TestCase):
    def setUp(self):
        self._orig = db.LOOKUP_RIGHT_MAX
        db.LOOKUP_RIGHT_MAX = 5

    def tearDown(self):
        db.LOOKUP_RIGHT_MAX = self._orig

    def test_basic_lookup(self):
        base = (["cust_id", "order_id"], [("C1", "O1"), ("C2", "O2"), ("C9", "O3")])
        right = (["cust_id", "cname"], [("C1", "张三"), ("C2", "李四")])
        cols, rows, coltypes = db.merge_lookup(base[0], base[1], right[0], right[1], ["cust_id"])
        self.assertEqual(cols, ["cust_id", "order_id", "cname"])
        self.assertEqual(rows, [("C1", "O1", "张三"), ("C2", "O2", "李四"), ("C9", "O3", "")])
        self.assertEqual(coltypes, ["str", "str", "str"])  # 缺省类型视为 str

    def test_multi_key(self):
        base = (["k1", "k2", "v"], [("a", "1", "X")])
        right = (["k2", "k1", "val"], [("1", "a", "Y")])
        _, rows, _ = db.merge_lookup(base[0], base[1], right[0], right[1], ["k1", "k2"])
        self.assertEqual(rows, [("a", "1", "X", "Y")])

    def test_take_subset(self):
        base = (["id"], [("1",)])
        right = (["id", "a", "b"], [("1", "A", "B")])
        cols, rows, _ = db.merge_lookup(base[0], base[1], right[0], right[1], ["id"], cols=["b"])
        self.assertEqual(cols, ["id", "b"])
        self.assertEqual(rows, [("1", "B")])

    def test_default_take_excludes_key(self):
        base = (["id"], [("1",)])
        right = (["id", "a"], [("1", "A")])
        cols, _, _ = db.merge_lookup(base[0], base[1], right[0], right[1], ["id"])
        self.assertEqual(cols, ["id", "a"])

    def test_duplicate_key_first_wins(self):
        base = (["id"], [("1",)])
        right = (["id", "v"], [("1", "first"), ("1", "second")])
        _, rows, _ = db.merge_lookup(base[0], base[1], right[0], right[1], ["id"])
        self.assertEqual(rows, [("1", "first")])

    def test_right_too_large_rejected(self):
        right_rows = [(str(i),) for i in range(6)]
        with self.assertRaises(ValueError):
            db.merge_lookup(["id"], [("0",)], ["id"], right_rows, ["id"])

    def test_missing_on_raises(self):
        with self.assertRaises(ValueError):
            db.merge_lookup(["id"], [("1",)], ["id"], [("1",)], [])

    def test_missing_key_col_raises(self):
        with self.assertRaises(ValueError):
            db.merge_lookup(["id"], [("1",)], ["other"], [("1",)], ["id"])

    def test_missing_take_col_raises(self):
        with self.assertRaises(ValueError):
            db.merge_lookup(["id"], [("1",)], ["id"], [("1",)], ["id"], cols=["nope"])


class TestQueryCache(unittest.TestCase):
    def test_put_get_hit(self):
        c = db.QueryCache()
        key = c.make_key("r1", {"a": "1"})
        c.put(key, ["a"], [("1",)], ttl=60)
        self.assertEqual(c.get(key), (["a"], [("1",)], False, None))  # coltypes 未传则缓存 None

    def test_ttl_expiry(self):
        c = db.QueryCache()
        key = c.make_key("r1", {})
        c.put(key, ["a"], [("1",)], ttl=0.1)
        time.sleep(0.15)
        self.assertIsNone(c.get(key))

    def test_missing_key(self):
        self.assertIsNone(db.QueryCache().get("nope"))

    def test_ttl_zero_not_cached(self):
        c = db.QueryCache()
        key = c.make_key("r1", {})
        c.put(key, ["a"], [("1",)], ttl=0)
        self.assertIsNone(c.get(key))

    def test_entry_too_large_not_cached(self):
        c = db.QueryCache()
        c.MAX_ENTRY_ROWS = 2
        key = c.make_key("r1", {})
        c.put(key, ["a"], [("x",)] * 3, ttl=60)
        self.assertIsNone(c.get(key))

    def test_lru_eviction(self):
        c = db.QueryCache()
        c.MAX_ENTRIES = 2
        keys = [c.make_key("r", {"i": str(i)}) for i in range(3)]
        for k in keys:
            c.put(k, ["a"], [k], ttl=60)
        self.assertIsNone(c.get(keys[0]))  # 最旧被驱逐
        self.assertIsNotNone(c.get(keys[2]))

    def test_invalidate_only_target_report(self):
        c = db.QueryCache()
        k1 = c.make_key("r1", {})
        k2 = c.make_key("r2", {})
        c.put(k1, ["a"], [("1",)], ttl=60)
        c.put(k2, ["a"], [("2",)], ttl=60)
        c.invalidate("r1")
        self.assertIsNone(c.get(k1))
        self.assertIsNotNone(c.get(k2))

    def test_key_canonicalizes_param_order(self):
        c = db.QueryCache()
        self.assertEqual(c.make_key("r", {"b": "1", "a": "2"}), c.make_key("r", {"a": "2", "b": "1"}))

    def test_cached_truncated_flag_preserved(self):
        c = db.QueryCache()
        key = c.make_key("r1", {})
        c.put(key, ["a"], [("1",)], ttl=60, truncated=True)
        cols, rows, tr, coltypes = c.get(key)
        self.assertTrue(tr)
        self.assertIsNone(coltypes)


class TestDatasourceStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ds_file = os.path.join(self.tmp, "datasources.json")
        self.reports_dir = os.path.join(self.tmp, "reports")
        os.makedirs(self.reports_dir, exist_ok=True)
        self._orig_reports = db.REPORTS_DIR
        db.REPORTS_DIR = self.reports_dir
        self.store = db.DatasourceStore(self.ds_file)

    def tearDown(self):
        db.REPORTS_DIR = self._orig_reports
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_load(self):
        self.store.save("a", {"type": "sqlite", "path": "x.db"})
        self.assertEqual(self.store.get("a")["path"], "x.db")
        with open(self.ds_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["a"]["path"], "x.db")

    def test_mtime_lazy_reload(self):
        self.store.save("a", {"type": "sqlite"})
        # 外部修改文件，mtime 变化后 get 应看到新值
        with open(self.ds_file, encoding="utf-8") as f:
            d = json.load(f)
        d["a"]["path"] = "new.db"
        with open(self.ds_file, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.utime(self.ds_file, (time.time() + 5, time.time() + 5))  # 强制 mtime 前进，防时间戳粒度偶发
        self.assertEqual(self.store.get("a")["path"], "new.db")

    def test_delete(self):
        self.store.save("a", {})
        self.store.delete("a")
        self.assertIsNone(self.store.get("a"))

    def test_delete_missing_raises(self):
        with self.assertRaises(ValueError):
            self.store.delete("ghost")

    def test_is_enabled_rules(self):
        self.store.save("a", {"enabled": True})
        self.store.save("b", {"enabled": False})
        self.store.save("_c", {"enabled": True})   # _ 前缀 = 禁用
        self.store.save("_d", {"enabled": False})
        self.assertTrue(self.store.is_enabled("a"))
        self.assertFalse(self.store.is_enabled("b"))
        self.assertFalse(self.store.is_enabled("_c"))
        self.assertFalse(self.store.is_enabled("_d"))
        self.assertFalse(self.store.is_enabled("ghost"))
        names = self.store.visible_names()
        self.assertEqual(names, ["a"])

    def test_toggle_removes_underscore_prefix(self):
        self.store.save("_old", {"enabled": True})
        real = self.store.toggle("_old", enabled=True)
        self.assertEqual(real, "old")
        self.assertTrue(self.store.is_enabled("old"))
        self.assertIsNone(self.store.get("_old"))

    def test_referenced_by(self):
        self.store.save("a", {"type": "sqlite"})
        self.store.save("b", {"type": "sqlite"})
        rec_single = {"ds": "a", "sql": "SELECT 1"}
        rec_multi = {"datasets": [{"name": "x", "ds": "b", "sql": "S"}]}
        for rid, rec in (("one", rec_single), ("two", rec_multi)):
            with open(os.path.join(self.reports_dir, rid + ".json"), "w", encoding="utf-8") as f:
                json.dump(rec, f)
        self.assertEqual(self.store.referenced_by("a"), ["one"])
        self.assertEqual(self.store.referenced_by("b"), ["two"])
        self.assertEqual(self.store.referenced_by("ghost"), [])


if __name__ == "__main__":
    unittest.main()