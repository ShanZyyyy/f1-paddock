# -*- coding: utf-8 -*-
"""Formula Paddock — tasarım sistemi (tek kaynak).

F1 TV yayın grafiği yönü. Bütün renkler, tipografi ve bileşen sınıfları
burada tanımlanır; sayfalar kendi ``<style>`` bloğunu YAZMAZ.

Bu modül Streamlit'e bağımlı değildir — saf string üretir, izole test edilebilir.
`design/preview.html` bu jetonların birebir görsel karşılığıdır.

Performans: `page_style` / `shell_style` / `hud_iframe_style` her Streamlit
rerun'ında çağrılır ama girdileri sabittir (yalnız `light` bool). Sonuçlar
`lru_cache` ile bir kez üretilir — rerun başına kilobaytlarca string birleştirme
tekrarı önlenir.
"""

from functools import lru_cache

# =====================================================================
# RENK JETONLARI
# =====================================================================
TOKENS = {
    # Zemin katmanları (arkadan öne)
    "bg-0": "#07090d",
    "bg-1": "#0c1016",
    "bg-2": "#11161f",
    "bg-3": "#161d28",
    "bg-4": "#1e2836",
    # Çizgi / kenar
    "line": "#26313f",
    "line-soft": "#1b2330",
    # Metin
    "text": "#f2f5f8",
    "text-dim": "#9fb0c0",
    "text-mute": "#63748a",
    # Marka & durum
    "red": "#e10600",
    "red-bright": "#ff1801",
    "cyan": "#38e1d0",
    "amber": "#f5c33b",
    "green": "#4ade80",
    "purple": "#b98bff",
    "pink": "#ff5c8a",
}

# Açık tema (isteğe bağlı). Koyu = varsayılan, yayın grafiği koyu çalışır.
TOKENS_LIGHT = {
    "bg-0": "#dfe7f0",
    "bg-1": "#e9eff6",
    "bg-2": "#f6f9fc",
    "bg-3": "#ffffff",
    "bg-4": "#eef3f9",
    "line": "#c3d1e0",
    "line-soft": "#d5deea",
    "text": "#0f1b2a",
    "text-dim": "#3c5064",
    "text-mute": "#66788c",
    "red": "#d10600",
    "red-bright": "#e8002d",
    "cyan": "#0f9b8e",
    "amber": "#b7861a",
    "green": "#1f9d57",
    "purple": "#7a4fd0",
    "pink": "#d1477e",
}

# 2026 takım renkleri. Anahtarlar canonical takım adıyla eşleşir.
TEAM_COLORS = {
    "Mercedes": "#00d7b6",
    "Ferrari": "#e8002d",
    "Red Bull Racing": "#3671c6",
    "McLaren": "#ff8000",
    "Aston Martin": "#229971",
    "Alpine": "#0093cc",
    "Williams": "#64c4ff",
    "Racing Bulls": "#6692ff",
    "Haas F1 Team": "#b6babd",
    "Audi": "#00e701",
    "Cadillac": "#e0c04a",
}
TEAM_COLOR_FALLBACK = "#8a9bb0"

# Lastik hamuru renkleri (rozet için)
TYRE_COLORS = {
    "SOFT": ("#3a0f12", "#ff5b5b"),
    "MEDIUM": ("#3a3410", "#ffe14d"),
    "HARD": ("#2a2f36", "#e7edf3"),
    "INTERMEDIATE": ("#0f2a17", "#4ade80"),
    "WET": ("#0e2436", "#5db4ff"),
}

# Durum tonları (data_state / rozet)
TONE_COLORS = {
    "info": "cyan",
    "success": "green",
    "warning": "amber",
    "error": "pink",
    "neutral": "text-dim",
}

# =====================================================================
# TİPOGRAFİ
# =====================================================================
FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Saira+Condensed:wght@500;600;700;800&'
    'family=Saira:wght@400;500;600;700&'
    'family=Antonio:wght@400;600;700&'
    'family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">'
)

F_DISPLAY = "'Saira Condensed','Arial Narrow',system-ui,sans-serif"
F_BODY = "'Saira',system-ui,-apple-system,'Segoe UI',sans-serif"
F_MONO = "'JetBrains Mono','Consolas',ui-monospace,monospace"
F_XCOND = "'Antonio','Saira Condensed','Arial Narrow',sans-serif"


def team_color(name):
    """Takım adından marka rengi; bilinmiyorsa nötr fallback."""
    if not name:
        return TEAM_COLOR_FALLBACK
    key = str(name).strip()
    if key in TEAM_COLORS:
        return TEAM_COLORS[key]
    low = key.lower()
    for team, colour in TEAM_COLORS.items():
        if team.lower() in low or low in team.lower():
            return colour
    return TEAM_COLOR_FALLBACK


def tone_hex(tone, light=False):
    """Durum tonunun ham hex karşılığı."""
    token = TONE_COLORS.get(tone, "cyan")
    table = TOKENS_LIGHT if light else TOKENS
    return table.get(token, table["cyan"])


def _palette_vars(table):
    """Sadece renk degiskenleri (tema ile degisen)."""
    parts = [f"--fp-{key}:{value}" for key, value in table.items()]
    # Eski degisken adlari — hala ~40 satir inline HUD markup bunlari kullaniyor.
    parts += [
        f"--fp-page:{table['bg-1']}", f"--fp-page2:{table['bg-0']}",
        f"--fp-panel:{table['bg-2']}", f"--fp-panel2:{table['bg-3']}",
        f"--fp-muted:{table['text-dim']}",
    ]
    return ";".join(parts)


