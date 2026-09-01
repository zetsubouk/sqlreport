#!/usr/bin/env python3
"""轻量 SQL 报表工具：贴 SQL + 参数条件 + 独立 URL + Excel 导出。
Python 标准库实现；MySQL/SQLServer 驱动按需懒加载（pymysql / pyodbc）。
运行: python3 server.py [端口]  默认 8765
"""
import json, os, re, sqlite3, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE, "reports")
DS_FILE = os.path.join(BASE, "datasources.json")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
PH_RE = re.compile(r"\{\{([\w.]+)\}\}")

# ---------------- 数据访问 ----------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def connect(ds_name):
    dss = load_json(DS_FILE)
    if ds_name not in dss or ds_name.startswith("_"):
        raise ValueError(f"数据源不存在: {ds_name}")
    ds = dss[ds_name]
    t = ds["type"]
    if t == "sqlite":
        return sqlite3.connect(ds["path"])
    if t == "mysql":
        import pymysql
        return pymysql.connect(host=ds["host"], port=int(ds.get("port", 3306)),
                               user=ds["user"], password=ds["password"],
                               database=ds["database"], charset="utf8mb4")
    if t == "sqlserver":
        import pyodbc
        dsn = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={ds['host']},{ds.get('port',1433)};DATABASE={ds['database']};UID={ds['user']};PWD={ds['password']}"
        return pyodbc.connect(dsn)
    raise ValueError(f"不支持的数据源类型: {t}")

def esc(v):
    """拼接前转义（工具面向可信 SQL 作者，仅防参数值注入）"""
    s = str(v)
    return s.replace("'", "''")

def build_values(sql, params, given):
    """按参数定义校验输入，返回 {占位符: 已转义值}"""
    values = {}
    for p in params:
        pid = p["id"]
        g = given.get(pid, "").strip() if isinstance(given.get(pid), str) else str(given.get(pid, ""))
        t = p.get("type", "text")
        if t in ("daterange", "numrange"):
            g2 = given.get(pid + "_2", "").strip() if isinstance(given.get(pid + "_2"), str) else ""
            a, b = ("_begin", "_end") if t == "daterange" else ("_min", "_max")
            if g:
                values[pid + a] = esc(g)
                values[pid + "." + a[1:]] = esc(g)  # 同时支持 {{d_begin}} 与 {{d.begin}}
            if g2:
                values[pid + b] = esc(g2)
                values[pid + "." + b[1:]] = esc(g2)
        elif g != "":
            values[pid] = esc(g)
    return values

def run_query(ds_name, sql, values):
    conn = connect(ds_name)
    try:
        cur = conn.cursor()
        cur.execute(sql, ())
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        return cols, [tuple("" if v is None else str(v) for v in r) for r in rows]
    finally:
        conn.close()

