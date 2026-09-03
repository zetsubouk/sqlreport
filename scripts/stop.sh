#!/bin/sh
# SqlReport 一键停止（macOS / Linux 通用，POSIX sh）
# 用法：scripts/stop.sh [端口]   （默认 8765，也可用环境变量 PORT）
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

PORT="${1:-${PORT:-8765}}"
PIDFILE="logs/sqlreport.$PORT.pid"

log() { printf '%s\n' "$*"; }

# ---------- 1. pidfile 精准停 ----------
if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE")
  if kill -0 "$pid" 2>/dev/null; then
    log "[停止] 结束 PID $pid ..."
    kill "$pid" 2>/dev/null || true
    i=0
    while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do sleep 1; i=$((i + 1)); done
    if kill -0 "$pid" 2>/dev/null; then
      log "[重试] 进程未退出，强制结束 ..."
      kill -9 "$pid" 2>/dev/null || true
      sleep 1
    fi
  fi
  rm -f "$PIDFILE"
fi

# ---------- 2. 按端口兜底（pidfile 丢失时） ----------
pids=""
if command -v lsof >/dev/null 2>&1; then
  pids=$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
fi
if [ -n "$pids" ]; then
  # shellcheck disable=SC2086
  log "[停止] 端口 $PORT 残留进程：${pids}，结束 ..."
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 2
  # shellcheck disable=SC2086
  kill -9 $pids 2>/dev/null || true
fi

# ---------- 3. 确认 ----------
if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  log "[失败] 端口 $PORT 仍被占用，请检查："
  lsof -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  exit 1
else
  log "[成功] 端口 $PORT 的服务已停止"
fi
