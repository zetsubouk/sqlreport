# 变更记录（CHANGELOG）

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化。
最新变更置顶。

## [Unreleased]

### 计划中（见 PLAN.md）
- P0：MySQL/SQLServer 真库连通测试、SQLServer ODBC 部署文档、数值列类型保留
- P1：合计行、简单交叉表（pivot 配置）、点列头排序、分页/行数上限
- P2：查询超时与只读拦截、报表 token 鉴权、真 .xlsx 导出、systemd/Docker 化、报表分组目录

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