@lru_cache(maxsize=1)
def _static_vars():
    """Tema ile degismeyen: takim renkleri, font, olcek."""
    parts = [f"--t-{slug}:{hexv}" for slug, hexv in _team_slug_vars().items()]
    parts += [
        f"--fp-f-display:{F_DISPLAY}", f"--fp-f-body:{F_BODY}", f"--fp-f-mono:{F_MONO}",
        f"--fp-f-x:{F_XCOND}",
        "--fp-edge:3px", "--fp-r-sm:3px", "--fp-r-md:5px", "--fp-r-lg:8px",
        "--fp-shadow:0 12px 30px rgba(0,0,0,.45)",
        "--fp-glow:rgba(56,225,208,.10)", "--fp-grid:rgba(120,140,160,.05)",
    ]
    return ";".join(parts)


@lru_cache(maxsize=2)
def _root_vars(light=False):
    """Tek tema (hud_iframe_style icin). Sayfa CSS'i _root_vars_dual kullanir."""
    table = TOKENS_LIGHT if light else TOKENS
    return ":root{" + _palette_vars(table) + ";" + _static_vars() + "}"


@lru_cache(maxsize=1)
def _root_vars_dual():
    """Iki paleti birden yayar. Istemci :root[data-fp-theme] ile aninda gecer."""
    return (
        ":root{color-scheme:dark;" + _palette_vars(TOKENS) + ";" + _static_vars() + "}"
        + ':root[data-fp-theme="light"]{color-scheme:light;' + _palette_vars(TOKENS_LIGHT) + "}"
    )


def _team_slug_vars():
    slugs = {
        "mercedes": "Mercedes", "ferrari": "Ferrari", "redbull": "Red Bull Racing",
        "mclaren": "McLaren", "aston": "Aston Martin", "alpine": "Alpine",
        "williams": "Williams", "rb": "Racing Bulls", "haas": "Haas F1 Team",
        "audi": "Audi", "cadillac": "Cadillac",
    }
    return {slug: TEAM_COLORS[team] for slug, team in slugs.items()}


# =====================================================================
# GLOBAL CSS — bileşen sınıfları
# =====================================================================
# Streamlit kabugu — SADECE tam page_style'da (tum sayfa gecince). Gecis
# doneminde eski sayfa CSS'iyle catismasin diye shell_style buna dokunmaz.
_SHELL_CHROME_CSS = r"""
.block-container{padding-top:2.4rem;max-width:1180px}
body,[data-testid="stMarkdownContainer"]{font-family:var(--fp-f-body)}
h1,h2,h3,h4{font-family:var(--fp-f-display);letter-spacing:.02em}
a{color:var(--fp-cyan)}
"""

