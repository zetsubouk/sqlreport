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

echo [启动] SqlReport 端口 %PORT%  Python=!PYTHON!
start "SqlReport" /min cmd /c ""!PYTHON!" server.py !PORT! > logs\sqlreport.log 2>&1"

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
