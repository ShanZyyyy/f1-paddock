# -*- coding: utf-8 -*-
"""Formula 2 & Formula 3 takip sayfasi + F2/F3 medya yardimcilari.

streamlit_app.py monolitinden ayrildi. junior_* yardimcilari yalniz bu
sayfada kullaniliyordu, birlikte tasindi.
"""

import re
import html as html_lib
import unicodedata

import streamlit as st

from core import ui as fp_ui
from core.i18n import t as T
from core.f1_constants import JUNIOR_TEAM_SLUGS


def junior_team_logo(series, team_name):
    slug = JUNIOR_TEAM_SLUGS.get(team_name, re.sub(r'[^a-z0-9]', '', team_name.lower()))
    suffix = 'logocolourfrless.webp' if series == 'f3' else 'logo.webp'
    return (
        'https://res.cloudinary.com/prod-f2f3/d_common%3Af2%3Afallback.webp/'
        f'c_fit%2Ch_128/q_auto/v1770000000/common/{series}/2026/{slug}/2026{slug}{suffix}'
    )


def junior_driver_key(full_name):
    # Resmî F2/F3 medya anahtarları ASCII'dir. Önceki sürümde aksanlı
    # karakterler silindiği için "Câmara" gibi isimlerin URL'si bozuluyordu.
    clean = unicodedata.normalize('NFKD', str(full_name)).encode('ascii', 'ignore').decode('ascii').lower()
    clean = re.sub(r'[^a-z ]', '', clean)
    chunks = [part for part in clean.split() if part not in {'van', 'de', 'del'}]
    if len(chunks) < 2:
        return ''
    return f"{chunks[0][:3]}{chunks[-1][:3]}01"


def junior_driver_portrait(series, team_name, driver_name):
    slug = JUNIOR_TEAM_SLUGS.get(team_name, re.sub(r'[^a-z0-9]', '', team_name.lower()))
    key = junior_driver_key(driver_name)
    return (
        f'https://res.cloudinary.com/prod-f2f3/c_lfill%2Ch_300/q_auto/'
        f'v1770000000/common/{series}/2026/{slug}/{key}/2026{slug}{key}right.webp'
    )


def junior_team_car(series, team_name):
    """Resmî F2/F3 takım aracını sürücü portresiyle aynı HUD'a yerleştirir."""
    slug = JUNIOR_TEAM_SLUGS.get(team_name, re.sub(r'[^a-z0-9]', '', team_name.lower()))
    return (
        f'https://res.cloudinary.com/prod-f2f3/c_lfill%2Ch_208/q_auto/'
        f'v1770000000/common/{series}/2026/{slug}/2026{slug}carleft.webp'
    )



