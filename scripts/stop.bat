@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
if exist "..\server.py" cd /d "%~dp0.."

if "%PORT%"=="" set PORT=8765
if not "%~1"=="" set PORT=%~1

echo [停止] 查找端口 %PORT% 上的 SqlReport 进程...

set PID=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  set PID=%%a
  goto :found
)

echo [提示] 未发现端口 %PORT% 的监听进程，服务可能已停止
if exist logs\sqlreport.log echo 日志: logs\sqlreport.log
pause
exit /b 0

:found
echo 端口 %PORT% 占用 PID=%PID%
for /f "tokens=1" %%b in ('tasklist /fi "PID eq %PID%" /fo csv /nh 2^>nul') do set PNAME=%%b
echo 进程: %PNAME% PID=%PID%

taskkill /PID %PID% /T >nul 2>&1
timeout /t 2 /nobreak >nul

tasklist /fi "PID eq %PID%" 2>nul | findstr "%PID%" >nul
if !errorlevel!==0 (
  echo [重试] 进程未退出，强制结束...
  taskkill /F /PID %PID% /T >nul 2>&1
  timeout /t 2 /nobreak >nul
)

netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if !errorlevel!==0 (
  echo [失败] 端口 %PORT% 仍被占用，请检查：
  netstat -ano | findstr ":%PORT% "
) else (
  echo [成功] 已停止端口 %PORT% 的服务 ^(PID %PID%^)
)
pause
endlocal
