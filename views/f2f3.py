# -*- coding: utf-8 -*-
"""Formula 2 & Formula 3 takip sayfasi + F2/F3 medya yardimcilari.

streamlit_app.py monolitinden ayrildi. Sayfaya ozel HUD'lar:
- takim izgarasi tek CSS grid -> tum kutular ESIT yukseklik
- pilot fotografi yoksa takim renginde bashaf monogrami (bos kutu yok)
- takim secici st.pills (esit, kaydirilabilir)
"""

import re
import html as html_lib
import unicodedata

import streamlit as st

from core import ui as fp_ui
from core.i18n import t as T
from core.f1_constants import JUNIOR_TEAM_SLUGS


# --------------------------------------------------------------------------
# Resmi F2/F3 medya URL'leri
# --------------------------------------------------------------------------
def junior_team_logo(series, team_name):
    slug = JUNIOR_TEAM_SLUGS.get(team_name, re.sub(r'[^a-z0-9]', '', team_name.lower()))
    suffix = 'logocolourfrless.webp' if series == 'f3' else 'logo.webp'
    return ('https://res.cloudinary.com/prod-f2f3/d_common%3Af2%3Afallback.webp/'
            f'c_fit%2Ch_128/q_auto/v1770000000/common/{series}/2026/{slug}/2026{slug}{suffix}')


def junior_driver_key(full_name):
    # Resmi F2/F3 medya anahtarlari ASCII'dir.
    clean = unicodedata.normalize('NFKD', str(full_name)).encode('ascii', 'ignore').decode('ascii').lower()
    clean = re.sub(r'[^a-z ]', '', clean)
    chunks = [part for part in clean.split() if part not in {'van', 'de', 'del', 'von', 'da'}]
    if len(chunks) < 2:
        return ''
    return f"{chunks[0][:3]}{chunks[-1][:3]}01"


def junior_driver_portrait(series, team_name, driver_name):
    slug = JUNIOR_TEAM_SLUGS.get(team_name, re.sub(r'[^a-z0-9]', '', team_name.lower()))
    key = junior_driver_key(driver_name)
    return (f'https://res.cloudinary.com/prod-f2f3/c_lfill%2Ch_300/q_auto/'
            f'v1770000000/common/{series}/2026/{slug}/{key}/2026{slug}{key}right.webp')


def junior_team_car(series, team_name):
    slug = JUNIOR_TEAM_SLUGS.get(team_name, re.sub(r'[^a-z0-9]', '', team_name.lower()))
    return (f'https://res.cloudinary.com/prod-f2f3/c_lfill%2Ch_208/q_auto/'
            f'v1770000000/common/{series}/2026/{slug}/2026{slug}carleft.webp')


def _initials(name):
    parts = [p for p in re.sub(r'[^0-9A-Za-zÀ-ɏ ]', '', str(name)).split()
             if p.lower() not in {'van', 'de', 'del', 'von', 'da', 'di'}]
    if not parts:
        return '—'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# --------------------------------------------------------------------------
# HUD'lar (sayfaya ozel)
# --------------------------------------------------------------------------
_CARD_H = 156        # kart yuksekligi (px) — gorunmez buton bununla ortusur
_ROW_GAP = 0         # kart markdown'i ile buton konteyneri arasi olculen bosluk

