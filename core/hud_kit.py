# -*- coding: utf-8 -*-
"""Formula Paddock — yayın HUD kiti (tek kaynak).

`st.components` iframe'lerine basılan bağımsız HUD'ların ortak tasarım dili:
jetonlar (`:root`), tipografi, temel bileşen sınıfları. F1 TV yayın grafiği /
pit duvarı mühendislik ekranı yönü — `core/theme.py` "Yayın Arayüzü" paletini
yansıtır.

Her HUD kendi `<style>` bloğunu YAZMAYA devam eder ama başına `kit_css()`
ekler; böylece renk/font/ölçek tek yerden değişir. Streamlit'e bağımlı
değildir — saf string üretir, izole test edilebilir.

Not: HUD iframe'i srcdoc + çevrimdışıdır — React/CDN yok, saf vanilla-JS/canvas.
"""

from core import theme

# =====================================================================
# JETONLAR
# =====================================================================
# theme.TOKENS koyu paletinden türetilir; anahtarlar --k-* ad alanında
# (sayfanın --fp-* değişkenleriyle çakışmaz, iframe zaten izole).
_T = theme.TOKENS

_SCALE = {
    "void": _T["bg-0"], "night": _T["bg-1"], "panel": _T["bg-2"],
    "raised": _T["bg-3"], "hover": _T["bg-4"],
    "line": _T["line"], "line-lit": _T["line-2"], "line-soft": _T["line-soft"],
    "ink": _T["text"], "dim": _T["text-dim"], "mute": _T["text-mute"],
    "red": _T["red"], "red-bright": _T["red-bright"], "cyan": _T["cyan"],
    "amber": _T["amber"], "green": _T["green"], "pink": _T["pink"], "violet": _T["purple"],
}

# Lastik hamuru — tek kaynak (yayın rengi + rozet zemini).
COMPOUND = {
    "SOFT": "#ff5b5b", "MEDIUM": "#ffd23f", "HARD": "#eef2f7",
    "INTERMEDIATE": "#3ecf8e", "WET": "#39a9ff", "UNKNOWN": "#8fa0b4",
}

F_UI = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif"
F_DATA = "'JetBrains Mono','SFMono-Regular',Consolas,ui-monospace,monospace"


def compound_hex(name):
    """Hamur adından yayın rengi; bilinmiyorsa nötr."""
    return COMPOUND.get(str(name or "").upper().strip(), COMPOUND["UNKNOWN"])


def _root_vars():
    parts = [f"--k-{k}:{v}" for k, v in _SCALE.items()]
    parts.append(f"--k-f-ui:{F_UI}")
    parts.append(f"--k-f-data:{F_DATA}")
    parts += ["--k-r-s:6px", "--k-r-m:9px", "--k-r-l:13px", "--k-r-pill:999px", "--k-edge:2px"]
    return ";".join(parts)


# =====================================================================
# TEMEL BİLEŞEN SINIFLARI
# =====================================================================
_PRIMITIVES = r"""
*{box-sizing:border-box}
html,body{margin:0}
/* body zemini şeffaf bırakılır — HUD, sayfa üstünde yüzen bir .k-pane kartıdır
   (ev tarzı). Kök öğeye .k-pane ver. */
body{background:transparent;color:var(--k-ink);font-family:var(--k-f-ui);
  -webkit-font-smoothing:antialiased;font-feature-settings:"cv05","ss01"}
::selection{background:color-mix(in srgb,var(--k-cyan) 30%,transparent)}
:focus-visible{outline:2px solid var(--k-cyan);outline-offset:2px}

/* theme.hud_iframe_style eski bileşen seçicilerini (.r/.box/.tile/.panel/.card/
   .summary/.hud) nötrle — kit HUD'ları kendi zeminini açıkça boyar. Bir HUD'un
   kendi kuralı bu sıfırlamadan sonra geldiği için onları yener. */
.r,.box,.tile,.panel,.card,.summary,.hud{background:transparent;border-color:var(--k-line)}

.k-pane{background:var(--k-panel);border:1px solid var(--k-line);border-radius:var(--k-r-l)}
.k-pane-h{display:flex;align-items:center;gap:10px;padding:12px 15px;border-bottom:1px solid var(--k-line)}
.k-k{font-family:var(--k-f-data);font-weight:600;font-size:10.5px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--k-mute)}
.k-k b{color:var(--k-cyan);font-weight:600}
.k-sub{font-size:10px;color:var(--k-dim);margin-top:5px;line-height:1.5}
.k-data{font-family:var(--k-f-data);font-variant-numeric:tabular-nums;letter-spacing:-.01em}

/* etiketli metre */
.k-meter{height:6px;border-radius:3px;background:var(--k-raised);overflow:hidden}
.k-meter>i{display:block;height:100%;border-radius:3px;transition:width .1s linear}

/* pilot çip şeridi */
.k-chip{border:1px solid var(--k-line);border-left:var(--k-edge) solid var(--team,var(--k-mute));
  border-radius:var(--k-r-s);background:var(--k-raised);color:var(--k-ink);
  padding:5px 9px;font:700 11px var(--k-f-ui);cursor:pointer;transition:background .2s ease}
.k-chip:hover{background:var(--k-hover)}
.k-chip[aria-pressed="true"]{box-shadow:inset 0 0 0 1px var(--team,var(--k-cyan))}

@media (prefers-reduced-motion:reduce){*,*::before,*::after{
  animation-duration:.001ms !important;transition-duration:.001ms !important}}
"""


def kit_css():
    """HUD `<style>` bloğunun başına eklenecek ortak stil metni (etiketsiz)."""
    return ":root{color-scheme:dark;" + _root_vars() + "}" + _PRIMITIVES


def google_fonts_link():
    """Iframe <head>'ine — Inter + JetBrains Mono."""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?'
        "family=Inter:wght@400;500;600;700;800&"
        'family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">'
    )
