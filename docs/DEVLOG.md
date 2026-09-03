# 开发日志（DEVLOG）

按日期追加，只增不改。记录开发过程、踩坑、关键决策。纠错在后文补充新条目。

---

## 2026-09-01 ｜ 需求与选型

- 提出报表需求 4 条：多库 SQL 取数、中国式复杂报表、每报表独立查询条件、独立 URL 发布。
- 选型调研结论：成熟开源方案首推 JimuReport（免费可商用，4 条全满足）；商业方案 FineReport。
- 用户决定自研轻量工具，理由：现有工具授权/体积/复杂度限制。自研边界划定：
  - 做：贴 SQL → 参数条件 → 表格 → 独立 URL → Excel 导出
  - 不做：拖拽设计器、多级不规则表头、交叉表引擎、打印套打（个别需求用 Excel 导出兜底）
- 需求定版：报表仅简单表格+可选简单交叉；用户只读、不登录；导出 Excel、不打印。

## 2026-09-02 ｜ v0.1 开发

### 实现
- 单文件 `server.py` 实现：数据层（驱动懒加载）→ 参数层（校验/转义/替换）→ 路由层（6 端点）→ 视图层（内联 HTML）。
- 报表定义 = `reports/*.json`，一个文件一张报表，天然对应 `/r/<id>`；数据源 = `datasources.json`。
- 参数体系：6 种控件；日期范围/数字范围各展开两个占位符（`{{d.begin}}/{{d.end}}`、`{{n.min}}/{{n.max}}`，兼容下划线写法）。
- 可选条件实现：含未填占位符的整行丢弃 → SQL 条件独立成行即可选。
- Excel 导出：HTML-Excel（`application/vnd.ms-excel`）零依赖方案。

### 端到端验证
- 参数过滤（区域=华东 → 2 行）✅
- 可选条件跳过（区域留空 → 全量 7 行）✅
- 注入尝试（`x' OR '1'='1`）被转义拦截，返回 0 行而非报错 ✅
- 导出响应头（Content-Type / filename*）✅

### 踩坑记录（3 个，均已修复）
1. `parse_qs` 返回值是列表，未取 `[0]` 导致参数被替换成 `['华东']` 字符串 → POST 表单分支统一展平。
2. 占位符正则 `\w+` 不匹配 `{{d.begin}}`（含点号）→ 改为 `[\w.]+`。
3. POST 错误响应漏 `encode()`，字符串直写 wfile 报 TypeError → 统一在 `_send` 处理。

### 决策记录
- 参数值转义拼接而非 DB 参数绑定：工具面向可信 SQL 作者，参数值必须防注入即可；好处是同一 SQL 文本可直接在数据库客户端复用。
- 无登录：按需求用户只读；公网部署计划 P2 加报表级 token。
- SQLite 作为演示/元数据兜底：零依赖，标准库自带。

### 部署验证
- 本地沙盒 127.0.0.1 浏览器不可达（网络隔离），改走平台发布（`workbuddy_sites_deploy`），已上线。
- `server.py` 补 `PORT` 环境变量支持（平台注入端口），命令行参数保留。

## 2026-09-02 ｜ 文档与仓库化

- 产出 `PLAN.md`（架构总结 + 三期开发计划）、`README.md`（快速开始 + 文档/版本约定）、`CHANGELOG.md`（v0.1 定版）。
- 文档约定：CHANGELOG 面向使用者按版本记录；DEVLOG（本文件）面向开发者按日期追加；PLAN.md 完成项标 ✅ 注明版本。

## 2026-09-02 ｜ v0.2 开发（模块化 + 数据源管理 + 跨源合并 + 缓存）

