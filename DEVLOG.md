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