# .fp-* bilesen siniflari — hem shell_style hem page_style bunu iceriir.
_FP_COMPONENTS_CSS = r"""
/* ---- kırıntı yolu (breadcrumb) ---- */
.fp-crumb{display:flex;flex-wrap:wrap;align-items:center;gap:.15rem;margin:2px 0 10px;
  font:600 10.5px/1 var(--fp-f-mono);letter-spacing:.08em;text-transform:uppercase}
.fp-crumb a{color:var(--fp-text-mute);text-decoration:none;transition:color .12s ease}
.fp-crumb a:hover{color:var(--fp-cyan)}
.fp-crumb span{color:var(--fp-text-dim)}
.fp-crumb i{color:var(--fp-line);font-style:normal;padding:0 .35rem}

/* ---- site ayağı (footer) ---- */
.fp-foot{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem 1.2rem;
  margin:44px 0 8px;padding-top:16px;border-top:1px solid var(--fp-line-soft);
  font:500 11px/1.4 var(--fp-f-mono);letter-spacing:.04em;color:var(--fp-text-mute)}
.fp-foot b{color:var(--fp-red);font-weight:700}
.fp-foot .lk{display:flex;gap:1rem;flex:1}
.fp-foot .lk a{color:var(--fp-text-dim);text-decoration:none}
.fp-foot .lk a:hover{color:var(--fp-cyan)}
.fp-foot .yr{margin-left:auto;opacity:.7}
@media(max-width:620px){.fp-foot .yr{margin-left:0}}

/* ---- eyebrow / bölüm başlığı ---- */
.fp-eyebrow{font-weight:700;font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--fp-text-mute)}
.fp-section{font-family:var(--fp-f-display);font-weight:700;font-size:19px;letter-spacing:.05em;text-transform:uppercase;
  padding-left:10px;border-left:var(--fp-edge) solid var(--fp-red);margin:6px 0 2px}

/* ---- page header ---- */
.fp-page-header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
  padding-bottom:12px;border-bottom:1px solid var(--fp-line);margin-bottom:6px}
.fp-page-header h1{font-weight:800;font-size:32px;text-transform:uppercase;line-height:1;margin:6px 0 0}
.fp-page-header .sub{color:var(--fp-text-dim);font-size:13px;margin-top:5px;max-width:62ch}
.fp-badge{font-family:var(--fp-f-mono);font-size:11px;font-weight:700;letter-spacing:.08em;
  padding:5px 10px;border-radius:var(--fp-r-sm);white-space:nowrap;
  background:var(--fp-bg-3);border:1px solid var(--fp-line);color:var(--fp-text-dim)}
.fp-badge.live{background:color-mix(in srgb,var(--fp-green) 12%,transparent);border-color:color-mix(in srgb,var(--fp-green) 40%,transparent);color:var(--fp-green)}
.fp-badge.wait{background:color-mix(in srgb,var(--fp-amber) 10%,transparent);border-color:color-mix(in srgb,var(--fp-amber) 35%,transparent);color:var(--fp-amber)}

/* ---- pit-duvarı ortak dili: pah köşe + nokta-matris / telemetri ızgarası ----
   --fp-cham : köşe kesme derinliği   --fp-dot : nokta deseni   --fp-grid : ızgara */
:root{
  --fp-cham:13px;
  --fp-dot:radial-gradient(rgba(120,140,160,.10) 1px,transparent 1.5px);
  --fp-dot-size:13px 13px;
  --fp-tgrid:linear-gradient(rgba(120,140,160,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(120,140,160,.05) 1px,transparent 1px);
  --fp-tgrid-size:32px 32px;
}
.fp-cut{clip-path:polygon(var(--fp-cham) 0,100% 0,100% calc(100% - var(--fp-cham)),
  calc(100% - var(--fp-cham)) 100%,0 100%,0 var(--fp-cham))}

/* ---- HUD kart (pit-duvarı kanalı) ---- */
.fp-hud{position:relative;padding:15px 17px;
  background-color:var(--fp-bg-2);background-image:var(--fp-dot);background-size:var(--fp-dot-size);
  clip-path:polygon(var(--fp-cham) 0,100% 0,100% calc(100% - var(--fp-cham)),calc(100% - var(--fp-cham)) 100%,0 100%,0 var(--fp-cham));
  box-shadow:inset 0 0 0 1px var(--fp-line), inset 3px 0 0 var(--accent,var(--fp-red))}
.fp-hud .lbl{font-family:var(--fp-f-mono);font-weight:700;font-size:10px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--fp-text-mute);display:flex;align-items:center;gap:7px}
.fp-hud .lbl::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent,var(--fp-red));
  box-shadow:0 0 0 3px color-mix(in srgb,var(--accent,var(--fp-red)) 20%,transparent);flex:0 0 auto}
.fp-hud .val{font-family:var(--fp-f-display);font-weight:700;font-size:22px;text-transform:uppercase;letter-spacing:.02em;margin-top:9px;color:var(--fp-text)}
.fp-hud .cpy{color:var(--fp-text-dim);font-size:13px;margin-top:8px}

/* ---- pit ızgarası (yan yana widget'lar — yeni fp_ui.pit_grid) ---- */
.fp-pit{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:6px 0 2px}

/* ---- Oyun Merkezi kartları — pit-duvarı diline uydur (--gc: oyun rengi) ---- */
.hud-card.game-card-v24,.hud-card.game-choice-v19{
  border:none !important;border-radius:0 !important;
  background-color:var(--fp-bg-2) !important;background-image:var(--fp-dot) !important;background-size:var(--fp-dot-size) !important;
  clip-path:polygon(var(--fp-cham) 0,100% 0,100% calc(100% - var(--fp-cham)),calc(100% - var(--fp-cham)) 100%,0 100%,0 var(--fp-cham));
  box-shadow:inset 0 0 0 1px var(--fp-line), inset 3px 0 0 var(--gc,var(--fp-red)) !important;
  transform:none !important}
.hud-card.game-card-v24:hover,.hud-card.game-choice-v19:hover{
  box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--gc,var(--fp-red)) 45%,var(--fp-line)), inset 3px 0 0 var(--gc,var(--fp-red)) !important}
.hud-card.game-card-v24 .hud-label,.hud-card.game-choice-v19 .hud-label{
  font-family:var(--fp-f-mono);letter-spacing:.16em;color:var(--gc,var(--fp-text-mute)) !important}

/* ---- eski kartlar (.hud-card / .driver-card / .news-card / .metric-card) ----
   Tek dile getir: pahlı köşe + nokta zemin + iç çerçeve. Satır-içi accent
   border'lar (border-left/top) çoğunlukla görünür kalır (yalnız kesik köşe kırpılır);
   drop-shadow clip-path ile bozulacağı için iç çerçeveye çevrildi. */
.hud-card,.metric-card,.driver-card,.news-card{
  border-radius:0 !important;
  clip-path:polygon(11px 0,100% 0,100% calc(100% - 11px),calc(100% - 11px) 100%,0 100%,0 11px);
  background:var(--fp-bg-2) !important;
  background-image:var(--fp-dot) !important;background-size:var(--fp-dot-size) !important;
  box-shadow:inset 0 0 0 1px var(--fp-line) !important}
.hud-card:hover,.metric-card:hover,.driver-card:hover{transform:none !important;box-shadow:inset 0 0 0 1px var(--fp-line) !important}

/* ---- stat tile ---- */
.fp-tile{position:relative;background-color:var(--fp-bg-2);
  background-image:var(--fp-dot);background-size:11px 11px;
  clip-path:polygon(10px 0,100% 0,100% calc(100% - 10px),calc(100% - 10px) 100%,0 100%,0 10px);
  box-shadow:inset 0 0 0 1px var(--fp-line-soft), inset 3px 0 0 var(--accent,var(--fp-cyan));
  padding:12px 15px;height:100%;min-height:96px;display:flex;flex-direction:column;justify-content:flex-start}
.fp-tile .lbl{font-family:var(--fp-f-mono);font-size:10px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:var(--fp-text-mute);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fp-tile .val{font-family:var(--fp-f-mono);font-weight:700;font-size:18px;letter-spacing:-.01em;
  margin-top:7px;color:var(--fp-text);line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fp-tile .val.txt{font-family:var(--fp-f-display);font-size:15.5px;letter-spacing:.01em;text-transform:uppercase;
  line-height:1.15;white-space:normal;overflow:hidden;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.fp-tile .sub{font-size:11px;color:var(--fp-text-dim);margin-top:auto;padding-top:4px;line-height:1.3;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}

/* ---- data-state (pit-duvarı konsolu) ---- */
.fp-state{position:relative;padding:14px 18px;
  background-color:var(--fp-bg-2);background-image:var(--fp-tgrid);background-size:var(--fp-tgrid-size);
  clip-path:polygon(var(--fp-cham) 0,100% 0,100% calc(100% - var(--fp-cham)),calc(100% - var(--fp-cham)) 100%,0 100%,0 var(--fp-cham));
  box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--sc,var(--fp-cyan)) 42%,var(--fp-line)), inset 3px 0 0 var(--sc,var(--fp-cyan))}
.fp-state .st{font-family:var(--fp-f-mono);font-weight:700;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--sc,var(--fp-cyan));display:flex;align-items:center;gap:7px}
.fp-state .st::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--sc,var(--fp-cyan));
  box-shadow:0 0 0 3px color-mix(in srgb,var(--sc,var(--fp-cyan)) 22%,transparent);flex:0 0 auto}
.fp-state .sc{font-size:13px;color:var(--fp-text-dim);margin-top:7px;line-height:1.5}
.fp-state::after{content:"";position:absolute;right:8px;bottom:8px;width:12px;height:12px;
  border-right:2px solid var(--sc,var(--fp-cyan));border-bottom:2px solid var(--sc,var(--fp-cyan));opacity:.5}

/* ---- result hero (yarış bitti) ---- */
.fp-result{background:
    radial-gradient(120% 130% at 8% 0%, color-mix(in srgb,var(--tc) 22%,transparent), transparent 60%),
    linear-gradient(160deg,var(--fp-bg-3),var(--fp-bg-2));
  border:1px solid var(--fp-line);border-left:5px solid var(--tc);border-radius:var(--fp-r-md);
  padding:16px 24px;box-shadow:var(--fp-shadow)}
.fp-result .eb{font-weight:700;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fp-text-mute)}
.fp-result .nm{font-family:var(--fp-f-display);font-weight:800;font-size:36px;line-height:1;text-transform:uppercase;margin-top:8px}
.fp-result .nm b{color:var(--tc)}
.fp-result .row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:12px}
.fp-result .team{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--fp-text-dim)}
.fp-result .gap{font-family:var(--fp-f-mono);font-weight:700;font-size:13px;padding:6px 11px;border-radius:var(--fp-r-sm);
  background:var(--fp-bg-0);border:1px solid var(--fp-line);color:var(--fp-text)}
.fp-result .gap span{color:var(--fp-text-mute);font-weight:500;margin-right:6px}
.fp-result .next{font-family:var(--fp-f-mono);font-size:12px;color:var(--fp-text-dim)}
.fp-result .next b{color:var(--fp-text)}

/* ---- news card ---- */
.fp-news{background:var(--fp-bg-2);border:1px solid var(--fp-line-soft);border-radius:var(--fp-r-md);
  overflow:hidden;display:flex;flex-direction:column;height:100%}
.fp-news .ph{height:110px;background:linear-gradient(135deg,#1a2433,#101722);display:flex;
  align-items:center;justify-content:center;font-family:var(--fp-f-display);font-weight:800;
  color:#2b3a4d;font-size:24px;letter-spacing:.1em}
.fp-news .ph img{width:100%;height:100%;object-fit:cover}
.fp-news .bd{padding:12px 15px;display:flex;flex-direction:column;gap:6px;flex:1}
.fp-news .src{font-family:var(--fp-f-mono);font-size:10px;letter-spacing:.06em;color:var(--fp-red-bright);text-transform:uppercase}
.fp-news .hl{font-family:var(--fp-f-display);font-weight:700;font-size:15px;line-height:1.25}
.fp-news .ex{font-size:12px;color:var(--fp-text-dim);flex:1}
.fp-news .lk{font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--fp-cyan);text-decoration:none}

/* ---- mini not (seans ozeti vb.) ---- */
.fp-note{position:relative;padding:11px 14px;height:100%;font-size:12.5px;color:var(--fp-text-dim);line-height:1.5;
  background-color:var(--fp-bg-2);background-image:var(--fp-dot);background-size:12px 12px;
  clip-path:polygon(9px 0,100% 0,100% calc(100% - 9px),calc(100% - 9px) 100%,0 100%,0 9px);
  box-shadow:inset 0 0 0 1px var(--fp-line-soft), inset 3px 0 0 var(--nc,var(--fp-cyan))}
.fp-notes-grid{display:grid;grid-template-columns:repeat(var(--per,3),1fr);gap:10px;margin:4px 0 2px}
.fp-notes-grid .fp-note{height:100%}
@media(max-width:760px){.fp-notes-grid{grid-template-columns:1fr 1fr}}
@media(max-width:480px){.fp-notes-grid{grid-template-columns:1fr}}

/* ---- Streamlit yerel uyarıları (st.info / warning / success / error) ----
   Hepsi pit-duvarı kanalına dönüşür; renk = yarış bayrağı. */
[data-testid="stAlert"]{background:none !important;border:none !important;box-shadow:none !important;padding:0 !important}
[data-testid="stAlertContainer"]{
  --fp-flag:var(--fp-cyan);
  position:relative;border:none !important;border-radius:0 !important;
  background-color:var(--fp-bg-2) !important;background-image:var(--fp-dot) !important;background-size:var(--fp-dot-size) !important;
  clip-path:polygon(var(--fp-cham) 0,100% 0,100% calc(100% - var(--fp-cham)),calc(100% - var(--fp-cham)) 100%,0 100%,0 var(--fp-cham));
  box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--fp-flag) 40%,var(--fp-line)), inset 3px 0 0 var(--fp-flag) !important;
  color:var(--fp-text) !important;padding:13px 17px 13px 19px !important}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]){--fp-flag:var(--fp-amber)}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]){--fp-flag:var(--fp-green)}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]){--fp-flag:var(--fp-pink)}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]){--fp-flag:var(--fp-cyan)}
[data-testid="stAlertContainer"] p,[data-testid="stAlertContainer"] li{color:var(--fp-text) !important}
[data-testid="stAlertContainer"] [data-testid="stMarkdownContainer"]{color:var(--fp-text) !important}
/* köşe braketi (alt-sağ) */
[data-testid="stAlertContainer"]::after{content:"";position:absolute;right:7px;bottom:7px;width:12px;height:12px;
  border-right:2px solid var(--fp-flag);border-bottom:2px solid var(--fp-flag);opacity:.45;pointer-events:none}

/* ---- haber kart grid'i (st.columns yerine — satirdaki kartlar esit yukseklik) ---- */
.fp-news-grid{display:grid;grid-template-columns:repeat(var(--per,2),1fr);gap:14px;
  margin:6px 0 2px;align-items:stretch}
.fp-news-grid .fp-news{height:100%}
@media(max-width:760px){.fp-news-grid{grid-template-columns:1fr}}

/* =====================================================================
   MOBIL — telefon ekrani (<= 760px)
   ===================================================================== */
@media (max-width: 760px){
  .block-container{padding:1.4rem 0.9rem 3rem !important}
  .fp-page-header{flex-direction:column;align-items:flex-start;gap:8px}
  .fp-page-header h1{font-size:23px}
  .fp-page-header .sub{font-size:12px;max-width:100%}
  .fp-badge{align-self:flex-start}
  .fp-section{font-size:15px;letter-spacing:.04em}
  .fp-eyebrow{font-size:9.5px}

  .fp-hud{padding:13px}
  .fp-hud .val{font-size:18px}
  .fp-tile{padding:10px 12px}
  .fp-tile .val{font-size:15px}
  .fp-tile .val.txt{font-size:13px}

  .fp-result{padding:14px 15px;border-left-width:4px}
  .fp-result .nm{font-size:24px}
  .fp-result .eb{font-size:9.5px}
  .fp-result .row{gap:8px}
  .fp-result .gap,.fp-result .next{font-size:11px}

  .fp-news .ph{height:96px}
  .fp-news .hl{font-size:14px}

  h1{font-size:23px !important}
  h2{font-size:19px !important}
  h3{font-size:16px !important}

  /* HUD iframe'ler ve genis tablolar kendi icinde yatay kayar */
  [data-testid="stIFrame"]{max-width:100%}
  /* Streamlit kolonlari dar ekranda dikey yigilir; zorunlu min-width'i gevset */
  [data-testid="stHorizontalBlock"]{flex-wrap:wrap !important}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]{
    min-width:calc(50% - 0.5rem) !important;flex:1 1 calc(50% - 0.5rem) !important;
  }
}
@media (max-width: 480px){
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]{min-width:100% !important;flex-basis:100% !important}
  .fp-page-header h1{font-size:21px}
  .fp-result .nm{font-size:21px}
}
"""


