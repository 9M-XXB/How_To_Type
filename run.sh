#!/bin/sh
# pyin 启动脚本：优先用 code/.venv（含 pyobjc → 全局热键 ⌘⌥E + CGEvent 注入）；
# 没有 .venv 时退回系统 python3（降级模式：无热键、注入走 osascript，功能等价）。
cd "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
exec "$PY" pyin.py "$@"
