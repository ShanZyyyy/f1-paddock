#!/usr/bin/env bash
# Regenerates desktop-app/app/*.html from the root game files and packages
# the Windows desktop build. Run from the desktop-app/ directory.
set -euo pipefail

ROOT="$(cd .. && pwd)"
APP_DIR="app"

mkdir -p "$APP_DIR"

SCREENS=(index.html team-management.html garage-rd.html strategy-setup.html live-race.html post-race.html race-center.html)

for f in "${SCREENS[@]}"; do
  cp "$ROOT/$f" "$APP_DIR/$f"
done
cp "$ROOT/f1-2026-data.js" "$APP_DIR/f1-2026-data.js"

BACK_BTN='<a href="hub.html" title="Ana Menü" style="position:fixed;top:8px;left:8px;z-index:99999;
  width:34px;height:34px;border-radius:50%;background:rgba(10,14,20,.85);border:1px solid #333c4a;
  color:#eef1f6;display:flex;align-items:center;justify-content:center;font-family:sans-serif;
  font-size:16px;text-decoration:none;backdrop-filter:blur(4px);">&#8962;</a>
</body>'

for f in "${SCREENS[@]}"; do
  python3 - "$APP_DIR/$f" "$BACK_BTN" <<'PYEOF'
import sys
path, snippet = sys.argv[1], sys.argv[2]
with open(path, 'r', encoding='utf-8') as fh:
    content = fh.read()
if '</body>' in content:
    content = content.replace('</body>', snippet, 1)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(content)
PYEOF
done

echo "Copied + patched ${#SCREENS[@]} screens into $APP_DIR/"

if [ "${1:-}" = "package" ]; then
  npm install --no-audit --no-fund
  npx electron-packager . "Paddock GP" --platform=win32 --arch=x64 --out=dist --overwrite --asar
  echo "Built dist/Paddock GP-win32-x64"
fi
