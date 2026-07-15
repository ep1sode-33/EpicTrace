#!/usr/bin/env bash
# EpicTrace 一键启动:构建前端 → 起后端(serve dist)+ 开 pywebview 窗口。
#
# 用法:
#   ./run.sh              构建前端,然后启动桌面应用
#   ./run.sh --no-build   跳过构建,直接启动(快速重启,前端没改时用)
#   ./run.sh --build      只构建前端,不启动
#   ./run.sh --help       显示本帮助
#
# 架构:后端 FastAPI 把 frontend/dist 挂在 /,uvicorn 跑在 127.0.0.1:8765,
# 壳(epictrace.shell)起后端 + 用 pywebview 开窗指向它。改了前端代码须重新 build 才生效。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/backend/.venv/bin/python"

build=1
launch=1
case "${1:-}" in
  -n|--no-build) build=0 ;;
  -b|--build)    launch=0 ;;
  -h|--help)     awk 'NR>1{if(/^#/){sub(/^# ?/,"");print}else exit}' "$0"; exit 0 ;;
  "")            ;;
  *)             echo "未知参数:$1(见 --help)" >&2; exit 2 ;;
esac

# ---- 前置检查 ----
if [ ! -x "$PY" ]; then
  echo "✗ 后端 venv 缺失:$PY" >&2
  echo "  先建:cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi
if [ "$build" = 1 ] && ! command -v npm >/dev/null 2>&1; then
  echo "✗ 未找到 npm(需 Node.js;或用 ./run.sh --no-build 跳过构建)" >&2
  exit 1
fi

# ---- 构建前端 ----
if [ "$build" = 1 ]; then
  echo "▶ 构建前端…"
  cd "$ROOT/frontend"
  [ -d node_modules ] || { echo "  首次依赖安装:npm install…"; npm install; }
  npm run build
  echo "✓ 前端已构建 → frontend/dist"
fi

# ---- 启动应用 ----
if [ "$launch" = 1 ]; then
  echo "▶ 启动 EpicTrace(后端 http://127.0.0.1:8765 + 桌面窗口)…  关窗或 Ctrl-C 退出"
  cd "$ROOT"
  exec "$PY" -m epictrace.shell
fi
