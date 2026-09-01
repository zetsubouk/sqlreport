# SQL 报表工具

轻量级 Web 报表工具：贴 SQL + 参数条件 → 独立 URL 查询 → Excel 导出。
Python 3 标准库实现，无 Web 框架依赖；MySQL/SQLServer 驱动按需懒加载。

## 快速开始

```bash
pip install pymysql          # 如需 MySQL（SQLServer 需 pyodbc + ODBC 驱动）
python3 server.py            # 默认 0.0.0.0:8765，或 PORT=8765 python3 server.py
```

1. 浏览器打开 `http://<host>:8765/`，点「＋新建报表」
2. 填名称、选数据源、贴 SQL（用 `{{参数}}` 占位）、定义参数控件
3. 保存后即得独立访问 URL `/r/<报表id>`，用户免登录直接查询、导出 Excel

## 目录结构

```
sqlreport/
├── server.py          # 全部后端逻辑（单文件）
├── datasources.json   # 数据源配置（"_"前缀条目隐藏）
├── reports/*.json     # 报表定义，一文件一报表 = 独立URL /r/<id>
├── PLAN.md            # 架构总结 + 开发计划（三期路线）
├── CHANGELOG.md       # 版本变更记录
└── DEVLOG.md          # 开发日志（按日期追加，含踩坑记录）
```

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
- SQL 允许任意查询语法（不做沙箱），不校验 SELECT（P2 计划中）

## 文档约定

- **CHANGELOG.md**：面向使用者，按版本号记录功能变更，新版本置顶
- **DEVLOG.md**：面向开发者，按日期记录开发过程、踩坑与决策，只追加不修改
- **PLAN.md**：路线图，功能完成后在对应条目标 ✅ 并注明版本
- 版本号：`v0.x` 阶段（未对外交付），交付客户起升 `v1.0`；变更遵循语义化（新增功能升 minor，修复升 patch）