### 实现（按 DESIGN-v0.2.md 任务分解，5 个独立 commit）
- **T01 模块拆分**：数据层/参数层迁出 server.py → `db.py`（DatasourceStore mtime 懒加载 + os.replace 原子写 / connect 含 timeout / run_query fetchmany 分批 10 万行硬顶 / sql_is_readonly / QueryCache）+ `params.py`（esc/build_values/substitute 原样迁移 + normalize_report 双格式归一化）。对外路由零变化。
- **T03 合并引擎**：merge_union 以第一个数据集列序为基准按列名对齐（右表多余列丢弃、缺失列填空）；merge_lookup 右表建 dict 哈希（≤10 万行，重复键取首条）。`_query/_export/_save` 改造为 datasets 编排，响应统一 `{columns, rows, truncated, cached, elapsed_ms}`。
- **T02 数据源管理后台**：`/datasources` 列表/新建/编辑/保存/测试/开关/删除；密码留空=不修改、列表页不渲染密码；删除被引用源需 force 二次确认；保护策略 = admin_password 空 → 仅 127.0.0.1，非空 → HTTP Basic（config.json 不入库）。
- **T04 编辑器数据集 UI**：数据集块增删 + 每数据集选源/写 SQL/试运行（`POST /preview` 前 20 行）；单数据集保存写回旧 `{ds, sql}` 格式。
- **T05 TTL 缓存**：QueryCache（Lock + OrderedDict，TTL 惰性过期无后台线程；单条目 ≤2 万行 / 全表 ≤10 万行 / 50 条目 LRU）；key = 报表id + sha1(sorted 参数 JSON)；`/save` 即 invalidate 该报表；查询页 loading 态 + 截断/缓存/耗时提示。

### 端到端验证（curl 实测，demo 数据源）
- 旧报表 `/q/orders` 回归 ✅；union 双数据集 5 行、lookup 订单×客户 left join（无匹配键填空）✅
- 缓存：同参数二查 `cached:true`；`/save` 后同参数 `cached:false`（失效生效）✅
- 截断：max_rows=3 → rows=3 + truncated:true ✅；导出响应头/文件名 UTF-8 ✅
- 数据源 CRUD：建源→测试(ms)→列表→改类型→禁用→引用检查拒绝删除→force 删除 ✅
- 密码留空保留 ✅；单数据集保存写回旧格式 ✅；HTTP Basic 401/200 ✅
- `python3 -m py_compile` 三文件 + `import db, params` 通过 ✅

### 踩坑记录（4 个，均已修复）
1. **后台进程随命令结束被回收**：`(python3 server.py &)` 双括号后台在沙盒下命令结束即被杀，curl 得到 "upstream connect refused"；改用宿主后台任务方式长驻。
2. **缓存命中丢失 truncated 标志**：初版缓存条目只存 (columns, rows)，命中后把已截断结果误报未截断 → 条目扩为含 truncated 四元组（对 DESIGN §3.4 的小幅补充）。
3. **`_save` 漏落盘 `cache_ttl`**：编辑器 JS 传了该字段但保存端未写入 rec，导致配置的缓存秒数静默丢失 → 保存端补齐。
4. **union 冒烟用例预期写错**：误以为右表多余列会保留填空；实现按设计「以第一个数据集列序为基准」丢弃右表多余列——修用例而非代码。

### 决策记录
- **lookup 提为 P0**：与 union 同批实现（用户拍板），合并引擎一次性落地，T04 仅留编辑器 UI 与预览。
- **不做 SQL 层分页**（用户拍板）：百万级明细靠 fetch 硬顶 + TTL 缓存 + max_rows 截断兜底。
- **管理页保护**：不设口令默认仅本机；HTTP Basic 代码路径保留，填 `admin_password` 即启用。
- **缓存不做后台清理线程**：惰性过期 + 写入时 LRU 驱逐，维持零线程形态。
- demo.db 追加 `customers` 表与 `orders.cust_id` 列（幂等迁移），支撑 lookup 演示报表。

## 2026-09-02 ｜ v0.2 QA 回归与 Bug#1 修复