@lru_cache(maxsize=2)
def page_style(light=False):
    """Tam tema — tum sayfalar gecince. `st.markdown(unsafe_allow_html=True)`."""
    return ("<style>" + _root_vars_dual() + _SHELL_CHROME_CSS
            + _FP_COMPONENTS_CSS + _SHELL_BG_CSS + _SIDEBAR_CSS + "</style>")


# =====================================================================
# SIDEBAR — slim rail
# =====================================================================
_SIDEBAR_CSS = r"""
section[data-testid="stSidebar"]{
  background:linear-gradient(180deg,var(--fp-bg-2),var(--fp-bg-1)) !important;
  border-right:1px solid var(--fp-line);
}
section[data-testid="stSidebar"] .block-container{padding-top:1rem}
section[data-testid="stSidebar"] *{color:var(--fp-text)}

/* marka kilidi */
.fp-brand{display:flex;align-items:center;gap:11px;padding:6px 4px 14px;margin-bottom:2px;border-bottom:1px solid var(--fp-line)}
.fp-brand .mark{flex:0 0 auto;width:36px;height:36px;border:1px solid var(--fp-line);border-radius:8px;
  background:var(--fp-bg-2) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath d='M13 11 L27 24 L13 37' fill='none' stroke='%23e10600' stroke-width='6.5' stroke-linecap='square'/%3E%3Cpath d='M24.5 15 L33.5 24 L24.5 33' fill='none' stroke='%23e10600' stroke-width='5' stroke-linecap='square' opacity='.5'/%3E%3C/svg%3E") center/34px 34px no-repeat}
.fp-brand .txt{font-family:var(--fp-f-display);font-weight:800;font-size:16.5px;letter-spacing:.045em;text-transform:uppercase;line-height:1.02}
.fp-brand .txt s{display:block;font-weight:600;font-size:9px;letter-spacing:.24em;color:var(--fp-text-mute);text-decoration:none;margin-top:4px}

/* bölüm etiketi */
.fp-nav-sec{font-size:9px;font-weight:700;letter-spacing:.2em;text-transform:uppercase;color:var(--fp-text-mute);
  padding:16px 6px 4px}

/* nav = st.button (key="nav_*") -> slim rail.
   Sadece .st-key-nav_* sarmaliyicilarini hedefler; expander ic butonlari (CTA)
   dokunulmaz. !important eski tema bloklarini yenmek icin. */
section[data-testid="stSidebar"] [class*="st-key-nav_"] div[data-testid="stButton"]{margin:0 0 1px !important}
section[data-testid="stSidebar"] [class*="st-key-nav_"] div[data-testid="stButton"] > button{
  width:100% !important;justify-content:flex-start !important;gap:11px !important;
  padding:9px 12px !important;min-height:0 !important;height:auto !important;
  background:transparent !important;border:none !important;
  border-left:var(--fp-edge) solid transparent !important;border-radius:0 !important;
  box-shadow:none !important;transform:none !important;
  font-family:var(--fp-f-display) !important;font-weight:600 !important;font-size:14px !important;
  letter-spacing:.02em;color:var(--fp-text-dim) !important;
  transition:background .12s ease,color .12s ease,border-color .12s ease !important;
}
section[data-testid="stSidebar"] [class*="st-key-nav_"] div[data-testid="stButton"] > button:hover{
  background:rgba(255,255,255,.05) !important;color:var(--fp-text) !important;border-left-color:transparent !important;
}
section[data-testid="stSidebar"] [class*="st-key-nav_"] div[data-testid="stButton"] > button span[data-testid="stIconMaterial"]{
  font-size:17px !important;opacity:.7;
}
/* aktif sayfa: type="primary" */
section[data-testid="stSidebar"] [class*="st-key-nav_"] div[data-testid="stButton"] > button[kind="primary"]{
  color:#fff !important;
  background:linear-gradient(90deg,color-mix(in srgb,var(--fp-red) 22%,transparent),transparent 72%) !important;
  border-left-color:var(--fp-red) !important;
}
section[data-testid="stSidebar"] [class*="st-key-nav_"] div[data-testid="stButton"] > button[kind="primary"] span[data-testid="stIconMaterial"]{
  opacity:1;color:var(--fp-red) !important;
}

/* sidebar expander (Telemetri Seans Ayarlari, Hizli Favori) — slim rail dili */
section[data-testid="stSidebar"] div[data-testid="stExpander"]{
  background:transparent !important;border:1px solid var(--fp-line) !important;
  border-left:var(--fp-edge) solid var(--fp-line) !important;
  border-radius:var(--fp-r-sm) !important;box-shadow:none !important;margin:2px 0 6px !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary,
section[data-testid="stSidebar"] div[data-testid="stExpander"] details > summary{
  font-family:var(--fp-f-display) !important;font-weight:600 !important;font-size:13px !important;
  letter-spacing:.03em !important;color:var(--fp-text-dim) !important;padding:10px 12px !important;
  background:transparent !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary:hover{color:var(--fp-text) !important}
section[data-testid="stSidebar"] div[data-testid="stExpander"][open]{
  border-left-color:var(--fp-red) !important;background:rgba(255,255,255,.02) !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] [data-testid="stExpanderDetails"]{
  padding:4px 10px 10px !important;
}
/* sidebar form kontrolleri */
section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] [data-baseweb="input"],
section[data-testid="stSidebar"] input{
  background:var(--fp-bg-2) !important;border-color:var(--fp-line) !important;
  border-radius:var(--fp-r-sm) !important;color:var(--fp-text) !important;
}
section[data-testid="stSidebar"] [data-testid="stNumberInputStepUp"],
section[data-testid="stSidebar"] [data-testid="stNumberInputStepDown"]{
  background:var(--fp-bg-3) !important;color:var(--fp-text-dim) !important;border-color:var(--fp-line) !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] [data-testid="stButton"] > button{
  border-radius:var(--fp-r-sm) !important;
}
/* nav DISI sidebar butonlari (select tetikleyicisi vb.): notr, mavi kenar yok */
section[data-testid="stSidebar"] [data-testid="stSelectbox"] button,
section[data-testid="stSidebar"] [data-baseweb="select"] button{
  border:1px solid var(--fp-line) !important;border-left-width:1px !important;
  background:var(--fp-bg-2) !important;color:var(--fp-text) !important;
  border-radius:var(--fp-r-sm) !important;box-shadow:none !important;transform:none !important;
}
"""

