# 贡献指南（Contributing）

感谢你愿意为 sqlreport 贡献代码！请阅读并遵守以下约定。

## 开发环境

- Python 3.10+（标准库优先，不引入框架依赖）
- 可选驱动：pymysql（MySQL）、pyodbc（SQL Server）——按需懒加载
- 本地运行：`PORT=8765 python3 server.py`

## 提交规范

- 分支：`main`；功能开发请基于 `main` 新建分支（如 `feat/xxx`）
- Commit 风格：
  - `feat:` 新功能
  - `fix:` 缺陷修复
  - `docs:` 文档
  - `style:` 格式
  - `test:` 测试
  - `security:` 安全修复
- 每个 commit 只做一件事，提交信息不超过 72 字符

## 代码约定

- 遵循 PEP 8
- 保持标准库优先 —— 新增第三方依赖需在 Issue 中先讨论
- 最小变更：不顺手重构无关代码
- SQL 参数占位符 `{{name}}`、日期范围 `{{d.begin}}/{{d.end}}`、数字范围 `{{n.min}}/{{n.max}}` 为既有约定，改动需更新 README

## 提交 PR 流程

1. 先开一个 Issue 说明要解决的问题/功能
2. Fork 仓库，在 `feat/` 分支实现
3. 运行验证：`python3 -m py_compile server.py db.py params.py` 且冒烟测试通过（`python3 -c "import server"`）
4. 更新 CHANGELOG.md（Unreleased 区）与相关文档
5. 发起 PR，标题写明 Issue 编号，描述改动内容与验证结果

## 安全

- 严禁将任何真实数据库连接串（含密码）提交入库
- 涉及鉴权/缓存/查询执行相关改动，需在 PR 中说明安全影响