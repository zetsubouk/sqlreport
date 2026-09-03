#!/bin/sh
# 薄转发：兼容旧路径，实际逻辑在 scripts/stop.sh
exec "$(dirname "$0")/scripts/stop.sh" "$@"
