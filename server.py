#!/usr/bin/env python3
"""轻量 SQL 报表工具：贴 SQL + 参数条件 + 独立 URL + Excel 导出。
Python 标准库实现；MySQL/SQLServer 驱动按需懒加载（pymysql / pyodbc）。
运行: python3 server.py [端口]  默认 8765
"""
import base64, json, os, re, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from db import DS_STORE, load_json, run_query, merge_union, merge_lookup
from params import build_values, substitute, normalize_report

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE, "reports")
CONFIG_FILE = os.path.join(BASE, "config.json")

def load_config():
    """全局配置（config.json，不入库）。缺省：auth=off，admin_password 空 → 管理页仅本机可访问。"""
    try:
        return load_json(CONFIG_FILE)
    except Exception:
        return {"auth": "off", "admin_password": ""}

# ---------------- 页面模板 ----------------

PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - SQL报表</title>
<style>
body{{font-family:-apple-system,'Microsoft YaHei',sans-serif;margin:0;background:#f6f6f4;color:#222}}
.wrap{{max-width:1200px;margin:0 auto;padding:16px}}
h1{{font-size:18px}} h2{{font-size:15px}}
a{{color:#185fa5;text-decoration:none}}
table{{border-collapse:collapse;background:#fff;width:100%;font-size:13px}}
th,td{{border:1px solid #ddd;padding:6px 10px;text-align:left;white-space:nowrap}}
th{{background:#eef2f6}}
.bar{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}
label{{font-size:13px;margin-right:4px}}
input,select,textarea{{font-size:13px;padding:5px 8px;border:1px solid #ccc;border-radius:4px}}
textarea{{width:100%;box-sizing:border-box;font-family:Menlo,Consolas,monospace}}
button{{padding:6px 16px;background:#185fa5;color:#fff;border:0;border-radius:4px;cursor:pointer}}
.fields div{{margin:6px 0}}
.err{{color:#a32d2d;background:#fcebeb;padding:8px 12px;border-radius:4px}}
#status{{font-size:12px;color:#888}}
</style></head><body><div class="wrap">{body}</div>
<script>{script}</script></body></html>"""

def page(title, body, script=""):
    return PAGE.format(title=title, body=body, script=script).encode()

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
        body = f'<div class="err">{msg}</div><p><a href="/">返回</a></p>'
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE.format(title="错误", body=body, script="").encode())

    def _args(self):
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q, keep_blank_values=True).items()}

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True) \
            if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded") \
            else json.loads(self.rfile.read(n) or b"{}")

    @staticmethod
    def _flat(qs_body):
        return {k: v[0] for k, v in qs_body.items()}

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
        rows = ""
        for fn in sorted(os.listdir(REPORTS_DIR)):
            if not fn.endswith(".json"):
                continue
            try:
                r = load_json(os.path.join(REPORTS_DIR, fn))
                rid = fn[:-5]
                ds_txt = r.get("ds") or f"多数据集×{len(r.get('datasets', []))}"
                rows += f'<tr><td>{r.get("name", rid)}</td><td>{rid}</td><td>{ds_txt}</td><td><a href="/r/{rid}">打开</a> <a href="/edit/{rid}">编辑</a></td></tr>'
            except Exception:
                pass
        body = f"""<h1>SQL 报表</h1>
        <div class="bar"><a href="/new">＋新建报表</a></div>
        <table><tr><th>名称</th><th>ID(独立URL /r/ID)</th><th>数据源</th><th>操作</th></tr>{rows}</table>"""
        self._send(page("报表列表", body))

    def _editor(self, rid):
        r = {"name": "", "ds": "", "sql": "", "params": []}
        if rid:
            r = load_json(os.path.join(REPORTS_DIR, rid + ".json"))
        dss = DS_STORE.visible_names()
        # 双格式兼容：旧 {ds, sql} 视为单个数据集 main 编辑，保存时自动写回旧格式
        datasets = r.get("datasets") or [{"name": "main", "ds": r.get("ds", ""), "sql": r.get("sql", "")}]
        merge = r.get("merge") or {"mode": "union"}
        body = f"""<h1>报表编辑器</h1>
<form onsubmit="save(event)">
<div class="fields">
<div><label>报表名称</label><input id="rname" size="30" value="{r['name']}">
<label>缓存秒数</label><input id="rcache" size="5" value="{r.get('cache_ttl', 0)}" title="0=实时，每次直查数据库">
<span style="font-size:12px;color:#888">0=实时</span></div>
</div>
<h2>数据集（≥2 个可配置合并方式；参数全局共享）</h2>
<div id="dlist"></div>
<div class="bar"><button type="button" onclick="addDs()">＋加数据集</button></div>
<div id="mrow" style="display:none"><b>合并方式：</b>
<select id="mmode" onchange="updMerge()"><option value="union">纵向合并 union</option><option value="lookup">横向关联 lookup</option></select>
<span id="lookupcfg" style="display:none">
主数据集 <select id="mbase"></select> 关联 <select id="mwith"></select>
关联键 <input id="mon" size="15" placeholder="列名,可多列"> 取值列 <input id="mcols" size="15" placeholder="留空=除键外全部">
</span></div>
<h2>查询参数（所有数据集共享；留空则无条件）</h2>
<div id="plist"></div>
<div class="bar"><button type="button" onclick="addp()">＋加参数</button><button>保存并生成URL</button><span id="status"></span></div>
</form>"""
        script = """
let params = %(params)s;
let datasets = %(datasets)s;
const DSS = %(dss)s;
const MERGE = %(merge)s;
const TYPES = {text:'文本', select:'下拉', date:'日期', daterange:'日期范围', number:'数字', numrange:'数字范围'};
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}
function dsOpts(sel){return DSS.map(k=>`<option value="${esc(k)}"${k===sel?' selected':''}>${esc(k)}</option>`).join('')}
function addDs(d){d = d || {name:'', ds:DSS[0]||'', sql:''};
  const div = document.createElement('div');
  div.style.cssText = 'border:1px solid #ddd;border-radius:4px;padding:8px;margin:8px 0;background:#fff';
  div.innerHTML = `<div><input class="dname" placeholder="数据集名(唯一)" value="${esc(d.name)}" size="12">
    <label>数据源</label><select class="dds">${dsOpts(d.ds)}</select>
    <button type="button" onclick="pv(this)">试运行</button>
    <button type="button" onclick="rmDs(this)">删数据集</button></div>
    <textarea class="dsql" rows="5" placeholder="SELECT ... WHERE dt BETWEEN '{{dt.begin}}' AND '{{dt.end}}'">${esc(d.sql)}</textarea>
    <div class="pv" style="font-size:12px"></div>`;
  document.getElementById('dlist').appendChild(div);updMerge();}
function rmDs(btn){btn.closest('#dlist > div').remove();updMerge();}
function collectDs(){return [...document.querySelectorAll('#dlist > div')].map(d=>({
  name:d.querySelector('.dname').value.trim()||'main',
  ds:d.querySelector('.dds').value,
  sql:d.querySelector('.dsql').value}));}
function fillSel(id, names, val){document.getElementById(id).innerHTML =
  names.map(n=>`<option${n===val?' selected':''}>${esc(n)}</option>`).join('');}
function updMerge(){const ds=collectDs();const two=ds.length>=2;
  document.getElementById('mrow').style.display = two?'':'none';
  if(!two)return;
  document.getElementById('lookupcfg').style.display = document.getElementById('mmode').value==='lookup'?'':'none';
  fillSel('mbase', ds.map(d=>d.name), MERGE.base||ds[0].name);
  fillSel('mwith', ds.map(d=>d.name), MERGE.with||ds[1].name);}
function collectMerge(){const ds=collectDs();if(ds.length<2)return null;
  const m={mode:document.getElementById('mmode').value};
  if(m.mode==='lookup'){
    m.base=document.getElementById('mbase').value;
    m.with=document.getElementById('mwith').value;
    m.on=document.getElementById('mon').value.split(',').map(s=>s.trim()).filter(Boolean);
    const cols=document.getElementById('mcols').value.split(',').map(s=>s.trim()).filter(Boolean);
    if(cols.length)m.cols=cols;}
  return m;}
async function pv(btn){const block=btn.closest('#dlist > div');const out=block.querySelector('.pv');
  out.textContent='试运行中…';
  const res = await fetch('/preview',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ds:block.querySelector('.dds').value,sql:block.querySelector('.dsql').value,params:collect()})});
  const j = await res.json();
  if(j.error){out.innerHTML='<span style="color:#a32d2d">失败:'+esc(j.error)+'</span>';return;}
  let h = '试运行前 '+j.rows.length+' 行'+(j.truncated?'（已截断）':'')+'：<table><tr>'+j.columns.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>';
  h += j.rows.map(r=>'<tr>'+r.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</table>';
  out.innerHTML = h;}
function addp(p){p = p || {id:'',label:'',type:'text',options:'',default:''};
  const d = document.createElement('div');
  d.innerHTML = `<input placeholder="参数id" value="${esc(p.id)}" oninput="upd(this,0,'id')">
    <input placeholder="显示名" value="${esc(p.label)}" oninput="upd(this,0,'label')" size="12">
    <select onchange="upd(this,0,'type')">${Object.entries(TYPES).map(([k,v])=>`<option value="${k}"${p.type===k?' selected':''}>${v}</option>`).join('')}</select>
    <input placeholder="下拉选项(逗号分隔)" value="${esc(p.options||'')}" oninput="upd(this,0,'options')" size="20">
    <input placeholder="默认值" value="${esc(p.default||'')}" oninput="upd(this,0,'default')" size="10">
    <button type="button" onclick="this.parentNode.remove()">删</button>`;
  document.getElementById('plist').appendChild(d);}
function upd(el,idx,key){params[idx]=params[idx]||{};params[idx][key]=el.value;}
function collect(){const ds=[...document.querySelectorAll('#plist > div')];return ds.map((d,i)=>({id:d.querySelector('input').value,label:d.querySelectorAll('input')[1].value,type:d.querySelector('select').value,options:d.querySelectorAll('input')[2].value,default:d.querySelectorAll('input')[3].value})).filter(p=>p.id);}
async function save(e){e.preventDefault();
  const res = await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:%(rid)s,name:document.getElementById('rname').value,
      cache_ttl:parseInt(document.getElementById('rcache').value)||0,
      params:collect(),datasets:collectDs(),merge:collectMerge()})});
  const j = await res.json();
  document.getElementById('status').textContent = j.error ? ('失败:'+j.error) : ('已保存，独立URL: /r/'+j.id);}
document.getElementById('mmode').value = MERGE.mode || 'union';
document.getElementById('mon').value = (MERGE.on||[]).join(',');
document.getElementById('mcols').value = (MERGE.cols||[]).join(',');
datasets.forEach(d=>addDs(d));
params.forEach((p,i)=>addp(p));
""" % {"params": json.dumps(r["params"], ensure_ascii=False),
       "datasets": json.dumps(datasets, ensure_ascii=False),
       "dss": json.dumps(dss),
       "merge": json.dumps(merge, ensure_ascii=False),
       "rid": json.dumps(rid) if rid else "null"}
        self._send(page("编辑报表", body, script))

    # ---- 数据源管理 ----
    def _ds_list(self):
        dss = DS_STORE.load()
        rows = ""
        for name in sorted(dss):
            cfg = dss[name]
            enabled = DS_STORE.is_enabled(name, cfg)
            refs = DS_STORE.referenced_by(name)
            conn = cfg.get("host") or cfg.get("path") or ""
            if cfg.get("database"):
                conn += "/" + str(cfg["database"])
            nm = json.dumps(name, ensure_ascii=False)  # 防名称含引号破坏 JS
            ops = (f'<a href="#" onclick="dsTest({nm},this)">测试</a> '
                   f'<a href="/datasources/edit/{urllib.parse.quote(name)}">编辑</a> '
                   f'<a href="#" onclick="dsToggle({nm},{str(not enabled).lower()})">{"禁用" if enabled else "启用"}</a> '
                   f'<a href="#" onclick="dsDel({nm})">删除</a>')
            rows += (f'<tr><td>{name}</td><td>{cfg.get("type", "")}</td><td>{conn}</td>'
                     f'<td>{cfg.get("note", "")}</td><td>{"" if enabled else "禁用"}</td>'
                     f'<td>{", ".join(refs) if refs else "—"}</td><td>{ops}</td></tr>')
        body = f"""<h1>数据源管理</h1>
        <div class="bar"><a href="/datasources/new">＋新建数据源</a><a href="/">返回报表列表</a>
        <span style="font-size:12px;color:#888">改动免重启生效；密码不出现在本页</span></div>
        <table><tr><th>名称</th><th>类型</th><th>地址/文件</th><th>备注</th><th>状态</th><th>被引用报表</th><th>操作</th></tr>{rows}</table>
        <p style="font-size:12px;color:#888">禁用（或旧 `_` 前缀命名）的数据源对报表不可见；删除被引用的数据源需二次确认。</p>"""
        script = """
async function post(url, data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
async function dsTest(name, el){el.textContent='测试中…';
  const j = await (await post('/datasources/test',{name})).json();
  el.textContent = j.ok ? ('成功 '+j.ms+'ms') : ('失败:'+(j.error||'').slice(0,60));}
async function dsToggle(name, en){await post('/datasources/toggle',{name,enabled:en});location.reload();}
async function dsDel(name){
  if(!confirm('确定删除数据源 '+name+' ？'))return;
  const j = await (await post('/datasources/delete',{name})).json();
  if(!j.error){location.reload();return;}
  if(!j.referenced || !confirm('该数据源被以下报表引用：'+j.referenced.join('、')+'\\n删除后这些报表查询将报错，确定仍要删除？')){alert(j.error);return;}
  await post('/datasources/delete',{name,force:true});location.reload();}
"""
        self._send(page("数据源管理", body, script))

    def _ds_form(self, name):
        editing = name is not None
        cfg = (DS_STORE.get(name) or {}) if editing else {}
        types = ["sqlite", "mysql", "sqlserver"]
        topts = "".join(f'<option{" selected" if cfg.get("type") == t else ""}>{t}</option>' for t in types)
        en = cfg.get("enabled", True)
        body = f"""<h1>{'编辑数据源：' + name if editing else '新建数据源'}</h1>
<form onsubmit="dssave(event)"><div class="fields">
<div><label>名称</label><input id="dname" value="{name or ''}"{' disabled' if editing else ''}>
<label>类型</label><select id="dtype" onchange="updType()">{topts}</select>
<label>启用</label><input type="checkbox" id="denable"{' checked' if en else ''}></div>
<div id="f_file"><label>SQLite 文件路径</label><input id="dpath" size="40" value="{cfg.get('path', '')}" placeholder="demo.db（相对服务目录）或绝对路径"></div>
<div id="f_host"><label>主机</label><input id="dhost" value="{cfg.get('host', '')}">
<label>端口</label><input id="dport" size="6" value="{cfg.get('port', '')}">
<label>用户</label><input id="duser" value="{cfg.get('user', '')}">
<label>密码</label><input id="dpwd" type="password" value="" placeholder="{'留空 = 不修改' if editing else ''}">
<label>数据库</label><input id="ddb" value="{cfg.get('database', '')}"></div>
<div><label>超时(秒)</label><input id="dtimeout" size="4" value="{cfg.get('timeout', 30)}">
<label>备注</label><input id="dnote" size="30" value="{cfg.get('note', '')}"></div>
</div>
<div class="bar"><button>保存</button><button type="button" onclick="dstest()">测试连接</button>
<span id="dst"></span><a href="/datasources">返回列表</a></div>
</form>"""
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
  if(j.error){document.getElementById('dst').textContent='失败:'+j.error;return;}
  location.href='/datasources';}
async function dstest(){
  document.getElementById('dst').textContent='连接中…';
  const res = await fetch('/datasources/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(collect())});
  const j = await res.json();
  document.getElementById('dst').textContent = j.ok ? ('连接成功 '+j.ms+'ms') : ('失败:'+(j.error||'').slice(0,80));}
updType();
"""
        self._send(page("数据源表单", body, script))

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

    def _param_form(self, r, given):
        html = ""
        for p in r.get("params", []):
            pid, t = p["id"], p.get("type", "text")
            label = p.get("label") or pid
            dv = given.get(pid, p.get("default", ""))
            dv2 = given.get(pid + "_2", "")
            name = f'<label>{label}</label>'
            if t == "select":
                opts = "".join(f'<option{" selected" if o == dv else ""}>{o}</option>'
                               for o in (p.get("options") or "").replace("，", ",").split(",") if o)
                html += f'<span>{name}<select name="{pid}">{opts}</select></span>'
            elif t == "daterange":
                html += f'<span>{name}<input type="date" name="{pid}" value="{dv}"> ~ <input type="date" name="{pid}_2" value="{dv2}"></span>'
            elif t == "numrange":
                html += f'<span>{name}<input type="number" name="{pid}" value="{dv}" style="width:90px"> ~ <input type="number" name="{pid}_2" value="{dv2}" style="width:90px"></span>'
            elif t == "date":
                html += f'<span>{name}<input type="date" name="{pid}" value="{dv}"></span>'
            elif t == "number":
                html += f'<span>{name}<input type="number" name="{pid}" value="{dv}" style="width:110px"></span>'
            else:
                html += f'<span>{name}<input name="{pid}" value="{dv}"></span>'
        return html

    def _viewer(self, rid, args):
        path = os.path.join(REPORTS_DIR, rid + ".json")
        if not os.path.exists(path):
            return self._err(f"报表不存在: {rid}")
        r = load_json(path)
        form = self._param_form(r, args)
        body = f"""<h1>{r['name']}</h1>
<form method="get" action="/r/{rid}" id="ff"><div class="bar">{form}
<button type="submit">查询</button>
<button type="button" onclick="location='/r/{rid}/export?'+new URLSearchParams(new FormData(document.getElementById('ff')))">导出Excel</button>
</div></form>
<div id="out"><p style="color:#888">设置条件后点「查询」</p></div>"""
        script = """
function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')}
async function run(){
  const btns = document.querySelectorAll('#ff button');
  btns.forEach(b=>b.disabled=true);  // 加载态：禁用按钮防重复提交
  document.getElementById('out').innerHTML = '<p style="color:#888">查询中，请稍候…</p>';
  try{
    const res = await fetch('/q/%s',{method:'POST',headers:{'Content-Type':'application/json'},
      body:new URLSearchParams(new FormData(document.getElementById('ff')))});
    const j = await res.json();
    if(j.error){document.getElementById('out').innerHTML='<div class="err">'+escHtml(j.error)+'</div>';return;}
    let st = j.rows.length+' 行 · '+j.elapsed_ms+'ms'+(j.cached?' · 缓存命中':'');
    if(j.truncated) st += ' · <span style="color:#a32d2d">结果超限已截断，请缩小条件</span>';
    let h = '<p id="status">'+st+'</p><table><tr>'+j.columns.map(c=>'<th>'+escHtml(c)+'</th>').join('')+'</tr>';
    h += j.rows.map(r=>'<tr>'+r.map(v=>'<td>'+escHtml(v)+'</td>').join('')+'</tr>').join('')+'</table>';
    document.getElementById('out').innerHTML = h;
  } finally { btns.forEach(b=>b.disabled=false); }
}
document.getElementById('ff').addEventListener('submit',e=>{e.preventDefault();run();});
if(Object.keys(new FormData(document.getElementById('ff')).getAll('')).length||%s)run();
""" % (rid, "true" if args else "false")
        self._send(page(r["name"], body, script))

    # ---- 接口 ----
    def _save(self, data):
        rid = (data.get("id") or re.sub(r"\W+", "", data.get("name", "")) or "r1").lower()
        os.makedirs(REPORTS_DIR, exist_ok=True)
        rec = {"name": data["name"], "params": data.get("params", []),
               "cache_ttl": int(data.get("cache_ttl") or 0)}  # 0 = 实时（不缓存）
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

    def _merge_results(self, r, datasets, results):
        """按 merge 配置合并各数据集结果；单数据集直接透传。"""
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
            bcols, brows = byname[base]
            wcols, wrows = byname[with_ds]
            return merge_lookup(bcols, brows, wcols, wrows, merge.get("on") or [], merge.get("cols"))
        if mode != "union":
            raise ValueError(f"不支持的合并方式: {mode}")
        return merge_union([byname[d["name"]] for d in datasets])

    def _execute_report(self, rid, r, given):
        """报表执行统一链路（/q 与 /export 共用）：
        归一化 → 占位符替换 → 缓存查询（cache_ttl>0 时）→ 各数据集独立查询（各自限流）
        → 合并 → max_rows 截断 → 回填缓存。
        返回 {columns, rows, truncated, cached, elapsed_ms}。
        """
        t0 = time.time()
        datasets = normalize_report(r, DS_STORE.visible_names())
        values = build_values("", r.get("params", []), given)
        ttl = int(r.get("cache_ttl") or 0)  # 0 = 不缓存（实时）
        key = CACHE.make_key(rid, values) if ttl > 0 else None
        if key:
            hit = CACHE.get(key)
            if hit:
                cols, rows, tr_hit = hit
                return {"columns": cols, "rows": rows, "truncated": tr_hit,
                        "cached": True, "elapsed_ms": int((time.time() - t0) * 1000)}
        results, fetch_truncated = {}, False
        for d in datasets:
            cols, rows, tr = run_query(d["ds"], substitute(d["sql"], values))
            results[d["name"]] = (cols, rows)
            fetch_truncated = fetch_truncated or tr
        cols, rows = self._merge_results(r, datasets, results)
        max_rows = int(r.get("max_rows") or 2000)
        truncated = fetch_truncated or len(rows) > max_rows
        if len(rows) > max_rows:
            rows = rows[:max_rows]
        if key:
            CACHE.put(key, cols, rows, ttl, truncated)
        return {"columns": cols, "rows": rows, "truncated": truncated,
                "cached": False, "elapsed_ms": int((time.time() - t0) * 1000)}

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
        """编辑器单数据集试运行：用参数默认值替换后执行，返回前 20 行（复用 run_query 限流）。"""
        try:
            ds = (data.get("ds") or "").strip()
            if ds not in DS_STORE.visible_names():
                raise ValueError(f"数据源不存在或已禁用: {ds or '(空)'}")
            params = data.get("params", [])
            values = build_values("", params, {p.get("id", ""): p.get("default", "") for p in params})
            sql = substitute(data.get("sql", ""), values)
            cols, rows, tr = run_query(ds, sql)
        except Exception as e:
            return self._send(*self._json_res({"error": str(e)}))
        result = {"columns": cols, "rows": rows[:20], "truncated": tr or len(rows) > 20}
        self._send(*self._json_res(result))

    def _export(self, rid, args):
        r = self._load_report(rid)
        if r is None:
            return self._err("报表不存在")
        try:
            result = self._execute_report(rid, r, args)
        except Exception as e:
            return self._err(f"错误：{e}", 500)
        cols, rows = result["columns"], result["rows"]
        h = "".join(f"<th>{c}</th>" for c in cols)
        b = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
        xls = f'<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body><table border="1">{h}{b}</table></body></html>'
        fn = urllib.parse.quote(f"{r['name']}.xls")
        self._send(xls.encode("utf-8"), "application/vnd.ms-excel", f'attachment; filename*=UTF-8\'\'{fn}')

if __name__ == "__main__":
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8765))
    os.chdir(BASE)
    print(f"SQL报表服务 http://0.0.0.0:{port}  (报表目录: {REPORTS_DIR})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
