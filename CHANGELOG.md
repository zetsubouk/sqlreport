# 变更记录（CHANGELOG）

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化。
最新变更置顶。

## [v0.2] - 待发布

### 新增
- **数据源管理后台** `/datasources`：Web 界面新建/编辑/删除数据源、连接测试（返回耗时 ms）、启用/禁用、被引用报表检查（删除二次确认）；改动免重启生效（mtime 懒加载 + 原子写）
- **跨数据源联合查询**：报表支持多数据集 `datasets`，各数据集独立选库写 SQL；纵向合并 union（按列名对齐）与横向关联 lookup（dict 哈希 left join，右表上限 10 万行）
- **编辑器数据集 UI**：数据集块增删、每数据集选源写 SQL、单数据集试运行前 20 行（`POST /preview`）
- **查询稳定性**：每数据源 `timeout` 超时、fetchmany 分批 + 单数据集 10 万行硬顶、只读 SQL 校验（仅单条 SELECT/WITH）
- **TTL 结果缓存**：报表级 `cache_ttl`（0=实时），key=报表id+参数哈希，单条目 2 万行/全表 10 万行/50 条目 LRU 上限；保存报表即清空该报表缓存
- **查询体验**：查询页 loading 态（按钮禁用防重复提交）、行数/耗时/缓存命中/截断提示；查询响应统一 `{columns, rows, truncated, cached, elapsed_ms}`
- 演示报表：`union_demo`（双数据集 union）、`lookup_demo`（订单关联客户）、`trunc_demo`（截断提示）

### 兼容性
- 旧报表 `{ds, sql}` 格式零迁移可用；单数据集保存时仍写回旧格式（git diff 稳定）
- 旧 `_` 前缀数据源隐藏语义保留（与 `enabled:false` 等价）
- 对外既有路由签名不变；管理页默认仅本机 127.0.0.1 可访问（`config.json` 的 `admin_password` 非空时改走 HTTP Basic）

### 计划中（见 PLAN.md）
- P0：MySQL/SQLServer 真库连通测试、SQLServer ODBC 部署文档、数值列类型保留
- P1：合计行、简单交叉表（pivot 配置）、点列头排序、SQL 层分页翻页
- P2：真 .xlsx 导出、报表 token 鉴权、systemd/Docker 化、报表分组目录

## [v0.1] - 2026-09-02

首个可运行版本。

### 新增
- 多数据源连接：MySQL（pymysql）/ SQLServer（pyodbc）/ SQLite（标准库），驱动懒加载，连接配置于 `datasources.json`
- 报表编辑器 `/new`、`/edit/{id}`：SQL 贴入 + 参数控件动态增删（文本/下拉/日期/日期范围/数字/数字范围 6 种），保存前试编译校验占位符
- 报表访问页 `/r/{id}`：参数表单 + 异步查询 + 独立 URL 免登录访问
- 查询接口 `POST /q/{id}`：占位符替换（未填参数所在整行自动丢弃，实现可选条件）
- Excel 导出 `/r/{id}/export`：HTML-Excel 方案输出 .xls，零依赖
- 报表列表页 `/`：入口 + 独立 URL 展示
- 参数值转义防注入（`''` 转义，已验证拦截典型注入）
- 端口支持 `PORT` 环境变量（平台部署）或命令行参数
- 演示数据 `demo.db` 与示例报表 `reports/orders.json`

### 已知限制
- 导出 .xls 为 HTML 方案：Excel 打开可能有格式提示，不支持多 Sheet/单元格样式
- 所有查询结果按字符串返回，数值列未保留类型
- 无登录/鉴权（按需求：内网只读使用；公网部署需反向代理层兜底）
- 不做多级表头/交叉表/打印套打（明确边界，见 PLAN.md）
