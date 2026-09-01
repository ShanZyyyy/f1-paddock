# -*- coding: utf-8 -*-
"""Formula Paddock marka işareti — TEK KAYNAK.

**Apeks köşesi**: düz giriş düzlüğü → köşe → düz çıkış düzlüğü, köşenin içinde
kırmızı apeks noktası (pilotun kıvrımı öptüğü yer). Ana sayfadaki kendini çizen
turun / Telemetri'deki pist-dominasyonu haritasının küçük kardeşi — "yarış
çizgisi" fikri, kullanıcının seçtiği yön.

İki eleman:
  1. pist köşesi — `currentColor`, her zemine oturur
  2. apeks noktası — marka kırmızısı (sabit)

Bu modül Streamlit'e / başka hiçbir şeye bağımlı değildir — saf string üretir,
izole test edilebilir. Önceki dört inline-SVG kopyası (hero.py / ui.py ×2 /
theme.py) artık buradan beslenir.
"""

RED = "#e10600"          # marka kırmızısı — apeks noktası + wordmark "PADDOCK"
ACCENT = "#38e1d0"       # turkuaz — favicon / tek-renk bağlamlarda köşe rengi
INK_DARK = "#0c1016"     # favicon karo zemini

# Apeks köşesi. viewBox 0 0 32 32; ölçek CSS width/height ile.
# Yatay giriş düzlüğü (alt) → çeyrek dönüş → dikey çıkış düzlüğü (sağ-üst).
_PATH = "M4 25L12 25Q22 25 22 15L22 4"
_APEX = (16.7, 20.7)    # apeks — köşenin iç kenarı, çizginin hemen yanı
_APEX_R = 2.9
_SW = 3.7               # varsayılan çizgi kalınlığı


def _svg(line, dot, sw, *, xmlns=True, extra="", cls="", title=""):
    ns = " xmlns='http://www.w3.org/2000/svg'" if xmlns else ""
    c = f" class='{cls}'" if cls else ""
    role = f" role='img' aria-label='{title}'" if title else " aria-hidden='true'"
    return (
        f"<svg{c}{ns} viewBox='0 0 32 32' fill='none'{role}>{extra}"
        f"<path d='{_PATH}' stroke='{line}' stroke-width='{sw}' "
        f"stroke-linecap='round' stroke-linejoin='round'/>"
        f"<circle cx='{_APEX[0]}' cy='{_APEX[1]}' r='{_APEX_R}' fill='{dot}'/>"
        f"</svg>"
    )


def mark_svg(*, line="currentColor", dot=RED, sw=_SW, cls="", title="Formula Paddock"):
    """Satır-içi <svg> — DOM'a basılır (st.markdown). `line` CSS değişkeni de
    olabilir (`var(--fp-text)`), data-uri değil gerçek düğüm."""
    return _svg(line, dot, sw, cls=cls, title=title)


def _data_uri(svg):
    safe = (svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23")
            .replace('"', "'").replace(" ", "%20"))
    return f"data:image/svg+xml,{safe}"


def mark_data_uri(*, line=ACCENT, dot=RED, sw=_SW):
    """CSS `url()` için % kaçışlı SVG data-uri. `currentColor` burada ÇALIŞMAZ —
    somut renk ver."""
    return _data_uri(_svg(line, dot, sw))


# Favicon: yuvarlak köşeli koyu karo + turkuaz çizgi + kırmızı apeks.
FAVICON_SVG = _svg(
    ACCENT, RED, 4.3, xmlns=True, title="Formula Paddock",
    extra=f"<rect width='32' height='32' rx='7' fill='{INK_DARK}'/>",
)


def favicon_link_tag():
    """`<link rel=icon>` — <head>'e değil body'ye enjekte edilse de tarayıcılar
    onurlar. Streamlit `page_icon`'un üstüne biner (vektör, ikili varlık yok)."""
    return f"<link rel='icon' type='image/svg+xml' href='{_data_uri(FAVICON_SVG)}'>"
