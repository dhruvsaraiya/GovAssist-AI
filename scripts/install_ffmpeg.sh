#!/usr/bin/env bash
set -euo pipefail
# Installs a local static ffmpeg build under tools/ffmpeg (Linux/macOS) without touching system package managers.
# Usage: ./scripts/install_ffmpeg.sh
# After completion, add tools/ffmpeg/bin (printed) to your PATH or source the export line.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
TOOLS_DIR="$ROOT_DIR/tools"
FF_DIR="$TOOLS_DIR/ffmpeg"
mkdir -p "$FF_DIR"

OS="$(uname -s)"
ARCH="$(uname -m)"

# Pick a static build source
if [[ "$OS" == "Darwin" ]]; then
  URL="https://evermeet.cx/ffmpeg/ffmpeg-6.1.zip"
  ZIP_NAME="ffmpeg-mac.zip"
  echo "[ffmpeg] Downloading macOS build: $URL"
  curl -L "$URL" -o "$FF_DIR/$ZIP_NAME"
  echo "[ffmpeg] Extracting..."
  unzip -o "$FF_DIR/$ZIP_NAME" -d "$FF_DIR" >/dev/null
  rm "$FF_DIR/$ZIP_NAME"
  mkdir -p "$FF_DIR/bin"
  mv "$FF_DIR/ffmpeg" "$FF_DIR/bin/ffmpeg"
  chmod +x "$FF_DIR/bin/ffmpeg"
else
  # Linux generic static build
  URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
  TAR_NAME="ffmpeg-linux.tar.xz"
  echo "[ffmpeg] Downloading linux static build: $URL"
  curl -L "$URL" -o "$FF_DIR/$TAR_NAME"
  echo "[ffmpeg] Extracting..."
  tar -xJf "$FF_DIR/$TAR_NAME" -C "$FF_DIR"
  rm "$FF_DIR/$TAR_NAME"
  # Move into bin
  INNER_DIR="$(find "$FF_DIR" -maxdepth 1 -type d -name 'ffmpeg-*' | head -n1)"
  mkdir -p "$FF_DIR/bin"
  mv "$INNER_DIR/ffmpeg" "$FF_DIR/bin/ffmpeg"
  mv "$INNER_DIR/ffprobe" "$FF_DIR/bin/ffprobe" || true
  rm -rf "$INNER_DIR"
fi

if [[ ! -x "$FF_DIR/bin/ffmpeg" ]]; then
  echo "[ffmpeg] ERROR: ffmpeg binary not found after extraction" >&2
  exit 1
fi

# Write helper file
HELPER="$FF_DIR/PATH_ADD.txt"
{
  echo "Add this line to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
  echo "export PATH=\"$FF_DIR/bin:\$PATH\""
  echo
  echo "Current session (run now):"
  echo "export PATH=\"$FF_DIR/bin:\$PATH\""
} > "$HELPER"

chmod +x "$FF_DIR/bin/ffmpeg"
"$FF_DIR/bin/ffmpeg" -version | head -n1

echo "[ffmpeg] Installed under: $FF_DIR"
echo "[ffmpeg] Helper file: $HELPER"
echo "[ffmpeg] Add to PATH as instructed above."