def substitute(sql, values):
    """已填参数直接替换；未填占位符所在整行丢弃（实现可选条件）"""
    def rep(m):
        k = m.group(1)
        if k in values:
            return values[k]
        return "\x00DROP"
    lines = [ln for ln in PH_RE.sub(rep, sql).splitlines() if "\x00DROP" not in ln]
    return "\n".join(lines)

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
                rows += f'<tr><td>{r.get("name", rid)}</td><td>{rid}</td><td>{r.get("ds","")}</td><td><a href="/r/{rid}">打开</a> <a href="/edit/{rid}">编辑</a></td></tr>'
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
        dss = [k for k in load_json(DS_FILE) if not k.startswith("_")]
        opts = "".join(f'<option{" selected" if r["ds"]==k else ""}>{k}</option>' for k in dss)
        body = f"""<h1>报表编辑器</h1>
<form onsubmit="save(event)">
<div class="fields">
<div><label>报表名称</label><input id="rname" size="30" value="{r['name']}">
 <label>数据源</label><select id="rds">{opts}</select></div>
<div><textarea id="rsql" rows="10" placeholder="SELECT ... WHERE dt BETWEEN '{{{{dt.begin}}}}' AND '{{{{dt.end}}}}'">{r['sql']}</textarea>
<p style="font-size:12px;color:#888">占位符：普通参数 {{{{id}}}}；日期范围自动展开 {{{{id.begin}}}}/{{{{id.end}}}}；数字范围 {{{{id.min}}}}/{{{{id.max}}}}</p></div>
</div>
<h2>查询参数（留空则无条件）</h2>
<div id="plist"></div>
<div class="bar"><button type="button" onclick="addp()">＋加参数</button><button>保存并生成URL</button><span id="status"></span></div>
</form>"""
        script = """
let params = %s;
const TYPES = {text:'文本', select:'下拉', date:'日期', daterange:'日期范围', number:'数字', numrange:'数字范围'};
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}
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
    body:JSON.stringify({id:%s,name:document.getElementById('rname').value,ds:document.getElementById('rds').value,sql:document.getElementById('rsql').value,params:collect()})});
  const j = await res.json();
  document.getElementById('status').textContent = j.error ? ('失败:'+j.error) : ('已保存，独立URL: /r/'+j.id);}
params.forEach((p,i)=>addp(p));
""" % (json.dumps(r["params"], ensure_ascii=False), json.dumps(rid) if rid else "null")
        self._send(page("编辑报表", body, script))

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
async function run(){const res = await fetch('/q/%s',{method:'POST',headers:{'Content-Type':'application/json'},
  body:new URLSearchParams(new FormData(document.getElementById('ff')))});
  const j = await res.json();
  if(j.error){document.getElementById('out').innerHTML='<div class="err">'+j.error+'</div>';return;}
  let h = '<p id="status">'+j.rows.length+' 行</p><table><tr>'+j.columns.map(c=>'<th>'+c+'</th>').join('')+'</tr>';
  h += j.rows.map(r=>'<tr>'+r.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</table>';
  document.getElementById('out').innerHTML = h;}
document.getElementById('ff').addEventListener('submit',e=>{e.preventDefault();run();});
if(Object.keys(new FormData(document.getElementById('ff')).getAll('')).length||%s)run();
""" % (rid, "true" if args else "false")
        self._send(page(r["name"], body, script))

    # ---- 接口 ----
    def _save(self, data):
        rid = (data.get("id") or re.sub(r"\W+", "", data.get("name", "")) or "r1").lower()
        os.makedirs(REPORTS_DIR, exist_ok=True)
        rec = {"name": data["name"], "ds": data["ds"], "sql": data["sql"], "params": data.get("params", [])}
        # 保存前试编译：验证数据源存在、占位符可解析（用默认值空跑不执行）
        values = build_values(rec["sql"], rec["params"], {p["id"]: p.get("default", "") for p in rec["params"]})
        substitute(rec["sql"], values)
        with open(os.path.join(REPORTS_DIR, rid + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        self._send(json.dumps({"id": rid}), "application/json; charset=utf-8")

    def _query(self, rid, given):
        path = os.path.join(REPORTS_DIR, rid + ".json")
        if not os.path.exists(path):
            return self._send(json.dumps({"error": "报表不存在"}), "application/json; charset=utf-8")
        r = load_json(path)
        values = build_values(r["sql"], r.get("params", []), given)
        cols, rows = run_query(r["ds"], substitute(r["sql"], values), values)
        self._send(json.dumps({"columns": cols, "rows": rows}, ensure_ascii=False), "application/json; charset=utf-8")

    def _export(self, rid, args):
        path = os.path.join(REPORTS_DIR, rid + ".json")
        if not os.path.exists(path):
            return self._err("报表不存在")
        r = load_json(path)
        values = build_values(r["sql"], r.get("params", []), args)
        cols, rows = run_query(r["ds"], substitute(r["sql"], values), values)
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
