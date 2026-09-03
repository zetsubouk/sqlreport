@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
if exist "..\server.py" cd /d "%~dp0.."

if "%PORT%"=="" set PORT=8765
if not "%~1"=="" set PORT=%~1

set PYTHON=
where python >nul 2>&1
if !errorlevel!==0 set PYTHON=python
if "!PYTHON!"=="" (
  where py >nul 2>&1
  if !errorlevel!==0 set PYTHON=py
)
if "!PYTHON!"=="" (
  where python3 >nul 2>&1
  if !errorlevel!==0 set PYTHON=python3
)
if "!PYTHON!"=="" (
  echo [错误] 未找到 Python，请安装 Python 3.10+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if !errorlevel!==0 (
  echo [提示] 端口 %PORT% 已被占用，服务可能已在运行：
  netstat -ano | findstr ":%PORT% "
  pause
  exit /b 1
)

if not exist logs mkdir logs >nul 2>&1
if not exist reports mkdir reports >nul 2>&1

rem ---- 可选数据库驱动检查（pymysql/pyodbc）：缺失则引导自动安装 ----
set MISSING=
set NEED_INSTALL=0
for %%m in (pymysql pyodbc) do (
  "!PYTHON!" -c "import %%m" >nul 2>&1
  if not !errorlevel!==0 (
    set "MISSING=!MISSING! %%m"
    set NEED_INSTALL=1
  )
)
if !NEED_INSTALL!==1 (
  echo.
  echo [依赖] 检测到未安装的数据库驱动：!MISSING!
  echo        Python：!PYTHON!，需要这些驱动才能连接 MySQL/SQL Server，仅用 SQLite 可忽略
  set "DOINST="
  set /p "DOINST=是否自动安装 (Y/N): "
  if /i "!DOINST!"=="Y" (
    "!PYTHON!" -m pip install !MISSING!
    set VERIFY_OK=1
    for %%m in (pymysql pyodbc) do (
      "!PYTHON!" -c "import %%m" >nul 2>&1
      if not !errorlevel!==0 set VERIFY_OK=0
    )
    if !VERIFY_OK!==1 (
      echo [依赖] 安装完成
    ) else (
      echo [警告] 安装后仍无法导入，请手动执行： "!PYTHON!" -m pip install pymysql pyodbc
    )
  ) else (
    echo [提示] 已跳过安装。连接 MySQL/SQL Server 前请先手动安装驱动。
  )
  echo.
)

echo [启动] SqlReport 端口 %PORT%  Python=!PYTHON!
rem 服务进程：优先 pythonw（无控制台，彻底隐藏窗口）；找不到则回退最小化窗口。日志均写 logs\sqlreport.log
set PYTOOL=
where pythonw >nul 2>&1
if not !errorlevel!==0 set PYTOOL=pythonw
if defined PYTOOL (
  start "" !PYTOOL! server.py !PORT! > logs\sqlreport.log 2>&1
) else (
  start "SqlReport" /min cmd /c ""!PYTHON!" server.py !PORT! > logs\sqlreport.log 2>&1"
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