_JR_CSS_TMPL = """
<style>
.jr-hint{font:600 11px 'Saira',sans-serif;color:#8a9bb0;margin:14px 0 6px 2px}
.jr-hint b{color:__ACC__}
.jr-team{display:flex;flex-direction:column;height:__H__px;padding:12px 12px 11px;box-sizing:border-box;
  border:1px solid var(--fp-line,#26313f);border-top:3px solid __ACC__;border-radius:7px;
  background:linear-gradient(160deg,#161d28,#11161f);overflow:hidden}
.jr-team .lg{height:36px;display:flex;align-items:center;margin-bottom:8px}
.jr-team .lg img{max-height:32px;max-width:112px;object-fit:contain}
.jr-team .nm{font:800 12.5px 'Saira Condensed','Arial Narrow',sans-serif;letter-spacing:.02em;
  text-transform:uppercase;color:#f2f5f8;line-height:1.15;min-height:2.3em;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.jr-team .dv{margin-top:auto;padding-top:7px;border-top:1px solid #1b2330;display:flex;flex-direction:column;gap:2px}
.jr-team .dv b{font:600 10.5px 'Saira',sans-serif;color:#9fb0c0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jr-team.on{border-color:__ACC__;box-shadow:0 0 0 1px __ACC__ inset;background:linear-gradient(160deg,#1b2634,#141c28)}
.jr-team.on .nm{color:__ACC__}

/* gorunmez tiklama katmani: kart markdown'inin hemen ardindaki bos buton yukari cekilir */
.stApp div[class*="st-key-jrpick___S___"]{margin-top:-__OVER__px !important;margin-bottom:12px !important;
  height:__H__px !important;position:relative;z-index:6}
.stApp div[class*="st-key-jrpick___S___"] div[data-testid="stButton"]{height:__H__px !important}
.stApp div[class*="st-key-jrpick___S___"] button{height:__H__px !important;width:100% !important;
  min-height:0 !important;padding:0 !important;font-size:0 !important;color:transparent !important;
  border:1px solid transparent !important;background:transparent !important;box-shadow:none !important;
  border-radius:7px !important;opacity:0 !important;transition:none !important}
.stApp div[class*="st-key-jrpick___S___"] button:hover{opacity:1 !important;
  background:color-mix(in srgb,__ACC__ 13%,transparent) !important;
  border-color:color-mix(in srgb,__ACC__ 60%,transparent) !important}

.jr-detail{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px}
.jr-drv{border:1px solid var(--fp-line,#26313f);border-top:3px solid var(--jr-acc);border-radius:8px;
  background:linear-gradient(160deg,#161d28,#11161f);overflow:hidden;text-align:center}
.jr-drv .stage{position:relative;height:196px;background:radial-gradient(125% 92% at 50% 0%,#1c2836,#0b0f16)}
.jr-drv .stage .mono{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font:800 64px 'Saira Condensed',sans-serif;color:var(--jr-acc);opacity:.26;letter-spacing:.04em}
.jr-drv .stage .ph{position:absolute;left:2%;bottom:0;height:190px;max-width:66%;
  object-fit:contain;object-position:center bottom}
.jr-drv .stage .car{position:absolute;right:5px;bottom:16px;max-height:76px;max-width:46%;object-fit:contain;opacity:.9}
.jr-drv .stage .tag{position:absolute;right:8px;top:8px;font:800 9px 'JetBrains Mono',monospace;
  color:var(--jr-acc);letter-spacing:.11em}
.jr-drv .meta{padding:12px 10px 14px}
.jr-drv .meta b{display:block;font:800 15px 'Saira Condensed',sans-serif;text-transform:uppercase;
  letter-spacing:.02em;color:#f2f5f8;line-height:1.15;min-height:2.3em;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.jr-drv .meta span{display:block;font:600 11px 'Saira',sans-serif;color:#9fb0c0;margin-top:4px}

.jr-head{display:grid;grid-template-columns:96px 1fr;gap:14px;align-items:center;margin-top:6px;
  padding:14px 16px;border:1px solid var(--fp-line,#26313f);border-left:4px solid var(--jr-acc);
  border-radius:7px;background:linear-gradient(160deg,#161d28,#11161f)}
.jr-head .hl{height:64px;display:flex;align-items:center;justify-content:center}
.jr-head .hl img{max-height:60px;max-width:92px;object-fit:contain}
.jr-head .ht s{font:700 9px 'Saira Condensed',sans-serif;letter-spacing:.14em;text-transform:uppercase;
  color:#8a9bb0;text-decoration:none}
.jr-head .ht b{display:block;font:900 22px 'Saira Condensed',sans-serif;text-transform:uppercase;
  letter-spacing:.02em;color:#f2f5f8;margin-top:2px}
@media(max-width:520px){.jr-drv .stage{height:172px}.jr-drv .stage .mono{font-size:52px}}
</style>
"""


