# SQL 报表工具 — 功能总结与开发计划

版本：v0.1（2026-09-02）｜技术栈：Python 3 标准库（http.server / sqlite3），无框架依赖

---

## 一、当前架构

```
sqlreport/
├── server.py            # 全部后端逻辑（单文件 ~330 行）
│   ├── 数据层：connect() 懒加载数据源驱动（sqlite3 标准库 / pymysql / pyodbc）
│   ├── 参数层：build_values() 校验参数值 → esc() 转义 → substitute() 占位符替换
│   │          规则：{{id}} 普通参数；{{id.begin}}/{{id.end}} 日期范围；{{id.min}}/{{id.max}} 数字范围
│   │          未填的占位符所在整行丢弃（实现可选 WHERE 条件）
│   ├── HTTP层：BaseHTTPRequestHandler 路由分发
│   └── 视图层：PAGE 模板字符串内联 HTML/CSS/JS（无前端框架）
├── datasources.json     # 数据源配置（"_"前缀条目隐藏，仅作连接模板）
├── reports/*.json       # 报表定义，一文件一报表（天然对应独立 URL）
└── demo.db              # 演示数据（SQLite）
```

### 路由清单

| 路由 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 报表列表（含独立URL与编辑入口） |
| `/new` `/edit/{id}` | GET | 报表编辑器（SQL+参数控件动态增删） |
| `/r/{id}` | GET | 报表访问页：参数表单 + 异步查询 + 导出按钮 |
| `/r/{id}/export` | GET | Excel 导出（HTML-Excel 方案，.xls） |
| `/q/{id}` | POST | 查询接口（表单参数 → JSON 列/行） |
| `/save` | POST | 保存报表定义（保存前试编译校验占位符） |

### 已实现能力（对应最初 4 条需求）

1. ✅ 多数据源连接：MySQL / SQLServer / SQLite，SQL 脚本直接取数
2. ✅ 表格报表：查询结果表格渲染（含中文列别名）；多级表头/交叉表未做
3. ✅ 每报表独立查询条件：文本/下拉/日期/日期范围/数字/数字范围 6 种控件
4. ✅ 独立 URL 发布：`/r/<id>` 免登录直接访问

### 设计决策记录

- **报表=JSON 文件**：无数据库依赖，复制即备份/迁移，git 可版本管理
- **参数值转义拼接**（非 DB 参数绑定）：工具面向可信 SQL 作者，参数值一律 `''` 转义，已验证注入被拦截
- **Excel 用 HTML-.xls 方案**：零依赖；限制是 Excel 打开时可能提示格式确认，且不支持多 Sheet/样式
- **无登录**：按需求用户只读；公网部署时靠 URL 不公开 + 反向代理层加鉴权兜底
- **可选条件 = 整行丢弃**：SQL 中含未填占位符的行直接移除，写法简单但要求条件独立成行

---

## 二、开发方案（详细设计）

### P0 — 数据接入补全（1.5 天，客户环境可用前提）

**P0-1 真库连通测试**
- MySQL：pymysql 连接参数 charset=utf8mb4；验证 DATE/DATETIME/DECIMAL 返回值在表格与导出中的显示
- SQLServer：pyodbc；Linux 部署机用 Microsoft 官方 ODBC Driver 18（`msodbcsql18`），安装步骤写入 README；Windows 部署机用自带 ODBC Driver 17
- 验收标准：客户真实库跑通 1 张带日期范围+下拉条件的报表，导出 Excel 中文列名正常

**P0-2 列类型保留**
- 现状：所有值 `str()` 转字符串。改造：`run_query` 返回原始类型 + 列类型标记（`cursor.description` type_code 映射为 num/date/str）
- 前端：数值列右对齐 + 千分位（`toLocaleString`），日期列原样
- 导出：HTML 单元格加 `mso-number-format`，Excel 中为真数值
- 报表 JSON 增加可选 `columns: [{"name":"金额","type":"num","format":"#,##0.00"}]` 覆盖自动探测

