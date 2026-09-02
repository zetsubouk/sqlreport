# sqlreport - 轻量 SQL 报表工具
# Copyright (c) 2026 zetsubouk
# SPDX-License-Identifier: MIT

#!/usr/bin/env python3
"""轻量 SQL 报表工具：贴 SQL + 参数条件 + 独立 URL + Excel 导出。
Python 标准库实现；MySQL/SQLServer 驱动按需懒加载（pymysql / pyodbc）。
运行: python3 server.py [端口]  默认 8765
"""
import base64, json, os, re, secrets, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlreport import views_report
from sqlreport.db import CACHE, DS_STORE, load_json, run_query, merge_union, merge_lookup
from sqlreport.params import build_values, substitute, normalize_report, esc, normalize_blocks
from sqlreport.analytics import (total_row, summary_metrics, top_n_rows, add_share_columns,
                                 bucket_column, pivot)
from sqlreport.views_report import PAGE, nav, page, esc_html, _rel_time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(BASE, "reports")
CONFIG_FILE = os.path.join(BASE, "config.json")

def load_config():
    """全局配置（config.json，不入库）。缺省：auth=off，admin_password 空 → 管理页仅本机可访问。"""
    try:
        return load_json(CONFIG_FILE)
    except Exception:
        return {"auth": "off", "admin_password": ""}