def _jr_css(series, accent):
    return (_JR_CSS_TMPL
            .replace("__ACC__", accent)
            .replace("__S__", series)
            .replace("__H__", str(_CARD_H))
            .replace("__OVER__", str(_CARD_H + _ROW_GAP)))


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')


def _team_card_html(series, team, roster, accent, is_on):
    rows = "".join(f"<b>{html_lib.escape(d)}</b>" for d in roster.split(' • '))
    return (
        f"<div class='jr-team{' on' if is_on else ''}' style='--jr-acc:{accent}'>"
        f"<div class='lg'><img src='{junior_team_logo(series, team)}' alt='' onerror=\"this.style.display='none'\"></div>"
        f"<div class='nm'>{html_lib.escape(team)}</div>"
        f"<div class='dv'>{rows}</div></div>"
    )


def _team_detail_html(series, team, roster, accent, official_base):
    slug = JUNIOR_TEAM_SLUGS.get(team, re.sub(r'[^a-z0-9]', '', team.lower()))
    cards = []
    for name in roster.split(' • '):
        cards.append(
            f"<div class='jr-drv'>"
            f"<div class='stage'>"
            f"<div class='mono'>{html_lib.escape(_initials(name))}</div>"
            f"<img class='ph' src='{junior_driver_portrait(series, team, name)}' alt='' onerror=\"this.style.display='none'\">"
            f"<img class='car' src='{junior_team_car(series, team)}' alt='' onerror=\"this.style.display='none'\">"
            f"<span class='tag'>{series.upper()} 2026</span></div>"
            f"<div class='meta'><b>{html_lib.escape(name)}</b><span>{html_lib.escape(team)}</span></div></div>"
        )
    return (
        f"<div style='--jr-acc:{accent}'>"
        f"<div class='jr-head'>"
        f"<div class='hl'><img src='{junior_team_logo(series, team)}' alt='' onerror=\"this.style.display='none'\"></div>"
        f"<div class='ht'><s>{series.upper()} · 2026 TAKIM DOSYASI</s><b>{html_lib.escape(team)}</b></div></div>"
        f"<div class='jr-detail'>{''.join(cards)}</div></div>"
    ), f'{official_base}/en/teams/{slug}'


def render_junior_team_hud(series, grid, accent, official_base):
    """F2/F3 takim izgarasi + secili takim dosyasi.

    Izgaradaki kartlarin uzerine gorunmez bir st.button denk gelir — karta
    basmak takimi secer, dosyayi acar ve asagi kaydirir. Ayri buton yok.
    """
    state_key = f'{series}_team_focus_v19'
    anchor_id = f'jr-detail-{series}'
    teams = list(grid.items())
    names = [t[0] for t in teams]

    if state_key not in st.session_state or st.session_state[state_key] not in names:
        st.session_state[state_key] = names[0]
    selected = st.session_state[state_key]

    st.markdown(_jr_css(series, accent), unsafe_allow_html=True)
    st.markdown("<div class='jr-hint'>Bir <b>takıma</b> bas → kadro ve resmî görseller aşağıda açılır.</div>",
                unsafe_allow_html=True)

    per_row = 3
    for i in range(0, len(teams), per_row):
        cols = st.columns(per_row, gap="small")
        for col, (tn, roster) in zip(cols, teams[i:i + per_row]):
            with col:
                st.markdown(_team_card_html(series, tn, roster, accent, tn == selected),
                            unsafe_allow_html=True)
                if st.button(tn, key=f"jrpick_{series}_{_slug(tn)}", width='stretch'):
                    if tn != selected:
                        st.session_state[state_key] = tn
                    st.session_state[f'_jr_scroll_{series}'] = True
                    st.rerun()

    fp_ui.anchor(anchor_id)
    detail_html, profile_url = _team_detail_html(series, selected, grid[selected], accent, official_base)
    st.markdown(detail_html, unsafe_allow_html=True)
    st.link_button("Resmî takım profili ↗", profile_url, width='stretch')

    if st.session_state.pop(f'_jr_scroll_{series}', False):
        fp_ui.scroll_to(anchor_id)

    if st.session_state.pop(f'_jr_scroll_{series}', False):
        fp_ui.scroll_to(anchor_id)