# ---- Uygulama arka plani (F1 TV — hareketli telemetri + canli pist) --------
# Buyuk pist animasyonu artik ayri bir inline SVG katmani: core.ui.background_fx()
# -> #fp-bgfx. Burada sadece izgara + hiz isigi (salt CSS pseudo-eleman).
_SHELL_BG_CSS = r"""
/* Sade koyu zemin — animasyonlu pist/izgara/hiz-isigi kaldirildi.
   Atmosfer artik yalnizca ana sayfadaki hero duman'inda. */
[data-testid="stAppViewContainer"],.stApp{
  background:
    radial-gradient(120% 78% at 85% -8%, color-mix(in srgb,var(--fp-red) 8%,transparent), transparent 55%),
    linear-gradient(180deg, var(--fp-bg-1), var(--fp-bg-0)) !important;
  background-attachment:fixed !important;
}
[data-testid="stHeader"]{background:transparent !important}
[data-testid="stAppViewContainer"] > .main,.stApp [data-testid="stMain"]{position:relative;z-index:1}
.stApp [data-testid="stMain"] .block-container{position:relative;z-index:1}

/* Sadece <style> iceren markdown kaplari gorunmez bosluk yaratiyordu — gizle */
.stElementContainer:has(> .stMarkdown [data-testid="stMarkdownContainer"] > style:only-child),
.stElementContainer:has(> [data-testid="stMarkdown"] > [data-testid="stMarkdownContainer"] > style:only-child){
  display:none !important;
}

/* animasyonlu arka plan katmani — st.markdown kabini nötrle, tam ekran + en arka */
.stElementContainer:has(#fp-bgfx),.stElementContainer:has(#fp-pagebg){
  position:static !important;height:0 !important;min-height:0 !important;margin:0 !important;
  padding:0 !important;overflow:visible !important;
}
#fp-bgfx{
  position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;
  overflow:hidden;contain:layout paint;opacity:.62;
  -webkit-mask-image:radial-gradient(150% 120% at 70% 20%,#000 32%,transparent 96%);
  mask-image:radial-gradient(150% 120% at 70% 20%,#000 32%,transparent 96%);
}
#fp-bgfx svg{width:100%;height:100%;display:block}

/* ---- iç sayfa arka planı (ana ekran hariç HER menü sayfası) ----
   Sabit, sakin bir F1 telemetri katmanı: silik pist tel-kafesi + ince ızgara
   + köşe ışıması. İçerik kolonunun arkasında iyice soluk (radial maske). */
#fp-pagebg{
  position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;
  overflow:hidden;contain:layout paint;
  -webkit-mask-image:radial-gradient(125% 105% at 50% 32%,rgba(0,0,0,.13) 0%,rgba(0,0,0,.5) 52%,#000 82%);
  mask-image:radial-gradient(125% 105% at 50% 32%,rgba(0,0,0,.13) 0%,rgba(0,0,0,.5) 52%,#000 82%);
}
#fp-pagebg::before{
  content:"";position:absolute;inset:0;
  background:
    radial-gradient(46% 40% at 88% 4%, color-mix(in srgb,var(--fp-red) 16%,transparent), transparent 70%),
    radial-gradient(40% 46% at 6% 98%, color-mix(in srgb,var(--fp-cyan) 8%,transparent), transparent 72%);
}
#fp-pagebg svg{position:absolute;inset:0;width:100%;height:100%;display:block}
"""


