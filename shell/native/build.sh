#!/usr/bin/env bash
# 构建 macOS 系统内录 helper 到 $1(输出目录),产物名 epictrace-sysaudio。
# 用法:shell/native/build.sh <output_dir>
#   <output_dir> 通常是 data_dir/bin;Python 侧 SystemAudioSource 用该路径 Popen。
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <output_dir>" >&2
  exit 2
fi

OUT_DIR="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$OUT_DIR"

swiftc -O "$SCRIPT_DIR/SystemAudioCapture.swift" -o "$OUT_DIR/epictrace-sysaudio"
echo "built: $OUT_DIR/epictrace-sysaudio"