def render_junior_team_hud_v19(series, grid, accent, official_base):
    """V19 F2/F3 görünümü: logo kartın içine sabitlenir, boş fotoğraf kartı bırakmaz."""
    state_key = f'{series}_team_focus_v19'
    teams = list(grid.keys())
    if state_key not in st.session_state:
        st.session_state[state_key] = teams[0]

    st.markdown(
        f"<div class='hud-card' style='border-left:4px solid {accent};margin:12px 0 15px'>"
        f"<div class='hud-label'>{series.upper()} // 2026 GRID</div>"
        f"<div class='history-copy' style='margin-top:5px'>Bir takıma bas; resmî kadro ve kullanılabilir resmî görseller aynı HUD içinde açılır. "
        f"Bir görsel kaynakta yoksa boş kutu yerine metin kartı korunur.</div></div>",
        unsafe_allow_html=True,
    )
    for start in range(0, len(teams), 3):
        columns = st.columns(3)
        for column, team_name in zip(columns, teams[start:start + 3]):
            drivers = grid[team_name].split(' • ')
            logo = junior_team_logo(series, team_name)
            with column:
                st.markdown(
                    f"<div class='hud-card' style='height:142px;border-top:4px solid {accent};display:flex;flex-direction:column;justify-content:space-between'>"
                    f"<div style='height:54px;display:flex;align-items:center'><img src='{logo}' alt='{html_lib.escape(team_name)}' "
                    f"style='max-height:46px;max-width:128px;object-fit:contain' onerror=\"this.style.display='none'\"></div>"
                    f"<div style='font-size:.98rem;font-weight:900;color:#f4f8ff'>{html_lib.escape(team_name)}</div>"
                    f"<div class='driver-meta' style='white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{html_lib.escape(' • '.join(drivers))}</div></div>",
                    unsafe_allow_html=True,
                )
                if st.button(f"Takımı aç: {team_name}", key=f'v19_{series}_{team_name}', width='stretch'):
                    st.session_state[state_key] = team_name
                    st.rerun()

    selected_team = st.session_state[state_key]
    selected_drivers = grid[selected_team].split(' • ')
    team_slug = JUNIOR_TEAM_SLUGS.get(selected_team, re.sub(r'[^a-z0-9]', '', selected_team.lower()))
    logo = junior_team_logo(series, selected_team)
    st.markdown("---")
    head_logo, head_copy, head_action = st.columns([.65, 2.35, 1.15])
    with head_logo:
        st.markdown(
            f"<div class='hud-card' style='height:104px;display:flex;align-items:center;justify-content:center;border-top:4px solid {accent}'>"
            f"<img src='{logo}' alt='{html_lib.escape(selected_team)}' style='max-height:68px;max-width:150px;object-fit:contain' onerror=\"this.style.display='none'\"></div>",
            unsafe_allow_html=True,
        )
    with head_copy:
        st.markdown(
            f"<div class='hud-label'>{series.upper()} // 2026 TEAM DOSYASI</div>"
            f"<div style='font-size:1.7rem;font-weight:950;color:#f2f5f8;margin-top:3px'>{html_lib.escape(selected_team)}</div>"
            f"<div class='history-copy' style='margin-top:7px'>Seçili takımın güncel grid kadrosu. Pilot kartlarında fotoğraf kaynağı yoksa isim ve takım bilgisi yine görünür.</div>",
            unsafe_allow_html=True,
        )
    with head_action:
        st.link_button('Resmî takım profili ↗', f'{official_base}/en/teams/{team_slug}', width='stretch')

    columns = st.columns(min(3, max(1, len(selected_drivers))))
    for column, driver_name in zip(columns, selected_drivers):
        photo = junior_driver_portrait(series, selected_team, driver_name)
        car_image = junior_team_car(series, selected_team)
        with column:
            st.markdown(
                f"<div class='hud-card' style='min-height:278px;border-top:4px solid {accent};text-align:center;overflow:hidden'>"
                f"<div style='height:176px;position:relative;display:grid;grid-template-columns:1.08fr .92fr;align-items:end;gap:4px;background:linear-gradient(180deg,rgba(15,28,46,.62),rgba(7,13,22,.08));border-radius:8px;overflow:hidden;padding:5px 7px'>"
                f"<div style='height:165px;display:flex;align-items:flex-end;justify-content:center;border-right:1px solid rgba(148,163,184,.22)'><img src='{photo}' alt='{html_lib.escape(driver_name)}' style='height:164px;max-width:100%;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\"></div>"
                f"<div style='height:155px;display:flex;align-items:center;justify-content:center'><img src='{car_image}' alt='{html_lib.escape(selected_team)} car' style='max-height:104px;max-width:100%;object-fit:contain' onerror=\"this.style.display='none'\"></div>"
                f"<div style='position:absolute;right:8px;top:7px;color:{accent};font-size:9px;font-weight:900;letter-spacing:.08em'>{series.upper()} 2026</div></div>"
                f"<div style='font-size:1.02rem;font-weight:900;color:#f2f5f8;margin-top:10px'>{html_lib.escape(driver_name)}</div>"
                f"<div class='driver-meta'>{html_lib.escape(selected_team)} · Resmî portre + takım aracı</div></div>",
                unsafe_allow_html=True,
            )


# V19 görünümü eski çağrıyı bozmadan yeni, sabit HUD şablonunu kullanır.
render_junior_team_hud = render_junior_team_hud_v19


def render():
    fp_ui.page_header(T("page.f2f3.title"), T("page.f2f3.sub"), eyebrow=T("section.champ"))
    fp_ui.data_state("Junior Paddock · Beta", "Takim ve pilot kartlari yerel 2026 kadro dizininden gelir. F2/F3 sonuc akisi dogrulanmis kaynak baglanana kadar F1 puan tablosuyla karistirilmaz.", "info")
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
        fp_ui.hud_card("Formula 2", "11 takim · 22 pilot", "Sprint ve Feature Race sonuclari F1 puan merkezinden ayri tutulur.", accent="cyan")
        render_junior_team_hud('f2', f2_grid, '#00b3ff', 'https://www.fiaformula2.com')
        st.link_button("Resmî Formula 2 merkezi ↗", "https://www.fiaformula2.com/", width='stretch')
    with series[1]:
        fp_ui.section_title("F3 · 2026 Grid")
        fp_ui.hud_card("Formula 3", "10 takim · 30 pilot", "F3 verileri F1 ve F2 ile karismadan kendi yaris merkezi altinda tutulur.", accent="amber")
        render_junior_team_hud('f3', f3_grid, '#ffbe2e', 'https://www.fiaformula3.com')
        st.link_button("Resmî Formula 3 merkezi ↗", "https://www.fiaformula3.com/", width='stretch')