# ---------------- HTTP 处理 ----------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默访问日志
        pass

    def _send(self, data, ctype="text/html; charset=utf-8", extra=""):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if extra:
            self.send_header("Content-Disposition", extra)
        self.end_headers()
        self.wfile.write(data)

    def _err(self, msg, code=400):
        body = f'<div class="err" style="margin:16px">{msg}</div><p style="margin:0 16px"><a href="/">返回</a></p>'
        html = PAGE.replace("__TITLE__", "错误").replace("__NAV__", nav("")) \
                   .replace("__BODY__", body).replace("__SCRIPT__", "")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _args(self):
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q, keep_blank_values=True).items()}

    def _body(self):
        """按 Content-Type 统一返回「字典」：form 取 parse_qs 的标量值（v[0]）；
        JSON 原样返回（保留嵌套数组，供 /preview 的 params、/save 的 datasets 使用）。"""
        n = int(self.headers.get("Content-Length", 0))
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            qs = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
            return {k: v[0] for k, v in qs.items()}
        return json.loads(self.rfile.read(n) or b"{}")

    @staticmethod
    def _flat(body):
        """查询参数展平（仅 /q 使用）：标量原样返回，列表值取首元素（兼容 form 多值）。"""
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in body.items()}

    # ---- 管理页访问保护 ----
    def _check_admin(self):
        """config.json admin_password 非空 = HTTP Basic；为空 = 仅 127.0.0.1 可访问。"""
        pwd = str(load_config().get("admin_password") or "").strip()
        if pwd:
            expected = "Basic " + base64.b64encode(f"admin:{pwd}".encode()).decode()
            if self.headers.get("Authorization", "") == expected:
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="sqlreport admin"')
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page("需要认证", '<div class="err">需要管理员认证</div>'))
            return False
        remote = self.client_address[0]
        if remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        self._err("管理页仅允许本机（127.0.0.1）访问；如需远程管理请在服务端 config.json 配置 admin_password 启用 HTTP Basic", 403)
        return False

    @staticmethod
    def _json_res(obj):
        return json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8"

    # ---- GET ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        args = self._args()
        try:
            if path == "/" or path == "":
                return self._list_reports()
            if path == "/new":
                return self._editor(None)
            m = re.match(r"^/edit/(\w+)$", path)
            if m:
                return self._editor(m.group(1))
            if path == "/datasources" or path.startswith("/datasources/"):
                if not self._check_admin():
                    return
                if path == "/datasources":
                    return self._ds_list()
                if path == "/datasources/new":
                    return self._ds_form(None)
                m = re.match(r"^/datasources/edit/(.+)$", path)
                if m:
                    return self._ds_form(m.group(1))
                return self._err("404")
            m = re.match(r"^/r/(\w+)$", path)
            if m:
                return self._viewer(m.group(1), args)
            m = re.match(r"^/r/(\w+)/export$", path)
            if m:
                return self._export(m.group(1), args)
            return self._err("404")
        except Exception as e:
            return self._err(f"错误：{e}", 500)

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path.startswith("/q/"):
                return self._query(path[3:], self._flat(self._body()))
            if path == "/save":
                return self._save(self._body())
            if path == "/preview":
                return self._preview(self._body())
            if path == "/param-options":
                return self._param_options(self._body())
            if path == "/ds-fields":
                return self._ds_fields(self._body())
            if path == "/delete_report":
                return self._delete_report(self._body())
            if path.startswith("/datasources/"):
                if not self._check_admin():
                    return
                if path == "/datasources/save":
                    return self._ds_save(self._body())
                if path == "/datasources/test":
                    return self._ds_test(self._body())
                if path == "/datasources/toggle":
                    return self._ds_toggle(self._body())
                if path == "/datasources/delete":
                    return self._ds_delete(self._body())
                return self._err("404")
            return self._err("404")
        except Exception as e:
            self._send(json.dumps({"error": str(e)}), "application/json; charset=utf-8")

    # ---- 视图 ----
    def _list_reports(self):
        return views_report.render_list(self, DS_STORE, REPORTS_DIR)

    def _editor(self, rid):
        return views_report.render_editor(self, DS_STORE, REPORTS_DIR, rid)

    # ---- 数据源管理 ----
    def _ds_list(self):
        dss = DS_STORE.load()
        cards = ""
        n_en = n_ref = 0
        for name in sorted(dss):
            cfg = dss[name]
            enabled = DS_STORE.is_enabled(name, cfg)
            if enabled:
                n_en += 1
            refs = DS_STORE.referenced_by(name)
            if refs:
                n_ref += 1
            conn = cfg.get("host") or cfg.get("path") or ""
            if cfg.get("database"):
                conn += "/" + str(cfg["database"])
            t = cfg.get("type", "sqlite")
            ic = {"sqlite": "SQ", "mysql": "My", "sqlserver": "SS"}.get(t, "DB")
            ref_tags = " ".join(f'<span class="tag tag-cache">{esc_html(x)}</span>' for x in refs) or '<span style="color:var(--text-muted)">—</span>'
            nm = json.dumps(name, ensure_ascii=False)
            status = '<span class="tag tag-on">● 启用</span>' if enabled else '<span class="tag tag-off">○ 已禁用</span>'
            toggle_lbl = "禁用" if enabled else "启用"
            toggle_on = str(not enabled).lower()
            cards += (f'<div class="ds-card"><div class="top"><div class="typeic">{ic}</div>'
                      f'<div><div class="nm">{esc_html(name)}</div>{status}</div></div>'
                      f'<div class="meta"><span>类型：<b>{esc_html(t)}</b></span>'
                      f'<span>地址：<span class="mono">{esc_html(conn)}</span></span>'
                      f'<span>超时：{esc_html(cfg.get("timeout", 30))}s · 引用：{ref_tags}</span></div>'
                      f'<div class="ft"><button class="btn btn-secondary btn-sm" onclick="dsTest({nm},this)">测试</button>'
                      f'<a class="btn btn-secondary btn-sm" href="/datasources/edit/{urllib.parse.quote(name)}">编辑</a>'
                      f'<button class="btn btn-secondary btn-sm" onclick="dsToggle({nm},{toggle_on})">{toggle_lbl}</button>'
                      f'<div class="ops"><a class="op danger" onclick="dsDel({nm})">删除</a></div></div></div>')
        if not cards:
            cards = ('<div class="card"><div class="empty"><div class="il">🗄</div><h4>还没有数据源</h4>'
                     '<p>添加数据库连接后，即可在报表中使用。</p>'
                     '<a class="btn btn-primary" href="/datasources/new">＋ 新建数据源</a></div></div>')
        stat = (f'<div class="stat"><div class="k">启用中</div><div class="v">{n_en} <small>个</small></div></div>'
                f'<div class="stat"><div class="k">已禁用</div><div class="v">{len(dss) - n_en} <small>个</small></div></div>'
                f'<div class="stat"><div class="k">被报表引用</div><div class="v">{n_ref} <small>个</small></div></div>'
                f'<div class="stat"><div class="k">数据源总数</div><div class="v">{len(dss)} <small>个</small></div></div>')
        body = f"""<div class="pagehead"><div><h1>数据源管理</h1><div class="sub">集中维护数据库连接，改动免重启生效，密码不在页面展示</div></div>
        <a class="btn btn-primary" href="/datasources/new">＋ 新建数据源</a></div>
        <div class="stat-grid">{stat}</div>
        <div class="ds-grid">{cards}</div>
        <p style="font-size:12px;color:var(--text-muted);margin-top:12px">提示：被报表引用的数据源删除时会二次确认；编辑时密码留空 = 保持不变；`_` 前缀命名或禁用的数据源对报表不可见。</p>
        <div class="modal-mask hidden" id="mdl">
          <div class="modal"><div class="m-ic">⚠</div><h3 id="mdl-title"></h3><p id="mdl-body"></p>
          <div class="m-btns"><button class="btn btn-secondary" onclick="hideModal()">取消</button>
          <button class="btn btn-danger-ghost" id="mdl-ok">确认删除</button></div></div>
        </div>"""
        script = """
async function post(url, data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
function showModal(){document.getElementById('mdl').classList.remove('hidden');}
function hideModal(){document.getElementById('mdl').classList.add('hidden');}
async function dsTest(name, el){el.disabled=true;el.textContent='测试中…';
  const j = await (await post('/datasources/test',{name})).json();
  if(j.ok){toast('连接成功 '+j.ms+'ms','ok');}
  else{toast('连接失败：'+(j.error||'').slice(0,60),'err');}
  el.textContent='测试';el.disabled=false;}
async function dsToggle(name, en){await post('/datasources/toggle',{name,enabled:en});toast(en?'已启用':'已禁用','ok');location.reload();}
function dsDel(name){
  document.getElementById('mdl-title').textContent='删除数据源 '+name+' ？';
  document.getElementById('mdl-body').innerHTML='确定删除该数据源？删除后引用它的报表将无法查询。';
  document.getElementById('mdl-ok').onclick=async function(){
    hideModal();
    const j=await (await post('/datasources/delete',{name})).json();
    if(!j.error){toast('已删除','ok');location.reload();return;}
    if(j.referenced){
      document.getElementById('mdl-title').textContent='删除数据源 '+name+' ？';
      document.getElementById('mdl-body').innerHTML='该数据源被 <b>'+j.referenced.length+'</b> 张报表引用（'+j.referenced.join('、')+'），删除后这些报表将无法查询。';
      document.getElementById('mdl-ok').onclick=async function(){hideModal();await post('/datasources/delete',{name,force:true});toast('已删除','ok');location.reload();};
      showModal();
    } else { toast('删除失败：'+j.error,'err'); }
  };
  showModal();}
"""
        self._send(page("数据源管理", body, script, "ds"))

    def _ds_form(self, name):
        editing = name is not None
        cfg = (DS_STORE.get(name) or {}) if editing else {}
        types = ["sqlite", "mysql", "sqlserver"]
        topts = "".join(f'<option{" selected" if cfg.get("type") == t else ""}>{t}</option>' for t in types)
        en = cfg.get("enabled", True)
        title = ("编辑数据源：" + esc_html(name)) if editing else "新建数据源"
        body = f"""<div class="crumb">数据源管理 / <b>{title}</b></div>
        <div class="pagehead"><div><h1>{title}</h1><div class="sub">连接配置保存后立即生效，无需重启服务</div></div>
        <a class="btn btn-secondary" href="/datasources">返回列表</a></div>
        <div class="card" style="max-width:760px"><div style="padding:18px 20px">
        <form onsubmit="dssave(event)">
        <div class="parambar" style="align-items:flex-start">
          <div class="field" style="width:220px"><label>名称</label><input id="dname" value="{esc_html(name or '')}"{' disabled' if editing else ''} placeholder="如：sales_db"></div>
          <div class="field" style="width:160px"><label>类型</label><select id="dtype" onchange="updType()">{topts}</select></div>
          <div class="field"><label>启用</label><label class="switch{' on' if en else ''}"><input type="checkbox" id="denable" onchange="this.parentNode.classList.toggle('on',this.checked)"{' checked' if en else ''} style="display:none"></label></div>
        </div>
        <div id="f_file" class="ds-row" style="margin-top:14px">
          <div class="field" style="flex:1"><label>SQLite 文件路径</label><input id="dpath" value="{esc_html(cfg.get('path', ''))}" placeholder="demo.db（相对服务目录）或绝对路径"></div>
        </div>
        <div id="f_host" class="parambar" style="display:none;margin-top:14px">
          <div class="field" style="flex:2"><label>主机</label><input id="dhost" value="{esc_html(cfg.get('host', ''))}"></div>
          <div class="field" style="width:100px"><label>端口</label><input id="dport" value="{esc_html(cfg.get('port', ''))}"></div>
          <div class="field" style="flex:1"><label>用户</label><input id="duser" value="{esc_html(cfg.get('user', ''))}"></div>
          <div class="field" style="flex:1"><label>密码</label><input id="dpwd" type="password" value="" placeholder="{'留空 = 不修改' if editing else ''}"></div>
          <div class="field" style="flex:2"><label>数据库</label><input id="ddb" value="{esc_html(cfg.get('database', ''))}"></div>
        </div>
        <div class="parambar" style="margin-top:14px">
          <div class="field" style="width:120px"><label>超时(秒)</label><input id="dtimeout" value="{esc_html(cfg.get('timeout', 30))}"></div>
          <div class="field" style="flex:1"><label>备注</label><input id="dnote" value="{esc_html(cfg.get('note', ''))}"></div>
        </div>
        <div class="actbar">
          <button class="btn btn-primary">保存</button>
          <button type="button" class="btn btn-secondary" onclick="dstest()">测试连接</button>
        </div>
        </form>
        </div></div>"""
        script = """
function updType(){const t=document.getElementById('dtype').value;
  document.getElementById('f_file').style.display = t==='sqlite' ? '' : 'none';
  document.getElementById('f_host').style.display = t==='sqlite' ? 'none' : '';}
function collect(){const t=document.getElementById('dtype').value;
  const c={name:document.getElementById('dname').value.trim(),type:t,
    enabled:document.getElementById('denable').checked,
    timeout:parseInt(document.getElementById('dtimeout').value)||30,
    note:document.getElementById('dnote').value};
  if(t==='sqlite'){c.path=document.getElementById('dpath').value.trim();}
  else{c.host=document.getElementById('dhost').value.trim();
    c.port=document.getElementById('dport').value.trim();
    c.user=document.getElementById('duser').value;
    c.password=document.getElementById('dpwd').value;
    c.database=document.getElementById('ddb').value;}
  return c;}
async function dssave(e){e.preventDefault();
  const res = await fetch('/datasources/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});
  const j = await res.json();
  if(j.error){toast('保存失败：'+j.error,'err');return;}
  toast('已保存','ok');location.href='/datasources';}
async function dstest(){
  const res = await fetch('/datasources/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});
  const j = await res.json();
  if(j.ok){toast('连接成功 '+j.ms+'ms','ok');}
  else{toast('连接失败：'+(j.error||'').slice(0,80),'err');}}
updType();
"""
        self._send(page("数据源表单", body, script, "ds"))

    def _ds_save(self, data):
        try:
            name = (data.get("name") or "").strip()
            if not name:
                raise ValueError("数据源名称不能为空")
            t = data.get("type", "sqlite")
            if t not in ("sqlite", "mysql", "sqlserver"):
                raise ValueError(f"不支持的数据源类型: {t}")
            old = DS_STORE.get(name) or {}
            cfg = {"type": t}
            if t == "sqlite":
                cfg["path"] = (data.get("path") or "").strip()
                if not cfg["path"]:
                    raise ValueError("SQLite 数据源需要文件路径")
            else:
                cfg["host"] = (data.get("host") or "").strip()
                if not cfg["host"]:
                    raise ValueError("主机地址不能为空")
                cfg["port"] = int(data.get("port") or (3306 if t == "mysql" else 1433))
                cfg["user"] = data.get("user", "")
                pwd = data.get("password") or ""
                cfg["password"] = pwd if pwd else old.get("password", "")  # 留空 = 不修改
                cfg["database"] = data.get("database", "")
            cfg["timeout"] = int(data.get("timeout") or 30)
            cfg["enabled"] = bool(data.get("enabled", True))
            cfg["note"] = data.get("note", "")
            DS_STORE.save(name, cfg)
        except Exception as e:
            return self._send(*self._json_res({"error": str(e)}))
        self._send(*self._json_res({"ok": True, "name": name}))

    def _ds_test(self, data):
        """连接测试：仅建连不执行 SQL。列表页传 {name}（测已保存配置）；表单传完整字段（测未保存值）。"""
        try:
            if data.get("type"):
                name = (data.get("name") or "").strip()
                cfg = dict(data)
                old = DS_STORE.get(name) or {} if name else {}
                if not cfg.get("password") and old.get("password"):
                    cfg["password"] = old["password"]  # 密码留空沿用已存密码
                cfg["timeout"] = int(cfg.get("timeout") or 30)
            else:
                name = (data.get("name") or "").strip()
                cfg = DS_STORE.get(name)
                if cfg is None:
                    raise ValueError(f"数据源不存在: {name}")
            ok, ms, err = DS_STORE.test_connection(cfg)
        except Exception as e:
            ok, ms, err = False, 0, str(e)
        self._send(*self._json_res({"ok": ok, "ms": ms, "error": err}))

    def _ds_toggle(self, data):
        try:
            real = DS_STORE.toggle((data.get("name") or "").strip(), bool(data.get("enabled")))
            self._send(*self._json_res({"ok": True, "name": real}))
        except Exception as e:
            self._send(*self._json_res({"error": str(e)}))

    def _ds_delete(self, data):
        name = (data.get("name") or "").strip()
        try:
            if not name:
                raise ValueError("缺少数据源名称")
            refs = DS_STORE.referenced_by(name)
            if refs and not data.get("force"):
                return self._send(*self._json_res(
                    {"error": f"数据源被 {len(refs)} 张报表引用，删除后这些报表将无法查询", "referenced": refs}))
            DS_STORE.delete(name)
        except Exception as e:
            return self._send(*self._json_res({"error": str(e)}))
        self._send(*self._json_res({"ok": True}))


    def _viewer(self, rid, args):
        return views_report.render_viewer(self, DS_STORE, REPORTS_DIR, rid, args)

    # ---- 接口 ----
    def _save(self, data):
        rid = (data.get("id") or "").strip().lower()
        os.makedirs(REPORTS_DIR, exist_ok=True)
        if rid:
            # 编辑/显式指定 id：仅允许 Unicode 词字符与连字符，防路径穿越
            if not re.fullmatch(r"[\w-]+", rid):
                raise ValueError("非法的报表 ID")
        else:
            # 新建报表：随机不可猜测 id（URL 即唯一凭证，语义化名称派生的 id 可被枚举猜测）
            while True:
                rid = secrets.token_hex(8)
                if not os.path.exists(os.path.join(REPORTS_DIR, rid + ".json")):
                    break
        rec = {"name": data["name"], "params": data.get("params", []),
               "cache_ttl": int(data.get("cache_ttl") or 0),  # 0 = 实时（不缓存）
               "max_rows": int(data.get("max_rows") or 0)}     # 0 = 缺省 2000（保留用户配置）
        # 参数来源归一化：旧 mode:'sql'（自定义SQL取值）→ mode:'field' + field 空（field=过滤绑定，SQL=候选值）
        for p in rec["params"]:
            s = p.get("source")
            if isinstance(s, dict) and s.get("mode") == "sql":
                s["mode"] = "field"
                s.setdefault("field", "")
        if data.get("export_format") in ("xls", "csv"):
            rec["export_format"] = data["export_format"]  # 默认导出格式
        if data.get("query_mode") in ("auto", "manual"):
            rec["query_mode"] = data["query_mode"]  # auto=打开自动查询；manual=手动查询
        rec["page_size"] = max(int(data.get("page_size") or 20), 1)  # 结果每页行数
        # 分析键白名单（决策 D10：白名单外的键会被静默丢弃，故每键落地时同步加入并做最小结构校验）
        if isinstance(data.get("total"), dict):
            rec["total"] = {"label": str(data["total"].get("label", "合计")),
                            "label_col": int(data["total"].get("label_col", 0) or 0)}
        if isinstance(data.get("summary"), list):
            rec["summary"] = [m for m in data["summary"]
                              if isinstance(m, dict) and m.get("col")]
        for k in ("top_n", "share", "bucket"):
            if isinstance(data.get(k), dict) and data[k].get("col"):
                rec[k] = data[k]
        if isinstance(data.get("blocks"), list):
            rec["blocks"] = normalize_blocks(data)  # 经 normalize_blocks 校验（类型白名单 + pivot 必填键）
        # 双格式兼容：归一化后仅 1 个数据集 → 写回旧 {ds, sql} 格式（旧文件 diff 稳定）；≥2 个才写 datasets
        datasets = normalize_report(data, DS_STORE.visible_names())
        if len(datasets) == 1:
            rec["ds"], rec["sql"] = datasets[0]["ds"], datasets[0]["sql"]
        else:
            rec["datasets"] = datasets
            rec["merge"] = data.get("merge") or {"mode": "union"}
        # 保存前试编译：验证数据源存在、占位符可解析（用默认值空跑，不执行 SQL）
        values = build_values("", rec["params"], {p["id"]: p.get("default", "") for p in rec["params"]})
        for d in datasets:
            substitute(d["sql"], values)
        path = os.path.join(REPORTS_DIR, rid + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子写，防半写损坏
        CACHE.invalidate(rid)  # 报表定义已变更，清空该报表全部缓存条目
        self._send(json.dumps({"id": rid}), "application/json; charset=utf-8")

    def _load_report(self, rid):
        """读报表 JSON，不存在返回 None。"""
        path = os.path.join(REPORTS_DIR, rid + ".json")
        if not os.path.exists(path):
            return None
        return load_json(path)

    def _param_options(self, data):
        """查询参数取值接口（含级联）：对每个带 source 的 select 参数，
        用当前已填参数值 values 替换占位符后执行取数。
        - source.mode=field：从数据集 SQL 包一层 SELECT DISTINCT 字段
        - source.mode=sql：直接执行自定义 SQL
        返回 {options: {param_id: [值,...]}}；单个参数失败时忽略（前端降级文本输入）。"""
        rid = (data.get("rid") or "").strip()
        values = data.get("values") or {}
        r = self._load_report(rid)
        if r is None:
            return self._send(*self._json_res({"options": {}}))
        try:
            datasets = normalize_report(r, DS_STORE.visible_names())
        except Exception:
            return self._send(*self._json_res({"options": {}}))
        ds_by_name = {d["name"]: d for d in datasets}
        params = r.get("params", [])
        filled = build_values("", params, values)  # 级联：用已填参数替换
        out = {}
        for p in params:
            src = p.get("source")
            if not src or p.get("type") not in ("select", "text"):
                continue
            d = ds_by_name.get(src.get("ds") or "")
            if d is None:
                continue
            try:
                # 排除当前参数自身的值：避免字典取值被自身条件过滤（顶层下拉应显示全量；级联仍用其它参数过滤）
                local = {k: v for k, v in filled.items() if k != p["id"]}
                if src.get("mode") == "field":
                    cand = (src.get("sql") or "").strip()
                    if cand:  # 候选SQL优先：仅用于下拉/文本候选值，不参与过滤
                        sql = substitute(cand, local)
                        if not sql.strip():
                            continue
                    else:
                        f = (src.get("field") or "").strip()
                        if not re.fullmatch(r"\w+", f):  # 字段名需为合法标识符
                            continue
                        base = substitute(d["sql"], local)
                        sql = (f"SELECT DISTINCT {f} AS v FROM ({base}) t "
                               f"WHERE {f} IS NOT NULL AND {f} <> ''")
                else:
                    sql = substitute(src.get("sql", ""), local)
                    if not sql.strip():
                        continue
                cols, rows, tr, ct = run_query(d["ds"], sql)
                out[p["id"]] = [str(row[0]) for row in rows if row and row[0] != ""]
            except Exception:
                continue  # 单参数失败降级，不影响其它参数
        return self._send(*self._json_res({"options": out}))

    def _ds_fields(self, data):
        """字段枚举：SELECT * FROM (SQL) t WHERE 1=0 只取列名不取数据。
        支持两种调用：传 {ds_src, sql}（编辑器直接执行）；或 {rid, ds}（从报表定义取数据集）。"""
        ds_src = (data.get("ds_src") or "").strip()
        sql = data.get("sql") or ""
        if not ds_src:
            rid = (data.get("rid") or "").strip()
            ds_name = (data.get("ds") or "").strip()
            r = self._load_report(rid)
            if r is None:
                return self._send(*self._json_res({"fields": []}))
            try:
                datasets = normalize_report(r, DS_STORE.visible_names())
            except Exception:
                return self._send(*self._json_res({"fields": []}))
            d = next((x for x in datasets if x["name"] == ds_name), None)
            if d is None:
                return self._send(*self._json_res({"fields": []}))
            ds_src = d["ds"]
            sql = d["sql"]
        try:
            filled = build_values("", data.get("params") or [], data.get("values") or {})
            sql = substitute(sql, filled)
            cols, rows, tr, ct = run_query(ds_src, f"SELECT * FROM ({sql}) t WHERE 1=0")
        except Exception as e:
            return self._send(*self._json_res({"fields": [], "error": str(e)}))
        return self._send(*self._json_res({"fields": cols}))

    def _delete_report(self, data):
        """删除报表：删文件 + 清该报表缓存。权限与 _save 一致（无独立鉴权）。"""
        rid = (data.get("id") or "").strip()
        if not re.fullmatch(r"\w+", rid):
            return self._send(*self._json_res({"error": "报表 ID 不合法"}))
        path = os.path.join(REPORTS_DIR, rid + ".json")
        if not os.path.exists(path):
            return self._send(*self._json_res({"error": "报表不存在"}))
        try:
            os.remove(path)
            CACHE.invalidate(rid)
        except Exception as e:
            return self._send(*self._json_res({"error": str(e)}))
        return self._send(*self._json_res({"ok": True}))

    def _merge_results(self, r, datasets, results):
        """按 merge 配置合并各数据集结果；单数据集直接透传。返回 (cols, rows, coltypes)。"""
        if len(results) == 1:
            return next(iter(results.values()))
        merge = r.get("merge") or {}
        mode = merge.get("mode", "union")
        byname = {d["name"]: results[d["name"]] for d in datasets}
        if mode == "lookup":
            base = merge.get("base") or datasets[0]["name"]
            with_ds = merge.get("with") or next((d["name"] for d in datasets if d["name"] != base), None)
            if base not in byname or not with_ds or with_ds not in byname:
                raise ValueError(f"lookup 配置的 base/with 数据集不存在: {base} / {with_ds}")
            bcols, brows, btypes = byname[base]
            wcols, wrows, wtypes = byname[with_ds]
            return merge_lookup(bcols, brows, wcols, wrows, merge.get("on") or [], merge.get("cols"),
                                btypes, wtypes)
        if mode != "union":
            raise ValueError(f"不支持的合并方式: {mode}")
        return merge_union([byname[d["name"]] for d in datasets])

    def _auto_filters(self, r, datasets, given):
        """字段绑定自动过滤：对每个绑定数据集字段的参数生成 WHERE 条件（无需手写占位符）。
        返回 {数据集名: [条件,...]}；数字参数非数字、字段名非法时抛 ValueError（中文提示）。
        - text/select/date 精确：f = 'v'
        - 范围类（daterange/numrange、勾选范围的 date/number）：f >= 'v1' AND f <= 'v2'
          （date/number 双模式由提交的 id_2 区分：无 id_2 视为精确）
        - source.sql 为候选值 SQL，不参与过滤；未填参数不生成条件。
        """
        ds_by_name = {d["name"]: d for d in datasets}
        single = len(datasets) == 1
        conds = {}
        for p in r.get("params", []):
            src = p.get("source")
            if not isinstance(src, dict) or src.get("mode") != "field":
                continue
            f = (src.get("field") or "").strip()
            if not f:
                continue  # 未绑定字段 = 不过滤（可仅作候选值来源）
            pid = p["id"]
            if not re.fullmatch(r"\w+", f):
                raise ValueError(f"参数「{p.get('label') or pid}」绑定的字段名不合法: {f}")
            d = ds_by_name.get(src.get("ds") or "")
            if d is None:
                if not single:
                    continue  # 绑定的数据集不存在（多数据集时）→ 忽略
                d = datasets[0]
            target = d["name"]
            t = p.get("type", "text")
            g = given.get(pid, "")
            g = g.strip() if isinstance(g, str) else str(g or "")
            g2 = given.get(pid + "_2", "")
            g2 = g2.strip() if isinstance(g2, str) else ""

            def add(cond):
                conds.setdefault(target, []).append(cond)

            if t in ("daterange", "numrange"):
                if g:
                    add(f"{f} >= '{esc(g)}'")
                if g2:
                    add(f"{f} <= '{esc(g2)}'")
            elif t in ("date", "number") and p.get("range"):
                if g2:  # 范围模式（查看页精确/范围切换靠禁用隐藏输入实现，有 id_2 即范围）
                    if t == "number":
                        for v in (g, g2):
                            if v:
                                try:
                                    float(v)
                                except ValueError:
                                    raise ValueError(f"参数「{p.get('label') or pid}」需要数字，收到: {v}")
                    if g:
                        add(f"{f} >= '{esc(g)}'")
                    add(f"{f} <= '{esc(g2)}'")
                elif g != "":
                    if t == "number":
                        try:
                            float(g)
                        except ValueError:
                            raise ValueError(f"参数「{p.get('label') or pid}」需要数字，收到: {g}")
                    add(f"{f} = '{esc(g)}'")
            elif t == "number":
                if g != "":
                    try:
                        float(g)
                    except ValueError:
                        raise ValueError(f"参数「{p.get('label') or pid}」需要数字，收到: {g}")
                    add(f"{f} = '{esc(g)}'")
            elif g != "":
                add(f"{f} = '{esc(g)}'")
        return conds

    def _execute_report(self, rid, r, given):
        """报表执行统一链路（/q 与 /export 共用）：
        归一化 → 占位符替换 + 字段绑定自动过滤（包一层 SELECT * FROM (SQL) __flt WHERE）→
        缓存查询（cache_ttl>0 时）→ 各数据集独立查询（各自限流）→ 合并
        → 报表 columns 类型覆盖 → max_rows 截断 → 回填缓存。
        返回 {columns, rows, coltypes, truncated, cached, elapsed_ms}。
        """
        t0 = time.time()
        datasets = normalize_report(r, DS_STORE.visible_names())
        values = build_values("", r.get("params", []), given)
        flt = self._auto_filters(r, datasets, given)
        blocks_cfg = normalize_blocks(r)
        # 缓存豁免（决策 D2 / Task 11 Step 3.5）：含非 table 块（pivot/hist）的报表不使用结果缓存——
        # 缓存里只有主表行，命中路径拿不到 pivot/hist 所需的各 dataset 原始结果，故将 cache_ttl 视为 0（不读不写）。
        if any(b.get("type") != "table" for b in blocks_cfg):
            ttl = 0
        else:
            ttl = int(r.get("cache_ttl") or 0)  # 0 = 不缓存（实时）
        key = CACHE.make_key(rid, values) if ttl > 0 else None
        cached = False
        if key:
            hit = CACHE.get(key)
            if hit:
                cols, rows, truncated, coltypes = hit
                cached = True
        if not cached:
            results, fetch_truncated = {}, False
            for d in datasets:
                sql = substitute(d["sql"], values)
                conds = flt.get(d["name"])
                if conds:
                    sql = f"SELECT * FROM ({sql}) __flt WHERE " + " AND ".join(conds)
                cols, rows, tr, coltypes = run_query(d["ds"], sql)
                results[d["name"]] = (cols, rows, coltypes)
                fetch_truncated = fetch_truncated or tr
            cols, rows, coltypes = self._merge_results(r, datasets, results)
            ovr = {c.get("name"): c.get("type") for c in (r.get("columns") or [])
                   if c.get("type") in ("num", "date", "str")}
            if ovr:
                coltypes = [ovr.get(c, t) for c, t in zip(cols, coltypes)]
            max_rows = int(r.get("max_rows") or 2000)
            truncated = fetch_truncated or len(rows) > max_rows
            if len(rows) > max_rows:
                rows = rows[:max_rows]
            if key:
                CACHE.put(key, cols, rows, ttl, truncated, coltypes)
        elapsed_ms = int((time.time() - t0) * 1000)
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
        # ---- blocks 构建（决策 D3/D4）：table 块 = 主结果 + M1 分析管道；pivot 块 = 指定 dataset 原始结果 ----
        blocks = []
        for b in blocks_cfg:
            if b.get("type") == "table":
                blocks.append({"type": "table", "title": b.get("title", ""),
                               "columns": cols, "rows": rows, "coltypes": coltypes})
                continue
            ds_name = b.get("dataset")
            if ds_name not in results:
                raise ValueError(f"pivot 块引用的数据集不存在: {ds_name}")
            bcols, brows, btypes = results[ds_name]
            agg = str(b.get("agg") or "sum")
            max_cols = int(b.get("max_cols") or 50)
            col_total = bool(b.get("col_total", True))
            if b.get("col"):
                pcols, prows, ptypes = pivot(
                    bcols, brows, btypes, str(b["row"]), str(b["col"]), str(b["value"]),
                    agg=agg, row_total=bool(b.get("row_total", True)),
                    col_total=col_total, max_cols=max_cols)
            else:
                # 单维汇总锁定形态：col 缺省时注入常量维度列「合计」（每行恒为「合计」）调用 pivot，
                # row_total 强制 False → 列头 [row, "合计"]，不引入 __all__ 之类合成列名
                bcols2 = list(bcols) + ["合计"]
                btypes2 = list(btypes) + ["str"]
                brows2 = [list(r) + ["合计"] for r in brows]
                pcols, prows, ptypes = pivot(
                    bcols2, brows2, btypes2, str(b["row"]), "合计", str(b["value"]),
                    agg=agg, row_total=False, col_total=col_total, max_cols=max_cols)
            blocks.append({"type": "pivot", "title": b.get("title", ""),
                           "columns": pcols, "rows": prows, "coltypes": ptypes})
        return {"columns": cols, "rows": rows, "coltypes": coltypes, "truncated": truncated,
                "cached": cached, "elapsed_ms": elapsed_ms, "total_row": total,
                "summary": summary, "blocks": blocks}

    def _query(self, rid, given):
        r = self._load_report(rid)
        if r is None:
            return self._send(json.dumps({"error": "报表不存在"}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        try:
            result = self._execute_report(rid, r, given)
        except Exception as e:
            return self._send(json.dumps({"error": str(e)}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        self._send(json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")

    def _preview(self, data):
        """编辑器单数据集试运行：用参数默认值替换后执行，返回前 200 行（复用 run_query 限流）。"""
        try:
            ds = (data.get("ds") or "").strip()
            if ds not in DS_STORE.visible_names():
                raise ValueError(f"数据源不存在或已禁用: {ds or '(空)'}")
            params = data.get("params", [])
            values = build_values("", params, {p.get("id", ""): p.get("default", "") for p in params})
            sql = substitute(data.get("sql", ""), values)
            cols, rows, tr, coltypes = run_query(ds, sql)
        except Exception as e:
            return self._send(*self._json_res({"error": str(e)}))
        result = {"columns": cols, "rows": rows[:200], "coltypes": coltypes,
                  "truncated": tr or len(rows) > 200}
        self._send(*self._json_res(result))

    def _export(self, rid, args):
        r = self._load_report(rid)
        if r is None:
            return self._err("报表不存在")
        try:
            result = self._execute_report(rid, r, args)
        except Exception as e:
            return self._err(f"错误：{e}", 500)
        cols, rows, coltypes = result["columns"], result["rows"], result["coltypes"]
        total = result.get("total_row")
        summary = result.get("summary") or []
        fmt = args.get("format") or r.get("export_format") or "xls"
        if fmt == "csv":
            import csv, io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for row in rows:
                w.writerow(row)
            if total:
                w.writerow(total)  # 与页面所见一致：追加合计行
            fn = urllib.parse.quote(f"{r['name']}.csv")
            self._send(buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                       f'attachment; filename*=UTF-8\'\'{fn}')
            return

        def cell(v, i, cts):
            """数值列输出真数值（Excel 右对齐/可计算），其余维持 HTML 转义。"""
            if cts[i] == "num":
                try:
                    return f'<td style="mso-number-format:0.00;">{float(v)}</td>'
                except (TypeError, ValueError):
                    pass
            return f"<td>{esc_html(v)}</td>"

        def xls_table(bcols, brows, bct, total_row=None):
            h = "".join(f"<th>{c}</th>" for c in bcols)
            b = "".join("<tr>" + "".join(cell(v, i, bct) for i, v in enumerate(row)) + "</tr>" for row in brows)
            if total_row:
                b += "<tr>" + "".join(cell(v, i, bct) for i, v in enumerate(total_row)) + "</tr>"
            return f'<table border="1">{h}{b}</table>'

        s = ""
        if summary:
            s = "<p>" + "; ".join(f"{esc_html(m.get('label', ''))}: {m.get('value', '')}" for m in summary) + "</p>"
        # xls 按 blocks 顺序输出：每块 <h3>标题</h3> + 表格段；table 块附主表合计行（csv 仍只导主表）
        blocks = result.get("blocks") or [{"type": "table", "title": "",
                                           "columns": cols, "rows": rows, "coltypes": coltypes}]
        sections = []
        for b in blocks:
            sec = ""
            if b.get("title"):
                sec += f"<h3>{esc_html(b['title'])}</h3>"
            bct = b.get("coltypes") or coltypes
            bcols = b.get("columns") or cols
            brows = b.get("rows") or rows
            if b.get("type") == "table":
                sec += xls_table(bcols, brows, bct, total)
            else:
                sec += xls_table(bcols, brows, bct)
            sections.append(sec)
        xls = f'<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body>{s}{"".join(sections)}</body></html>'
        fn = urllib.parse.quote(f"{r['name']}.xls")
        self._send(xls.encode("utf-8"), "application/vnd.ms-excel", f'attachment; filename*=UTF-8\'\'{fn}')

def main():
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8765))
    os.chdir(BASE)
    print(f"SQL报表服务 http://0.0.0.0:{port}  (报表目录: {REPORTS_DIR})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