def render():
    fp_ui.page_header(T("page.f2f3.title"), T("page.f2f3.sub"), eyebrow=T("section.champ"))
    fp_ui.data_state(
        "Junior Paddock · Beta",
        "Takim ve pilot kartlari yerel 2026 kadro dizininden gelir. F2/F3 sonuc akisi "
        "dogrulanmis kaynak baglanana kadar F1 puan tablosuyla karistirilmaz.", "info")
    f2_grid = {
        'Invicta Racing': 'Rafael Câmara • Joshua Dürksen', 'Hitech': 'Ritomo Miyata • Colton Herta',
        'Campos Racing': 'Noel León • Nikola Tsolov', 'DAMS Lucas Oil': 'Dino Beganovic • Roman Bilinski',
        'MP Motorsport': 'Gabriele Minì • Oliver Goethe', 'PREMA Racing': 'Sebastián Montoya • Mari Boya',
        'Rodin Motorsport': 'Martinius Stenshorne • Alexander Dunne', 'ART Grand Prix': 'Kush Maini • Tasanapol Inthraphuvasak',
        'AIX Racing': 'Emerson Fittipaldi • Cian Shields', 'Van Amersfoort Racing': 'Nicolás Varrone • Rafael Villagómez',
        'TRIDENT': 'Laurens van Hoepen • John Bennett',
    }
    f3_grid = {
        'Campos Racing': 'Théophile Naël • Ugo Ugochukwu • Ernesto Rivera', 'TRIDENT': 'Noah Strømsted • Freddie Slater • Matteo De Palo',
        'MP Motorsport': 'Mattia Colnaghi • Tuukka Taponen • Alessandro Giusti', 'ART Grand Prix': 'Taito Kato • Maciej Gładysz • Kanato Le',
        'Van Amersfoort Racing': 'Hiyu Yamakoshi • Enzo Deligny • Bruno del Pino', 'Rodin Motorsport': 'Pedro Clerot • Brando Badoer • Christian Ho',
        'PREMA Racing': 'Louis Sharp • James Wharton • José Garfias', 'Hitech': 'Michael Shin • Fionn McLaughlin • Jin Nakamura',
        'AIX Racing': 'Rafael Escotto • Yevan David • Fernando Barrichello', 'DAMS Lucas Oil': 'Nicola Lacorte • Nandhavud Bhirombhakdi • Gerrard Xie',
    }
    series = st.tabs(["Formula 2", "Formula 3"])
    with series[0]:
        fp_ui.section_title("F2 · 2026 Grid")
        fp_ui.hud_card("Formula 2", "11 takim · 22 pilot",
                       "Sprint ve Feature Race sonuclari F1 puan merkezinden ayri tutulur.", accent="cyan")
        render_junior_team_hud('f2', f2_grid, '#38c6ff', 'https://www.fiaformula2.com')
        st.link_button("Resmî Formula 2 merkezi ↗", "https://www.fiaformula2.com/", width='stretch')
    with series[1]:
        fp_ui.section_title("F3 · 2026 Grid")
        fp_ui.hud_card("Formula 3", "10 takim · 30 pilot",
                       "F3 verileri F1 ve F2 ile karismadan kendi yaris merkezi altinda tutulur.", accent="amber")
        render_junior_team_hud('f3', f3_grid, '#ffbe2e', 'https://www.fiaformula3.com')
        st.link_button("Resmî Formula 3 merkezi ↗", "https://www.fiaformula3.com/", width='stretch')
