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

from sqlreport.db import CACHE, DS_STORE, load_json, run_query, merge_union, merge_lookup
from sqlreport.params import build_values, substitute, normalize_report, esc

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
<title>__TITLE__ - SQL 报表</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--surface-muted:#f8f9fc;--border:#e4e8ef;--border-strong:#cfd7e3;
--text:#1c2431;--text-secondary:#5b6574;--text-muted:#8a94a6;--brand:#5b8dd8;--brand-strong:#4a78c2;
--brand-weak:#e9f0fa;--brand-line:#c6d8f2;--success:#58a97c;--success-weak:#e8f4ee;--danger:#d97a7a;
--danger-weak:#fbecec;--warning:#c49a4a;--warning-weak:#fbf4e4;--radius:8px;--radius-card:12px;--radius-full:999px;
--shadow-sm:0 1px 2px rgba(28,42,66,.06);--shadow-md:0 6px 18px rgba(28,42,66,.09);
--font:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg);color:var(--text);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--brand);text-decoration:none}
button{font-family:inherit;cursor:pointer;border:none}
input,select,textarea{font-family:inherit;font-size:13px;color:var(--text);border:1px solid var(--border);border-radius:var(--radius);padding:7px 10px;background:#fff;transition:border-color .12s,box-shadow .12s}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-weak)}
::placeholder{color:var(--text-muted)}
/* 导航 */
.nav{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
.nav-inner{max-width:1180px;margin:0 auto;padding:0 24px;height:52px;display:flex;align-items:center;gap:24px}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px}
.brand .logo{width:26px;height:26px;border-radius:7px;background:linear-gradient(135deg,var(--brand),#8fb3e3);color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800}
.nav-links{display:flex;gap:2px}
.nav-links a{padding:6px 12px;border-radius:7px;color:var(--text-secondary);font-size:13.5px;font-weight:500}
.nav-links a:hover{background:var(--surface-muted);color:var(--text)}
.nav-links a.active{background:var(--brand-weak);color:var(--brand-strong)}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.badge{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:500;padding:2px 9px;border-radius:var(--radius-full);border:1px solid var(--border);color:var(--text-secondary);background:#fff}
.badge .dot{width:6px;height:6px;border-radius:50%;background:var(--success)}
.version{font-size:11.5px;color:var(--text-muted);font-weight:500}
/* 页面 */
.page{max-width:1180px;margin:0 auto;padding:32px 24px 80px}
.pagehead{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:20px}
.pagehead h1{font-size:22px;font-weight:700}
.pagehead .sub{color:var(--text-secondary);font-size:13.5px;margin-top:3px}
.crumb{font-size:12.5px;color:var(--text-muted);margin-bottom:8px}
.crumb b{color:var(--text-secondary);font-weight:500}
/* 按钮 */
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:8px;font-size:13.5px;font-weight:500;transition:background .12s,box-shadow .12s,transform .05s}
.btn:active{transform:translateY(1px)}
.btn-primary{background:var(--brand);color:#fff}
.btn-primary:hover{background:var(--brand-strong);box-shadow:var(--shadow-sm)}
.btn-secondary{background:#fff;color:var(--text);border:1px solid var(--border-strong)}
.btn-secondary:hover{background:var(--surface-muted);border-color:var(--brand)}
.btn-danger-ghost{background:#fff;color:var(--danger);border:1px solid var(--border-strong)}
.btn-danger-ghost:hover{background:var(--danger-weak)}
.btn-sm{padding:4px 9px;font-size:12.5px;border-radius:6px}
.btn-icon{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:6px;color:var(--text-secondary);background:transparent;border:1px solid transparent;cursor:pointer}
.btn-icon:hover{background:var(--brand-weak);border-color:var(--brand-line);color:var(--brand-strong)}
/* 卡片 */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-card);box-shadow:var(--shadow-sm)}
.card-head{display:flex;align-items:center;gap:12px;padding:13px 18px;border-bottom:1px solid var(--border)}
.card-head h3{font-size:14px;font-weight:600}
.card-head .hint{margin-left:auto;font-size:12px;color:var(--text-muted)}
/* 工具栏 */
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.search{position:relative;display:flex;align-items:center}
.search input{width:260px;padding-left:32px}
.search .ic{position:absolute;left:10px;color:var(--text-muted);font-size:13px}
/* 表格 */
.table-wrap{overflow:auto;max-height:60vh}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{background:var(--surface-muted);color:var(--text-secondary);font-weight:600;font-size:12px;letter-spacing:.3px;text-align:left;padding:9px 16px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:11px 16px;border-bottom:1px solid var(--border);vertical-align:middle;white-space:nowrap}
tbody tr{transition:background .1s}
tbody tr:hover{background:var(--surface-muted)}
tbody tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
code,.mono{font-family:var(--font-mono);font-size:12.5px}
.id-cell code{color:var(--brand-strong);background:var(--brand-weak);padding:2px 7px;border-radius:5px;font-size:12px}
.updated{color:var(--text-muted);font-size:12px}
/* 徽章 */
.tag{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:500;padding:2px 9px;border-radius:var(--radius-full);line-height:1.6}
.tag-on{background:var(--success-weak);color:var(--success)}
.tag-off{background:var(--surface-muted);color:var(--text-muted);border:1px solid var(--border)}
.tag-cache{background:var(--brand-weak);color:var(--brand-strong)}
.tag-warn{background:var(--warning-weak);color:var(--warning)}
.tag-danger{background:var(--danger-weak);color:var(--danger)}
.tag-type{background:var(--surface-muted);color:var(--text-secondary);border:1px solid var(--border)}
/* 操作链接 */
.ops{display:flex;align-items:center;gap:2px}
.op{font-size:12.5px;color:var(--text-secondary);padding:4px 7px;border-radius:6px;font-weight:500}
.op:hover{background:var(--brand-weak);color:var(--brand-strong)}
.op.danger:hover{background:var(--danger-weak);color:var(--danger)}
/* 空状态 */
.empty{text-align:center;padding:56px 20px;color:var(--text-muted)}
.empty .il{width:64px;height:64px;margin:0 auto 16px;border-radius:16px;background:var(--brand-weak);display:flex;align-items:center;justify-content:center;font-size:26px}
.empty h4{font-size:15px;color:var(--text);font-weight:600;margin-bottom:4px}
.empty p{font-size:13px;max-width:360px;margin:0 auto 16px}
/* 查询页表单 */
.parambar{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end}
.field{display:flex;flex-direction:column;gap:5px}
.field label{font-size:12px;color:var(--text-secondary);font-weight:500}
.field .range{display:flex;align-items:center;gap:6px;color:var(--text-muted)}
.actbar{display:flex;align-items:center;gap:10px;margin-top:16px;flex-wrap:wrap}
.rng-seg{display:inline-flex;background:var(--surface-muted);border:1px solid var(--border);border-radius:99px;padding:1px;gap:2px;margin-left:6px;vertical-align:1px}
.rng-seg a{font-size:11px;padding:0 8px;border-radius:99px;color:var(--text-muted);cursor:pointer;line-height:1.7}
.rng-seg a.on{background:var(--brand);color:#fff}
.statusline{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--border);font-size:12.5px;color:var(--text-secondary)}
.statusline .sep{color:var(--border-strong)}
.spinner{width:14px;height:14px;border:2px solid var(--border-strong);border-top-color:var(--brand);border-radius:50%;animation:spin .7s linear infinite;display:inline-block;vertical-align:-2px}
@keyframes spin{to{transform:rotate(360deg)}}
.resultbar{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.resultbar .spacer{flex:1}
/* 统计 */
.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-card);padding:14px 18px;box-shadow:var(--shadow-sm)}
.stat .k{font-size:12px;color:var(--text-muted)}
.stat .v{font-size:22px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}
.stat .v small{font-size:13px;color:var(--text-muted);font-weight:500}
/* 数据源卡片 */
.ds-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.ds-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-card);padding:16px 18px;box-shadow:var(--shadow-sm);transition:box-shadow .12s,border-color .12s}
.ds-card:hover{box-shadow:var(--shadow-md);border-color:var(--brand-line)}
.ds-card .top{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.ds-card .typeic{width:36px;height:36px;border-radius:10px;background:var(--brand-weak);color:var(--brand-strong);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700}
.ds-card .nm{font-weight:600;font-size:14px}
.ds-card .meta{font-size:12.5px;color:var(--text-muted);margin-bottom:12px;display:flex;flex-direction:column;gap:3px}
.ds-card .meta .mono{color:var(--text-secondary)}
.ds-card .ft{display:flex;align-items:center;gap:8px}
.ds-card .ft .ops{margin-left:auto}
/* 编辑器 */
.editor-grid{display:grid;grid-template-columns:1fr 360px;gap:16px;align-items:start}
.block{background:var(--surface-muted);border:1px solid var(--border);border-radius:var(--radius-card);padding:14px 16px;margin-top:12px}
.block .bt{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.block .bt b{font-size:13px}
.ds-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.param-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
/* 参数卡（编辑器） */
.pr{background:var(--surface-muted);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.pr-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.pr-tag{font-size:11px;font-weight:600;color:var(--brand-strong);background:var(--brand-weak);padding:1px 9px;border-radius:var(--radius-full);white-space:nowrap}
.pr-name{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pr-sp{flex:1}
.pr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(125px,1fr));gap:8px 10px}
.pr-sec{border-top:1px dashed var(--border-strong);margin-top:9px;padding-top:8px}
.pr-sec-t{font-size:12px;color:var(--text-secondary);font-weight:600;margin-bottom:6px}
.pr-sec-t .p-bindhint{font-weight:400;color:var(--text-muted);font-size:11.5px;margin-left:6px}
.switch{position:relative;width:34px;height:20px;background:var(--border-strong);border-radius:var(--radius-full);transition:background .15s;flex-shrink:0;display:inline-block;cursor:pointer}
.switch.on{background:var(--brand)}
.switch::after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .15s}
.switch.on::after{transform:translateX(14px)}
.reference{background:var(--brand-weak);border:1px solid var(--brand-line);border-radius:10px;padding:12px 14px;margin-top:12px}
.reference b{font-size:12.5px;color:var(--brand-strong);display:block;margin-bottom:6px}
.reference code{display:block;font-size:12px;color:var(--text-secondary);margin:3px 0;background:rgba(255,255,255,.6);border-radius:5px;padding:3px 8px}
/* 兼容旧模板类 */
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.fields div{margin:6px 0}
.err{color:#a34a4a;background:var(--danger-weak);border-left:3px solid var(--danger);padding:8px 12px;border-radius:6px;margin:8px 0}
#status{font-size:12px;color:var(--text-muted)}
.pv{font-size:12px;margin-top:8px}
.pv table{border-collapse:collapse;margin-top:4px}
.pv th,.pv td{border:1px solid var(--border);padding:3px 8px;font-size:12px}
/* 弹层 / toast */
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:8px;background:var(--text);color:#fff;padding:10px 16px;border-radius:9px;font-size:13px;box-shadow:var(--shadow-md);z-index:99}
.toast.ok{background:var(--success)}
.toast.err{background:var(--danger)}
.modal-mask{position:fixed;inset:0;background:rgba(28,42,66,.4);display:flex;align-items:center;justify-content:center;z-index:100;backdrop-filter:blur(2px)}
.modal{background:#fff;border-radius:14px;box-shadow:var(--shadow-md);width:400px;padding:22px;text-align:center}
.modal .m-ic{width:44px;height:44px;border-radius:50%;background:var(--danger-weak);color:var(--danger);display:flex;align-items:center;justify-content:center;margin:0 auto 12px;font-size:20px}
.modal h3{font-size:16px;margin-bottom:6px}
.modal p{font-size:13px;color:var(--text-secondary);margin-bottom:18px}
.modal .m-btns{display:flex;gap:10px;justify-content:center}
.hidden{display:none!important}
@media (max-width:900px){.editor-grid{grid-template-columns:1fr}.stat-grid{grid-template-columns:repeat(2,1fr)}.ds-grid{grid-template-columns:1fr}}
</style></head><body>
__NAV__
<div class="page">__BODY__</div>
<script>
function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
function toast(msg,cls){var t=document.createElement('div');t.className='toast '+(cls||'');t.textContent=msg;document.body.appendChild(t);setTimeout(function(){t.remove();},2200)}
function copyText(t){var done=function(){toast('链接已复制','ok')},fail=function(){var i=document.createElement('textarea');i.value=t;document.body.appendChild(i);i.select();try{document.execCommand('copy');done()}catch(e){toast('复制失败','err')}document.body.removeChild(i)};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fail)}else{fail()}}
/* 共享表格组件：查看页与编辑器试运行共用（滚动容器+客户端分页+状态行）。
   宿主先 tblInit(out)，再 tblShow(out,j,defPage)。 */
function tblInit(out){
  if(out.classList.contains('rs'))return;
  out.classList.add('rs');
  out.addEventListener('change',function(e){
    if(e.target&&e.target.classList&&e.target.classList.contains('ppage'))tblGo(out,1);});
  out.addEventListener('click',function(e){
    var b=e.target&&e.target.closest?e.target.closest('[data-pg]'):null;
    if(b&&!b.disabled)tblGo(out,parseInt(b.getAttribute('data-pg'),10));});
}
function tblGo(out,p){if(out._st){out._st.page=Math.max(1,p);tblDraw(out);}}
function tblDraw(out){
  var st=out._st;
  var psel=out.querySelector('.ppage');
  var ps=(psel?parseInt(psel.value,10):0)||st.defPage;
  var total=st.rows.length,pages=Math.max(1,Math.ceil(total/ps));
  if(st.page>pages)st.page=pages;
  var start=(st.page-1)*ps,end=Math.min(start+ps,total),slice=st.rows.slice(start,end);
  var h='';
  if(st.status)h+='<div class="statusline">'+st.status+'</div>';
  h+='<div class="table-wrap"><table><thead><tr>'+st.cols.map(function(c){return '<th>'+escHtml(c)+'</th>';}).join('')+'</tr></thead><tbody>';
  h+=slice.map(function(r){return '<tr>'+r.map(function(v,i){
    return '<td>'+escHtml(v)+'</td>';}).join('')+'</tr>';}).join('');
  h+='</tbody></table></div>';
  h+='<div class="resultbar">';
  h+='<span style="font-size:12.5px;color:var(--text-secondary)">'+total+' 行 · 第 '+st.page+'/'+pages+' 页</span><span class="spacer"></span>';
  h+='每页 <select class="ppage">'+function(){
    var opts=[5,10,20,50,100,200];
    if(opts.indexOf(ps)<0)opts.push(ps);  // defPage 不在预设列表时补入，避免翻页重绘后页大小漂移
    opts.sort(function(a,b){return a-b;});
    return opts.map(function(n){
      return '<option value="'+n+'"'+(n===ps?' selected':'')+'>'+n+'</option>';}).join('');
  }()+'</select>';
  h+='<button class="btn btn-secondary btn-sm" data-pg="'+(st.page-1)+'"'+(st.page<=1?' disabled':'')+'>‹ 上一页</button>';
  h+='<button class="btn btn-secondary btn-sm" data-pg="'+(st.page+1)+'"'+(st.page>=pages?' disabled':'')+'>下一页 ›</button>';
  h+='</div>';
  out.innerHTML=h;
}
function tblShow(out,j,defPage){
  if(j.error){out.innerHTML='<div class="err" style="margin:14px">'+escHtml(j.error)+'</div>';return;}
  var st='<span>共 <b>'+(j.rows||[]).length+'</b> 行</span>';
  if(j.elapsed_ms!=null)st+='<span class="sep">·</span><span>耗时 <b>'+j.elapsed_ms+'</b> ms</span>';
  if(j.cached)st+='<span class="sep">·</span><span class="tag tag-cache">缓存命中</span>';
  if(j.truncated)st+='<span class="sep">·</span><span class="tag tag-warn">结果已截断</span>';
  out._st={rows:j.rows||[],cols:j.columns||[],ct:j.coltypes||[],page:1,defPage:defPage||20,status:st};
  tblDraw(out);
}
__SCRIPT__
</script></body></html>"""

def nav(active=""):
    items = [("home", "报表列表", "/"), ("editor", "新建报表", "/new"), ("ds", "数据源管理", "/datasources")]
    links = ""
    for k, name, href in items:
        cls = ' class="active"' if k == active else ""
        links += f'<a href="{href}"{cls}>{name}</a>'
    return f'<nav class="nav"><div class="nav-inner"><div class="brand"><span class="logo">SQ</span>SQL 报表</div>' \
           f'<div class="nav-links">{links}</div>' \
           f'<div class="nav-right"><span class="badge"><span class="dot"></span>服务运行中</span><span class="version">v0.3.0</span></div></div></nav>'

def page(title, body, script="", active=""):
    return PAGE.replace("__TITLE__", title).replace("__NAV__", nav(active)) \
               .replace("__BODY__", body).replace("__SCRIPT__", script).encode()


def esc_html(s):
    """HTML 转义（服务端渲染用户数据时防注入/破版）。"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _rel_time(ts):
    """文件时间戳 → 相对时间文案。"""
    d = time.time() - ts
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    if d < 86400 * 30:
        return f"{int(d // 86400)} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(ts))

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
        rows = ""
        n_total = n_multi = n_cached = 0
        for fn in sorted(os.listdir(REPORTS_DIR) if os.path.isdir(REPORTS_DIR) else []):
            if not fn.endswith(".json"):
                continue
            try:
                r = load_json(os.path.join(REPORTS_DIR, fn))
                rid = fn[:-5]
                name = esc_html(r.get("name", rid))
                if r.get("datasets"):
                    ds_names = [d.get("ds", "") for d in r.get("datasets", []) if d.get("ds")]
                else:
                    ds_names = [r.get("ds")] if r.get("ds") else []
                ds_tags = " ".join(f'<span class="tag tag-type">{esc_html(d)}</span>'
                                   for d in dict.fromkeys(ds_names)) or '<span style="color:var(--text-muted)">—</span>'
                ttl = int(r.get("cache_ttl") or 0)
                cache_tag = f'<span class="tag tag-cache">TTL {ttl}s</span>' if ttl > 0 else '<span class="tag tag-on">实时</span>'
                if ttl > 0:
                    n_cached += 1
                max_rows = int(r.get("max_rows") or 2000)
                if len(r.get("datasets", [])) >= 2:
                    n_multi += 1
                n_total += 1
                mtime = _rel_time(os.path.getmtime(os.path.join(REPORTS_DIR, fn)))
                rows += (f'<tr><td><b>{name}</b></td>'
                         f'<td><span class="id-cell"><code>/r/{rid}</code></span></td>'
                         f'<td>{ds_tags}</td><td>{cache_tag}</td>'
                         f'<td class="num">{max_rows:,}</td>'
                         f'<td class="updated">{mtime}</td>'
                         f'<td><div class="ops"><a class="op" href="/r/{rid}">打开</a>'
                         f'<a class="op" href="/edit/{rid}">编辑</a>'
                         f'<a class="op" href="#" onclick="copyText(location.origin+\'/r/{rid}\');return false">复制链接</a>'
                         f'<a class="op danger" href="#" onclick="delReport(\'{rid}\');return false">删除</a></div></td></tr>')
            except Exception:
                pass
        dss = DS_STORE.load()
        ds_enabled = len(DS_STORE.visible_names())
        if not rows:
            rows = ('<tr><td colspan="7"><div class="empty"><div class="il">📄</div>'
                    '<h4>还没有报表</h4>'
                    '<p>把 SQL 贴进来，配上参数条件，就能生成一张可独立访问、可导出 Excel 的报表。</p>'
                    '<a class="btn btn-primary" href="/new">＋ 新建第一张报表</a></div></td></tr>')
        stat = (f'<div class="stat"><div class="k">报表总数</div><div class="v">{n_total} <small>张</small></div></div>'
                f'<div class="stat"><div class="k">数据源</div><div class="v">{ds_enabled} <small>/ {len(dss)} 启用</small></div></div>'
                f'<div class="stat"><div class="k">多数据集合并</div><div class="v">{n_multi} <small>张</small></div></div>'
                f'<div class="stat"><div class="k">启用缓存</div><div class="v">{n_cached} <small>张</small></div></div>')
        ds_opts = "".join(f'<option>{esc_html(d)}</option>' for d in DS_STORE.visible_names())
        body = f"""<div class="pagehead"><div><h1>报表列表</h1><div class="sub">贴 SQL 生成报表，独立 URL 免登录访问，一键导出 Excel</div></div>
        <a class="btn btn-primary" href="/new">＋ 新建报表</a></div>
        <div class="stat-grid">{stat}</div>
        <div class="toolbar">
          <div class="search"><span class="ic">⌕</span><input id="q" placeholder="搜索报表名称或 ID…"></div>
          <select id="fds"><option value="">全部数据源</option>{ds_opts}</select>
        </div>
        <div class="card"><div class="table-wrap"><table id="rt">
        <thead><tr><th>名称</th><th>ID · 独立 URL</th><th>数据源</th><th>缓存</th><th class="num">最大行数</th><th>更新时间</th><th>操作</th></tr></thead>
        <tbody>{rows}</tbody></table></div></div>
        <div class="modal-mask hidden" id="mdl">
          <div class="modal"><div class="m-ic">⚠</div><h3 id="mdl-title"></h3><p id="mdl-body"></p>
          <div class="m-btns"><button class="btn btn-secondary" onclick="hideModal()">取消</button>
          <button class="btn btn-danger-ghost" id="mdl-ok">确认删除</button></div></div>
        </div>"""
        script = """
function post(url, data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
function showModal(){document.getElementById('mdl').classList.remove('hidden');}
function hideModal(){document.getElementById('mdl').classList.add('hidden');}
function delReport(id){
  document.getElementById('mdl-title').textContent='删除报表 '+id+' ？';
  document.getElementById('mdl-body').innerHTML='确定删除该报表？删除后其独立 URL 将失效。';
  document.getElementById('mdl-ok').onclick=async function(){
    hideModal();
    const j=await (await post('/delete_report',{id})).json();
    if(j.error){toast('删除失败：'+j.error,'err');return;}
    toast('已删除','ok');location.reload();
  };
  showModal();}
function flt(){
  var q=document.getElementById('q').value.toLowerCase(), f=document.getElementById('fds').value;
  document.querySelectorAll('#rt tbody tr').forEach(function(r){
    var show=(!q||r.textContent.toLowerCase().indexOf(q)>=0)&&(!f||r.textContent.indexOf(f)>=0);
    r.style.display=show?'':'none';});
}
document.getElementById('q').addEventListener('input',flt);
document.getElementById('fds').addEventListener('change',flt);
"""
        self._send(page("报表列表", body, script, "home"))

    def _editor(self, rid):
        r = {"name": "", "ds": "", "sql": "", "params": []}
        if rid:
            path = os.path.join(REPORTS_DIR, rid + ".json")
            if not os.path.exists(path):
                return self._err(f"报表不存在: {rid}", 404)
            r = load_json(path)
        dss = DS_STORE.visible_names()
        fmt = r.get("export_format") or "xls"
        qmode = r.get("query_mode") or "manual"
        psize = int(r.get("page_size") or 20)
        # 双格式兼容：旧 {ds, sql} 视为单个数据集 main 编辑，保存时自动写回旧格式
        datasets = r.get("datasets") or [{"name": "main", "ds": r.get("ds", ""), "sql": r.get("sql", "")}]
        merge = r.get("merge") or {"mode": "union"}
        title = "编辑报表" if rid else "新建报表"
        body = f"""<div class="crumb">报表列表 / <b>{title}</b></div>
        <div class="pagehead"><div><h1>{title}</h1><div class="sub">保存后即可获得独立访问 URL</div></div>
        <div style="display:flex;gap:8px"><a class="btn btn-secondary" href="/">取消</a>
        <button class="btn btn-primary" onclick="save(event)">保存并生成 URL</button></div></div>
        <form onsubmit="save(event)" id="edform">
        <div class="editor-grid">
        <div>
        <div class="card"><div class="card-head"><h3>基本信息</h3><span class="hint">报表 ID 由名称自动生成</span></div>
        <div style="padding:16px 18px;display:flex;gap:14px;flex-wrap:wrap">
          <div class="field" style="flex:2;min-width:220px"><label>报表名称</label><input id="rname" placeholder="如：订单汇总" value="{esc_html(r.get('name', ''))}"></div>
          <div class="field" style="width:110px"><label>缓存秒数</label><input id="rcache" type="number" value="{r.get('cache_ttl', 0)}" title="0=实时，每次直查数据库"></div>
          <div class="field" style="width:130px"><label>最大行数</label><input id="rmax" type="number" value="{r.get('max_rows', 2000) or 2000}" title="展示行上限，超限截断并提示"></div>
          <div class="field" style="width:150px"><label>默认导出格式</label><select id="rexp"><option value="xls"{' selected' if fmt == 'xls' else ''}>Excel .xls</option><option value="csv"{' selected' if fmt == 'csv' else ''}>CSV .csv</option></select></div>
          <div class="field" style="width:130px"><label>查询模式</label><select id="rquery"><option value="manual"{' selected' if qmode == 'manual' else ''}>手动查询</option><option value="auto"{' selected' if qmode == 'auto' else ''}>自动查询</option></select></div>
          <div class="field" style="width:100px"><label>每页行数</label><input id="rpage" type="number" value="{psize}" title="查询结果每页显示行数"></div>
        </div></div>
        <div class="card" style="margin-top:16px"><div class="card-head"><h3>数据集</h3><span class="hint">≥2 个时可配置合并方式</span></div>
        <div style="padding:16px 18px"><div id="dlist"></div>
        <button type="button" class="btn btn-secondary btn-sm" onclick="addDs()">＋ 加数据集</button></div></div>
        </div>
        <div>
        <div class="card"><div class="card-head"><h3>合并方式</h3></div>
        <div style="padding:16px 18px" id="mrow">
          <div class="field" style="margin-bottom:10px"><label>方式</label>
            <select id="mmode" onchange="updMerge()"><option value="union">纵向合并 union</option><option value="lookup">横向关联 lookup</option></select></div>
          <div id="lookupcfg" class="param-row" style="display:none">
            <div class="field"><label>主数据集</label><select id="mbase"></select></div>
            <div class="field"><label>关联</label><select id="mwith"></select></div>
            <div class="field"><label>关联键</label><input id="mon" placeholder="列名,可多列"></div>
            <div class="field"><label>取值列</label><input id="mcols" placeholder="留空=除键外全部"></div>
          </div>
        </div></div>
        <div class="card" style="margin-top:16px"><div class="card-head"><h3>查询参数</h3><span class="hint">全局共享，留空则无条件</span></div>
        <div style="padding:16px 18px"><div id="plist"></div>
        <button type="button" class="btn btn-secondary btn-sm" onclick="addp()">＋ 加参数</button></div></div>
        <div class="reference"><b>查询条件说明</b>
        <code>参数绑定「数据集 + 过滤字段」后，查询时自动生成 WHERE 条件：文本/日期/数字 =，范围类型 ≥ 与 ≤，无需改写 SQL</code>
        <code>{{id}} 文本占位符（手写进 SQL 的条件仍然有效）</code>
        <code>{{d.begin}} / {{d.end}} 日期范围起止</code>
        <code>{{n.min}} / {{n.max}} 数字范围起止</code>
        <code style="color:var(--brand-strong)">含未填参数的整行占位条件会自动跳过；下拉/文本候选值默认取绑定字段去重，候选SQL仅提供选项</code></div>
        </div>
        </div>
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
  div.className = 'block';
  div.innerHTML = `<div class="bt"><b>数据集</b><span class="tag tag-type">${esc(d.ds)||'未选源'}</span>
    <span style="margin-left:auto">
    <button type="button" class="btn btn-secondary btn-sm" onclick="pv(this)">试运行</button>
    <button type="button" class="btn btn-danger-ghost btn-sm" onclick="rmDs(this)">删除</button></span></div>
    <div class="ds-row">
      <div class="field" style="flex:1"><label>数据源</label><select class="dds">${dsOpts(d.ds)}</select></div>
      <div class="field" style="flex:1"><label>数据集名称</label><input class="dname" placeholder="main" value="${esc(d.name)}"></div>
    </div>
    <textarea class="dsql" rows="4" style="width:100%%;font-family:var(--font-mono);font-size:12.5px" placeholder="SELECT ... WHERE dt BETWEEN '{{d.begin}}' AND '{{d.end}}'">${esc(d.sql)}</textarea>
    <div class="pv"></div>`;
  document.getElementById('dlist').appendChild(div);updMerge();refreshSrcDs();}
function rmDs(btn){btn.closest('#dlist > div').remove();updMerge();refreshSrcDs();}
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
  tblInit(out);
  out.innerHTML='<div class="statusline"><span class="spinner"></span> 试运行中…</div>';
  const res = await fetch('/preview',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ds:block.querySelector('.dds').value,sql:block.querySelector('.dsql').value,params:collect()})});
  const j = await res.json();
  tblShow(out,j,200);}
function syncName(row){
  row.querySelector('.pr-name').textContent =
    row.querySelector('.p-label').value || row.querySelector('.p-id').value || '未命名参数';}
function syncRow(row){
  const t=row.querySelector('.p-type').value;
  const isRng=t==='date'||t==='number';
  row.querySelector('.p-rngbox').style.display = isRng ? '' : 'none';
  row.querySelector('.p-optbox').style.display = t==='select' ? '' : 'none';
  row.querySelector('.f-srcsql').style.display = (t==='select'||t==='text') ? '' : 'none';
  row.querySelector('.p-bindhint').textContent =
    t==='select' ? '选项默认取绑定字段去重值；填了候选SQL则用SQL取值'
    : t==='text' ? '绑定字段后为文本输入提供候选值'
    : '绑定字段后，查询时自动按该字段过滤（范围类型生成 ≥/≤ 条件）';
  row.querySelector('.pr-tag').textContent = TYPES[t];
  syncName(row);}
function refreshSrcDs(){
  document.querySelectorAll('#plist .p-srcds').forEach(function(sel){
    const cur=sel.value;
    sel.innerHTML=datasets.map(x=>'<option'+(x.name===cur?' selected':'')+'>'+esc(x.name)+'</option>').join('');
  });
  document.querySelectorAll('#plist .pr').forEach(function(row){
    const ds=row.querySelector('.p-srcds');
    if(ds.value && !row.querySelector('.p-srcfield').getAttribute('data-loaded'))loadFields(ds);
  });
}
function fldChanged(sel){
  const row=sel.closest('.pr');
  sel.setAttribute('data-cur',sel.value);
  if(!sel.value)return;
  const id=row.querySelector('.p-id');
  if(!id.value)id.value=sel.value;
  const lb=row.querySelector('.p-label');
  if(!lb.value)lb.value=sel.value;
  syncName(row);
}
function loadFields(el){
  const row=el.closest('.pr');
  const d=collectDs().find(function(x){return x.name===row.querySelector('.p-srcds').value;});
  if(!d){toast('请先选择数据集（若列表为空请先添加数据集）','err');return;}
  const sel=row.querySelector('.p-srcfield');
  sel.innerHTML='<option value="">加载中…</option>';
  const defaults={};
  collect().forEach(function(p){defaults[p.id]=p.default||'';});
  fetch('/ds-fields',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ds_src:d.ds, sql:d.sql, values:defaults})})
   .then(function(r){return r.json();}).then(function(j){
     if(j.error){toast('取字段失败：'+j.error,'err');sel.innerHTML='<option value="">不绑定（不过滤）</option>';return;}
     sel.setAttribute('data-loaded','1');
     sel.innerHTML='<option value="">不绑定（不过滤）</option>'+j.fields.map(function(f){return '<option>'+esc(f)+'</option>';}).join('');
     const cur=sel.getAttribute('data-cur');
     if(cur&&j.fields.indexOf(cur)>=0)sel.value=cur;
   });
}
function prmDel(btn){btn.closest('.pr').remove();}
function addp(p){p = p || {id:'',label:'',type:'select',options:'',default:'',range:false,source:null};
  const src = p.source||{};
  if(src.mode==='sql'){src.mode='field';src.field=src.field||'';}
  const ds = src.ds||'';
  const d = document.createElement('div');
  d.className = 'pr';
  d.innerHTML = `
    <div class="pr-head">
      <span class="pr-tag">${esc(TYPES[p.type]||'')}</span>
      <span class="pr-name">${esc(p.label||p.id||'未命名参数')}</span>
      <span class="pr-sp"></span>
      <button type="button" class="btn-icon" title="删除参数" onclick="prmDel(this)">✕</button>
    </div>
    <div class="pr-grid">
      <div class="field"><label>参数ID</label><input class="p-id" placeholder="选字段后自动填" value="${esc(p.id)}"></div>
      <div class="field"><label>显示名</label><input class="p-label" placeholder="显示名" value="${esc(p.label)}"></div>
      <div class="field"><label>类型</label><select class="p-type" onchange="syncRow(this.closest('.pr'))">${Object.entries(TYPES).map(([k,v])=>`<option value="${k}"${p.type===k?' selected':''}>${v}</option>`).join('')}</select></div>
      <div class="field"><label>默认值</label><input class="p-default" placeholder="默认" value="${esc(p.default||'')}"></div>
      <div class="field p-rngbox" style="display:none"><label>范围模式</label><label class="switch${p.range?' on':''}"><input type="checkbox" class="p-range" style="display:none"${p.range?' checked':''} onchange="this.parentNode.classList.toggle('on',this.checked)"></label></div>
    </div>
    <div class="pr-sec">
      <div class="pr-sec-t">数据绑定<span class="p-bindhint"></span></div>
      <div class="pr-grid">
        <div class="field"><label>数据集</label><select class="p-srcds" onchange="this.closest('.pr').querySelector('.p-srcfield').removeAttribute('data-loaded');loadFields(this)">${datasets.map(x=>`<option${x.name===ds?' selected':''}>${esc(x.name)}</option>`).join('')}</select></div>
        <div class="field" style="grid-column:1/-1"><label>过滤字段（选中后自动填参数ID/显示名）</label><div class="range"><select class="p-srcfield" data-cur="${esc(src.field||'')}" onchange="fldChanged(this)"><option value="">不绑定（不过滤）</option></select><button type="button" class="btn btn-secondary btn-sm" onclick="loadFields(this)">刷新字段</button></div></div>
      </div>
    </div>
    <div class="pr-sec p-optbox"><div class="pr-sec-t">手动下拉选项<span class="p-bindhint">逗号分隔；未绑定字段时使用</span></div><div class="field" style="flex:1"><input class="p-options" placeholder="如：待审批,已通过,已驳回" value="${esc(p.options||'')}"></div></div>
    <div class="pr-sec f-srcsql"><div class="pr-sec-t">候选值 SQL（可选，仅用于下拉/文本候选值，不参与过滤）</div><textarea class="p-srcsql" rows="2" style="width:100%%;font-family:var(--font-mono);font-size:12px" placeholder="SELECT DISTINCT 字段 FROM ... WHERE 其它字段 = '{{其它参数id}}'">${esc(src.sql||'')}</textarea></div>`;
  document.getElementById('plist').appendChild(d);
  d.querySelector('.p-id').addEventListener('input',function(){syncName(d);});
  d.querySelector('.p-label').addEventListener('input',function(){syncName(d);});
  syncRow(d);
  const sel=d.querySelector('.p-srcds');
  if(sel.value && !d.querySelector('.p-srcfield').getAttribute('data-loaded'))loadFields(sel);}
function collectParam(row){
  const p = {id:row.querySelector('.p-id').value.trim(), label:row.querySelector('.p-label').value,
    type:row.querySelector('.p-type').value, options:row.querySelector('.p-options').value,
    default:row.querySelector('.p-default').value};
  const rng = row.querySelector('.p-range');
  if(rng && (p.type==='date'||p.type==='number')){p.range = rng.checked;}
  const ds = row.querySelector('.p-srcds').value;
  const field = row.querySelector('.p-srcfield').value;
  const sql = (row.querySelector('.p-srcsql').value||'').trim();
  if(ds && (field || sql)){
    p.source = {mode:'field', ds:ds, field:field};
    if(sql)p.source.sql = sql;}
  return p;}
function collect(){return [...document.querySelectorAll('#plist .pr')].map(collectParam).filter(function(p){return p.id;});}
async function save(e){e.preventDefault();
  const all = [...document.querySelectorAll('#plist .pr')].map(collectParam);
  for(const p of all){
    if(!p.id){toast('存在未填参数ID的参数，请补全或删除','err');return;}
    if(p.type==='select' && !p.source && !(p.options||'').trim()){toast('参数「'+(p.label||p.id)+'」为下拉类型，请绑定过滤字段或填写手动选项','err');return;}}
  const res = await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:%(rid)s,name:document.getElementById('rname').value,
      cache_ttl:parseInt(document.getElementById('rcache').value)||0,
      max_rows:parseInt(document.getElementById('rmax').value)||0,
      export_format:document.getElementById('rexp').value,
      query_mode:document.getElementById('rquery').value,
      page_size:parseInt(document.getElementById('rpage').value)||20,
      params:collect(),datasets:collectDs(),merge:collectMerge()})});
  const j = await res.json();
  if(j.error){toast('失败:'+j.error,'err');return;}
  toast('已保存，URL: /r/'+j.id,'ok');
  location.href='/r/'+j.id;}
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
        self._send(page(title, body, script, "editor"))

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

    def _param_form(self, r, given):
        html = ""
        for p in r.get("params", []):
            pid, t = p["id"], p.get("type", "text")
            label = esc_html(p.get("label") or pid)
            dv = given.get(pid, p.get("default", ""))
            dv2 = given.get(pid + "_2", "")
            src = p.get("source")
            rng = p.get("range")
            if t == "select" and src:
                # 动态取值下拉：选项由 /param-options 加载（支持级联）
                html += (f'<div class="field"><label>{label}</label>'
                         f'<select name="{pid}" class="p-sel" data-pid="{esc_html(pid)}">'
                         f'<option value="">请选择</option></select></div>')
            elif t == "select":
                opts = "".join(f'<option{" selected" if o == dv else ""}>{esc_html(o)}</option>'
                               for o in (p.get("options") or "").replace("，", ",").split(",") if o)
                html += f'<div class="field"><label>{label}</label><select name="{pid}">{opts}</select></div>'
            elif t in ("date", "number") and rng:
                # 精确/范围双模式（提交 id_2 即范围）；隐藏侧输入必须 disabled，避免同名参数污染提交
                typ = "date" if t == "date" else "number"
                w = ' style="width:110px"' if t == "number" else ""
                is_rng = bool(dv2)
                dis_x = " disabled" if is_rng else ""  # 精确侧隐藏时禁用
                dis_r = "" if is_rng else " disabled"  # 范围侧隐藏时禁用
                html += (f'<div class="field p-rng" data-pid="{esc_html(pid)}">'
                         f'<label>{label}<span class="rng-seg">'
                         f'<a class="m-exact{" on" if not is_rng else ""}" onclick="rngMode(this,\'{pid}\',\'exact\')">精确</a>'
                         f'<a class="m-range{" on" if is_rng else ""}" onclick="rngMode(this,\'{pid}\',\'range\')">范围</a>'
                         f'</span></label>'
                         f'<div class="rng-exact"{(" style=display:none" if is_rng else "")}>'
                         f'<input type="{typ}" name="{pid}" value="{esc_html(dv)}"{w}{dis_x}></div>'
                         f'<div class="rng-range"{("" if is_rng else " style=display:none")}>'
                         f'<div class="range"><input type="{typ}" name="{pid}" value="{esc_html(dv)}"{w}{dis_r}><span>~</span>'
                         f'<input type="{typ}" name="{pid}_2" value="{esc_html(dv2)}"{w}{dis_r}></div></div></div>')
            elif t == "daterange":
                html += (f'<div class="field"><label>{label}</label><div class="range">'
                         f'<input type="date" name="{pid}" value="{esc_html(dv)}"><span>~</span>'
                         f'<input type="date" name="{pid}_2" value="{esc_html(dv2)}"></div></div>')
            elif t == "numrange":
                html += (f'<div class="field"><label>{label}</label><div class="range">'
                         f'<input type="number" name="{pid}" value="{esc_html(dv)}" style="width:100px"><span>~</span>'
                         f'<input type="number" name="{pid}_2" value="{esc_html(dv2)}" style="width:100px"></div></div>')
            elif t == "date":
                html += f'<div class="field"><label>{label}</label><input type="date" name="{pid}" value="{esc_html(dv)}"></div>'
            elif t == "number":
                html += f'<div class="field"><label>{label}</label><input type="number" name="{pid}" value="{esc_html(dv)}" style="width:120px"></div>'
            elif t == "text" and src:
                # 文本参数带取值来源：datalist 自动补全（候选值来自 /param-options）
                html += (f'<div class="field"><label>{label}</label>'
                         f'<input name="{pid}" value="{esc_html(dv)}" list="dl-{esc_html(pid)}" class="p-dl" data-pid="{esc_html(pid)}" placeholder="可输入或从候选选择">'
                         f'<datalist id="dl-{esc_html(pid)}"></datalist></div>')
            else:
                html += f'<div class="field"><label>{label}</label><input name="{pid}" value="{esc_html(dv)}"></div>'
        return html

    def _viewer(self, rid, args):
        path = os.path.join(REPORTS_DIR, rid + ".json")
        if not os.path.exists(path):
            return self._err(f"报表不存在: {rid}")
        r = load_json(path)
        name = esc_html(r.get("name", rid))
        form = self._param_form(r, args)
        fmt = r.get("export_format") or "xls"
        fmt_opts = "".join(f'<option value="{v}"{" selected" if v == fmt else ""}>{lbl}</option>'
                           for v, lbl in (("xls", "格式 .xls"), ("csv", "格式 .csv")))
        body = f"""<div class="crumb">报表列表 / <b>{name}</b></div>
        <div class="pagehead"><div><h1>{name}</h1><div class="sub">设置查询条件，查看或导出数据</div></div>
        <div style="display:flex;gap:8px"><a class="btn btn-secondary" href="/edit/{rid}">编辑</a>
        <a class="btn btn-primary" href="/new">＋ 新建报表</a></div></div>
        <form method="get" action="/r/{rid}" id="ff">
        <div class="card" style="padding:18px">
          <div class="parambar">{form}</div>
          <div class="actbar">
            <button type="submit" class="btn btn-primary">查询</button>
            <button type="button" class="btn btn-secondary" onclick="exp()">导出</button>
            <select id="fexp" title="导出格式">{fmt_opts}</select>
            <button type="button" class="btn btn-secondary" onclick="copyText(location.origin+'/r/{rid}')">复制独立 URL</button>
            <button type="button" class="btn btn-secondary" onclick="clr()">清空条件</button>
          </div>
        </div></form>
        <div class="card" style="margin-top:16px"><div id="out" style="min-height:120px">
        <div class="empty" style="padding:40px 20px"><div class="il">🔍</div><h4>设置条件后点「查询」</h4></div>
        </div></div>"""
        script = """
const OUT = document.getElementById('out');
tblInit(OUT);
const RID = %(ridq)s;
const DEF_PAGE = %(page)s;
async function loadOptions(){
  const fd=new FormData(document.getElementById('ff'));
  const values={};
  fd.forEach(function(v,k){if(v!=='')values[k]=v;});
  const res=await fetch('/param-options',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({rid:RID,values})});
  const j=await res.json();
  const opts=(j.options||{});
  document.querySelectorAll('.p-sel').forEach(function(sel){
    const pid=sel.getAttribute('data-pid'),cur=sel.value,list=opts[pid]||[];
    sel.innerHTML='<option value="">请选择</option>'+list.map(function(o){
      return '<option'+(o===cur?' selected':'')+'>'+escHtml(o)+'</option>';}).join('');
    if(cur&&list.indexOf(cur)<0)sel.value='';
  });
  document.querySelectorAll('.p-dl').forEach(function(inp){
    const pid=inp.getAttribute('data-pid'),list=opts[pid]||[];
    const dl=document.getElementById('dl-'+pid);
    if(dl)dl.innerHTML=list.map(function(o){return '<option value="'+escHtml(o)+'">';}).join('');
  });
}
async function run(){
  const btns=document.querySelectorAll('#ff button');
  btns.forEach(function(b){b.disabled=true;});
  OUT.innerHTML='<div class="statusline"><span class="spinner"></span> 查询中，请稍候…</div>';
  try{
    const res=await fetch('/q/'+RID,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams(new FormData(document.getElementById('ff')))});
    const j=await res.json();
    tblShow(OUT,j,DEF_PAGE);
  } finally { btns.forEach(function(b){b.disabled=false;}); }
}
function exp(){
  location='/r/'+RID+'/export?'+new URLSearchParams(new FormData(document.getElementById('ff')))+'&format='+document.getElementById('fexp').value;
}
function clr(){
  document.querySelectorAll('#ff input').forEach(function(i){i.value='';});
  document.querySelectorAll('#ff select').forEach(function(s){if(s.id!=='fexp')s.value='';});
  loadOptions();
}
function rngMode(el,pid,mode){
  const box=el.closest('.p-rng');
  const ex=box.querySelector('.rng-exact'),rg=box.querySelector('.rng-range');
  box.querySelector('.m-exact').classList.toggle('on',mode==='exact');
  box.querySelector('.m-range').classList.toggle('on',mode==='range');
  ex.style.display=mode==='exact'?'':'none';
  rg.style.display=mode==='range'?'':'none';
  (mode==='exact'?rg:ex).querySelectorAll('input').forEach(function(i){i.disabled=true;});
  (mode==='exact'?ex:rg).querySelectorAll('input').forEach(function(i){i.disabled=false;});
}
document.getElementById('ff').addEventListener('submit',function(e){e.preventDefault();run();});
document.getElementById('ff').addEventListener('change',function(e){
  if(e.target&&e.target.classList&&e.target.classList.contains('p-sel'))loadOptions();
});
loadOptions().then(function(){
  if(%(auto)s||%(hasargs)s)run();
});
""" % {"ridq": json.dumps(rid),
       "page": int(r.get("page_size") or 20),
       "auto": "true" if r.get("query_mode") == "auto" else "false",
       "hasargs": "true" if args else "false"}
        self._send(page(name, body, script, ""))

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
        ttl = int(r.get("cache_ttl") or 0)  # 0 = 不缓存（实时）
        key = CACHE.make_key(rid, values) if ttl > 0 else None
        if key:
            hit = CACHE.get(key)
            if hit:
                cols, rows, tr_hit, ct_hit = hit
                return {"columns": cols, "rows": rows, "coltypes": ct_hit, "truncated": tr_hit,
                        "cached": True, "elapsed_ms": int((time.time() - t0) * 1000)}
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
        return {"columns": cols, "rows": rows, "coltypes": coltypes, "truncated": truncated,
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
        cols, rows = result["columns"], result["rows"]
        fmt = args.get("format") or r.get("export_format") or "xls"
        if fmt == "csv":
            import csv, io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for row in rows:
                w.writerow(row)
            fn = urllib.parse.quote(f"{r['name']}.csv")
            self._send(buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                       f'attachment; filename*=UTF-8\'\'{fn}')
            return
        h = "".join(f"<th>{c}</th>" for c in cols)
        b = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
        xls = f'<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body><table border="1">{h}{b}</table></body></html>'
        fn = urllib.parse.quote(f"{r['name']}.xls")
        self._send(xls.encode("utf-8"), "application/vnd.ms-excel", f'attachment; filename*=UTF-8\'\'{fn}')

def main():
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8765))
    os.chdir(BASE)
    print(f"SQL报表服务 http://0.0.0.0:{port}  (报表目录: {REPORTS_DIR})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
