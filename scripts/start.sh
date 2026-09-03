#!/bin/sh
# SqlReport 一键启动（macOS / Linux 通用，POSIX sh，兼容 bash/dash/zsh）
# 自检 Python 3.10+ → 自动创建 .venv → pip install -e . → 可选装数据库驱动 → 后台启动 + 健康检查
# 用法：scripts/start.sh [端口]   （默认 8765，也可用环境变量 PORT）
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

PORT="${1:-${PORT:-8765}}"

log()  { printf '%s\n' "$*"; }
fail() { printf '[失败] %s\n' "$*" >&2; exit 1; }

# ---------- 1. 找 Python 3.10+ ----------
find_python() {
  for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
    if command -v "$c" >/dev/null 2>&1; then
      ver=$("$c" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null) || continue
      if [ "${ver:-0}" -ge 310 ]; then printf '%s' "$c"; return 0; fi
    fi
  done
  return 1
}

if PYTHON="$(find_python)"; then
  log "[环境] Python=$PYTHON ($("$PYTHON" --version 2>&1))"
else
  case "$(uname -s)" in
    Darwin) fail "未找到 Python 3.10+，请执行：brew install python3" ;;
    *)
      if [ -f /etc/os-release ]; then . /etc/os-release; fi
      case "${ID:-}" in
        ubuntu|debian) fail "未找到 Python 3.10+，请执行：sudo apt install python3 python3-venv" ;;
        fedora|rhel|centos|rocky|almalinux) fail "未找到 Python 3.10+，请执行：sudo dnf install python3" ;;
        arch) fail "未找到 Python 3.10+，请执行：sudo pacman -S python" ;;
        *) fail "未找到 Python 3.10+，请先安装 Python 3.10 或更高版本" ;;
      esac ;;
  esac
fi

# ---------- 2. 端口占用检查 ----------
port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -q ":$PORT "
  else
    (echo >/dev/tcp/127.0.0.1/"$PORT") >/dev/null 2>&1
  fi
}
if port_in_use; then
  fail "端口 ${PORT} 已被占用，服务可能已在运行（停止：scripts/stop.sh ${PORT}）"
fi

# ---------- 3. venv + 可编辑安装 ----------
VENV="$ROOT/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  log "[依赖] 创建虚拟环境 .venv ..."
  "$PYTHON" -m venv "$VENV" || fail "venv 创建失败（Debian/Ubuntu 请先装 python3-venv）"
fi
VENV_PY="$VENV/bin/python"
if ! "$VENV_PY" -c 'import sqlreport' 2>/dev/null; then
  log "[依赖] 安装 sqlreport（pip install -e .）..."
  "$VENV_PY" -m pip install -q -e . || fail "pip install -e . 失败"
fi

# ---------- 4. 可选数据库驱动（仅 SQLite 可忽略；有终端才询问） ----------
MISSING=""
for m in pymysql pyodbc; do
  "$VENV_PY" -c "import $m" 2>/dev/null || MISSING="${MISSING} $m"
done
if [ -n "${MISSING}" ]; then
  log "[依赖] 未安装的数据库驱动：${MISSING}（仅用 SQLite 可忽略）"
  if [ -t 0 ]; then
    printf '是否自动安装 (Y/N): '
    read -r ans
    case "$ans" in
      [Yy]*)
        # shellcheck disable=SC2086
        if "$VENV_PY" -m pip install ${MISSING}; then
          log "[依赖] 安装完成"
        else
          log "[警告] 安装失败，连接 MySQL/SQL Server 前请手动安装驱动"
        fi ;;
      *) log "[提示] 已跳过，连接 MySQL/SQL Server 前请手动安装驱动" ;;
    esac
  else
    log "[提示] 非交互终端，已跳过驱动安装"
  fi
fi

# ---------- 5. 后台启动 + 健康检查 ----------
mkdir -p logs reports
PIDFILE="logs/sqlreport.$PORT.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  fail "pidfile $PIDFILE 对应进程仍在运行，先执行 scripts/stop.sh $PORT"
fi
log "[启动] SqlReport 端口 $PORT ..."
# shellcheck disable=SC2086
nohup "$VENV_PY" -m sqlreport $PORT >logs/sqlreport.log 2>&1 &
echo $! >"$PIDFILE"

sleep 2
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)
if [ "$code" = "200" ]; then
  log "[成功] 服务已启动 http://localhost:$PORT/"
  log "日志: logs/sqlreport.log  停止: scripts/stop.sh $PORT"
else
  rm -f "$PIDFILE"
  log "[失败] 启动后健康检查未通过，日志："
  tail -n 20 logs/sqlreport.log 2>/dev/null
  exit 1
fi
