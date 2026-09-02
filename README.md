# sqlreport

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/zetsubouk/sqlreport/actions/workflows/ci.yml/badge.svg)](https://github.com/zetsubouk/sqlreport/actions)

轻量级 Web 报表工具：贴 SQL + 参数条件 → 独立 URL 查询 → Excel 导出。

- 纯 Python 3 标准库实现，无 Web 框架依赖
- 支持 MySQL / SQLServer / SQLite（驱动按需懒加载）
- 每个报表 = 一个 JSON 文件 = 一个独立 URL，用户免登录只读访问
- 6 种参数控件：文本 / 下拉 / 日期 / 日期范围 / 数字 / 数字范围
- 多数据集跨源查询：union 纵向合并 / lookup 横向关联
- TTL 结果缓存 + 行数截断 + 只读 SQL 校验（大数据量稳定）
- Excel 导出（.xls，零依赖）

## 快速开始

```bash
pip install pymysql              # 如需 MySQL（SQLServer 需 pyodbc + ODBC 驱动）
cp datasources.example.json datasources.json   # 填入真实数据源连接（已被 gitignore）
python3 server.py                # 默认 0.0.0.0:8765，或 PORT=8765 python3 server.py
```

1. 浏览器打开 `http://<host>:8765/`，点「＋新建报表」
2. 填名称、选数据源、贴 SQL（用 `{{参数}}` 占位）、定义参数控件
3. 保存后即得独立访问 URL `/r/<报表id>`，用户免登录直接查询、导出 Excel

> 报表定义存放于 `reports/*.json`（一文件一报表）。`server.py` 会在首次保存时自动创建该目录。

## 目录结构

```
sqlreport/
├── server.py          # 路由 + 页面模板（视图层）
├── db.py              # 数据层：数据源存取/建连(超时)/查询限流/合并引擎/查询缓存
├── params.py          # 参数层：转义/替换/报表双格式归一化（纯函数）
├── config.json        # 全局配置（不入库）：admin_password 空=管理页仅本机可访问
├── datasources.example.json  # 数据源配置模板（"_[前缀]"或 enabled:false = 禁用）
├── reports/*.json     # 运行时报表定义（不入库，按需创建）
├── docs/              # PRD / 架构设计文档
├── PLAN.md            # 架构总结 + 开发计划
├── CHANGELOG.md       # 版本变更记录
└── DEVLOG.md          # 开发日志（按日期追加，含踩坑记录）
```

## 数据源管理

浏览器打开 `/datasources`（默认仅本机 127.0.0.1 可访问；远程管理在 `config.json` 配置
`"admin_password": "口令"` 后走 HTTP Basic）。支持新建/编辑/连接测试/启用禁用/删除，
改动免重启生效；密码在列表页不显示、编辑时留空表示不修改；删除被报表引用的数据源需二次确认。

## 多数据集报表（跨源查询）

编辑器可添加多个数据集（各自选数据源、写 SQL、单独试运行前 20 行），≥2 个数据集时可选合并方式：

```jsonc
{
  "datasets": [
    {"name": "a", "ds": "mysql1", "sql": "SELECT ..."},
    {"name": "b", "ds": "mssql1", "sql": "SELECT ..."}
  ],
  "merge": {"mode": "union"},                                              // 纵向合并（按列名对齐，缺失列留空）
  "merge": {"mode": "lookup", "base": "a", "with": "b", "on": ["cust_id"], "cols": ["cname"]},  // 横向关联（left join 取值，右表≤10万行）
  "cache_ttl": 300,      // 结果缓存秒数，0=实时
  "max_rows": 2000       // 展示行上限，超限截断并提示
}
```

只有一个数据集的报表自动以旧格式 `{ds, sql}` 保存，旧报表零迁移可用。

## 性能与缓存

- 查询硬上限：单数据集 fetch 10 万行；每数据源 `timeout`（秒）超时控制
- 结果缓存：报表级 `cache_ttl`（默认建议 300，0=实时），key=报表id+参数哈希；
  保存报表即清空该报表缓存；仅 SELECT/WITH 单语句可执行

## 参数占位符约定

| 参数类型 | SQL 写法 | 说明 |
|----------|----------|------|
| 文本/数字/日期/下拉 | `AND region = '{{region}}'` | 值经转义后替换 |
| 日期范围 | `dt >= '{{d.begin}}' AND dt <= '{{d.end}}'` | 也支持下划线写法 `{{d_begin}}` |
| 数字范围 | `amt >= '{{n.min}}' AND amt <= '{{n.max}}'` | 同上 |
| 可选条件 | 含占位符的整行，参数未填时自动丢弃 | 条件务必独立成行 |

## 安全约定

- 业务库一律使用只读账号
- 参数值按 `''` 转义后拼接；SQL 作者可信，但参数必须防注入
- SQL 白名单校验：仅允许单条 SELECT/WITH 语句（去注释后校验，拒绝多语句）
- 管理页（/datasources）默认仅本机可访问，或 HTTP Basic 口令保护
- 建议通过反向代理（Nginx/Caddy）提供 HTTPS

## 贡献与支持

见 [CONTRIBUTING.md](CONTRIBUTING.md)、[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)、[SECURITY.md](SECURITY.md)。

## License

[MIT](LICENSE) © 2026 [zetsubouk](https://github.com/zetsubouk)