**P0-3 配置安全**
- `datasources.json` 改为 `datasources.example.json`（占位密码）入库，真实配置 gitignore
- `.gitignore` 增补 `config.json`（P2-2 的密钥文件同步排除）

### P1 — 报表体验（~3 天，用户侧增强）

**P1-1 合计行**
- 报表 JSON：`"total": {"label_col": "区域", "cols": ["金额"], "position": "bottom"}`
- 实现：查询接口内对数值列 sum，追加一行「合计」；导出同步

**P1-2 简单交叉表（pivot）**
- 报表 JSON：`"pivot": {"row": "区域", "col": "产品", "value": "金额", "agg": "sum", "row_total": true, "col_total": true}`
- 实现：SQL 只出 row/col/value 三列明细，Python dict 透视，列头按值排序；agg 支持 sum/count/avg
- 边界：列头去重后 >50 列时拒绝执行，提示在 SQL 层先归类

**P1-3 排序 + P1-4 分页**
- 排序：纯前端，点列头对已加载数据排序（≤1 万行场景够用）
- 分页：报表 JSON `"max_rows": 5000`（默认 2000），超限截断并提示「结果超 N 行已截断，请缩小条件」；SQLServer 用 OFFSET/FETCH，MySQL/SQLite 用 LIMIT

### P2 — 交付加固（~3 天，对外交付前必做）

**P2-1 查询超时与只读保护**
- 每数据源配置 `"timeout": 30`（秒）：pymysql `connect_timeout`+`read_timeout`，pyodbc `cnxn.timeout`
- SQL 拦截：去注释后首词必须为 SELECT/WITH；含 `;` 多语句直接拒绝；与只读账号形成双保险
- 查询行数硬上限（防 SELECT * 大表拖垮服务）

**P2-2 报表 token 鉴权（可选开关）**
- `config.json`：`{"auth": "off|token", "token_secret": "..."}`
- token 模式：访问 `/r/<id>` 需 `?t=HMAC(secret, id+date)`；编辑器生成带 token 的分享链接；`off` 行为同现在（内网直用）

**P2-3 真 .xlsx 导出**
- 首选：手写最小 xlsx（zip + Sheet1.xml，字符串 inlineStr，数值 `t="n"`），保持零依赖
- 若超 1 天未通过兼容验证，降级引 openpyxl，DEVLOG 记录决策

**P2-4 部署**
- 环境A 客户 Linux：systemd unit（`ExecStart=python3 /opt/sqlreport/server.py`）
- 环境B NAS Docker：python:3.13-slim 基础镜像，volume 挂载 `reports/` + `datasources.json`；出 base 与 mssql（含 ODBC 驱动层）两个 tag
- README 补部署章节与 systemd unit / Dockerfile 样例

**P2-5 报表分组目录**
- `reports/<分组>/<id>.json`，URL `/r/<分组>/<id>`；列表页按分组折叠；兼容根目录报表

### P3 — 候选池（有明确需求才启动，不排期）
下拉选项来自 SQL（options_sql）｜导出多 Sheet｜定时快照对比｜查询审计日志｜简单图表（内联 ECharts）

---

## 三、风险与对策

| 风险 | 对策 |
|------|------|
| pyodbc 客户环境安装失败（ODBC 依赖） | P0 最先验证；备选 pymssql 或容器内置 ODBC 驱动 |
| 整行丢弃规则被误用（条件跨多行） | 编辑器占位符说明 + 保存时对可疑写法警告 |
| 大结果集拖垮服务 | max_rows + timeout + 只读账号三重防护 |
| datasources.json 含密码入库 | P0-3 拆 example/真实文件；config.json 一并 gitignore |
| 单文件 server.py 膨胀 | 超 800 行拆 db.py/params.py/views.py，路由不变，对外零影响 |

## 四、节奏

P0（1.5 天）→ 客户真实库出 1~2 张报表验证 → P1/P2 按反馈挑做 → v1.0 对外交付（P2 完成 + CHANGELOG 定版）。
