@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
if exist "..\pyproject.toml" cd /d "%~dp0.."
if exist "..\server.py" cd /d "%~dp0.."

if "%PORT%"=="" set PORT=8765
if not "%~1"=="" set PORT=%~1

set PYTHON=
rem ---- Python 探测：轮询多候选，逐个验证有效性（版本 3.10+ 且能真正执行） ----
rem 注意：不能只取第一个 where 命中的命令就用，Microsoft Store 跳转的 python
rem where 能找到但无法真正执行，必须跳过并继续试 py / python3。
for %%P in (python py python3) do (
  if not defined PYTHON (
    where %%P >nul 2>&1
    if not errorlevel 1 (
      %%P -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
      if not errorlevel 1 set PYTHON=%%P
    )
  )
)
rem ---- 常见安装路径兜底（安装时未勾选 Add to PATH 的情况） ----
if not defined PYTHON (
  for /d %%D in ("%LocalAppData%\Programs\Python\Python3*" "%ProgramFiles%\Python3*" "%ProgramFiles(x86)%\Python3*") do (
    if not defined PYTHON (
      if exist "%%D\python.exe" (
        "%%D\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
        if not errorlevel 1 set PYTHON=%%D\python.exe
      )
    )
  )
)
if not defined PYTHON (
  echo [错误] 未找到有效的 Python 3.10+，需要 Python 3.10+
  echo        已按顺序尝试：python / py / python3，以及常见安装目录，均无效或版本过低
  echo.
  echo [排查] 请在新开 cmd 逐条执行并把输出贴回：
  echo        where python
  echo        where py
  echo        python --version
  echo        py --version
  echo        py --list
  echo.
  echo        常见原因：仅有 Microsoft Store 跳转（WindowsApps\python.exe），
  echo        请从 python.org 安装 Python 3.10+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)
"!PYTHON!" --version
echo [环境] 使用 Python：!PYTHON!

netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if !errorlevel!==0 (
  echo [提示] 端口 %PORT% 已被占用，服务可能已在运行：
  netstat -ano | findstr ":%PORT% "
  pause
  exit /b 1
)

if not exist logs mkdir logs >nul 2>&1
if not exist reports mkdir reports >nul 2>&1

rem ---- 可选数据库驱动检查（pymysql/pyodbc）：缺失仅提示，默认跳过安装 ----
rem 注意：for 循环体内不可用 if not !errorlevel!==0 判断上条命令，
rem 中间的 set 会把 errorlevel 置 0 造成误判；统一用 if errorlevel 1（即 >=1）。
set MISSING=
for %%m in (pymysql pyodbc) do (
  "!PYTHON!" -c "import %%m" >nul 2>&1
  if errorlevel 1 set "MISSING=!MISSING! %%m"
)
if not "!MISSING!"=="" (
  echo.
  echo [依赖] 未安装的数据库驱动：!MISSING!
  echo        需要这些驱动才能连接 MySQL/SQL Server，仅用 SQLite 可忽略
  echo        Python：!PYTHON!
  set "DOINST=N"
  set /p "DOINST=是否自动安装 [默认N，直接回车跳过] (Y/N): "
  if /i "!DOINST!"=="Y" (
    "!PYTHON!" -m pip install !MISSING!
    set "STILL_MISSING="
    for %%m in (pymysql pyodbc) do (
      "!PYTHON!" -c "import %%m" >nul 2>&1
      if errorlevel 1 set "STILL_MISSING=!STILL_MISSING! %%m"
    )
    if "!STILL_MISSING!"=="" (
      echo [依赖] 安装完成
    ) else (
      echo [警告] 以下驱动安装后仍无法导入：!STILL_MISSING!
      echo        请手动执行： "!PYTHON!" -m pip install!STILL_MISSING!
    )
  ) else (
    echo [提示] 已跳过驱动安装。连接 MySQL/SQL Server 前请先手动安装驱动。
  )
  echo.
)

rem ---- 包自检：pip 安装过则直接用，否则用 src 布局兜底 ----
rem 同样用 if errorlevel 1 判断 import 结果，避免 errorlevel 被覆盖误判。
rem 注意：包自检必须在 PYTHONPATH 兜底后复验，避免“已兜底仍误报缺包”。
"!PYTHON!" -c "import sqlreport" >nul 2>&1
if errorlevel 1 (
  if exist "src\sqlreport\__init__.py" (
    if defined PYTHONPATH ( set "PYTHONPATH=%CD%\src;!PYTHONPATH!" ) else ( set "PYTHONPATH=%CD%\src" )
    "!PYTHON!" -c "import sqlreport" >nul 2>&1
    if errorlevel 1 (
      echo [依赖] PYTHONPATH 已指向 %CD%\src 但仍无法导入 sqlreport，请检查 src\sqlreport 是否完整
      pause
      exit /b 1
    )
  ) else (
    echo [依赖] 未找到 sqlreport 包且无 src\sqlreport，请先执行 "!PYTHON!" -m pip install -e .
    pause
    exit /b 1
  )
)

echo [启动] SqlReport 端口 %PORT%  Python=!PYTHON!
if defined PYTHONPATH echo        PYTHONPATH=!PYTHONPATH!
rem 服务进程：优先 pythonw（无控制台，彻底隐藏窗口）；找不到则回退最小化窗口。日志均写 logs\sqlreport.log
rem 注意：start 异步进程继承不到本窗口的 setlocal 环境变量，PYTHONPATH 必须随命令显式传递。
set PYTOOL=
where pythonw >nul 2>&1
if not !errorlevel!==0 set PYTOOL=pythonw
if defined PYTOOL (
  if defined PYTHONPATH (
    start "" cmd /c "set PYTHONPATH=!PYTHONPATH!&& !PYTOOL! -m sqlreport !PORT! > logs\sqlreport.log 2>&1"
  ) else (
    start "" !PYTOOL! -m sqlreport !PORT! > logs\sqlreport.log 2>&1
  )
) else (
  start "SqlReport" /min cmd /c ""!PYTHON!" -m sqlreport !PORT! > logs\sqlreport.log 2>&1"
)

timeout /t 2 /nobreak >nul
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if !errorlevel!==0 (
  echo [成功] 服务已启动 http://localhost:!PORT!/
  echo 日志: logs\sqlreport.log
  echo 停止: scripts\stop.bat ^(或 scripts\stop.bat !PORT!^)
) else (
  echo [失败] 启动后未检测到监听端口，请查看日志：
  if exist logs\sqlreport.log type logs\sqlreport.log
)
pause
endlocal