- QA 独立回归（严过关）：9 项全 PASS（旧报表回归/union/lookup/缓存/截断/数据源管理/异常/并发20/py_compile）。
- 发现 Bug#1：/q 与 /preview 的 JSON Content-Type 提交参数被截断——_body() 对 JSON 返回 {k: v[0]} 结构，_flat() 再取 v[0]，字符串被截成单字符；且同首字符导致缓存 key 撞车（"换参数仍 cached=true"假象）。
- 修复（commit 1f7518c，最小改动）：_body() 按 Content-Type 统一返回标量字典（JSON 分支原样返回嵌套数组）；_flat() 收敛为 /q 专用幂等展平（仅对残留列表取首元素）。
- QA 复测：15/15 PASS 闭环（JSON/form 结果一致、缓存 key 归一、/preview、/save 嵌套数组透传、只读校验、数据源保护 403）。
- 排除 3 项疑似问题（均测试脚本问题）：JSON 参数形态须用生效参数 pid/pid_2；单数据集保存写回旧格式为设计约定；union 空参数被只读校验拒为既有可选条件语义。

---

> 以下为 2026-09-04 补记：DEVLOG 之前停在 v0.2，现依据 git 历史（commit 5f2e979 → 1d13d5c）与 CHANGELOG 还原 v0.3 → v0.9 条目。只增不改，既有内容不动。

## 2026-09-02 ｜ v0.3 工程化（src 布局）

### 实现
- **src 布局**：源码迁入 `src/sqlreport/`，新增 `pyproject.toml`（setuptools src 布局，`pip install -e .`）；三种运行方式：`python server.py` / `python -m sqlreport` / `sqlreport` 命令行入口；根级 `server.py` / `db.py` / `params.py` 变为 `sys.modules` 转发 shim，旧导入零影响。
- **测试纳入版本控制**：`tests/`（params/db 单测 + server 集成，共 86 例），导入兼容双入口（`sys.path` 插 `../src`，根级 shim 兜底）。
- **运维与样例归档**：`scripts/start.bat`（探测 python/py/python3、端口检查、`start /min` 后台、日志 `logs\sqlreport.log`）+ `scripts/stop.bat`；样例配置归档 `examples/datasources.example.json`，根级副本移除。
- **开发准则**：新增 `docs/DEVELOPMENT.md`（目录/分层/流程/测试/发布约定）与 `.trae/rules/project_rules.md`；架构图迁 `docs/diagrams/`。
- **CI 升级**：`pip install -e .` + 编译检查 + `import sqlreport.*` 冒烟。

### 修复
- **查询结果取消千分位格式化（第一次）**：数字/ID 列不再 `toLocaleString('zh-CN')`（如 `-7341879067155610000` 原样输出，避免大整数精度丢失）。

### 决策记录
- 源码唯一可信位置定为 `src/sqlreport/`，根级只做转发、不堆业务逻辑（DEVELOPMENT.md §1 原则）。

## 2026-09-03 ｜ v0.4 统计基础（分析层）

### 实现（按 docs/PLAN-v0.4-v0.7.md 决策表 D1-D10 / 22 Task 先行规划，commit 24295d4）
- **分析层骨架** `analytics.py`（纯函数，与 params.py 同风格）：`total_row` 合计行 → `summary_metrics` KPI 摘要（sum/avg/count/max/min）→ `top_n_rows` Top N + 其他归并 → `add_share_columns` 占比/累计占比列 → `bucket_column` 时间分桶（月/季/周）。
- **分析管道**（决策 D5，顺序固定）：bucket → top_n → share → total/summary；`/q` 集成管道并扩展 `/save` 白名单（total/summary/top_n/share/bucket 可选键，缺省零迁移）；缓存命中路径同样重算（口径 = 所见 = 导出）。
- **查看页增强**：KPI 摘要卡片区、表格合计行（`<tfoot>`）、数值列右对齐 + 千分位（仅 num 列）、参数口径回显、点列头排序（num 数值 / str 拼音分型，▲/▼ 指示）。
- **导出同步**：xls/csv 附带合计行与 KPI 摘要文本，数值列 `mso-number-format` 真数值。

### 决策记录
- 分析函数一律纯函数、可独立单测；管道顺序写死 D5，避免各版本口径漂移。

## 2026-09-03 ｜ v0.5 交叉分析（透视表 + 块化 + 视图拆分）