# ---- Canli pist arka plani (inline SVG — kendini cizen tur + iki arac) -----
# st.markdown(unsafe_allow_html=True) -> DOMPurify SVG + SMIL'e izin verir.
# viewBox 1600x900, tek kapali devre. Renk: cyan pist, cyan+kirmizi arac.
_FP_TRACK_D = (
    "M240 720C240 560 300 470 430 470L820 470C940 470 980 400 980 320"
    "C980 230 910 190 820 190L560 190C470 190 450 120 530 95C640 62 820 78 1010 78"
    "L1240 78C1400 78 1480 180 1480 350C1480 500 1390 560 1250 578L1030 600"
    "C950 608 935 665 1000 700C1075 740 1230 726 1330 758C1440 792 1450 862 1320 862"
    "L420 862C290 862 240 800 240 720Z"
)
_BG_FX_HTML = (
    "<div id='fp-bgfx' aria-hidden='true'>"
    "<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' "
    "viewBox='0 0 1600 900' preserveAspectRatio='xMidYMid slice'>"
    "<defs>"
    f"<path id='fpTrack' d='{_FP_TRACK_D}'/>"
    "<filter id='fpGlow' x='-60%' y='-60%' width='220%' height='220%'>"
    "<feGaussianBlur stdDeviation='6' result='b'/><feMerge><feMergeNode in='b'/><feMergeNode in='SourceGraphic'/></feMerge>"
    "</filter>"
    "<style>"
    "#fp-bgfx .trk{fill:none;stroke:#2ee6c9}"
    "#fp-bgfx .ghost{stroke:#2ee6c9;opacity:.2;stroke-width:2.5}"
    "#fp-bgfx .pulse{opacity:.65;stroke-width:3.5;stroke-linecap:round;"
    "stroke-dasharray:150 3370;animation:fpChase 7s linear infinite}"
    "#fp-bgfx .pulse2{opacity:.38;stroke:#e10600;stroke-width:2.5;stroke-linecap:round;"
    "stroke-dasharray:80 3440;animation:fpChase 11s linear infinite reverse}"
    "@keyframes fpChase{to{stroke-dashoffset:-3520}}"
    "#fp-bgfx .grid{stroke:#2ee6c9;opacity:.05;stroke-width:1}"
    "#fp-bgfx .sf{stroke:#f2f5f8;opacity:.28;stroke-width:4}"
    "</style>"
    "</defs>"
    # ince ic izgara
    "<g class='grid'>"
    + "".join(f"<line x1='{x}' y1='0' x2='{x}' y2='900'/>" for x in range(0, 1601, 80))
    + "".join(f"<line x1='0' y1='{y}' x2='1600' y2='{y}'/>" for y in range(0, 901, 80))
    + "</g>"
    "<use xlink:href='#fpTrack' class='trk ghost'/>"
    "<use xlink:href='#fpTrack' class='trk pulse'/>"
    "<use xlink:href='#fpTrack' class='trk pulse2'/>"
    # start/finish
    "<line class='sf' x1='240' y1='700' x2='240' y2='740'/>"
    # arac 1 (cyan)
    "<g filter='url(#fpGlow)'>"
    "<circle r='7' fill='#8ff7e9'>"
    "<animateMotion dur='13s' repeatCount='indefinite' rotate='auto'>"
    "<mpath xlink:href='#fpTrack'/></animateMotion></circle></g>"
    # arac 2 (kirmizi, farkli hiz -> tur atar)
    "<g filter='url(#fpGlow)'>"
    "<circle r='6' fill='#ff5a4d'>"
    "<animateMotion dur='17s' begin='-4s' repeatCount='indefinite' rotate='auto'>"
    "<mpath xlink:href='#fpTrack'/></animateMotion></circle></g>"
    "</svg></div>"
)


