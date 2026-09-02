# 开发准则（Development Guide）

> 本文件为项目开发唯一准则，所有成员与 AI 协作均需遵守。变更需提 PR 说明理由。

## 1. 目录与职责

```
sqlreport/
├── src/sqlreport/         # 源码包（唯一可信源码）
│   ├── server.py          # 视图/路由（Handler + PAGE 模板）
│   ├── db.py              # 数据层（DatasourceStore / run_query / merge_* / QueryCache）
│   └── params.py          # 参数层（esc / build_values / substitute / normalize_report）
├── server.py / db.py / params.py  # 根级 shim，仅做 sys.modules 转发（兼容旧导入 + python server.py）
├── pyproject.toml         # 构建元数据（setuptools src 布局）
├── tests/                 # 测试（镜像 src 结构，test_*.py）
├── scripts/               # 运维脚本（start.bat / stop.bat）
├── examples/              # 样例配置（datasources.example.json）
├── docs/
│   ├── DESIGN-v0.2.md / PRD-v0.2.md
│   ├── diagrams/*.mermaid
│   └── DEVELOPMENT.md     # 本文件
├── .trae/rules/project_rules.md  # 机器可读规则（与本文保持一致）
├── reports/ / datasources.json / config.json / logs/  # 运行时产物，不入库
└── .github/workflows/ci.yml
```

**原则**：源码只在 `src/sqlreport/` 修改；根级 `server.py/db.py/params.py` 仅为兼容转发，不得堆业务逻辑。

## 2. 分层与依赖

- `params.py` 纯函数，无状态，不依赖 `db.py` / `server.py`。
- `db.py` 依赖 `params` 仅在归一化，不依赖 `server.py`。
- `server.py` 依赖 `db` + `params`，承载所有 I/O 与 HTML/JS。

**禁止循环依赖**。BASE/REPORTS_DIR/DS_FILE 等路径常量以项目根（`src` 的上两级）为基准，通过 `os.path.dirname(os.path.dirname(os.path.dirname(__file__)))` 推导。

## 3. 开发流程

1. **分支**：`main` 为主干；功能分支 `feat/<简短名>`，修复 `fix/<简短名>`。
2. **提交信息**：`feat:` / `fix:` / `docs:` / `test:` / `chore:` 前缀，72 字符内，说明影响面。
3. **最小变更**：一次提交只做一件事，不顺手重构无关代码（见 CONTRIBUTING）。
4. **必跑检查**（本地，提交前）：
   ```bash
   python -m py_compile src/sqlreport/*.py
   python -m unittest discover -s tests -v
   # 改动 server.py 时
   python server.py & sleep 2; curl -s http://127.0.0.1:8765/ | head; kill %1
   ```
5. **CI 门禁**（`ci.yml`）：`pip install -e .` → 编译 → 导入冒烟 → unittest → 首页 200 冒烟。任一步失败不得合入。
6. **服务重启策略**：每次修改 `src/sqlreport/*.py` 后，必须重启本地服务再验证（`scripts/stop.bat` / `scripts/start.bat` 或 `kill + python server.py`），避免旧进程持有旧代码。
7. **文档同步**：变更对外行为或目录结构时，同时更新 `README.md`、`CHANGELOG.md`（Unreleased 区）、`docs/DESIGN-v0.2.md` 与 `PLAN.md` 完成标记。

## 4. 配置与数据

- `datasources.json` / `config.json` / `reports/*.json` / `logs/` / `demo.db` 不入库（`.gitignore`）。
- 样例以 `examples/datasources.example.json` 为唯一真源（`cp examples/datasources.example.json datasources.json`）。
- 新增配置项必须提供默认值与迁移兼容（旧文件缺字段时自动补）。

## 5. 测试准则

- 测试文件位于 `tests/test_*.py`，与 `src/sqlreport/*.py` 一一对应。
- 导入方式兼容双入口：优先 `from sqlreport.xxx import`，测试中通过 `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))` 解析（根级 shim 兜底旧 `import db`）。
- 覆盖范围：`params` 纯函数单测、`db` 的只读校验/合并/缓存、集成用 `ThreadingHTTPServer` 真实端口测试（见 `test_server.py`）。

## 6. 代码风格

- 遵循 PEP 8；优先标准库，不引入新依赖需先在 Issue 讨论。
- `params.py` 的正则与转义行为视为契约（`{{name}}` / `{{d.begin}}` / `{{n.min}}`），不得擅自改动（见 `docs/DESIGN-v0.2.md §8`）。
- 文件写入统一 `tmp + os.replace` 原子替换；`datasources.json` 写操作持 `db.py` 全局锁。

## 7. 发布与部署

- 版本号在 `src/sqlreport/__init__.py` 与 `pyproject.toml` 同步。
- Windows 启停脚本在 `scripts/`，修改后需在 Windows 真机或说明兼容性。
- 部署文档在 `README.md`「Windows 部署」与 `docs/DESIGN-v0.2.md` 保持一致。

## 8. 文档分工

| 文档 | 面向 | 更新时机 |
|------|------|----------|
| `README.md` | 使用者 | 目录/启动/部署变更时 |
| `CHANGELOG.md` | 使用者 | 每个版本发布时 |
| `docs/DESIGN-v0.2.md` | 架构 | 分层/接口/预算变更时 |
| `docs/DEVELOPMENT.md` | 开发者 | 流程/目录/准则变更时 |
| `DEVLOG.md` | 开发者 | 按日期追加踩坑与决策 |
| `PLAN.md` | 规划 | 完成项打 ✅ 并注版本 |
