#!/usr/bin/env bash
# Regenerates desktop-app/app/*.html from the root game files and packages
# the Windows desktop build. Run from the desktop-app/ directory.
set -euo pipefail

ROOT="$(cd .. && pwd)"
APP_DIR="app"

mkdir -p "$APP_DIR"

# game.html is the single-page app entry point (title screen -> HQ -> pre-race
# -> live race -> podium). The other screens are optional legacy modules
# linked from the HQ dashboard (Garaj & Ar-Ge, Pilot & Personel).
MAIN=game.html
MODULES=(garage-rd.html team-management.html)

cp "$ROOT/$MAIN" "$APP_DIR/$MAIN"
for f in "${MODULES[@]}"; do
  cp "$ROOT/$f" "$APP_DIR/$f"
done
cp "$ROOT/f1-2026-data.js" "$APP_DIR/f1-2026-data.js"

# race3d-demo.html (Faz 0 Three.js tech spike) has its own hardcoded back
# link and needs the vendored three.js/OrbitControls modules alongside it.
cp "$ROOT/race3d-demo.html" "$APP_DIR/race3d-demo.html"
mkdir -p "$APP_DIR/vendor"
cp "$ROOT/vendor/"*.js "$APP_DIR/vendor/"

BACK_BTN='<a href="game.html" title="Sezon Merkezine Dön" style="position:fixed;top:8px;left:8px;z-index:99999;
  width:34px;height:34px;border-radius:50%;background:rgba(10,14,20,.85);border:1px solid #333c4a;
  color:#eef1f6;display:flex;align-items:center;justify-content:center;font-family:sans-serif;
  font-size:16px;text-decoration:none;backdrop-filter:blur(4px);">&#8962;</a>
</body>'

for f in "${MODULES[@]}"; do
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

echo "Copied $MAIN + patched ${#MODULES[@]} module screens into $APP_DIR/"

if [ "${1:-}" = "package" ]; then
  npm install --no-audit --no-fund
  npx electron-packager . "Paddock GP" --platform=win32 --arch=x64 --out=dist --overwrite --asar
  echo "Built dist/Paddock GP-win32-x64"
fi
