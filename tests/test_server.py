#!/usr/bin/env python3
"""server.py 进程内集成测试：真实 ThreadingHTTPServer + 临时 sqlite，覆盖全部路由。
回归项：/q JSON 与 form 双形态（Bug#1）、缓存命中/失效、截断、数据源 CRUD、管理页保护。"""
import base64
import http.client
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import server
from db import DatasourceStore, QueryCache

HOST = "127.0.0.1"


def make_demo_db(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE orders (order_id TEXT, cust_id TEXT, region TEXT, amount REAL, dt TEXT)")
    cur.executemany(
        "INSERT INTO orders VALUES (?,?,?,?,?)",
        [("O1", "C1", "华东", 100.0, "2026-01-01"),
         ("O2", "C1", "华东", 250.5, "2026-01-02"),
         ("O3", "C2", "华北", 88.0, "2026-01-03"),
         ("O4", "C3", "华南", 999.0, "2026-01-04"),
         ("O5", "C2", "华北", 33.3, "2026-01-05")])
    cur.execute("CREATE TABLE customers (cust_id TEXT, cname TEXT, level TEXT)")
    cur.executemany("INSERT INTO customers VALUES (?,?,?)",
                    [("C1", "张三", "VIP"), ("C2", "李四", "普通"), ("C3", "王五", "普通")])
    conn.commit()
    conn.close()


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.demodb = os.path.join(self.tmp, "demo.db")
        make_demo_db(self.demodb)
        self.ds_file = os.path.join(self.tmp, "datasources.json")
        self.reports_dir = os.path.join(self.tmp, "reports")
        self.config_file = os.path.join(self.tmp, "config.json")
        os.makedirs(self.reports_dir, exist_ok=True)
        self._backup = (
            db.DS_FILE, db.REPORTS_DIR, db.DS_STORE, db.CACHE,
            server.DS_STORE, server.CACHE, server.REPORTS_DIR, server.CONFIG_FILE)
        db.DS_FILE = self.ds_file
        db.REPORTS_DIR = self.reports_dir
        db.DS_STORE = DatasourceStore(self.ds_file)
        db.CACHE = QueryCache()
        server.DS_STORE = db.DS_STORE
        server.CACHE = db.CACHE
        server.REPORTS_DIR = self.reports_dir
        server.CONFIG_FILE = self.config_file
        db.DS_STORE.save("demo", {"type": "sqlite", "path": self.demodb,
                                  "timeout": 30, "enabled": True, "note": ""})
        self.httpd = ThreadingHTTPServer((HOST, 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        (db.DS_FILE, db.REPORTS_DIR, db.DS_STORE, db.CACHE,
         server.DS_STORE, server.CACHE, server.REPORTS_DIR, server.CONFIG_FILE) = self._backup
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 请求辅助 ----
    def req(self, method, path, body=None, ctype=None, auth=None, raw=False):
        headers = {}
        if ctype:
            headers["Content-Type"] = ctype
        if auth:
            headers["Authorization"] = auth
        if isinstance(body, str):
            body = body.encode("utf-8")
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        if not raw:
            data = data.decode("utf-8")
        self.last_ctype = resp.getheader("Content-Type", "")
        conn.close()
        return resp.status, data

    def get(self, path, auth=None):
        return self.req("GET", path, auth=auth)

    def post_json(self, path, obj, auth=None):
        return self.req("POST", path, body=json.dumps(obj, ensure_ascii=False),
                        ctype="application/json", auth=auth)

    def post_form(self, path, obj):
        import urllib.parse
        return self.req("POST", path, body=urllib.parse.urlencode(obj),
                        ctype="application/x-www-form-urlencoded")

    def save_report(self, rec, rid):
        st, body = self.post_json("/save", {"id": rid, **rec})
        self.assertEqual(st, 200, body)
        return json.loads(body)

    def _save_report(self, extra):
        """分析报表辅助（Task 5 提升到基类）：固定 4 列 SQL + ASCII id analysis1。"""
        rec = {"name": "分析报表", "ds": "demo",
               "sql": "SELECT order_id, region, amount, dt FROM orders ORDER BY order_id"}
        rec.update(extra)
        self.save_report(rec, "analysis1")

    # ---- 基础路由 ----
    def test_index_200(self):
        st, body = self.get("/")
        self.assertEqual(st, 200)
        self.assertIn("SQL 报表", body)

    def test_404(self):
        st, _ = self.get("/nope")
        self.assertEqual(st, 400)

    # ---- 报表保存与查询 ----
    def test_save_and_query_legacy_format(self):
        self.save_report({"name": "订单", "ds": "demo", "sql": "SELECT * FROM orders",
                          "params": [], "cache_ttl": 0}, "orders")
        st, body = self.post_json("/q/orders", {})
        j = json.loads(body)
        self.assertEqual(j["columns"], ["order_id", "cust_id", "region", "amount", "dt"])
        self.assertEqual(len(j["rows"]), 5)
        self.assertFalse(j["cached"])

    def test_save_without_id_generates_random_id(self):
        st, body = self.post_json("/save", {"name": "订单", "ds": "demo",
                                            "sql": "SELECT * FROM orders", "params": [],
                                            "cache_ttl": 0})
        self.assertEqual(st, 200, body)
        j = json.loads(body)
        self.assertRegex(j["id"], r"^[0-9a-f]{16}$")
        self.assertTrue(os.path.exists(os.path.join(self.reports_dir, j["id"] + ".json")))

    def test_save_rejects_invalid_id(self):
        st, body = self.post_json("/save", {"id": "../evil", "name": "订单", "ds": "demo",
                                            "sql": "SELECT * FROM orders", "params": [],
                                            "cache_ttl": 0})
        self.assertEqual(st, 200)
        self.assertIn("error", json.loads(body))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.json")))
        self.assertEqual([f for f in os.listdir(self.reports_dir) if f.endswith(".json")], [])

    def test_query_param_filtering(self):
        self.save_report({"name": "区域", "ds": "demo",
                          "sql": "SELECT * FROM orders\nWHERE region = '{{region}}'",
                          "params": [{"id": "region", "type": "text"}], "cache_ttl": 0}, "by_region")
        st, body = self.post_json("/q/by_region", {"region": "华东"})
        j = json.loads(body)
        self.assertEqual(len(j["rows"]), 2)

    def test_query_optional_param_blank_returns_all(self):
        self.save_report({"name": "区域", "ds": "demo",
                          "sql": "SELECT * FROM orders\nWHERE region = '{{region}}'",
                          "params": [{"id": "region", "type": "text"}], "cache_ttl": 0}, "by_region2")
        st, body = self.post_json("/q/by_region2", {"region": ""})
        self.assertEqual(len(json.loads(body)["rows"]), 5)

    def test_injection_escaped_no_crash(self):
        self.save_report({"name": "区域", "ds": "demo",
                          "sql": "SELECT * FROM orders\nWHERE region = '{{region}}'",
                          "params": [{"id": "region", "type": "text"}], "cache_ttl": 0}, "inj")
        st, body = self.post_json("/q/inj", {"region": "x' OR '1'='1"})
        self.assertEqual(st, 200)
        self.assertEqual(len(json.loads(body)["rows"]), 0)

    def test_json_and_form_equivalent(self):
        rec = {"name": "区域", "ds": "demo",
               "sql": "SELECT * FROM orders\nWHERE region = '{{region}}'",
               "params": [{"id": "region", "type": "text"}], "cache_ttl": 0}
        self.save_report(rec, "eq")
        _, jbody = self.post_json("/q/eq", {"region": "华东"})
        _, fbody = self.post_form("/q/eq", {"region": "华东"})
        self.assertEqual(json.loads(jbody)["rows"], json.loads(fbody)["rows"])

    def test_readonly_sql_rejected(self):
        self.save_report({"name": "坏SQL", "ds": "demo", "sql": "DELETE FROM orders",
                          "params": [], "cache_ttl": 0}, "bad")
        st, body = self.post_json("/q/bad", {})
        j = json.loads(body)
        self.assertIn("error", j)
        self.assertIn("只读", j["error"])

    def test_query_missing_report(self):
        st, body = self.post_json("/q/ghost", {})
        self.assertIn("error", json.loads(body))

    # ---- 缓存 ----
    def test_cache_hit_and_invalidate_on_save(self):
        rec = {"name": "缓存", "ds": "demo", "sql": "SELECT * FROM orders",
               "params": [], "cache_ttl": 60}
        self.save_report(rec, "c1")
        _, first = self.post_json("/q/c1", {})
        _, second = self.post_json("/q/c1", {})
        self.assertFalse(json.loads(first)["cached"])
        self.assertTrue(json.loads(second)["cached"])
        self.save_report(rec, "c1")  # 保存应失效缓存
        _, third = self.post_json("/q/c1", {})
        self.assertFalse(json.loads(third)["cached"])

    def test_truncation_flag(self):
        rec = {"name": "截断", "ds": "demo", "sql": "SELECT * FROM orders",
               "params": [], "cache_ttl": 0, "max_rows": 3}
        self.save_report(rec, "tr")
        _, body = self.post_json("/q/tr", {})
        j = json.loads(body)
        self.assertEqual(len(j["rows"]), 3)
        self.assertTrue(j["truncated"])

    # ---- 多数据集合并 ----
    def test_union_merge(self):
        rec = {"name": "合并", "params": [], "cache_ttl": 0,
               "datasets": [{"name": "a", "ds": "demo",
                             "sql": "SELECT region, amount FROM orders"},
                            {"name": "b", "ds": "demo",
                             "sql": "SELECT region, amount FROM orders"}],
               "merge": {"mode": "union"}}
        self.save_report(rec, "uni")
        _, body = self.post_json("/q/uni", {})
        self.assertEqual(len(json.loads(body)["rows"]), 10)

    def test_lookup_merge(self):
        rec = {"name": "关联", "params": [], "cache_ttl": 0,
               "datasets": [{"name": "base", "ds": "demo",
                             "sql": "SELECT cust_id, order_id, amount FROM orders"},
                            {"name": "dim", "ds": "demo",
                             "sql": "SELECT cust_id, cname FROM customers"}],
               "merge": {"mode": "lookup", "base": "base", "with": "dim",
                         "on": ["cust_id"], "cols": ["cname"]}}
        self.save_report(rec, "lk")
        _, body = self.post_json("/q/lk", {})
        j = json.loads(body)
        self.assertIn("cname", j["columns"])
        self.assertEqual(j["rows"][0][-1], "张三")

    # ---- 预览 ----
    def test_preview(self):
        st, body = self.post_json("/preview",
                                  {"ds": "demo", "sql": "SELECT * FROM orders", "params": []})
        j = json.loads(body)
        self.assertEqual(st, 200)
        self.assertEqual(len(j["rows"]), 5)

    def test_preview_bad_ds(self):
        st, body = self.post_json("/preview", {"ds": "ghost", "sql": "SELECT 1", "params": []})
        self.assertIn("error", json.loads(body))

    # ---- 导出 ----
    def test_export(self):
        self.save_report({"name": "导出", "ds": "demo", "sql": "SELECT * FROM orders",
                          "params": [], "cache_ttl": 0}, "ex")
        st, body = self.get("/r/ex/export")
        self.assertEqual(st, 200)
        self.assertIn("application/vnd.ms-excel", self.last_ctype)
        self.assertIn("order_id", body)
        self.assertIn("华东", body)

    # ---- 数据源管理 ----
    def test_ds_save_toggle_delete(self):
        st, body = self.post_json("/datasources/save",
                                  {"name": "tmp1", "type": "sqlite", "path": self.demodb})
        self.assertEqual(st, 200, body)
        st, body = self.post_json("/datasources/toggle", {"name": "tmp1", "enabled": False})
        self.assertEqual(st, 200)
        self.assertTrue(self.is_ds_hidden("tmp1"))
        st, body = self.post_json("/datasources/delete", {"name": "tmp1"})
        self.assertEqual(st, 200, body)

    def test_ds_delete_referenced_requires_force(self):
        self.save_report({"name": "引", "ds": "demo", "sql": "SELECT 1", "params": []}, "ref1")
        st, body = self.post_json("/datasources/delete", {"name": "demo"})
        j = json.loads(body)
        self.assertIn("referenced", j)
        self.assertEqual(j["referenced"], ["ref1"])
        st, body = self.post_json("/datasources/delete", {"name": "demo", "force": True})
        self.assertEqual(st, 200, body)

    def test_ds_test_connection(self):
        st, body = self.post_json("/datasources/test", {"name": "demo"})
        j = json.loads(body)
        self.assertTrue(j["ok"], j)

    def is_ds_hidden(self, name):
        return name not in db.DS_STORE.visible_names()

    # ---- 管理页保护 ----
    def test_admin_no_password_localhost_allowed(self):
        st, _ = self.get("/datasources")
        self.assertEqual(st, 200)

    def test_admin_password_basic_auth(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump({"admin_password": "pw"}, f)
        st, _ = self.get("/datasources")
        self.assertEqual(st, 401)
        token = "Basic " + base64.b64encode(b"admin:pw").decode()
        st, body = self.get("/datasources", auth=token)
        self.assertEqual(st, 200)
        self.assertIn("数据源管理", body)

    # ---- 查看页 ----
    def test_viewer_page(self):
        self.save_report({"name": "页面", "ds": "demo", "sql": "SELECT * FROM orders",
                          "params": [], "cache_ttl": 0}, "pg")
        st, body = self.get("/r/pg")
        self.assertEqual(st, 200)
        self.assertIn("页面", body)


class AnalysisOnQuery(ServerTestCase):
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
        self.assertEqual(b["title"], "区域汇总")
        self.assertEqual(b["columns"], ["region", "合计"])   # 单维汇总锁定形态（见下）
        self.assertAlmostEqual([r for r in b["rows"] if r[0] == "华东"][0][1], 350.5)
        # 顶层键仍为主表（向后兼容，决策 D4）
        self.assertEqual(j["columns"], ["region", "amount"])

    def test_blocks_default_table_backward_compat(self):
        # 无 blocks 的旧报表：/q 响应 blocks == [{"type":"table",...主表...}] 且顶层键不变
        self._save_report({})
        j = self._q()
        self.assertEqual(len(j["blocks"]), 1)
        b = j["blocks"][0]
        self.assertEqual(b["type"], "table")
        self.assertEqual(b["columns"], j["columns"])
        self.assertEqual(b["rows"], j["rows"])
        self.assertEqual(b["coltypes"], j["coltypes"])

    def test_cache_exempt_for_pivot_block(self):
        # 守护红线：cache_ttl>0 + pivot 块 → 两次 /q 均 blocks 完整且 cached=False（不读不写缓存）
        rec = {"name": "交叉缓存", "ds": "demo",
               "sql": "SELECT region, amount FROM orders",
               "cache_ttl": 60,
               "blocks": [{"type": "pivot", "dataset": "main", "row": "region",
                            "value": "amount", "agg": "sum"}]}
        self.save_report(rec, "pivot2")
        for _ in range(2):
            st, body = self.req("POST", "/q/pivot2", "page=1",
                                "application/x-www-form-urlencoded")
            self.assertEqual(st, 200)
            j = json.loads(body)
            self.assertFalse(j["cached"])                       # 缓存豁免：不读不写
            self.assertEqual(j["blocks"][0]["type"], "pivot")
            self.assertEqual(j["blocks"][0]["columns"], ["region", "合计"])
            self.assertEqual(len(j["blocks"][0]["rows"]), 4)    # 3 区域 + 总计行

    def test_export_contains_total(self):
        self._save_report({"total": {"label": "合计"}})
        st, body = self.req("GET", "/r/analysis1/export")
        self.assertEqual(st, 200)
        self.assertIn("合计", body)
        self.assertIn("1470.8", body.replace(",", ""))    # xls 为 HTML 文本


class CompareOnQuery(ServerTestCase):
    """Task 15：compare 对比差值（环比语义，两个数据集 report 走 /q）。"""

    def _compare_report(self, rid="cmp1", extra=None):
        rec = {"name": "对比", "params": [], "cache_ttl": 0,
               "datasets": [
                   {"name": "cur", "ds": "demo",
                    "sql": "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region ORDER BY region"},
                   {"name": "last", "ds": "demo",
                    "sql": "SELECT region, SUM(amount) AS amount FROM orders WHERE order_id <> 'O5' GROUP BY region ORDER BY region"}],
               "compare": {"dataset": "last", "on": ["region"], "metric": "amount", "label": "上月"}}
        if extra:
            rec.update(extra)
        self.save_report(rec, rid)

    def _q(self, rid="cmp1"):
        st, body = self.req("POST", f"/q/{rid}", "page=1",
                            "application/x-www-form-urlencoded")
        self.assertEqual(st, 200, body)
        return json.loads(body)

    def test_compare_diff_and_rate(self):
        # cur：华东 350.5 / 华北 121.3 / 华南 999.0；last（去 O5）：华东 350.5 / 华北 88.0 / 华南 999.0
        self._compare_report()
        j = self._q()
        self.assertEqual(j["columns"],
                         ["region", "amount", "amount(上月)差值", "amount(上月)增长率%"])
        self.assertEqual(j["rows"][0][0], "华东")
        self.assertAlmostEqual(j["rows"][0][-2], 0.0)
        self.assertAlmostEqual(j["rows"][0][-1], 0.0)
        self.assertEqual(j["rows"][1][0], "华北")
        self.assertAlmostEqual(j["rows"][1][-2], 33.3)
        self.assertAlmostEqual(j["rows"][1][-1], 37.8)
        self.assertEqual(j["rows"][2][0], "华南")
        self.assertAlmostEqual(j["rows"][2][-2], 0.0)
        self.assertEqual(j["coltypes"], ["str", "num", "num", "num"])

    def test_compare_table_block_has_diff_cols(self):
        self._compare_report()
        j = self._q()
        b = j["blocks"][0]
        self.assertEqual(b["type"], "table")
        self.assertIn("amount(上月)增长率%", b["columns"])
        self.assertEqual(len(b["rows"]), 3)

    def test_compare_cache_exempt(self):
        # 含 compare 报表同 pivot/hist 触发缓存豁免：两次 /q 均 cached=False 且 diff 列完整
        self._compare_report("cmp2", {"cache_ttl": 60})
        for _ in range(2):
            j = self._q("cmp2")
            self.assertFalse(j["cached"])
            self.assertIn("amount(上月)差值", j["columns"])

    def test_compare_export_contains_diff_cols(self):
        self._compare_report("cmp3")
        st, body = self.get("/r/cmp3/export")
        self.assertEqual(st, 200)
        self.assertIn("amount(上月)增长率%", body)
        self.assertIn("37.8", body.replace(",", ""))

    def test_no_compare_backward_compat(self):
        # 兼容对：无 compare 键 → 两数据集报表响应与 M2 完全一致（无差值/增长率列）
        rec = {"name": "兼容", "params": [], "cache_ttl": 0,
               "datasets": [
                   {"name": "a", "ds": "demo", "sql": "SELECT region FROM orders"},
                   {"name": "b", "ds": "demo", "sql": "SELECT region FROM orders"}]}
        self.save_report(rec, "cmp4")
        j = self._q("cmp4")
        self.assertNotIn("差值", j["columns"])
        self.assertNotIn("增长率", j["columns"])
        self.assertEqual(len(j["rows"]), 10)   # 仍为 union 合并

    def test_save_compare_missing_required_rejected(self):
        rec = {"name": "坏对比", "ds": "demo", "sql": "SELECT 1",
               "compare": {"dataset": "last", "on": ["region"]}}   # 缺 metric
        st, body = self.post_json("/save", {"id": "badcmp", **rec})
        j = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIn("error", j)
        self.assertFalse(os.path.exists(os.path.join(self.reports_dir, "badcmp.json")))

    def test_compare_missing_dataset_rejected(self):
        self._compare_report("cmp5", {"compare": {"dataset": "ghost", "on": ["region"],
                                                  "metric": "amount", "label": "上月"}})
        st, body = self.req("POST", "/q/cmp5", "page=1",
                            "application/x-www-form-urlencoded")
        j = json.loads(body)
        self.assertIn("error", j)
        self.assertIn("compare 引用的数据集不存在", j["error"])


class HistBlockOnQuery(ServerTestCase):
    """Task 16：数值分箱统计表 hist 块（静态块，与 pivot 同机制）。"""

    def _q(self, rid="hist1"):
        st, body = self.req("POST", f"/q/{rid}", "page=1",
                            "application/x-www-form-urlencoded")
        self.assertEqual(st, 200, body)
        return json.loads(body)

    def test_q_blocks_hist(self):
        rec = {"name": "分箱", "ds": "demo", "sql": "SELECT amount FROM orders",
               "blocks": [{"type": "hist", "dataset": "main", "col": "amount",
                           "bins": 5, "title": "金额分布"}]}
        self.save_report(rec, "hist1")
        j = self._q()
        b = j["blocks"][0]
        self.assertEqual(b["type"], "hist")
        self.assertEqual(b["title"], "金额分布")
        self.assertEqual(b["columns"], ["区间", "计数", "占比%"])
        self.assertEqual(b["coltypes"], ["str", "num", "num"])
        self.assertEqual(sum(r[1] for r in b["rows"]), 5)   # 5 个订单金额全部入箱
        self.assertAlmostEqual(sum(r[2] for r in b["rows"]), 100.0, delta=0.3)
        # 顶层键仍为主表（向后兼容，决策 D4）
        self.assertEqual(j["columns"], ["amount"])

    def test_hist_cache_exempt(self):
        # 守护红线：cache_ttl>0 + hist 块 → 两次 /q 均 hist 完整且 cached=False
        rec = {"name": "分箱缓存", "ds": "demo", "sql": "SELECT amount FROM orders",
               "cache_ttl": 60,
               "blocks": [{"type": "hist", "dataset": "main", "col": "amount", "bins": 5}]}
        self.save_report(rec, "hist2")
        for _ in range(2):
            j = self._q("hist2")
            self.assertFalse(j["cached"])
            self.assertEqual(j["blocks"][0]["type"], "hist")
            self.assertEqual(sum(r[1] for r in j["blocks"][0]["rows"]), 5)

    def test_export_hist_section(self):
        rec = {"name": "分箱", "ds": "demo", "sql": "SELECT amount FROM orders",
               "blocks": [{"type": "hist", "dataset": "main", "col": "amount",
                           "bins": 5, "title": "金额分布"}]}
        self.save_report(rec, "hist1")
        st, body = self.get("/r/hist1/export")
        self.assertEqual(st, 200)
        self.assertIn("<h3>金额分布</h3>", body)
        self.assertIn("<th>区间</th>", body)
        self.assertIn("<th>计数</th>", body)

    def test_save_hist_missing_col_rejected(self):
        rec = {"name": "坏分箱", "ds": "demo", "sql": "SELECT amount FROM orders",
               "blocks": [{"type": "hist", "bins": 5}]}
        st, body = self.post_json("/save", {"id": "badhist", **rec})
        j = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIn("error", j)
        self.assertFalse(os.path.exists(os.path.join(self.reports_dir, "badhist.json")))

    def test_no_hist_backward_compat(self):
        # 兼容对：无 hist 块的报表行为不变（blocks 默认单 table 块，无区间列）
        self.save_report({"name": "无分箱", "ds": "demo", "sql": "SELECT amount FROM orders"},
                         "hist3")
        j = self._q("hist3")
        self.assertEqual(len(j["blocks"]), 1)
        self.assertEqual(j["blocks"][0]["type"], "table")
        self.assertNotIn("区间", j["blocks"][0]["columns"])
        self.assertEqual(len(j["rows"]), 5)


class BlocksViewAndExport(ServerTestCase):
    """Task 12：查看页多块渲染 / 导出多块 / 编辑器 blocks 配置（服务端可测面）。"""

    def _save_pivot_report(self, rid="blk1", title="区域汇总"):
        rec = {"name": "交叉导出", "ds": "demo",
               "sql": "SELECT region, amount FROM orders",
               "blocks": [{"type": "pivot", "dataset": "main", "row": "region",
                           "value": "amount", "agg": "sum", "title": title}]}
        self.save_report(rec, rid)

    def test_viewer_blocks_page_ok(self):
        self._save_pivot_report()
        st, body = self.get("/r/blk1")
        self.assertEqual(st, 200)
        self.assertIn("交叉导出", body)
        self.assertIn('id="kpi"', body)          # 查看页 KPI 容器仍在

    def test_export_blocks_h3_sections(self):
        self._save_pivot_report()
        st, body = self.get("/r/blk1/export")
        self.assertEqual(st, 200)
        self.assertIn("<h3>区域汇总</h3>", body)  # pivot 块标题
        self.assertIn("<th>region</th>", body)
        self.assertIn("<th>合计</th>", body)      # 单维 pivot 列头
        self.assertIn("总计", body)               # col_total 总计行

    def test_export_old_report_backward_compat(self):
        # 无 blocks 旧报表：导出与 v0.4 一致（无 h3 段，仍含主表）
        self.save_report({"name": "旧报表", "ds": "demo",
                          "sql": "SELECT region, amount FROM orders",
                          "params": [], "cache_ttl": 0}, "old1")
        st, body = self.get("/r/old1/export")
        self.assertEqual(st, 200)
        self.assertNotIn("<h3>", body)
        self.assertIn("<table border=\"1\">", body)
        self.assertIn("华东", body)

    def test_export_csv_still_main_only(self):
        # csv 仍只导主表（文档注明），不含 pivot 总计行
        self._save_pivot_report()
        st, body = self.get("/r/blk1/export?format=csv")
        self.assertEqual(st, 200)
        self.assertIn("region", body)
        self.assertNotIn("总计", body)

    def test_editor_new_has_blocks_textarea(self):
        st, body = self.get("/new")
        self.assertEqual(st, 200)
        self.assertIn('id="rblocks"', body)

    def test_editor_blocks_textarea_prefilled(self):
        self._save_pivot_report()
        st, body = self.get("/edit/blk1")
        self.assertEqual(st, 200)
        self.assertIn('id="rblocks"', body)
        self.assertIn("区域汇总", body)   # 预填已存 blocks JSON

    def test_editor_save_blocks_roundtrip(self):
        # 编辑器保存路径：带 blocks 字段提交 → /save 经 normalize_blocks 校验后入库
        self.save_report({"name": "往返", "ds": "demo", "sql": "SELECT region, amount FROM orders",
                          "blocks": [{"type": "pivot", "dataset": "main", "row": "region",
                                      "value": "amount", "agg": "sum"}]}, "rt1")
        r = server.load_json(os.path.join(self.reports_dir, "rt1.json"))
        self.assertEqual(r["blocks"][0]["type"], "pivot")
        self.assertEqual(r["blocks"][0]["row"], "region")

    def test_editor_save_rejects_bad_blocks(self):
        # 非法块类型：/save 返回 error 且不落盘
        rec = {"name": "坏块", "ds": "demo", "sql": "SELECT region FROM orders",
               "blocks": [{"type": "chart"}]}
        st, body = self.post_json("/save", {"id": "badblk", **rec})
        j = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIn("error", j)
        self.assertFalse(os.path.exists(os.path.join(self.reports_dir, "badblk.json")))


class ViewsBarTest(ServerTestCase):
    """保存视图（Task 17）：/save 白名单落盘 + 查看页快捷链接（URL 即状态）。"""

    def test_save_views_whitelist_persists(self):
        rec = {"name": "视图报表", "ds": "demo", "sql": "SELECT region FROM orders",
               "views": [{"name": "本月", "params": {"d": "2026-09-01", "d_2": "2026-09-30"}},
                         {"name": "无参数", "params": {}}]}
        self.save_report(rec, "views1")
        r = server.load_json(os.path.join(self.reports_dir, "views1.json"))
        self.assertEqual(r["views"][0]["name"], "本月")
        self.assertEqual(r["views"][0]["params"], {"d": "2026-09-01", "d_2": "2026-09-30"})

    def test_viewer_renders_view_links(self):
        rec = {"name": "视图报表", "ds": "demo", "sql": "SELECT region FROM orders",
               "views": [{"name": "本月", "params": {"d": "2026-09-01", "d_2": "2026-09-30"}}]}
        self.save_report(rec, "views2")
        st, body = self.get("/r/views2")
        self.assertEqual(st, 200)
        self.assertIn("快捷视图", body)
        self.assertIn("/r/views2?d=2026-09-01", body)  # urlencode 后的参数链接
        self.assertIn(">本月</a>", body)

    def test_viewer_without_views_no_bar(self):
        # 兼容对：无 views 键的查看页无快捷区，页面结构与旧版一致
        rec = {"name": "无视图", "ds": "demo", "sql": "SELECT region FROM orders"}
        self.save_report(rec, "views3")
        st, body = self.get("/r/views3")
        self.assertEqual(st, 200)
        self.assertNotIn("快捷视图", body)


class XlsxExportTest(ServerTestCase):
    """Task 19：真 .xlsx 导出（format=xlsx 查询参数 + export_format 白名单）。"""

    def _export_xlsx(self, rid):
        st, body = self.req("GET", f"/r/{rid}/export?format=xlsx", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("spreadsheetml.sheet", self.last_ctype)
        return zipfile.ZipFile(io.BytesIO(body))

    def test_export_xlsx_basic(self):
        # region 拼 '<b>' 验证端到端 XML 转义；amount 验证 num 列 <v> 数值单元格
        self.save_report({"name": "xlsx报表", "ds": "demo",
                          "sql": "SELECT order_id, region || '<b>' AS region, amount, dt"
                                 " FROM orders ORDER BY order_id",
                          "params": [], "cache_ttl": 0}, "x1")
        z = self._export_xlsx("x1")
        self.assertEqual(z.namelist()[0], "[Content_Types].xml")
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('name="数据"', wb)                    # 无 title 的 table 块默认 sheet 名
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('t="inlineStr"', sheet)
        self.assertIn("华东&lt;b&gt;", sheet)               # 端到端 XML 转义
        # num 列输出 <v> 数值单元格而非 inlineStr（否则 Excel 整列文本/绿三角）
        self.assertIn('<c r="C2"><v>100.0</v></c>', sheet)

    def test_export_xlsx_summary_and_total_sheets(self):
        rec = {"name": "摘要合计", "ds": "demo",
               "sql": "SELECT order_id, region, amount, dt FROM orders ORDER BY order_id",
               "params": [], "cache_ttl": 0,
               "summary": [{"col": "amount", "fn": "sum", "label": "销售额"},
                           {"col": "order_id", "fn": "count", "label": "单数"}],
               "total": {"label": "合计"}}
        self.save_report(rec, "x2")
        z = self._export_xlsx("x2")
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('name="摘要"', wb)
        s1 = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("销售额", s1)
        self.assertIn("<v>1470.8</v>", s1)                  # summary 数值
        self.assertIn("<v>5</v>", s1)                       # count 计数
        s2 = z.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("合计", s2)
        self.assertIn("<v>1470.8</v>", s2)                  # 主表末行附合计行（与 xls/csv 口径一致）

    def test_export_xlsx_pivot_block_sheet(self):
        rec = {"name": "交叉xlsx", "ds": "demo", "sql": "SELECT region, amount FROM orders",
               "params": [], "cache_ttl": 0,
               "blocks": [{"type": "pivot", "dataset": "main", "row": "region",
                           "value": "amount", "agg": "sum", "title": "区域汇总"}]}
        self.save_report(rec, "x3")
        z = self._export_xlsx("x3")
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('name="区域汇总"', wb)                # 块 title 作为 sheet 名
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("<v>350.5</v>", sheet)                # 华东 sum
        self.assertIn("总计", sheet)                        # col_total 总计行

    def test_export_format_xlsx_as_default(self):
        # export_format 白名单接纳 xlsx：保存后不带 format 参数的导出走 xlsx
        self.save_report({"name": "默认xlsx", "ds": "demo", "sql": "SELECT region FROM orders",
                          "params": [], "export_format": "xlsx"}, "x4")
        st, body = self.req("GET", "/r/x4/export", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("spreadsheetml.sheet", self.last_ctype)
        z = zipfile.ZipFile(io.BytesIO(body))
        self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
        r = server.load_json(os.path.join(self.reports_dir, "x4.json"))
        self.assertEqual(r["export_format"], "xlsx")

    def test_export_default_xls_backward_compat(self):
        # 兼容对：不传 format 且 export_format 缺省 → 与 v0.6 完全一致（HTML-.xls）
        self.save_report({"name": "旧格式", "ds": "demo", "sql": "SELECT region FROM orders",
                          "params": [], "cache_ttl": 0}, "x5")
        st, body = self.get("/r/x5/export")
        self.assertEqual(st, 200)
        self.assertIn("application/vnd.ms-excel", self.last_ctype)
        self.assertIn("<table border=\"1\">", body)

    def test_editor_and_viewer_have_xlsx_option(self):
        self.save_report({"name": "选项", "ds": "demo", "sql": "SELECT region FROM orders",
                          "params": []}, "x6")
        st, body = self.get("/edit/x6")
        self.assertEqual(st, 200)
        self.assertIn('<option value="xlsx">Excel .xlsx</option>', body)
        st, body = self.get("/r/x6")
        self.assertEqual(st, 200)
        self.assertIn('<option value="xlsx">格式 .xlsx</option>', body)


if __name__ == "__main__":
    unittest.main()