# -*- coding: utf-8 -*-
"""Formula Paddock — yeniden kullanilabilir arayuz bilesenleri.

Sayfalar ham HTML/``<style>`` yazmaz; bu fonksiyonlari cagirir.
Tum gorsel dil `core/theme.py`'den gelir.
"""

import html as _html
import json as _json
import re as _re

import streamlit as st
import streamlit.components.v1 as components

from core import theme

_LIGHT_KEY = "paddock_light_mode_v31"  # eski anahtarla uyumlu

_ACCENTS = {
    "red": "var(--fp-red)", "cyan": "var(--fp-cyan)", "amber": "var(--fp-amber)",
    "green": "var(--fp-green)", "purple": "var(--fp-purple)", "pink": "var(--fp-pink)",
}

_LINE_SEP = chr(0x2028)  # JS string icinde gecersiz
_PARA_SEP = chr(0x2029)


def _esc(value):
    return _html.escape(str(value if value is not None else ""))


def _accent(name):
    if name in _ACCENTS:
        return _ACCENTS[name]
    return name if str(name).startswith(("#", "var(")) else "var(--fp-red)"


def is_light():
    return bool(st.session_state.get(_LIGHT_KEY, False))


# =====================================================================
# TEMA ENJEKSIYONU — her sayfada bir kez, en ustte
# =====================================================================
def inject_theme():
    light = is_light()
    st.markdown(theme.FONT_LINK, unsafe_allow_html=True)
    st.markdown(theme.page_style(light), unsafe_allow_html=True)
    st.markdown(theme.sidebar_style(), unsafe_allow_html=True)


def inject_shell_theme():
    """Faz 2 gecis donemi kabuk temasi: :root jetonlari + yeni sakin arka plan
    + slim-rail sidebar (menu + expander'lar).

    Sayfa govde CSS'i henuz eski monolitten geldigi icin _COMPONENT_CSS'i
    enjekte etmiyoruz. Dosyanin EN SONUNDA cagrilmali ki eski !important
    bloklarini yensin.
    """
    st.markdown(theme.FONT_LINK, unsafe_allow_html=True)
    st.markdown(theme.shell_style(is_light()), unsafe_allow_html=True)


inject_sidebar_theme = inject_shell_theme  # geriye donuk ad


# =====================================================================
# BASLIKLAR
# =====================================================================
def page_header(title, subtitle="", eyebrow="", badge=None, badge_tone="neutral"):
    badge_html = ""
    if badge:
        cls = {"live": "live", "wait": "wait"}.get(badge_tone, "")
        badge_html = f"<span class='fp-badge {cls}'>{_esc(badge)}</span>"
    eyebrow_html = f"<div class='fp-eyebrow'>{_esc(eyebrow)}</div>" if eyebrow else ""
    sub_html = f"<div class='sub'>{_esc(subtitle)}</div>" if subtitle else ""
    st.markdown(
        f"<div class='fp-page-header'><div>{eyebrow_html}"
        f"<h1>{_esc(title)}</h1>{sub_html}</div>{badge_html}</div>",
        unsafe_allow_html=True,
    )


def section_title(text):
    st.markdown(f"<div class='fp-section'>{_esc(text)}</div>", unsafe_allow_html=True)


# =====================================================================
# KARTLAR
# =====================================================================
def hud_card(label, value, copy="", accent="red"):
    copy_html = f"<div class='cpy'>{_esc(copy)}</div>" if copy else ""
    st.markdown(
        f"<div class='fp-hud' style='--accent:{_accent(accent)}'>"
        f"<div class='lbl'>{_esc(label)}</div><div class='val'>{_esc(value)}</div>{copy_html}</div>",
        unsafe_allow_html=True,
    )


