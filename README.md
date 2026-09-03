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
- 统计与分析：合计行 / KPI 摘要 / Top N / 占比 / 时间分桶 / 透视表 / 对比差值 / 数值分箱
- TTL 结果缓存 + 行数截断 + 只读 SQL 校验（大数据量稳定）
- Excel 导出：真 .xlsx（多 sheet、数值单元格可计算）与轻量 .xls / CSV
- 可选 token 鉴权（HMAC 按日轮换）与报表分组目录，支持中文分组

## 快速开始

```bash
pip install -e .                    # 可编辑安装
# 按需：pip install -e ".[mysql]"  或  ".[mssql]"
cp examples/datasources.example.json datasources.json  # 填入真实连接（已被 gitignore）
python server.py                    # 默认 0.0.0.0:8765
# 或：sqlreport                     # 安装后可用命令行入口
# 或：python -m sqlreport 8765      # 模块方式
```

1. 浏览器打开 `http://<host>:8765/`，点「＋新建报表」
2. 填名称、选数据源、贴 SQL（用 `{{参数}}` 占位）、定义参数控件
3. 保存后即得独立访问 URL `/r/<报表id>`，用户免登录直接查询、导出 Excel

> 报表定义存放于 `reports/*.json`（一文件一报表），支持分组子目录 `reports/<分组>/<id>.json`
> （访问 `/r/<分组>/<id>`，列表页按分组折叠；分组名与 ID 禁用 `export`/`edit` 保留字）。
> 首次保存时自动创建该目录。导出格式：查看页可选 `.xlsx` / `.xls` / `.csv`，
> 或报表 `export_format` 指定默认格式。公网部署可在 `config.json` 配置
> `{"auth": "token", "token_secret": "…"}` 启用 HMAC 分享链接鉴权（缺省关闭，内网直用）。

## 目录结构

```
sqlreport/
├── src/sqlreport/         # 源码包（标准 src 布局）
│   ├── __init__.py
│   ├── __main__.py        # python -m sqlreport 入口
│   ├── server.py          # 路由 + 页面模板（视图层）
│   ├── db.py              # 数据层：连接/限流/合并/缓存
│   └── params.py          # 参数层：转义/替换/归一化（纯函数）
├── server.py              # 根级兼容入口（转发到 src）
├── db.py / params.py      # 根级兼容 shim（保留旧导入）
├── pyproject.toml         # 构建与项目元数据（pip install -e .）
├── tests/                 # 单元/集成测试
├── scripts/               # 运维脚本（start.bat / stop.bat）
├── examples/              # 配置样例（datasources.example.json）
├── docs/                  # 架构与产品文档
│   ├── DESIGN-v0.2.md / PRD-v0.2.md
│   ├── diagrams/          # 架构图（mermaid）
│   └── DEVELOPMENT.md     # 开发准则（必读）
├── reports/*.json         # 运行时报表定义（不入库）
├── config.json            # 全局配置（不入库）
└── datasources.json       # 数据源配置（不入库）
```

详见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 开发准则。

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

## Windows 部署

```bat
scripts\start.bat         :: 后台启动（端口 8765，日志 logs\sqlreport.log）
scripts\start.bat 9000    :: 指定端口
scripts\stop.bat          :: 停止
```

`scripts/start.bat` 自动探测 `python`/`py`/`python3`，端口占用检查，`start /min` 后台运行；根目录 `start.bat`/`stop.bat` 为薄转发，兼容旧路径。

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
