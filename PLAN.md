# SQL 报表工具 — 功能总结与开发计划

当前版本：v0.6.0（2026-09-03）｜技术栈：Python 3 标准库（http.server / sqlite3），零新依赖

> **实施计划**：v0.4 → v0.7 的详细实施（决策表 D1-D10、22 个 Task、TDD 步骤与完整代码）见
> [`docs/PLAN-v0.4-v0.7.md`](docs/PLAN-v0.4-v0.7.md)。
> 里程碑进度：✅ v0.4 统计基础 → ✅ v0.5 交叉分析 → ✅ v0.6 对比与维度 → ⏳ v0.7 交付加固（xlsx/token/分组）进行中。
> 开发日志见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 一、当前架构

```
sqlreport/
├── server.py            # 路由 + 业务编排（视图层已拆分到 views_report.py）
│   ├── 管理保护：_check_admin()（admin_password 空=仅本机 / 非空=HTTP Basic）
│   ├── 数据源管理：/datasources 列表/表单/保存/测试/开关/删除
│   └── 报表编排：_execute_report()（归一化→替换→缓存→各数据集查询→合并→截断→分析管道）
├── views_report.py      # 视图层（自 v0.5 拆分）：PAGE/nav/page/render_list/render_editor/render_viewer/param_form
├── analytics.py         # 分析层（自 v0.4，纯函数）：total_row/summary/top_n/share/bucket/pivot/diff_merge/bin_numeric
├── db.py                # 数据层：DatasourceStore（mtime 懒加载+原子写）/ connect(超时)
│   │                    #           run_query(fetchmany 分批+10万行硬顶) / sql_is_readonly
│   │                    #           merge_union / merge_lookup / QueryCache(TTL+LRU)
├── params.py            # 参数层：esc/build_values/substitute + normalize_report + normalize_blocks
├── config.json          # 全局配置（gitignore）：admin_password 空=管理页仅本机
├── datasources.json     # 数据源配置（"_"前缀或 enabled:false=禁用；密码不入库）
├── reports/*.json       # 报表定义，一文件一报表（天然对应独立 URL）
└── demo.db              # 演示数据（SQLite，orders + customers）
```

> 注：根目录 `server.py` / `db.py` / `params.py` / `analytics.py` 为 `sys.modules` 转发 shim（src 布局），
> 真源在 `src/sqlreport/`。

### 路由清单

| 路由 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 报表列表（含独立URL与编辑入口） |
| `/new` `/edit/{id}` | GET | 报表编辑器（多数据集增删 + 参数控件动态增删 + 缓存秒数） |
| `/r/{id}` | GET | 报表访问页：参数表单 + 异步查询（loading/缓存/截断提示）+ 导出按钮 |
| `/r/{id}/export` | GET | Excel 导出（HTML-Excel 方案，.xls，对合并后最终结果生效） |
| `/q/{id}` | POST | 查询接口（响应 `{columns, rows, coltypes, truncated, cached, elapsed_ms, total_row, summary, blocks}`，v0.5 起含分析块） |
| `/save` | POST | 保存报表定义（试编译校验 + 原子写 + 清该报表缓存） |
| `/preview` | POST | 编辑器单数据集试运行（前 20 行） |
| `/datasources` `/datasources/new` `/datasources/edit/{name}` | GET | 数据源管理页（仅本机/Basic） |
| `/datasources/save` `/test` `/toggle` `/delete` | POST | 数据源保存/连接测试/启用禁用/删除（引用检查二次确认） |

### 已实现能力

1. ✅ 多数据源连接：MySQL / SQLServer / SQLite（超时可控），Web 界面管理免重启 ✅ v0.2
2. ✅ 表格报表：查询结果表格渲染（含中文列别名）；多级表头未做
3. ✅ 每报表独立查询条件：6 种参数控件
4. ✅ 独立 URL 发布：`/r/<id>` 免登录直接访问
5. ✅ 跨源联合查询：多数据集 union（列名对齐）/ lookup（哈希 left join）✅ v0.2
6. ✅ 大数据量稳定：fetch 10 万行硬顶 + timeout + 只读校验 + max_rows 截断提示 ✅ v0.2
7. ✅ TTL 结果缓存（报表级 cache_ttl，保存即失效）+ 查询 loading 态 ✅ v0.2
8. ✅ 统计与分析层 `analytics.py`（纯函数）：合计行 / KPI 摘要 / Top N+其他归并 / 占比·累计占比列 / 时间分桶（月季周）✅ v0.4
9. ✅ 查看页增强：KPI 卡、合计行、数值列格式化、参数口径回显、点列头排序；导出同步合计/摘要/数值真格式 ✅ v0.4
10. ✅ 透视表 `pivot`（小计/总计/聚合）+ 分析块化 `blocks` Schema + 多块渲染/导出 + 编辑器配置 ✅ v0.5
11. ✅ 视图层拆分 `views_report.py`（server.py 瘦身，路由不变）✅ v0.5
12. ✅ 对比差值 `compare`（双数据集，差值+增长率%）+ 数值分箱 `hist` 块 ✅ v0.6
13. ✅ 保存视图 `views`（参数组合快捷链接，URL 即状态）✅ v0.6

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

