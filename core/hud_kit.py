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

/* --- jargon baloncuğu (saf CSS tooltip) --------------------------------
   Kullanım:  <span class="k-info" tabindex="0" data-tip="kısa açıklama">i</span>
   Varsayılan aşağı açılır (dar iframe'de üst kenara takılmasın); üstte yer
   varsa .up ver. fp_kit.info("...") yardımcısı hazır işaretleme döndürür. */
.k-info{position:relative;display:inline-flex;align-items:center;justify-content:center;
  width:14px;height:14px;margin:0 1px 0 4px;flex:0 0 auto;border-radius:50%;vertical-align:middle;
  border:1px solid var(--k-line-lit);background:var(--k-raised);color:var(--k-mute);
  font:700 9px/1 var(--k-f-ui);font-style:normal;text-transform:none;letter-spacing:0;
  cursor:help;-webkit-user-select:none;user-select:none;transition:color .14s,border-color .14s}
.k-info:hover,.k-info:focus-visible{color:var(--k-cyan);border-color:var(--k-cyan);outline:none}
.k-info::after{content:attr(data-tip);position:absolute;top:calc(100% + 9px);left:50%;z-index:60;
  transform:translateX(-50%) translateY(-3px);width:max-content;max-width:220px;padding:8px 10px;
  border-radius:var(--k-r-m);background:var(--k-void);border:1px solid var(--k-line-lit);
  box-shadow:0 12px 32px rgba(0,0,0,.55);
  font:500 11px/1.5 var(--k-f-ui);letter-spacing:0;text-transform:none;text-align:left;
  color:var(--k-dim);white-space:normal;opacity:0;pointer-events:none;
  transition:opacity .14s ease,transform .14s ease}
.k-info::before{content:"";position:absolute;top:calc(100% + 4px);left:50%;z-index:60;
  width:8px;height:8px;background:var(--k-void);
  border-left:1px solid var(--k-line-lit);border-top:1px solid var(--k-line-lit);
  transform:translateX(-50%) rotate(45deg);opacity:0;transition:opacity .14s ease}
.k-info.up::after{top:auto;bottom:calc(100% + 9px);transform:translateX(-50%) translateY(3px)}
.k-info.up::before{top:auto;bottom:calc(100% + 4px);
  border-left:0;border-top:0;border-right:1px solid var(--k-line-lit);border-bottom:1px solid var(--k-line-lit)}
.k-info:hover::after,.k-info:focus-visible::after{opacity:1;transform:translateX(-50%) translateY(0)}
.k-info:hover::before,.k-info:focus-visible::before{opacity:1}

/* --- responsive yardımcıları (mobil ≤768px: sıkışma yerine temiz yığın) ---
   .k-resp  : yatay grid/flex; ≤768px'te tek sütun / flex-column olur.
   .k-hide-sm / .k-only-sm : mobilde gizle / yalnız mobilde göster. */
.k-resp{display:flex;flex-wrap:wrap;gap:10px}
@media (max-width:768px){
  .k-resp{flex-direction:column;align-items:stretch}
  .k-resp > *{width:100% !important;min-width:0 !important;flex:1 1 auto !important}
  .k-hide-sm{display:none !important}
}
@media (min-width:769px){ .k-only-sm{display:none !important} }

@media (prefers-reduced-motion:reduce){*,*::before,*::after{
  animation-duration:.001ms !important;transition-duration:.001ms !important}}
"""


def kit_css():
    """HUD `<style>` bloğunun başına eklenecek ortak stil metni (etiketsiz)."""
    return ":root{color-scheme:dark;" + _root_vars() + "}" + _PRIMITIVES


def info(tip, *, up=False):
    """Jargon baloncuğu işaretlemesi — `<span class="k-info" …>i</span>`.

    `tip` düz metin; çift tırnak/`<` kaçırılır. `up=True` baloncuğu yukarı açar
    (öğenin altında yer yoksa). `.k-info` stili `kit_css()` içinde gelir.
    """
    safe = (str(tip or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))
    cls = "k-info up" if up else "k-info"
    return f'<span class="{cls}" tabindex="0" role="note" aria-label="{safe}" data-tip="{safe}">i</span>'


def google_fonts_link():
    """Iframe <head>'ine — Inter + JetBrains Mono."""
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?'
        "family=Inter:wght@400;500;600;700;800&"
        'family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">'
    )
