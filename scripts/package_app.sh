#!/usr/bin/env bash
# EpicTrace 打包:组装签名/公证的 .app 与 dmg。设计:docs/superpowers/specs/
# 2026-07-14-epictrace-macos-app-packaging-design.md(§3 布局、§6 流水线)。
#
# 用法:
#   scripts/package_app.sh                   构建 + 签名(需 SIGN_IDENTITY)
#   scripts/package_app.sh --no-sign         构建不签名(本地冒烟)
#   scripts/package_app.sh --notarize        构建 + 签名 + dmg + 公证 + staple
#                                            (需 SIGN_IDENTITY 与 NOTARY_PROFILE)
#   scripts/package_app.sh --skip-frontend   跳过 npm build(前端没改时)
# 环境:
#   SIGN_IDENTITY   例 "Developer ID Application: <Name> (<TEAMID>)"
#   NOTARY_PROFILE  notarytool keychain profile 名(xcrun notarytool store-credentials 建)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_VERSION="0.11.28"
BUILD="$ROOT/dist/build"
APP="$ROOT/dist/EpicTrace.app"
PY="$ROOT/backend/.venv/bin/python"

sign=1; notarize=0; frontend=1
for a in "$@"; do case "$a" in
  --no-sign) sign=0 ;;
  --notarize) notarize=1 ;;
  --skip-frontend) frontend=0 ;;
  *) echo "未知参数:$a" >&2; exit 2 ;;
esac; done
[ "$sign" = 0 ] && [ "$notarize" = 1 ] && { echo "✗ --no-sign 与 --notarize 冲突:公证要求已签名的 .app" >&2; exit 2; }
[ "$sign" = 1 ] && : "${SIGN_IDENTITY:?需要 SIGN_IDENTITY(或用 --no-sign)}"
[ "$notarize" = 1 ] && : "${NOTARY_PROFILE:?--notarize 需要 NOTARY_PROFILE}"
[ -x "$PY" ] || { echo "✗ backend/.venv 缺失" >&2; exit 1; }

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/backend/pyproject.toml" | head -1)"
echo "▶ EpicTrace ${VERSION}(uv ${UV_VERSION},python $(cat "$ROOT/packaging/python-version"))"
rm -rf "$BUILD" "$APP"; mkdir -p "$BUILD"

# [1] 前端
if [ "$frontend" = 1 ]; then
  echo "▶ 构建前端…"
  # npm ci 按 lockfile 重建依赖树,杜绝陈旧 node_modules 混进签名产物;--skip-frontend 仍是快路径。
  (cd "$ROOT/frontend" && npm ci && npm run build)
fi
[ -f "$ROOT/frontend/dist/index.html" ] || { echo "✗ frontend/dist 缺失(先 build)" >&2; exit 1; }

# [2] vendor uv(缓存;下载校验 .sha256)
VENDOR="$ROOT/packaging/vendor/uv-$UV_VERSION"
if [ ! -x "$VENDOR/uv" ]; then
  echo "▶ 下载 uv ${UV_VERSION}…"
  mkdir -p "$VENDOR"; cd "$VENDOR"
  base="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
  curl -fsSL -o uv.tar.gz "$base/uv-aarch64-apple-darwin.tar.gz"
  curl -fsSL -o uv.tar.gz.sha256 "$base/uv-aarch64-apple-darwin.tar.gz.sha256"
  echo "$(awk '{print $1}' uv.tar.gz.sha256)  uv.tar.gz" | shasum -a 256 -c -
  tar -xzf uv.tar.gz --strip-components=1 uv-aarch64-apple-darwin/uv
  rm uv.tar.gz uv.tar.gz.sha256; cd "$ROOT"
fi

# [3] wheel + lockfile(vendored uv 编译,单平台 arm64-mac,带 hash)
echo "▶ 构建 wheel 与 lockfile…"
"$PY" -m pip wheel "$ROOT/backend" --no-deps -w "$BUILD/wheels" -q
WHEEL="$(ls "$BUILD"/wheels/epictrace-*.whl)"
"$VENDOR/uv" pip compile "$ROOT/backend/pyproject.toml" --generate-hashes \
  --python-version 3.11 -o "$BUILD/requirements.lock" -q

# [4] 编译 Swift(helper + 启动器)。-target 必带:不带时 minos=构建机系统版本,
# 14.4~15.x 的机器 dyld 直接拒载(与 LSMinimumSystemVersion=14.4 矛盾)。
SWIFT_TARGET="arm64-apple-macosx14.4"
echo "▶ 编译 Swift…"
swiftc -O -target "$SWIFT_TARGET" "$ROOT/backend/epictrace/shell/native/SystemAudioCapture.swift" -o "$BUILD/epictrace-sysaudio"
swiftc -O -target "$SWIFT_TARGET" "$ROOT/packaging/launcher/main.swift" "$ROOT/packaging/launcher/Provisioner.swift" -o "$BUILD/EpicTrace"

# [5] 组装 .app
echo "▶ 组装 ${APP}…"
RES="$APP/Contents/Resources"
mkdir -p "$APP/Contents/MacOS" "$RES"
cp "$BUILD/EpicTrace" "$APP/Contents/MacOS/EpicTrace"
sed "s/@VERSION@/$VERSION/g" "$ROOT/packaging/Info.plist.in" > "$APP/Contents/Info.plist"
cp "$VENDOR/uv" "$RES/uv"
cp "$WHEEL" "$RES/"
cp "$BUILD/requirements.lock" "$RES/requirements.lock"
cp "$ROOT/packaging/python-version" "$RES/python-version"
cp "$BUILD/epictrace-sysaudio" "$RES/epictrace-sysaudio"
cp -R "$ROOT/frontend/dist" "$RES/frontend-dist"
mkdir -p "$RES/licenses" && cp "$ROOT/packaging/licenses/"* "$RES/licenses/"
[ -f "$ROOT/packaging/AppIcon.icns" ] && cp "$ROOT/packaging/AppIcon.icns" "$RES/AppIcon.icns"

# [6] 签名(inside-out:先嵌套二进制,后 .app;禁 --deep)
if [ "$sign" = 1 ]; then
  echo "▶ 签名…"
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" "$RES/uv"
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" "$RES/epictrace-sysaudio"
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" \
    --entitlements "$ROOT/packaging/entitlements/launcher.entitlements" "$APP"
  codesign --verify --strict --verbose=2 "$APP"
fi

# [7] dmg + 公证(可选)
if [ "$notarize" = 1 ]; then
  echo "▶ 打 dmg 并公证…"
  DMGROOT="$BUILD/dmgroot"; rm -rf "$DMGROOT"; mkdir -p "$DMGROOT"
  cp -R "$APP" "$DMGROOT/"; ln -s /Applications "$DMGROOT/Applications"
  DMG="$ROOT/dist/EpicTrace-$VERSION.dmg"
  hdiutil create -volname "EpicTrace" -srcfolder "$DMGROOT" -ov -format UDZO "$DMG"
  codesign --force --timestamp -s "$SIGN_IDENTITY" "$DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  echo "✓ ${DMG}(已公证 + staple)"
  # 可选增强(设计 §6):先单独公证并 staple .app 再打 dmg,离线首启不查在线票据;MVP 不做。
fi
echo "✓ 完成:$APP"