# ---- İç sayfa arka planı (ana ekran hariç her menü sayfası) — SAKİN sürüm ----
# Aynı devre şekli, ama hareketsiz tel-kafes + tek yavaş ışık nabzı. Veri
# okurken dikkat dağıtmasın diye düşük opaklık; #fp-pagebg maskesi merkezi soluklaştırır.
PAGE_BG_HTML = (
    "<div id='fp-pagebg' aria-hidden='true'>"
    "<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' "
    "viewBox='0 0 1600 900' preserveAspectRatio='xMidYMid slice'>"
    "<defs>"
    f"<path id='fpPageTrack' d='{_FP_TRACK_D}'/>"
    "<style>"
    "#fp-pagebg .g{stroke:#3a5570;opacity:.16;stroke-width:1}"
    "#fp-pagebg .trk{fill:none;stroke:#2ee6c9;opacity:.13;stroke-width:2.5;stroke-linejoin:round}"
    "#fp-pagebg .pulse{fill:none;stroke:#2ee6c9;opacity:.4;stroke-width:3;stroke-linecap:round;"
    "stroke-dasharray:120 3400;animation:fpPageChase 22s linear infinite}"
    "@keyframes fpPageChase{to{stroke-dashoffset:-3520}}"
    "#fp-pagebg .sf{stroke:#f2f5f8;opacity:.14;stroke-width:4}"
    "@media(prefers-reduced-motion:reduce){#fp-pagebg .pulse{display:none}}"
    "</style>"
    "</defs>"
    "<g class='g'>"
    + "".join(f"<line x1='{x}' y1='0' x2='{x}' y2='900'/>" for x in range(0, 1601, 96))
    + "".join(f"<line x1='0' y1='{y}' x2='1600' y2='{y}'/>" for y in range(0, 901, 96))
    + "</g>"
    "<use xlink:href='#fpPageTrack' class='trk'/>"
    "<use xlink:href='#fpPageTrack' class='pulse'/>"
    "<line class='sf' x1='240' y1='700' x2='240' y2='740'/>"
    "</svg></div>"
)


