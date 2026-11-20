#!/usr/bin/env bash
# build.sh — build the PWA into static files and stage them inside
# pi/setup_service/static/. Run on macOS before rsync to Pi.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$ROOT/app"
PI_STATIC_DIR="$ROOT/pi/setup_service/static"

if [ ! -d "$APP_DIR/node_modules" ]; then
  (cd "$APP_DIR" && npm install)
fi

(cd "$APP_DIR" && npm run build)

if [ ! -d "$APP_DIR/out" ]; then
  echo "ERROR: $APP_DIR/out was not produced." >&2
  exit 1
fi

rm -rf "$PI_STATIC_DIR"
cp -R "$APP_DIR/out" "$PI_STATIC_DIR"

echo "==> DONE. rsync -avz --delete \"$ROOT/pi/\" philipn1@moo.local:~/pi/"