def stat_tile(label, value, sub="", accent="cyan", mono=True):
    """mono=True: buyuk JetBrains Mono (sayi/zaman icin).
    mono=False: kompakt Saira (metin degerleri icin — takim adi, durum vb.)."""
    sub_html = f"<div class='sub'>{_esc(sub)}</div>" if sub else ""
    val_cls = "val" if mono else "val txt"
    st.markdown(
        f"<div class='fp-tile' style='--accent:{_accent(accent)}'>"
        f"<div class='lbl'>{_esc(label)}</div><div class='{val_cls}'>{_esc(value)}</div>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def data_state(title, message, tone="info"):
    colour = theme.tone_hex(tone, is_light())
    st.markdown(
        f"<div class='fp-state' style='--sc:{colour}'>"
        f"<div class='st'>{_esc(title)}</div><div class='sc'>{_esc(message)}</div></div>",
        unsafe_allow_html=True,
    )


def result_hero(event, session_label, winner_name, team, gap_text, runners=None):
    """Yaris bitince: kim, hangi takim, kac saniye farkla kazandi.

    ``runners`` = [(pos_label, code, team), ...] — 2. ve 3. icin kisa satir.
    Hicbir alan uydurulmaz; veri yoksa cagiran taraf bu fonksiyonu cagirmaz.
    """
    colour = theme.team_color(team)
    eyebrow = " · ".join(p for p in [event, session_label, "Resmi Sonuc"] if p)
    parts = str(winner_name or "").split()
    if len(parts) > 1:
        name_html = f"{_esc(' '.join(parts[:-1]))} <b>{_esc(parts[-1])}</b>"
    else:
        name_html = f"<b>{_esc(winner_name)}</b>"
    gap_html = ""
    if gap_text:
        gap_html = f"<span class='gap'><span>KAZANMA FARKI</span>{_esc(gap_text)}</span>"
    next_html = ""
    if runners:
        bits = [f"{_esc(pos)} <b>{_esc(code)}</b> ({_esc(tm)})" for pos, code, tm in runners]
        next_html = "<span class='next'>" + " &nbsp;·&nbsp; ".join(bits) + "</span>"
    st.markdown(
        f"<div class='fp-result' style='--tc:{colour}'>"
        f"<div class='eb'>{_esc(eyebrow)}</div>"
        f"<div class='nm'>{name_html} kazandi</div>"
        f"<div class='row'><span class='team'>{_esc(team)}</span>{gap_html}{next_html}</div></div>",
        unsafe_allow_html=True,
    )


# =====================================================================
# IZOLE HUD IFRAME — tek guvenli render kapisi
# =====================================================================
def render_html_hud(markup, height=150, scrolling=False):
    if not isinstance(markup, str) or not markup.strip():
        st.info("Bu HUD icin gosterilecek dogrulanmis veri henuz yok.")
        return None
    theme_css = "<style>" + theme.hud_iframe_style(is_light()) + "</style>"
    document = markup.strip()
    if "<html" not in document.lower():
        document = (
            '<!doctype html><html><head><meta charset="utf-8">'
            + theme_css
            + '</head><body><div class="fp-hud-shell">'
            + document
            + "</div></body></html>"
        )
    elif "</head>" in document.lower():
        document = _re.sub(r"</head>", theme_css + "</head>", document, count=1, flags=_re.IGNORECASE)
    else:
        document = theme_css + document
    if hasattr(st, "iframe"):
        return st.iframe(document, height=height)
    return components.html(document, height=height, scrolling=scrolling)


def json_for_script(obj):
    """JSON'u satir ici <script> blenda guvenle gomer: </script>, yorum ve
    satir ayiricilarini escape'ler."""
    text = _json.dumps(obj, ensure_ascii=False)
    text = text.replace("</", "<\\/").replace("<!--", "<\\!--")
    text = text.replace(_LINE_SEP, "\\u2028").replace(_PARA_SEP, "\\u2029")
    return text


# =====================================================================
# SIDEBAR
# =====================================================================
def sidebar_brand():
    st.sidebar.markdown(
        "<div class='fp-brand'><span class='mark'></span>"
        "<span class='txt'>Formula Paddock<s>Race Intelligence</s></span></div>",
        unsafe_allow_html=True,
    )


def sidebar_section(label):
    st.sidebar.markdown(f"<div class='fp-nav-sec'>{_esc(label)}</div>", unsafe_allow_html=True)


def nav_button(label, icon, page_key, current_page):
    """Slim-rail nav satiri. Aktif sayfa 'primary' tipiyle isaretlenir.

    ``icon`` = Material sembol adi, orn. "home" -> :material/home:
    Tiklaninca True doner (cagiran taraf sayfayi degistirip rerun eder).
    """
    is_active = current_page == page_key
    return st.sidebar.button(
        label,
        key=f"nav_{page_key}",
        icon=f":material/{icon}:" if icon else None,
        use_container_width=True,
        type="primary" if is_active else "secondary",
    )