# ---- ESKI SINIF KOPRUSU --------------------------------------------
# Faz 3 hizlandirici: her sayfanin markup'ini elle degistirmek yerine
# eski sinif adlarini (.hud-card, .metric-card, .stTabs ...) yeni palete
# bagliyoruz. Sayfa bazli is sadece baslik + yerlesim kaliyor.
_LEGACY_BRIDGE_CSS = r"""
.hud-card,.metric-card,.news-card,.driver-card,.career-panel-v28,.career-metric-v28{
  background:linear-gradient(160deg,var(--fp-bg-3),var(--fp-bg-2)) !important;
  border:1px solid var(--fp-line) !important;border-radius:var(--fp-r-md) !important;
  box-shadow:var(--fp-shadow) !important;
}
.hud-card:hover,.metric-card:hover{transform:none !important;border-color:var(--fp-line) !important;box-shadow:var(--fp-shadow) !important}
.hud-label,[data-testid="stCaptionContainer"]{color:var(--fp-text-mute) !important;letter-spacing:.14em}
.hud-value,.metric-card .value,.news-title{color:var(--fp-text) !important}
.history-copy,.driver-meta,.news-desc,.metric-card .title{color:var(--fp-text-dim) !important}
.new-badge,.term-badge{background:var(--fp-bg-4) !important;color:var(--fp-text-dim) !important;border:1px solid var(--fp-line) !important}

/* tablar — notr koyu, secili sekmede ince cyan alt-cizgi (az kirmizi) */
.stTabs [data-baseweb="tab-list"]{background:var(--fp-bg-2) !important;border:1px solid var(--fp-line) !important;
  border-radius:var(--fp-r-sm) !important;padding:4px !important;gap:2px !important;box-shadow:none !important}
.stTabs [data-baseweb="tab"]{color:var(--fp-text-dim) !important;border-radius:var(--fp-r-sm) !important;
  font-family:var(--fp-f-display) !important;font-weight:700 !important;letter-spacing:.04em}
.stTabs button[aria-selected="true"]{background:var(--fp-bg-4) !important;color:var(--fp-text) !important;
  border-radius:var(--fp-r-sm) !important}
/* secim gostergesi (React Aria) + eski baseweb highlight -> cyan, kirmizi degil */
.stTabs .react-aria-SelectionIndicator,
.stTabs [data-baseweb="tab-highlight"]{background:var(--fp-cyan) !important}
.stTabs [data-baseweb="tab-border"]{background:var(--fp-line) !important}

/* dataframe */
[data-testid="stDataFrame"],[data-testid="stTable"]{border:1px solid var(--fp-line) !important;border-radius:var(--fp-r-sm)}

/* metin butonlari (sayfa govdesi) — notr koyu, mavi degil */
.stApp div[data-testid="stButton"] > button{
  background:var(--fp-bg-2) !important;border:1px solid var(--fp-line) !important;
  color:var(--fp-text) !important;border-radius:var(--fp-r-sm) !important;box-shadow:none !important;
  font-family:var(--fp-f-body) !important;font-weight:600 !important;
}
.stApp div[data-testid="stButton"] > button:hover{border-color:var(--fp-red) !important;background:var(--fp-bg-3) !important}
.stApp div[data-testid="stButton"] > button[kind="primary"]{background:var(--fp-red) !important;border-color:var(--fp-red) !important;color:#fff !important}
"""


@lru_cache(maxsize=1)
def sidebar_style():
    return "<style>" + _SIDEBAR_CSS + "</style>"


@lru_cache(maxsize=2)
def shell_style(light=False):
    """Gecis donemi kabuk temasi: :root jetonlari + yeni arka plan + slim-rail
    sidebar + .fp-* bilesen siniflari (yeni sayfalarin kullandigi).
    Eski Streamlit-kabuk kurallari (_SHELL_CHROME_CSS) burada YOK — henuz
    eski sayfalar var. Dosyanin en sonunda cagrilir ki eski bloklari yensin."""
    return ("<style>" + _root_vars_dual() + _SHELL_BG_CSS + _LEGACY_BRIDGE_CSS
            + _FP_COMPONENTS_CSS + _SIDEBAR_CSS + "</style>")


# =====================================================================
# İZOLE HUD IFRAME — tema propagasyonu
# (eski hud_theme_override_css'in yerini alır)
# =====================================================================
@lru_cache(maxsize=2)
def hud_iframe_style(light=False):
    t = TOKENS_LIGHT if light else TOKENS
    scheme = "light" if light else "dark"
    return (
        f"html{{color-scheme:{scheme}}}"
        f"body{{margin:0;background:transparent;color:{t['text']};"
        f"font-family:{F_BODY}}}"
        f".fp-hud-shell{{width:100%;overflow:hidden}}"
        f".hud,.r,.panel,.summary,.card,.f1-hud,.f1-hud-shell,.racecenter-card,"
        f".rc-ticker,.rc-driver-box,.box,.tile{{"
        f"background:{t['bg-3']};color:{t['text']};border-color:{t['line']}}}"
        f".sub,.note,.meta,.time-label{{color:{t['text-mute']}}}"
        f".title,.head,.hero b,.time-num{{color:{t['text']}}}"
    )
