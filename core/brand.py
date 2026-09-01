# -*- coding: utf-8 -*-
"""Formula Paddock marka işareti — TEK KAYNAK.

**Pit tabelası**: pit duvarından araca tutulan sopalı sinyal tabelası — üstte
kısa sopa, altında yuvarlak köşeli pano, panonun içinde kırmızı bir "P"
(Paddock / Pit / Pozisyon). Küçük boyutta bile "motor sporu" okunur.

İki katman:
  1. sopa + pano — `currentColor`, her zemine oturur
  2. "P" — marka kırmızısı (sabit)

Bu modül Streamlit'e / başka hiçbir şeye bağımlı değildir — saf string üretir,
izole test edilebilir. Önceki dört inline-SVG kopyası (hero.py / ui.py ×2 /
theme.py) artık buradan beslenir.
"""

RED = "#e10600"          # marka kırmızısı — pano içindeki "P" + wordmark "PADDOCK"
ACCENT = "#38e1d0"       # turkuaz — kullanılmıyor (geriye dönük uyumluluk)
INK_DARK = "#0c1016"     # favicon karo zemini
BOARD_LIGHT = "#f2f5f8"  # favicon panosu (koyu karo üstünde)

# viewBox 0 0 32 32; ölçek CSS width/height ile.
_POLE = "M16 3V6.6"                                   # sopa
_BOARD = ("M5.5 6.6h21a3.3 3.3 0 0 1 3.3 3.3v14.5a3.3 3.3 0 0 1-3.3 3.3"
          "h-21a3.3 3.3 0 0 1-3.3-3.3v-14.5a3.3 3.3 0 0 1 3.3-3.3Z")
_P = "M13 22.4V11.6h4a2.8 2.8 0 0 1 0 5.6h-4"         # pano içindeki "P"
_SW = 2.8                # sopa + pano çizgi kalınlığı
_P_SW = 2.6             # "P" çizgi kalınlığı


def _svg(line, mark, sw, *, xmlns=True, extra="", board_fill="none", cls="", title=""):
    ns = " xmlns='http://www.w3.org/2000/svg'" if xmlns else ""
    c = f" class='{cls}'" if cls else ""
    role = f" role='img' aria-label='{title}'" if title else " aria-hidden='true'"
    board_stroke = "" if board_fill != "none" else (
        f" stroke='{line}' stroke-width='{sw}'")
    return (
        f"<svg{c}{ns} viewBox='0 0 32 32' fill='none'{role}>{extra}"
        f"<path d='{_POLE}' stroke='{line}' stroke-width='{sw}' stroke-linecap='round'/>"
        f"<path d='{_BOARD}' fill='{board_fill}'{board_stroke} stroke-linejoin='round'/>"
        f"<path d='{_P}' stroke='{mark}' stroke-width='{_P_SW}' "
        f"stroke-linecap='round' stroke-linejoin='round'/>"
        f"</svg>"
    )


def mark_svg(*, line="currentColor", dot=RED, sw=_SW, cls="", title="Formula Paddock"):
    """Satır-içi <svg> — DOM'a basılır (st.markdown). `line` CSS değişkeni de
    olabilir (`var(--fp-text)`), data-uri değil gerçek düğüm. `dot` = "P" rengi
    (geriye dönük ad)."""
    return _svg(line, dot, sw, cls=cls, title=title)


def _data_uri(svg):
    # Tırnakları da kaçır: hem href='…' hem url("…") bağlamında güvenli olsun.
    safe = (svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23")
            .replace('"', "%22").replace("'", "%27").replace(" ", "%20"))
    return f"data:image/svg+xml,{safe}"


def mark_data_uri(*, line="#9fb0c0", dot=RED, sw=_SW):
    """CSS `url()` için % kaçışlı SVG data-uri. `currentColor` burada ÇALIŞMAZ —
    somut renk ver."""
    return _data_uri(_svg(line, dot, sw))


# Favicon: yuvarlak köşeli koyu karo + açık pano + kırmızı "P".
FAVICON_SVG = _svg(
    BOARD_LIGHT, RED, 3.0, xmlns=True, title="Formula Paddock", board_fill=BOARD_LIGHT,
    extra=f"<rect width='32' height='32' rx='7' fill='{INK_DARK}'/>",
)


def favicon_link_tag():
    """`<link rel=icon>` — <head>'e değil body'ye enjekte edilse de tarayıcılar
    onurlar. Streamlit `page_icon`'un üstüne biner (vektör, ikili varlık yok)."""
    return f"<link rel='icon' type='image/svg+xml' href='{_data_uri(FAVICON_SVG)}'>"