### 实现
- **透视表** `analytics.pivot`：row × col 交叉，agg 支持 sum/count/avg/max/min，小计列「合计」+ 总计行「总计」（avg 按底层原始值重算）；行/列维度去重后 > 50 拒绝执行（提示 SQL 层先归类）；None 与空串归并；单维汇总锁定时 col 缺省注入常量维度列。
- **分析块化 Schema**：报表顶层可选 `blocks`（table / pivot），无 blocks → 单 table 块（零迁移）；`normalize_blocks` 保存期校验类型白名单与 pivot 必填键。
- **多块报表**：`/q` 响应固定新增 `blocks`（块级 `{type,title,columns,rows,coltypes}`），查看页逐块渲染，导出 xls 按块输出（csv 仍仅主表）；编辑器新增「分析块（JSON）」配置区。
- **视图层拆分**：页面模板迁至 `views_report.py`，server.py 保留薄转发，路由与业务方法不动。

### 决策记录
- 含非 table 块（pivot/hist）的报表不使用结果缓存（cache_ttl 视为 0）：缓存仅存主表行，命中路径拿不到各 dataset 原始结果（决策 D2 / Task 11 Step 3.5）。

## 2026-09-03 ｜ v0.6 对比与维度

### 实现
- **对比差值** `analytics.diff_merge`：顶层可选 `compare`（`dataset` 第二数据集、`on` 关联键、`metric` 指标列、`label` 对比期标签），主表追加「差值」「增长率%」两列；右侧缺失留空、r=0 增长率留空、行序保持主表首现序；与非 table 块同享缓存豁免。
- **数值分箱** `analytics.bin_numeric`：等宽分箱（hi==lo 退化单区间、左闭右开末箱右闭），输出 `[区间, 计数, 占比%]`，以 `hist` 块类型接入。
- **保存视图** `views`：`[{name, params}]` 参数组合快捷链接（URL 即状态，无后端存储）；`/save` 白名单校验 name 非空、params 为字典。
- **文档同步**（commit 93784ff）：CHANGELOG 统一 + PLAN 里程碑状态与实施计划索引。

## 2026-09-03 ｜ v0.7 交付加固（Task 19-22 全部落地）

### 实现
- **真 .xlsx 导出**（P2-3 首选方案，未降级 openpyxl）：手写最小 xlsx 写出器（zip + inlineStr，零新依赖）；按 blocks 多 sheet（`summary` → 「摘要」sheet）；num 列 `_to_num` 真数值单元格；sheet 名清洗（非法字符/31 字符截断/重名 `-2` 后缀）；`export_format` 白名单接纳 `xlsx`（默认仍 `xls`，存量零变化）。
- **打印样式**：PAGE 全局 `@media print`（隐藏导航/表单/按钮/分页条，表格全量铺开 + 网格线，KPI 卡 `break-inside:avoid`）。
- **token 鉴权**（P2-2）：`config.json {"auth": "token", "token_secret": "…"}`；`/r` `/q` `/export` 三类出口需 `?t=HMAC(secret, rid+YYYYMMDD)[:16]`（按日轮换、基于 rid、`compare_digest` 防时序）；token 模式管理面纳入 `_check_admin`；缺 secret 时 fail-closed；查看页展示带 token 分享链接，查询/导出自动透传。
- **报表分组目录**（P2-5）：`reports/<分组>/<id>.json` → `/r/<分组>/<id>`（`/q` `/export` `/edit` 两形态路由）；路径 `unquote` 支持 CJK；末段 `export`/`edit` 恒为动作保留字（分组名与 ID 禁用 + 路径穿越校验）；编辑器「分组」输入框；列表页按分组折叠；`db.referenced_by` 改递归防误删；手写 JSON 缺 `params` 键自动补空数组。

### 决策记录
- `auth=off` 缺省与无分组用法行为与 v0.6 完全一致；分组报表 token 基于 `{group}/{id}` 全串，与根目录报表互不通用。

## 2026-09-03 ｜ v0.8 编辑器与预览体验