**P1-1 合计行** ✅ v0.4（`total` 键，含 KPI 摘要 `summary`、Top N `top_n`、占比 `share`、分桶 `bucket`）
- 报表 JSON：`"total": {"label_col": 0, "label": "合计"}`（列维度在分析管道实现，见 docs/PLAN-v0.4-v0.7.md）
- 实现：查询接口内对数值列 sum，追加一行「合计」；导出同步 ✅

**P1-2 简单交叉表（pivot）** ✅ v0.5（以 blocks 形式实现：`{"type":"pivot", ...}`）
- 报表 JSON：`"blocks": [{"type": "pivot", "row": "区域", "col": "产品", "value": "金额", "agg": "sum", "row_total": true, "col_total": true}]`
- 实现：SQL 只出 row/col/value 三列明细，Python dict 透视，列头按首现序；agg 支持 sum/count/avg/max/min
- 边界：行/列维度去重后 >50 时拒绝执行，提示在 SQL 层先归类 ✅

**P1-3 排序 + P1-4 分页**
- 排序 ✅ v0.4：纯前端，点列头对已加载数据排序（num 数值 / str 拼音分型，≤1 万行场景够用）
- 分页 ✅ v0.2：报表 JSON `"max_rows"`（默认 2000）超限截断并提示；SQL 层翻页（OFFSET/FETCH / LIMIT）视真实痛点再上（用户已拍板本轮不做）

### P2 — 交付加固（~3 天，对外交付前必做）

**P2-1 查询超时与只读保护** ✅ v0.2（T01 落地）
- 每数据源配置 `"timeout": 30`（秒）：pymysql `connect_timeout`+`read_timeout`，pyodbc `cnxn.timeout`，sqlite 本地锁等待 5s
- SQL 拦截：去注释后首词必须为 SELECT/WITH；含 `;` 多语句直接拒绝；与只读账号形成双保险
- 查询行数硬上限：单数据集 fetch 10 万行（fetchmany 分批）

**P2-2 报表 token 鉴权（可选开关）** ⏳ v0.7 实施中（docs/PLAN-v0.4-v0.7.md Task 21）
- `config.json`：`{"auth": "off|token", "token_secret": "..."}`
- token 模式：访问 `/r/<id>` / `/q/<id>` / `/r/<id>/export` 需 `?t=HMAC(secret, rid+date)`；管理面（/edit、/save、/delete_report、/preview）在 token 模式下纳入 `_check_admin`；`off` 行为同现在（内网直用）

**P2-3 真 .xlsx 导出** ⏳ v0.7 实施中（docs/PLAN-v0.4-v0.7.md Task 19）
- 首选：手写最小 xlsx（zip + inlineStr，数值 `t="n"`），保持零依赖；按 blocks 构造多 sheet（含摘要 sheet）
- 若超预算未通过兼容验证，降级引 openpyxl，DEVLOG 记录决策

**P2-4 部署**
- 环境A 客户 Linux：systemd unit（`ExecStart=python3 /opt/sqlreport/server.py`）
- 环境B NAS Docker：python:3.13-slim 基础镜像，volume 挂载 `reports/` + `datasources.json`；出 base 与 mssql（含 ODBC 驱动层）两个 tag
- README 补部署章节与 systemd unit / Dockerfile 样例

**P2-5 报表分组目录** ⏳ v0.7 实施中（docs/PLAN-v0.4-v0.7.md Task 22）
- `reports/<分组>/<id>.json`，URL `/r/<分组>/<id>`；列表页按分组折叠；兼容根目录报表；路由 path 需 unquote 支持 CJK 分组；`db.referenced_by` 改递归防误删

### P3 — 候选池（有明确需求才启动，不排期）
下拉选项来自 SQL（options_sql）｜导出多 Sheet｜定时快照对比｜查询审计日志｜简单图表（内联 ECharts）

---

## 三、风险与对策

| 风险 | 对策 |
|------|------|
| pyodbc 客户环境安装失败（ODBC 依赖） | P0 最先验证；备选 pymssql 或容器内置 ODBC 驱动 |
| 整行丢弃规则被误用（条件跨多行） | 编辑器占位符说明 + 保存时对可疑写法警告 |
| 大结果集拖垮服务 | max_rows + timeout + 只读账号三重防护 |
| datasources.json 含密码入库 | ✅ v0.2：config.json 已 gitignore；管理页密码不回显、留空不修改 |
| 单文件 server.py 膨胀 | ✅ v0.5：视图层已拆 `views_report.py`，路由与业务方法不变，对外零影响 |

## 四、节奏

✅ v0.4 统计基础 → ✅ v0.5 交叉分析 → ✅ v0.6 对比与维度 → ⏳ v0.7 交付加固（xlsx / token / 分组，见 docs/PLAN-v0.4-v0.7.md Task 19-22）→ v1.0 对外交付（P2 完成 + CHANGELOG 定版）。
