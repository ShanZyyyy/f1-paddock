# -*- coding: utf-8 -*-
"""Formula Paddock — tasarım sistemi (tek kaynak).

F1 TV yayın grafiği yönü. Bütün renkler, tipografi ve bileşen sınıfları
burada tanımlanır; sayfalar kendi ``<style>`` bloğunu YAZMAZ.

Bu modül Streamlit'e bağımlı değildir — saf string üretir, izole test edilebilir.
`design/preview.html` bu jetonların birebir görsel karşılığıdır.
"""

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
    'family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">'
)

F_DISPLAY = "'Saira Condensed','Arial Narrow',system-ui,sans-serif"
F_BODY = "'Saira',system-ui,-apple-system,'Segoe UI',sans-serif"
F_MONO = "'JetBrains Mono','Consolas',ui-monospace,monospace"


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


def _root_vars(light=False):
    table = TOKENS_LIGHT if light else TOKENS
    parts = [f"--fp-{key}:{value}" for key, value in table.items()]
    parts += [f"--t-{slug}:{hexv}" for slug, hexv in _team_slug_vars().items()]
    parts.append(f"--fp-f-display:{F_DISPLAY}")
    parts.append(f"--fp-f-body:{F_BODY}")
    parts.append(f"--fp-f-mono:{F_MONO}")
    # Ölçek
    parts += ["--fp-edge:3px", "--fp-r-sm:3px", "--fp-r-md:5px", "--fp-r-lg:8px",
              "--fp-shadow:0 12px 30px rgba(0,0,0,.45)"]
    return ":root{" + ";".join(parts) + "}"


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

/* ---- HUD kart ---- */
.fp-hud{background:linear-gradient(160deg,var(--fp-bg-3),var(--fp-bg-2));border:1px solid var(--fp-line);
  border-top:var(--fp-edge) solid var(--accent,var(--fp-red));border-radius:var(--fp-r-md);
  padding:16px;box-shadow:var(--fp-shadow)}
.fp-hud .lbl{font-weight:700;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--fp-text-mute)}
.fp-hud .val{font-family:var(--fp-f-display);font-weight:700;font-size:22px;text-transform:uppercase;letter-spacing:.02em;margin-top:6px}
.fp-hud .cpy{color:var(--fp-text-dim);font-size:13px;margin-top:8px}

/* ---- stat tile ---- */
.fp-tile{background:var(--fp-bg-2);border:1px solid var(--fp-line-soft);
  border-left:var(--fp-edge) solid var(--accent,var(--fp-cyan));border-radius:var(--fp-r-sm);
  padding:12px 16px;height:100%}
.fp-tile .lbl{font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--fp-text-mute)}
.fp-tile .val{font-family:var(--fp-f-mono);font-weight:700;font-size:18px;letter-spacing:-.01em;
  margin-top:6px;color:var(--fp-text);line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fp-tile .val.txt{font-family:var(--fp-f-display);font-size:16px;letter-spacing:.01em;
  text-transform:uppercase;white-space:normal;word-break:normal;overflow:visible}
.fp-tile .sub{font-size:11px;color:var(--fp-text-dim);margin-top:2px}

/* ---- data-state ---- */
.fp-state{border:1px solid var(--fp-line);border-left:var(--fp-edge) solid var(--sc,var(--fp-cyan));
  background:var(--fp-bg-2);border-radius:var(--fp-r-sm);padding:12px 16px}
.fp-state .st{font-weight:700;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--sc,var(--fp-cyan))}
.fp-state .sc{font-size:12.5px;color:var(--fp-text-dim);margin-top:5px}

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
.fp-note{border:1px solid var(--fp-line);border-left:var(--fp-edge) solid var(--nc,var(--fp-cyan));
  background:var(--fp-bg-2);border-radius:var(--fp-r-sm);padding:11px 14px;height:100%;
  font-size:12.5px;color:var(--fp-text-dim);line-height:1.5}
"""


def page_style(light=False):
    """Tam tema — tum sayfalar gecince. `st.markdown(unsafe_allow_html=True)`."""
    return ("<style>" + _root_vars(light) + _SHELL_CHROME_CSS
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
.fp-brand{display:flex;align-items:center;gap:9px;padding:4px 6px 12px;margin-bottom:2px;border-bottom:1px solid var(--fp-line)}
.fp-brand .mark{width:26px;height:16px;background:var(--fp-red);clip-path:polygon(0 0,100% 0,78% 100%,0 100%);flex:0 0 auto}
.fp-brand .txt{font-family:var(--fp-f-display);font-weight:800;font-size:13px;letter-spacing:.09em;text-transform:uppercase;line-height:1}
.fp-brand .txt s{display:block;font-weight:600;font-size:8.5px;letter-spacing:.22em;color:var(--fp-text-mute);text-decoration:none;margin-top:3px}

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

# ---- Uygulama arka plani (F1 TV — sakin, animasyonsuz) ---------------
_SHELL_BG_CSS = r"""
/* eski animasyonlu grid + yorunge halkasini kapat */
[data-testid="stAppViewContainer"]::before,
[data-testid="stAppViewContainer"]::after{content:none !important;display:none !important;animation:none !important}
[data-testid="stAppViewContainer"],.stApp{
  background:
    radial-gradient(120% 78% at 85% -8%, color-mix(in srgb,var(--fp-red) 9%,transparent), transparent 55%),
    linear-gradient(180deg, var(--fp-bg-1), var(--fp-bg-0)) !important;
  background-attachment:fixed !important;
}
[data-testid="stHeader"]{background:color-mix(in srgb,var(--fp-bg-1) 90%,transparent) !important}
"""

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

/* tablar — eski mavi gradyan yerine kirmizi */
.stTabs [data-baseweb="tab-list"]{background:var(--fp-bg-2) !important;border:1px solid var(--fp-line) !important;
  border-radius:var(--fp-r-sm) !important;padding:4px !important;gap:2px !important;box-shadow:none !important}
.stTabs [data-baseweb="tab"]{color:var(--fp-text-dim) !important;border-radius:var(--fp-r-sm) !important}
.stTabs [aria-selected="true"]{background:var(--fp-red) !important;color:#fff !important;
  border-radius:var(--fp-r-sm) !important;box-shadow:none !important}

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


def sidebar_style():
    return "<style>" + _SIDEBAR_CSS + "</style>"


def shell_style(light=False):
    """Gecis donemi kabuk temasi: :root jetonlari + yeni arka plan + slim-rail
    sidebar + .fp-* bilesen siniflari (yeni sayfalarin kullandigi).
    Eski Streamlit-kabuk kurallari (_SHELL_CHROME_CSS) burada YOK — henuz
    eski sayfalar var. Dosyanin en sonunda cagrilir ki eski bloklari yensin."""
    return ("<style>" + _root_vars(light) + _SHELL_BG_CSS + _LEGACY_BRIDGE_CSS
            + _FP_COMPONENTS_CSS + _SIDEBAR_CSS + "</style>")


# =====================================================================
# İZOLE HUD IFRAME — tema propagasyonu
# (eski hud_theme_override_css'in yerini alır)
# =====================================================================
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