### 实现
- **参数抽屉化**：查询参数区从全量平铺改为紧凑列表（类型标签 + 名称 + 参数ID + 配置/上移/下移/删除），右侧滑出抽屉集中配置（基本信息/数据绑定/手动选项/候选值 SQL，按类型动态显隐）；JS 模型数组为唯一数据源（输入实时提交）；数据集增删时已开面板自动刷新绑定选项；保存/试运行/字段枚举口径与旧版一致。
- **只读预览页**：列表页每报表新增「预览」入口，新窗口打开只读预览（无导航/编辑/新建/删除/管理入口）；轻量路由 `/pv/{id}`，与 `/r/{id}` 同套访问控制（token 模式需有效 `?t=`），复用查询/导出链路。
- **驱动懒加载**：`db._require()` 缺驱动时抛中文可操作错误（提示安装命令）；`scripts/start.bat` 启动前检测 pymysql/pyodbc，缺失引导 Y/N 自动安装并二次校验；服务进程优先 `pythonw` 无窗口，找不到回退最小化窗口。
- **SQL 容错**：`db.strip_semicolon()` 去语句尾分号（DB 客户端粘贴常带 `;`，避免只读校验误判多语句）。
- **测试**：`tests/test_db.py` +19（驱动缺失/尾分号用例），`tests/test_server.py` +149（抽屉/预览路由用例）。

### 修复（回归）
- **查询结果取消千分位（第二次，彻底）**：`cellFmt` 与 KPI 卡片移除 `toLocaleString`——v0.3 曾修复、v0.4 数值列格式化时误回退；本次数字列保留右对齐 + `tabular-nums`，全部列按数据库返回值原样展示。

### 决策记录
- 格式化只做对齐不做改值：展示层不得改变数据原样，千分位如需由用户在 Excel 端处理。

## 2026-09-03 ｜ v0.9 版本基线

- `0.8.0` → `0.9.0`（`src/sqlreport/__init__.py` 与 `pyproject.toml` 同步），不引入新功能，为后续开发提供独立版本基线；对外路由与报表 JSON 格式完全一致，旧报表零迁移。

## 2026-09-04 ｜ v0.9.1 导航与报表浏览重构

### 实现
- **列表页收敛**：报表列表（`/`）只保留未分组报表主表，移除下方各分组折叠块（`groups_html`）；分组报表统一由浏览报表页展示；右上角「＋新建报表」保留。
- **导航栏调整**：`报表列表 / 浏览报表 / 数据源管理`，取消「新建报表」独立导航项（`/new` 路由保留，作列表页按钮入口）。
- **浏览报表（`/browse`）**：新增 `render_browse()` 只读目录——未分组归入「默认分组」，各分组一块，每报表仅「打开」按钮（`target="_blank"` 新窗口进 `/r/<id>`）；默认分组无报表时整块隐藏；无任何新建/编辑/删除入口。
- **查看页按钮化**：`/r/<id>` 右上角新增【返回】（回列表）与【编辑】（跳 `/edit/<id>`），替换原面包屑。
- **编辑器保存拆分**：原「保存并生成URL」拆为【保存】（仅保存，原地不动；新建保存后 URL 原地变 `/edit/<id>` 进入编辑态）与【保存并预览】（保存后新窗口打开查看页）。
- **测试**：更新 `test_list_groups_collapsed` → `test_list_hides_grouped_and_browse_shows`（覆盖列表无分组项/浏览页分组展示/全分组时默认分组隐藏/浏览页无编辑入口），新增 `test_viewer_nav_buttons`（查看页返回/编辑按钮），458 用例全绿。

### 决策记录
- 浏览页「打开」直达 `/r/<id>` 而非 `/pv/<id>`：浏览定位是日常使用入口，完整查看页的导出/清空条件是高频操作；`/pv` 仍保留给列表页快速预览。
- `/pv` lite 页不随查看页加返回/编辑按钮：预览页定位纯只读，与浏览页「打开」的完整查看页区分。

### 踩坑记录
- f-string 表达式内不能含反斜杠（Python 3.10 语法限制）：`rows.count("<div class=\"card\">")` 直接 SyntaxError，改为无转义的短前缀 `rows.count("<div class=")` 计数。
