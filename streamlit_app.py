# -*- coding: utf-8 -*-
import os
import time
import functools
import datetime
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
import json
import re
import html as html_lib
import logging
import xml.etree.ElementTree as ET
import streamlit as st
import fastf1
import fastf1.plotting
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import openf1_fallback

# Yeniden yapilandirma (redesign) — tasarim sistemi
from core import ui as fp_ui
from core import plot as fp_plot
from core import i18n as fp_i18n
from core import hero as fp_hero
from core.i18n import t as T
from core.f1_constants import *  # noqa: F401,F403 — statik F1 veri sabitleri
from views import glossary as _view_glossary
from views import f2f3 as _view_f2f3


LOGGER = logging.getLogger("f1_paddock")


def safe_external_url(value, allowed_hosts=None):
    """Yalnızca güvenli HTTP(S) bağlantılarını arayüze geçirir."""
    try:
        parsed = urllib.parse.urlparse(str(value or '').strip())
        host = parsed.hostname.lower() if parsed.hostname else ''
        if parsed.scheme not in {'http', 'https'} or not host:
            return ''
        if allowed_hosts and not any(host == item or host.endswith('.' + item) for item in allowed_hosts):
            return ''
        return parsed.geturl()
    except (TypeError, ValueError):
        return ''


_DATA_ERROR_LOG = []          # son N veri-kaynağı hatası (süreç ömrü boyunca)
_DATA_ERROR_COUNT = {"n": 0}  # toplam sayaç


def log_data_error(context, error):
    """Ekranı kırmadan gerçek hatayı terminal günlüğüne + hafif bir tanılama
    tamponuna yazar (``?debug=1`` ile kenar menüde görülür)."""
    LOGGER.warning("%s | %s | %s", context, type(error).__name__, str(error)[:420])
    try:
        _DATA_ERROR_COUNT["n"] += 1
        _DATA_ERROR_LOG.append("{} · {} · {}".format(
            datetime.datetime.now().strftime("%H:%M:%S"),
            str(context)[:48],
            type(error).__name__,
        ))
        del _DATA_ERROR_LOG[:-40]
    except Exception:
        pass


def render_data_diagnostics_panel():
    """Kenar menü altında, yalnızca ``?debug=1`` sorgu parametresiyle görünür."""
    try:
        debug_on = str(st.query_params.get("debug", "")) in ("1", "true", "yes")
    except Exception:
        debug_on = False
    if not debug_on:
        return
    with st.expander(f"🩺 Veri tanılama · {_DATA_ERROR_COUNT['n']} hata", expanded=False):
        if not _DATA_ERROR_LOG:
            st.caption("Bu oturumda kaydedilmiş veri hatası yok.")
        else:
            st.code("\n".join(reversed(_DATA_ERROR_LOG)), language="text")


def cache_data_safe(ttl, *, on_error=None, label=None, show_spinner=False):
    """`st.cache_data` — ama SADECE başarı önbelleğe alınır.

    Sarılan fonksiyon, veri gelmediğinde sentinel döndürmek yerine exception
    FIRLATMALIDIR. Böylece geçici bir ağ/kaynak hatası TTL boyunca "veri yok"
    olarak donup kalmaz; bir sonraki çağrı yeniden dener. Hata anında
    `on_error` (çağrılabilirse çağrılır) değeri döner ve önbelleğe YAZILMAZ.

        @cache_data_safe(ttl=900, on_error=lambda: ([], []), label='top drivers')
        def get_real_top_drivers(...):
            ...  # başarısızlıkta raise
    """
    def decorator(raw):
        cached = st.cache_data(ttl=ttl, show_spinner=show_spinner)(raw)

        @functools.wraps(raw)
        def wrapper(*args, **kwargs):
            try:
                return cached(*args, **kwargs)
            except Exception as error:  # noqa: BLE001 — bilinçli genel yakalama
                log_data_error(label or getattr(raw, '__name__', 'data'), error)
                return on_error() if callable(on_error) else on_error

        wrapper.clear = getattr(cached, 'clear', lambda: None)
        return wrapper

    return decorator


# redesign: HUD render kapisi + tema propagasyonu core/ui.py + core/theme.py'de.
def render_html_hud(markup, height=150, scrolling=False):
    return fp_ui.render_html_hud(markup, height=height, scrolling=scrolling)


def render_data_state(title, message, tone='info'):
    """Yükleme/hata/boş veri durumlarının ortak HUD görünümü."""
    colours = {'info': '#5ddcff', 'success': '#6ee7b7', 'warning': '#f7c948', 'error': '#ff6677'}
    colour = colours.get(tone, colours['info'])
    st.markdown(
        f"<div class='hud-card' style='border-left:4px solid {colour}'><div class='hud-label'>{html_lib.escape(title)}</div>"
        f"<div class='history-copy' style='margin-top:7px'>{html_lib.escape(message)}</div></div>",
        unsafe_allow_html=True,
    )


def validate_stable_replay_payload(payload):
    """Bozuk veya yarım kalan telemetri paketinin 2D oynatıcıya ulaşmasını engeller."""
    try:
        if not isinstance(payload, dict) or not payload.get('cars'):
            return False, 'Yarışta gösterilecek doğrulanmış araç verisi bulunamadı.'
        track = np.asarray(payload.get('track', []), dtype=float)
        if track.ndim != 2 or track.shape[0] < 80 or track.shape[1] != 2 or not np.isfinite(track).all():
            return False, 'Pist yörüngesi eksik olduğu için tekrar güvenli biçimde açılamadı.'
        span_x, span_y = np.ptp(track[:, 0]), np.ptp(track[:, 1])
        if span_x <= 0 or span_y <= 0:
            return False, 'Pist yörüngesi geçerli bir alan oluşturmuyor.'
        diagonal = float(np.hypot(span_x, span_y))
        closure = float(np.hypot(*(track[0] - track[-1])))
        if diagonal <= 0 or closure > diagonal * 0.72:
            return False, 'Temiz ve kapalı bir pist turu bulunamadı; hatalı pist çizimi gösterilmiyor.'
        codes = set()
        final_time = 0.0
        for car in payload['cars']:
            code = str(car.get('code', '')).strip()
            laps = car.get('laps', [])
            if not code or code in codes or not laps:
                return False, 'Araç zaman çizelgesi eksik olduğu için tekrar güvenli biçimde açılamadı.'
            codes.add(code)
            previous_end = -1.0
            for lap in laps:
                start, end = float(lap.get('start', -1)), float(lap.get('end', -1))
                if start < 0 or end <= start or start + 0.05 < previous_end:
                    return False, 'Tur zaman çizelgesinde çakışma bulundu; hatalı hareket gösterilmiyor.'
                previous_end, final_time = end, max(final_time, end)
        if final_time <= 0 or float(payload.get('total_seconds', 0)) + 0.1 < final_time:
            return False, 'Yarış süresi doğrulanamadı.'
        return True, ''
    except Exception as error:
        log_data_error('replay payload validation', error)
        return False, 'Yarış paketi doğrulama aşamasında tamamlanamadı.'


st.set_page_config(
    page_title="Formula Paddock Control Pro",
    page_icon="🏎️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if 'paddock_light_mode_v31' not in st.session_state:
    st.session_state['paddock_light_mode_v31'] = False

# STREAMLIT 1.60 LAYOUT RECOVERY
# Bazı Windows/Chrome oturumlarında, önceki HUD/tema denemelerinden sonra
# Streamlit'in kök kapsayıcısı 0px yüksekliğe düşebiliyor. İçerik aslında
# üretiliyor fakat dış katman kırptığı için yalnızca siyah ekran görünüyor.
# Bu, görsel tema değil; ana sayfanın yerleşim yüksekliğini güvenli biçimde
# geri kuran küçük bir düzeltmedir.
st.markdown("""
<style>
html, body, #root {
    height: 100% !important;
    min-height: 100% !important;
}
[data-testid="stApp"], .stApp {
    height: 100vh !important;
    min-height: 100vh !important;
}
[data-testid="stAppViewContainer"] {
    position: relative !important;
    height: 100vh !important;
    min-height: 100vh !important;
    overflow: visible !important;
}

/* KRITIK ZEMIN — tam tema (theme.shell_style) dosyanin sonunda enjekte
   edildiginden, ilk boyamada ana govde beyaz/stilsiz parlarabiliyordu.
   Koyu = varsayilan; acik tema kullanicilari icin :root guardi var.
   Sol menunun slim-rail temasi icin: fp_ui.inject_rail_theme() (asagida). */
:root:not([data-fp-theme="light"]) [data-testid="stApp"],
:root:not([data-fp-theme="light"]) [data-testid="stAppViewContainer"]{
    background:#0c1016 !important;
}
</style>
""", unsafe_allow_html=True)

# Slim-rail sol menü temasını EN BAŞTA da bir kez enjekte et — yoksa açılışta
# menü birkaç kare eski stille görünüyor (tam tema dosya sonunda geliyor).
fp_ui.inject_rail_theme()

# Uyarıları küresel olarak kapatmayın; gerçek sorunları görünür bırakın.

# FastF1 önbelleği + matplotlib teması: PAHALI ve DEĞİŞMEZ kurulum.
# Streamlit her etkileşimde tüm betiği baştan çalıştırır; bu blok yalnız
# oturumda bir kez koşsun (setup_mpl rcParams'ı her rerun'da yeniden yazıyordu).
cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
if not st.session_state.get('_fp_runtime_ready'):
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)
    fastf1.plotting.setup_mpl()
    st.session_state['_fp_runtime_ready'] = True

# SAYFA OTURUM DURUMU
if 'page' not in st.session_state:
    st.session_state['page'] = 'home'

# ---- URL <-> sayfa senkronu -------------------------------------------------
# Üst bar linkleri `?p=telemetry` gibi çalışır. Buradan gelen değer session
# state'i günceller; kod içi `st.session_state['page'] = X` çağrıları da URL'e
# geri yazılır (paylaşılabilir / yer imlenebilir bağlantı).
VALID_PAGES = {
    'home', 'news', 'telemetry', 'live', 'calendar', 'weekend', 'story',
    'compare', 'drivers', 'learn', 'favourites', 'teams', 'standings',
    'f2f3', 'glossary', 'assistant', 'games',
    # oyun alt sayfaları (Oyun Merkezi içinden derin bağlantı)
    'stewarlde', 'paddock_career',
    # yardımcı sayfalar (ayaktan / breadcrumb'dan)
    'faq', 'privacy',
}
#  - URL `?p=` DEĞİŞTİYSE (sert nav / geri-ileri / <a href> yedeği): onu izle.
#  - URL aynı ama kod içi `st.session_state['page'] = X; st.rerun()` çağrılmışsa
#    (Oyun Merkezi düğmeleri, "hafta sonuna git" vb.): session state kazanır ve
#    URL'e yazılır. Eskiden ilk kural URL'i kaynak sayıp bu değişiklikleri geri
#    alıyordu → oyunlar açılmıyordu.
_qp_page = st.query_params.get('p')
_prev_qp = st.session_state.get('_prev_qp_page')
_bad_page = None
if _qp_page != _prev_qp:
    if _qp_page in VALID_PAGES:
        st.session_state['page'] = _qp_page
    elif _qp_page:                                  # ?p=<bilinmeyen> -> 404 (link/eski oturum)
        _bad_page = _qp_page
st.session_state['_prev_qp_page'] = _qp_page
if not _bad_page and st.session_state['page'] in VALID_PAGES and st.session_state['page'] != _qp_page:
    st.query_params['p'] = st.session_state['page']
    st.session_state['_prev_qp_page'] = st.session_state['page']

# BOOT FIX 1.4.2
# Eski Streamlit tarayıcı oturumları, daha önce tıklanmış "veri yükle"
# düğmelerinin durumunu bellekte tutar. Bu sürüm açıldığında o eski durumları
# yalnızca bir kez temizler; böylece uygulama daha HTML'yi çizmeden FastF1'e
# takılı kalamaz. HUD ve arka plan burada değiştirilmez.
BOOT_FIX_VERSION = '1.4.4-auto-data-feed'
if st.session_state.get('_boot_fix_version') != BOOT_FIX_VERSION:
    st.session_state['_boot_fix_version'] = BOOT_FIX_VERSION
    # Sayfa artık 0px yükseklik hatasından bağımsız biçimde açılıyor.
    # Kullanıcıyı düğmeye bastırmamak için veri merkezlerini yeniden otomatik aç.
    # Ana sayfa açılır açılmaz güncel yarış merkezi ve haberler yüklensin.
    st.session_state['home_data_requested'] = True
    st.session_state['telemetry_schedule_requested'] = True
    st.session_state['news_requested'] = True

# Açılış emniyeti: Streamlit tüm dosyayı baştan çalıştırır. Bu nedenle uzak
# FastF1/RSS çağrılarını ilk karede yapmak boş ekrana ve "sonsuz yükleniyor"
# hissine yol açar. Veri yalnızca kullanıcı istediğinde yüklenir.
if 'home_data_requested' not in st.session_state:
    st.session_state['home_data_requested'] = True
if 'telemetry_schedule_requested' not in st.session_state:
    st.session_state['telemetry_schedule_requested'] = True
if 'news_requested' not in st.session_state:
    st.session_state['news_requested'] = True

# 1. AKILLI GELECEK/ŞİMDİKİ SEANS TESPİT MOTORU
@cache_data_safe(ttl=1800, on_error=list, label='next-event schedule')
def _upcoming_schedule_records():
    """Sıradaki seansı bulmak için ham takvim satırları (ilk dolu yıl), 30 dk
    cache. `cache_data_safe`: sadece başarı önbelleğe alınır — geçici bir
    FastF1 hatası "takvim yok" olarak donmaz."""
    now_year = datetime.datetime.now(datetime.timezone.utc).year
    for yr in dict.fromkeys([now_year, 2026, now_year - 1]):
        try:
            sched = fastf1.get_event_schedule(int(yr), include_testing=False)
            sched = sched[sched['RoundNumber'] > 0]
            if not sched.empty:
                return sched.to_dict('records')
        except Exception:
            continue
    raise RuntimeError('takvim alınamadı')


def get_current_or_next_event():
    """Takvim alınmasa da ana sayfayı kırmadan sıradaki gerçek seansı bulur.

    Takvim çekme `_upcoming_schedule_records()` içinde 30 dk cache'li; burada
    kalan iş sadece `now` ile kıyas (ucuz) — her rerun'da FastF1'e gitmez.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    records = _upcoming_schedule_records()

    if not records:
        # Sayfa açılmaya devam eder; bu veri sonucu veya hayalî yarış değildir.
        return pd.Series({'EventName': 'Takvim verisi bekleniyor', 'Location': 'Formula 1'}), 'Yarış', now, False

    session_cols = [
        ('FP1', 'Session1DateUtc'),
        ('FP2', 'Session2DateUtc'),
        ('FP3', 'Session3DateUtc'),
        ('Sıralama Turları', 'Session4DateUtc'),
        ('Yarış', 'Session5DateUtc'),
    ]

    for event in records:
        for s_name, s_col in session_cols:
            raw = event.get(s_col)
            if raw is None or pd.isnull(raw):
                continue
            s_time = pd.to_datetime(raw)
            s_time = s_time.tz_localize('UTC') if s_time.tzinfo is None else s_time.tz_convert('UTC')
            s_end_time = s_time + datetime.timedelta(hours=2)

            if s_time <= now <= s_end_time:
                return pd.Series(event), s_name, s_time, True
            if s_time > now:
                return pd.Series(event), s_name, s_time, False

    last_event = records[-1]
    last_time = pd.to_datetime(last_event.get('Session5DateUtc'))
    last_time = last_time.tz_localize('UTC') if last_time.tzinfo is None else last_time.tz_convert('UTC')
    return pd.Series(last_event), "Yarış", last_time, False


# ==========================================
# ÜST BAR (NAVİGASYON) — sol menünün yerini aldı; hover'da mega-menü sarkar
# ==========================================

# Dil: ?lang=tr|en linkleri (üst bardaki TR/EN)
_qp_lang = st.query_params.get('lang')
if _qp_lang in ('tr', 'en'):
    if fp_i18n.get_lang() != _qp_lang:
        fp_i18n.set_lang(_qp_lang)
    del st.query_params['lang']

light_mode_v31 = False
st.markdown(
    "<style>.status-dot-v31{display:inline-block;width:8px;height:8px;border-radius:50%;"
    "background:var(--fp-green);box-shadow:0 0 8px var(--fp-green)}</style>",
    unsafe_allow_html=True,
)

_nav_now = st.session_state['page']

# Üst bar: "Haberler" düz sekme + eski sidebar bölümlerinin aynısı (her grup kendi açılır listesi)
NAV_STANDALONE = [("news", T("nav.news"))]
NAV_GROUPS = [
    (T("section.data"), "telemetry", [("telemetry", T("nav.telemetry"))]),
    (T("section.live"), "live", [
        ("live", T("nav.live")), ("calendar", T("nav.calendar")), ("weekend", T("nav.weekend")),
        ("story", T("nav.story")), ("compare", T("nav.compare")), ("drivers", T("nav.drivers"))]),
    (T("section.paddock"), "learn", [("learn", T("nav.learn")), ("favourites", T("nav.favourites"))]),
    (T("section.champ"), "teams", [
        ("teams", T("nav.teams")), ("standings", T("nav.standings")), ("f2f3", T("nav.f2f3")),
        ("glossary", T("nav.glossary")), ("assistant", T("nav.assistant"))]),
    (T("section.games"), "games", [("games", T("nav.games"))]),
]

# sayfa -> (kırıntı etiketi, bölüm etiketi, bölüm birincil sayfası)
_PAGE_META = {}
for _t, _pri, _pages in NAV_GROUPS:
    for _pk, _plbl in _pages:
        _PAGE_META[_pk] = (_plbl, _t, _pri)
for _pk, _plbl in NAV_STANDALONE:
    _PAGE_META[_pk] = (_plbl, _plbl, _pk)
_PAGE_META['stewarlde'] = ("Stewardle", T("section.games"), "games")
_PAGE_META['paddock_career'] = ("Paddock Career", T("section.games"), "games")
_PAGE_META['faq'] = ("SSS", "Bilgi", "faq")
_PAGE_META['privacy'] = ("Gizlilik", "Bilgi", "privacy")

FOOTER_LINKS = [("SSS", "faq"), ("Gizlilik", "privacy")]


def _render_breadcrumb(page):
    meta = _PAGE_META.get(page)
    if not meta:
        fp_ui.breadcrumb([("Ana Ekran", "home"), ("Bulunamadı", None)])
        return
    label, section, primary = meta
    trail = [("Ana Ekran", "home")]
    if section and section != label:
        trail.append((section, primary if primary != page else None))
    trail.append((label, None))
    fp_ui.breadcrumb(trail)


def _topbar_session_line():
    """Üst bardaki 'sıradaki seans' metni — gerçek takvim verisi, kırılgan değil."""
    try:
        event, s_name, s_time, is_live = get_current_or_next_event()
        loc = str(event.get('Location') or event.get('EventName') or '').strip()
        if not loc:
            return "", False
        if is_live:
            return f"{loc} · {s_name} · CANLI", True
        delta = s_time - datetime.datetime.now(datetime.timezone.utc)
        total = max(0, int(delta.total_seconds()))
        d, h = total // 86400, (total % 86400) // 3600
        return (f"{loc} · {s_name} · {d}g {h}s" if d else f"{loc} · {s_name} · {h}s"), False
    except Exception:
        return "", False


_sesh_line, _sesh_live = _topbar_session_line()
fp_ui.topbar(_nav_now, fp_i18n.get_lang(), standalone=NAV_STANDALONE, groups=NAV_GROUPS,
             session_line=_sesh_line, session_live=_sesh_live)


# --- Üst bar navigasyonu: reload YERİNE aynı-oturum rerun --------------------
# Bardaki <a> linkleri (core/ui.py _TOPBAR_ACTIVE_JS) tam sayfa yeniden yükleme
# yapmaz; görünmez ama DOM'da duran şu Streamlit butonlarını JS ile tıklar.
# Sonuç: bar hiç yeniden kurulmaz, eski içerik yenisi gelene kadar durur.
# JS köprüsü çökerse <a href> normal reload olarak çalışır (graceful degrade).
def _nav_jump(_p):
    if _p in VALID_PAGES:
        st.session_state['page'] = _p
        st.query_params['p'] = _p


def _lang_jump(_l):
    if _l in ('tr', 'en') and fp_i18n.get_lang() != _l:
        fp_i18n.set_lang(_l)


for _pk in sorted(VALID_PAGES):
    st.button("·", key=f"njp_{_pk}", on_click=_nav_jump, args=(_pk,))
for _lk in ('tr', 'en'):
    st.button("·", key=f"njl_{_lk}", on_click=_lang_jump, args=(_lk,))

# İç sayfa arka planı — ana ekran (hero) hariç her menü sayfasında aynı
# sabit F1 telemetri katmanı.
if _bad_page or st.session_state['page'] != 'home':
    fp_ui.page_background()


# 2. SON TAMAMLANAN SEANSI VE GERÇEK İLK 5'İ ÇEKEN FONKSİYONLAR
@st.cache_data(ttl=120, show_spinner=False)
def get_latest_completed_session(year):
    """Event nesnesini cache'e vermeden, takvimdeki en son biten seansı bulur."""
    now = datetime.datetime.now(datetime.timezone.utc)
    sessions = [
        ("FP1", "FP1", "Session1DateUtc", 2),
        ("FP2", "FP2", "Session2DateUtc", 2),
        ("FP3", "FP3", "Session3DateUtc", 2),
        ("Sıralama", "Q", "Session4DateUtc", 2),
        ("Yarış", "R", "Session5DateUtc", 3),
    ]

    for search_year in [int(year), int(year) - 1]:
        try:
            schedule = fastf1.get_event_schedule(search_year, include_testing=False)
            schedule = schedule[schedule['RoundNumber'] > 0]
        except Exception:
            continue

        completed = []
        for _, event in schedule.iterrows():
            for display_name, session_code, column, duration_hours in sessions:
                session_date = event.get(column)
                if pd.isnull(session_date):
                    continue
                session_time = pd.to_datetime(session_date)
                if session_time.tzinfo is None:
                    session_time = session_time.tz_localize('UTC')
                else:
                    session_time = session_time.tz_convert('UTC')
                if session_time + datetime.timedelta(hours=duration_hours) <= now:
                    completed.append({
                        'year': search_year,
                        'event_name': str(event.get('EventName', '')),
                        'display_name': display_name,
                        'session_code': session_code,
                        'time': session_time,
                    })
        if completed:
            return max(completed, key=lambda item: item['time'])
    return None


@cache_data_safe(ttl=900, on_error=lambda: ([], []), label='top drivers')
def get_real_top_drivers(year, gp_name, session_code):
    """Sahte veri üretmez; Q seansında Q3 turunu ve o turun lastiğini kullanır.

    Kaynak hatası önbelleğe alınmaz (`cache_data_safe`): boş sonuç yerine
    bir sonraki çağrıda yeniden denenir."""
    sess = fastf1.get_session(int(year), gp_name, session_code)
    sess.load(telemetry=False, weather=False, messages=False)
    results = sess.results
    if results is None or results.empty:
        return [], []

    # LED serit tum kadroyu gosterir; ilk 5 degil, tamami sirali.
    results = results.sort_values('Position', na_position='last').head(30)
    drivers_data = []
    race_finish_times = {}
    race_leader_finish = None
    if session_code == 'R':
        for race_code in results['Abbreviation'].dropna().astype(str):
            driver_laps = sess.laps.pick_drivers(race_code).dropna(subset=['Time'])
            if not driver_laps.empty:
                race_finish_times[race_code] = driver_laps.sort_values('LapNumber').iloc[-1]['Time']
        if not results.empty:
            race_leader_finish = race_finish_times.get(str(results.iloc[0].get('Abbreviation', '')).strip())

    for _, row in results.iterrows():
        code = str(row.get('Abbreviation', '')).strip()
        if not code or code == 'nan':
            continue
        position = int(float(row['Position'])) if pd.notnull(row.get('Position')) else len(drivers_data) + 1
        laps = sess.laps.pick_drivers(code)
        chosen_lap = None

        if session_code == 'Q' and pd.notnull(row.get('Q3')):
            q3_laps = laps[laps['LapTime'] == row['Q3']]
            if not q3_laps.empty:
                chosen_lap = q3_laps.iloc[0]
        if chosen_lap is None and not laps.empty:
            chosen_lap = laps.pick_fastest()

        if session_code == 'R':
            # Sonuç paketleri bazı yarışlarda P2+ için toplam yarış süresini
            # yazabiliyor. Bu durumda resmî tur bitiş saatinden gerçek farkı
            # tekrar hesaplarız; asla +59:59 gibi uydurma bir fark göstermeyiz.
            official_result_time = row.get('Time')
            status = str(row.get('Status', '')).strip()
            official_seconds = _timedelta_seconds(official_result_time)
            status_lower = status.lower()
            is_lapped_or_retired = any(token in status_lower for token in ('lap', 'retired', 'accident', 'disqualified', 'withdrawn'))
            if position == 1 and pd.notnull(official_result_time):
                shown_time = format_time(official_result_time)
            elif is_lapped_or_retired:
                shown_time = status or '—'
            else:
                finish_time = _timedelta_seconds(race_finish_times.get(code))
                calculated_gap = finish_time - _timedelta_seconds(race_leader_finish) if finish_time is not None and race_leader_finish is not None else None
                gap = official_seconds
                if gap is None or gap < 0 or gap > 900:
                    gap = calculated_gap
                shown_time = '+' + format_time(pd.to_timedelta(gap, unit='s')) if gap is not None and 0 <= gap <= 900 else (status if status and status_lower != 'nan' else '—')
        else:
            lap_time = chosen_lap['LapTime'] if chosen_lap is not None else row.get('Time')
            if pd.notnull(lap_time):
                if not drivers_data:
                    shown_time = format_time(lap_time)
                else:
                    leader_lap = drivers_data[0].get('_lap_time')
                    shown_time = '+' + format_time(lap_time - leader_lap) if pd.notnull(leader_lap) else format_time(lap_time)
            else:
                shown_time = str(row.get('Status', '—'))

        drivers_data.append({
            'name': f'{position}. {code}',
            'time': shown_time,
            'code': code,
            'tyre': str(chosen_lap.get('Compound', '')) if chosen_lap is not None else '',
            '_lap_time': chosen_lap['LapTime'] if chosen_lap is not None else pd.NaT,
        })
    if not drivers_data:
        return [], []

    for driver in drivers_data:
        driver.pop('_lap_time', None)

    leader = drivers_data[0]
    headline = 'Seans Lideri'
    if session_code == 'Q':
        headline = 'Pole Pozisyonu'
    elif session_code == 'R':
        headline = 'Yarış Lideri'
    summary = [f"{headline}: {leader['code']}"]
    if leader['tyre']:
        summary.append(f"En hızlı tur lastiği: {leader['tyre'].title()}")
    if len(drivers_data) > 1:
        summary.append(
            f"En yakın rakip: {drivers_data[1]['code']} ({drivers_data[1]['time']})"
        )
    return drivers_data, summary


@cache_data_safe(ttl=900, on_error=list, label='session summary')
def get_session_summary(year, gp_name, session_code):
    """Sonucun tekrarı yerine seansın doğrulanmış dikkat çeken anlarını verir."""
    session = fastf1.get_session(int(year), gp_name, session_code)
    session.load(laps=False, telemetry=False, weather=False, messages=True)
    results = session.results
    if results is None or results.empty:
        return []
    summary = []
    messages = getattr(session, 'race_control_messages', None)
    if messages is not None and not getattr(messages, 'empty', True):
        keywords = ('RED FLAG', 'SAFETY CAR', 'VIRTUAL SAFETY', 'CRASH', 'STOPPED', 'SPUN',
                    'YELLOW', 'PENALTY', 'REPRIMAND', 'DISQUALIFIED', 'DRIVE THROUGH', 'STOP AND GO')
        seen_incidents = set()
        for _, row in messages.iloc[::-1].iterrows():
            message = str(row.get('Message', '')).strip()
            if message and any(word in message.upper() for word in keywords):
                car_match = re.search(r'CAR\s+(\d+)\s*\(([^)]+)\)', message.upper())
                driver_label = f"{car_match.group(2)} (#{car_match.group(1)})" if car_match else 'Bir pilot'
                upper = message.upper()
                pen = re.search(r'(\d+)\s*SECOND', upper)
                if 'YELLOW FLAG INFRINGEMENT' in upper:
                    clean = f"⚠️ {driver_label} için sarı bayrak ihlali incelemesi başlatıldı."
                    incident_key = f"yellow-{driver_label}"
                elif 'DISQUALIFIED' in upper:
                    clean = f"⛔ {driver_label} diskalifiye edildi."
                    incident_key = f"dsq-{driver_label}"
                elif 'PENALTY' in upper or 'DRIVE THROUGH' in upper or 'STOP AND GO' in upper:
                    reason = 'kural ihlali'
                    for tag, label in (('TRACK LIMITS', 'pist sınırları'), ('CAUSING A COLLISION', 'çarpışmaya sebep olma'),
                                       ('UNSAFE RELEASE', 'güvensiz bırakma'), ('SPEEDING', 'pit hız limiti'),
                                       ('IGNORING', 'bayrak/işaret ihlali'), ('FALSE START', 'erken start')):
                        if tag in upper:
                            reason = label
                            break
                    amount = f" · {pen.group(1)} sn ceza" if pen else (" · geç-git cezası" if 'DRIVE THROUGH' in upper else "")
                    clean = f"🟥 {driver_label} — {reason}{amount}."
                    incident_key = f"pen-{driver_label}-{reason}"
                elif 'REPRIMAND' in upper:
                    clean = f"🟨 {driver_label} kınama (reprimand) aldı."
                    incident_key = f"repr-{driver_label}"
                elif 'RED FLAG' in upper:
                    clean = '🚩 Seans kırmızı bayrakla durduruldu.'
                    incident_key = 'red-flag'
                elif 'VIRTUAL SAFETY' in upper:
                    clean = '🟡 Sanal Güvenlik Aracı (VSC) uygulandı.'
                    incident_key = 'vsc'
                elif 'SAFETY CAR' in upper:
                    clean = '🚗 Güvenlik Aracı piste çıktı.'
                    incident_key = 'safety-car'
                elif 'SPUN' in upper:
                    clean = f"↪️ {driver_label} spin attı."
                    incident_key = f"spin-{driver_label}"
                elif 'CRASH' in upper or 'STOPPED' in upper:
                    clean = f"⚠️ {driver_label} ile ilgili pistte olay kaydedildi."
                    incident_key = f"incident-{driver_label}"
                else:
                    continue
                if incident_key not in seen_incidents:
                    seen_incidents.add(incident_key)
                    summary.append(clean)
            if len(summary) >= 4:
                break

    ordered = results.sort_values('Position', na_position='last').copy()
    if session_code == 'Q':
        q3_teams = ordered[pd.to_numeric(ordered.get('Position'), errors='coerce') <= 10].groupby('TeamName').size()
        double_q3 = q3_teams[q3_teams >= 2]
        for team_name in double_q3.index[:1]:
            summary.append(f"📈 {team_name}, iki pilotuyla Q3'e kaldı.")
    elif session_code in ['R', 'S']:
        if 'GridPosition' in ordered.columns:
            ordered['gain'] = pd.to_numeric(ordered['GridPosition'], errors='coerce') - pd.to_numeric(ordered['Position'], errors='coerce')
            biggest_gain = ordered.sort_values('gain', ascending=False).iloc[0]
            if pd.notnull(biggest_gain.get('gain')) and biggest_gain['gain'] >= 4:
                summary.append(f"⬆️ {biggest_gain.get('Abbreviation', 'Bir pilot')}, start yerine göre {int(biggest_gain['gain'])} sıra kazandı.")
        # yarisi tamamlayamayanlar
        if 'Status' in ordered.columns:
            _dnf = []
            for _, row in ordered.iterrows():
                if is_dnf_status(row.get('Status', '')):
                    _dnf.append(str(row.get('Abbreviation', '')).strip())
            if _dnf:
                _names = ', '.join(d for d in _dnf[:3] if d)
                _extra = f" +{len(_dnf) - 3}" if len(_dnf) > 3 else ""
                summary.append(f"🔧 Yarışı tamamlayamayan: {_names}{_extra}.")

    return summary[:5]


# 3. OTOMATİK TÜRKÇE ÇEVİRİ MOTORU
def deepl_configured():
    """DeepL API anahtarı tanımlı mı? (Streamlit Secrets veya ortam değişkeni)"""
    return bool(_secret_or_environment('DEEPL_API_KEY'))


@st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)
def _deepl_translate_raw(text):
    """DeepL API (ücretsiz plan da dahil). Anahtar yoksa / hata varsa firlatir.
    Başarılı çeviri 1 hafta önbellekte kalır — aynı başlık tekrar çevrilmez."""
    key = _secret_or_environment('DEEPL_API_KEY')
    if not key:
        raise RuntimeError('DEEPL_API_KEY tanımlı değil')
    host = 'api-free.deepl.com' if key.strip().endswith(':fx') else 'api.deepl.com'
    body = urllib.parse.urlencode({'text': text, 'target_lang': 'TR'}).encode('utf-8')
    req = urllib.request.Request(
        f'https://{host}/v2/translate', data=body,
        headers={'Authorization': f'DeepL-Auth-Key {key}',
                 'Content-Type': 'application/x-www-form-urlencoded',
                 'User-Agent': 'FormulaPaddock/1.0'})
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    out = ((payload.get('translations') or [{}])[0].get('text') or '').strip()
    if not out:
        raise ValueError('DeepL boş çeviri döndü')
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def _translate_to_tr_raw(text):
    """Yedek: ücretsiz Google endpoint'i. Sadece BAŞARILI çeviri önbelleğe girer."""
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=tr&dt=t&q={urllib.parse.quote(text)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    translated = "".join([item[0] for item in data[0] if item[0]])
    if not translated:
        raise ValueError('empty translation')
    return translated


@st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)
def _deepl_translate_batch_v34(texts):
    """Birden çok metni TEK DeepL isteğinde çevirir (haber merkezi için kritik:
    ~40 seri istek yerine 1). `texts` hashable tuple; dönüş aynı sıradadır.
    Başarılı sonuç 1 hafta önbellekte kalır."""
    key = _secret_or_environment('DEEPL_API_KEY')
    if not key:
        raise RuntimeError('DEEPL_API_KEY tanımlı değil')
    items = [str(t or '') for t in texts]
    if not items:
        return ()
    host = 'api-free.deepl.com' if key.strip().endswith(':fx') else 'api.deepl.com'
    fields = [('text', t) for t in items] + [('target_lang', 'TR')]
    body = urllib.parse.urlencode(fields).encode('utf-8')
    req = urllib.request.Request(
        f'https://{host}/v2/translate', data=body,
        headers={'Authorization': f'DeepL-Auth-Key {key}',
                 'Content-Type': 'application/x-www-form-urlencoded',
                 'User-Agent': 'FormulaPaddock/1.0'})
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    out = [(entry.get('text') or '').strip() for entry in (payload.get('translations') or [])]
    if len(out) != len(items):
        raise ValueError('DeepL batch: çeviri sayısı metin sayısıyla uyuşmuyor')
    return tuple(out)


def _gtx_translate_plain(text):
    """Thread-güvenli tekil Google çevirisi (st.session_state'e dokunmaz).
    Yalnızca DeepL toplu isteği düştüğünde paralel yedek olarak kullanılır."""
    clean = str(text or '').strip()
    if not clean:
        return ''
    try:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=tr&dt=t&q="
               + urllib.parse.quote(clean))
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return "".join(seg[0] for seg in data[0] if seg[0]) or clean
    except Exception:
        return clean


def translate_to_tr(text):
    """DeepL anahtarı varsa DeepL, yoksa ücretsiz Google. Başarılı çeviri
    önbellekte yaşar; hata orijinal metni döndürür.

    Devre kesici: kaynak arka arkaya 3 kez patlarsa (haber sayfası tek
    rerun'da ~16 çağrı yapıyor) 5 dakika boyunca ağı hiç denemeyiz."""
    if not text:
        return ""
    try:
        breaker = st.session_state.get('_tr_breaker_until', 0)
    except Exception:
        breaker = 0
    if breaker and time.time() < breaker:
        return text
    _use_deepl = deepl_configured()
    try:
        out = _deepl_translate_raw(text) if _use_deepl else _translate_to_tr_raw(text)
        try:
            st.session_state['_tr_fail_streak'] = 0
        except Exception:
            pass
        return out
    except Exception as error:
        try:
            streak = st.session_state.get('_tr_fail_streak', 0) + 1
            st.session_state['_tr_fail_streak'] = streak
            if streak >= 3:
                st.session_state['_tr_breaker_until'] = time.time() + 300
        except Exception:
            pass
        log_data_error('translate_to_tr (deepl)' if _use_deepl else 'translate_to_tr', error)
        return text

# 4. CANLI F1 HABERLERİ RSS MOTORU

# 5. AKILLI ZAMAN FORMATLAMA
def format_time(td):
    try:
        if pd.isnull(td):
            return "-"
        total_seconds = td.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = total_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:06.3f}"
        else:
            return f"{minutes}:{seconds:06.3f}"
    except (AttributeError, TypeError, ValueError):
        return str(td)

def get_driver_fastest_lap(session, driver, q_sub=None):
    if isinstance(session, openf1_fallback.OpenF1Session):
        return session.fastest_lap(driver, q_sub)
    drv_laps = session.laps.pick_drivers(driver)
    if drv_laps.empty:
        return None
    
    if q_sub and q_sub in ["Q1", "Q2", "Q3"]:
        try:
            res = session.results
            drv_res = res[res['Abbreviation'] == driver]
            if not drv_res.empty and q_sub in drv_res.columns:
                q_time = drv_res.iloc[0][q_sub]
                if pd.notnull(q_time):
                    matched_laps = drv_laps[drv_laps['LapTime'] == q_time]
                    if not matched_laps.empty:
                        return matched_laps.iloc[0]
        except Exception:
            pass
    return drv_laps.pick_fastest()


def get_speed_difference_insight(session, driver_1, driver_2, telemetry_1, telemetry_2):
    """Hız farkının en belirgin olduğu pist bölümünü sade dille açıklar."""
    try:
        maximum_distance = min(telemetry_1['Distance'].max(), telemetry_2['Distance'].max())
        distance = np.linspace(0, maximum_distance, 1200)
        speed_1 = np.interp(distance, telemetry_1['Distance'], telemetry_1['Speed'])
        speed_2 = np.interp(distance, telemetry_2['Distance'], telemetry_2['Speed'])
        difference = speed_1 - speed_2
        point = int(np.argmax(np.abs(difference)))
        faster_driver = driver_1 if difference[point] >= 0 else driver_2
        speed_gap = abs(difference[point])
        location = distance[point]

        try:
            corners = session.get_circuit_info().corners
            nearest = corners.iloc[(corners['Distance'] - location).abs().argsort().iloc[0]]
            corner_name = str(nearest.get('Letter', '')).strip()
            corner_number = int(nearest.get('Number'))
            corner_text = f"Viraj {corner_number}{corner_name}"
        except Exception:
            corner_text = f"pistin {int(location)}. metresinde"

        return (
            f"En büyük hız farkı {corner_text} civarında: "
            f"{faster_driver} yaklaşık {speed_gap:.0f} km/h daha hızlı."
        )
    except Exception:
        return "Bu iki turun belirgin hız farkı olan bölümü hesaplanamadı."


def build_strategy_data(session):
    """Yarıştaki stintleri tablo ve grafik için hazırlar."""
    rows = []
    if session.laps is None or session.laps.empty:
        return pd.DataFrame()
    for driver in session.laps['Driver'].dropna().astype(str).unique():
        laps = session.laps.pick_drivers(driver).dropna(subset=['LapNumber'])
        if laps.empty or 'Stint' not in laps.columns:
            continue
        for stint, group in laps.groupby('Stint'):
            compound = str(group['Compound'].dropna().iloc[0]) if group['Compound'].notna().any() else '-'
            rows.append({
                'Pilot': driver,
                'Stint': int(stint) if pd.notnull(stint) else 0,
                'Lastik': compound,
                'Başlangıç Turu': int(group['LapNumber'].min()),
                'Bitiş Turu': int(group['LapNumber'].max()),
                'Tur Sayısı': int(group['LapNumber'].max() - group['LapNumber'].min() + 1),
            })
    return pd.DataFrame(rows)


def build_strategy_from_laps(laps):
    """Yarış tekrar merkezinde mevcut FastF1 tur tablosundan stintleri çıkarır."""
    if laps is None or laps.empty or 'Stint' not in laps.columns:
        return pd.DataFrame()
    rows = []
    for driver in laps['Driver'].dropna().astype(str).unique():
        driver_laps = laps.pick_drivers(driver).dropna(subset=['LapNumber'])
        if driver_laps.empty:
            continue
        for stint, group in driver_laps.groupby('Stint'):
            compound = str(group['Compound'].dropna().iloc[0]).upper() if group['Compound'].notna().any() else '-'
            rows.append({
                'Pilot': driver,
                'Stint': int(stint) if pd.notnull(stint) else 0,
                'Lastik': compound,
                'Başlangıç': int(group['LapNumber'].min()),
                'Bitiş': int(group['LapNumber'].max()),
                'Tur': int(group['LapNumber'].max() - group['LapNumber'].min() + 1)
            })
    return pd.DataFrame(rows)

# 6. LASTİK ROZETİ (HTML)

# 7. DRIVER RENK VERİSİ


# Güncel 2026 grid. Bu liste, eski sidebar verisinden bağımsız olarak
# Takımlar & Pilotlar merkezinde kullanılır.


# 2026 takım renkleriyle çekilmiş resmî portreler. Eski sürücünün önceki
# takımındaki fotoğrafını göstermek yerine F1 Media'nın 2026 görüntüsünü kullanır.


def current_driver_portrait(team_name, old_image_path):
    """Sürücüyü 2026 takım kıyafetiyle gösteren resmî F1 Media bağlantısı."""
    driver_key = os.path.splitext(os.path.basename(old_image_path))[0].lower()
    team_key = TEAM_MEDIA_NAMES.get(team_name, "")
    if not team_key or not driver_key:
        return MEDIA_DRIVER + old_image_path
    return (
        "https://media.formula1.com/image/upload/c_fill%2Cw_720/q_auto/"
        f"v1740000001/common/f1/2026/{team_key}/{driver_key}/"
        f"2026{team_key}{driver_key}right.webp"
    )


# Verified team leadership is kept separate from the game engineer packages.
# Individual race-engineer assignments are not a stable public roster, so the
# game never invents a real person's identity or photo for that role.


# Takım sayfasındaki kısa biyografiler, sezon sonucu değil kariyer dosyasıdır.
# Böylece pilot kartları yalnızca isim/fotoğraf olmaktan çıkar; her profilde
# izleyenin anlayabileceği bir geçmiş ve hatırlanacak bir yarış anı bulunur.


def driver_career_profile(driver_code):
    return DRIVER_CAREER_PROFILE.get(
        driver_code,
        {
            'wins': '—', 'podiums': '—',
            'bio': 'Bu pilotun kariyer dosyası doğrulanmış detaylarla güncelleniyor.',
            'moment': 'Öne çıkan kariyer anı yakında eklenecek.',
        },
    )


def driver_age(driver_code):
    birthday = DRIVER_BIRTHDAYS.get(driver_code)
    if not birthday:
        return '—'
    born = datetime.date.fromisoformat(birthday)
    today = datetime.date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# 9. CSS TASARIMI
st.markdown("""
<style>
    .stApp {
        background:
            radial-gradient(circle at 88% -10%, rgba(36, 99, 235, .13), transparent 30%),
            radial-gradient(circle at 12% 8%, rgba(16, 185, 129, .08), transparent 23%),
            #07090d;
        color: #f2f5f8;
    }
    section[data-testid="stSidebar"] {
        background-color: #11161f !important;
        border-right: 1px solid #1E293B;
    }
    
    .f1-header {
        background: rgba(15, 23, 42, .72);
        border: 1px solid #273449;
        border-radius: 14px;
        padding: 12px 18px;
        margin-bottom: 22px;
        box-shadow: 0 10px 30px rgba(0,0,0,.22);
        backdrop-filter: blur(8px);
    }
    .f1-header h1 {
        color: #EAF2FF !important;
        margin: 0;
        font-size: 1.05rem;
        letter-spacing: .13em;
        font-weight: 900;
    }
    .f1-header p {
        color: #91a4bd;
        margin: 3px 0 0;
        font-size: .78rem;
    }
    .hud-label { color:#7f93ab; font-size:.72rem; font-weight:800; letter-spacing:.12em; }
    .hud-value { color:#f2f5f8; font-size:1rem; font-weight:800; margin-top:3px; }
    .hud-card {
        background:rgba(15,23,42,.78); border:1px solid #273449; border-radius:12px;
        padding:14px 16px; box-shadow:0 9px 22px rgba(0,0,0,.16);
    }
    .history-copy { color:#a6b6c9; line-height:1.55; font-size:.92rem; }
    .driver-meta { color:#90a5bc; font-size:.8rem; margin-top:4px; }
    .term-badge { display:inline-block; color:#60a5fa; border:1px solid #1d4ed8; border-radius:99px; padding:2px 7px; font-size:.68rem; font-weight:800; }
    .new-badge { display:inline-block; color:#7fffd4; border:1px solid #0f766e; border-radius:99px; padding:2px 7px; font-size:.68rem; font-weight:800; }
    div[data-testid="stButton"] > button {
        border-radius:10px; border:1px solid #34445c; background:#111927; color:#e5eefb;
        font-weight:700;
    }
    div[data-testid="stButton"] > button:hover { border-color:#60a5fa; color:#fff; }
    div[data-testid="stDataFrame"] { border:1px solid #263449; border-radius:10px; overflow:hidden; }
    h1, h2, h3 { letter-spacing:-.02em; }
    .streamlit-expanderHeader { color:#dce8f7; }
    section[data-testid="stSidebar"] button { background:#111927 !important; }
    section[data-testid="stSidebar"] button:hover { border-color:#60a5fa !important; }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg,#0e1728 0%,#11161f 100%) !important;
    }

    .news-card {
        background: #11161f;
        border: 1px solid #1E293B;
        border-left: 4px solid #E10600;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
        transition: transform 0.2s;
        height: 100%;
        box-sizing: border-box;
    }
    .news-card:hover {
        border-color: #E10600;
        transform: translateX(4px);
    }
    .news-title {
        font-size: 1.02rem;
        font-weight: 800;
        color: #FFFFFF;
        margin-bottom: 6px;
        line-height: 1.35;
    }
    .news-date {
        font-size: 0.75rem;
        color: #E10600;
        font-weight: 700;
        margin-bottom: 8px;
    }
    .news-desc {
        font-size: 0.88rem;
        color: #94A3B8;
        line-height: 1.4;
        margin-bottom: 10px;
    }
    .news-link {
        color: #38BDF8;
        font-size: 0.82rem;
        font-weight: 700;
        text-decoration: none;
    }

    .metric-card {
        background: #11161f;
        border: 1px solid #1E293B;
        border-radius: 10px;
        padding: 14px;
        text-align: center;
    }
    .metric-card .title {
        color: #94A3B8;
        font-size: 0.8rem;
        font-weight: 700;
    }
    .metric-card .value {
        color: #FFFFFF;
        font-size: 1.6rem;
        font-weight: 800;
        margin-top: 4px;
        font-family: monospace;
    }
    .driver-card {
        background: #1E293B;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 12px;
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 8px;
    }
    .driver-img {
        width: 55px;
        height: 55px;
        border-radius: 50%;
        background: #0F172A;
        object-fit: cover;
        border: 2px solid #E10600;
    }
    @media (max-width: 760px) {
        .f1-header { padding: 14px 16px; }
        .f1-header h1 { font-size: 1.35rem; }
        .f1-header p { font-size: 0.85rem; }
        .news-card { min-height: auto; }
        div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
    }
</style>
""", unsafe_allow_html=True)

# 10. YARIŞ TAKVİMİ FONKSİYONU
st.markdown("""
<style>
@media (max-width: 760px) {
  .block-container { padding: .8rem .7rem 4rem !important; }
  section[data-testid="stSidebar"] { min-width: min(82vw, 320px) !important; }
  div[data-testid="stHorizontalBlock"] { gap: .6rem !important; }
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] { min-width: 100% !important; }
  div[data-testid="stTabs"] button { font-size: .75rem !important; padding: .5rem .45rem !important; }
  div[data-testid="stDataFrame"] { font-size: .82rem; }
  h1 { font-size: 1.55rem !important; } h2 { font-size: 1.3rem !important; } h3 { font-size: 1.12rem !important; }
}
</style>
""", unsafe_allow_html=True)


# redesign: site_theme_css() kaldirildi — tema tamamen core/theme.py'de.


@cache_data_safe(ttl=3600, on_error=list, label='season schedule')
def get_season_schedule(year):
    schedule = fastf1.get_event_schedule(year, include_testing=False)
    schedule = schedule[schedule['RoundNumber'] > 0]
    names = schedule['EventName'].dropna().astype(str).tolist()
    if not names:
        raise RuntimeError('boş sezon takvimi')
    return names


@cache_data_safe(ttl=3600, on_error=list, label='calendar details')
def get_calendar_details(year):
    schedule = fastf1.get_event_schedule(int(year), include_testing=False)
    schedule = schedule[schedule['RoundNumber'] > 0].copy()
    records = schedule.to_dict('records')
    if not records:
        raise RuntimeError('boş takvim')
    return records


def event_session_cards(event):
    cards = [
        # Süreler muhafazakâr tutulur: devam eden bir seansı yanlışlıkla
        # "tamamlandı" kabul edip geçmiş sonuç / replay olarak göstermeyiz.
        ("FP1", "Session1DateUtc", "FP1", 120),
        ("FP2", "Session2DateUtc", "FP2", 120),
        ("FP3", "Session3DateUtc", "FP3", 120),
        ("Sıralama", "Session4DateUtc", "Q", 150),
        ("Yarış", "Session5DateUtc", "R", 210),
    ]
    now = datetime.datetime.now(datetime.timezone.utc)
    items = []
    for title, column, code, duration_minutes in cards:
        value = event.get(column)
        if pd.isnull(value):
            continue
        time_value = pd.to_datetime(value)
        time_value = time_value.tz_localize('UTC') if time_value.tzinfo is None else time_value.tz_convert('UTC')
        estimated_end = time_value + datetime.timedelta(minutes=duration_minutes)
        if now < time_value:
            status = 'Yaklaşıyor'
        elif now < estimated_end:
            status = 'Canlı / sürüyor'
        else:
            status = 'Tamamlandı'
        items.append({
            'title': title,
            'code': code,
            'time': time_value,
            'estimated_end': estimated_end,
            'status': status,
        })
    return items


@cache_data_safe(ttl=900, on_error=lambda: (pd.DataFrame(), pd.DataFrame()), label='session results table')
def get_session_results_table(year, event_name, session_code):
    """Seans türüne göre yalnızca anlamlı sonuç sütunlarını gösterir.

    Antrenmanların resmî sonuç tablosunda pozisyon ve zaman boş olabilir. Bu
    yüzden FP seanslarında hızlı turlardan kendi sıralamamızı üretiriz.
    """
    session = fastf1.get_session(int(year), event_name, session_code)
    session.load(telemetry=False, weather=False, messages=False)
    results = session.results
    laps = session.laps.copy() if session.laps is not None else pd.DataFrame()

    if session_code in ['FP1', 'FP2', 'FP3']:
        practice_rows = []
        team_by_driver = {}
        if results is not None and not results.empty:
            for _, row in results.iterrows():
                team_by_driver[str(row.get('Abbreviation', ''))] = str(row.get('TeamName', '—'))

        for driver in sorted(laps['Driver'].dropna().astype(str).unique()) if not laps.empty else []:
            driver_laps = laps.pick_drivers(driver)
            fastest = driver_laps.pick_fastest()
            if fastest is None or pd.isnull(fastest.get('LapTime')):
                continue
            practice_rows.append({
                'Pilot': driver,
                'Takım': team_by_driver.get(driver, '—'),
                'En Hızlı Tur': format_time(fastest['LapTime']),
                'Lastik': str(fastest.get('Compound', '—')).title(),
                '_lap_seconds': fastest['LapTime'].total_seconds(),
            })

        if not practice_rows:
            return pd.DataFrame(), laps
        table = pd.DataFrame(practice_rows).sort_values('_lap_seconds').reset_index(drop=True)
        table.insert(0, 'Sıra', table.index + 1)
        return table.drop(columns=['_lap_seconds']), laps

    if results is None or results.empty:
        return pd.DataFrame(), laps

    if session_code == 'Q':
        columns = [column for column in ['Position', 'Abbreviation', 'TeamName', 'Q1', 'Q2', 'Q3'] if column in results.columns]
    elif session_code in ['R', 'S']:
        columns = [column for column in ['Position', 'Abbreviation', 'TeamName', 'Time', 'Status', 'Points'] if column in results.columns]
    else:
        columns = [column for column in ['Position', 'Abbreviation', 'TeamName', 'Time', 'Status'] if column in results.columns]

    table = results[columns].copy().sort_values('Position', na_position='last')
    for column in ['Time', 'Q1', 'Q2', 'Q3']:
        if column in table.columns:
            table[column] = table[column].apply(lambda value: format_time(value) if pd.notnull(value) else '—')
    table = table.rename(columns={
        'Position': 'Sıra', 'Abbreviation': 'Pilot', 'TeamName': 'Takım',
        'Time': 'Zaman', 'Status': 'Durum', 'Points': 'Puan'
    })
    return table.fillna('—'), laps


def translate_race_control_message(message):
    """Yaygın FIA Race Control kalıplarını anlamını kaybetmeden Türkçeleştirir."""
    text = str(message or '').strip()
    if not text:
        return ''
    upper = text.upper()
    car_match = re.search(r'CAR\s+(\d+)\s*\(([^)]+)\)', upper)
    driver_text = f"{car_match.group(2)} / araç {car_match.group(1)}" if car_match else 'ilgili araç'
    if 'WILL BE INVESTIGATED AFTER THE SESSION' in upper:
        reason = 'sarı bayrak ihlali' if 'YELLOW FLAG' in upper else 'olay'
        return f"FIA hakemleri {driver_text} için {reason} nedeniyle seans sonrasında inceleme yapacak."
    if 'INCIDENT' in upper and 'NOTED' in upper:
        reason = 'sarı bayrak ihlali' if 'YELLOW FLAG' in upper else 'olay'
        return f"FIA hakemleri {driver_text} ile ilgili {reason} olayını kayda aldı."
    replacements = [
        ('SAFETY CAR DEPLOYED', 'Güvenlik aracı piste çıktı.'),
        ('VIRTUAL SAFETY CAR DEPLOYED', 'Sanal Güvenlik Aracı uygulaması başladı.'),
        ('VIRTUAL SAFETY CAR ENDING', 'Sanal Güvenlik Aracı uygulaması bitiyor.'),
        ('RED FLAG', 'Kırmızı bayrak.'),
        ('YELLOW FLAG', 'Sarı bayrak.'),
        ('TRACK CLEAR', 'Pist yeniden açık.'),
    ]
    for english, turkish in replacements:
        if english in upper:
            return turkish
    return text


@cache_data_safe(ttl=1800, on_error=list, label='session story')
def get_session_story(year, event_name, session_code):
    """Seans sonucunu ve varsa Race Control notlarını kısa, doğrulanabilir hikâyeye çevirir."""
    session = fastf1.get_session(int(year), event_name, session_code)
    session.load(telemetry=False, weather=False, messages=True)
    results = session.results.copy() if session.results is not None else pd.DataFrame()
    story = []
    if not results.empty:
        results = results.sort_values('Position', na_position='last')
        leader = results.iloc[0]
        code = str(leader.get('Abbreviation', '')).strip()
        team = str(leader.get('TeamName', '')).strip()
        if session_code == 'Q' and code:
            story.append({'kind': 'POLE', 'text': f"Pole pozisyonu {code} tarafından alındı ({team})."})
            if 'Q3' in results.columns:
                q3 = results[results['Q3'].notna()]
                team_counts = q3.groupby('TeamName')['Abbreviation'].count() if not q3.empty else pd.Series(dtype=int)
                for listed_team, count in team_counts.items():
                    if int(count) >= 2:
                        story.append({'kind': 'Q3', 'text': f"{listed_team}, iki pilotuyla Q3'e kaldı."})
        elif session_code in ['FP1', 'FP2', 'FP3'] and code:
            story.append({'kind': 'PACE', 'text': f"Seansın en hızlı pilotu {code} ({team}) oldu."})
        elif session_code in ['R', 'S'] and code:
            story.append({'kind': 'WIN', 'text': f"Seansı {code} ({team}) kazandı."})

    messages = getattr(session, 'race_control_messages', None)
    if isinstance(messages, pd.DataFrame) and not messages.empty:
        text_column = next((column for column in ['Message', 'Text'] if column in messages.columns), None)
        if text_column:
            recent = messages[text_column].dropna().astype(str).tolist()
            seen = set()
            for item in reversed(recent):
                translated = translate_race_control_message(item)
                if translated and translated not in seen:
                    seen.add(translated)
                    story.append({'kind': 'RACE CONTROL', 'text': translated})
                if len([entry for entry in story if entry['kind'] == 'RACE CONTROL']) >= 3:
                    break
    return story[:5]


def round_badge(event):
    """Geniş tabloda okunabilen ülke bayrağı + pist kısa adı."""
    country = str(event.get('Country', ''))
    location = str(event.get('Location', event.get('EventName', 'GP')))
    short_name = re.sub(r'[^A-Za-z0-9]', '', location).upper()[:4] or 'GP'
    return f"{COUNTRY_FLAGS.get(country, '🏁')} {short_name}"


def round_key(event):
    country = str(event.get('Country', ''))
    location = str(event.get('Location', event.get('EventName', 'GP')))
    return f"{COUNTRY_CODES.get(country, 'xx')}-{re.sub(r'[^a-z0-9]', '', location.lower())[:6]}"


def points_value(value):
    """FastF1'in boş/NA puanlarını güvenli şekilde sıfır yapar ('nan' metni dahil)."""
    try:
        result = 0.0 if pd.isnull(value) else float(value)
        return 0.0 if pd.isnull(result) else result
    except Exception:
        return 0.0


def is_dnf_status(status):
    """Yarışı bitirememe (DNF) durumu mu?

    'Finished' bitirmedir. '+1 Lap' / '+2 Laps' geride kalmıştır ama YARIŞI
    BİTİRMİŞTİR (DNF değil). 'Lapped' de bitiştir. Diğer her şey (Accident,
    Engine, Collision, Retired, Withdrew, Disqualified...) DNF sayılır.
    Boş durum bilinmiyor kabul edilir -> DNF değil.
    """
    text = str(status or '').strip().lower()
    if not text or text == 'finished':
        return False
    if text.startswith('+') or 'lap' in text:
        return False
    return True


def format_finish_position(value):
    """Sıra verisini P14.0 değil, okunabilir Türkçe sonuç biçimine dönüştürür."""
    try:
        numeric = float(value)
        if np.isfinite(numeric):
            return f"{int(numeric)}. sırada"
    except (TypeError, ValueError):
        pass
    text = str(value or '').strip()
    return text if text else 'sıralama verisi olmadan'


def clean_position_value(value):
    """1.0/P1.0 gibi FastF1 kaynaklı görünümü yalnızca P1 biçimine çevirir."""
    try:
        numeric = float(value)
        if np.isfinite(numeric):
            return f"P{int(numeric)}"
    except (TypeError, ValueError):
        pass
    text = str(value or '').strip().upper()
    return text if text else '—'


def get_championship_round_v19(year, event_name):
    """Bir GP'yi ayrı önbelleğe alır; tek bir yarışın hatası tüm Puan Merkezi'ni kilitlemez.

    Geçici hata artık önbelleğe alınmaz — o GP bir sonraki açılışta yeniden denenir."""
    try:
        return _championship_round_raw_v33(int(year), event_name)
    except Exception as error:
        log_data_error('championship round', error)
        return {'ok': False, 'race': [], 'sprint': []}


@st.cache_data(ttl=21600, show_spinner=False)
def _championship_round_raw_v33(year, event_name):
    output = {'ok': False, 'race': [], 'sprint': []}
    race = fastf1.get_session(int(year), event_name, 'R')
    race.load(laps=False, telemetry=False, weather=False, messages=False)
    results = race.results
    if results is None or results.empty:
        raise RuntimeError(f'{event_name}: doğrulanmış yarış sonucu henüz yok')
    for _, row in results.iterrows():
        code = str(row.get('Abbreviation', '')).strip()
        if not code or code == 'nan':
            continue
        position = pd.to_numeric(row.get('Position'), errors='coerce')
        output['race'].append({
            'code': code,
            'team': str(row.get('TeamName', '—')).strip(),
            'position': str(int(position)) if pd.notna(position) else 'DNF',
            'points': points_value(row.get('Points', 0)),
        })
    output['ok'] = bool(output['race'])
    if not output['ok']:
        raise RuntimeError(f'{event_name}: sonuç satırı ayrıştırılamadı')

    try:
        sprint = fastf1.get_session(int(year), event_name, 'S')
        sprint.load(laps=False, telemetry=False, weather=False, messages=False)
        results = sprint.results
        if results is not None and not results.empty:
            for _, row in results.iterrows():
                code = str(row.get('Abbreviation', '')).strip()
                if not code or code == 'nan':
                    continue
                position = pd.to_numeric(row.get('Position'), errors='coerce')
                output['sprint'].append({
                    'code': code,
                    'position': str(int(position)) if pd.notna(position) else 'DNF',
                    'points': points_value(row.get('Points', 0)),
                })
    except Exception:
        pass
    return output


@st.cache_data(ttl=21600, show_spinner=False)
def get_championship_data_v19(year):
    """V19 Puan Merkezi: GP bazlı önbellek, sprint ayrımı ve hataya dayanıklı tam sezon matrisi."""
    now = datetime.datetime.now(datetime.timezone.utc)
    schedule = get_calendar_details(int(year))
    completed = []
    for event in schedule:
        race_date = event.get('Session5DateUtc')
        if pd.isnull(race_date):
            continue
        race_time = pd.to_datetime(race_date)
        race_time = race_time.tz_localize('UTC') if race_time.tzinfo is None else race_time.tz_convert('UTC')
        if race_time + datetime.timedelta(hours=3) <= now:
            completed.append(event)

    driver_totals, team_totals, per_driver = {}, {}, {}
    rounds = []
    for event in completed:
        event_name = str(event.get('EventName', '')).strip()
        if not event_name:
            continue
        result = get_championship_round_v19(int(year), event_name)
        if not result.get('ok'):
            continue
        round_info = {
            'event_name': event_name,
            'badge': round_badge(event),
            'key': round_key(event),
            'country_code': COUNTRY_CODES.get(str(event.get('Country', '')), 'un'),
            'has_sprint': bool(result.get('sprint')),
        }
        event_entries = {}
        for row in result.get('race', []):
            code, team = row['code'], row['team']
            event_entries[code] = {
                'race': row['position'], 'sprint': '', 'team': team,
                'race_points': row.get('points', 0), 'sprint_points': '',
            }
            driver_totals.setdefault(code, {'Pilot': code, 'Takım': team, 'Puan': 0.0})
            driver_totals[code]['Puan'] += float(row.get('points', 0))
            team_totals[team] = team_totals.get(team, 0.0) + float(row.get('points', 0))
        for row in result.get('sprint', []):
            code = row['code']
            if code not in event_entries:
                continue
            event_entries[code]['sprint'] = row['position']
            event_entries[code]['sprint_points'] = row.get('points', 0)
            driver_totals[code]['Puan'] += float(row.get('points', 0))
            team = event_entries[code]['team']
            team_totals[team] = team_totals.get(team, 0.0) + float(row.get('points', 0))
        for code, entry in event_entries.items():
            per_driver.setdefault(code, {})[event_name] = entry
        rounds.append(round_info)

    driver_rows = sorted(driver_totals.values(), key=lambda item: (-item['Puan'], item['Pilot']))
    for index, row in enumerate(driver_rows, start=1):
        row['Sıra'] = index
        row['Puan'] = int(row['Puan']) if float(row['Puan']).is_integer() else round(row['Puan'], 1)
    team_rows = [{'Takım': team, 'Puan': int(points) if float(points).is_integer() else round(points, 1)} for team, points in team_totals.items()]
    team_rows.sort(key=lambda item: (-item['Puan'], item['Takım']))
    for index, row in enumerate(team_rows, start=1):
        row['Sıra'] = index

    matrix_rows, points_matrix_rows = [], []
    for row in driver_rows:
        matrix = {'Pilot': row['Pilot'], 'Takım': row['Takım'], 'Puan': row['Puan']}
        points_matrix = {'Pilot': row['Pilot'], 'Takım': row['Takım'], 'Puan': row['Puan']}
        for event in rounds:
            entry = per_driver.get(row['Pilot'], {}).get(event['event_name'])
            if not entry:
                matrix[event['key']] = '—'
                points_matrix[event['key']] = '—'
            elif event['has_sprint']:
                matrix[event['key']] = f"{entry['race']} / {entry['sprint'] or '—'}"
                points_matrix[event['key']] = f"{entry['race_points']} / {entry['sprint_points'] if entry['sprint_points'] != '' else '—'}"
            else:
                matrix[event['key']] = entry['race']
                points_matrix[event['key']] = entry['race_points']
        matrix_rows.append(matrix)
        points_matrix_rows.append(points_matrix)
    return (
        pd.DataFrame(driver_rows),
        pd.DataFrame(team_rows),
        pd.DataFrame(matrix_rows),
        pd.DataFrame(points_matrix_rows),
        rounds,
    )


def canonical_team_name(team_name):
    raw = str(team_name or '').strip()
    if raw in TEAM_DIRECTORY_2026:
        return raw
    return TEAM_NAME_ALIASES.get(raw, raw)


def team_colour(team_name):
    return TEAM_DIRECTORY_2026.get(canonical_team_name(team_name), {}).get('color', '#94a3b8')


# FastF1 2023+ sonuç satırında gerçek sezon livery rengi (`TeamColor`) zaten var;
# bu tablo yalnızca daha eski yıllarda ve o alanın boş geldiği durumlarda kullanılır.
# Anahtarlar FastF1'in o sezon döndürdüğü ham TeamName değerleridir.
_SEASON_TEAM_COLOURS = {
    2018: {'Mercedes': '#00D2BE', 'Ferrari': '#DC0000', 'Red Bull Racing': '#1E41FF',
           'Renault': '#FFF500', 'Haas F1 Team': '#828A8F', 'McLaren': '#FF8700',
           'Force India': '#F596C8', 'Racing Point': '#F596C8', 'Toro Rosso': '#469BFF',
           'Williams': '#FFFFFF', 'Sauber': '#9B0000'},
    2019: {'Mercedes': '#00D2BE', 'Ferrari': '#DC0000', 'Red Bull Racing': '#1E41FF',
           'Renault': '#FFF500', 'Haas F1 Team': '#B6BABD', 'McLaren': '#FF8700',
           'Racing Point': '#F596C8', 'Toro Rosso': '#469BFF', 'Williams': '#FFFFFF',
           'Alfa Romeo Racing': '#9B0000'},
    2020: {'Mercedes': '#00D2BE', 'Ferrari': '#C00000', 'Red Bull Racing': '#0600EF',
           'Renault': '#FFF500', 'Haas F1 Team': '#B6BABD', 'McLaren': '#FF8700',
           'Racing Point': '#F596C8', 'AlphaTauri': '#C8C8C8', 'Williams': '#0082FA',
           'Alfa Romeo Racing': '#960000'},
    2021: {'Mercedes': '#00D2BE', 'Ferrari': '#DC0000', 'Red Bull Racing': '#0600EF',
           'Alpine': '#0090FF', 'Aston Martin': '#006F62', 'Haas F1 Team': '#FFFFFF',
           'McLaren': '#FF8700', 'AlphaTauri': '#2B4562', 'Williams': '#005AFF',
           'Alfa Romeo Racing': '#900000'},
    2022: {'Mercedes': '#6CD3BF', 'Ferrari': '#F91536', 'Red Bull Racing': '#1E41FF',
           'Alpine': '#2293D1', 'Aston Martin': '#2D826D', 'Haas F1 Team': '#B6BABD',
           'McLaren': '#FF8700', 'AlphaTauri': '#5E8FAA', 'Williams': '#37BEDD',
           'Alfa Romeo': '#900000', 'Alfa Romeo Racing': '#900000'},
    2023: {'Mercedes': '#6CD3BF', 'Ferrari': '#F91536', 'Red Bull Racing': '#3671C6',
           'Alpine': '#2293D1', 'Aston Martin': '#358C75', 'Haas F1 Team': '#B6BABD',
           'McLaren': '#FF8000', 'AlphaTauri': '#5E8FAA', 'Williams': '#37BEDD',
           'Alfa Romeo': '#C92D4B'},
    2024: {'Mercedes': '#27F4D2', 'Ferrari': '#E8002D', 'Red Bull Racing': '#3671C6',
           'Alpine': '#0093CC', 'Aston Martin': '#229971', 'Haas F1 Team': '#B6BABD',
           'McLaren': '#FF8000', 'RB': '#6692FF', 'Williams': '#64C4FF',
           'Kick Sauber': '#52E252'},
    2025: {'Mercedes': '#27F4D2', 'Ferrari': '#E8002D', 'Red Bull Racing': '#3671C6',
           'Alpine': '#00A1E8', 'Aston Martin': '#229971', 'Haas F1 Team': '#B6BABD',
           'McLaren': '#FF8000', 'Racing Bulls': '#6C98FF', 'RB': '#6C98FF',
           'Williams': '#1868DB', 'Kick Sauber': '#52E252'},
}


def season_team_colour(team_name, year):
    """O sezona ait takım livery rengi; tabloda yoksa güncel (2026) renge düşer."""
    try:
        table = _SEASON_TEAM_COLOURS.get(int(year))
    except (TypeError, ValueError):
        table = None
    if table:
        raw = str(team_name or '').strip()
        if raw in table:
            return table[raw]
        canon = canonical_team_name(team_name)
        if canon in table:
            return table[canon]
    return team_colour(team_name)


def _replay_driver_visual_v34(result, team_name, year):
    """SEÇİLEN SEZONA ait pilot görselleri — yalnızca o seansın veri satırından.
    Renk: FastF1/OpenF1 `TeamColor` (2019+ gerçek livery) -> sezon renk tablosu.
    Fotoğraf: yalnızca o seansın `HeadshotUrl`'ü; yoksa BOŞ (2026 portresine
    asla düşmez, aksi halde geçmiş yıllarda güncel foto görünüyordu)."""
    def _val(*names):
        for name in names:
            try:
                value = result.get(name)
            except Exception:
                value = None
            text = '' if value is None else str(value).strip()
            if text and text.lower() != 'nan':
                return text
        return ''

    code = _val('Abbreviation') or '—'
    raw_colour = _val('TeamColor')
    colour = ('#' + raw_colour.lstrip('#')) if raw_colour else season_team_colour(team_name, year)
    name = _val('FullName') or ' '.join(part for part in (_val('FirstName'), _val('LastName')) if part) or code
    flag = _val('CountryCode').lower() or DRIVER_DISPLAY.get(code, ('', ''))[0]
    photo = _season_headshot_url_v35(year, name) or _val('HeadshotUrl')   # önce o sezonun portresi
    return {
        'name': name,
        'photo': photo,
        'flag': flag,
        'number': _val('DriverNumber') or '—',
        'age': '—',
        'colour': colour,
    }


def _season_headshot_url_v35(year, name):
    """F1.com'un O SEZONA ait sürücü portresi — o yılın takım tulumuyla çekilmiş.
    Kalıp: /drivers/{yıl}Drivers/{soyadı}.jpg.transform/2col/image.jpg
    2019–2025 için doğrulandı; aralık dışında None döner (ham HeadshotUrl kullanılır)."""
    try:
        yr = int(year)
    except (TypeError, ValueError):
        return None
    if yr < 2019 or yr > 2025:
        return None
    parts = [p for p in str(name or '').replace('.', ' ').split() if p]
    if not parts:
        return None
    surname = '-'.join(parts[1:]) if len(parts) > 1 else parts[0]
    surname = unicodedata.normalize('NFKD', surname).encode('ascii', 'ignore').decode('ascii').lower()
    surname = re.sub(r'[^a-z-]', '', surname).strip('-')
    if not surname:
        return None
    return (
        "https://www.formula1.com/content/dam/fom-website/drivers/"
        f"{yr}Drivers/{surname}.jpg.transform/2col/image.jpg"
    )


def championship_matrix_html(matrix, rounds):
    """Şampiyona geçmişini Excel tablosu yerine yatay HUD tablosu olarak üretir."""
    if matrix.empty:
        return ''
    matrix = matrix.copy()
    def clean_cell(value):
        return re.sub(r'(?<![0-9])(-?[0-9]+)\.0(?=$|[^0-9])', r'\1', str(value))
    for column in matrix.columns:
        matrix[column] = matrix[column].map(clean_cell)
    round_columns = [round_item for round_item in rounds if round_item['key'] in matrix.columns]
    headers = ''.join(
        f"<th title='{html_lib.escape(round_item['event_name'])}'><img class='flag' src='https://flagcdn.com/w40/{round_item['country_code']}.png' alt='{round_item['country_code']}'></th>"
        for round_item in round_columns
    )
    rows = []
    for index, (_, row) in enumerate(matrix.iterrows(), start=1):
        code = str(row.get('Pilot', ''))
        flag_code, display_name = DRIVER_DISPLAY.get(code, ('un', code))
        team = str(row.get('Takım', '—'))
        colour = team_colour(team)
        cell_html = ''.join(f"<td>{html_lib.escape(str(row.get(round_item['key'], '—')))}</td>" for round_item in round_columns)
        rows.append(
            f"<tr><td class='rank'>{index}</td><td class='driver'><span><img class='flag driver-flag' src='https://flagcdn.com/w40/{flag_code}.png' alt='{flag_code}'> {html_lib.escape(display_name)}</span><small style='color:{colour}'>{html_lib.escape(team)}</small></td>"
            f"<td class='points'>{html_lib.escape(str(row.get('Puan', '—')))}</td>{cell_html}</tr>"
        )
    return f"""
    <style>
        body{{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}}
        /* Dikey kaydırma ana Streamlit sayfasında kalır: 13. pilotta ikinci bir
           küçük kaydırma alanı oluşmaz. Yalnızca çok geniş yarış sütunları yatay kayar. */
        .matrix-wrap{{overflow-x:auto;overflow-y:visible;max-height:none;border:1px solid #26313f;border-radius:12px;background:#11161f}}
        table{{border-collapse:separate;border-spacing:0;min-width:1180px;width:100%;font-size:14px}}
        th{{position:sticky;top:0;background:#161d28;color:#9fb0c0;padding:13px 10px;text-align:center;font-size:11px;letter-spacing:.08em;border-bottom:1px solid #26313f;z-index:2}}
        td{{padding:12px 10px;text-align:center;border-bottom:1px solid #1b2330;color:#f2f5f8;font-weight:700}}
        tr:last-child td{{border-bottom:0}} tr:hover td{{background:#1e2836}}
        .rank{{position:sticky;left:0;z-index:1;background:#11161f;width:42px;color:#9fb0c0}}
        .driver{{position:sticky;left:42px;z-index:1;background:#11161f;text-align:left;min-width:165px}}
        .driver span{{display:block;font-weight:900}} .driver small{{display:block;margin-top:4px;font-size:11px;font-weight:800}}
        .flag{{width:22px;height:15px;object-fit:cover;border-radius:2px;vertical-align:middle;box-shadow:0 1px 4px rgba(0,0,0,.35)}} .driver-flag{{width:18px;height:12px;margin-right:4px}}
        .points{{position:sticky;left:207px;z-index:1;background:#11161f;min-width:54px;color:#ffffff}}
        th.sticky-rank{{left:0;z-index:3}} th.sticky-driver{{left:42px;z-index:3;text-align:left}} th.sticky-points{{left:207px;z-index:3}}
    </style>
    <div class='matrix-wrap'><table><thead><tr><th class='sticky-rank'>SIRA</th><th class='sticky-driver'>PİLOT</th><th class='sticky-points'>PUAN</th>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    """


def championship_matrix_component_height(matrix):
    """Şampiyona tablosundaki tüm pilotları tek, normal sayfa kaydırmasında tutar."""
    row_count = len(matrix) if matrix is not None else 0
    return min(1600, max(340, 115 + row_count * 58))


def constructor_hud_html(standings):
    """Takım puanlarını logo kaymadan, 2-1-3 podyum ve HUD kartlarıyla gösterir."""
    if standings.empty:
        return ''

    def card(row, podium=False):
        team_name = str(row['Takım'])
        colour = team_colour(team_name)
        logo = OFFICIAL_TEAM_LOGOS.get(team_name, '')
        logo_html = (
            f"<img src='{logo}' alt='{html_lib.escape(team_name)}' onerror=\"this.style.display='none'\">"
            if logo else ''
        )
        podium_class = f"podium p{int(row['Sıra'])}" if podium else 'team-card'
        return (
            f"<div class='{podium_class}' style='--team:{colour}'>"
            f"<div class='place'>#{int(row['Sıra'])}</div>{logo_html}"
            f"<div class='team-name'>{html_lib.escape(team_name)}</div>"
            f"<div class='team-points'>{html_lib.escape(str(row['Puan']))} <small>PUAN</small></div>"
            f"</div>"
        )

    by_rank = {int(row['Sıra']): row for _, row in standings.iterrows()}
    podium = ''.join(card(by_rank[rank], podium=True) for rank in [2, 1, 3] if rank in by_rank)
    rest = ''.join(card(row) for _, row in standings.iterrows() if int(row['Sıra']) > 3)
    return f"""
    <style>
        body{{margin:0;background:transparent;color:#f2f5f8;font-family:'Saira',system-ui,sans-serif}}
        .podium-wrap{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;align-items:end;padding:2px 0 18px}}
        .podium,.team-card{{position:relative;background:linear-gradient(160deg,#161d28,#11161f);border:1px solid #26313f;
          border-top:3px solid var(--team);border-radius:5px;padding:14px 16px;box-sizing:border-box;overflow:hidden}}
        .podium{{min-height:172px;text-align:center}} .p1{{min-height:206px;order:0;box-shadow:0 12px 30px rgba(0,0,0,.35)}} .p2{{order:-1}} .p3{{order:1}}
        .podium img{{height:60px;max-width:128px;object-fit:contain;margin:10px auto 8px;display:block}} .team-card img{{height:38px;max-width:92px;object-fit:contain;margin-bottom:6px;display:block}}
        .place{{position:absolute;right:12px;top:10px;color:var(--team);font:800 13px 'JetBrains Mono',monospace}}
        .team-name{{color:var(--team);font:800 16px 'Saira Condensed',sans-serif;text-transform:uppercase;letter-spacing:.02em}}
        .team-points{{margin-top:6px;font:800 22px 'JetBrains Mono',monospace}} .team-points small{{font:700 9px 'Saira Condensed',sans-serif;color:#63748a;letter-spacing:.14em;margin-left:4px}}
        .team-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}} .team-card{{min-height:108px}}
        @media(max-width:440px){{.podium-wrap,.team-grid{{grid-template-columns:1fr}} .p1,.p2,.p3{{order:initial;min-height:132px}}}}
    </style>
    <div class='podium-wrap'>{podium}</div><div class='team-grid'>{rest}</div>
    """


def constructor_hud_component_height(standings):
    """Podyum ve kalan takım kartlarını boş dev iframe bırakmadan sığdırır."""
    count = len(standings) if standings is not None else 0
    remaining_rows = (max(0, count - 3) + 2) // 3
    return min(1060, max(360, 255 + remaining_rows * 145))


def weekend_overview_hud(event, sessions):
    """Alpha 0.9 yarış hafta sonu için tek bakışta program HUD'u."""
    now = datetime.datetime.now(datetime.timezone.utc)
    cards = []
    upcoming = None
    for item in sessions:
        session_time = item['time']
        complete = item['estimated_end'] <= now
        if not complete and upcoming is None:
            upcoming = item
        local_time = session_time.tz_convert('Europe/Istanbul').strftime('%a %d %b · %H:%M')
        colour = '#6ee7b7' if complete else '#f7c948'
        status = 'TAMAMLANDI' if complete else 'SIRADA'
        cards.append(
            f"<div class='weekend-session' style='--accent:{colour}'><small>{html_lib.escape(item['title'].upper())}</small>"
            f"<b>{html_lib.escape(local_time)}</b><span>{status}</span></div>"
        )
    next_text = 'Hafta sonu tamamlandı' if upcoming is None else f"Sıradaki: {upcoming['title']} · İstanbul saatiyle {upcoming['time'].tz_convert('Europe/Istanbul').strftime('%d %b %H:%M')}"
    return f"""
    <style>
      body{{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}}
      .weekend-hud{{border:1px solid #2a405a;border-radius:14px;padding:15px;background:linear-gradient(125deg,#111c2c,#0c1420);overflow:hidden}}
      .weekend-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}}
      .eyebrow{{color:#8ba2bc;font-weight:900;font-size:10px;letter-spacing:.12em}}.race-name{{font-size:22px;font-weight:950;margin-top:5px}}.next{{border:1px solid #36506e;background:#122137;border-radius:8px;padding:8px 10px;color:#b8c9db;font-size:11px;font-weight:800}}
      .weekend-sessions{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:14px}}.weekend-session{{min-height:70px;padding:10px;border:1px solid #294057;border-top:3px solid var(--accent);border-radius:9px;background:#0d1724}}.weekend-session small,.weekend-session span{{display:block;color:#8da4bc;font-weight:900;font-size:10px;letter-spacing:.07em}}.weekend-session b{{display:block;color:#f3f8ff;margin:7px 0;font-size:12px}}.weekend-session span{{color:var(--accent)}}
      @media(max-width:440px){{.race-name{{font-size:18px}}.weekend-sessions{{grid-template-columns:repeat(2,1fr)}}.weekend-session:last-child{{grid-column:span 2}}}}
    </style>
    <div class='weekend-hud'><div class='weekend-head'><div><div class='eyebrow'>RACE WEEKEND // İSTANBUL SAATİ</div><div class='race-name'>{html_lib.escape(str(event.get('EventName', 'Formula 1')))}</div></div><div class='next'>{html_lib.escape(next_text)}</div></div><div class='weekend-sessions'>{''.join(cards)}</div></div>
    """


def championship_snapshot_hud(driver_standings, constructor_standings, rounds, year=None):
    """Puan Merkezi açıldığında önce görünen hızlı sezon özeti."""
    if driver_standings.empty or constructor_standings.empty:
        return ''
    _tc = (lambda name: season_team_colour(name, year)) if year else team_colour
    driver = driver_standings.iloc[0]
    team = constructor_standings.iloc[0]
    driver_team = str(driver.get('Takım', ''))
    dc = _tc(driver_team)
    tc = _tc(str(team.get('Takım', '')))
    d_name = html_lib.escape(str(driver.get('Pilot', '—')))
    d_pts = html_lib.escape(str(driver.get('Puan', '—')))
    t_name = html_lib.escape(str(team.get('Takım', '—')))
    t_pts = html_lib.escape(str(team.get('Puan', '—')))
    # takim ligindeki fark
    gap = ''
    try:
        if len(constructor_standings) > 1:
            gap = f"+{int(float(team.get('Puan', 0)) - float(constructor_standings.iloc[1].get('Puan', 0)))} fark"
    except (TypeError, ValueError):
        gap = ''
    remaining = max(0, 24 - len(rounds))
    _is_past = bool(year) and int(year) < datetime.datetime.now(datetime.timezone.utc).year
    third_card = (
        f"<div class='ss-c' style='--a:#f5c33b'><s>Sezon</s><b>{html_lib.escape(str(year))}</b><i>{len(rounds)} yarış tamamlandı</i></div>"
        if _is_past else
        f"<div class='ss-c' style='--a:#f5c33b'><s>Kalan Yarış</s><b>{remaining}</b><i>{len(rounds)} tamamlandı</i></div>"
    )
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .ss{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}}
      .ss-c{{border:1px solid #26313f;border-left:3px solid var(--a);border-radius:5px;
        padding:14px 16px;background:linear-gradient(160deg,#161d28,#11161f);
        min-height:118px;display:flex;flex-direction:column}}
      .ss-c s{{font:700 9.5px 'Saira Condensed',sans-serif;letter-spacing:.16em;
        text-transform:uppercase;color:#63748a;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .ss-c b{{font-family:'Antonio','Saira Condensed',sans-serif;font-weight:700;font-size:26px;
        text-transform:uppercase;letter-spacing:.01em;color:var(--a);margin-top:9px;line-height:.95;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .ss-c i{{font:12px 'JetBrains Mono',monospace;color:#9fb0c0;margin-top:auto;padding-top:8px;font-style:normal;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      @media(max-width:430px){{.ss{{grid-template-columns:1fr}}.ss-c{{min-height:0}}}}
    </style>
    <div class='ss'>
      <div class='ss-c' style='--a:{dc}'><s>{'Dünya Şampiyonu' if _is_past else 'Pilot Lideri'}</s><b>{d_name}</b><i>{d_pts} P · {html_lib.escape(driver_team)}</i></div>
      <div class='ss-c' style='--a:{tc}'><s>{'Yapımcılar Şampiyonu' if _is_past else 'Takım Lideri'}</s><b>{t_name}</b><i>{t_pts} P{(' · ' + gap) if gap else ''}</i></div>
      {third_card}
    </div>
    """


# =========================================================
# FAZ 2 · #10 — ŞAMPİYONLUK SENARYO / PERMÜTASYON HESAPLAYICI
# Yalnızca doğrulanmış puan tablosu + resmî takvimden hesaplanır.
# =========================================================

_F1_RACE_POINTS_V40 = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
_F1_SPRINT_POINTS_V40 = [8, 7, 6, 5, 4, 3, 2, 1]


def _f1_points_v40(finish_pos, sprint=False):
    """1-tabanlı bitiş sırası -> puan. Puan dışıysa 0."""
    table = _F1_SPRINT_POINTS_V40 if sprint else _F1_RACE_POINTS_V40
    try:
        pos = int(finish_pos)
    except (TypeError, ValueError):
        return 0
    return table[pos - 1] if 1 <= pos <= len(table) else 0


@st.cache_data(ttl=60 * 60 * 3, show_spinner=False)
def _championship_remaining_v40(year, completed_count):
    """Resmî takvimden kalan yarış + sprint sayısı ve sıradaki yarış adı."""
    try:
        calendar = get_calendar_details(int(year))
    except Exception:
        return {'races': 0, 'sprints': 0, 'next': None, 'total': int(completed_count)}
    now = datetime.datetime.now(datetime.timezone.utc)
    races = sprints = 0
    nxt = None
    for event in calendar:
        race_date = event.get('Session5DateUtc')
        if pd.isnull(race_date):
            continue
        race_time = pd.to_datetime(race_date)
        race_time = race_time.tz_localize('UTC') if race_time.tzinfo is None else race_time.tz_convert('UTC')
        if race_time + datetime.timedelta(hours=3) <= now:
            continue
        races += 1
        if 'sprint' in str(event.get('EventFormat', '')).lower():
            sprints += 1
        if nxt is None:
            nxt = str(event.get('EventName', '')).strip() or None
    return {'races': races, 'sprints': sprints, 'next': nxt,
            'total': int(completed_count) + races}


def championship_scenarios_v40(driver_standings, remaining):
    """Saf hesap: her üst pilot için güncel puan, teorik maksimum, matematiksel
    durum ve lidere fark. `remaining` = _championship_remaining_v40 çıktısı."""
    if driver_standings is None or driver_standings.empty:
        return {'ok': False}
    rows = []
    for _, row in driver_standings.iterrows():
        try:
            pts = float(row.get('Puan', 0) or 0)
        except (TypeError, ValueError):
            pts = 0.0
        rows.append({'code': str(row.get('Pilot', '')).strip(),
                     'team': str(row.get('Takım', '')).strip(), 'points': pts})
    if not rows:
        return {'ok': False}
    rows.sort(key=lambda r: -r['points'])
    leader = rows[0]
    swing = remaining['races'] * 25 + remaining['sprints'] * 8
    contenders = []
    for i, r in enumerate(rows[:6]):
        ceiling = r['points'] + swing
        gap = leader['points'] - r['points']
        alive = i == 0 or ceiling >= leader['points']
        contenders.append({**r, 'rank': i + 1, 'ceiling': ceiling,
                           'gap': gap, 'alive': alive})
    runner_ceiling = rows[1]['points'] + swing if len(rows) > 1 else 0
    clinched = len(rows) > 1 and leader['points'] > runner_ceiling
    return {
        'ok': True, 'leader': leader['code'], 'leader_points': leader['points'],
        'contenders': contenders, 'swing': swing, 'clinched': clinched,
        'races': remaining['races'], 'sprints': remaining['sprints'],
        'next': remaining['next'],
        'still_alive': sum(1 for c in contenders if c['alive']),
    }


def championship_scenarios_html(scn, colour_of):
    """Matematiksel durum HUD'u — güncel puan, tavan, lidere fark, elendi/yarışta."""
    if not scn.get('ok'):
        return "<div style='padding:18px;color:#8a9bb0;font-family:Saira,sans-serif'>Senaryo için yeterli puan verisi yok.</div>"
    head = (f"{scn['races']} yarış" + (f" · {scn['sprints']} sprint" if scn['sprints'] else "")
            + f" kaldı · sahadaki en yüksek kazanç <b>{int(scn['swing'])} puan</b>")
    if scn['races'] == 0:
        head = "Sezon tamamlandı — unvan kesinleşti."
    banner = ""
    if scn['clinched'] and scn['races'] > 0:
        banner = (f"<div class='scn-ban win'>{html_lib.escape(scn['leader'])} şampiyonluğu "
                  f"matematiksel olarak GARANTİLEDİ — takipçi artık yetişemez.</div>")
    elif scn['races'] > 0:
        banner = (f"<div class='scn-ban'>{scn['still_alive']} pilot hâlâ matematiksel olarak "
                  f"şampiyon olabilir.</div>")
    rows = ""
    _over = scn['races'] == 0
    for c in scn['contenders']:
        col = colour_of(c['team']) or '#8a9bb0'
        if _over:
            state = ("<span class='pill live'>ŞAMPİYON</span>" if c['rank'] == 1
                     else "<span class='pill done'>—</span>")
        else:
            state = ("<span class='pill live'>YARIŞTA</span>" if c['alive']
                     else "<span class='pill out'>ELENDİ</span>")
        gaptext = "—" if c['rank'] == 1 else f"-{int(c['gap'])}"
        rows += (
            f"<div class='scn-row' style='--c:{col}'>"
            f"<span class='pos'>{c['rank']}</span>"
            f"<span class='who'><b>{html_lib.escape(c['code'])}</b><small>{html_lib.escape(c['team'])}</small></span>"
            f"<span class='num'><s>PUAN</s>{int(c['points'])}</span>"
            f"<span class='num'><s>TAVAN</s>{int(c['ceiling'])}</span>"
            f"<span class='num'><s>LİDERE</s>{gaptext}</span>"
            f"<span class='st'>{state}</span>"
            "</div>"
        )
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .scn{{border:1px solid #26313f;border-radius:12px;overflow:hidden;background:#11161f}}
      .scn-hd{{padding:13px 16px;border-bottom:1px solid #26313f;font:600 12px 'Saira',sans-serif;color:#c4d2e0}}
      .scn-hd b{{color:#f2f5f8;font-family:'JetBrains Mono',monospace}}
      .scn-ban{{margin:10px 12px 0;padding:9px 12px;border-radius:7px;background:#12212f;
        border:1px solid #24445c;font:600 11.5px 'Saira',sans-serif;color:#9fd0ea}}
      .scn-ban.win{{background:#12241a;border-color:#2c5a3b;color:#7fe0a6}}
      .scn-list{{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px}}
      .scn-row{{display:grid;grid-template-columns:26px 1.6fr repeat(3,64px) 78px;gap:10px;align-items:center;
        padding:9px 11px;background:#131a24;border:1px solid #222c39;border-left:3px solid var(--c);border-radius:8px}}
      .pos{{font:700 13px 'JetBrains Mono',monospace;color:#7c8ea0;text-align:center}}
      .who b{{font:700 13px 'Saira Condensed',sans-serif;text-transform:uppercase;letter-spacing:.02em;display:block}}
      .who small{{font:500 10.5px 'Saira',sans-serif;color:#8a9bb0;display:block;margin-top:1px;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .num{{text-align:right;font:700 13px 'JetBrains Mono',monospace}}
      .num s{{display:block;font:700 8px 'Saira Condensed',sans-serif;letter-spacing:.09em;color:#63748a;text-decoration:none;margin-bottom:2px}}
      .st{{text-align:right}}
      .pill{{font:800 9px 'Saira Condensed',sans-serif;letter-spacing:.08em;padding:4px 7px;border-radius:5px}}
      .pill.live{{background:#12241a;color:#7fe0a6}} .pill.out{{background:#241417;color:#ff8b78}}
      .pill.done{{background:#151b25;color:#63748a}}
      @media(max-width:620px){{
        .scn-row{{grid-template-columns:22px 1fr 56px 78px;row-gap:4px}}
        .num:nth-of-type(3){{display:none}} .st{{grid-column:3/-1;text-align:left}}
      }}
    </style>
    <div class="scn">
      <div class="scn-hd">{head}</div>
      {banner}
      <div class="scn-list">{rows}</div>
    </div>
    """


def championship_projection_html(leader, challenger, leader_pts, challenger_pts,
                                 leader_finish, challenger_finish, races, sprints, colour_l, colour_c):
    """Etkileşimli senaryo sonucu: iki pilot kalan yarışlarda verilen sırada
    biterse nihai puanlar ve unvan sahibi."""
    lp = leader_pts + races * _f1_points_v40(leader_finish) + sprints * _f1_points_v40(leader_finish, sprint=True)
    cp = challenger_pts + races * _f1_points_v40(challenger_finish) + sprints * _f1_points_v40(challenger_finish, sprint=True)
    champ, champ_col, other = ((leader, colour_l, challenger) if lp >= cp else (challenger, colour_c, leader))
    margin = abs(lp - cp)
    verdict = (f"{html_lib.escape(champ)} unvanı {int(margin)} puan farkla alır"
               if margin else f"{html_lib.escape(champ)} eşit puanda, galibiyet üstünlüğüyle şampiyon")
    bars = ""
    top = max(lp, cp, 1)
    for who, val, col in [(leader, lp, colour_l), (challenger, cp, colour_c)]:
        bars += (
            f"<div class='pj-row'><span class='pj-n'>{html_lib.escape(who)}</span>"
            f"<span class='pj-bar'><i style='width:{round(val / top * 100)}%;background:{col}'></i></span>"
            f"<span class='pj-v'>{int(val)}</span></div>"
        )
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .pj{{border:1px solid #26313f;border-radius:12px;background:#11161f;padding:15px 16px}}
      .pj-v-hd{{font:700 10px 'Saira Condensed',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#63748a}}
      .pj-verdict{{font:700 16px 'Saira Condensed',sans-serif;text-transform:uppercase;letter-spacing:.02em;
        color:{champ_col};margin:6px 0 14px}}
      .pj-row{{display:grid;grid-template-columns:120px 1fr 46px;gap:10px;align-items:center;padding:5px 0}}
      .pj-n{{font:600 12px 'Saira',sans-serif;color:#c4d2e0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .pj-bar{{height:14px;background:#0a111b;border-radius:4px;overflow:hidden}}
      .pj-bar i{{display:block;height:100%}}
      .pj-v{{font:700 14px 'JetBrains Mono',monospace;text-align:right}}
      .pj-note{{margin-top:12px;font:500 11px 'Saira',sans-serif;color:#8a9bb0}}
    </style>
    <div class="pj">
      <div class="pj-v-hd">Senaryo sonucu</div>
      <div class="pj-verdict">{verdict}</div>
      {bars}
      <div class="pj-note">Varsayım: kalan {races} yarış (+{sprints} sprint) boyunca her ikisi de
        sabit sırada bitiyor. Gerçek sonuçlar farklı olacaktır — bu yalnızca puan matematiğini gösterir.</div>
    </div>
    """


def season_h2h_v41(result_matrix, points_matrix, rounds, standings, code_a, code_b):
    """İki pilotun bu sezonki kafa-kafaya dökümü — yalnızca hazır puan/sonuç
    matrisinden. Ağ yok. Dönüş: tur tur kim önde + galibiyet sayısı + form."""
    if result_matrix is None or result_matrix.empty or not rounds:
        return {'ok': False}

    def _row(df, code):
        match = df[df['Pilot'] == code]
        return match.iloc[0].to_dict() if not match.empty else None

    ra, rb = _row(result_matrix, code_a), _row(result_matrix, code_b)
    pa, pb = _row(points_matrix, code_a), _row(points_matrix, code_b)
    if not (ra and rb and pa and pb):
        return {'ok': False}
    sa, sb = _row(standings, code_a) or {}, _row(standings, code_b) or {}

    def _split(value):
        text = str(value).strip() if value is not None else ''
        if text.lower() in ('', '—', '-', 'nan'):
            return (None, None)
        if '/' in text:
            left, right = (part.strip() for part in text.split('/', 1))
            clean = lambda x: None if x in ('—', '-', '') else x
            return (clean(left), clean(right))
        return (text, None)

    def _pos(text):
        try:
            return int(text)
        except (TypeError, ValueError):
            return None

    def _num(text):
        try:
            return float(text)
        except (TypeError, ValueError):
            return 0.0

    out_rounds, mom_a, mom_b = [], [], []
    race_w_a = race_w_b = spr_w_a = spr_w_b = 0
    for rnd in rounds:
        key = rnd['key']
        a_race, a_spr = _split(ra.get(key))
        b_race, b_spr = _split(rb.get(key))
        ap_race, ap_spr = _split(pa.get(key))
        bp_race, bp_spr = _split(pb.get(key))
        a_pos, b_pos = _pos(a_race), _pos(b_race)

        winner = None
        if a_pos is not None and b_pos is not None:
            winner = 'a' if a_pos < b_pos else 'b' if b_pos < a_pos else None
        elif a_pos is not None and b_race is not None:
            winner = 'a'
        elif b_pos is not None and a_race is not None:
            winner = 'b'
        elif a_pos is not None:
            winner = 'a'
        elif b_pos is not None:
            winner = 'b'
        if winner == 'a':
            race_w_a += 1
        elif winner == 'b':
            race_w_b += 1

        if rnd.get('has_sprint'):
            asp, bsp = _pos(a_spr), _pos(b_spr)
            if asp is not None and (bsp is None or asp < bsp):
                spr_w_a += 1
            elif bsp is not None and (asp is None or bsp < asp):
                spr_w_b += 1

        tot_a = _num(ap_race) + _num(ap_spr)
        tot_b = _num(bp_race) + _num(bp_spr)
        mom_a.append(tot_a)
        mom_b.append(tot_b)
        out_rounds.append({
            'badge': rnd['badge'], 'sprint': bool(rnd.get('has_sprint')),
            'a_pos': a_race or 'YOK', 'b_pos': b_race or 'YOK',
            'a_pts': tot_a, 'b_pts': tot_b, 'winner': winner,
        })

    span = min(5, len(out_rounds)) or 1
    return {
        'ok': True, 'a': code_a, 'b': code_b,
        'team_a': str(sa.get('Takım', '')), 'team_b': str(sb.get('Takım', '')),
        'pts_a': _num(sa.get('Puan')), 'pts_b': _num(sb.get('Puan')),
        'rounds': out_rounds, 'has_sprints': any(r['sprint'] for r in out_rounds),
        'race_w_a': race_w_a, 'race_w_b': race_w_b,
        'spr_w_a': spr_w_a, 'spr_w_b': spr_w_b,
        'mom_a': round(sum(mom_a[-span:]), 1), 'mom_b': round(sum(mom_b[-span:]), 1),
        'mom_span': span,
    }


def season_h2h_html(h, colour_a, colour_b):
    """Kafa-kafaya HUD'u — güncel puan farkı, yarışta önde sayısı, form, tur şeridi."""
    if not h.get('ok'):
        return ("<div style='padding:20px;color:#8a9bb0;font-family:Saira,sans-serif'>"
                "Bu iki pilot için bu sezona ait karşılaştırma verisi yok.</div>")
    ca, cb = colour_a or '#e10600', colour_b or '#38e1d0'
    gap = h['pts_a'] - h['pts_b']
    ahead, ahead_col = (h['a'], ca) if gap >= 0 else (h['b'], cb)
    total_races = max(1, h['race_w_a'] + h['race_w_b'])
    a_share = round(h['race_w_a'] / total_races * 100)
    mom_leader = h['a'] if h['mom_a'] > h['mom_b'] else h['b'] if h['mom_b'] > h['mom_a'] else None
    strip = "".join(
        f"<span class='cell {('a' if r['winner'] == 'a' else 'b' if r['winner'] == 'b' else 'd')}'"
        f" title=\"{html_lib.escape(r['badge'])} · {html_lib.escape(h['a'])} P{html_lib.escape(str(r['a_pos']))}"
        f" ({r['a_pts']:g}p) · {html_lib.escape(h['b'])} P{html_lib.escape(str(r['b_pos']))} ({r['b_pts']:g}p)\">"
        f"{html_lib.escape(r['badge'].split(' ')[-1][:4])}</span>"
        for r in h['rounds']
    )
    sprint_line = ""
    if h['has_sprints']:
        sprint_line = (f"<div class='h2h-sub'>Sprint · <b>{h['a']}</b> {h['spr_w_a']} — "
                       f"{h['spr_w_b']} <b>{h['b']}</b></div>")
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .h2h{{border:1px solid #26313f;border-radius:12px;overflow:hidden;background:#11161f}}
      .h2h-top{{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;
        padding:16px;border-bottom:1px solid #26313f}}
      .h2h-d{{display:flex;flex-direction:column;gap:3px}}
      .h2h-d.r{{align-items:flex-end;text-align:right}}
      .h2h-d b{{font:800 20px 'Saira Condensed',sans-serif;text-transform:uppercase;letter-spacing:.02em}}
      .h2h-d s{{font:600 10.5px 'Saira',sans-serif;color:#8a9bb0;text-decoration:none;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px}}
      .h2h-d i{{font:700 15px 'JetBrains Mono',monospace;font-style:normal;margin-top:2px}}
      .h2h-gap{{text-align:center}}
      .h2h-gap s{{display:block;font:700 8px 'Saira Condensed',sans-serif;letter-spacing:.12em;color:#63748a;text-decoration:none}}
      .h2h-gap b{{font:800 22px 'JetBrains Mono',monospace;color:{ahead_col}}}
      .h2h-gap em{{display:block;font:600 10px 'Saira',sans-serif;font-style:normal;color:#9fb0c0;margin-top:2px}}
      .h2h-body{{padding:14px 16px}}
      .h2h-hd{{font:700 9px 'Saira Condensed',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#63748a;margin:2px 0 7px}}
      .h2h-bar{{display:flex;height:26px;border-radius:6px;overflow:hidden;border:1px solid #26313f;font:800 11px 'JetBrains Mono',monospace}}
      .h2h-bar i{{display:flex;align-items:center;justify-content:center;color:#05080d;min-width:34px}}
      .h2h-bar .ba{{background:{ca};width:{a_share}%}} .h2h-bar .bb{{background:{cb};flex:1}}
      .h2h-sub{{margin-top:9px;font:600 11.5px 'Saira',sans-serif;color:#9fb0c0}}
      .h2h-sub b{{color:#e8eef4}}
      .h2h-mom{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}}
      .h2h-mom>div{{border:1px solid #222c39;border-radius:8px;padding:9px 11px;background:#131a24}}
      .h2h-mom s{{display:block;font:700 8.5px 'Saira Condensed',sans-serif;letter-spacing:.1em;color:#63748a;text-decoration:none}}
      .h2h-mom b{{font:700 17px 'JetBrains Mono',monospace;margin-top:3px;display:block}}
      .h2h-mom em{{font:600 10px 'Saira',sans-serif;font-style:normal;color:#7fe0a6}}
      .h2h-strip{{display:flex;flex-wrap:wrap;gap:3px;margin-top:14px}}
      .cell{{flex:1 0 46px;text-align:center;font:700 9px 'JetBrains Mono',monospace;padding:6px 2px;border-radius:4px;
        border:1px solid #222c39;cursor:help;color:#c9d6e2}}
      .cell.a{{background:color-mix(in srgb,{ca} 26%,#11161f);border-color:{ca}}}
      .cell.b{{background:color-mix(in srgb,{cb} 26%,#11161f);border-color:{cb}}}
      .cell.d{{background:#161d28}}
      @media(max-width:560px){{.h2h-top{{grid-template-columns:1fr auto 1fr;gap:6px}}.h2h-d b{{font-size:16px}}.h2h-mom{{grid-template-columns:1fr}}}}
    </style>
    <div class="h2h">
      <div class="h2h-top">
        <div class="h2h-d" style="color:{ca}"><b>{html_lib.escape(h['a'])}</b><s>{html_lib.escape(h['team_a'])}</s><i>{h['pts_a']:g} P</i></div>
        <div class="h2h-gap"><s>Fark</s><b>{abs(gap):g}</b><em>{html_lib.escape(ahead)} önde</em></div>
        <div class="h2h-d r" style="color:{cb}"><b>{html_lib.escape(h['b'])}</b><s>{html_lib.escape(h['team_b'])}</s><i>{h['pts_b']:g} P</i></div>
      </div>
      <div class="h2h-body">
        <div class="h2h-hd">Yarışta önde biten — {h['race_w_a']} / {h['race_w_b']}</div>
        <div class="h2h-bar"><i class="ba">{h['a']} {h['race_w_a']}</i><i class="bb">{h['race_w_b']} {h['b']}</i></div>
        {sprint_line}
        <div class="h2h-mom">
          <div><s>Son {h['mom_span']} yarış · {html_lib.escape(h['a'])}</s><b style="color:{ca}">{h['mom_a']:g} P</b>{'<em>daha formda</em>' if mom_leader == h['a'] else ''}</div>
          <div><s>Son {h['mom_span']} yarış · {html_lib.escape(h['b'])}</s><b style="color:{cb}">{h['mom_b']:g} P</b>{'<em>daha formda</em>' if mom_leader == h['b'] else ''}</div>
        </div>
        <div class="h2h-hd" style="margin-top:14px">Tur tur — kutu rengi o yarışta önde biteni gösterir</div>
        <div class="h2h-strip">{strip}</div>
      </div>
    </div>
    """


def season_h2h_component_height(h):
    rounds = len((h or {}).get('rounds', []) or [])
    strip_rows = (max(0, rounds - 1) // 12) + 1
    return min(760, 430 + strip_rows * 34)


def session_leaderboard_html(table, title):
    """FP, sıralama ve yarış sonuçlarını takım renkli HUD leaderboard'a çevirir."""
    if table.empty:
        return ''
    time_column = next((column for column in ['En Hızlı Tur', 'Zaman', 'Q3', 'Q2', 'Q1'] if column in table.columns), '')
    tyre_colours = {'SOFT': '#ff3b3b', 'MEDIUM': '#ffd234', 'HARD': '#f0f4f8', 'INTERMEDIATE': '#3fd66a', 'WET': '#3aa9ff'}

    def row_html(row, podium=False):
        raw_rank = row.get('Sıra', '—')
        try:
            rank = str(int(float(raw_rank)))
        except (TypeError, ValueError):
            rank = str(raw_rank)
        pilot = str(row.get('Pilot', '—'))
        team = str(row.get('Takım', '—'))
        colour = team_colour(team)
        time = str(row.get(time_column, row.get('Durum', '—'))) if time_column else str(row.get('Durum', '—'))
        compound = str(row.get('Lastik', '')).upper()
        tyre = f"<span class='tyre' style='--tyre:{tyre_colours.get(compound, '#64748b')}'>{compound[:1]}</span>" if compound and compound != '—' else ''
        class_name = f"top top-{rank}" if podium else 'leader-row'
        return f"<div class='{class_name}' style='--team:{colour}'><div class='rank'>{rank}</div><div class='pilot'>{html_lib.escape(pilot)}<small>{html_lib.escape(team)}</small></div><div class='lap'>{html_lib.escape(time)}</div>{tyre}</div>"

    rows = [row for _, row in table.iterrows()]
    podium = ''.join(row_html(row, podium=True) for row in rows[:3])
    rest = ''.join(row_html(row) for row in rows[3:])
    return f"""
    <style>
        body{{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}}
        .wrap{{border:1px solid #2c3c53;border-radius:13px;background:#11161f;overflow:hidden}}
        .head{{padding:13px 16px;background:#151f2f;border-bottom:1px solid #2c3c53;font-weight:900;letter-spacing:.04em}}
        .sub{{font-size:11px;color:#8ea4bc;margin-top:4px;font-weight:700}}
        .tops{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:12px}}
        .top{{background:#111b2a;border:1px solid #2c3c53;border-top:4px solid var(--team);border-radius:10px;padding:12px;position:relative;min-height:64px}}
        .top .rank{{color:var(--team);font-size:20px;font-weight:900;position:absolute;right:12px;top:8px}}
        .pilot{{font-weight:900;color:#f2f5f8}} .pilot small{{display:block;color:var(--team);font-size:11px;margin-top:4px;font-weight:800}}
        .lap{{margin-top:7px;color:#d7e4f4;font-family:ui-monospace,Consolas,monospace;font-weight:800}}
        .tyre{{display:inline-flex;align-items:center;justify-content:center;width:19px;height:19px;border:2px solid var(--tyre);border-radius:50%;font-size:10px;color:var(--tyre);font-weight:900;margin-left:7px}}
        .leader-list{{border-top:1px solid #243145}}
        .leader-row{{display:grid;grid-template-columns:45px 1fr 135px 30px;align-items:center;min-height:53px;padding:0 14px;border-top:1px solid #243145;border-left:4px solid var(--team)}}
        .leader-row .rank{{color:#9fb4cb;font-weight:900}} .leader-row .lap{{margin:0;text-align:right}}
        @media(max-width:440px){{.tops{{grid-template-columns:1fr}} .leader-row{{grid-template-columns:36px 1fr 95px 25px;padding:0 8px}}}}
    </style>
    <div class='wrap'><div class='head'>{html_lib.escape(title)}<div class='sub'>TAKIM RENKLERİ • TUR ZAMANI • LASTİK HAMURU</div></div><div class='tops'>{podium}</div><div class='leader-list'>{rest}</div></div>
    """


def leaderboard_component_height(table):
    """Her sonucu sayfanın normal kaydırmasına bırakır; 13. pilotta kesilmez."""
    row_count = len(table) if table is not None else 0
    return min(1540, max(360, 210 + max(0, row_count - 3) * 54))


def _duel_samples_v18(telemetry, sample_count=360):
    """2D düello için her mesafede gerçek geçen zamanı da taşıyan örnekler üretir."""
    columns = ['X', 'Y', 'Distance', 'Speed', 'Time']
    source = telemetry[[column for column in columns if column in telemetry.columns]].dropna(subset=['X', 'Y']).copy()
    if source.empty:
        return {'distance': [], 'realtime': [], 'lap_seconds': 0}
    source['Distance'] = pd.to_numeric(source.get('Distance', pd.Series(np.arange(len(source)))), errors='coerce')
    source['Speed'] = pd.to_numeric(source.get('Speed', pd.Series(np.zeros(len(source)))), errors='coerce').fillna(0)
    source = source.dropna(subset=['Distance']).sort_values('Distance').drop_duplicates('Distance')
    if len(source) < 2:
        return {'distance': [], 'realtime': [], 'lap_seconds': 0}
    try:
        elapsed = (pd.to_timedelta(source['Time']) - pd.to_timedelta(source['Time']).iloc[0]).dt.total_seconds()
        duration = float(elapsed.iloc[-1])
        if duration <= 0:
            raise ValueError('No duration')
        source = source.assign(_elapsed=elapsed)
    except Exception:
        duration = 0.0
        source = source.assign(_elapsed=np.zeros(len(source)))
    grid = np.linspace(float(source['Distance'].min()), float(source['Distance'].max()), sample_count)
    distance_points = [
        {
            'x': round(float(np.interp(value, source['Distance'], source['X'])), 2),
            'y': round(float(np.interp(value, source['Distance'], source['Y'])), 2),
            'speed': round(float(np.interp(value, source['Distance'], source['Speed'])), 1),
            'elapsed': round(float(np.interp(value, source['Distance'], source['_elapsed'])), 3)
        }
        for value in grid
    ]
    if duration <= 0:
        return {'distance': distance_points, 'realtime': distance_points, 'lap_seconds': 0}
    time_source = source.sort_values('_elapsed').drop_duplicates('_elapsed')
    time_grid = np.linspace(0, duration, sample_count)
    realtime_points = [
        {
            'x': round(float(np.interp(value, time_source['_elapsed'], time_source['X'])), 2),
            'y': round(float(np.interp(value, time_source['_elapsed'], time_source['Y'])), 2),
            'speed': round(float(np.interp(value, time_source['_elapsed'], time_source['Speed'])), 1),
            'elapsed': round(float(value), 3)
        }
        for value in time_grid
    ]
    return {'distance': distance_points, 'realtime': realtime_points, 'lap_seconds': round(duration, 3)}


def build_track_overlay(telemetry, lap=None, session=None):
    """Gerçek telemetri ve FastF1 devre bilgisinden güvenli 2D pist katmanları üretir.

    Pit giriş/çıkışı için konum verisi FastF1 tarafından her devre için yayınlanmaz.
    Bu nedenle pit işareti yalnızca başlangıç-bitiş çevresinde şematik olarak gösterilir;
    hız, fren ve Straight/Ovetake alanları ise doğrudan telemetriden hesaplanır.
    """
    empty = {
        'sectors': [], 'corners': [], 'brakes': [], 'straights': [],
        'speed_marker': None, 'pit': [{'fraction': 0.985, 'label': 'PIT IN (şematik)'}, {'fraction': 0.025, 'label': 'PIT OUT (şematik)'}]
    }
    try:
        wanted = [name for name in ['Distance', 'X', 'Y', 'Time', 'Speed', 'Throttle', 'Brake'] if name in telemetry.columns]
        source = telemetry[wanted].dropna(subset=['Distance', 'X', 'Y']).copy()
        source['Distance'] = pd.to_numeric(source['Distance'], errors='coerce')
        if 'Speed' in source.columns:
            source['Speed'] = pd.to_numeric(source['Speed'], errors='coerce').fillna(0)
        else:
            source['Speed'] = 0.0
        source = source.dropna(subset=['Distance']).sort_values('Distance').drop_duplicates('Distance')
        if len(source) < 12:
            return empty

        max_distance = float(source['Distance'].max())
        if max_distance <= 0:
            return empty

        def fraction_for_distance(value):
            return round(float(np.clip(value / max_distance, 0, 1)), 5)

        output = dict(empty)
        speed_index = source['Speed'].idxmax()
        output['speed_marker'] = {
            'fraction': fraction_for_distance(float(source.loc[speed_index, 'Distance'])),
            'speed': round(float(source.loc[speed_index, 'Speed']), 1),
            'label': 'Telemetri max hız'
        }

        # Sektör sınırları: referans turun gerçek sektör zamanlarını telemetri zamanına eşler.
        if lap is not None and 'Time' in source.columns:
            try:
                elapsed = (pd.to_timedelta(source['Time']) - pd.to_timedelta(source['Time']).iloc[0]).dt.total_seconds()
                sector_1 = _timedelta_seconds(lap.get('Sector1Time'))
                sector_2 = _timedelta_seconds(lap.get('Sector2Time'))
                for index, target in enumerate([sector_1, (sector_1 or 0) + (sector_2 or 0)], start=1):
                    if target and target > 0:
                        nearest = int((elapsed - target).abs().idxmin())
                        distance = float(source.loc[nearest, 'Distance'])
                        output['sectors'].append({'fraction': fraction_for_distance(distance), 'label': f'S{index + 1}', 'colour': '#f4d35e' if index == 1 else '#56cfe1'})
            except Exception:
                pass

        # Viraj koordinatları FastF1 pist bilgisinden gelir; yoksa çizilmez.
        if session is not None:
            try:
                corners = session.get_circuit_info().corners
                used = []
                for _, corner in corners.iterrows():
                    distance = pd.to_numeric(corner.get('Distance'), errors='coerce')
                    if pd.isna(distance) or distance < 0 or distance > max_distance:
                        continue
                    fraction = fraction_for_distance(float(distance))
                    if any(abs(fraction - previous) < 0.018 for previous in used):
                        continue
                    number = str(corner.get('Number', '')).replace('.0', '').strip()
                    letter = str(corner.get('Letter', '')).strip()
                    if number:
                        output['corners'].append({'fraction': fraction, 'label': f'T{number}{letter}'})
                        used.append(fraction)
                    if len(output['corners']) >= 18:
                        break
            except Exception:
                pass

        # Ağır frenleme: fren başlangıcında yüksek hız varsa işaretle.
        if 'Brake' in source.columns:
            brake = source['Brake'].fillna(False).astype(bool).to_numpy()
            candidates = []
            for index in range(1, len(source)):
                if brake[index] and not brake[index - 1] and float(source.iloc[index]['Speed']) >= 175:
                    candidates.append((float(source.iloc[index]['Speed']), float(source.iloc[index]['Distance'])))
            selected = []
            for speed, distance in sorted(candidates, reverse=True):
                fraction = fraction_for_distance(distance)
                if not any(abs(fraction - item['fraction']) < 0.06 for item in selected):
                    selected.append({'fraction': fraction, 'speed': round(speed), 'label': 'Ağır fren / geçiş olasılığı'})
                if len(selected) == 4:
                    break
            output['brakes'] = sorted(selected, key=lambda item: item['fraction'])

        # Straight / Overtake bölgeleri: telemetride uzun yüksek-hız ve tam gaz bölgeleri.
        throttle = (pd.to_numeric(source['Throttle'], errors='coerce').fillna(0).to_numpy()
                    if 'Throttle' in source.columns else np.full(len(source), 100.0))
        speed = source['Speed'].to_numpy()
        active = (throttle >= 96) & (speed >= max(210, float(np.nanpercentile(speed, 72))))
        groups, start = [], None
        for index, value in enumerate(active):
            if value and start is None:
                start = index
            if start is not None and (not value or index == len(active) - 1):
                end = index if value and index == len(active) - 1 else index - 1
                if end - start >= 5:
                    groups.append((start, end))
                start = None
        for start, end in sorted(groups, key=lambda item: item[1] - item[0], reverse=True)[:3]:
            output['straights'].append({
                'start': fraction_for_distance(float(source.iloc[start]['Distance'])),
                'end': fraction_for_distance(float(source.iloc[end]['Distance'])),
                'label': 'Straight / Overtake olasılığı'
            })
        return output
    except Exception:
        return empty


def two_driver_duel_html_stable(telemetry_1, telemetry_2, driver_1, driver_2, team_1, team_2, colour_1, colour_2, lap_time_1, lap_time_2, lap_seconds_1, lap_seconds_2, track_overlay=None, sector_times_1=None, sector_times_2=None):
    """İki turu ortak gerçek-zaman saatinde ve tek, önbellekli canvas dönüşümünde oynatır."""
    first, second = _duel_samples_v18(telemetry_1), _duel_samples_v18(telemetry_2)
    first['lap_seconds'], second['lap_seconds'] = float(lap_seconds_1), float(lap_seconds_2)
    packed = fp_ui.json_for_script({'drivers': [
        {'code': str(driver_1), 'team': str(team_1), 'colour': colour_1, 'lap': str(lap_time_1), 'samples': first, 'sectors': sector_times_1 or []},
        {'code': str(driver_2), 'team': str(team_2), 'colour': colour_2, 'lap': str(lap_time_2), 'samples': second, 'sectors': sector_times_2 or []},
    ], 'overlay': track_overlay or {}})
    return r'''<style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}
.hud{border:1px solid #26313f;border-radius:13px;padding:12px;background:#11161f}
.head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.title{font-size:13px;font-weight:950;letter-spacing:.09em}
.sub{font-size:10px;color:#9fb0c0;margin-top:5px}
.tag{border:1px solid #35506d;border-radius:7px;padding:6px 8px;font-size:11px;font-weight:900;color:var(--team)}
.legend{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.legend span{border:1px solid #35506d;border-radius:99px;padding:4px 8px;font:800 9.5px Inter,Arial,sans-serif;color:#c2d4e6;background:#101f34}
.legend span[title]{cursor:help}
.map{margin-top:9px;border:1px solid #26313f;border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 45%,#141b26,#07090d 78%)}
canvas{width:100%;height:392px;display:block}
.sectors{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}
.sector{border:1px solid #2b3a4d;border-top:3px solid var(--c);border-radius:8px;padding:8px;background:#161d28;font:800 11px ui-monospace,Consolas,monospace}
.sector small{display:block;color:#9fb0c0;font-family:Inter,Arial,sans-serif;margin-bottom:6px}
.win{color:#79e7a7}.lose{color:#ff8793}
.msec{margin-top:12px}
.mslab{display:flex;justify-content:space-between;gap:8px;font:700 8.5px ui-monospace,Consolas,monospace;color:#7f97ac;margin-bottom:5px}
.mslab s{font-style:normal;font-weight:900}
.msrow{position:relative;display:flex;align-items:stretch;gap:1px;height:48px}
.msrow::before{content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:#3a4a5e;z-index:1}
.msbar{flex:1;position:relative;cursor:help}
.msbar i{position:absolute;left:0;right:0;display:block;border-radius:1px}
.msbar.c0 i{bottom:50%;background:#4ea981}
.msbar.c1 i{top:50%;background:#d3576a}
.msbar.big i{box-shadow:0 0 0 1px rgba(255,255,255,.4)}
.dtrace{margin-top:12px}
.dtlab{display:flex;justify-content:space-between;gap:8px;font:700 8.5px ui-monospace,Consolas,monospace;color:#7f97ac;margin-bottom:5px}
.dtlab s{font-style:normal;font-weight:900}
.dtrace canvas{width:100%;height:104px;display:block;border:1px solid #26313f;border-radius:8px;background:#0d131c;cursor:crosshair}
.bottom{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}
.btn{border:1px solid #2b3a4d;border-radius:7px;background:#161d28;color:#f2f5f8;font-weight:900;padding:7px 9px;cursor:pointer}
.btn.active{border-color:#ff4757;background:#3a0f12}
.slider{flex:1;min-width:130px;accent-color:#ff4051}
.delta{font:900 12px ui-monospace,Consolas,monospace;margin-left:auto}
@media(max-width:650px){canvas{height:320px}.sectors{grid-template-columns:1fr}.delta{width:100%;margin-left:0}}
</style>
<div class="hud">
  <div class="head">
    <div><div class="title">2D TUR DUELLOSU</div><div class="sub">IKI TUR ORTAK ZAMAN EKSENINDE - AYNI PIST NOKTASINDAKI DELTA</div></div>
    <div id="tags"></div>
  </div>
  <div class="legend"><span>START / BITIS</span><span style="border-color:#45c8ff;color:#8fd8ff" title="Straight Mode - duzlukte dusuk surtunme bolgesi. Eski adiyla DRS.">SM · duzluk (≈DRS)</span><span style="border-color:#71e6a1;color:#9af0c4" title="Overtake Mode - ekstra elektrik gucu kullanilabilen, gecis sansi yuksek bolge. Yayin diliyle ERS hucum / push-to-pass.">OM · gecis (≈ERS)</span><span style="border-color:#f4d35e;color:#f4d35e">sektor</span></div>
  <div class="map"><canvas id="duel"></canvas></div>
  <div class="sectors" id="sectors"></div>
  <div class="msec" id="msec"></div>
  <div class="dtrace" id="dtrace"><div class="dtlab"><span>KUMULATIF &Delta; - tur boyunca zaman farkinin gelisimi (cizgi yukarida = <s id="dtc0">1.</s> pilot onde) - imlece tikla</span><span><s id="dtnow">&Delta; --</s></span></div><canvas id="dtcv"></canvas></div>
  <div class="bottom">
    <button class="btn" id="play">Oynat</button>
    <button class="btn active" data-rate="1">1x</button><button class="btn" data-rate="2">2x</button>
    <button class="btn" data-rate="4">4x</button><button class="btn" data-rate="8">8x</button>
    <input id="range" class="slider" type="range" min="0" max="1000" value="0">
    <span class="delta" id="delta">D --</span>
  </div>
</div>
<script>
"use strict";
(function(){
const D=__PAYLOAD__, cars=(D.drivers||[]).filter(function(c){return c&&c.samples;}), O=D.overlay||{};
const cv=document.getElementById('duel'), ctx=cv.getContext('2d');
const $=function(id){return document.getElementById(id);};
let playing=false, rate=1, p=0, last=performance.now(), lastHud=0, V=null, raf=0;

const line=(cars[0]&&cars[0].samples.distance)||[];
const maxLap=Math.max.apply(null,[1].concat(cars.map(function(c){return +c.samples.lap_seconds||1;})));

let B={mnx:0,mxx:1,mny:0,mxy:1};
if(line.length){
  let a=1e18,b=-1e18,d=1e18,e=-1e18;
  for(let i=0;i<line.length;i++){const q=line[i];
    if(q.x<a)a=q.x; if(q.x>b)b=q.x; if(q.y<d)d=q.y; if(q.y>e)e=q.y;}
  B={mnx:a,mxx:b,mny:d,mxy:e};
}
function fit(){
  const r=cv.getBoundingClientRect(), dpr=Math.min(2,devicePixelRatio||1);
  cv.width=Math.max(2,r.width*dpr); cv.height=Math.max(2,r.height*dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const pad=34, spanX=(B.mxx-B.mnx)||1, spanY=(B.mxy-B.mny)||1;
  const s=Math.min((r.width-pad*2)/spanX,(r.height-pad*2)/spanY);
  V={s:s, w:r.width, h:r.height,
     ox:(r.width-spanX*s)/2 - B.mnx*s,
     oy:(r.height-spanY*s)/2 + B.mxy*s};
  draw();
}
function T(q){ return [q.x*V.s+V.ox, -q.y*V.s+V.oy]; }

function lerp(arr,f){
  if(!arr||!arr.length) return null;
  const n=Math.max(0,Math.min(arr.length-1, f*(arr.length-1)));
  const i=n|0, r=n-i, x=arr[i], y=arr[Math.min(arr.length-1,i+1)];
  return {x:x.x+(y.x-x.x)*r, y:x.y+(y.y-x.y)*r,
          speed:x.speed+(y.speed-x.speed)*r, elapsed:x.elapsed+(y.elapsed-x.elapsed)*r};
}
function adv(c){ return Math.min(1, p*maxLap/(+c.samples.lap_seconds||maxLap)); }
function sec(v){ const x=String(v||'').split(':'); return x.length===2 ? (+x[0]*60 + +x[1]) : NaN; }

function drawCar(sx,sy,ang,code,col,done){
  ctx.save(); ctx.translate(sx,sy); ctx.rotate(ang);
  ctx.globalAlpha=done?0.5:1;
  ctx.fillStyle='#05080d';
  [[-13,-11,7,6],[-13,5,7,6],[8,-12,7,7],[8,5,7,7]].forEach(function(w){ctx.beginPath();ctx.roundRect(w[0],w[1],w[2],w[3],2);ctx.fill();});
  const g=ctx.createLinearGradient(-16,0,18,0);
  g.addColorStop(0,'#0b0f16'); g.addColorStop(.5,col); g.addColorStop(1,'#0b0f16');
  ctx.fillStyle=g;
  ctx.beginPath();
  ctx.moveTo(19,0);ctx.lineTo(9,-4.6);ctx.lineTo(-10,-5.4);ctx.lineTo(-13,-3.6);
  ctx.lineTo(-13,3.6);ctx.lineTo(-10,5.4);ctx.lineTo(9,4.6);ctx.closePath();ctx.fill();
  ctx.fillStyle='#0c141d'; ctx.beginPath(); ctx.ellipse(2,0,5,3.6,0,0,7); ctx.fill();
  ctx.strokeStyle='#243444'; ctx.lineWidth=1.3; ctx.beginPath(); ctx.arc(3,0,4.6,-1.1,1.1); ctx.stroke();
  ctx.fillStyle='#eef4fa'; ctx.fillRect(16,-13,3,26);
  ctx.fillStyle=col; ctx.fillRect(15,-13,1.5,26);
  ctx.fillStyle=done?'#7f8c9b':'#eef4fa'; ctx.fillRect(-18,-10,3,20);
  ctx.globalAlpha=1; ctx.restore();
  ctx.fillStyle=col; ctx.font='900 10px Inter,Arial,sans-serif'; ctx.textAlign='center';
  ctx.fillText(code, sx, sy-18);
}

function drawOverlay(){
  if(!line.length) return;
  const mark=function(f,label,c){ const q=lerp(line,f); if(!q)return; const s=T(q);
    ctx.fillStyle=c; ctx.beginPath(); ctx.arc(s[0],s[1],3.6,0,7); ctx.fill();
    ctx.fillStyle='#eef4fa'; ctx.font='800 9px Inter,Arial,sans-serif'; ctx.textAlign='left';
    ctx.fillText(label,s[0]+6,s[1]-5); };
  const zone=function(z,label,c){ if(!Number.isFinite(z.start)||!Number.isFinite(z.end))return;
    ctx.beginPath();
    for(let i=0;i<=26;i++){ const q=lerp(line, z.start+(z.end-z.start)*i/26); if(!q)break;
      const s=T(q); i?ctx.lineTo(s[0],s[1]):ctx.moveTo(s[0],s[1]); }
    ctx.strokeStyle=c; ctx.lineWidth=6; ctx.globalAlpha=.85; ctx.stroke(); ctx.globalAlpha=1;
    mark(z.start,label,c); };
  (O.straights||[]).forEach(function(z,i){ zone(z, i?'OM':'SM', i?'#71e6a1':'#45c8ff'); });
  mark(0,'START / BITIS','#ffffff');
  (O.sectors||[]).forEach(function(x){ mark(x.fraction,x.label,x.colour||'#f4d35e'); });
  (O.pit||[]).forEach(function(x){ mark(x.fraction,x.label,'#b79cff'); });
}

function draw(){
  if(!V) return;
  ctx.clearRect(0,0,V.w,V.h);
  if(!line.length){ ctx.fillStyle='#9fb0c0'; ctx.font='700 12px Inter,Arial,sans-serif'; ctx.textAlign='center';
    ctx.fillText('Bu tur icin konum telemetrisi yok.', V.w/2, V.h/2); return; }
  ctx.lineJoin='round'; ctx.lineCap='round';
  ctx.beginPath(); for(let i=0;i<line.length;i++){ const s=T(line[i]); i?ctx.lineTo(s[0],s[1]):ctx.moveTo(s[0],s[1]); } ctx.closePath();
  ctx.strokeStyle='#1b222d'; ctx.lineWidth=24; ctx.stroke();
  ctx.strokeStyle='#39424e'; ctx.lineWidth=16; ctx.stroke();
  ctx.setLineDash([9,16]); ctx.strokeStyle='rgba(255,255,255,.10)'; ctx.lineWidth=2; ctx.stroke(); ctx.setLineDash([]);
  drawOverlay();
  for(let k=0;k<cars.length;k++){
    const c=cars[k], a=adv(c);
    const here=lerp(c.samples.realtime, a), nxt=lerp(c.samples.realtime, Math.min(1,a+0.006));
    if(!here||!nxt) continue;
    const s=T(here), sn=T(nxt);
    drawCar(s[0], s[1], Math.atan2(sn[1]-s[1], sn[0]-s[0]), c.code, c.colour, a>=0.999);
  }
}

function updateHud(){
  const t=performance.now(); if(t-lastHud<160) return; lastHud=t;
  let raw=null;
  if(cars.length>=2){
    const f=Math.min(adv(cars[0]),adv(cars[1]));
    const a=lerp(cars[0].samples.distance,f), b=lerp(cars[1].samples.distance,f);
    if(a&&b&&Number.isFinite(a.elapsed)&&Number.isFinite(b.elapsed)) raw=a.elapsed-b.elapsed;
  }
  $('delta').textContent = raw===null ? 'D --'
    : 'D '+Math.abs(raw).toFixed(3)+' sn - '+(raw<0?cars[0].code:raw>0?cars[1].code:'esit')+' onde';
  $('range').value = Math.round(p*1000);
  dtReadout();
}

function buildMinisectors(){
  const el=$('msec'); if(!el) return;
  if(cars.length<2 || !(cars[0].samples||{}).distance || !(cars[1].samples||{}).distance){ el.innerHTML=''; return; }
  const N=20, seg=[]; let maxAbs=0.0005, cum0=0;
  for(let i=0;i<N;i++){
    const a0=lerp(cars[0].samples.distance,i/N), a1=lerp(cars[0].samples.distance,(i+1)/N);
    const b0=lerp(cars[1].samples.distance,i/N), b1=lerp(cars[1].samples.distance,(i+1)/N);
    if(!a0||!a1||!b0||!b1){ seg.push(null); continue; }
    const d=(a1.elapsed-a0.elapsed)-(b1.elapsed-b0.elapsed);   // >0 => car0 bu dilimde daha yavaş
    seg.push(d); if(Math.abs(d)>maxAbs) maxAbs=Math.abs(d);
  }
  const bars=seg.map(function(d,i){
    if(d===null) return '<div class="msbar"></div>';
    const h=(Math.min(1,Math.abs(d)/maxAbs)*46).toFixed(1);
    const faster=d<0?cars[0].code:cars[1].code;
    const cls='msbar '+(d<0?'c0':'c1')+(Math.abs(d)>=maxAbs*0.6?' big':'');
    return '<div class="'+cls+'" title="Mini-sektör '+(i+1)+'/'+N+' — '+faster+' '+Math.abs(d).toFixed(3)+' sn hızlı"><i style="height:'+h+'%"></i></div>';
  }).join('');
  el.innerHTML='<div class="mslab"><span>MİNİ-SEKTÖR Δ · '+N+' DİLİM · zamanın nerede kazanıldığı</span>'
    +'<span><s style="color:#4ea981">▲ '+cars[0].code+'</s> · <s style="color:#d3576a">▼ '+cars[1].code+'</s></span></div>'
    +'<div class="msrow">'+bars+'</div>';
}

// --- kümülatif Δ izi (tur boyunca zaman farkı) ---
const dtcv=$('dtcv'), dtx=dtcv?dtcv.getContext('2d'):null;
let DT=null, dtRange=0.25, DV=null;
function buildDeltaTrace(){
  DT=null;
  if(cars.length<2) return;
  const s0=cars[0].samples.distance, s1=cars[1].samples.distance;
  if(!s0||!s1||!s0.length||!s1.length) return;
  if(!((+cars[0].samples.lap_seconds>0)&&(+cars[1].samples.lap_seconds>0))) return;
  const N=200, pts=[]; let mx=0.05;
  for(let i=0;i<=N;i++){
    const f=i/N, a=lerp(s0,f), b=lerp(s1,f);
    if(!a||!b||!Number.isFinite(a.elapsed)||!Number.isFinite(b.elapsed)){ pts.push(null); continue; }
    const v=b.elapsed-a.elapsed;   // v>0 => car0 daha az sürede geldi => car0 önde
    pts.push(v); if(Math.abs(v)>mx) mx=Math.abs(v);
  }
  DT=pts; dtRange=mx*1.15;
}
function fitDT(){
  if(!dtcv) return;
  const r=dtcv.getBoundingClientRect(), dpr=Math.min(2,devicePixelRatio||1);
  dtcv.width=Math.max(2,r.width*dpr); dtcv.height=Math.max(2,r.height*dpr);
  dtx.setTransform(dpr,0,0,dpr,0,0);
  DV={w:r.width,h:r.height};
  drawDT();
}
function drawDT(){
  if(!dtx||!DV) return;
  const w=DV.w,h=DV.h,mid=h/2;
  dtx.clearRect(0,0,w,h);
  if(!DT){ dtx.fillStyle='#6b7d8f'; dtx.font='700 10px Inter,Arial,sans-serif'; dtx.textAlign='center';
    dtx.fillText('Bu turlar için zaman telemetrisi yok.', w/2, mid+3); return; }
  const X=function(f){return f*w;}, Y=function(v){return mid - Math.max(-1,Math.min(1,v/dtRange))*(mid-6);};
  // zemin bantları — üst = 1. pilot rengi, alt = 2. pilot rengi
  dtx.fillStyle=(cars[0].colour||'#e10600')+'12'; dtx.fillRect(0,0,w,mid);
  dtx.fillStyle=(cars[1].colour||'#38bdf8')+'12'; dtx.fillRect(0,mid,w,h-mid);
  // sektör çizgileri
  (O.sectors||[]).forEach(function(sx){ const gx=X(sx.fraction||0);
    dtx.strokeStyle='rgba(244,211,94,.35)'; dtx.setLineDash([3,3]); dtx.lineWidth=1;
    dtx.beginPath(); dtx.moveTo(gx,0); dtx.lineTo(gx,h); dtx.stroke(); dtx.setLineDash([]); });
  // sıfır çizgisi
  dtx.strokeStyle='#3a4a5e'; dtx.lineWidth=1; dtx.beginPath(); dtx.moveTo(0,mid); dtx.lineTo(w,mid); dtx.stroke();
  // dolgulu alan
  let started=false;
  dtx.beginPath();
  for(let i=0;i<DT.length;i++){ const v=DT[i]; if(v===null){ continue; }
    const px=X(i/(DT.length-1)), py=Y(v);
    if(!started){ dtx.moveTo(px,mid); dtx.lineTo(px,py); started=true; } else dtx.lineTo(px,py); }
  if(started){
    dtx.lineTo(X(1),mid); dtx.closePath();
    const g=dtx.createLinearGradient(0,0,0,h);
    g.addColorStop(0,(cars[0].colour||'#e10600')+'55');
    g.addColorStop(0.5,(cars[0].colour||'#e10600')+'14');
    g.addColorStop(0.5,(cars[1].colour||'#38bdf8')+'14');
    g.addColorStop(1,(cars[1].colour||'#38bdf8')+'55');
    dtx.fillStyle=g; dtx.fill();
  }
  // çizgi
  dtx.beginPath(); started=false;
  for(let i=0;i<DT.length;i++){ const v=DT[i]; if(v===null){ started=false; continue; }
    const px=X(i/(DT.length-1)), py=Y(v);
    started?dtx.lineTo(px,py):dtx.moveTo(px,py); started=true; }
  dtx.strokeStyle='#e7eef6'; dtx.lineWidth=1.7; dtx.stroke();
  // oynatma imleci
  const f=Math.min(adv(cars[0]),adv(cars[1]));
  const cx=X(f); dtx.strokeStyle='rgba(255,255,255,.5)'; dtx.lineWidth=1;
  dtx.beginPath(); dtx.moveTo(cx,0); dtx.lineTo(cx,h); dtx.stroke();
  const idx=Math.round(f*(DT.length-1)); const cv=DT[idx];
  if(cv!==null&&cv!==undefined){ dtx.fillStyle='#fff'; dtx.beginPath(); dtx.arc(cx,Y(cv),3,0,7); dtx.fill(); }
}
function dtReadout(){
  const el=$('dtnow'); if(!el||!DT) return;
  const f=Math.min(adv(cars[0]),adv(cars[1]));
  const v=DT[Math.round(f*(DT.length-1))];
  if(v===null||v===undefined){ el.textContent='Δ --'; return; }
  el.textContent='Δ '+Math.abs(v).toFixed(3)+' sn · '+(Math.abs(v)<0.02?'eşit':(v>0?cars[0].code:cars[1].code)+' önde');
}

function buildStatic(){
  $('tags').innerHTML = cars.map(function(c){return '<span class="tag" style="--team:'+c.colour+'">'+c.code+' - '+c.lap+'</span>';}).join(' ');
  const c0=$('dtc0'); if(c0&&cars[0]){ c0.textContent=cars[0].code; c0.style.color=cars[0].colour||'#e7eef6'; }
  buildMinisectors();
  buildDeltaTrace();
  if(cars.length<2){ $('sectors').innerHTML=''; return; }
  $('sectors').innerHTML=[0,1,2].map(function(i){
    const a=(cars[0].sectors||[])[i]||'-', b=(cars[1].sectors||[])[i]||'-';
    const d=sec(a)-sec(b), ok=Number.isFinite(d);
    const c=i===0?'#f4d35e':i===1?'#56cfe1':'#ff7a9f';
    return '<div class="sector" style="--c:'+c+'"><small>SEKTOR '+(i+1)+' - '+(ok?(d<0?cars[0].code:d>0?cars[1].code:'ESIT'):'-')+' onde</small>'
      +'<div class="'+(ok&&d<=0?'win':'lose')+'">'+cars[0].code+' '+a+'</div>'
      +'<div class="'+(ok&&d>=0?'win':'lose')+'">'+cars[1].code+' '+b+'</div>'
      +'<div>D '+(ok?Math.abs(d).toFixed(3)+' sn':'-')+'</div></div>';
  }).join('');
}

function tick(t){
  t=t||performance.now();
  const dt=Math.min(0.05, Math.max(0,(t-last)/1000)); last=t;
  if(playing){
    p += dt*rate/maxLap;
    if(p>=1){ p=1; playing=false; $('play').textContent='Bastan'; }
  }
  draw(); drawDT(); updateHud();
}
function loop(t){ tick(t); raf=requestAnimationFrame(loop); }

$('play').onclick=function(){ if(p>=1) p=0; playing=!playing; $('play').textContent=playing?'Duraklat':'Oynat'; };
document.querySelectorAll('[data-rate]').forEach(function(b){ b.onclick=function(){
  rate=+b.dataset.rate;
  document.querySelectorAll('[data-rate]').forEach(function(x){ x.classList.toggle('active',x===b); });
}; });
$('range').oninput=function(e){ p=+e.target.value/1000; playing=false; $('play').textContent='Oynat'; draw(); drawDT(); updateHud(); };
if(dtcv){
  const seek=function(e){ const r=dtcv.getBoundingClientRect();
    const f=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));
    p=f; playing=false; $('play').textContent='Oynat'; draw(); drawDT(); updateHud(); };
  dtcv.addEventListener('pointerdown',function(e){ seek(e); dtcv.setPointerCapture(e.pointerId); });
  dtcv.addEventListener('pointermove',function(e){ if(e.buttons) seek(e); });
}
window.addEventListener('resize',function(){ fit(); fitDT(); });
document.addEventListener('visibilitychange',function(){ if(document.hidden) playing=false; });

buildStatic(); fit(); fitDT();
raf=requestAnimationFrame(loop);
setInterval(function(){ if(performance.now()-last>60) tick(); }, 40);
})();
</script>'''.replace('__PAYLOAD__', packed)


def two_driver_duel_html_repaired(*args, **kwargs):
    """Clean self-contained duel HUD; old .replace() patch layers removed."""
    return two_driver_duel_html_stable(*args, **kwargs)


def _telemetry_trace_payload_v38(entries, grid_n=480):
    """entries: [(code, colour, lap_label, telemetry_df)] -> etkileşimli iz paketi.

    Tüm turlar ortak mesafe ızgarasına örneklenir; hız/gaz/fren/vites + X/Y
    aynı indekste hizalanır, böylece tek bir imleç dört izi ve pist noktasını
    eşzamanlı gösterir."""
    frames, max_d = [], 0.0
    for code, colour, lap_label, tel in entries:
        cols = list(getattr(tel, 'columns', []))
        if 'Distance' not in cols:
            continue
        frame = tel[[c for c in ('Distance', 'Speed', 'Throttle', 'Brake', 'nGear', 'X', 'Y') if c in cols]].copy()
        frame['Distance'] = pd.to_numeric(frame['Distance'], errors='coerce')
        frame = frame.dropna(subset=['Distance']).sort_values('Distance').drop_duplicates('Distance')
        if len(frame) < 5:
            continue
        max_d = max(max_d, float(frame['Distance'].max()))
        frames.append((str(code), str(colour), str(lap_label or ''), frame))
    if not frames or max_d <= 0:
        return {'ok': False}

    grid = np.linspace(0.0, max_d, grid_n)

    def _series(frame, name, fill=0.0):
        if name not in frame.columns:
            return np.full(grid_n, fill)
        values = pd.to_numeric(frame[name], errors='coerce').astype(float).ffill().bfill().fillna(fill)
        return np.interp(grid, frame['Distance'].values, values.values)

    drivers, track = [], []
    for index, (code, colour, lap_label, frame) in enumerate(frames):
        speed = _series(frame, 'Speed')
        throttle = _series(frame, 'Throttle')
        brake = _series(frame, 'Brake')
        if float(np.nanmax(brake) if brake.size else 0) <= 1.5:
            brake = brake * 100.0
        gear = _series(frame, 'nGear')
        xs, ys = _series(frame, 'X'), _series(frame, 'Y')
        drivers.append({
            'code': code, 'colour': colour, 'lap': lap_label,
            'speed': [round(float(v), 1) for v in speed],
            'throttle': [round(float(max(0.0, min(100.0, v))), 1) for v in throttle],
            'brake': [round(float(max(0.0, min(100.0, v))), 0) for v in brake],
            'gear': [int(round(float(v))) for v in gear],
            'x': [round(float(v), 1) for v in xs],
            'y': [round(float(v), 1) for v in ys],
        })
        if index == 0:
            track = [[round(float(xs[k]), 1), round(float(ys[k]), 1)] for k in range(grid_n)]
    return {
        'ok': True,
        'drivers': drivers,
        'distance': [round(float(v), 0) for v in grid],
        'track': track,
        'sectors': [],
    }


def telemetry_trace_html(payload):
    """Etkileşimli telemetri HUD'u: hız / gaz / fren / vites izleri ortak mesafe
    ekseninde; fare imleci dördünü ve pist üzerindeki konumu eşzamanlı gösterir."""
    packed = fp_ui.json_for_script(payload)
    return r'''<style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}
.hud{border:1px solid #26313f;border-radius:13px;padding:12px;background:#11161f}
.head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:flex-start}
.title{font-size:13px;font-weight:950;letter-spacing:.09em}
.sub{font-size:10px;color:#9fb0c0;margin-top:5px;max-width:520px}
.tags{display:flex;gap:6px;flex-wrap:wrap}
.tag{border:1px solid #35506d;border-radius:7px;padding:5px 8px;font:900 11px Inter,Arial,sans-serif;color:var(--team)}
.wrap{display:grid;grid-template-columns:290px minmax(0,1fr);gap:12px;margin-top:10px}
.mapbox{border:1px solid #26313f;border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 45%,#141b26,#07090d 78%)}
.mapbox canvas{width:100%;height:250px;display:block;cursor:crosshair}
.readout{margin-top:8px;border:1px solid #26313f;border-radius:9px;background:#0d131c;padding:9px 10px}
.readout .rh{color:#8496a8;font:900 9.5px Inter,Arial,sans-serif;letter-spacing:.07em;margin-bottom:5px}
.readout .rd{display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-top:1px solid #1b2531;font:800 12px ui-monospace,Consolas,monospace;color:#c7d6e6}
.readout .rd:first-of-type{border-top:0}
.readout .rd span{color:#8fa2b4}
.charts{display:flex;flex-direction:column;gap:6px}
.chart{position:relative;border:1px solid #212b38;border-radius:8px;background:#0c121b}
.chart canvas{width:100%;display:block;cursor:crosshair}
.chart .lab{position:absolute;top:5px;left:9px;font:900 9px Inter,Arial,sans-serif;letter-spacing:.09em;color:#7c90a4;pointer-events:none}
@media(max-width:640px){.wrap{grid-template-columns:1fr}}
</style>
<div class="hud">
  <div class="head">
    <div><div class="title">ETKILESIMLI TELEMETRI</div><div class="sub">Fareyi grafigin veya pistin uzerinde gezdir - imlec dort izi ve pist noktasini eszamanli okur. Fren izindeki dikey sicrama = fren noktasi.</div></div>
    <div class="tags" id="tags"></div>
  </div>
  <div class="wrap">
    <div>
      <div class="mapbox"><canvas id="map"></canvas></div>
      <div class="readout" id="readout"></div>
    </div>
    <div class="charts" id="charts">
      <div class="chart"><span class="lab">HIZ km/h</span><canvas data-k="speed"></canvas></div>
      <div class="chart"><span class="lab">GAZ %</span><canvas data-k="throttle"></canvas></div>
      <div class="chart"><span class="lab">FREN</span><canvas data-k="brake"></canvas></div>
      <div class="chart"><span class="lab">VITES</span><canvas data-k="gear"></canvas></div>
    </div>
  </div>
</div>
<script>
"use strict";
(function(){
const D=__PAYLOAD__, drv=D.drivers||[], DIST=D.distance||[], TRACK=D.track||[];
const N=DIST.length;
const $=function(s){return document.querySelector(s);};
if(!N||!drv.length){ $('#readout').textContent='Telemetri izi yok.'; return; }
let cursor=Math.floor(N*0.5);

const specs={
  speed:{h:132,min:0,max:340,fmt:function(v){return Math.round(v);}},
  throttle:{h:74,min:0,max:100},
  brake:{h:62,min:0,max:100},
  gear:{h:70,min:0,max:8,step:true}
};
(function(){ let mx=0; drv.forEach(function(c){ (c.speed||[]).forEach(function(v){ if(v>mx)mx=v; }); });
  specs.speed.max=Math.max(120,Math.ceil((mx+8)/20)*20); })();

const charts=[].slice.call(document.querySelectorAll('.chart canvas')).map(function(cv){
  return {cv:cv, ctx:cv.getContext('2d'), k:cv.dataset.k};
});
const mapCv=$('#map'), mapCtx=mapCv.getContext('2d');
let MB=null;

function tags(){ $('#tags').innerHTML=drv.map(function(c){
  return '<span class="tag" style="--team:'+c.colour+'">'+c.code+(c.lap?' - '+c.lap:'')+'</span>'; }).join(''); }

function fitMap(){
  const r=mapCv.getBoundingClientRect(), dpr=Math.min(2,devicePixelRatio||1);
  mapCv.width=Math.max(2,r.width*dpr); mapCv.height=Math.max(2,r.height*dpr);
  mapCtx.setTransform(dpr,0,0,dpr,0,0);
  let a=1e18,b=-1e18,d=1e18,e=-1e18;
  TRACK.forEach(function(p){ if(p[0]<a)a=p[0]; if(p[0]>b)b=p[0]; if(p[1]<d)d=p[1]; if(p[1]>e)e=p[1]; });
  const pad=20, sx=(b-a)||1, sy=(e-d)||1, s=Math.min((r.width-pad*2)/sx,(r.height-pad*2)/sy);
  MB={s:s,w:r.width,h:r.height,ox:(r.width-sx*s)/2-a*s,oy:(r.height-sy*s)/2+e*s};
}
function mapT(p){ return [p[0]*MB.s+MB.ox, -p[1]*MB.s+MB.oy]; }

function fitCharts(){
  charts.forEach(function(o){
    const sp=specs[o.k]; o.cv.style.height=sp.h+'px';
    const r=o.cv.getBoundingClientRect(), dpr=Math.min(2,devicePixelRatio||1);
    o.cv.width=Math.max(2,r.width*dpr); o.cv.height=Math.max(2,sp.h*dpr);
    o.ctx.setTransform(dpr,0,0,dpr,0,0); o.w=r.width; o.h=sp.h;
  });
}

function drawChart(o){
  const sp=specs[o.k], w=o.w, h=o.h, ctx=o.ctx, pl=7, pr=7, pt=15, pb=6;
  if(!w) return;
  ctx.clearRect(0,0,w,h);
  const X=function(i){ return pl+(i/(N-1))*(w-pl-pr); };
  const Y=function(v){ return pt+(1-(v-sp.min)/((sp.max-sp.min)||1))*(h-pt-pb); };
  ctx.strokeStyle='#1a2330'; ctx.lineWidth=1;
  [0.5].concat(sp.step?[]:[0.25,0.75]).forEach(function(f){ const gy=pt+f*(h-pt-pb);
    ctx.beginPath(); ctx.moveTo(pl,gy); ctx.lineTo(w-pr,gy); ctx.stroke(); });
  (D.sectors||[]).forEach(function(sc){ const gx=X((sc.fraction||0)*(N-1));
    ctx.strokeStyle='rgba(244,211,94,.28)'; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(gx,pt); ctx.lineTo(gx,h-pb); ctx.stroke(); ctx.setLineDash([]); });
  drv.forEach(function(c){
    const arr=c[o.k]||[]; ctx.beginPath();
    for(let i=0;i<N;i++){ const px=X(i);
      if(sp.step && i) ctx.lineTo(px,Y(arr[i-1]||0));
      const py=Y(arr[i]||0); i?ctx.lineTo(px,py):ctx.moveTo(px,py); }
    ctx.strokeStyle=c.colour; ctx.lineWidth=1.6; ctx.stroke();
  });
  const cx=X(cursor);
  ctx.strokeStyle='rgba(255,255,255,.5)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(cx,pt); ctx.lineTo(cx,h-pb); ctx.stroke();
  drv.forEach(function(c){ const v=(c[o.k]||[])[cursor]||0;
    ctx.fillStyle=c.colour; ctx.beginPath(); ctx.arc(cx,Y(v),2.7,0,7); ctx.fill(); });
}

function drawMap(){
  if(!MB) return;
  const w=MB.w,h=MB.h; mapCtx.clearRect(0,0,w,h);
  mapCtx.lineJoin='round'; mapCtx.lineCap='round';
  mapCtx.beginPath(); TRACK.forEach(function(p,i){ const s=mapT(p); i?mapCtx.lineTo(s[0],s[1]):mapCtx.moveTo(s[0],s[1]); });
  mapCtx.strokeStyle='#1b222d'; mapCtx.lineWidth=13; mapCtx.stroke();
  mapCtx.strokeStyle='#39424e'; mapCtx.lineWidth=8; mapCtx.stroke();
  (D.sectors||[]).forEach(function(sc){ const idx=Math.round((sc.fraction||0)*(N-1)), p=TRACK[idx]; if(!p)return;
    const s=mapT(p); mapCtx.fillStyle='#f4d35e'; mapCtx.beginPath(); mapCtx.arc(s[0],s[1],3,0,7); mapCtx.fill(); });
  drv.forEach(function(c){ const px=(c.x||[])[cursor], py=(c.y||[])[cursor]; if(px==null)return;
    const s=mapT([px,py]); mapCtx.fillStyle=c.colour; mapCtx.strokeStyle='#05080d'; mapCtx.lineWidth=2;
    mapCtx.beginPath(); mapCtx.arc(s[0],s[1],5,0,7); mapCtx.fill(); mapCtx.stroke(); });
}

function readout(){
  const d=DIST[cursor]||0;
  let html='<div class="rh">MESAFE '+Math.round(d)+' m - imlec '+(Math.round((cursor/(N-1))*100))+'% tur</div>';
  [['Hiz','speed',' km/h'],['Gaz','throttle',' %'],['Fren','brake',''],['Vites','gear','']].forEach(function(r){
    html+='<div class="rd"><span>'+r[0]+'</span><b>'+drv.map(function(c){
      const v=(c[r[1]]||[])[cursor];
      return '<span style="color:'+c.colour+'">'+(v==null?'-':Math.round(v))+'</span>';
    }).join('  ')+r[2]+'</b></div>';
  });
  if(drv.length>=2){
    const ds=((drv[0].speed||[])[cursor]||0)-((drv[1].speed||[])[cursor]||0);
    html+='<div class="rd"><span>&Delta; hiz</span><b>'+(ds>0?'+':'')+Math.round(ds)+' km/h - '+(Math.abs(ds)<1?'esit':(ds>0?drv[0].code:drv[1].code)+' hizli')+'</b></div>';
  }
  $('#readout').innerHTML=html;
}

function render(){ charts.forEach(drawChart); drawMap(); readout(); }

function seekFromClientX(clientX, el){
  const r=el.getBoundingClientRect();
  const f=Math.max(0,Math.min(1,(clientX-r.left-7)/(r.width-14)));
  cursor=Math.round(f*(N-1)); render();
}
function nearestOnTrack(clientX, clientY){
  const r=mapCv.getBoundingClientRect();
  const mx=clientX-r.left, my=clientY-r.top; let best=0, bd=1e18;
  for(let i=0;i<TRACK.length;i++){ const s=mapT(TRACK[i]);
    const dd=(s[0]-mx)*(s[0]-mx)+(s[1]-my)*(s[1]-my); if(dd<bd){ bd=dd; best=i; } }
  cursor=best; render();
}
charts.forEach(function(o){
  o.cv.addEventListener('pointermove',function(e){ if(e.pointerType==='mouse'||e.buttons) seekFromClientX(e.clientX,o.cv); });
  o.cv.addEventListener('pointerdown',function(e){ seekFromClientX(e.clientX,o.cv); try{o.cv.setPointerCapture(e.pointerId);}catch(_){} });
});
mapCv.addEventListener('pointermove',function(e){ if(e.pointerType==='mouse'||e.buttons) nearestOnTrack(e.clientX,e.clientY); });
mapCv.addEventListener('pointerdown',function(e){ nearestOnTrack(e.clientX,e.clientY); try{mapCv.setPointerCapture(e.pointerId);}catch(_){} });

function fitAll(){ fitMap(); fitCharts(); render(); }
let rz=0; window.addEventListener('resize',function(){ clearTimeout(rz); rz=setTimeout(fitAll,120); });
tags(); fitAll();
setTimeout(fitAll,60);
})();
</script>'''.replace('__PAYLOAD__', packed)


def dominance_map_html(payload):
    """Kuş bakışı pist dominasyonu — pist her noktada o an daha hızlı olan
    pilotun rengiyle boyanır; imleç o noktadaki iki hızı ve farkı okur."""
    packed = fp_ui.json_for_script(payload)
    return r'''<style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}
.hud{border:1px solid #26313f;border-radius:13px;padding:12px;background:#11161f}
.head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:flex-start}
.title{font-size:13px;font-weight:950;letter-spacing:.09em}
.sub{font-size:10px;color:#9fb0c0;margin-top:5px;max-width:540px}
.tags{display:flex;gap:6px;flex-wrap:wrap}
.tag{border:1px solid #35506d;border-radius:7px;padding:5px 8px;font:900 11px Inter,Arial,sans-serif;color:var(--team)}
.mapbox{margin-top:10px;border:1px solid #26313f;border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 45%,#141b26,#07090d 78%)}
.mapbox canvas{width:100%;height:430px;display:block;cursor:crosshair}
.share{display:flex;height:16px;border-radius:6px;overflow:hidden;margin-top:10px;border:1px solid #26313f;font:900 9px Inter,Arial,sans-serif}
.share i{display:flex;align-items:center;justify-content:center;color:#05080d}
.rowline{display:flex;justify-content:space-between;gap:8px;margin-top:8px;font:800 12px ui-monospace,Consolas,monospace;color:#c7d6e6}
.rowline span{color:#8fa2b4}
@media(max-width:640px){.mapbox canvas{height:320px}}
</style>
<div class="hud">
  <div class="head">
    <div><div class="title">KUS BAKISI PIST DOMINASYONU</div><div class="sub">Pist her noktada o an daha hizli olan pilotun rengiyle boyanir. Fareyi pistin uzerinde gezdir: alttaki panel o noktadaki iki hizi ve farki verir.</div></div>
    <div class="tags" id="tags"></div>
  </div>
  <div class="mapbox"><canvas id="dom"></canvas></div>
  <div class="share" id="share"></div>
  <div class="rowline" id="cur"><span>Imlec</span><b>pistin uzerine gel</b></div>
</div>
<script>
"use strict";
(function(){
const D=__PAYLOAD__, drv=(D.drivers||[]).slice(0,2), DIST=D.distance||[], TRACK=D.track||[];
const N=Math.min(DIST.length, TRACK.length);
const $=function(s){return document.querySelector(s);};
if(drv.length<2 || N<4){ $('#cur').innerHTML='<span>Hata</span><b>iki tur icin konum telemetrisi yok</b>'; return; }
const cv=$('#dom'), ctx=cv.getContext('2d'); let MB=null, cursor=-1;
const c0=drv[0].colour||'#e10600', c1=drv[1].colour||'#38e1d0';
const faster=[]; let lead0=0;
for(let i=0;i<N;i++){ const a=(drv[0].speed||[])[i]||0, b=(drv[1].speed||[])[i]||0; const f=a>=b?0:1; faster.push(f); if(f===0) lead0++; }

function tags(){ $('#tags').innerHTML=drv.map(function(c){ return '<span class="tag" style="--team:'+c.colour+'">'+c.code+'</span>'; }).join(''); }
function share(){
  const p0=Math.round(lead0/N*100);
  $('#share').innerHTML='<i style="width:'+p0+'%;background:'+c0+'">'+drv[0].code+' '+p0+'%</i>'
    +'<i style="width:'+(100-p0)+'%;background:'+c1+'">'+drv[1].code+' '+(100-p0)+'%</i>';
}
function fit(){
  const r=cv.getBoundingClientRect(), dpr=Math.min(2,devicePixelRatio||1);
  cv.width=Math.max(2,r.width*dpr); cv.height=Math.max(2,r.height*dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  let a=1e18,b=-1e18,d=1e18,e=-1e18;
  TRACK.forEach(function(p){ if(p[0]<a)a=p[0]; if(p[0]>b)b=p[0]; if(p[1]<d)d=p[1]; if(p[1]>e)e=p[1]; });
  const pad=26, sx=(b-a)||1, sy=(e-d)||1, s=Math.min((r.width-pad*2)/sx,(r.height-pad*2)/sy);
  MB={s:s,w:r.width,h:r.height,ox:(r.width-sx*s)/2-a*s,oy:(r.height-sy*s)/2+e*s};
  draw();
}
function T(p){ return [p[0]*MB.s+MB.ox, -p[1]*MB.s+MB.oy]; }
function draw(){
  if(!MB) return; const w=MB.w,h=MB.h; ctx.clearRect(0,0,w,h);
  ctx.lineJoin='round'; ctx.lineCap='round';
  ctx.beginPath(); for(let i=0;i<N;i++){ const s=T(TRACK[i]); i?ctx.lineTo(s[0],s[1]):ctx.moveTo(s[0],s[1]); }
  ctx.strokeStyle='#121822'; ctx.lineWidth=16; ctx.stroke();
  for(let i=1;i<N;i++){ const s0=T(TRACK[i-1]), s1=T(TRACK[i]);
    ctx.beginPath(); ctx.moveTo(s0[0],s0[1]); ctx.lineTo(s1[0],s1[1]);
    ctx.strokeStyle=faster[i]===0?c0:c1; ctx.lineWidth=6; ctx.stroke(); }
  const s0=T(TRACK[0]); ctx.fillStyle='#eef4fa'; ctx.beginPath(); ctx.arc(s0[0],s0[1],4,0,7); ctx.fill();
  if(cursor>=0 && cursor<N){ const s=T(TRACK[cursor]);
    ctx.strokeStyle='#fff'; ctx.lineWidth=2; ctx.beginPath(); ctx.arc(s[0],s[1],7,0,7); ctx.stroke(); }
}
function pick(clientX,clientY){
  const r=cv.getBoundingClientRect(), mx=clientX-r.left, my=clientY-r.top; let best=0,bd=1e18;
  for(let i=0;i<N;i++){ const s=T(TRACK[i]); const dd=(s[0]-mx)*(s[0]-mx)+(s[1]-my)*(s[1]-my); if(dd<bd){bd=dd;best=i;} }
  cursor=best; draw(); readout();
}
function readout(){
  if(cursor<0){ return; }
  const a=(drv[0].speed||[])[cursor]||0, b=(drv[1].speed||[])[cursor]||0, dd=a-b;
  $('#cur').innerHTML='<span>'+Math.round(DIST[cursor]||0)+' m</span><b>'
    +'<span style="color:'+c0+'">'+drv[0].code+' '+Math.round(a)+'</span>  '
    +'<span style="color:'+c1+'">'+drv[1].code+' '+Math.round(b)+'</span>  km/h  ·  '
    +(Math.abs(dd)<1?'esit':(dd>0?drv[0].code:drv[1].code)+' +'+Math.abs(Math.round(dd)))+'</b>';
}
cv.addEventListener('pointermove',function(e){ if(e.pointerType==='mouse'||e.buttons) pick(e.clientX,e.clientY); });
cv.addEventListener('pointerdown',function(e){ pick(e.clientX,e.clientY); try{cv.setPointerCapture(e.pointerId);}catch(_){} });
let rz=0; window.addEventListener('resize',function(){ clearTimeout(rz); rz=setTimeout(fit,120); });
tags(); share(); fit(); setTimeout(fit,60);
})();
</script>'''.replace('__PAYLOAD__', packed)


def _openf1_credentials():
    """Canlı erişim bilgilerini yalnızca Streamlit sunucusundan okur; tarayıcıya göndermez."""
    try:
        username = str(st.secrets.get('OPENF1_USERNAME', '')).strip()
        password = str(st.secrets.get('OPENF1_PASSWORD', '')).strip()
    except Exception:
        username = os.getenv('OPENF1_USERNAME', '').strip()
        password = os.getenv('OPENF1_PASSWORD', '').strip()
    return username, password


@st.cache_data(ttl=3300, show_spinner=False)
def _openf1_token(username, password):
    """OAuth tokeni sunucuda yeniler; token hiçbir HTML/JavaScript içine yazılmaz."""
    if not username or not password:
        return ''
    try:
        encoded = urllib.parse.urlencode({'username': username, 'password': password}).encode('utf-8')
        request = urllib.request.Request(
            'https://api.openf1.org/token', data=encoded, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'PaddockDataCentre/1.0'}
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            return str(json.loads(response.read().decode('utf-8')).get('access_token', ''))
    except Exception:
        return ''


def _api_duration_text(value):
    try:
        return format_time(pd.to_timedelta(float(value), unit='s'))
    except (TypeError, ValueError):
        return '-'


# =========================================================
# V19: AÇIK VERİ, YARIŞ İSTİHBARATI VE PADDOCK ASİSTAN
# =========================================================

def _secret_or_environment(name):
    """Sırları kodun içine yazmadan Streamlit Secrets veya ortam değişkeninden alır."""
    try:
        value = st.secrets.get(name, '')
    except Exception:
        value = os.getenv(name, '')
    return str(value or '').strip()


def get_openf1_access_v19():
    """OpenF1 erişimi isteğe bağlıdır; token yoksa uygulama sahte canlı veri üretmez."""
    token = _secret_or_environment('OPENF1_TOKEN') or _secret_or_environment('OPENF1_ACCESS_TOKEN')
    username, password = _openf1_credentials()
    return token, username, password


@st.cache_data(ttl=3300, show_spinner=False)
def _openf1_token_v19(explicit_token, username, password):
    """Varsa doğrudan tokeni, yoksa eski kullanıcı adı/parola yolunu kullanır."""
    if explicit_token:
        return explicit_token
    return _openf1_token(username, password)


def _openf1_get_optional_v19(endpoint, token=''):
    """Tek endpoint bozulsa bile tüm canlı HUD'un çökmesini engeller."""
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'PaddockDataCentre/1.9',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(
        f'https://api.openf1.org/v1/{endpoint}',
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=7) as response:
            decoded = json.loads(response.read().decode('utf-8'))
        return decoded if isinstance(decoded, list) else []
    except Exception:
        return []


def _latest_by_driver_v19(records):
    latest = {}
    for record in records or []:
        number = record.get('driver_number')
        if number is None:
            continue
        key = str(number)
        current = latest.get(key)
        if current is None or str(record.get('date', '')) >= str(current.get('date', '')):
            latest[key] = record
    return latest


def _openf1_track_outline_v19(locations):
    """Konum paketi birden fazla nokta içerdiğinde gerçek kayıttan hafif bir pist izi çıkarır."""
    grouped = {}
    for item in locations or []:
        number = str(item.get('driver_number', ''))
        try:
            point = {
                'x': float(item.get('x')),
                'y': float(item.get('y')),
                'date': str(item.get('date', '')),
            }
        except (TypeError, ValueError):
            continue
        grouped.setdefault(number, []).append(point)
    if not grouped:
        return []
    source = max(grouped.values(), key=len)
    source = sorted(source, key=lambda item: item['date'])
    if len(source) < 24:
        return []
    step = max(1, len(source) // 620)
    return [{'x': round(item['x'], 1), 'y': round(item['y'], 1)} for item in source[::step]]


def _openf1_weather_summary_v19(records):
    if not records:
        return {}
    latest = max(records, key=lambda item: str(item.get('date', '')))

    def number(name):
        value = pd.to_numeric(latest.get(name), errors='coerce')
        return None if pd.isna(value) else round(float(value), 1)

    return {
        'air': number('air_temperature'),
        'track': number('track_temperature'),
        'humidity': number('humidity'),
        'wind': number('wind_speed'),
        'rainfall': bool(latest.get('rainfall', False)),
        'date': str(latest.get('date', '')),
    }


def _openf1_race_control_v19(records, limit=5):
    messages = []
    seen = set()
    for item in sorted(records or [], key=lambda row: str(row.get('date', '')), reverse=True):
        raw = str(item.get('message') or item.get('Message') or '').strip()
        translated = translate_race_control_message(raw)
        if translated and translated not in seen:
            seen.add(translated)
            messages.append({
                'time': str(item.get('date', ''))[-8:],
                'text': translated,
            })
        if len(messages) >= limit:
            break
    return list(reversed(messages))


def _openf1_utc_v19(value):
    """OpenF1 zamanını karşılaştırma için UTC'ye çevirir; bozuk tarih gelirse None döner."""
    try:
        timestamp = pd.to_datetime(value)
        return timestamp.tz_localize('UTC') if timestamp.tzinfo is None else timestamp.tz_convert('UTC')
    except Exception:
        return None


def _openf1_endpoint_v19(resource, **params):
    clean = {str(key): str(value) for key, value in params.items() if value not in (None, '')}
    return resource if not clean else f"{resource}?{urllib.parse.urlencode(clean)}"


@st.cache_data(ttl=60, show_spinner=False)
def get_openf1_live_session_context_v19(token=''):
    """`latest` seansının gerçekten açık olup olmadığını doğrular.

    Tamamlanmış bir seansın konum kayıtlarını canlı gibi göstermemek için önce
    yalnızca bu hafif meta paketi okunur.
    """
    records = _openf1_get_optional_v19('sessions?session_key=latest', token)
    session = records[0] if records else {}
    start = _openf1_utc_v19(session.get('date_start'))
    end = _openf1_utc_v19(session.get('date_end'))
    now = pd.Timestamp.now(tz='UTC')
    active = False
    if start is not None and now >= start:
        # date_end yoksa API'nin açık seans için verdiği en güvenli kısa pencere.
        active = now <= ((end + pd.Timedelta(minutes=3)) if end is not None else (start + pd.Timedelta(hours=4)))
    if not session:
        reason = 'Açık bir OpenF1 seans paketi bulunamadı.'
    elif not active:
        reason = 'OpenF1’deki son paket tamamlanmış bir seansa ait; geçmiş kayıt canlı diye gösterilmez.'
    else:
        reason = ''
    return {'session': session, 'active': bool(active), 'reason': reason}


@st.cache_data(ttl=1800, show_spinner=False)
def get_openf1_session_track_outline_v19(session_key, driver_number, token=''):
    """Tek bir aracın kayıtlı konumundan hafif bir pist izi çıkarır.

    Tüm gridin tüm konum geçmişini istemez; bu, canlı sayfayı gereksizce
    ağırlaştırmadan gerçek koordinatlı pist izi sağlamayı amaçlar.
    """
    endpoint = _openf1_endpoint_v19(
        'location', session_key=session_key, driver_number=driver_number
    )
    return _openf1_track_outline_v19(_openf1_get_optional_v19(endpoint, token))


@st.cache_data(ttl=20, show_spinner=False)
def get_openf1_live_snapshot_verified_v19(explicit_token='', username='', password=''):
    """Sadece doğrulanmış açık seans için sınırlı, düşük frekanslı canlı HUD paketi döndürür."""
    token = _openf1_token_v19(explicit_token, username, password)
    context = get_openf1_live_session_context_v19(token)
    session = context.get('session', {})
    if not context.get('active'):
        return {
            'ok': False, 'reason': context.get('reason', 'Canlı seans bekleniyor.'),
            'authenticated': bool(token), 'source': 'OpenF1 seans doğrulaması',
            'cars': [], 'track': [], 'session': session, 'weather': {}, 'race_control': [],
        }
    if not token:
        return {
            'ok': False,
            'reason': 'Açık canlı seans doğrulandı; ancak bu sağlayıcı canlı konum paketi için yetkili erişim istiyor. Site sahte araç konumu çizmez.',
            'authenticated': False, 'source': 'OpenF1 seans doğrulaması',
            'cars': [], 'track': [], 'session': session, 'weather': {}, 'race_control': [],
        }

    session_key = session.get('session_key') or 'latest'
    drivers = _openf1_get_optional_v19(
        _openf1_endpoint_v19('drivers', session_key=session_key), token
    )
    if not drivers:
        return {
            'ok': False, 'reason': 'Canlı seans için sürücü paketi henüz gelmedi.',
            'authenticated': True, 'source': 'OpenF1 canlı paket',
            'cars': [], 'track': [], 'session': session, 'weather': {}, 'race_control': [],
        }

    recent_iso = (pd.Timestamp.now(tz='UTC') - pd.Timedelta(seconds=50)).isoformat().replace('+00:00', 'Z')
    dynamic = {
        'session_key': session_key,
        'date>': recent_iso,
    }
    # 20 saniyede bir dokuz küçük paket: ücretsiz tarihsel limitini zorlamaz;
    # otomatik yenileme zaten yalnızca tokenli canlı pakette açılır.
    locations = _openf1_get_optional_v19(_openf1_endpoint_v19('location', **dynamic), token)
    laps = _openf1_get_optional_v19(_openf1_endpoint_v19('laps', session_key=session_key), token)
    stints = _openf1_get_optional_v19(_openf1_endpoint_v19('stints', session_key=session_key), token)
    pits = _openf1_get_optional_v19(_openf1_endpoint_v19('pit', session_key=session_key), token)
    positions = _openf1_get_optional_v19(_openf1_endpoint_v19('position', **dynamic), token)
    intervals = _openf1_get_optional_v19(_openf1_endpoint_v19('intervals', **dynamic), token)
    weather = _openf1_get_optional_v19(_openf1_endpoint_v19('weather', **dynamic), token)
    race_control = _openf1_get_optional_v19(_openf1_endpoint_v19('race_control', session_key=session_key), token)

    driver_map = {
        str(item.get('driver_number')): item for item in drivers
        if item.get('driver_number') is not None
    }
    location_map = _latest_by_driver_v19(locations)
    lap_map = _latest_by_driver_v19(laps)
    stint_map = _latest_by_driver_v19(stints)
    pit_map = _latest_by_driver_v19(pits)
    position_map = _latest_by_driver_v19(positions)
    interval_map = _latest_by_driver_v19(intervals)

    cars = []
    for number, location in location_map.items():
        try:
            x, y = float(location.get('x')), float(location.get('y'))
        except (TypeError, ValueError):
            continue
        driver = driver_map.get(number, {})
        lap, stint = lap_map.get(number, {}), stint_map.get(number, {})
        pit, position, interval = pit_map.get(number, {}), position_map.get(number, {}), interval_map.get(number, {})
        code = str(driver.get('name_acronym') or driver.get('last_name') or f'#{number}').strip()
        team = canonical_team_name(driver.get('team_name') or 'Formula 1')
        pit_passage = pd.to_numeric(pit.get('pit_duration'), errors='coerce')
        cars.append({
            'number': number, 'code': code, 'team': team, 'colour': team_colour(team),
            'x': x, 'y': y, 'position': position.get('position') or '—',
            'lap': lap.get('lap_number') or '—',
            'last_lap': _api_duration_text(lap.get('lap_duration')) if lap.get('lap_duration') else '—',
            'compound': str(stint.get('compound') or '—').upper(),
            'tyre_age_start': stint.get('tyre_age_at_start') if stint.get('tyre_age_at_start') is not None else '—',
            'gap': str(interval.get('gap_to_leader') or interval.get('interval') or '—'),
            'last_pit_lap': pit.get('lap_number') or '—',
            # Bu değer servis süresi diye yorumlanmaz; sağlayıcının pit geçiş ölçümüdür.
            'pit_passage': None if pd.isna(pit_passage) else round(float(pit_passage), 2),
            'profile': race_driver_profile(code, team), 'date': str(location.get('date', '')),
        })

    def position_key(item):
        try:
            return int(float(item['position']))
        except (TypeError, ValueError):
            return 999

    cars.sort(key=lambda item: (position_key(item), item['number']))
    outline_driver = str(drivers[0].get('driver_number', '')) if drivers else ''
    outline = get_openf1_session_track_outline_v19(session_key, outline_driver, token) if outline_driver else []
    return {
        'ok': bool(cars),
        'reason': '' if cars else 'Canlı seans açık, fakat son 50 saniyeye ait doğrulanmış araç konumu henüz gelmedi.',
        'authenticated': True, 'source': 'OpenF1 yetkili canlı paket', 'cars': cars,
        'track': outline, 'session': session,
        'weather': _openf1_weather_summary_v19(weather),
        'race_control': _openf1_race_control_v19(race_control),
    }


# Eski fonksiyon adı korunur; sayfanın diğer kısmı yalnızca doğrulanmış V19 paketini kullanır.
get_openf1_live_snapshot_v19 = get_openf1_live_snapshot_verified_v19


def live_race_hud_html_v19(snapshot):
    """Gerçek paket geldiğinde pist izi, seçilebilir araç, lastik/pit/hava/Race Control HUD'u üretir."""
    payload = fp_ui.json_for_script(snapshot)
    return r"""
    <style>
      *{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}
      .hud{border:1px solid #2c425c;border-radius:14px;background:linear-gradient(135deg,#11161f,#09111b);padding:14px}
      .top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.title{font-size:13px;font-weight:950;letter-spacing:.11em}.sub{font-size:11px;color:#91a9c1;margin-top:5px}.signal{font-size:11px;color:#6ee7a4;font-weight:900;border:1px solid #2d5f4b;background:#102b23;border-radius:7px;padding:6px 8px}
      .layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px;margin-top:12px}.map{border:1px solid #28405a;border-radius:11px;background:radial-gradient(circle at 50% 42%,#17263b,#080d14 73%);overflow:hidden}.map canvas{width:100%;height:455px;display:block}.panel{border:1px solid #2d4057;border-radius:11px;background:#11161f;padding:12px}
      .hero{position:relative;overflow:hidden;min-height:76px;border-bottom:1px solid #293c53;padding-bottom:10px}.portrait{position:absolute;right:-4px;bottom:0;max-height:94px;max-width:92px;object-fit:contain;opacity:.86}.selected{font-size:20px;font-weight:950;color:var(--team);position:relative;z-index:1}.team{font-size:12px;color:#9ab0c6;margin:4px 0 9px;position:relative;z-index:1}.stat{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #26394f;padding:8px 0;font-size:12px}.stat span{color:#91a7be}.tyre{display:inline-flex;width:22px;height:22px;align-items:center;justify-content:center;border-radius:50%;border:2px solid var(--tyre);color:var(--tyre);font-weight:950}
      .weather{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}.weather div{background:#0d1725;border:1px solid #26394f;border-radius:7px;padding:7px;font-size:10px;color:#96abc0}.weather b{display:block;color:#f2f5f8;font-size:13px;margin-top:3px}.strip{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.pilot .flm,.stat .flm{color:#b06cff;font-style:normal;font-weight:900;font-size:9px;margin-left:4px;letter-spacing:.04em}.pilot{border:1px solid #334b68;border-left:4px solid var(--team);border-radius:7px;background:#111d2e;color:#f2f5f8;font-size:11px;font-weight:900;padding:6px 8px;cursor:pointer}.pilot.active{background:#21344c;box-shadow:0 0 0 1px var(--team) inset}.control{margin-top:10px;border-top:1px solid #26394f;padding-top:9px}.control h4{margin:0 0 6px;font-size:11px;letter-spacing:.08em}.msg{font-size:10px;color:#b7c7d7;border-left:3px solid #ffcc62;padding:5px 7px;margin:5px 0;background:#171a1b}.note{font-size:10px;color:#8299b3;margin-top:8px}
      @media(max-width:860px){.layout{grid-template-columns:1fr}.map canvas{height:365px}}
    </style>
    <div class="hud"><div class="top"><div><div class="title">LIVE RACE CONTROL // OPEN DATA HUD</div><div class="sub" id="sub">Doğrulanmış canlı paket bekleniyor</div></div><div class="signal" id="signal">● VERİ DURUMU</div></div>
      <div class="layout"><div><div class="map"><canvas id="track"></canvas></div><div class="strip" id="strip"></div><div class="note">Araç veya pilot kartına basarak sürücü ayrıntısını seç. Pist çizgisi yalnızca konum paketinden yeterli nokta gelirse çizilir.</div></div><aside class="panel" id="panel"></aside></div>
    </div>
    <script>
      const data=__LIVE_V19_PAYLOAD__,cars=data.cars||[],route=data.track||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d');
      const tyres={SOFT:'#ff4654',MEDIUM:'#ffd23e',HARD:'#f0f4f8',INTERMEDIATE:'#44d97a',WET:'#45a9ff'};let selected=cars[0]?.number||null;
      function all(){return route.length?route:cars}function transform(){const pts=all();if(!pts.length)return null;const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=30,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}
      function xy(p,t){return[((p.x-t.minX)*t.s)+(t.w-(t.maxX-t.minX)*t.s)/2,((t.maxY-p.y)*t.s)+(t.h-(t.maxY-t.minY)*t.s)/2]}
      function f1(x,y,c,label,on){ctx.save();ctx.translate(x,y);ctx.fillStyle='#05090e';ctx.fillRect(-11,-7,5,14);ctx.fillRect(8,-8,4,16);ctx.fillStyle=c;ctx.fillRect(-7,-4,17,8);ctx.fillRect(8,-2,8,4);ctx.fillRect(11,-8,3,16);ctx.fillStyle='#f1f6ff';ctx.fillRect(0,-1,6,2);if(on){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.strokeRect(-13,-10,30,20)}ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(label,x,y-15)}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const t=transform();if(!t){ctx.fillStyle='#9eb0c2';ctx.font='bold 14px Arial';ctx.textAlign='center';ctx.fillText(data.reason||'Canlı konum bekleniyor',w/2,h/2);return}if(route.length>1){ctx.beginPath();route.forEach((p,i)=>{const q=xy(p,t);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.strokeStyle='#8296ae';ctx.globalAlpha=.58;ctx.lineWidth=3;ctx.stroke();ctx.globalAlpha=1}cars.forEach(c=>{const q=xy(c,t);f1(q[0],q[1],c.colour,c.code,c.number===selected)})}
      function panel(){const c=cars.find(item=>item.number===selected)||cars[0],panel=document.getElementById('panel');if(!c){panel.innerHTML='<div class="selected">Canlı veri bekleniyor</div><div class="note">'+(data.reason||'')+'</div>';return}const tyre=String(c.compound||'—').toUpperCase(),tc=tyres[tyre]||'#8190a4',p=c.profile||{},w=data.weather||{};panel.style.setProperty('--team',c.colour);panel.innerHTML='<div class="hero"><img class="portrait" src="'+(p.photo||'')+'" alt="" onerror="this.remove()"><div class="selected">'+(p.name||c.code)+' <span style="font-size:12px;color:#a8b7c9">P'+c.position+'</span></div><div class="team">'+c.team+' · '+c.code+'</div></div><div class="stat"><span>Son tur</span><b>'+c.last_lap+'</b></div><div class="stat"><span>Tur</span><b>'+c.lap+'</b></div><div class="stat"><span>Delta / fark</span><b>'+c.gap+'</b></div><div class="stat"><span>Lastik</span><b><i class="tyre" style="--tyre:'+tc+'">'+tyre.slice(0,1)+'</i> '+tyre+'</b></div><div class="stat"><span>Set başlangıç yaşı</span><b>'+c.tyre_age_start+' tur</b></div><div class="stat"><span>Son pit geçişi</span><b>Tur '+c.last_pit_lap+(c.pit_passage!==null&&c.pit_passage!==undefined?' · '+c.pit_passage.toFixed(1)+' sn':'')+'</b></div><div class="weather"><div>HAVA<b>'+(w.air===null||w.air===undefined?'—':w.air+'°C')+'</b></div><div>PİST<b>'+(w.track===null||w.track===undefined?'—':w.track+'°C')+'</b></div><div>RÜZGAR<b>'+(w.wind===null||w.wind===undefined?'—':w.wind+' km/s')+'</b></div><div>KOŞUL<b>'+(w.rainfall?'YAĞMUR':'KURU / BİLİNMİYOR')+'</b></div></div><div class="control"><h4>RACE CONTROL</h4>'+((data.race_control||[]).map(m=>'<div class="msg">'+(m.time?m.time+' · ':'')+m.text+'</div>').join('')||'<div class="note">Yeni Race Control mesajı yok.</div>')+'</div>'}
      function strip(){document.getElementById('strip').innerHTML=cars.map(c=>'<button class="pilot '+(c.number===selected?'active':'')+'" style="--team:'+c.colour+'" data-n="'+c.number+'">P'+c.position+' · '+c.code+' · T'+c.lap+'</button>').join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.n;draw();panel();strip()})}
      function resize(){const b=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=b.width*d;canvas.height=b.height*d;ctx.setTransform(d,0,0,d,0,0);draw()}canvas.onclick=e=>{const t=transform();if(!t)return;const r=canvas.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let found=null,best=28;cars.forEach(c=>{const q=xy(c,t),d=Math.hypot(q[0]-mx,q[1]-my);if(d<best){best=d;found=c}});if(found){selected=found.number;draw();panel();strip()}};document.getElementById('sub').textContent=(data.session?.meeting_name||'Formula 1')+' · '+(data.session?.session_name||'Aktif seans')+' · '+(data.source||'açık veri');document.getElementById('signal').textContent=data.ok?'● CANLI PAKET AKTİF':'● CANLI PAKET BEKLENİYOR';window.addEventListener('resize',resize);resize();panel();strip();
    </script>
    """.replace('__LIVE_V19_PAYLOAD__', payload)


@st.cache_data(ttl=1800, show_spinner=False)
# =========================================================
# FAZ 2 · #12 — HAVA & PİST EVRİMİ ZAMAN ÇİZELGESİ
# =========================================================

@st.cache_data(ttl=21600, show_spinner=False)
def get_weather_evolution_v42(year, event_name, session_code):
    """Hava (hava/pist sıcaklığı, nem, rüzgâr, yağış) + pist evrimi (temsili tur
    zamanının seans boyunca nasıl düştüğü) — tek FastF1 seans yüklemesinden."""
    try:
        session = fastf1.get_session(int(year), event_name, str(session_code))
        session.load(laps=True, telemetry=False, weather=True, messages=False)
    except Exception as error:
        return {'ok': False, 'reason': str(error)}

    weather = session.weather_data.copy() if getattr(session, 'weather_data', None) is not None else pd.DataFrame()
    laps = session.laps.copy() if session.laps is not None else pd.DataFrame()

    raw_weather, rained = [], False
    if not weather.empty and 'Time' in weather.columns:
        step = max(1, len(weather) // 60)
        for _, row in weather.iloc[::step].iterrows():
            secs = _timedelta_seconds(row.get('Time'))
            if secs is None:
                continue
            rain = bool(row.get('Rainfall', False))
            rained = rained or rain
            raw_weather.append({
                '_s': secs,
                'air': _career_float_v27(row.get('AirTemp')),
                'track': _career_float_v27(row.get('TrackTemp')),
                'hum': _career_float_v27(row.get('Humidity')),
                'wind': _career_float_v27(row.get('WindSpeed')),
                'rain': rain,
            })

    raw_evo = []
    if not laps.empty and 'LapTime' in laps.columns and 'LapStartTime' in laps.columns:
        frame = laps.copy()
        frame['_sec'] = frame['LapTime'].apply(_timedelta_seconds)
        frame['_start'] = frame['LapStartTime'].apply(_timedelta_seconds)
        frame = frame.dropna(subset=['_sec', '_start'])
        if not frame.empty:
            fastest = float(frame['_sec'].min())
            frame = frame[frame['_sec'] <= fastest * 1.08]
            if len(frame) >= 4:
                lo, hi = float(frame['_start'].min()), float(frame['_start'].max())
                buckets = max(6, min(20, int((hi - lo) // 180) or 6))
                edges = np.linspace(lo, hi + 1, buckets + 1)
                for i in range(buckets):
                    chunk = frame[(frame['_start'] >= edges[i]) & (frame['_start'] < edges[i + 1])]
                    if chunk.empty:
                        continue
                    raw_evo.append({
                        '_s': float(edges[i]),
                        'best': round(float(chunk['_sec'].min()), 3),
                        'median': round(float(chunk['_sec'].median()), 3),
                    })

    origin = min([r['_s'] for r in raw_weather] + [r['_s'] for r in raw_evo], default=0.0)
    w_points = [{**{k: v for k, v in r.items() if k != '_s'}, 't': round((r['_s'] - origin) / 60, 1)}
                for r in raw_weather]
    evo = [{'t': round((r['_s'] - origin) / 60, 1), 'best': r['best'], 'median': r['median']}
           for r in raw_evo]

    improvement = round(evo[0]['best'] - evo[-1]['best'], 3) if len(evo) >= 2 else None

    def _mm(key):
        vals = [p[key] for p in w_points if p.get(key) is not None]
        return [round(min(vals), 1), round(max(vals), 1)] if vals else [None, None]

    return {
        'ok': bool(w_points or evo),
        'event': str(session.event['EventName']) if getattr(session, 'event', None) is not None else str(event_name),
        'weather': w_points, 'evolution': evo, 'rained': rained,
        'air_range': _mm('air'), 'track_range': _mm('track'), 'improvement': improvement,
    }


def weather_evolution_html(p):
    """İki panelli HUD: üstte tur zamanı evrimi, altta hava zaman çizelgesi."""
    if not p.get('ok'):
        return ("<div style='padding:20px;color:#8a9bb0;font-family:Saira,sans-serif'>"
                "Bu seans için hava / pist evrimi verisi henüz alınamadı.</div>")
    packed = fp_ui.json_for_script(p)
    return r'''<style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}
.hud{border:1px solid #26313f;border-radius:13px;padding:13px;background:#11161f}
.hd{font:950 13px Inter,Arial,sans-serif;letter-spacing:.09em}
.sub{font:10px Inter,Arial,sans-serif;color:#9fb0c0;margin-top:5px}
.kpis{display:flex;gap:7px;flex-wrap:wrap;margin:11px 0}
.kpi{border:1px solid #2b3a4d;border-radius:8px;background:#141b26;padding:7px 10px;font:800 11px ui-monospace,Consolas,monospace}
.kpi s{display:block;font:700 8px 'Saira Condensed',Inter,sans-serif;letter-spacing:.09em;color:#8496a8;text-decoration:none;margin-bottom:3px}
.kpi b{font-size:13px}
.panel{position:relative;border:1px solid #212b38;border-radius:9px;background:#0c121b;margin-top:8px}
.panel .lab{position:absolute;top:6px;left:10px;font:900 9px Inter,Arial,sans-serif;letter-spacing:.08em;color:#7c90a4;z-index:2}
.panel canvas{width:100%;display:block;cursor:crosshair}
.leg{display:flex;gap:11px;flex-wrap:wrap;margin-top:7px;font:700 9.5px ui-monospace,Consolas,monospace;color:#9fb0c0}
.leg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:middle}
.read{margin-top:8px;font:800 11px ui-monospace,Consolas,monospace;color:#c7d6e6;min-height:15px}
</style>
<div class="hud">
  <div class="hd">HAVA & PIST EVRIMI</div>
  <div class="sub">__EVENT__ · imleci grafiğin üzerinde gezdir</div>
  <div class="kpis" id="kpis"></div>
  <div class="panel"><span class="lab">TUR ZAMANI EVRIMI (sn)</span><canvas id="evo"></canvas></div>
  <div class="leg"><span><i style="background:#45c8ff"></i>en hızlı tur</span><span><i style="background:#5b6b7e"></i>ortalama tur</span></div>
  <div class="panel"><span class="lab">HAVA</span><canvas id="wx"></canvas></div>
  <div class="leg"><span><i style="background:#ff7a45"></i>pist °C</span><span><i style="background:#ffd23f"></i>hava °C</span><span><i style="background:#4ea981"></i>nem %</span><span><i style="background:#3aa9ff"></i>yağış</span></div>
  <div class="read" id="read"></div>
</div>
<script>
"use strict";
(function(){
const D=__PAYLOAD__, EV=D.evolution||[], WX=D.weather||[];
const $=function(s){return document.querySelector(s);};
const evo=$('#evo'), ex=evo.getContext('2d'), wx=$('#wx'), wc=wx.getContext('2d');
let EB=null, WB=null, cursorMin=null;

$('#kpis').innerHTML=[
  D.improvement!=null ? '<div class="kpi"><s>Pist kazancı</s><b>'+(D.improvement>0?'−':'+')+Math.abs(D.improvement).toFixed(2)+' sn</b></div>' : '',
  (D.track_range&&D.track_range[0]!=null) ? '<div class="kpi"><s>Pist °C</s><b>'+D.track_range[0]+'–'+D.track_range[1]+'</b></div>' : '',
  (D.air_range&&D.air_range[0]!=null) ? '<div class="kpi"><s>Hava °C</s><b>'+D.air_range[0]+'–'+D.air_range[1]+'</b></div>' : '',
  '<div class="kpi"><s>Yağış</s><b>'+(D.rained?'VAR':'yok')+'</b></div>',
].join('');

function fit(cv, h){ const r=cv.getBoundingClientRect(), d=Math.min(2,devicePixelRatio||1);
  cv.style.height=h+'px'; cv.width=Math.max(2,r.width*d); cv.height=Math.max(2,h*d);
  cv.getContext('2d').setTransform(d,0,0,d,0,0); return {w:r.width,h:h}; }

function tRange(arr){ if(!arr.length) return [0,1]; let a=1e9,b=-1e9;
  arr.forEach(function(p){ if(p.t<a)a=p.t; if(p.t>b)b=p.t; }); return [a, b>a?b:a+1]; }

function drawEvo(){
  if(!EB) return; const w=EB.w,h=EB.h,pl=44,pr=10,pt=16,pb=20;
  ex.clearRect(0,0,w,h);
  if(EV.length<2){ ex.fillStyle='#6b7d8f';ex.font='700 11px Inter,Arial';ex.textAlign='center';
    ex.fillText('Bu seansta temsili tur zamanı verisi yok.',w/2,h/2); return; }
  const tr=tRange(EV);
  let lo=1e9,hi=-1e9; EV.forEach(function(p){ lo=Math.min(lo,p.best); hi=Math.max(hi,p.median); });
  const pad=(hi-lo)*0.12||0.4; lo-=pad; hi+=pad;
  const X=function(t){ return pl+(t-tr[0])/(tr[1]-tr[0])*(w-pl-pr); };
  const Y=function(v){ return pt+(1-(v-lo)/(hi-lo))*(h-pt-pb); };
  ex.strokeStyle='#1a2330';ex.lineWidth=1;ex.fillStyle='#63748a';ex.font='9px Inter,Arial';ex.textAlign='right';
  for(let k=0;k<=3;k++){ const v=lo+(hi-lo)*k/3, gy=Y(v);
    ex.beginPath();ex.moveTo(pl,gy);ex.lineTo(w-pr,gy);ex.stroke(); ex.fillText(v.toFixed(1),pl-5,gy+3); }
  // median band
  ex.beginPath();
  EV.forEach(function(p,i){ const x=X(p.t),y=Y(p.median); i?ex.lineTo(x,y):ex.moveTo(x,y); });
  ex.strokeStyle='#5b6b7e';ex.lineWidth=1.6;ex.stroke();
  // best line
  ex.beginPath();
  EV.forEach(function(p,i){ const x=X(p.t),y=Y(p.best); i?ex.lineTo(x,y):ex.moveTo(x,y); });
  ex.strokeStyle='#45c8ff';ex.lineWidth=2;ex.stroke();
  EV.forEach(function(p){ const x=X(p.t),y=Y(p.best); ex.fillStyle='#45c8ff';ex.beginPath();ex.arc(x,y,2.4,0,7);ex.fill(); });
  cursorLine(ex,X,tr,h,pt,pb,w,pl,pr);
}
function drawWx(){
  if(!WB) return; const w=WB.w,h=WB.h,pl=34,pr=32,pt=16,pb=20;
  wc.clearRect(0,0,w,h);
  if(WX.length<2){ wc.fillStyle='#6b7d8f';wc.font='700 11px Inter,Arial';wc.textAlign='center';
    wc.fillText('Bu seans için hava verisi yok.',w/2,h/2); return; }
  const tr=tRange(WX);
  const X=function(t){ return pl+(t-tr[0])/(tr[1]-tr[0])*(w-pl-pr); };
  // rain shading
  wc.fillStyle='rgba(58,169,255,.16)';
  for(let i=1;i<WX.length;i++){ if(WX[i].rain){ wc.fillRect(X(WX[i-1].t),pt,X(WX[i].t)-X(WX[i-1].t),h-pt-pb); } }
  const series=function(key,col,mn,mx){
    const vals=WX.map(function(p){return p[key];}).filter(function(v){return v!=null;});
    if(!vals.length) return;
    const a=mn!=null?mn:Math.min.apply(null,vals), b=mx!=null?mx:Math.max.apply(null,vals);
    const Y=function(v){ return pt+(1-(v-a)/((b-a)||1))*(h-pt-pb); };
    wc.beginPath(); let started=false;
    WX.forEach(function(p){ if(p[key]==null){return;} const x=X(p.t),y=Y(p[key]);
      started?wc.lineTo(x,y):wc.moveTo(x,y); started=true; });
    wc.strokeStyle=col;wc.lineWidth=1.7;wc.stroke();
  };
  let tlo=1e9,thi=-1e9;
  WX.forEach(function(p){ [p.air,p.track].forEach(function(v){ if(v!=null){ tlo=Math.min(tlo,v);thi=Math.max(thi,v);} }); });
  series('track','#ff7a45',tlo-1,thi+1);
  series('air','#ffd23f',tlo-1,thi+1);
  series('hum','#4ea981',0,100);
  wc.fillStyle='#63748a';wc.font='9px Inter,Arial';wc.textAlign='right';
  wc.fillText(Math.round(thi)+'°',pl-4,pt+8); wc.fillText(Math.round(tlo)+'°',pl-4,h-pb);
  wc.textAlign='left'; wc.fillText('100%',w-pr+4,pt+8); wc.fillText('0%',w-pr+4,h-pb);
  for(let k=0;k<=4;k++){ const t=tr[0]+(tr[1]-tr[0])*k/4;
    wc.fillStyle='#63748a';wc.textAlign='center';wc.fillText(Math.round(t)+'dk',X(t),h-6); }
  cursorLine(wc,X,tr,h,pt,pb,w,pl,pr);
}
function cursorLine(ctx,X,tr,h,pt,pb){
  if(cursorMin==null) return; const x=X(cursorMin);
  ctx.strokeStyle='rgba(255,255,255,.5)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,pt);ctx.lineTo(x,h-pb);ctx.stroke();
}
function nearest(arr,t){ let best=arr[0],bd=1e9; arr.forEach(function(p){ const d=Math.abs(p.t-t); if(d<bd){bd=d;best=p;} }); return best; }
function readout(){
  const el=$('#read'); if(cursorMin==null){ el.textContent=''; return; }
  const parts=['@ '+cursorMin.toFixed(0)+' dk'];
  if(EV.length){ const e=nearest(EV,cursorMin); parts.push('en hızlı '+e.best.toFixed(3)+' sn'); }
  if(WX.length){ const wxp=nearest(WX,cursorMin);
    if(wxp.track!=null) parts.push('pist '+wxp.track.toFixed(1)+'°');
    if(wxp.air!=null) parts.push('hava '+wxp.air.toFixed(1)+'°');
    if(wxp.rain) parts.push('YAĞIŞ'); }
  el.textContent=parts.join('  ·  ');
}
function allT(){ return (EV.concat(WX)).map(function(p){return p.t;}); }
function bounds(){ const t=allT(); return t.length?[Math.min.apply(null,t),Math.max.apply(null,t)]:[0,1]; }
function hover(e,cv){ const r=cv.getBoundingClientRect(); const b=bounds();
  const pl=cv.id==='evo'?44:34, pr=cv.id==='evo'?10:32;
  const f=Math.max(0,Math.min(1,(e.clientX-r.left-pl)/(r.width-pl-pr)));
  cursorMin=b[0]+f*(b[1]-b[0]); drawEvo(); drawWx(); readout(); }
[evo,wx].forEach(function(cv){ cv.addEventListener('pointermove',function(e){ hover(e,cv); }); });
function fitAll(){ EB=fit(evo,168); WB=fit(wx,150); drawEvo(); drawWx(); }
let rz=0; window.addEventListener('resize',function(){ clearTimeout(rz); rz=setTimeout(fitAll,120); });
fitAll(); setTimeout(fitAll,60);
})();
</script>'''.replace('__PAYLOAD__', packed).replace('__EVENT__', html_lib.escape(str(p.get('event', ''))))


def get_race_intelligence_v19(year, event_name):
    """Tamamlanmış yarışın hava, pit-lane, lastik ve Race Control özetini FastF1'den çıkarır."""
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=False, weather=True, messages=True)
        laps = session.laps.copy() if session.laps is not None else pd.DataFrame()
        weather = session.weather_data.copy() if getattr(session, 'weather_data', None) is not None else pd.DataFrame()
        weather_summary = {}
        weather_timeline = []
        if not weather.empty:
            numeric_columns = {'air': 'AirTemp', 'track': 'TrackTemp', 'humidity': 'Humidity', 'wind': 'WindSpeed', 'rainfall': 'Rainfall'}
            for output, column in numeric_columns.items():
                if column in weather.columns:
                    series = pd.to_numeric(weather[column], errors='coerce').dropna()
                    if not series.empty:
                        weather_summary[output] = round(float(series.mean()), 1)
            for _, row in weather.iloc[::max(1, len(weather) // 12)].iterrows():
                timestamp = _timedelta_seconds(row.get('Time'))
                weather_timeline.append({
                    'time': format_time(pd.to_timedelta(timestamp, unit='s')) if timestamp is not None else '—',
                    'air': pd.to_numeric(row.get('AirTemp'), errors='coerce'),
                    'track': pd.to_numeric(row.get('TrackTemp'), errors='coerce'),
                    'rain': bool(row.get('Rainfall', False)),
                })

        pits = []
        if not laps.empty:
            for driver, driver_laps in laps.groupby('Driver'):
                pending_entry = None
                for _, lap in driver_laps.iterrows():
                    pit_in = _timedelta_seconds(lap.get('PitInTime'))
                    pit_out = _timedelta_seconds(lap.get('PitOutTime'))
                    lap_number = int(lap.get('LapNumber', 0)) if pd.notna(lap.get('LapNumber')) else '—'
                    compound = str(lap.get('Compound', '—')).upper()
                    # FastF1 pit-in ve pit-out değerlerini çoğunlukla ardışık tur
                    # satırlarında tutar. İkisini eşleyerek lane-time varsa gösteririz.
                    if pit_in is not None:
                        pending_entry = {
                            'driver': str(driver), 'lap': lap_number,
                            'pit_in': pit_in, 'compound': compound,
                        }
                    if pit_out is not None:
                        entry = pending_entry or {
                            'driver': str(driver), 'lap': lap_number,
                            'pit_in': None, 'compound': compound,
                        }
                        lane_time = pit_out - entry['pit_in'] if entry['pit_in'] is not None and pit_out >= entry['pit_in'] else None
                        pits.append({
                            'driver': entry['driver'], 'lap': lap_number,
                            'lane_time': round(lane_time, 1) if lane_time is not None else None,
                            'compound': compound,
                        })
                        pending_entry = None
                if pending_entry is not None:
                    pits.append({
                        'driver': pending_entry['driver'], 'lap': pending_entry['lap'],
                        'lane_time': None, 'compound': pending_entry['compound'],
                    })
        pits = pits[-30:]

        speed_trap = None
        if not laps.empty and 'SpeedST' in laps.columns:
            trap_laps = laps[['Driver', 'SpeedST']].copy()
            trap_laps['SpeedST'] = pd.to_numeric(trap_laps['SpeedST'], errors='coerce')
            trap_laps = trap_laps.dropna(subset=['SpeedST'])
            if not trap_laps.empty:
                best = trap_laps.loc[trap_laps['SpeedST'].idxmax()]
                speed_trap = {'driver': str(best['Driver']), 'speed': round(float(best['SpeedST']), 1)}

        raw_messages = getattr(session, 'race_control_messages', pd.DataFrame())
        control = []
        if isinstance(raw_messages, pd.DataFrame) and not raw_messages.empty:
            column = next((name for name in ['Message', 'Text'] if name in raw_messages.columns), None)
            if column:
                seen = set()
                for _, row in raw_messages.iloc[::-1].iterrows():
                    translated = translate_race_control_message(row.get(column, ''))
                    if translated and translated not in seen:
                        seen.add(translated)
                        control.append({
                            'time': format_time(row.get('Time')) if pd.notnull(row.get('Time')) else '',
                            'text': translated,
                        })
                    if len(control) >= 6:
                        break
                control.reverse()

        return {
            'ok': True,
            'weather': weather_summary,
            'weather_timeline': weather_timeline,
            'pits': pits,
            'speed_trap': speed_trap,
            'race_control': control,
            'pit_note': 'Gösterilen süre, FastF1’de pit girişi ve pit çıkışı eşleşirse hesaplanan pit-lane zaman aralığıdır; sabit servis süresi değildir.',
        }
    except Exception as error:
        return {'ok': False, 'reason': f'Yarış istihbaratı henüz alınamadı: {error}'}


def race_intelligence_hud_html_v19(info):
    """Yarış tekrarının altına gelen hafif, kaydırılabilir gerçek veri HUD'u."""
    if not info.get('ok'):
        return f"<div class='hud-card'>Yarış istihbaratı alınamadı: {html_lib.escape(info.get('reason', 'bilinmeyen hata'))}</div>"
    weather = info.get('weather', {})
    trap = info.get('speed_trap') or {}
    tiles = [
        ('ORT. HAVA', f"{weather.get('air', '—')}°C" if weather.get('air') is not None else '—'),
        ('ORT. PİST', f"{weather.get('track', '—')}°C" if weather.get('track') is not None else '—'),
        ('RÜZGAR', f"{weather.get('wind', '—')} km/s" if weather.get('wind') is not None else '—'),
        ('SPEED TRAP', f"{trap.get('driver', '—')} · {trap.get('speed', '—')} km/h" if trap else 'Veri yok'),
    ]
    tile_html = ''.join(
        f"<div class='tile'><small>{html_lib.escape(label)}</small><b>{html_lib.escape(str(value))}</b></div>"
        for label, value in tiles
    )
    control_html = ''.join(
        f"<div class='msg'><b>{html_lib.escape(str(item.get('time', '')))}</b> {html_lib.escape(str(item.get('text', '')))}</div>"
        for item in info.get('race_control', [])
    ) or "<div class='muted'>Doğrulanmış Race Control mesajı yok.</div>"
    pit_html = ''.join(
        f"<div class='pit'><b>{html_lib.escape(str(item['driver']))}</b><span>T{html_lib.escape(str(item['lap']))}</span><span>{html_lib.escape(str(item['compound']))}</span><span>{'≈ ' + str(item['lane_time']) + ' sn' if item.get('lane_time') is not None else 'pit geçişi'}</span></div>"
        for item in info.get('pits', [])[-12:]
    ) or "<div class='muted'>Pit geçişi verisi yok.</div>"
    timeline_html = ''.join(
        f"<span>{html_lib.escape(str(item['time']))} · {item['air'] if pd.notna(item.get('air')) else '—'}°C / {item['track'] if pd.notna(item.get('track')) else '—'}°C{' · yağmur' if item.get('rain') else ''}</span>"
        for item in info.get('weather_timeline', [])
    ) or "<span>Hava zaman çizelgesi yok.</span>"
    return f"""
    <style>
      body{{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}}.hud{{border:1px solid #2c425c;border-radius:13px;background:#11161f;padding:13px}}.head{{font-size:13px;font-weight:950;letter-spacing:.09em}}.sub{{font-size:10px;color:#91a9c0;margin-top:5px}}.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:11px}}.tile{{border:1px solid #2a405a;border-radius:8px;background:#0d1724;padding:9px}}.tile small{{display:block;color:#8ca3bb;font-weight:800;font-size:10px}}.tile b{{display:block;color:#f4f8ff;margin-top:5px;font-size:13px}}.grid{{display:grid;grid-template-columns:1.1fr .9fr;gap:12px;margin-top:12px}}.box{{border:1px solid #293e56;border-radius:9px;background:#0d1623;padding:10px;min-height:135px}}.box h4{{margin:0 0 8px;font-size:11px;letter-spacing:.08em}}.msg{{border-left:3px solid #ffd168;padding:6px 7px;background:#19191a;margin:5px 0;font-size:11px;line-height:1.35}}.msg b{{color:#ffd168;margin-right:5px}}.pit{{display:grid;grid-template-columns:1fr 42px 72px 88px;gap:5px;border-top:1px solid #26394e;padding:7px 0;font-size:11px}}.pit b{{color:#f2f5f8}}.pit span{{color:#a9bbcf}}.timeline{{display:flex;gap:7px;overflow:auto;padding-top:8px}}.timeline span{{white-space:nowrap;border:1px solid #2c4059;background:#0c1420;padding:6px 8px;border-radius:6px;font-size:10px;color:#aec1d4}}.muted{{font-size:11px;color:#8da2b8}}@media(max-width:440px){{.tiles{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}.pit{{grid-template-columns:1fr 38px 58px 76px}}}}
    </style>
    <div class='hud'><div class='head'>RACE INTELLIGENCE // VERIFIED DATA</div><div class='sub'>HAVA · RESMÎ SPEED TRAP · PİT-LANE GEÇİŞİ · FIA RACE CONTROL</div><div class='tiles'>{tile_html}</div><div class='timeline'>{timeline_html}</div><div class='grid'><div class='box'><h4>FIA RACE CONTROL — TÜRKÇE</h4>{control_html}</div><div class='box'><h4>PIT / LASTİK OLAYLARI</h4>{pit_html}<div class='muted' style='margin-top:8px'>{html_lib.escape(info.get('pit_note', ''))}</div></div></div></div>
    """


def _normalise_question_v19(value):
    text = str(value or '').lower().strip()
    replacements = str.maketrans({'ı': 'i', 'İ': 'i', 'ş': 's', 'Ş': 's', 'ğ': 'g', 'Ğ': 'g', 'ü': 'u', 'Ü': 'u', 'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c'})
    return text.translate(replacements)


def _driver_from_question_v19(question):
    clean = _normalise_question_v19(question)
    for details in TEAM_DIRECTORY_2026.values():
        for name, code, *_ in details.get('drivers', []):
            full = _normalise_question_v19(name)
            surname = full.split()[-1] if full else ''
            if f' {code.lower()} ' in f' {clean} ' or (surname and surname in clean):
                return code
    return None


@st.cache_data(ttl=300, show_spinner=False)
def get_paddock_context_v19(year):
    """Asistanın yalnızca doğrulanmış uygulama verisine yaslanacağı küçük bağlam paketi."""
    latest = get_latest_completed_session(int(year))
    if not latest:
        return {'ok': False, 'reason': 'Tamamlanmış bir seans bulunamadı.'}
    table, _ = get_session_results_table(latest['year'], latest['event_name'], latest['session_code'])
    story = get_session_story(latest['year'], latest['event_name'], latest['session_code'])
    return {
        'ok': not table.empty,
        'latest': latest,
        'rows': table.fillna('—').to_dict('records') if not table.empty else [],
        'story': story,
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_assistant_session_context_v20(year, event_name, session_code):
    """Asistanın pole/galip sorusunda doğru seans tablosunu kullanır."""
    table, _ = get_session_results_table(int(year), event_name, session_code)
    if table is None or table.empty:
        return []
    return table.fillna('—').to_dict('records')


def local_f1_history_answer_v20(question):
    """İnternet veya AI anahtarı olmadan verilebilecek birkaç kesin tarihsel yanıt."""
    text = _normalise_question_v19(question)
    if '6 sampiyon' in text or '6 kez sampiyon' in text:
        return (
            'F1 tarihinde altı kez dünya şampiyonu olan pilot yok. Rekor yedi şampiyonlukla '
            'Lewis Hamilton ve Michael Schumacher’e ait; Juan Manuel Fangio beş kez şampiyon oldu.'
        )
    if 'hamilton' in text and any(word in text for word in ['sampiyon', 'sampiyonluk']):
        return 'Lewis Hamilton yedi kez Formula 1 dünya şampiyonu oldu.'
    if 'schumacher' in text and any(word in text for word in ['sampiyon', 'sampiyonluk']):
        return 'Michael Schumacher yedi kez Formula 1 dünya şampiyonu oldu.'
    return ''


def configured_openai_api_key():
    """Anahtar yoksa uygulama tamamen yerel/FastF1 veri asistanı olarak kalır."""
    value = os.getenv('OPENAI_API_KEY', '').strip()
    if value:
        return value
    try:
        return str(st.secrets.get('OPENAI_API_KEY', '')).strip()
    except Exception:
        return ''


def extract_response_text(response_body):
    """Responses API'nin ham JSON çıktısından yalnızca görünen metni alır."""
    texts = []
    for item in response_body.get('output', []) if isinstance(response_body, dict) else []:
        if item.get('type') != 'message':
            continue
        for content in item.get('content', []):
            if content.get('type') == 'output_text' and content.get('text'):
                texts.append(str(content['text']))
    return '\n'.join(texts).strip()


def openai_paddock_reply(question, context):
    """İsteğe bağlı gerçek ChatGPT katmanı. Yarış sonuçlarında FastF1 her zaman önceliklidir."""
    api_key = configured_openai_api_key()
    if not api_key:
        return None, 'OpenAI anahtarı bağlı değil'
    latest = context.get('latest', {})
    rows = context.get('rows', [])[:24]
    verified_context = {
        'last_completed_session': {
            'event': latest.get('event_name'),
            'session': latest.get('display_name'),
            'year': latest.get('year'),
        },
        'verified_results': rows,
        'session_story': context.get('story', []),
    }
    instructions = (
        'Sen Formula Paddock uygulamasinin Turkce asistanisin. Kisa, samimi ve net yanit ver. '
        'Verilen DOGGRULANMIS UYGULAMA VERISI yarıs sonuclari icin tek gercek kaynaktir; '
        'veride olmayan sonuc, kaza, puan veya zaman uydurma. Sonuc sorulursa "14. sirada bitirdi" '
        'gibi tam cumle kullan. Genel F1 sorularini yararli sekilde yanitla; emin degilsen bunu acikca soyle. '
        'Girdi veri:\n' + json.dumps(verified_context, ensure_ascii=False)
    )
    payload = json.dumps({
        'model': os.getenv('PADDOCK_OPENAI_MODEL', 'gpt-4.1-mini'),
        'instructions': instructions,
        'input': question,
        'max_output_tokens': 360,
    }).encode('utf-8')
    request = urllib.request.Request(
        'https://api.openai.com/v1/responses',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = json.loads(response.read().decode('utf-8'))
        answer = extract_response_text(body)
        return (answer or None), 'OpenAI Responses API'
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        log_data_error('paddock openai response', error)
        return None, 'OpenAI yanıtı şu an alınamadı'


def paddock_assistant_answer_v19(question, year=None):
    """Ücretsiz, veriye dayalı asistan. Bilmediği şeyi tahmin etmez."""
    current_year = int(year or datetime.datetime.now(datetime.timezone.utc).year)
    context = get_paddock_context_v19(current_year)
    if not context.get('ok'):
        return {'title': 'Veri bekleniyor', 'answer': context.get('reason', 'Bu soru için doğrulanmış sonuç bulunamadı.'), 'source': 'FastF1 sonuç paketi'}
    latest = context['latest']
    rows = context['rows']
    text = _normalise_question_v19(question)
    driver = _driver_from_question_v19(question)
    row = next((item for item in rows if str(item.get('Pilot', '')).upper() == str(driver or '').upper()), None)
    source = f"{latest['event_name']} · {latest['display_name']} · FastF1"

    if 'pole' in text:
        qualifying_rows = get_assistant_session_context_v20(latest['year'], latest['event_name'], 'Q')
        if qualifying_rows:
            leader = qualifying_rows[0]
            return {
                'title': 'Pole pozisyonu',
                'answer': f"{leader.get('Pilot', '—')}, {latest['event_name']} sıralama seansında pole pozisyonunu aldı.",
                'source': f"{latest['event_name']} · Sıralama · FastF1",
            }
        return {
            'title': 'Pole verisi yok',
            'answer': 'Bu hafta sonu için doğrulanmış sıralama sonucu henüz bulunamadı.',
            'source': 'FastF1 sonuç paketi',
        }
    if any(token in text for token in ['kim kazandi', 'yarisi kim kazandi', 'galip', 'kazanan', 'zafer']):
        race_rows = get_assistant_session_context_v20(latest['year'], latest['event_name'], 'R')
        if race_rows:
            winner = race_rows[0]
            return {
                'title': 'Yarış galibi',
                'answer': f"{winner.get('Pilot', '—')}, {latest['event_name']} yarışını kazandı.",
                'source': f"{latest['event_name']} · Yarış · FastF1",
            }
        return {
            'title': 'Yarış sonucu yok',
            'answer': 'Bu hafta sonu için doğrulanmış yarış sonucu henüz bulunamadı.',
            'source': 'FastF1 sonuç paketi',
        }
    if any(token in text for token in ['ilk kim', 'lider kim']):
        leader = rows[0] if rows else {}
        return {
            'title': 'Son seans lideri',
            'answer': f"{leader.get('Pilot', '—')} {clean_position_value(leader.get('Sıra'))} ile son tamamlanan seansın lideri.",
            'source': source,
        }
    if driver and row and any(token in text for token in ['kacinci', 'sira', 'siralama', 'nerede bitir', 'pozisyon']):
        position = format_finish_position(row.get('Sıra'))
        if str(latest.get('session_code', '')).upper() == 'R':
            sentence = f"{driver}, {latest['event_name']} yarışını {position} bitirdi."
        else:
            sentence = f"{driver}, {latest['display_name']} seansını {position} tamamladı."
        return {'title': f"{driver} sonucu", 'answer': sentence, 'source': source}
    if driver and row and any(token in text for token in ['lastik', 'hamur']):
        tyre = row.get('Lastik')
        if tyre and tyre != '—':
            return {'title': f"{driver} lastiği", 'answer': f"{driver} için sonuç tablosunda görünen en hızlı tur lastiği: {tyre}.", 'source': source}
        return {'title': f"{driver} lastiği", 'answer': 'Bu seansın sonuç tablosunda doğrulanmış lastik hamuru yok.', 'source': source}
    if any(token in text for token in ['ne oldu', 'olay', 'race control', 'kaza', 'spin']):
        story = [str(item.get('text', '')) for item in context.get('story', [])]
        reply = ' '.join(story) if story else 'Bu seans için doğrulanmış dikkat çeken olay notu bulunamadı.'
        return {'title': 'Seans özeti', 'answer': reply, 'source': source}
    local_answer = local_f1_history_answer_v20(question)
    if local_answer:
        return {'title': 'F1 tarih bilgisi', 'answer': local_answer, 'source': 'Yerel doğrulanmış F1 tarih bilgisi'}
    if not driver:
        ai_answer, ai_source = openai_paddock_reply(question, context)
        if ai_answer:
            return {'title': 'Paddock AI', 'answer': ai_answer, 'source': ai_source}
        if configured_openai_api_key():
            return {'title': 'Paddock AI', 'answer': 'AI yanıtı şu an alınamadı. Sonuç sorularında FastF1 verisi çalışmaya devam ediyor.', 'source': ai_source}
    direct_tokens = ['kacinci', 'sira', 'siralama', 'nerede bitir', 'pozisyon', 'lastik', 'hamur']
    if driver and row and not any(token in text for token in direct_tokens):
        ai_answer, ai_source = openai_paddock_reply(question, context)
        if ai_answer:
            return {'title': 'Paddock AI', 'answer': ai_answer, 'source': ai_source}
    if driver and row:
        return {'title': f"{driver} — son seans", 'answer': f"{driver}: {clean_position_value(row.get('Sıra'))}. İstersen “{driver} kaçıncı oldu?”, “{driver} lastiği neydi?” veya “son seansta ne oldu?” diye sor.", 'source': source}
    return {'title': 'Paddock Veri Asistanı', 'answer': 'Şu an son tamamlanan seansın doğrulanmış sonuçlarını okuyabiliyorum. Örnek: “Alonso kaçıncı oldu?”, “Pole kim?”, “Son seansta ne oldu?”', 'source': source}


def stewarlde_drivers():
    """Resmî 2026 gridinden türeyen, internet gerektirmeyen oyun havuzu."""
    rows = []
    for team, details in TEAM_DIRECTORY_2026.items():
        for name, code, number, old_image_path in details['drivers']:
            nation, debut, titles = STEWARDLE_META.get(code, ('—', None, None))
            rows.append({
                'name': name, 'code': code, 'team': team, 'number': str(number).replace('#', ''),
                'nation': nation, 'debut': debut, 'titles': titles,
                'age': driver_age(code),
                'photo': current_driver_portrait(team, old_image_path),
            })
    return sorted(rows, key=lambda item: item['name'])


@st.cache_data(ttl=86400, show_spinner=False)
def get_official_team_logo(team_slug):
    """F1 takım sayfasından resmî logo adresini bulur; bulunamazsa kart metni kalır."""
    try:
        url = f"https://www.formula1.com/en/teams/{team_slug}"
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=6) as response:
            page = response.read().decode('utf-8', errors='ignore')
        candidates = re.findall(r'https?[^"\\\\ ]+?logo[^"\\\\ ]+?\\.webp', page, flags=re.IGNORECASE)
        if candidates:
            logo_url = candidates[0].replace('\\u002F', '/').replace('\\/', '/')
            return safe_external_url(logo_url, {'formula1.com', 'media.formula1.com'})
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log_data_error('team logo lookup', error)
    return ''


@st.cache_data(ttl=900, show_spinner=False)
def get_track_outline(year, event_name):
    """Tamamlanmış seansın telemetrisinden basit pist çizimi üretir."""
    for search_year in [int(year), int(year) - 1]:
        for code in ['Q', 'R', 'FP3', 'FP2', 'FP1']:
            try:
                session = fastf1.get_session(search_year, event_name, code)
                session.load(telemetry=False, weather=False, messages=False)
                driver = session.laps['Driver'].dropna().iloc[0]
                lap = session.laps.pick_drivers(driver).pick_fastest()
                telemetry = lap.get_telemetry()
                return telemetry[['X', 'Y']].dropna().to_dict('list')
            except Exception:
                continue
    return {}


def _timedelta_seconds(value):
    """FastF1 zamanını güvenli biçimde saniyeye çevirir."""
    try:
        if value is None or pd.isna(value):
            return None
        return float(pd.to_timedelta(value).total_seconds())
    except Exception:
        return None


def _race_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def race_driver_profile(driver_code, team_name):
    """Yarış HUD'unda kullanılacak güncel isim, numara, bayrak ve portre bilgisi."""
    for directory_team, details in TEAM_DIRECTORY_2026.items():
        for name, code, number, image_path in details.get('drivers', []):
            if code == driver_code:
                flag = DRIVER_DISPLAY.get(code, ('un', name))[0]
                return {
                    'name': name, 'number': number, 'flag': flag,
                    'photo': current_driver_portrait(directory_team, image_path), 'age': driver_age(code),
                }
    flag = DRIVER_DISPLAY.get(driver_code, ('un', driver_code))[0]
    return {'name': driver_code, 'number': '—', 'flag': flag, 'photo': '', 'age': '—'}


def _race_position(value):
    position = _race_int(value)
    return position if position is not None and 1 <= position <= 30 else None


@st.cache_data(ttl=604800, show_spinner=False)
def _build_stable_race_replay_payload_v36(year, event_name):
    """FastF1-yalnız yedek kurucu (v2.5). Dışarıdan `build_stable_race_replay_payload`
    (aşağıda, OpenF1 önce) çağrılır; bu, o fonksiyonun FastF1 yedeğidir.

    Tek ortak SessionTime saatiyle doğrulanmış, akıcı yarış tekrar paketi.

    FastF1'in `Time`, `LapStartTime`, `PitInTime` ve `PitOutTime` alanları aynı
    seans saatini kullanır. Bu nedenle tüm araçların saati ayrı ayrı sıfırlanmaz.
    Böylece farklar, tur içi yakalamalar ve pit süreleri korunur.
    """
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=True, weather=False, messages=False)
        if session.results is None or session.results.empty or session.laps is None or session.laps.empty:
            return {'ok': False, 'reason': 'Bu yarış için doğrulanmış sonuç ve tur verisi henüz hazır değil.'}

        reference_lap, reference_telemetry = None, None
        candidates = session.laps.dropna(subset=['LapTime']).sort_values('LapTime')
        for _, candidate in candidates.iterrows():
            try:
                telemetry = candidate.get_telemetry()
                required = {'Distance', 'X', 'Y'}
                if telemetry is not None and required.issubset(telemetry.columns) and len(telemetry) >= 40:
                    reference_lap, reference_telemetry = candidate, telemetry
                    break
            except Exception:
                continue
        if reference_lap is None:
            return {'ok': False, 'reason': 'Bu yarış için temiz pist telemetrisi bulunamadı.'}

        source = reference_telemetry[['Distance', 'X', 'Y']].dropna().copy()
        source = source.apply(pd.to_numeric, errors='coerce').dropna().sort_values('Distance').drop_duplicates('Distance')
        if len(source) < 40:
            return {'ok': False, 'reason': 'Pist çizimi için yeterli telemetri noktası yok.'}
        # GPS aykırı sıçramaları (bir kare ışınlanan nokta) pisti bozuk çizdiriyordu:
        # ardışık adım uzunluğunun medyanına göre aşırı büyük sıçramaları at.
        xy = source[['X', 'Y']].to_numpy(dtype=float)
        if len(xy) > 8:
            step = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
            typical = float(np.median(step[step > 0])) if np.any(step > 0) else 0.0
            if typical > 0:
                keep = np.concatenate(([True], step <= typical * 6.0))
                if keep.sum() >= max(40, int(len(xy) * 0.6)):
                    source = source[keep]
        distances = np.linspace(float(source['Distance'].min()), float(source['Distance'].max()), 720)
        xs = np.interp(distances, source['Distance'], source['X'])
        ys = np.interp(distances, source['Distance'], source['Y'])
        # başlangıç ve bitiş noktasını birleştirerek kapalı bir tur elde et
        xs[-1], ys[-1] = xs[0], ys[0]
        track = [[round(float(px), 1), round(float(py), 1)] for px, py in zip(xs, ys)]

        prepared, all_starts, total_laps = [], [], 0
        for _, result in session.results.iterrows():
            code = str(result.get('Abbreviation', '')).strip()
            if not code or code.lower() == 'nan':
                continue
            laps = []
            for _, raw in session.laps.pick_drivers(code).sort_values('LapNumber').iterrows():
                lap_number = _race_int(raw.get('LapNumber'))
                duration = _timedelta_seconds(raw.get('LapTime'))
                end = _timedelta_seconds(raw.get('Time'))
                start = _timedelta_seconds(raw.get('LapStartTime'))
                if start is None and end is not None and duration is not None:
                    start = end - duration
                if lap_number is None or duration is None or duration <= 0 or start is None or end is None or end <= start:
                    continue
                pit_in = _timedelta_seconds(raw.get('PitInTime'))
                pit_out = _timedelta_seconds(raw.get('PitOutTime'))
                laps.append({
                    'lap': int(lap_number), 'start_abs': float(start), 'end_abs': float(end),
                    'position': _race_position(raw.get('Position')),
                    'compound': str(raw.get('Compound', '')).upper(),
                    'stint': int(raw.get('Stint', 0)) if pd.notna(raw.get('Stint')) else 0,
                    'pit_in_abs': pit_in, 'pit_out_abs': pit_out,
                })
                if lap_number <= 1:
                    all_starts.append(float(start))
            if laps:
                prepared.append({'code': code, 'team': str(result.get('TeamName', 'Team')), 'result': result, 'laps': laps})
                total_laps = max(total_laps, laps[-1]['lap'])
        if not prepared or not all_starts:
            return {'ok': False, 'reason': 'Yarışın ortak zaman çizelgesi oluşturulamadı.'}

        race_start = min(all_starts)
        cars = []
        for item in prepared:
            previous_position = _race_position(item['result'].get('GridPosition')) or 20
            timeline, pit_events, pending_in = [], [], None
            for raw in item['laps']:
                start, end = raw['start_abs'] - race_start, raw['end_abs'] - race_start
                if end <= 0:
                    continue
                start, end = max(0.0, start), max(0.01, end)
                position = raw['position'] or previous_position
                pit_in = raw['pit_in_abs'] - race_start if raw['pit_in_abs'] is not None else None
                pit_out = raw['pit_out_abs'] - race_start if raw['pit_out_abs'] is not None else None
                if pit_in is not None and 0 <= pit_in <= end + 15:
                    pending_in = max(start, pit_in)
                if pit_out is not None and 0 <= pit_out <= end + 20:
                    event_start = pending_in if pending_in is not None else max(start, pit_out - 9.0)
                    if pit_out > event_start:
                        pit_events.append({'start': round(event_start, 3), 'end': round(pit_out, 3), 'lap': int(raw['lap'])})
                    pending_in = None
                timeline.append({'lap': int(raw['lap']), 'start': round(start, 3), 'end': round(end, 3),
                                 'position': position, 'start_position': previous_position,
                                 'compound': raw['compound'], 'stint': raw['stint']})
                previous_position = position
            if pending_in is not None and timeline:
                pit_events.append({'start': round(pending_in, 3), 'end': round(min(timeline[-1]['end'], pending_in + 9.0), 3), 'lap': timeline[-1]['lap']})
            if not timeline:
                continue
            result = item['result']
            status = str(result.get('Status', 'Finished')).strip()
            finished_like = not is_dnf_status(status)
            visual = _replay_driver_visual_v34(result, item['team'], year)
            cars.append({'code': item['code'], 'team': item['team'], 'colour': visual['colour'],
                         'accent': TEAM_LIVERY_ACCENTS.get(item['team'], visual['colour']),
                         'profile': visual,
                         'grid': _race_position(result.get('GridPosition')),
                         'final_position': _race_position(result.get('Position')),
                         'status': status, 'retired': bool(not finished_like and len(timeline) < total_laps),
                         'laps': timeline,
                         'pit_events': pit_events})
        if not cars:
            return {'ok': False, 'reason': 'Geçerli araç zaman çizelgesi bulunamadı.'}
        cars.sort(key=lambda car: car['final_position'] if car['final_position'] is not None else 99)

        # Tüm araçlar için ortak yarış saati bu noktada kesinleşir. Bu değer
        # eski bir kurucu fonksiyonda kaldığında bazı yarış tekrarları hiç
        # açılamıyordu (validated_seconds tanımsızdı).
        validated_seconds = round(
            max(
                lap['end']
                for car in cars
                for lap in car['laps']
            ),
            2,
        )
        if validated_seconds <= 0:
            return {'ok': False, 'reason': 'Yarış tekrarının ortak süresi doğrulanamadı.'}
        return {'ok': True, 'event': str(session.event.get('EventName', event_name)), 'track': track,
                'overlay': build_track_overlay(reference_telemetry, reference_lap, session), 'cars': cars,
                'total_laps': total_laps, 'total_seconds': validated_seconds,
                'replay_source': 'FastF1 ortak seans saati, tur, sıra, pit giriş/çıkış ve lastik verisi',
                'version': 'beta-1.3'}
    except Exception as error:
        log_data_error('stable race replay', error)
        return {'ok': False, 'reason': f'Yarış tekrar paketi hazırlanamadı: {error}'}


# Shared replay HUD: portrait, tyre history, pits and track-mode overlays.

def _pit_move_notes_v37(payload):
    """Strateji duvarı alt satırı — hepsi kayıtlı veriden, spekülasyon yok:
    gün sonucu (grid→finiş), stop sayısı + ilk pit turu ve pit yolu süresi,
    ve (payload olay akışında tespit edildiyse) undercut/overcut hamlesi."""
    events = payload.get('events') or []
    notes = {}
    for car in payload.get('cars', []):
        code = car.get('code', '')
        pits = car.get('pit_events', []) or []
        laps = car.get('laps', []) or []
        grid = _race_int(car.get('grid'))
        finish = _race_int(car.get('final_position'))
        parts = []

        if car.get('retired'):
            last_lap = int(laps[-1].get('lap', 0)) if laps else 0
            parts.append(f"T{last_lap}'de yarış dışı")
        elif grid is not None and finish is not None:
            net = grid - finish
            tag = f"+{net}" if net > 0 else (str(net) if net < 0 else "±0")
            parts.append(f"grid P{grid}→P{finish} ({tag})")
        elif finish is not None:
            parts.append(f"finiş P{finish}")

        if pits:
            first = min(pits, key=lambda pe: int(pe.get('lap', 0)))
            lap_no = int(first.get('lap', 0))
            lane = round(float(first.get('end', 0)) - float(first.get('start', 0)), 1)
            parts.append(f"{len(pits)} stop · pit T{lap_no}·{lane:.0f}s")
        else:
            parts.append("duraksız (tek stint)")

        move = next(
            (str(e.get('text', '')).split('—', 1)[1].strip()
             for e in events
             if e.get('kind') == 'undercut' and e.get('code') == code and '—' in str(e.get('text', ''))),
            None,
        )
        if move:
            parts.append(move)

        if parts:
            notes[code] = "  ·  ".join(parts)
    return notes


def strategy_wall_html(payload):
    """Stint tablosunu yarış mühendisliği strateji duvarı HUD'una dönüştürür."""
    total = max(1, int(payload.get('total_laps', 1)))
    tyre = {'SOFT': '#ef3340', 'MEDIUM': '#ffd23f', 'HARD': '#eef2f7', 'INTERMEDIATE': '#36c96a', 'WET': '#39a9ff'}
    pit_notes = _pit_move_notes_v37(payload)
    rows = []
    for car in payload.get('cars', []):
        groups = []
        for lap in car.get('laps', []):
            compound, stint = lap.get('compound', 'UNKNOWN'), lap.get('stint', 0)
            if groups and groups[-1]['compound'] == compound and groups[-1]['stint'] == stint and lap['lap'] == groups[-1]['end'] + 1:
                groups[-1]['end'] = lap['lap']
            else:
                groups.append({'start': lap['lap'], 'end': lap['lap'], 'compound': compound, 'stint': stint})
        blocks = ''.join(
            f"<span class='stint' style='--tyre:{tyre.get(group['compound'], '#738197')};width:{max(2, (group['end']-group['start']+1)/total*100):.2f}%'><b>{group['compound'][:1]}</b><small>{group['start']}–{group['end']}</small></span>"
            for group in groups
        )
        note = pit_notes.get(car['code'], '')
        note_html = f"<div class='pitnote'>{html_lib.escape(note)}</div>" if note else ""
        rows.append(f"<div class='row' style='--team:{car['colour']}'><div class='driver'>{html_lib.escape(car['code'])}<small>{html_lib.escape(car['team'])}</small></div><div class='stints'>{blocks}{note_html}</div><div class='finish'>P{car['final_position'] or '—'}<small>{len(groups)-1} PIT</small></div></div>")
    return f"""<style>body{{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}}.wall{{border:1px solid #2c425c;border-radius:13px;background:#11161f;overflow:hidden}}.head{{padding:13px 15px;border-bottom:1px solid #2b4058;font-size:13px;font-weight:950;letter-spacing:.08em}}.sub{{font-size:10px;color:#91a8bf;margin-top:5px}}.row{{display:grid;grid-template-columns:110px 1fr 58px;gap:10px;align-items:center;min-height:62px;padding:9px 12px;border-top:1px solid #23364b;border-left:4px solid var(--team)}}.driver{{font-weight:950;color:var(--team)}}.driver small,.finish small{{display:block;font-size:10px;color:#8fa6bd;margin-top:4px}}.stints{{display:flex;min-width:380px;height:29px;border-radius:6px;overflow:hidden;background:#0a111b;gap:2px}}.stint{{min-width:20px;display:flex;align-items:center;justify-content:center;gap:5px;background:color-mix(in srgb,var(--tyre) 23%,#11161f);border-top:3px solid var(--tyre);color:#f6f9ff;font-size:11px;font-weight:950}}.stint small{{font-size:9px;color:#bdcadd}}.finish{{font-weight:950;text-align:right}}.stints{{flex-wrap:wrap}}.pitnote{{flex:1 0 100%;font:600 10px ui-monospace,Consolas,monospace;color:#9db3c7;margin-top:5px;line-height:1.4}}@media(max-width:700px){{.row{{grid-template-columns:84px 1fr 42px;padding:8px}}.stints{{min-width:220px}}.stint small{{display:none}}}}</style><div class='wall'><div class='head'>TYRE STRATEGY WALL<div class='sub'>HER BLOK BİR STINT • ALT SATIR: GRID→FİNİŞ SONUCU · İLK PİT · TESPİT EDİLEN UNDERCUT/OVERCUT (kayıtlı veriden) • TOPLAM {total} TUR</div></div><div class='scroll'>{''.join(rows)}</div></div>"""


def strategy_wall_component_height(payload):
    """Lastik duvarının tüm 20+ pilotunu ana sayfada görünür tutar."""
    return min(2000, max(320, 110 + len(payload.get('cars', [])) * 82))


def position_flow_html(payload):
    """Pilotun tur tur sıra değişimini takım renkli, seçilebilir HUD grafiğine dönüştürür."""
    packed = fp_ui.json_for_script(payload)
    return r"""<style>*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2c425c;border-radius:13px;background:#11161f;padding:13px}.head{font-size:13px;font-weight:950;letter-spacing:.08em}.sub{font-size:10px;color:#90a7be;margin-top:5px}.chips{display:flex;gap:6px;flex-wrap:wrap;margin:11px 0}.chip{border:1px solid #36506e;border-left:4px solid var(--team);border-radius:6px;background:#132137;color:#f1f7ff;padding:6px 8px;font-weight:900;font-size:11px;cursor:pointer}.chip.active{background:#20334d;box-shadow:0 0 0 1px var(--team) inset}.layout{display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:12px}.graph{border:1px solid #29405a;border-radius:9px;background:#0b121c}.graph canvas{display:block;width:100%;height:270px}.summary{border:1px solid #2b405a;border-radius:9px;padding:11px;background:#11161f}.name{font-size:19px;font-weight:950;color:var(--team)}.line{display:flex;justify-content:space-between;border-top:1px solid #293b50;padding:8px 0;font-size:12px}.line span{color:#96aac0}.up{color:#79e5a7}.down{color:#ff7380}@media(max-width:720px){.layout{grid-template-columns:1fr}}</style><div class='hud'><div class='head'>RACE POSITION FLOW</div><div class='sub'>TUR TUR SIRA DEĞİŞİMİ • YUKARI OK POZİSYON KAZANCI, AŞAĞI OK POZİSYON KAYBI</div><div class='chips' id='chips'></div><div class='layout'><div class='graph'><canvas id='chart'></canvas></div><aside class='summary' id='summary'></aside></div></div><script>const data=__POSITION_FLOW_PAYLOAD__,cars=data.cars||[],canvas=document.getElementById('chart'),ctx=canvas.getContext('2d');let chosen=cars[0]?.code||'';function info(c){const a=(c.laps||[]).filter(x=>Number.isFinite(x.position));const start=c.grid||a[0]?.position||'—',finish=c.final_position||a[a.length-1]?.position||'—',values=a.map(x=>x.position),best=values.length?Math.min(...values):'—',worst=values.length?Math.max(...values):'—';return{a,start,finish,best,worst,change:(typeof start==='number'&&typeof finish==='number')?start-finish:0}}function draw(){const c=cars.find(x=>x.code===chosen)||cars[0],d=info(c),w=canvas.clientWidth,h=canvas.clientHeight,p={l:35,r:14,t:16,b:25},laps=Math.max(1,data.total_laps||1),maxP=Math.max(20,...cars.flatMap(x=>x.laps.map(y=>y.position||0)));ctx.clearRect(0,0,w,h);ctx.strokeStyle='#23384f';ctx.fillStyle='#8fa6bd';ctx.font='10px Arial';for(let pos=1;pos<=maxP;pos+=4){const y=p.t+(pos-1)/(maxP-1)*(h-p.t-p.b);ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText('P'+pos,4,y+3)}for(let x=1;x<=laps;x+=Math.max(1,Math.ceil(laps/8))){const px=p.l+(x-1)/(laps-1||1)*(w-p.l-p.r);ctx.fillText(x,px-4,h-7)}if(!d.a.length)return;ctx.strokeStyle=c.colour;ctx.lineWidth=3;ctx.beginPath();d.a.forEach((item,i)=>{const x=p.l+(item.lap-1)/(laps-1||1)*(w-p.l-p.r),y=p.t+(item.position-1)/(maxP-1)*(h-p.t-p.b);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();d.a.forEach(item=>{const x=p.l+(item.lap-1)/(laps-1||1)*(w-p.l-p.r),y=p.t+(item.position-1)/(maxP-1)*(h-p.t-p.b);ctx.fillStyle=c.colour;ctx.beginPath();ctx.arc(x,y,2.5,0,Math.PI*2);ctx.fill()})}function render(){const c=cars.find(x=>x.code===chosen)||cars[0],d=info(c),arrow=d.change>0?'↑ '+d.change+' SIRA':d.change<0?'↓ '+Math.abs(d.change)+' SIRA':'→ DEĞİŞMEDİ';document.getElementById('chips').innerHTML=cars.map(x=>`<button class='chip ${x.code===chosen?'active':''}' style='--team:${x.colour}' data-c='${x.code}'>${x.code}</button>`).join('');document.querySelectorAll('.chip').forEach(b=>b.onclick=()=>{chosen=b.dataset.c;render()});document.getElementById('summary').style.setProperty('--team',c.colour);document.getElementById('summary').innerHTML=`<div class='name'>${c.code}</div><div class='line'><span>Başlangıç</span><b>P${d.start}</b></div><div class='line'><span>En iyi sıra</span><b>P${d.best}</b></div><div class='line'><span>En kötü sıra</span><b>P${d.worst}</b></div><div class='line'><span>Bitiş</span><b>P${d.finish}</b></div><div class='line'><span>Toplam değişim</span><b class='${d.change>0?'up':d.change<0?'down':''}'>${arrow}</b></div>`;resize()}function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);draw()}window.addEventListener('resize',resize);render();</script>""".replace('__POSITION_FLOW_PAYLOAD__', packed)

# =========================================================
# BETA 1.2 — VERIFIED DATA / REPLAY STABILITY
# =========================================================

def render_data_trust_hud():
    """Kullanıcıya verinin ne olduğunu açıkça söyler; sahte canlılık iddiası yoktur."""
    st.markdown(
        "<div class='hud-card' style='border-left:4px solid #5ddcff;margin:8px 0 18px'>"
        "<div class='hud-label'>BETA 1.3 // VERIFIED DATA</div>"
        "<div class='history-copy' style='margin-top:7px'>"
        "Sonuç, tur, lastik ve pit verileri FastF1 paketinden geldiğinde doğrulanmış olarak gösterilir. "
        "Yarış Tekrarı gerçek tur/sıra/pit verisinden oluşturulan görsel canlandırmadır; resmî canlı GPS değildir. "
        "Canlı 2D konum paketi doğrulanmadan açılmaz."
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_driver_profile_hud(team_name, driver):
    """Takım sayfasına eksiksiz, tek kaynaktan üretilen pilot bilgi kartı ekler."""
    name, code, number, image_path = driver
    nation, debut, _titles = STEWARDLE_META.get(code, ('—', '—', '—'))
    career = driver_career_profile(code)
    team = TEAM_DIRECTORY_2026[team_name]
    portrait = current_driver_portrait(team_name, image_path)
    st.markdown(
        f"<div class='hud-card' style='border-left:4px solid {team['color']};margin-top:16px'>"
        f"<div class='hud-label'>PILOT DOSYASI // {html_lib.escape(code)}</div>"
        f"<div style='display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-top:8px'>"
        f"<img src='{html_lib.escape(portrait, quote=True)}' alt='' style='width:96px;height:118px;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\">"
        f"<div><div style='font-size:1.45rem;font-weight:950;color:{team['color']}'>{html_lib.escape(name)} <span style='color:#f2f5f8'>{html_lib.escape(number)}</span></div>"
        f"<div class='driver-meta' style='margin-top:5px'>{html_lib.escape(team_name)} · {html_lib.escape(nation)} · {driver_age(code)} yaş</div>"
        f"<div class='history-copy' style='margin-top:7px'>F1 başlangıcı: {html_lib.escape(str(debut))} · Kariyer GP galibiyeti: {html_lib.escape(str(career['wins']))} · Podyum: {html_lib.escape(str(career['podiums']))}</div>"
        f"<div class='history-copy' style='margin-top:7px'>{html_lib.escape(career['bio'])}</div>"
        f"<div style='margin-top:8px;padding:8px 10px;border-left:3px solid {team['color']};background:rgba(15,28,46,.55);font-size:.86rem;line-height:1.45'><b>Öne çıkan an:</b> {html_lib.escape(career['moment'])}</div>"
        f"</div></div></div>",
        unsafe_allow_html=True,
    )


# =========================================================
# 1.6 STABILITY PATCH: SAFE DATA + PIT WALL STRATEGY LAB
# This block deliberately leaves the replay and duel movement engines alone.
# =========================================================

def get_championship_data_stable(year):
    """Return FastF1 championship data, keeping the latest good page data on a short outage."""
    cache_key = f'last_good_championship_{int(year)}'
    try:
        payload = get_championship_data_v19(int(year))
        if payload and len(payload) == 5 and not payload[0].empty:
            st.session_state[cache_key] = {
                'drivers': payload[0].to_dict('records'),
                'teams': payload[1].to_dict('records'),
                'results': payload[2].to_dict('records'),
                'points': payload[3].to_dict('records'),
                'rounds': payload[4],
            }
        return payload
    except Exception:
        saved = st.session_state.get(cache_key)
        if saved:
            return (
                pd.DataFrame(saved['drivers']), pd.DataFrame(saved['teams']),
                pd.DataFrame(saved['results']), pd.DataFrame(saved['points']),
                saved['rounds'],
            )
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), []


def strategy_game_image(src, alt, color):
    """A portrait with a visible fallback instead of leaving a blank photo card."""
    fallback = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='150'%3E"
        "%3Crect width='100%25' height='100%25' rx='12' fill='%23101828'/%3E"
        f"%3Ctext x='60' y='80' text-anchor='middle' font-family='Arial' font-size='24' font-weight='bold' fill='{color.replace('#', '%23')}'%3EF1%3C/text%3E%3C/svg%3E"
    )
    return (
        f"<img src='{html_lib.escape(src, quote=True)}' alt='{html_lib.escape(alt, quote=True)}' "
        "style='width:82px;height:112px;object-fit:contain;object-position:center bottom' "
        f"onerror=\"this.onerror=null;this.src='{fallback}'\">"
    )


# Safe visual depth layer: CSS backgrounds only. No script, no fixed overlay,
# no animation loop and no element placed above Streamlit widgets.
st.markdown(r"""
<style>
    .stApp {
        background-color: #080d15 !important;
        background-image:
            radial-gradient(900px 520px at 84% 8%, rgba(42, 120, 165, .12), transparent 67%),
            radial-gradient(720px 480px at 12% 74%, rgba(10, 174, 154, .075), transparent 70%),
            linear-gradient(rgba(105, 137, 173, .035) 1px, transparent 1px),
            linear-gradient(90deg, rgba(105, 137, 173, .035) 1px, transparent 1px),
            linear-gradient(180deg, #080d15 0%, #090f18 48%, #070b12 100%) !important;
        background-size: auto, auto, 38px 38px, 38px 38px, auto !important;
        background-attachment: fixed, fixed, fixed, fixed, fixed !important;
    }
    section[data-testid="stMain"] > div {
        background: linear-gradient(90deg, rgba(8,13,21,.36), rgba(8,13,21,.10), rgba(8,13,21,.36));
    }
    section[data-testid="stSidebar"] {
        background-image: linear-gradient(180deg, rgba(13, 28, 49, .96), rgba(10, 18, 31, .98)) !important;
    }
    @media (max-width: 768px) {
        .stApp { background-size: auto, auto, 52px 52px, 52px 52px, auto !important; }
    }
</style>
""", unsafe_allow_html=True)

# =========================================================
# 1.7 FEATURE CENTRES
# Separate pages: weekend, story, comparison, learning and favourites.
# They use the same FastF1 cache and do not change replay/game engines.
# =========================================================

def directory_driver_by_code(code):
    for team_name, team in TEAM_DIRECTORY_2026.items():
        for name, driver_code, number, image_path in team['drivers']:
            if driver_code == code:
                return {'name': name, 'code': driver_code, 'number': number, 'image': image_path, 'team': team_name}
    return {'name': str(code), 'code': str(code), 'number': '', 'image': '', 'team': ''}


def render_page_header(title, subtitle, accent='#2ee6c9'):
    # redesign: tek noktadan tum merkez sayfalarina F1-TV basligi
    fp_ui.page_header(title, subtitle, eyebrow="Paddock")


def completed_session_options(event):
    return [item for item in event_session_cards(event) if item.get('status') == 'Tamamlandı']


def render_weekend_centre():
    render_page_header(T('page.weekend.title'), T('page.weekend.sub'))
    events = get_calendar_details(2026)
    if not events:
        st.warning('Takvim verisi şu anda alınamadı. Biraz sonra tekrar dene.')
        return
    event_names = [str(event.get('EventName', 'Grand Prix')) for event in events]
    selected_name = st.selectbox('Grand Prix seç', event_names, key='weekend_centre_event')
    event = next(item for item in events if str(item.get('EventName')) == selected_name)
    sessions = event_session_cards(event)
    st.markdown(f"### {html_lib.escape(selected_name)} // {html_lib.escape(str(event.get('Location', '')))}", unsafe_allow_html=True)
    if sessions:
        cols = st.columns(len(sessions))
        for col, item in zip(cols, sessions):
            local = item['time'].tz_convert('Europe/Istanbul').strftime('%d %b %H:%M')
            with col:
                st.markdown(f"<div class='hud-card' style='min-height:100px'><div class='hud-label'>{html_lib.escape(item['title'])}</div><div style='font-size:1.08rem;font-weight:900;margin-top:8px'>{local}</div><div class='driver-meta' style='margin-top:7px'>{html_lib.escape(item['status'])}</div></div>", unsafe_allow_html=True)
    completed = completed_session_options(event)
    if not completed:
        st.info('Bu hafta sonunda henüz tamamlanan seans yok. Program yukarıda İstanbul saatine göre görünür.')
        return
    code_map = {f"{item['title']} // {item['time'].tz_convert('Europe/Istanbul').strftime('%d %b %H:%M')}": item for item in completed}
    selected_label = st.radio('Tamamlanan seans', list(code_map), horizontal=True, key='weekend_centre_session')
    selected = code_map[selected_label]
    with st.spinner('Doğrulanmış seans sonucu yükleniyor…'):
        table, _ = get_session_results_table(2026, selected_name, selected['code'])
        story = get_session_story(2026, selected_name, selected['code'])
    if story:
        st.markdown('#### Bu seansta ne oldu?')
        story_cols = st.columns(min(3, len(story)))
        for col, entry in zip(story_cols, story[:3]):
            with col:
                st.markdown(f"<div class='hud-card' style='border-top:3px solid #f7c948;min-height:94px'><div class='hud-label'>{html_lib.escape(entry.get('kind', 'NOT'))}</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(entry.get('text', ''))}</div></div>", unsafe_allow_html=True)
    if table.empty:
        st.info('Bu seansın doğrulanmış sonuçları henüz paketlenmedi.')
        return
    st.markdown(f"#### {html_lib.escape(selected['title'])} sonuçları", unsafe_allow_html=True)
    render_html_hud(session_leaderboard_html(table, f'{selected_name} // {selected["title"].upper()}'), height=leaderboard_component_height(table), scrolling=False)


def render_favourites_centre():
    render_page_header(T('page.favourites.title'), T('page.favourites.sub'))
    _fcols = st.columns(2)
    with _fcols[0]:
        st.selectbox("Favori takım", list(TEAM_DIRECTORY_2026.keys()), key="favourite_team")
    team_name = st.session_state.get('favourite_team', 'Mercedes')
    with _fcols[1]:
        _drv = [d[0] for d in TEAM_DIRECTORY_2026.get(team_name, TEAM_DIRECTORY_2026['Mercedes'])['drivers']]
        st.selectbox("Favori pilot", _drv, key="favourite_driver")
    driver_name = st.session_state.get('favourite_driver', 'George Russell')
    team = TEAM_DIRECTORY_2026.get(team_name, TEAM_DIRECTORY_2026['Mercedes'])
    st.markdown(f"<div class='hud-card' style='border-top:4px solid {team['color']}'><div class='hud-label'>FAVORI TAKIM</div><div class='hud-value' style='color:{team['color']}'>{html_lib.escape(team_name)}</div><div class='driver-meta'>{html_lib.escape(driver_name)} secili pilotun.</div></div>", unsafe_allow_html=True)
    cols = st.columns(2)
    with cols[0]:
        if st.button('Hafta Sonu Merkezine git', key='favourite_weekend', width='stretch'):
            st.session_state['page'] = 'weekend'
            st.rerun()
    with cols[1]:
        if st.button('Pilot karşılaştırmasını aç', key='favourite_compare', width='stretch'):
            st.session_state['page'] = 'compare'
            st.rerun()
    st.markdown('#### Takım kadrosu')
    cards = st.columns(2)
    for col, item in zip(cards, team['drivers']):
        name, code, number, image_path = item
        with col:
            portrait = current_driver_portrait(team_name, image_path)
            st.markdown(f"<div class='hud-card' style='border-left:4px solid {team['color']};display:flex;align-items:center;gap:12px'>{strategy_game_image(portrait, name, team['color'])}<div><b style='font-size:1.1rem'>{html_lib.escape(name)}</b><div class='driver-meta'>{html_lib.escape(number)} | {html_lib.escape(code)}</div></div></div>", unsafe_allow_html=True)


# ÜST HUD — büyük kırmızı banner yerine sakin, ortak arayüz başlığı


# =========================================================
# 1.8 UI + PADDOCK AI PATCH
# Visual-only dashboard improvements and a safe, optional AI layer.
# =========================================================


def paddock_history_answer_v18(question):
    """Stable historic F1 answer set; no API key or network needed."""
    normal = _normalise_question_v19(question)
    year_match = re.search(r'\b(19[5-9][0-9]|20[0-2][0-9])\b', normal)
    wants_champion = any(token in normal for token in ('sampiyon', 'champion', 'wcd', 'dunya birincisi'))
    if year_match and wants_champion:
        year = int(year_match.group(1))
        champion = F1_WORLD_CHAMPIONS.get(year)
        if champion:
            return f'{year} Formula 1 dünya şampiyonu {champion} oldu.'
    if ('1985' in normal or '85' in normal) and wants_champion:
        return '1985 Formula 1 dünya şampiyonu Alain Prost oldu.'
    if ('en cok sampiyon' in normal or 'en fazla sampiyon' in normal or '7 sampiyon' in normal):
        return 'Formula 1 tarihinde rekor yedi şampiyonlukla Lewis Hamilton ve Michael Schumacher arasında paylaşılır.'
    return ''


st.markdown(r"""
<style>
/* 1.8 safe visual pass: CSS only, no layers, no animations, no request loop. */
.f1-header{background:linear-gradient(120deg,#0f1d32 0%,#101b2d 56%,#151123 100%)!important;border:1px solid #29446c!important;border-radius:18px!important;box-shadow:0 16px 36px rgba(0,0,0,.25)!important;padding:20px 24px!important;}
.f1-header h1{letter-spacing:1.7px!important;font-size:1.6rem!important;}
.paddock-topline{display:flex;align-items:center;gap:12px}.paddock-topline img{width:74px;height:auto;filter:invert(1) sepia(1) saturate(8) hue-rotate(125deg)}
/* redesign: eski .paddock-side-brand + bare sidebar-button mavi stili kaldirildi */
.hud-card{border-radius:15px!important;background:linear-gradient(145deg,rgba(18,31,52,.96),rgba(13,23,39,.96))!important;border-color:#294566!important;box-shadow:0 12px 24px rgba(0,0,0,.14)!important}.hud-label{letter-spacing:1.45px!important;color:#92abd0!important}.hud-value{margin-top:8px!important}.home-command-card{min-height:100px}.compare-mini{min-height:95px}.compare-driver-card{min-height:178px}.compare-driver-main{display:flex;align-items:center;gap:15px}.compare-driver-main img{width:92px!important;height:112px!important;object-fit:contain!important}.compare-stat-grid{display:grid;grid-template-columns:1fr 1.4fr;gap:10px;margin-top:14px}.compare-stat-grid>div{background:#0b1627;border:1px solid #25405f;border-radius:10px;padding:10px}.compare-stat-grid span{display:block;color:#89a3c7;font-size:.65rem;letter-spacing:1.1px;font-weight:800}.compare-stat-grid b{display:block;color:#f5fbff;font-size:1.05rem;margin-top:4px}
.paddock-ai-intro{margin-bottom:15px!important}@media(max-width:800px){.home-command-card{min-height:78px}.compare-driver-main img{width:72px!important;height:92px!important}.f1-header{padding:16px!important}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 1.9 PROFESSIONAL NEWS + AI KNOWLEDGE + GAME PATCH
# Additive patch. It does not change replay, telemetry or FastF1 loading.
# =========================================================


def _ascii_question_v19(question):
    return unicodedata.normalize('NFKD', str(question or '')).encode('ascii', 'ignore').decode('ascii').lower().strip()


def paddock_record_answer_v19(question):
    text = _ascii_question_v19(question)
    if ('bir sezonda' in text or '1 sezonda' in text or 'tek sezonda' in text) and ('en cok galibiyet' in text or 'en fazla galibiyet' in text or 'cok kazan' in text):
        return F1_RECORD_FACTS_V19['most_wins_single_season']
    if ('en cok sampiyon' in text or 'en fazla sampiyon' in text or 'en cok dunya sampiyon' in text):
        return F1_RECORD_FACTS_V19['most_titles']
    if ('en cok yaris kazanan' in text or 'en cok galibiyet alan' in text or 'kariyerde en cok galibiyet' in text):
        return F1_RECORD_FACTS_V19['most_wins']
    if ('en cok pole' in text or 'pole rekoru' in text):
        return F1_RECORD_FACTS_V19['most_poles']
    if ('en genc sampiyon' in text or 'genc dunya sampiyonu' in text):
        return F1_RECORD_FACTS_V19['youngest_champion']
    return ''


def paddock_assistant_answer_v19_pro(question, year=2026):
    record = paddock_record_answer_v19(question)
    if record:
        return {'title': 'F1 rekor arşivi', 'answer': record, 'source': 'F1 tarih arşivi'}
    historic = paddock_history_answer_v18(question)
    if historic:
        return {'title': 'F1 tarih bilgisi', 'answer': historic, 'source': 'Yerel F1 dünya şampiyonları arşivi'}
    return paddock_assistant_answer_v19(question, year)


def render_paddock_assistant_v20():
    fp_ui.page_header(T("page.assistant.title"), T("page.assistant.sub"), eyebrow=T("section.paddock"))
    api_ready = bool(configured_openai_api_key())
    accent = '#2ee6c9' if api_ready else '#f7c948'
    mode = 'OPENAI + DOĞRULANMIŞ VERİ' if api_ready else 'F1 VERİSİ VE TARİH ARŞİVİ'
    description = (
        'OpenAI bağlantısı aktif. Sonuç sorularında FastF1 verisi önce gelir; genel F1 sorularında AI yanıtı devreye girer.'
        if api_ready else
        'Sonuç, pole, lastik, tarihî şampiyon ve temel rekor sorularını anahtarsız cevaplar. Genel F1 sohbeti için isteğe bağlı OpenAI anahtarı gerekir.'
    )
    st.markdown(f"<div class='hud-card ai-command-card' style='border-top:5px solid {accent}'><div class='hud-label'>{mode}</div><div style='font-size:1.25rem;font-weight:950;margin-top:7px'>F1 sorunu yaz, kaynaklı yanıt al.</div><div class='history-copy' style='margin-top:6px'>{description}</div></div>", unsafe_allow_html=True)
    if 'paddock_chat_history_v19' not in st.session_state:
        st.session_state['paddock_chat_history_v19'] = []

    examples = ['1985 sampiyonu kim?', '1 sezonda en cok galibiyet alan isim kim?', 'Pole kim?', 'Alonso kacinci oldu?']
    columns = st.columns(4)
    for col, question in zip(columns, examples):
        with col:
            if st.button(question, key='assistant_v19_' + question, width='stretch'):
                st.session_state['paddock_pending_v19'] = question
                st.rerun()

    for item in st.session_state['paddock_chat_history_v19'][-10:]:
        with st.chat_message(item['role']):
            st.markdown(item['text'])
            if item.get('source'):
                st.caption('Kaynak: ' + item['source'])

    prompt = st.chat_input('F1 hakkında sor… Örnek: 1 sezonda en çok galibiyet alan isim kim?')
    question = st.session_state.pop('paddock_pending_v19', '') or prompt
    if question:
        st.session_state['paddock_chat_history_v19'].append({'role': 'user', 'text': question})
        with st.chat_message('user'):
            st.markdown(question)
        with st.chat_message('assistant'):
            with st.spinner('Paddock kaynakları kontrol ediliyor…'):
                answer = paddock_assistant_answer_v19_pro(question, 2026)
            st.markdown(answer['answer'])
            st.caption('Kaynak: ' + answer['source'])
        st.session_state['paddock_chat_history_v19'].append({'role': 'assistant', 'text': answer['answer'], 'source': answer['source']})

    if not api_ready:
        with st.expander('Genel sorular için OpenAI bağlantısı'):
            st.write('ChatGPT hesabının kendisi siteye bağlanmaz; OpenAI API anahtarı gerekir. Proje klasöründeki .streamlit/secrets.toml dosyasına OPENAI_API_KEY eklediğinde asistan genel F1 sorularında OpenAI yanıtı da verir. Anahtar yokken bu ekran yine kaynaklı F1 veri modunda çalışır.')


def _rss_text_v19(node, name):
    child = node.find(name)
    return str(child.text or '').strip() if child is not None and child.text else ''


def _rss_image_v19(item):
    enclosure = item.find('enclosure')
    if enclosure is not None and enclosure.get('url'):
        return safe_external_url(enclosure.get('url'))
    for element in item.iter():
        tag = str(element.tag).lower()
        if tag.endswith('content') or tag.endswith('thumbnail'):
            candidate = element.get('url') or element.get('href')
            safe = safe_external_url(candidate)
            if safe:
                return safe
    return ''


def news_matches_team_v19(item, team_name):
    if team_name == 'Genel F1':
        return True
    team = TEAM_DIRECTORY_2026.get(team_name, {})
    words = [team_name.lower(), str(team.get('slug', '')).lower()]
    words.extend(str(driver[0]).lower().split()[-1] for driver in team.get('drivers', []))
    haystack = (str(item.get('title', '')) + ' ' + str(item.get('desc', ''))).lower()
    return any(word and word in haystack for word in words)


st.markdown(r"""
<style>
/* 1.9: safe component styling only. No fixed overlays, canvas or animations. */
.ai-command-card{margin-bottom:16px!important}.ai-command-card .history-copy{max-width:900px}.news-command-card{margin:12px 0 18px!important}.news-card-v19{min-height:340px!important;display:flex;flex-direction:column;gap:7px}.news-thumb-v19{width:100%;height:118px;object-fit:cover;border-radius:10px;border:1px solid #2b4669;background:#0b1627}.news-thumb-empty-v19{display:grid;place-items:center;font-size:2rem;font-weight:950;color:#2ee6c9;background:linear-gradient(135deg,#112846,#0c182b)}.news-card-v19 .news-title{margin-top:2px}.news-card-v19 .news-desc{flex:1}.game-choice-v19{min-height:150px}.stChatMessage{border:1px solid rgba(56,108,160,.35);border-radius:14px;padding:8px 12px}
section[data-testid="stSidebar"] .stButton,section[data-testid="stSidebar"] div[data-testid="stButton"]{width:100%!important;margin:0!important}section[data-testid="stSidebar"] .stButton>button,section[data-testid="stSidebar"] div[data-testid="stButton"]>button{width:100%!important;min-height:48px!important;padding:0 16px!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;gap:8px!important;line-height:1.1!important}section[data-testid="stSidebar"] .stButton>button p,section[data-testid="stSidebar"] div[data-testid="stButton"]>button p{width:100%!important;margin:0!important;text-align:left!important;white-space:normal!important}section[data-testid="stSidebar"] [data-testid="stExpander"]{border-radius:12px!important;overflow:hidden!important}section[data-testid="stSidebar"] [data-testid="stExpander"] summary{min-height:48px!important;display:flex!important;align-items:center!important;padding-left:14px!important}
</style>
""", unsafe_allow_html=True)


# redesign: global .f1-header banner kaldirildi — her sayfa kendi fp_ui.page_header'ini
# (veya mevcut "## Baslik" basligini) kullaniyor.


# ==========================================
# SAYFA 1: ANA SAYFA & DİNAMİK RACECENTER
# ==========================================

# =========================================================
# 2.0 STABILITY + TURKISH CONTENT PATCH
# This layer deliberately leaves replay and telemetry engines untouched.
# =========================================================

def repair_text_v20(value):
    """Repairs legacy UTF-8 text that was once read as latin-1, when possible."""
    text = str(value or '')
    if any(mark in text for mark in ('Ã', 'Ä', 'Å', 'â')):
        try:
            return text.encode('latin1').decode('utf-8')
        except (UnicodeError, UnicodeEncodeError):
            return text
    return text


def news_item_is_f1_v20(item, link, title, description):
    categories = ' '.join(
        str(node.text or '')
        for node in item.findall('category')
    )
    haystack = ' '.join([link, title, description, categories]).lower()
    return '/f1/' in haystack or 'formula 1' in haystack or 'formula1' in haystack or haystack.startswith('f1 ')


@st.cache_data(ttl=1800, show_spinner=False)
def translate_news_text_v20(text):
    # 30 dk önbellek: başarılı çeviri _translate_to_tr_raw'da zaten 24s yaşıyor;
    # buradaki kısa TTL, çeviri BAŞARISIZ olduğunda (İngilizce metin) her
    # rerun'da ~16 doomed ağ çağrısını engeller. En fazla 30 dk bayat kalır.
    clean = repair_text_v20(text).strip()
    if not clean:
        return ''
    return repair_text_v20(translate_to_tr(clean))


@cache_data_safe(ttl=900, on_error=list, label='news catalog v2')
def fetch_f1_news_catalog_v20(limit=30):
    """Turkish-first feed. English sources only fill the gap and are translated on display.

    Tüm kaynaklar düşerse boş liste önbelleğe alınmaz; sonraki açılış yeniden dener."""
    sources = [
        ('Motorsport Türkiye', 'https://tr.motorsport.com/rss/', 'tr'),
        ('Autosport', 'https://www.autosport.com/rss/f1/news/', 'en'),
        ('Sky Sports', 'https://www.skysports.com/rss/12433', 'en'),
        ('Motorsport', 'https://www.motorsport.com/rss/f1/news/', 'en'),
    ]
    def _pull(entry):
        """Tek kaynağın RSS öğelerini çek + ayrıştır — paralel çalışır."""
        source_name, url, language = entry
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 FormulaPaddock/2.0'})
            with urllib.request.urlopen(request, timeout=7) as response:
                root = ET.fromstring(response.read())
        except Exception as error:
            log_data_error('news catalog v2', error)
            return []
        out = []
        for item in root.findall('.//item'):
            raw_title = _rss_text_v19(item, 'title')
            raw_link = safe_external_url(_rss_text_v19(item, 'link'))
            raw_description = re.sub(r'<[^>]*>', ' ', _rss_text_v19(item, 'description'))
            raw_description = re.sub(r'\s+', ' ', raw_description).strip()
            if not raw_title or not raw_link:
                continue
            if source_name == 'Motorsport Türkiye' and not news_item_is_f1_v20(item, raw_link, raw_title, raw_description):
                continue
            out.append({
                'title': repair_text_v20(raw_title),
                'link': raw_link,
                'date': repair_text_v20(_rss_text_v19(item, 'pubDate'))[:30],
                'desc': repair_text_v20(raw_description)[:260],
                'source': source_name,
                'language': language,
                'image': _rss_image_v19(item),
            })
        return out

    # Kaynakları PARALEL çek (sıralı ~28 sn -> ~7 sn); sonra kaynak sırasına göre birleştir.
    from concurrent.futures import ThreadPoolExecutor as _TPE
    with _TPE(max_workers=len(sources)) as _ex:
        per_source = list(_ex.map(_pull, sources))

    catalog, seen = [], set()
    for items in per_source:            # per_source, `sources` ile aynı sırada (Motorsport Türkiye önce)
        for entry in items:
            fingerprint = (entry['title'].lower(), entry['link'].lower())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            catalog.append(entry)
            if len(catalog) >= int(limit):
                return catalog
    if not catalog:
        raise RuntimeError('hiçbir haber kaynağı yanıt vermedi')
    return catalog[:int(limit)]


def localise_news_item_v20(item):
    if item.get('language') == 'tr':
        return dict(item)
    local = dict(item)
    local['title'] = translate_news_text_v20(item.get('title', ''))
    local['desc'] = translate_news_text_v20(item.get('desc', ''))
    return local


@cache_data_safe(ttl=900, on_error=list, label='localised news v34')
def fetch_localised_news_catalog_v34(limit=30):
    """Haber kataloğunu çekip TÜMÜNÜ bir kez çevirir, çevrilmiş listeyi 15 dk
    önbelleğe alır. Böylece takım filtresi değişince ya da her rerun'da
    tekrar çeviri yapılmaz; ilk yüklemede de ~40 seri istek yerine 1 toplu
    DeepL isteği gider. Katalog boşsa hata yükselir (önbelleğe girmez)."""
    catalog = fetch_f1_news_catalog_v20(limit)
    if not catalog:
        raise RuntimeError('hiçbir haber kaynağı yanıt vermedi')

    result = [dict(item) for item in catalog]
    jobs = []               # (result_index, field, kaynak_metin)
    for index, item in enumerate(catalog):
        if item.get('language') == 'tr':
            continue
        for field in ('title', 'desc'):
            value = repair_text_v20(item.get(field, '')).strip()
            if value:
                jobs.append((index, field, value))
    if not jobs:
        return result

    source_texts = [job[2] for job in jobs]
    translated = None
    if deepl_configured():
        try:
            translated = list(_deepl_translate_batch_v34(tuple(source_texts)))
        except Exception as error:
            log_data_error('localised news batch (deepl)', error)
            translated = None
    if translated is None:
        # Yedek: paralel tekil Google çevirisi (thread-güvenli, st'ye dokunmaz)
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=min(12, len(source_texts))) as executor:
            translated = list(executor.map(_gtx_translate_plain, source_texts))

    for (index, field, original), rendered in zip(jobs, translated):
        rendered = repair_text_v20(rendered or '').strip()
        if rendered:
            result[index][field] = rendered
    return result


def render_news_centre_v20():
    render_page_header(T('page.news.title'), T('page.news.sub'))
    teams = ['Genel F1'] + list(TEAM_DIRECTORY_2026.keys())
    selected = st.selectbox('\u0130zlemek istedi\u011fin ak\u0131\u015f', teams, key='news_team_filter_v20')
    with st.spinner('Haber akışı hazırlanıyor...'):
        localized_catalog = fetch_localised_news_catalog_v34(30)
    localized = [item for item in localized_catalog if news_matches_team_v19(item, selected)]
    radar_title = 'Genel Formula 1 akışı' if selected == 'Genel F1' else selected + ' haberleri'
    st.markdown(
        f"<div class='hud-card news-command-card'><div class='hud-label'>HABER RADARI // TÜRKÇE</div>"
        f"<div class='hud-value'>{html_lib.escape(radar_title)}</div>"
        f"<div class='driver-meta'>{len(localized)} haber gösteriliyor · Öncelikli kaynak: Motorsport Türkiye</div></div>",
        unsafe_allow_html=True,
    )
    if not localized:
        st.info('Seçilen takım için yeni haber bulunamadı. Genel F1 akışına geçerek tüm Türkçe haberleri görebilirsin.')
        return
    featured = localized[0]
    feature_image = safe_external_url(featured.get('image'))
    feature_media = (
        f"<img class='news-feature-image-v20' src='{html_lib.escape(feature_image, quote=True)}' alt='' onerror=\"this.remove()\">"
        if feature_image else "<div class='news-feature-image-v20 news-thumb-empty-v19'>F1</div>"
    )
    st.markdown(
        f"<div class='news-feature-v20'>{feature_media}<div class='news-feature-copy-v20'><div class='hud-label'>GÜNÜN KAPAK HABERİ</div>"
        f"<div class='news-feature-title-v20'>{html_lib.escape(featured.get('title', ''))}</div>"
        f"<div class='history-copy'>{html_lib.escape(featured.get('desc', ''))}</div>"
        f"<a href='{html_lib.escape(featured.get('link', '#'), quote=True)}' target='_blank' rel='noopener noreferrer' class='news-link'>Kaynağa git ↗</a>"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    cards = []
    for item in localized[1:]:
        image = safe_external_url(item.get('image'))
        media = (
            f"<img class='news-thumb-v19' src='{html_lib.escape(image, quote=True)}' alt='' onerror=\"this.style.display='none'\">"
            if image else "<div class='news-thumb-v19 news-thumb-empty-v19'>F1</div>"
        )
        cards.append(
            f"<div class='news-card news-card-v20'>{media}<div class='news-date'>{html_lib.escape(item.get('source', 'Kaynak'))} · {html_lib.escape(item.get('date', ''))}</div>"
            f"<div class='news-title'>{html_lib.escape(item.get('title', ''))}</div><div class='news-desc'>{html_lib.escape(item.get('desc', ''))}</div>"
            f"<a href='{html_lib.escape(item.get('link', '#'), quote=True)}' target='_blank' rel='noopener noreferrer' class='news-link'>Haberi aç ↗</a></div>"
        )
    if cards:
        # TEK CSS grid — satirdaki tum kartlar esit yukseklik (st.columns'un
        # sutun-bazli esitsizligi yok). Bkz. fp_ui.news_grid.
        st.markdown(
            "<div class='news-grid-v20'>" + "".join(cards) + "</div>",
            unsafe_allow_html=True,
        )


@st.cache_data(ttl=1800, show_spinner=False)
def get_race_story_package_v20(year, event_name):
    """One verified FastF1 read for a fuller Turkish race-story page."""
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=False, weather=False, messages=True)
        results = session.results.copy() if session.results is not None else pd.DataFrame()
        laps = session.laps.copy() if session.laps is not None else pd.DataFrame()
        if results.empty:
            return {'ok': False, 'reason': 'Doğrulanmış yarış sonucu henüz yok.'}
        results = results.sort_values('Position', na_position='last').copy()
        finishers = results[pd.to_numeric(results.get('Position'), errors='coerce').notna()].copy()
        podium = []
        for _, row in finishers.head(3).iterrows():
            podium.append({
                'position': int(float(row.get('Position'))),
                'code': str(row.get('Abbreviation', '-')),
                'team': repair_text_v20(row.get('TeamName', '-')),
            })
        winner = podium[0] if podium else {'code': '-', 'team': '-'}
        mover = None
        if 'GridPosition' in finishers.columns:
            gains = finishers.copy()
            gains['_gain'] = pd.to_numeric(gains['GridPosition'], errors='coerce') - pd.to_numeric(gains['Position'], errors='coerce')
            gains = gains.dropna(subset=['_gain']).sort_values('_gain', ascending=False)
            if not gains.empty and float(gains.iloc[0]['_gain']) > 0:
                row = gains.iloc[0]
                mover = {'code': str(row.get('Abbreviation', '-')), 'gain': int(float(row['_gain']))}
        fastest = None
        if not laps.empty and 'LapTime' in laps.columns:
            valid = laps.dropna(subset=['LapTime']).copy()
            if not valid.empty:
                best = valid.loc[valid['LapTime'].idxmin()]
                fastest = {'code': str(best.get('Driver', '-')), 'time': format_time(best.get('LapTime'))}
        control = []
        messages = getattr(session, 'race_control_messages', pd.DataFrame())
        if isinstance(messages, pd.DataFrame) and not messages.empty:
            text_column = next((column for column in ('Message', 'Text') if column in messages.columns), None)
            if text_column:
                for raw in messages[text_column].dropna().astype(str).tolist()[::-1]:
                    translated = repair_text_v20(translate_race_control_message(raw))
                    if translated and translated not in control:
                        control.append(translated)
                    if len(control) == 4:
                        break
                control.reverse()
        points = []
        for _, row in finishers.head(10).iterrows():
            points.append({
                'position': int(float(row.get('Position'))),
                'code': str(row.get('Abbreviation', '-')),
                'team': repair_text_v20(row.get('TeamName', '-')),
                'points': int(float(row.get('Points', 0) or 0)),
            })
        return {'ok': True, 'winner': winner, 'podium': podium, 'mover': mover, 'fastest': fastest, 'control': control, 'points': points}
    except Exception as error:
        return {'ok': False, 'reason': 'Yarış hikâyesi verisi şu anda alınamadı: ' + repair_text_v20(error)}


def render_race_story_centre_v20():
    render_page_header(T('page.story.title'), T('page.story.sub'))
    events = get_calendar_details(2026)
    completed = [event for event in events if any(item.get('code') == 'R' for item in completed_session_options(event))]
    if not completed:
        st.info('Yarış hikâyesi için tamamlanmış bir Grand Prix sonucu bekleniyor.')
        return
    names = [repair_text_v20(event.get('EventName', 'Formula 1')) for event in completed]
    selected = st.selectbox('Yarış seç', names, index=len(names) - 1, key='story_centre_event_v20')
    with st.spinner('Doğrulanmış yarış hikâyesi hazırlanıyor...'):
        package = get_race_story_package_v20(2026, selected)
        table, _ = get_session_results_table(2026, selected, 'R')
        intelligence = get_race_intelligence_v19(2026, selected)
    if not package.get('ok') or table.empty:
        st.warning(package.get('reason', 'Bu yarışın verisi henüz hazır değil.'))
        return
    winner = package.get('winner', {})
    podium = package.get('podium', [])
    mover = package.get('mover')
    fastest = package.get('fastest')
    lead = f"{winner.get('code', '-')} yarışı kazandı; {winner.get('team', '-')} için günün en büyük sonucu geldi."
    if mover:
        lead += f" {mover['code']} start yerine göre {mover['gain']} sıra yükseldi."
    st.markdown(f"<div class='story-lead-v20'><div class='hud-label'>60 SANİYEDE YARIŞ</div><div class='story-lead-title-v20'>{html_lib.escape(lead)}</div><div class='history-copy'>Aşağıdaki tüm sonuçlar tamamlanmış FastF1 yarış paketinden gelir.</div></div>", unsafe_allow_html=True)
    cards = [
        ('KAZANAN', winner.get('code', '-'), winner.get('team', '-'), '#f7c948'),
        ('PODYUM', ' · '.join(item.get('code', '-') for item in podium) or '-', 'İlk üç', '#ff385c'),
        ('EN ÇOK YÜKSELEN', mover.get('code', '-') if mover else '-', f"+{mover.get('gain', 0)} sıra" if mover else 'Yükseliş verisi yok', '#2ee6c9'),
        ('EN HIZLI TUR', fastest.get('code', '-') if fastest else '-', fastest.get('time', '-') if fastest else 'Veri yok', '#7dd3fc'),
    ]
    columns = st.columns(4)
    for column, (label, main, sub, color) in zip(columns, cards):
        with column:
            st.markdown(f"<div class='hud-card story-metric-v20' style='border-top:4px solid {color}'><div class='hud-label'>{label}</div><div class='story-metric-value-v20'>{html_lib.escape(str(main))}</div><div class='driver-meta'>{html_lib.escape(str(sub))}</div></div>", unsafe_allow_html=True)
    left, right = st.columns([1.15, .85])
    with left:
        st.markdown('#### Yarışın dikkat çeken anları')
        notes = package.get('control', [])
        if notes:
            for note in notes:
                st.markdown(f"<div class='story-note-v20'><b>FIA Race Control</b><br>{html_lib.escape(note)}</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='story-note-v20'><b>Temiz yarış kaydı</b><br>Bu yarış için doğrulanmış ek Race Control notu bulunmadı.</div>", unsafe_allow_html=True)
    with right:
        st.markdown('#### Pit ve hız özeti')
        pits = intelligence.get('pits', []) if intelligence.get('ok') else []
        trap = intelligence.get('speed_trap') if intelligence.get('ok') else None
        pit_copy = f"{len(pits)} doğrulanmış pit geçişi kaydedildi." if pits else 'Pit geçişi verisi bulunamadı.'
        trap_copy = f"Hız tuzağı: {trap.get('driver')} · {trap.get('speed')} km/h" if trap else 'Hız tuzağı verisi yok.'
        st.markdown(f"<div class='hud-card'><div class='hud-label'>STRATEJİ ÖZETİ</div><div style='font-size:1.15rem;font-weight:950;margin-top:8px'>{html_lib.escape(pit_copy)}</div><div class='driver-meta' style='margin-top:6px'>{html_lib.escape(trap_copy)}</div></div>", unsafe_allow_html=True)
    st.markdown('#### Puan alanlar')
    points = package.get('points', [])
    if points:
        st.dataframe(pd.DataFrame(points).rename(columns={'position':'Sıra', 'code':'Pilot', 'team':'Takım', 'points':'Puan'}), width='stretch', hide_index=True)
    with st.expander('Tam yarış sonucu', expanded=False):
        render_html_hud(session_leaderboard_html(table, f'{selected} // YARIŞ SONUCU'), height=leaderboard_component_height(table), scrolling=False)


def render_learning_centre_v20():
    render_page_header(T('page.learn.title'), T('page.learn.sub'))
    st.markdown("<div class='hud-card learning-hero-v20'><div class='hud-label'>F1'E BAŞLA // 5 DAKİKALIK ROTA</div><div class='hud-value'>Önce yarışı anla, sonra veriyi oku.</div><div class='history-copy'>Buradaki kartlar terim ezberletmez; bir hafta sonunda ekranda neye bakacağını öğretir.</div></div>", unsafe_allow_html=True)
    tracks = [
        ('1', 'Hafta sonu', 'FP1–FP3 hazırlıktır. Sıralama başlangıç sırasını, yarış ise puanları belirler.', '#7dd3fc'),
        ('2', 'Start ve ilk tur', 'İlk virajda konum kazanmak önemlidir; ama lastiği gereksiz yıpratmak sonraki turları zorlaştırır.', '#ff385c'),
        ('3', 'Lastik kararı', 'Soft (kırmızı) hız verir, Hard (beyaz) uzun sürer, Medium (sarı) ortadadır. Doğru seçim pist sıcaklığına ve pit penceresine bağlıdır.', '#f7c948'),
        ('4', 'Pit duvarı', 'Takım, trafiği ve lastik ömrünü izleyerek pit zamanını seçer. Rakipten önce pit = "undercut", sonra pit = "overcut". Bir tur erken/geç karar sonucu değiştirir.', '#2ee6c9'),
        ('5', 'Geçiş ve enerji', 'Düzlükte Straight Mode (yayında eski adıyla "DRS") sürtünmeyi azaltır; mücadelede Overtake Mode (ERS hücum / push-to-pass) ek elektrik gücü verir.', '#a78bfa'),
        ('6', 'Yarış sonrası', 'Sonuçtan sonra en hızlı tur, pitler, sıra değişimi ve takım arkadaşları arasındaki fark okunur.', '#fb923c'),
    ]
    for start in range(0, len(tracks), 3):
        columns = st.columns(3)
        for column, (number, title, copy, color) in zip(columns, tracks[start:start + 3]):
            with column:
                st.markdown(f"<div class='hud-card learning-step-v20' style='border-top:4px solid {color}'><div class='learning-number-v20'>{number}</div><div style='font-size:1.18rem;font-weight:950'>{html_lib.escape(title)}</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(copy)}</div></div>", unsafe_allow_html=True)
    st.markdown('#### İlk yarışını izlerken buna bak')
    watch = st.radio('En çok neyi anlamak istiyorsun?', ['Kim önde?', 'Lastikler ne durumda?', 'Neden pit yaptılar?', 'İki pilot arasındaki fark nerede?'], horizontal=True, key='learning_watch_v20')
    watch_copy = {
        'Kim önde?': 'Seans Merkezi ve Hafta Sonu Merkezi ile sıralama, tur ve farkları takip et.',
        'Lastikler ne durumda?': 'Yarış Hikâyesi ve Lastik Stratejisi ekranında hamur, stint ve pit geçişlerini izle.',
        'Neden pit yaptılar?': 'Pit zamanı; lastik aşınması, trafik, hava ve rakibin hamlesiyle birlikte değerlendirilir.',
        'İki pilot arasındaki fark nerede?': 'Pilot Karşılaştırma bölümünde tur, sektör, fren ve gaz verilerini aç.',
    }
    st.markdown(f"<div class='hud-card' style='border-left:4px solid #f7c948'><div class='hud-label'>SANA ÖNERİ</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(watch_copy[watch])}</div></div>", unsafe_allow_html=True)
    buttons = st.columns(3)
    with buttons[0]:
        if st.button('Hafta Sonu Merkezini aç', key='learn_weekend_v20', width='stretch'):
            st.session_state['page'] = 'weekend'
            st.rerun()
    with buttons[1]:
        if st.button('Yarış Hikâyesini aç', key='learn_story_v20', width='stretch'):
            st.session_state['page'] = 'story'
            st.rerun()
    with buttons[2]:
        if st.button('60+ terimlik sözlüğe git', key='learn_glossary_v20', width='stretch'):
            st.session_state['page'] = 'glossary'
            st.rerun()


# Keep page routing untouched: these names replace only their page renderers.
render_news_centre_v19 = render_news_centre_v20
render_race_story_centre = render_race_story_centre_v20
render_learning_centre = render_learning_centre_v20


st.markdown(r"""
<style>
/* 2.0 common HUD rules: static CSS only, no overlays or animation loops. */
section[data-testid="stSidebar"] div[data-testid="stButton"]{width:100%!important;margin:0 0 8px!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button{width:100%!important;min-height:50px!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;padding:0 16px!important;border-radius:11px!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button p{width:100%!important;margin:0!important;text-align:left!important;line-height:1.15!important;font-size:.88rem!important}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary{min-height:50px!important;display:flex!important;align-items:center!important;text-align:left!important}
.news-feature-v20{display:grid;grid-template-columns:minmax(240px,.9fr) minmax(0,1.1fr);gap:18px;align-items:stretch;padding:16px;border:1px solid #325174;border-left:5px solid #ff385c;border-radius:15px;background:linear-gradient(135deg,#101c2d,#111a28);margin:18px 0}.news-feature-image-v20{width:100%;height:230px;object-fit:cover;border-radius:11px;border:1px solid #29435f;background:#0b1422}.news-feature-copy-v20{display:flex;flex-direction:column;gap:9px;justify-content:center}.news-feature-title-v20{font-size:1.45rem;font-weight:950;line-height:1.22;color:#f6f9ff}.news-card-v20{min-height:0!important;display:flex;flex-direction:column;gap:6px}.news-card-v20 .news-desc{display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}.news-card-v20 .news-link{margin-top:auto}.news-grid-v20{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:8px;align-items:stretch}.story-lead-v20{padding:18px;border:1px solid #3a526f;border-left:5px solid #ff385c;border-radius:14px;background:linear-gradient(135deg,#131b2a,#0f1928);margin:16px 0}.story-lead-title-v20{font-size:1.38rem;font-weight:950;line-height:1.3;margin:7px 0}.story-metric-v20{min-height:112px}.story-metric-value-v20{font-size:1.23rem;font-weight:950;margin-top:8px}.story-note-v20{border:1px solid #314964;border-left:4px solid #ff385c;border-radius:10px;background:#111b2a;padding:12px;margin-bottom:8px;line-height:1.5}.learning-hero-v20{border-top:5px solid #f7c948;margin-bottom:18px}.learning-step-v20{min-height:170px;position:relative;overflow:hidden}.learning-number-v20{font-size:2.7rem;font-weight:950;line-height:1;color:rgba(255,255,255,.14);margin-bottom:9px}
@media(max-width:900px){.news-grid-v20{grid-template-columns:1fr 1fr}}
@media(max-width:620px){.news-grid-v20{grid-template-columns:1fr}}
@media(max-width:800px){.news-feature-v20{grid-template-columns:1fr}.news-feature-image-v20{height:190px}.story-metric-value-v20{font-size:1.05rem}.learning-step-v20{min-height:auto}.stButton>button{min-height:46px!important}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.1 GAMES + SIDEBAR HUD PATCH
# Historical Stewarlde uses an external historical-results catalog.  It does
# not fabricate old driver statistics when the catalog cannot be reached.
# =========================================================


def _stewarlde_safe_int_v21(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def fetch_stewarlde_historic_roster_v21(season):
    """Returns real, final driver standings for one completed F1 season.

    The remote catalogue supplies the driver, constructor, nationality and
    season-win fields.  A failed request returns an empty list so the game can
    show a clear retry message rather than made-up historical drivers.
    """
    season = int(season)
    endpoint = (
        'https://api.jolpi.ca/ergast/f1/'
        + str(season)
        + '/driverstandings.json?limit=100'
    )
    try:
        request = urllib.request.Request(
            endpoint,
            headers={'User-Agent': 'FormulaPaddock/2.1 (historical game)'},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
        lists = (
            payload.get('MRData', {})
            .get('StandingsTable', {})
            .get('StandingsLists', [])
        )
        if not lists:
            return []
        rows = []
        for item in lists[0].get('DriverStandings', []):
            driver = item.get('Driver', {})
            constructors = item.get('Constructors', [])
            constructor = constructors[0].get('name', '-') if constructors else '-'
            driver_id = str(driver.get('driverId', '')).strip()
            given = str(driver.get('givenName', '')).strip()
            family = str(driver.get('familyName', '')).strip()
            name = (given + ' ' + family).strip() or driver_id
            if not driver_id or not name:
                continue
            rows.append({
                'name': name,
                'code': driver_id,
                'team': constructor,
                'nation': str(driver.get('nationality', '-')).strip() or '-',
                'wins': _stewarlde_safe_int_v21(item.get('wins'), 0),
                'champion': 1 if str(item.get('position', '')) == '1' else 0,
                'standing': _stewarlde_safe_int_v21(item.get('position'), 99),
                'points': _stewarlde_safe_int_v21(item.get('points'), 0),
                'season': season,
                'photo': '',
            })
        return sorted(rows, key=lambda row: (row['standing'], row['name']))
    except Exception as error:
        log_data_error('stewarlde historical roster', error)
        return []


def stewarlde_cell_v21(value, target, numeric=False):
    if str(value) == str(target):
        return 'match', '\u2713'
    if numeric:
        try:
            return ('near', '\u2191' if int(value) < int(target) else '\u2193')
        except (TypeError, ValueError):
            pass
    return 'miss', '\u2014'


# Keep all existing page routes and engines intact; only the renderer names change.


st.markdown(r"""
<style>
/* 2.1: one quiet, fixed navigation rhythm. No animation or JavaScript. */
section[data-testid="stSidebar"] div[data-testid="stButton"]{width:100%!important;margin:0 0 10px!important;display:block!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button{width:100%!important;min-height:52px!important;padding:0 18px!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;box-sizing:border-box!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button p,section[data-testid="stSidebar"] div[data-testid="stButton"]>button div{width:100%!important;margin:0!important;text-align:left!important;justify-content:flex-start!important;line-height:1.15!important}
section[data-testid="stSidebar"] [data-testid="stExpander"]{margin:0 0 10px!important;width:100%!important;box-sizing:border-box!important}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary{min-height:52px!important;padding:0 18px!important;display:flex!important;align-items:center!important;text-align:left!important;box-sizing:border-box!important}
.games-hub-v21{border-top:5px solid #a78bfa!important;margin-bottom:18px!important}.stewarlde-brief-v21{border-left:5px solid #ff385c!important;margin:12px 0 16px!important}.stewarlde-row-v21{display:grid;grid-template-columns:1.45fr 1.2fr repeat(4,1fr);gap:8px;margin:9px 0}.stewarlde-cell-v21{min-height:62px;border:1px solid #2d435c;border-radius:10px;padding:9px;background:#111b29;position:relative}.stewarlde-cell-v21 small{display:block;color:#9aafc4;font-size:.66rem;font-weight:850;letter-spacing:.35px}.stewarlde-cell-v21 b{display:block;color:#f4f8fc;font-size:.92rem;margin-top:7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stewarlde-cell-v21 i{position:absolute;right:9px;bottom:7px;font-style:normal;font-weight:950}.stewarlde-cell-v21.match{background:#123f31;border-color:#45d991}.stewarlde-cell-v21.near{background:#4c3d16;border-color:#efc84a}.stewarlde-cell-v21.miss{background:#252c36;border-color:#465463}.stewarlde-id-v21{width:106px;height:132px;border:2px solid;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:2rem;font-weight:950;background:linear-gradient(145deg,#152743,#0f1928);letter-spacing:2px}
@media(max-width:800px){section[data-testid="stSidebar"] div[data-testid="stButton"]>button{min-height:48px!important;padding:0 14px!important}.stewarlde-row-v21{grid-template-columns:repeat(2,1fr)}.stewarlde-cell-v21{min-height:56px}.stewarlde-id-v21{width:84px;height:104px}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.2 STATIC BACKGROUND + PADDOCK DRAFT MARKET
# Deliberately CSS-only background: no animated canvas, no fixed iframe, no
# asynchronous loop. This avoids the old black-screen failure mode.
# =========================================================


# Existing routes remain unchanged and now call the repaired market and no-date Stewarlde.


st.markdown(r"""
<style>
/* 2.2 static atmosphere. It is purely CSS paint, not an animated layer. */
[data-testid="stAppViewContainer"],.stApp{background-color:#080e16!important;background-image:radial-gradient(ellipse 85% 58% at 78% 4%,rgba(23,72,106,.22),transparent 64%),radial-gradient(ellipse 70% 48% at 12% 82%,rgba(19,93,84,.13),transparent 66%),linear-gradient(rgba(85,137,174,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(85,137,174,.045) 1px,transparent 1px)!important;background-size:auto,auto,34px 34px,34px 34px!important;background-attachment:fixed!important}
[data-testid="stHeader"]{background:rgba(8,14,22,.92)!important;border-bottom:1px solid rgba(66,107,146,.18)!important}.main .block-container{position:relative;z-index:1}.draft-summary-v22{min-height:108px}.draft-brief-v22{border-left:5px solid #a78bfa!important;margin:16px 0}.draft-driver-v22{min-height:252px!important;text-align:center!important;padding:14px!important}.draft-driver-v22 .draft-driver-visual-v22{height:118px;display:flex;align-items:center;justify-content:center;margin-bottom:5px}.draft-driver-v22 .draft-driver-visual-v22 img{width:102px!important;height:116px!important;object-fit:contain!important;object-position:center bottom!important}.draft-driver-name-v22{font-size:1.06rem;font-weight:950;line-height:1.2;margin:5px 0}.draft-driver-v22+.stButton>button{margin-top:7px!important}
@media(max-width:800px){[data-testid="stAppViewContainer"],.stApp{background-size:auto,auto,26px 26px,26px 26px!important}.draft-driver-v22{min-height:224px!important}.draft-driver-v22 .draft-driver-visual-v22{height:96px}.draft-driver-v22 .draft-driver-visual-v22 img{height:96px!important;width:86px!important}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.3 STEWARDLE UNIVERSE + SAFE MOVING BACKGROUND
# The game now exposes the full 2010-2026 driver universe, while the
# target remains random. Career wins, starts and titles are never invented.
# =========================================================


def stewarlde_numeric_cell_v23(value, target):
    if value is None or target is None:
        return 'miss', '\u2014'
    return stewarlde_cell_v21(value, target, True)


def stewarlde_target_index_v23(length, mode, round_number):
    if length < 1:
        return 0
    day = datetime.date.today().toordinal()
    if mode == 'G\u00fcnl\u00fck':
        return (day * 31 + 17) % length
    return (day * 19 + int(round_number) * 37 + 11) % length


st.markdown(r"""
<style>
/* A light CSS-only motion layer. No canvas, iframe, or positioned overlay. */
@keyframes paddock-grid-drift-v23{0%{background-position:0 0,0 0,0 0,0 0}50%{background-position:0 0,0 0,22px 16px,-22px -16px}100%{background-position:0 0,0 0,0 0,0 0}}
@media (prefers-reduced-motion:no-preference){[data-testid="stAppViewContainer"],.stApp{animation:paddock-grid-drift-v23 34s ease-in-out infinite!important}}
.stewarlde-stat-v23{min-height:118px!important;border-top:5px solid #52d6ff!important;background:linear-gradient(145deg,rgba(17,34,55,.96),rgba(12,21,34,.96))!important}.stewarlde-brief-v23{border-left:5px solid #ff385c!important;margin:16px 0 18px!important;background:linear-gradient(120deg,rgba(20,34,54,.96),rgba(14,24,38,.96))!important}.stewarlde-row-v23{display:grid;grid-template-columns:1.45fr 1.2fr repeat(4,1fr);gap:8px;margin:10px 0}.stewarlde-cell-v23{min-height:68px;border:1px solid #2d435c;border-radius:11px;padding:10px;background:#111b29;position:relative;box-shadow:inset 0 1px 0 rgba(255,255,255,.035)}.stewarlde-cell-v23 small{display:block;color:#9cb5d0;font-size:.67rem;font-weight:900;letter-spacing:.42px;text-transform:uppercase}.stewarlde-cell-v23 b{display:block;color:#f2f5f8;font-size:.96rem;margin-top:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stewarlde-cell-v23 i{position:absolute;right:10px;bottom:8px;font-style:normal;font-size:1rem;font-weight:950}.stewarlde-cell-v23.match{background:linear-gradient(145deg,#123f31,#103528);border-color:#45d991}.stewarlde-cell-v23.near{background:linear-gradient(145deg,#4c3d16,#392e13);border-color:#efc84a}.stewarlde-cell-v23.miss{background:linear-gradient(145deg,#29313d,#232a34);border-color:#4b5a69}
@media(max-width:900px){.stewarlde-row-v23{grid-template-columns:repeat(2,1fr)}.stewarlde-stat-v23{min-height:100px!important}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.4 GAME ENGINE STABILITY PATCH
# Canonical driver identities, source-only career statistics and one shared
# game HUD. This block intentionally does not touch FastF1/replay routes.
# =========================================================


# Jolpica IDs are only used to ask the historical source for a driver's own
# results. If an ID is unavailable, the game shows an honest dash -- never a
# guessed career total.


st.markdown(r"""
<style>
/* Game engine 2.4: static, shared cards only. No JavaScript or page overlay. */
.game-stat-v24{min-height:112px!important;border-top:5px solid #52d6ff!important}.game-brief-v24{border-left:5px solid #ff385c!important;margin:17px 0!important}.game-card-v24{min-height:158px!important;margin-top:10px}.game-card-title-v24{font-size:1.3rem;font-weight:950;margin-top:7px;color:#f6f9ff}.game-result-v24{margin-top:16px}.stewarlde-cell-v23{min-width:0}@media(max-width:900px){.game-stat-v24{min-height:95px!important}.game-card-v24{min-height:142px!important}.game-card-title-v24{font-size:1.12rem}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.5 STEWARDLE CAREER RECORD PATCH
# A seventh, source-verified "first GP year" field. No guessed career data.
# =========================================================


def stewarlde_date_cell_v25(value, target):
    """Match exact debut dates; otherwise indicate whether the target is earlier/later."""
    if not value or not target:
        return False, ''
    if value == target:
        return True, '✓'
    return 'near', '↑' if str(target) > str(value) else '↓'


def stewarlde_profile_v25(driver, stats, colour):
    photo = safe_external_url(driver.get('photo', ''))
    visual = (
        f"<img src='{html_lib.escape(photo, quote=True)}' alt='{html_lib.escape(driver['name'])}' "
        "style='width:108px;height:138px;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\">"
        if photo else
        f"<div class='stewarlde-id-v21' style='border-color:{colour};color:{colour}'>{html_lib.escape(''.join(piece[:1] for piece in str(driver['name']).split()[-2:]).upper())}</div>"
    )
    def text(value):
        return str(value) if value is not None else 'Kaynakta yok'
    return (
        f"<div class='hud-card game-result-v24' style='border-left:5px solid {colour}'>"
        "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>" + visual +
        "<div><div class='hud-label'>DOĞRU CEVAP</div>" +
        f"<div style='font-size:1.58rem;font-weight:950;color:{colour};margin-top:4px'>{html_lib.escape(driver['name'])}</div>" +
        f"<div class='driver-meta' style='margin-top:7px'>{html_lib.escape(driver['team'])} · {html_lib.escape(driver['nation'])}</div>" +
        f"<div class='history-copy' style='margin-top:8px'>Kariyer galibiyeti: {text(stats.get('wins'))} · Dünya şampiyonluğu: {stats.get('titles', 0)} · GP startı: {text(stats.get('starts'))} · F1’e giriş tarihi: {text(stats.get('first_gp_date'))}</div>" +
        "</div></div></div>"
    )


def render_stewarlde_v25():
    _game_shell(
        "Stewardle",
        "2010–2026 F1 pilot havuzu · doğrulanmış galibiyet, şampiyonluk, GP startı ve ilk GP yılı bulmacası.",
        "#ff385c",
    )
    mode = st.radio('Oyun modu', ['Günlük', 'Sınırsız'], horizontal=True, key='stewarlde_mode_v25')
    state_key = 'stewarlde_state_v25'
    day_key = datetime.date.today().isoformat()
    if state_key not in st.session_state:
        st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': 1, 'guesses': [], 'finished': False}
    game = st.session_state[state_key]
    if game.get('mode') != mode or (mode == 'Günlük' and game.get('day') != day_key):
        game = {'mode': mode, 'day': day_key, 'round': 1, 'guesses': [], 'finished': False}
        st.session_state[state_key] = game

    with st.spinner('Tekil tarihî pilot havuzu hazırlanıyor...'):
        drivers = fetch_stewarlde_universe_v24()
    if not drivers:
        st.error('Tarihî pilot verisi şu an alınamadı. Oyun veri uydurmaz; bağlantı geldiğinde tekrar dene.')
        if st.button('Yeniden dene', key='stewarlde_retry_v25'):
            fetch_stewarlde_universe_v24.clear()
            fetch_stewarlde_historic_roster_v21.clear()
            st.rerun()
        return

    target = drivers[stewarlde_target_index_v23(len(drivers), mode, game['round'])]
    cards = st.columns(3)
    values = [
        ('TEKİL PİLOT HAVUZU', f"{len(drivers)} pilot", 'Aynı kişi yalnızca bir kez listelenir'),
        ('OYUN MODU', mode, 'Günlük hedef veya sınırsız tur'),
        ('TAHMİN HAKKI', f"{max(0, 6-len(game['guesses']))} / 6", f"Tur {game['round']}"),
    ]
    for col, (label, value, note) in zip(cards, values):
        with col:
            st.markdown(f"<div class='hud-card game-stat-v24'><div class='hud-label'>{label}</div><div class='hud-value'>{html_lib.escape(value)}</div><div class='driver-meta'>{html_lib.escape(note)}</div></div>", unsafe_allow_html=True)

    st.markdown(
        "<div class='hud-card game-brief-v24'><div class='hud-label'>STEWARDLE // F1 KARİYER BULMACASI</div>"
        "<div class='history-copy' style='margin-top:7px'>2010–2026 döneminde yarışmış pilotu altı tahminde bul. Yeşil doğru cevabı, sarı hedefin daha yüksek veya düşük olduğunu, gri ise eşleşme olmadığını gösterir.</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['identity'] not in used]
        pick = st.selectbox('Pilot tahminin', options, format_func=lambda item: f"{item['name']} — {item['team']} ({item['latest_season']})", key=f"stewarlde_pick_v25_{mode}_{game['round']}_{len(game['guesses'])}")
        if st.button('Tahmini gönder', type='primary', width='stretch', key=f"stewarlde_submit_v25_{mode}_{game['round']}_{len(game['guesses'])}"):
            game['guesses'].append(pick['identity'])
            game['finished'] = pick['identity'] == target['identity'] or len(game['guesses']) >= 6
            st.session_state[state_key] = game
            st.rerun()

    lookup = {driver['identity']: driver for driver in drivers}
    target_stats = stewarlde_stats_v25(target) if game['guesses'] else None
    if game['guesses']:
        rows = []
        for identity in game['guesses']:
            guess = lookup.get(identity)
            if not guess:
                continue
            stats = stewarlde_stats_v25(guess)
            cells_data = [
                ('Pilot', guess['name'], guess['identity'] == target['identity'], ''),
                ('Takım', guess['team'], guess['team'] == target['team'], ''),
                ('Ülke', guess['nation'], guess['nation'] == target['nation'], ''),
                ('Galibiyet', stats['wins'], *stewarlde_numeric_cell_v23(stats['wins'], target_stats['wins'])),
                ('Şampiyonluk', stats['titles'], *stewarlde_numeric_cell_v23(stats['titles'], target_stats['titles'])),
                ('GP startı', stats['starts'], *stewarlde_numeric_cell_v23(stats['starts'], target_stats['starts'])),
                ('F1’e giriş', stats['first_gp_date'], *stewarlde_date_cell_v25(stats['first_gp_date'], target_stats['first_gp_date'])),
            ]
            cells = []
            for label, value, status, hint in cells_data:
                state = 'match' if status is True or status == 'match' else 'near' if status == 'near' else 'miss'
                display = value if value is not None else '—'
                cells.append(f"<div class='stewarlde-cell-v23 {state}'><small>{html_lib.escape(label)}</small><b>{html_lib.escape(str(display))}</b><i>{html_lib.escape(str(hint))}</i></div>")
            rows.append("<div class='stewarlde-row-v25'>" + ''.join(cells) + '</div>')
        st.markdown("<div class='stewarlde-table-v23'>" + ''.join(rows) + '</div>', unsafe_allow_html=True)

    if game['finished']:
        won = bool(game['guesses']) and game['guesses'][-1] == target['identity']
        if won:
            st.success(f"Doğru cevap: {target['name']}. {len(game['guesses'])}/6 tahminde buldun.")
        else:
            st.error(f"Bu tur bitti. Doğru cevap: {target['name']} ({target['team']}).")
        colour = team_colour(target['team']) if target['team'] in TEAM_DIRECTORY_2026 else '#52d6ff'
        st.markdown(stewarlde_profile_v25(target, target_stats or {}, colour), unsafe_allow_html=True)
        if mode == 'Sınırsız':
            if st.button('Yeni rastgele pilot', key=f"stewarlde_next_v25_{game['round']}", width='stretch'):
                st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'] + 1, 'guesses': [], 'finished': False}
                st.rerun()
        elif st.button('Günlük tahminleri temizle', key=f"stewarlde_reset_v25_{day_key}"):
            st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'], 'guesses': [], 'finished': False}
            st.rerun()


render_stewarlde = render_stewarlde_v25


@st.cache_data(show_spinner=False)
def _load_stewarlde_database_v29():
    """Load the complete bundled game database; Cloud never needs one request per driver."""
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'stewardle_drivers.json')
    with open(data_path, 'r', encoding='utf-8') as source:
        rows = json.load(source)
    required = {'identity', 'api_code', 'name', 'team', 'nation', 'latest_season',
                'titles', 'wins', 'starts', 'first_gp_date'}
    clean = [row for row in rows if required.issubset(row) and row.get('identity') and row.get('name')]
    if len(clean) < 80:
        raise ValueError('Stewardle database is incomplete')
    return sorted(clean, key=lambda item: str(item['name']).casefold())


def fetch_stewarlde_universe_v24():
    return _load_stewarlde_database_v29()


def stewarlde_stats_v25(driver):
    return {
        'wins': driver.get('wins'),
        'titles': int(driver.get('titles', 0)),
        'starts': driver.get('starts'),
        'first_gp_date': driver.get('first_gp_date'),
    }


st.markdown(r"""
<style>
.stewarlde-row-v25{display:grid;grid-template-columns:1.25fr 1.15fr repeat(5,minmax(94px,1fr));gap:8px;margin:10px 0}
@media(max-width:1080px){.stewarlde-row-v25{grid-template-columns:repeat(2,1fr)}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.6 REPLAY + NAVIGATION HUD PATCH
# Focused patch: keeps the existing pages and data engines intact.
# =========================================================


def _replay_overlay_v26(payload):
    """Keep track annotations available even when a circuit has sparse metadata."""
    overlay = payload.get('overlay') if isinstance(payload.get('overlay'), dict) else {}
    overlay.setdefault('sectors', [])
    overlay.setdefault('corners', [])
    overlay.setdefault('brakes', [])
    overlay.setdefault('straights', [])
    overlay.setdefault('pit', [
        {'fraction': 0.985, 'label': 'PIT IN (şematik)'},
        {'fraction': 0.025, 'label': 'PIT OUT (şematik)'},
    ])
    payload['overlay'] = overlay
    _replay_fastest_laps_v37(payload)
    if 'events' not in payload:
        payload['events'] = _build_race_events_v37(payload)
    return payload


def _replay_fastest_laps_v37(payload):
    """Her aracın en hızlı turu + seansın en hızlı turu (mor). 1. tur hariç
    tutulur (duran start süreyi şişirir). Payload'a car['fastest'] ve
    payload['fastest_lap'] eklenir."""
    try:
        best_overall = None
        for car in payload.get('cars') or []:
            laps = car.get('laps') or []
            car_best = None
            for lp in laps:
                lap_no = int(lp.get('lap', 0))
                if lap_no <= 1:
                    continue
                dur = float(lp.get('end', 0)) - float(lp.get('start', 0))
                if dur <= 20:          # geçersiz / pit turu
                    continue
                if car_best is None or dur < car_best['seconds']:
                    car_best = {'lap': lap_no, 'seconds': round(dur, 3)}
            car['fastest'] = car_best
            if car_best and (best_overall is None or car_best['seconds'] < best_overall['seconds']):
                best_overall = {'code': car.get('code', ''), 'lap': car_best['lap'], 'seconds': car_best['seconds']}
        payload['fastest_lap'] = best_overall
    except Exception as error:
        log_data_error('replay fastest laps v37', error)
        payload['fastest_lap'] = None


def _build_race_events_v37(payload):
    """Yarış tekrarı zaman çizgisi için otomatik anlatı işaretleri.

    Payload'da zaten bulunan sıra + pit + lastik verisinden türetilir; yeni
    veri kaynağı yok. Dönüş: t (saniye) sırasına göre
    [{'t','lap','kind','text','code','colour'}].
    """
    try:
        cars = payload.get('cars') or []
        total_laps = int(payload.get('total_laps') or 1)
        if not cars:
            return []

        def _compound_at(car, lap_no):
            for lp in car.get('laps', []):
                if int(lp.get('lap', 0)) == lap_no:
                    value = str(lp.get('compound', '') or '').upper().strip()
                    return '' if value in ('', '-', 'NAN', 'UNKNOWN', 'NONE') else value
            return ''

        def _pos_at(car, lap_no):
            for lp in car.get('laps', []):
                if int(lp.get('lap', 0)) == lap_no:
                    return _race_int(lp.get('position'))
            return None

        events = [{'t': 0.0, 'lap': 1, 'kind': 'start',
                   'text': 'Lights out — yarış başladı', 'code': '', 'colour': '#ffffff'}]

        # --- lider değişimleri ---
        leader_prev = None
        for lap_no in range(1, total_laps + 1):
            leaders = sorted(
                ((_pos_at(c, lap_no) or 99, c) for c in cars),
                key=lambda pair: pair[0],
            )
            if not leaders:
                continue
            _, leader = leaders[0]
            code = leader.get('code', '')
            if leader_prev and code and code != leader_prev:
                # o turun bitiş anını yakala
                t = None
                for lp in leader.get('laps', []):
                    if int(lp.get('lap', 0)) == lap_no:
                        t = float(lp.get('end', 0))
                        break
                events.append({'t': t if t is not None else lap_no / total_laps * float(payload.get('total_seconds', total_laps)),
                               'lap': lap_no, 'kind': 'lead',
                               'text': f"{code} liderliği aldı", 'code': code,
                               'colour': leader.get('colour', '#f4d35e')})
            if code:
                leader_prev = code

        # --- pit stopları + undercut/overcut ---
        pit_list = []   # (t, car, lap, new_compound)
        for car in cars:
            for pe in car.get('pit_events', []):
                lap_no = int(pe.get('lap', 0))
                new_c = _compound_at(car, lap_no + 1) or _compound_at(car, lap_no)
                pit_list.append((float(pe.get('start', 0)), car, lap_no, new_c))
        pit_list.sort(key=lambda x: x[0])

        for t, car, lap_no, new_c in pit_list:
            code = car.get('code', '')
            tail = f" → {new_c}" if new_c else ""
            kind, extra = 'pit', ''
            # undercut/overcut: aynı pencerede pit yapan bir rakiple sıra değişimi
            for t2, rival, lap2, _c2 in pit_list:
                if rival is car or abs(lap2 - lap_no) > 4:
                    continue
                before = _pos_at(car, min(lap_no, lap2) - 1)
                after = _pos_at(car, max(lap_no, lap2) + 2)
                rb = _pos_at(rival, min(lap_no, lap2) - 1)
                ra = _pos_at(rival, max(lap_no, lap2) + 2)
                if None in (before, after, rb, ra):
                    continue
                # car rakibin önüne geçtiyse ve daha erken pit yaptıysa -> undercut
                if before > rb and after < ra:
                    extra = f" — {rival.get('code','')} üzerine {'undercut' if lap_no < lap2 else 'overcut'}"
                    kind = 'undercut'
                    break
            events.append({'t': t, 'lap': lap_no, 'kind': kind,
                           'text': f"{code} pit{tail}{extra}", 'code': code,
                           'colour': car.get('colour', '#b79cff')})

        # --- yarış dışı kalanlar ---
        for car in cars:
            if not car.get('retired'):
                continue
            laps = car.get('laps', [])
            if not laps:
                continue
            last = laps[-1]
            events.append({'t': float(last.get('end', 0)), 'lap': int(last.get('lap', 0)),
                           'kind': 'dnf', 'text': f"{car.get('code','')} yarış dışı",
                           'code': car.get('code', ''), 'colour': '#ff5c5c'})

        # --- seansın en hızlı turu (mor) ---
        fl = payload.get('fastest_lap')
        if fl and fl.get('code'):
            m, sec = divmod(float(fl['seconds']), 60)
            for car in cars:
                if car.get('code') != fl['code']:
                    continue
                for lp in car.get('laps', []):
                    if int(lp.get('lap', 0)) == int(fl['lap']):
                        events.append({'t': float(lp.get('end', 0)), 'lap': int(fl['lap']), 'kind': 'fl',
                                       'text': f"{fl['code']} en hızlı tur — {int(m)}:{sec:06.3f}",
                                       'code': fl['code'], 'colour': '#b06cff'})
                        break

        events.sort(key=lambda e: (e['t'], e['lap']))
        # aynı ana düşen çok yakın olayları seyrelt (min 1.5 sn ara, start hariç)
        pruned, last_t = [], -99
        for e in events:
            if e['kind'] == 'start' or e['t'] - last_t >= 1.5 or e['kind'] in ('lead', 'dnf'):
                pruned.append(e)
                last_t = e['t']
        return pruned[:60]
    except Exception as error:
        log_data_error('race events v37', error)
        return []


@st.cache_data(ttl=21600, show_spinner=False)
def _race_replay_payload_raw_v36(year, event_name):
    """Yalnızca BAŞARILI paket önbelleğe alınır. Veri hazır değilse / geçici
    hata varsa `RuntimeError` firlatir (önbelleğe girmez), böylece yarıştan
    hemen sonra 'hazır değil' cevabı 6 saat donup kalmaz."""
    def _apply_season_portraits(payload):
        # Kaynak ne olursa olsun (OpenF1 veya FastF1) sağ paneldeki portre
        # SEÇİLEN SEZONA ait olsun — o yılın takım tulumuyla.
        for car in (payload.get('cars') or []):
            profile = car.get('profile')
            if isinstance(profile, dict):
                season_photo = _season_headshot_url_v35(year, profile.get('name') or car.get('code'))
                if season_photo:
                    profile['photo'] = season_photo
        return payload

    openf1_payload = openf1_fallback.build_race_replay(int(year), str(event_name))
    if isinstance(openf1_payload, dict) and openf1_payload.get('ok'):
        valid, reason = validate_stable_replay_payload(openf1_payload)
        if valid:
            payload = _replay_overlay_v26(openf1_payload)
            payload['replay_source'] = 'OpenF1 doğrulanmış tur, konum, sıra, pit ve lastik kayıtları.'
            payload['version'] = '3.2-openf1-fast'
            return _apply_season_portraits(payload)
        openf1_reason = reason
    else:
        openf1_reason = openf1_payload.get('reason', '') if isinstance(openf1_payload, dict) else ''

    payload = _build_stable_race_replay_payload_v36(year, event_name)
    if not isinstance(payload, dict) or not payload.get('ok'):
        fastf1_reason = payload.get('reason', '') if isinstance(payload, dict) else ''
        raise RuntimeError(' · '.join(item for item in (openf1_reason, fastf1_reason) if item)
                           or 'Yarış tekrar paketi henüz hazır değil.')
    payload = _replay_overlay_v26(payload)
    payload['version'] = '3.2-fastf1-fallback'
    payload['replay_source'] = 'FastF1 doğrulanmış tur, sıra, pit ve lastik kayıtları.'
    return _apply_season_portraits(payload)


def build_stable_race_replay_payload(year, event_name):
    """OpenF1 hızlı geçmişi önce, ağır FastF1 yalnızca yedek."""
    try:
        return _race_replay_payload_raw_v36(year, event_name)
    except Exception as error:
        log_data_error('stable race replay', error)
        return {'ok': False, 'reason': str(error)}


def stable_race_replay_html(payload):
    """Canvas-only replay HUD with an explicit schematic pit lane.

    The track is a clean FastF1 telemetry lap. Pit entry/exit timestamps are
    verified session data, but their exact coordinates are not part of the
    public lap telemetry. The off-track lane is therefore visibly labelled
    *schematic* instead of being presented as GPS.
    """
    packed = fp_ui.json_for_script(_replay_overlay_v26(dict(payload)))
    return r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}.r{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#11161f,#09101a)}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-size:14px;font-weight:950;letter-spacing:.1em}.sub{font-size:11px;color:#91a8c0;margin-top:5px}.badge{border:1px solid #365170;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.legend{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.key{border:1px solid #334d69;border-radius:99px;padding:5px 8px;font-size:10px;font-weight:850;color:#bcd0e4;background:#101d2f}.key i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}.key[title]{cursor:help}.key em{font-style:normal;color:#8ea4bc;font-weight:700}.grid{display:grid;grid-template-columns:minmax(0,1fr) 292px;gap:12px;margin-top:12px}.map{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#07090d 74%);overflow:hidden}.map canvas{width:100%;height:510px;display:block}.panel{border:1px solid #2c425d;border-radius:11px;background:#11161f;padding:12px}.hero{border-bottom:1px solid #2b4058;padding:0 0 10px;margin-bottom:8px;min-height:74px}.hero b{font-size:21px;color:var(--team)}.hero small{display:block;color:#a9bbcd;margin-top:5px}.hero img{float:right;width:65px;height:82px;object-fit:contain;object-position:right bottom;margin:-8px -4px -2px 8px}.stat{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #26394f;font-size:12px;gap:8px}.stat span{color:#92a7bc}.pit{color:#ffd46b}.on{color:#81e6ac}
.tyrehud{margin:11px 0 3px}.tyrehud [title],.stat [title]{cursor:help}
.tyrehead{display:flex;align-items:center;gap:9px}
.tcompound{width:30px;height:30px;border-radius:7px;display:flex;align-items:center;justify-content:center;font:900 14px ui-monospace,Consolas,monospace;color:#0a121c;flex:0 0 auto;box-shadow:inset 0 0 0 1px rgba(255,255,255,.25)}
.tmeta{flex:1;min-width:0}.tmeta b{font:900 13px Inter,Arial,sans-serif;letter-spacing:.04em}
.tmeta small{display:block;color:#8ea4bc;font-size:10px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tpct{font:900 15px ui-monospace,Consolas,monospace}
.tprog{position:relative;height:13px;border-radius:6px;background:#0a121c;box-shadow:inset 0 0 0 1px #29405a;overflow:hidden;margin:9px 0 7px}
.tprog i{display:block;height:100%;transition:width .25s linear}
.tprog-lap{position:absolute;right:6px;top:50%;transform:translateY(-50%);font:800 8px ui-monospace,Consolas,monospace;color:#eef4fa;mix-blend-mode:difference;pointer-events:none}
.tstrip{display:flex;height:8px;border-radius:3px;overflow:hidden;box-shadow:inset 0 0 0 1px #29405a}
.tstripseg{border-right:1px solid rgba(6,10,16,.55);opacity:.45}
.tstripseg.cur{opacity:1;box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.7)}
.tstripseg:last-child{border-right:0}
.tstriplab{display:flex;justify-content:space-between;gap:8px;font:700 8.5px ui-monospace,Consolas,monospace;color:#7f97ac;margin-top:5px}
.tstriplab span:last-child{color:#c2d4e6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.controls,.strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}
.evwrap{margin-top:12px}
.evbar{position:relative;height:20px;margin:4px 0 2px}
.evbar::before{content:"";position:absolute;left:0;right:0;top:9px;height:2px;background:#26394f}
.evbar i{position:absolute;top:4px;width:11px;height:11px;margin-left:-5.5px;border-radius:50%;
  background:#0e1b2d;box-shadow:inset 0 0 0 2px currentColor;cursor:pointer;transition:transform .1s ease}
.evbar i:hover{transform:scale(1.35)}
.evbar i.hit{background:currentColor}
.evbar i.play{box-shadow:inset 0 0 0 2px currentColor,0 0 0 3px rgba(255,255,255,.25)}
.evbar .evhead{position:absolute;top:-2px;width:2px;height:22px;background:#fff;margin-left:-1px;
  box-shadow:0 0 5px rgba(255,255,255,.7);z-index:3;pointer-events:none}
.evnow{min-height:16px;font:700 11px Inter,Arial,sans-serif;color:#dbe6f0;letter-spacing:.01em}
.evnow b{color:#f4d35e}.evnow .lap{color:#8ea4bc;font-family:ui-monospace,Consolas,monospace;font-size:10px;margin-right:6px}
.evlist{margin-top:7px;max-height:96px;overflow-y:auto;font:600 10.5px Inter,Arial,sans-serif;line-height:1.5}
.evlist button{display:block;width:100%;text-align:left;background:none;border:0;color:#9db1c8;padding:2px 0;cursor:pointer;border-left:2px solid transparent;padding-left:7px}
.evlist button:hover{color:#eef4fa}
.evlist button.on{color:#eef4fa;border-left-color:#f4d35e}
.evlist button .lap{color:#7f97ac;font-family:ui-monospace,Consolas,monospace;margin-right:6px}.btn,.pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#f2f5f8;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.pilot{border-left:4px solid var(--team);font-size:11px}.pilot.active{background:#1c3049;box-shadow:0 0 0 1px var(--team) inset}.slider{accent-color:#ff4051;flex:1;min-width:135px}.clock{font:900 12px ui-monospace,Consolas,monospace}.note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.grid{grid-template-columns:1fr}.map canvas{height:390px}}
</style></head><body><div class="r"><div class="top"><div><div class="title">RACE CONTROL // VERIFIED REPLAY</div><div class="sub" id="sub"></div></div><div class="badge">● DOĞRULANMIŞ YARIŞ AKIŞI</div></div><div class="legend"><span class="key" title="Düzlükte düşük sürtünme bölgesi. 2024 ve öncesinde yayında buna DRS bölgesi deniyordu."><i style="background:#45c8ff"></i>Straight Mode <em>(≈ DRS)</em></span><span class="key" title="Öndeki araca yakınken ekstra elektrik gücü kullanılabilen bölge — geçiş şansı yüksek. Yayın diliyle push-to-pass / ERS hücum."><i style="background:#71e6a1"></i>Overtake Mode <em>(≈ ERS hücum)</em></span><span class="key" title="Pilotun pite girip çıktığı yaklaşık konum."><i style="background:#b79cff"></i>Pit giriş / çıkış</span><span class="key" title="Pit yolu koordinatı resmî olarak yayımlanmaz; bu çizgi yalnızca şematiktir."><i style="background:#ffd46b"></i>Pit şeridi (şematik)</span></div><div class="grid"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn" data-speed="1">1× Gerçek</button><button class="btn active" data-speed="6">6×</button><button class="btn" data-speed="20">20×</button><button class="btn" data-speed="60">60×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="clock" id="clock"></span></div><div class="strip" id="strip"></div><div class="evwrap"><div class="evnow" id="evnow"></div><div class="evbar" id="evbar"></div><div class="evlist" id="evlist"></div></div><div class="note">Pist: temiz FastF1 telemetrisi. Sıra, tur, lastik ve pit zamanları doğrulanmış kayıttır. Olay çizgisi bu verilerden otomatik türetilir. Pit şeridi koordinatı yayımlanmadığı için görsel şematiktir.</div></div><aside class="panel" id="panel"></aside></div></div><script>
const data=__PAYLOAD__,cars=data.cars||[],route=data.track||[],overlay=data.overlay||{},canvas=document.getElementById('track'),ctx=canvas.getContext('2d');let selected=cars[0]?.code||'',playing=true,speed=6,time=0,last=performance.now(),lastHud=0,lastKey='',view=null;const tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));const fmt=n=>{n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')};const fmtLap=x=>{if(!x||x<=0)return '—';const m=Math.floor(x/60),s=x-m*60;return m+':'+s.toFixed(3).padStart(6,'0')};const avgLap=(data.total_seconds||1)/(data.total_laps||1);
function lap(c,t){const a=c.laps||[];for(let i=0;i<a.length;i++)if(t<=a[i].end)return a[i];return a[a.length-1]||null}function pitEvent(c,t){return(c.pit_events||[]).find(e=>t>=e.start&&t<=e.end)||null}function state(c,t){const l=lap(c,t),a=c.laps||[],last=a[a.length-1],out=!!c.retired&&t>=(last?.end||0);if(!l)return{lap:0,frac:0,pos:c.grid||20,pit:false,out};const i=a.indexOf(l),previous=a[Math.max(0,i-1)]?.position||l.start_position||c.grid||20,frac=Math.max(0,Math.min(1,(t-l.start)/(l.end-l.start||1)));return{lap:l.lap,frac,pos:frac>.997?(l.position||previous):previous,pit:!out&&!!pitEvent(c,t),out}}
function point(f){const n=route.length;if(!n)return{x:0,y:0,a:0};const p=((f%1)+1)%1*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,a:Math.atan2(b[1]-a[1],b[0]-a[0])}}function visual(c,t){const s=state(c,t),start=Math.max(0,1-Math.min(1,t/4)),grid=((c.grid||1)-1)*.0013*start;return point(s.frac-grid)}
function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=30,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
function mark(f,label,col){const q=xy(point(f),view);ctx.fillStyle=col;ctx.beginPath();ctx.arc(q[0],q[1],4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#eaf4ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)}function zone(z,label,col){if(!Number.isFinite(z.start)||!Number.isFinite(z.end))return;ctx.beginPath();for(let i=0;i<=28;i++){const q=xy(point(z.start+(z.end-z.start)*i/28),view);i?ctx.lineTo(...q):ctx.moveTo(...q)}ctx.strokeStyle=col;ctx.lineWidth=6;ctx.globalAlpha=.9;ctx.stroke();ctx.globalAlpha=1;mark(z.start,label,col)}
function pitPath(){const marks=overlay.pit||[],pin=marks.find(x=>String(x.label||'').includes('IN'))?.fraction??.972,pout=marks.find(x=>String(x.label||'').includes('OUT'))?.fraction??.032,a=xy(point(pin),view),b=xy(point(pout),view),dx=b[0]-a[0],dy=b[1]-a[1],len=Math.max(1,Math.hypot(dx,dy)),nx=-dy/len,ny=dx/len,dir=(a[0]+b[0])/2<view.w/2?1:-1,offset=Math.min(58,Math.max(34,view.w*.065))*dir;return{a,b,c:[a[0]+nx*offset,a[1]+ny*offset],d:[b[0]+nx*offset,b[1]+ny*offset]}}function bez(p0,p1,p2,p3,u){const v=1-u;return{x:v*v*v*p0[0]+3*v*v*u*p1[0]+3*v*u*u*p2[0]+u*u*u*p3[0],y:v*v*v*p0[1]+3*v*v*u*p1[1]+3*v*u*u*p2[1]+u*u*u*p3[1]}}function pitPoint(event,t){const p=pitPath(),u=Math.max(0,Math.min(1,(t-event.start)/(event.end-event.start||1))),q=bez(p.a,p.c,p.d,p.b,u),q2=bez(p.a,p.c,p.d,p.b,Math.min(1,u+.012));return{x:q.x,y:q.y,a:Math.atan2(q2.y-q.y,q2.x-q.x)}}
function drawLane(){const p=pitPath();ctx.beginPath();ctx.moveTo(...p.a);ctx.bezierCurveTo(...p.c,...p.d,...p.b);ctx.strokeStyle='#ffd46b';ctx.lineWidth=4;ctx.setLineDash([7,5]);ctx.globalAlpha=.9;ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;ctx.fillStyle='#ffd46b';ctx.font='bold 9px Arial';ctx.textAlign='center';ctx.fillText('PIT LANE (şematik)',(p.c[0]+p.d[0])/2,(p.c[1]+p.d[1])/2-8)}
function car(x,y,a,c,code,chosen,pit){ctx.save();ctx.translate(x,y);ctx.rotate(-a);var s=0.92;
 ctx.fillStyle='#05080d';[[-13,-11,7,6],[-13,5,7,6],[8,-12,7,7],[8,5,7,7]].forEach(function(w){ctx.beginPath();ctx.roundRect(w[0]*s,w[1]*s,w[2]*s,w[3]*s,2);ctx.fill();});
 var g=ctx.createLinearGradient(-16*s,0,18*s,0);g.addColorStop(0,'#0b0f16');g.addColorStop(.5,c);g.addColorStop(1,'#0b0f16');ctx.fillStyle=g;
 ctx.beginPath();ctx.moveTo(19*s,0);ctx.lineTo(9*s,-4.6*s);ctx.lineTo(-10*s,-5.4*s);ctx.lineTo(-13*s,-3.6*s);ctx.lineTo(-13*s,3.6*s);ctx.lineTo(-10*s,5.4*s);ctx.lineTo(9*s,4.6*s);ctx.closePath();ctx.fill();
 ctx.fillStyle='#0c141d';ctx.beginPath();ctx.ellipse(2*s,0,5*s,3.6*s,0,0,7);ctx.fill();
 ctx.strokeStyle='#243444';ctx.lineWidth=1.3;ctx.beginPath();ctx.arc(3*s,0,4.6*s,-1.1,1.1);ctx.stroke();
 ctx.fillStyle='#eef4fa';ctx.fillRect(16*s,-13*s,3*s,26*s);ctx.fillStyle=c;ctx.fillRect(15*s,-13*s,1.5*s,26*s);
 ctx.fillStyle='#eef4fa';ctx.fillRect(-18*s,-10*s,3*s,20*s);
 if(pit){ctx.strokeStyle='#ffd44b';ctx.lineWidth=2;ctx.strokeRect(-20*s,-13*s,41*s,26*s);}
 if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.4;ctx.strokeRect(-22*s,-15*s,45*s,30*s);}
 ctx.restore();ctx.fillStyle=chosen?'#fff':c;ctx.font='900 10px Inter,Arial,sans-serif';ctx.textAlign='center';ctx.fillText(code,x,y-16)}
function draw(){if(!view||!route.length)return;ctx.clearRect(0,0,view.w,view.h);ctx.strokeStyle='#8094ad';ctx.globalAlpha=.72;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},view);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;(overlay.straights||[]).forEach((z,i)=>zone(z,i?'OM · OVERTAKE MODE':'SM · STRAIGHT MODE',i?'#71e6a1':'#45c8ff'));mark(0,'START / FINISH','#fff');(overlay.sectors||[]).forEach(x=>mark(x.fraction,x.label,x.colour||'#f4d35e'));(overlay.pit||[]).forEach(x=>mark(x.fraction,x.label,'#b79cff'));drawLane();cars.forEach(c=>{const s=state(c,time);if(s.out)return;const e=pitEvent(c,time);let q,p;if(e){p=pitPoint(e,time);q=[p.x,p.y]}else{p=visual(c,time);q=xy(p,view)}car(q[0],q[1],p.a,c.colour,c.code,c.code===selected,!!e)})}
function order(){return cars.filter(c=>!state(c,time).out).sort((a,b)=>{const x=state(a,time),y=state(b,time);return x.pos-y.pos||(y.lap+y.frac)-(x.lap+x.frac)})}function lastPit(c){const e=(c.pit_events||[]).filter(x=>x.end<=time).at(-1);return e?'Tur '+e.lap:'Henüz yok'}
const TYRE_LIFE={SOFT:19,MEDIUM:29,HARD:42,INTERMEDIATE:26,WET:32};
function stintSegments(c){const laps=c.laps||[];const segs=[];let cur=null;for(const lp of laps){const comp=String(lp.compound||'').toUpperCase()||'?';const key=(lp.stint||0)+'|'+comp;if(!cur||cur.key!==key){cur={key:key,compound:comp,from:lp.lap,to:lp.lap};segs.push(cur);}else{cur.to=lp.lap;}}return segs;}
function stintSummary(c){const s=stintSegments(c);return s.length?s.map(x=>(x.compound[0]||'?')+(x.to-x.from+1)).join(' '):'—';}
function wearColour(w){return w<55?'#4ade80':w<80?'#f5c33b':'#ff5c5c';}
function currentSet(c,curLap){const segs=stintSegments(c);if(!segs.length)return null;
  let i=segs.findIndex(s=>curLap>=s.from&&curLap<=s.to);
  if(i<0)i=(curLap<segs[0].from)?0:segs.length-1;
  const seg=segs[i],age=Math.max(1,Math.min(seg.to,curLap)-seg.from+1),life=TYRE_LIFE[seg.compound]||28;
  return {compound:seg.compound,setNo:i+1,count:segs.length,age,life,from:seg.from,to:seg.to,
          wear:Math.max(0,Math.min(100,age/life*100))};}
function tyreHud(c,curLap){
  const s=currentSet(c,curLap);
  if(!s)return '<div class="tyrehud"><div class="tmeta"><small>Lastik verisi yok</small></div></div>';
  const col=tyres[s.compound]||'#8aa0b6', wc=wearColour(s.wear), total=Math.max(1,data.total_laps||1);
  let strip='';
  stintSegments(c).forEach((sg,i)=>{const w=(sg.to-sg.from+1)/total*100;
    strip+='<div class="tstripseg'+(i===s.setNo-1?' cur':'')+'" style="width:'+w.toFixed(2)+'%;background:'+(tyres[sg.compound]||'#8aa0b6')+'" title="'+sg.compound+' · Tur '+sg.from+'-'+sg.to+'"></div>';});
  const cName={SOFT:'Soft — en hızlı, en çabuk biter',MEDIUM:'Medium — hız/ömür dengesi',HARD:'Hard — en dayanıklı, ısınması zor',INTERMEDIATE:'Intermediate — hafif ıslak pist',WET:'Wet — yoğun yağmur'}[s.compound]||s.compound;
  return '<div class="tyrehud">'
    +'<div class="tyrehead"><span class="tcompound" style="background:'+col+'" title="'+cName+'">'+(s.compound[0]||'?')+'</span>'
    +'<div class="tmeta"><b>'+s.compound+'</b><small title="Kaçıncı lastik seti ve bu sette kaç turdur olduğu. Her pit yeni bir set demektir.">SET '+s.setNo+'/'+s.count+' · '+s.age+'. tur bu sette</small></div>'
    +'<span class="tpct" style="color:'+wc+'" title="Bu setin tahmini aşınması — geçen tur / o hamurun tipik ömrü. %100\'e yaklaştıkça pit yaklaşır.">%'+Math.round(s.wear)+'</span></div>'
    +'<div class="tprog" title="Lastik kullanıldıkça (turlar ilerledikçe) soldan sağa dolar."><i style="width:'+s.wear.toFixed(1)+'%;background:linear-gradient(90deg,'+col+' 30%,'+wc+')"></i>'
    +'<span class="tprog-lap">TUR '+s.from+'–'+Math.min(s.to,curLap)+'</span></div>'
    +'<div class="tstrip">'+strip+'</div>'
    +'<div class="tstriplab"><span title="Tüm yarışın lastik planı. Her blok bir stint, arasındaki çizgi pit stop.">STRATEJİ</span><span>'+stintSummary(c)+'</span></div>'
    +'</div>';
}
var EV=(data.events||[]).filter(function(e){return e&&isFinite(e.t);});
var evBuilt=false, evLastKey='';
function seekTo(t){ time=Math.max(0,Math.min(t,data.total_seconds||0)); playing=false;
  var pb=document.getElementById('play'); if(pb) pb.textContent='▶ Oynat';
  lastHud=0; draw(); update(); }
function buildEvents(){
  if(evBuilt || !EV.length) return; evBuilt=true;
  var T=data.total_seconds||1;
  var bar=document.getElementById('evbar'), listEl=document.getElementById('evlist');
  bar.innerHTML='<div class="evhead" id="evhead"></div>' + EV.map(function(e,i){
    return '<i data-i="'+i+'" style="left:'+(e.t/T*100).toFixed(2)+'%;color:'+e.colour+'" title="Tur '+e.lap+' · '+esc(e.text)+'"></i>';
  }).join('');
  listEl.innerHTML = EV.map(function(e,i){
    return '<button data-i="'+i+'"><span class="lap">T'+e.lap+'</span>'+esc(e.text)+'</button>';
  }).join('');
  bar.querySelectorAll('i').forEach(function(el){ el.onclick=function(){ seekTo(EV[+el.dataset.i].t); }; });
  listEl.querySelectorAll('button').forEach(function(b){ b.onclick=function(){ seekTo(EV[+b.dataset.i].t); }; });
}
function syncEvents(){
  if(!EV.length) return;
  var T=data.total_seconds||1, head=document.getElementById('evhead');
  if(head) head.style.left=(time/T*100).toFixed(2)+'%';
  var curIdx=-1;
  for(var i=0;i<EV.length;i++){ if(EV[i].t<=time+0.01) curIdx=i; else break; }
  var key=curIdx+'|'+(document.getElementById('evbar').children.length);
  if(key===evLastKey) return; evLastKey=key;
  var dots=document.getElementById('evbar').querySelectorAll('i');
  dots.forEach(function(el){ var i=+el.dataset.i; el.classList.toggle('hit',EV[i].t<=time+0.01); el.classList.toggle('play',i===curIdx); });
  var buttons=document.getElementById('evlist').querySelectorAll('button');
  buttons.forEach(function(b){ b.classList.toggle('on',+b.dataset.i===curIdx); });
  if(curIdx>=0 && buttons[curIdx]) buttons[curIdx].scrollIntoView({block:'nearest'});
  var now=document.getElementById('evnow');
  if(curIdx>=0){ var e=EV[curIdx];
    now.innerHTML='<span class="lap">TUR '+e.lap+'</span>'+(e.kind==='undercut'?'<b>':'')+esc(e.text)+(e.kind==='undercut'?'</b>':'');
  } else { now.textContent=''; }
}
function update(){const now=performance.now();if(now-lastHud<220)return;lastHud=now;const list=order(),key=list.map(c=>c.code+state(c,time).pos+state(c,time).lap).join('|')+selected;if(key!==lastKey){lastKey=key;document.getElementById('strip').innerHTML=list.map((c,i)=>{const s=state(c,time);let gap='LİDER';if(i>0){const a=list[i-1],sa=state(a,time);const dp=((sa.lap-1)+(sa.frac||0))-((s.lap-1)+(s.frac||0));gap=dp>=0.9?'+'+Math.round(dp)+' tur':'+'+(dp*avgLap).toFixed(1)+'s';}const flm=(data.fastest_lap&&data.fastest_lap.code===c.code)?' <s class="flm">FL</s>':'';return`<button class="pilot ${c.code===selected?'active':''}" style="--team:${c.colour}" data-c="${c.code}">P${s.pos} · ${c.code} · ${gap}${flm}</button>`}).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.c;lastKey='';lastHud=0;update()})}const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),compound=(l?.compound||'—').toUpperCase(),p=pitEvent(c,time),move=(c.grid&&s.pos)?c.grid-s.pos:0,wear=Math.max(8,100-Math.round(100*(s.frac||0)));const profile=c.profile||{},photo=profile.photo?`<img src="${esc(profile.photo)}" alt="" referrerpolicy="no-referrer" onerror="this.style.display='none'">`:'';document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').style.setProperty('--tyre',tyres[compound]||'#9db1c8');document.getElementById('panel').innerHTML=`<div class="hero">${photo}<b>${esc(profile.name||c.code)} · P${s.pos}</b><small>${esc(c.team)} · ${esc(profile.flag||'')} ${esc(c.code)}</small></div><div class="stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="stat"><span>Başlangıç → bitiş</span><b>P${c.grid||'—'} → P${c.final_position||'—'}</b></div><div class="stat"><span>Pozisyon değişimi</span><b>${move>0?'↑ '+move:move<0?'↓ '+Math.abs(move):'→ 0'} sıra</b></div>${tyreHud(c,s.lap)}<div class="stat"><span>En hızlı tur</span><b>${c.fastest?fmtLap(c.fastest.seconds)+' · T'+c.fastest.lap:'—'}${(data.fastest_lap&&data.fastest_lap.code===c.code)?' <s class="flm">MOR</s>':''}</b></div><div class="stat"><span>Son pit</span><b>${lastPit(c)}</b></div><div class="stat"><span>Pit durumu</span><b class="${p?'pit':'on'}">${p?'PIT LANE':'PİSTTE'}</b></div>`;document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds);syncEvents()}
let raf=0,lastPaint=0;function startLoop(){if(!raf){last=performance.now();raf=requestAnimationFrame(frame)}}function frame(now){raf=0;const dt=Math.min(.05,Math.max(0,(now-last)/1000));last=now;if(!playing)return;time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan'}if(now-lastPaint>=33||!playing){lastPaint=now;draw();update()}if(playing)raf=requestAnimationFrame(frame)}function resize(){const r=canvas.getBoundingClientRect(),d=Math.min(1.5,devicePixelRatio||1);canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);view=transform();draw();lastHud=0;update()}document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat';if(playing)startLoop();else{draw();update()}};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{time=Number(e.target.value)/1000*data.total_seconds;playing=false;document.getElementById('play').textContent='▶ Oynat';lastHud=0;draw();update()};document.addEventListener('visibilitychange',()=>{if(document.hidden){playing=false;document.getElementById('play').textContent='▶ Oynat'}});document.getElementById('sub').textContent=(data.event||'Formula 1')+' · '+data.total_laps+' tur · doğrulanmış yarış saati';window.addEventListener('resize',resize);buildEvents();resize();startLoop();
setInterval(function(){if(playing&&performance.now()-last>120){frame(performance.now());}},50);
</script></div></body></html>""".replace('__PAYLOAD__', packed)


st.markdown(r"""
<style>
/* One calm navigation grid. This does not use animation, fixed overlays or JS. */
section[data-testid="stSidebar"] div[data-testid="stButton"]{width:100%!important;margin:0 0 9px!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button{width:100%!important;min-height:50px!important;padding:0 16px!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;border:1px solid #315578!important;border-left:4px solid #3fa9ff!important;border-radius:10px!important;background:linear-gradient(90deg,#10213a,#0d192a)!important;box-shadow:0 7px 18px rgba(0,0,0,.14)!important;font-weight:850!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button:hover{border-left-color:#6ee7ff!important;background:linear-gradient(90deg,#142b49,#101e33)!important;transform:translateX(1px)}
section[data-testid="stSidebar"] [data-testid="stExpander"]{margin:0 0 9px!important;border:1px solid #315578!important;border-radius:10px!important;background:#0f1d30!important;overflow:hidden}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary{min-height:50px!important;padding:0 16px!important;display:flex!important;align-items:center!important;font-weight:850!important}
.hud-card.game-stat-v24,.hud-card.game-brief-v24,.hud-card.game-result-v24,.hud-card.draft-driver-v22{box-shadow:0 14px 28px rgba(0,0,0,.18)!important;border-radius:14px!important;background:linear-gradient(145deg,#111d31,#11161f)!important}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.7 CAREER COMPARISON CENTRE
# This deliberately replaces only the comparison page. It does not load a
# FastF1 session, so a current/unfinished session can never distort a career
# profile. Career results are read from Jolpica's historical F1 database and
# cached locally; unavailable source fields are shown as "—", never guessed.
# =========================================================


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _career_api_json_v27(endpoint):
    """Kariyer/karşılaştırma kaynağının kısa-timeout okuyucusu. Ham JSON 12 saat
    cache'lenir; hata `st.cache_data` tarafından cache'lenmez (istisna yükselir),
    böylece geçici ağ sorunu pilotu 'veri yok' durumunda bırakmaz."""
    request = urllib.request.Request(
        endpoint,
        headers={'User-Agent': 'FormulaPaddock/2.7 (career comparison)'},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        return json.loads(response.read().decode('utf-8'))


def _career_number_v27(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _career_float_v27(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _career_races_v27(api_code):
    """Load all individual race-result rows, paging only when the API needs it."""
    base = 'https://api.jolpi.ca/ergast/f1/drivers/' + urllib.parse.quote(api_code) + '/results.json'
    first = _career_api_json_v27(base + '?limit=1000')
    race_table = first.get('MRData', {}).get('RaceTable', {})
    races = list(race_table.get('Races', []) or [])
    total = _career_number_v27(first.get('MRData', {}).get('total'))
    if total is None:
        total = len(races)
    # Jolpica may cap a requested page size. Finish the data set instead of
    # displaying partial career points, fastest laps or constructor history.
    offset = len(races)
    while offset < total:
        page = _career_api_json_v27(base + f'?limit=1000&offset={offset}')
        extra = page.get('MRData', {}).get('RaceTable', {}).get('Races', []) or []
        if not extra:
            raise RuntimeError('Career result pagination returned an empty page.')
        races.extend(extra)
        offset += len(extra)
    return races, total


def _career_text_v27(value, suffix=''):
    if value is None:
        return '—'
    if isinstance(value, float):
        text = f'{value:,.1f}'.rstrip('0').rstrip('.')
    else:
        text = f'{int(value):,}' if isinstance(value, int) else str(value)
    return text + suffix


st.markdown(r"""
<style>
/* Career Comparison 2.7: equal visual columns with career-only data. */
.career-panel-v27{min-height:492px;border:1px solid #2b4664;border-top:5px solid var(--team);border-radius:16px;padding:18px;background:linear-gradient(145deg,#111d31,#0b1524);box-shadow:0 14px 30px rgba(0,0,0,.18)}
.career-hero-v27{min-height:118px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #2a4059;padding-bottom:13px}.career-hero-v27 img{width:92px;height:116px;object-fit:contain;object-position:center bottom;flex:0 0 auto}.career-hero-v27 h3{margin:5px 0 4px;color:var(--team);font-size:1.45rem;line-height:1.15}.career-hero-v27 p{margin:0;color:#a8c0d7;font-size:.86rem}
.career-metrics-v27{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:14px}.career-metric-v27{min-height:70px;padding:10px;border:1px solid #29435f;border-radius:10px;background:#0d1829}.career-metric-v27 span,.career-teams-v27 small{display:block;color:#91abd0;font-size:.63rem;font-weight:900;letter-spacing:1.05px}.career-metric-v27 b{display:block;color:#f2f5f8;font-size:1.16rem;margin-top:7px}
.career-teams-v27{margin-top:14px;padding-top:12px;border-top:1px solid #2a4059}.career-teams-v27>div{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.career-teams-v27 span{border:1px solid color-mix(in srgb,var(--team) 48%,#2e4a68);border-left:3px solid var(--team);border-radius:99px;padding:5px 8px;color:#c9d9e7;background:#122137;font-size:.74rem;font-weight:760}.career-source-v27{margin-top:13px;color:#83a0bd;font-size:.72rem}
@media(max-width:800px){.career-panel-v27{min-height:0;margin-bottom:12px}.career-hero-v27 img{width:76px;height:98px}.career-metrics-v27{grid-template-columns:repeat(2,minmax(0,1fr))}.career-metric-v27{min-height:63px}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.8 VERIFIED CAREER DATA + F1 CAR DUEL HUD
# Career totals now only use the result row belonging to the selected driver.
# A source timeout renders an unavailable value instead of a guessed statistic.
# =========================================================


def _career_result_for_driver_v28(race, api_code):
    """Return only this driver's result record from one historical race."""
    wanted = str(api_code or '').casefold()
    for result in race.get('Results', []) or []:
        result_code = str((result.get('Driver', {}) or {}).get('driverId', '')).casefold()
        if result_code == wanted:
            return result
    return None


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _career_verified_rows_v28(api_code):
    """Load, de-duplicate and verify historical result rows for one driver.
    Ayrıştırılmış satırlar 12 saat cache'lenir — pilotlar sayfası, karşılaştırma
    ve derin istatistik aynı çekimi paylaşır."""
    races, _ = _career_races_v27(api_code)
    unique = {}
    for race in races:
        result = _career_result_for_driver_v28(race, api_code)
        if not result:
            continue
        season = str(race.get('season', '')).strip()
        round_number = str(race.get('round', '')).strip()
        key = (season, round_number)
        if key not in unique:
            unique[key] = (race, result)
    return list(unique.values())


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def get_driver_career_stats_v28(driver_code):
    """Verified career totals for a selected driver.

    Every calculation is made from result records whose driverId matches the
    requested driver.  `poles` is deliberately named GRID P1: it is the
    verified race-start pole count available in the same result data source.
    """
    code = str(driver_code or '').upper().strip()
    empty = {
        'verified': False,
        'wins': None,
        'podiums': None,
        'poles': None,
        'fastest_laps': None,
        'starts': None,
        'points': None,
        'teams': [],
        'titles': CAREER_TITLES_V27.get(code, 0),
    }
    api_code = STEWARDLE_ACTIVE_API_IDS_V24.get(code, '')
    if not api_code:
        return empty
    try:
        rows = _career_verified_rows_v28(api_code)
        if not rows:
            return empty

        wins = podiums = poles = fastest_laps = 0
        points = 0.0
        teams = []
        for _race, result in rows:
            position = _career_number_v27(result.get('position'))
            grid = _career_number_v27(result.get('grid'))
            wins += int(position == 1)
            podiums += int(position in {1, 2, 3})
            poles += int(grid == 1)
            fastest = result.get('FastestLap', {}) or {}
            fastest_laps += int(str(fastest.get('rank', '')).strip() == '1')
            points += _career_float_v27(result.get('points')) or 0.0
            team_name = str((result.get('Constructor', {}) or {}).get('name', '')).strip()
            if team_name and team_name not in teams:
                teams.append(team_name)

        return {
            'verified': True,
            'wins': wins,
            'podiums': podiums,
            'poles': poles,
            'fastest_laps': fastest_laps,
            'starts': len(rows),
            'points': points,
            'teams': teams,
            'titles': CAREER_TITLES_V27.get(code, 0),
        }
    except Exception as error:
        log_data_error('verified career comparison source', error)
        return empty


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def get_driver_deep_stats_v32(driver_code, scope="season", season="2026"):
    """Sampiyona Merkezi pilot derin-istatistik karti icin.

    scope='season' -> yalniz o sezon; 'career' -> tum kariyer + pist bazinda galibiyet.
    Her sayi, pilotun kendi sonuc satirindan hesaplanir; kaynak yoksa verified=False.
    """
    code = str(driver_code or '').upper().strip()
    api_code = STEWARDLE_ACTIVE_API_IDS_V24.get(code, '')
    if not api_code:
        return {'verified': False}
    try:
        rows = _career_verified_rows_v28(api_code)
    except Exception as error:
        log_data_error('driver deep stats', error)
        return {'verified': False}
    if not rows:
        return {'verified': False}
    if scope == "season":
        rows = [(r, res) for r, res in rows if str(r.get('season', '')).strip() == str(season)]
    if not rows:
        return {'verified': True, 'empty': True}

    wins = podiums = poles = fastest_laps = dnf = 0
    points = 0.0
    finishes, grids, circuit_wins = [], [], {}
    for race, res in rows:
        pos = _career_number_v27(res.get('position'))
        grid = _career_number_v27(res.get('grid'))
        status = str(res.get('status', '')).strip()
        points += _career_float_v27(res.get('points')) or 0.0
        if grid == 1:
            poles += 1
        if grid:
            grids.append(grid)
        is_dnf = is_dnf_status(status)
        if is_dnf:
            dnf += 1
        elif pos:
            finishes.append(pos)
            if pos == 1:
                wins += 1
                circ = str((race.get('Circuit', {}) or {}).get('circuitName', '')).strip()
                if circ:
                    circuit_wins[circ] = circuit_wins.get(circ, 0) + 1
            if pos in (1, 2, 3):
                podiums += 1
        if str((res.get('FastestLap', {}) or {}).get('rank', '')).strip() == '1':
            fastest_laps += 1

    return {
        'verified': True, 'empty': False, 'races': len(rows), 'points': round(points, 1),
        'wins': wins, 'podiums': podiums, 'poles': poles, 'fastest_laps': fastest_laps, 'dnf': dnf,
        'best': min(finishes) if finishes else None,
        'worst': max(finishes) if finishes else None,
        'avg_grid': round(sum(grids) / len(grids), 1) if grids else None,
        'circuit_wins': sorted(circuit_wins.items(), key=lambda pair: -pair[1])[:6],
    }


def _driver_titles_v33(api_code, code=''):
    """Dogrulanmis sampiyonluk sayisi: once paketli oyun veritabani, sonra sabit harita."""
    try:
        for row in _load_stewarlde_database_v29():
            if str(row.get('api_code', '')).strip() == str(api_code or '').strip():
                return int(row.get('titles', 0) or 0)
    except Exception:
        pass
    return CAREER_TITLES_V27.get(str(code or '').upper().strip(), 0)


_CAREER_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'career_cache')


def _career_cache_file(api_code):
    safe = re.sub(r'[^a-z0-9]+', '_', str(api_code or '').lower()).strip('_') or 'x'
    return os.path.join(_CAREER_CACHE_DIR, safe + '.json')


def _career_disk_ttl(api_code):
    """Emekli pilotun kariyeri değişmez -> uzun; aktif pilot -> 12 saat."""
    try:
        latest = _career_number_v27(_stewarlde_row_by_api_v33(api_code).get('latest_season'))
        current = datetime.datetime.now(datetime.timezone.utc).year
        if latest and latest < current - 1:
            return 60 * 60 * 24 * 45
    except Exception:
        pass
    return 60 * 60 * 12


def _career_profile_from_disk(api_code):
    """Diskteki hazır profil (ağ yok). TTL aşılmışsa None döner."""
    try:
        with open(_career_cache_file(api_code), encoding='utf-8') as handle:
            payload = json.load(handle)
        if time.time() - float(payload.get('saved', 0)) > _career_disk_ttl(api_code):
            return None
        profile = payload.get('profile')
        if isinstance(profile, dict) and profile.get('ok'):
            return profile
    except (OSError, ValueError, TypeError):
        pass
    return None


def _career_profile_to_disk(api_code, profile):
    if not (isinstance(profile, dict) and profile.get('ok')):
        return
    try:
        os.makedirs(_CAREER_CACHE_DIR, exist_ok=True)
        target = _career_cache_file(api_code)
        tmp = f"{target}.{os.getpid()}.tmp"
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'saved': time.time(), 'api': str(api_code), 'profile': profile},
                      handle, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError as error:
        log_data_error('career cache write', error)


def get_driver_full_profile_v33(api_code, allow_network=True):
    """Pilotlar sayfasi icin tam profil. Once disk onbellegi (aninda), sonra
    12 saatlik bellek onbellegi, en son ag. Aktarim hatasi ONBELLEGE ALINMAZ."""
    if not api_code:
        return {'ok': False}
    disk = _career_profile_from_disk(api_code)
    if disk is not None:
        return disk
    if not allow_network:
        return None
    try:
        profile = _driver_full_profile_raw_v33(api_code)
    except LookupError:
        return {'ok': False, 'empty': True}
    except Exception as error:
        log_data_error('driver full profile', error)
        return {'ok': False}
    _career_profile_to_disk(api_code, profile)
    return profile


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _driver_full_profile_raw_v33(api_code):
    rows = _career_verified_rows_v28(api_code)
    if not rows:
        raise LookupError('no verified career rows for ' + str(api_code))
    return aggregate_driver_career_v33(rows)


def aggregate_driver_career_v33(rows):
    """Ergast/Jolpica sonuç satırlarından (race, result) tam profil sözlüğü üretir.

    Saf fonksiyon — ağ yok. `rows`: [(race_dict, result_dict), ...].
    Testlerde sabit fixture ile doğrulanır.
    """
    wins = podiums = poles = fastest = dnf = 0
    points = 0.0
    finishes, grids = [], []
    circ_wins, teams_years, seasons = {}, {}, {}
    race_rows = []
    for race, res in rows:
        year = str(race.get('season', '')).strip()
        rnd = _career_number_v27(race.get('round'))
        rname = str(race.get('raceName', '')).strip()
        circ = str((race.get('Circuit', {}) or {}).get('circuitName', '')).strip()
        pos = _career_number_v27(res.get('position'))
        pos_text = str(res.get('positionText', '')).strip()
        grid = _career_number_v27(res.get('grid'))
        status = str(res.get('status', '')).strip()
        pts = _career_float_v27(res.get('points')) or 0.0
        team_name = str((res.get('Constructor', {}) or {}).get('name', '')).strip()
        is_dnf = is_dnf_status(status)

        points += pts
        if grid == 1:
            poles += 1
        if grid:
            grids.append(grid)
        if str((res.get('FastestLap', {}) or {}).get('rank', '')).strip() == '1':
            fastest += 1
        if is_dnf:
            dnf += 1
        elif pos:
            finishes.append(pos)
            if pos == 1:
                wins += 1
                if circ:
                    circ_wins[circ] = circ_wins.get(circ, 0) + 1
            if pos in (1, 2, 3):
                podiums += 1

        if team_name and year:
            yr = int(year)
            lo, hi = teams_years.get(team_name, (yr, yr))
            teams_years[team_name] = (min(lo, yr), max(hi, yr))

        s = seasons.setdefault(year, {'year': year, 'team': team_name, 'points': 0.0,
                                      'wins': 0, 'races': 0, 'best': None})
        s['points'] += pts
        s['races'] += 1
        s['team'] = team_name or s['team']
        if pos == 1:
            s['wins'] += 1
        if pos and (s['best'] is None or pos < s['best']):
            s['best'] = pos

        race_rows.append({
            'year': year, 'round': rnd or 0, 'race': rname, 'circuit': circ,
            'grid': grid, 'pos': (pos_text if pos_text else '—'),
            'points': round(pts, 1), 'status': status, 'dnf': is_dnf,
        })

    race_rows.sort(key=lambda r: (r['year'], r['round']), reverse=True)
    season_list = sorted(seasons.values(), key=lambda s: s['year'], reverse=True)
    for s in season_list:
        s['points'] = round(s['points'], 1)

    return {
        'ok': True, 'starts': len(rows), 'points': round(points, 1),
        'wins': wins, 'podiums': podiums, 'poles': poles, 'fastest_laps': fastest, 'dnf': dnf,
        'best': min(finishes) if finishes else None,
        'worst': max(finishes) if finishes else None,
        'avg_grid': round(sum(grids) / len(grids), 1) if grids else None,
        'first_season': season_list[-1]['year'] if season_list else '',
        'last_season': season_list[0]['year'] if season_list else '',
        'teams': sorted(((n, y[0], y[1]) for n, y in teams_years.items()), key=lambda t: t[1]),
        'circuit_wins': sorted(circ_wins.items(), key=lambda p: -p[1]),
        'seasons': season_list,
        'races': race_rows,
    }


_NATION_FLAG_V33 = {
    'british': 'gb', 'english': 'gb', 'german': 'de', 'dutch': 'nl', 'spanish': 'es', 'french': 'fr',
    'italian': 'it', 'finnish': 'fi', 'australian': 'au', 'mexican': 'mx', 'canadian': 'ca',
    'monegasque': 'mc', 'thai': 'th', 'japanese': 'jp', 'american': 'us', 'brazilian': 'br',
    'danish': 'dk', 'chinese': 'cn', 'austrian': 'at', 'argentine': 'ar', 'argentinian': 'ar',
    'new zealander': 'nz', 'belgian': 'be', 'swiss': 'ch', 'polish': 'pl', 'russian': 'ru',
    'swedish': 'se', 'portuguese': 'pt', 'indian': 'in', 'venezuelan': 've', 'colombian': 'co',
}


def _stewarlde_row_by_api_v33(api_code):
    """Paketli oyun veritabanından tek pilot satırı (ağ yok)."""
    try:
        target = str(api_code or '').strip()
        for row in _load_stewarlde_database_v29():
            if str(row.get('api_code', '')).strip() == target:
                return row
    except Exception:
        pass
    return {}


def _driver_quick_header_html(name, code, nation, number, team, colour, titles, bundle):
    """Ağ beklemeden çizilen üst bilgi: kimlik + şampiyonluk / galibiyet / yarış /
    ilk GP (paketli veriden). Derin döküm bunun altında yüklenir."""
    flag = _NATION_FLAG_V33.get(str(nation or '').strip().lower(), '')
    flag_img = (f"<img src='https://flagcdn.com/w40/{flag}.png' alt='' "
                f"style='width:24px;height:16px;object-fit:cover;border-radius:2px'>") if flag else ''
    num = str(number or '').lstrip('#').strip()
    first_gp = str(bundle.get('first_gp_date', '') or '')[:4]

    def cell(lbl, val, cls=''):
        show = val if (val is not None and str(val) != '') else '—'
        return f"<div><s>{html_lib.escape(lbl)}</s><b class='{cls}'>{html_lib.escape(str(show))}</b></div>"

    cells = (cell('Şampiyonluk', titles or 0, 'g') + cell('Galibiyet', bundle.get('wins'), 'g')
             + cell('Yarış', bundle.get('starts')) + cell('İlk GP', first_gp))
    no_html = f"<span class='no'>#{html_lib.escape(num)}</span>" if num else ""
    sub = html_lib.escape(str(nation or '')) + (' · ' + html_lib.escape(str(team)) if team else '')
    css = (
        ".dqh{background:var(--fp-bg-2);background-image:var(--fp-dot);background-size:var(--fp-dot-size);"
        "clip-path:polygon(12px 0,100% 0,100% calc(100% - 12px),calc(100% - 12px) 100%,0 100%,0 12px);"
        "box-shadow:inset 0 0 0 1px var(--fp-line),inset 3px 0 0 %COL%;padding:15px 18px;margin:2px 0 12px}"
        ".dqh .t{display:flex;align-items:center;gap:13px}"
        ".dqh .cd{font:700 32px/1 var(--fp-f-x,var(--fp-f-display));color:%COL%}"
        ".dqh .nm{font:800 20px/1 var(--fp-f-display);text-transform:uppercase;letter-spacing:.02em}"
        ".dqh .sb{font:500 11px var(--fp-f-mono);color:var(--fp-text-dim);margin-top:4px;display:flex;align-items:center;gap:7px}"
        ".dqh .no{font:700 14px var(--fp-f-mono);color:%COL%;margin-left:auto;flex:0 0 auto}"
        ".dqh .r{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--fp-line-soft);margin-top:13px}"
        ".dqh .r>div{background:var(--fp-bg-2);padding:9px 11px}"
        ".dqh .r s{font:700 8.5px var(--fp-f-display);letter-spacing:.12em;text-transform:uppercase;color:var(--fp-text-mute);text-decoration:none}"
        ".dqh .r b{display:block;font:700 15px var(--fp-f-mono);margin-top:5px;color:var(--fp-text)}"
        ".dqh .r b.g{color:var(--fp-cyan)}"
        "@media(max-width:520px){.dqh .r{grid-template-columns:repeat(2,1fr)}}"
    ).replace("%COL%", colour)
    return (
        f"<style>{css}</style>"
        f"<div class='dqh'><div class='t'><span class='cd'>{html_lib.escape(str(code))}</span>"
        f"<span><span class='nm'>{html_lib.escape(str(name))}</span>"
        f"<span class='sb'>{flag_img}{sub}</span></span>{no_html}</div>"
        f"<div class='r'>{cells}</div></div>"
    )


def driver_profile_header_html(name, code, nation, number, prof, colour, titles=0):
    flag = _NATION_FLAG_V33.get(str(nation or '').strip().lower(), '')
    flag_img = (f"<img src='https://flagcdn.com/w40/{flag}.png' alt='' "
                f"style='width:26px;height:17px;object-fit:cover;border-radius:2px;vertical-align:middle'>") if flag else ''
    span = f"{prof.get('first_season', '')}–{prof.get('last_season', '')}" if prof.get('ok') else '—'
    last_team = prof['seasons'][0]['team'] if prof.get('ok') and prof.get('seasons') else '—'
    num = str(number or '').lstrip('#').strip()
    no_html = f"<span class='no'>#{html_lib.escape(num)}</span>" if num else ''
    meta = " · ".join(p for p in [html_lib.escape(str(nation or '')), span, html_lib.escape(str(last_team))] if p and p != '—')

    def s(label, value, cls=''):
        return (f"<div><s>{html_lib.escape(label)}</s>"
                f"<b class='{cls}'>{html_lib.escape(str(value if value is not None else '—'))}</b></div>")

    if not prof.get('ok'):
        _msg = ("Bu pilotun henüz doğrulanmış bir Formula 1 yarış başlangıcı yok."
                if prof.get('empty') else
                "Bu pilot için doğrulanmış kariyer kaydı şu an alınamadı. Birazdan tekrar dene.")
        grid = f"<div style='padding:16px;color:#9fb0c0'>{_msg}</div>"
    else:
        best = f"P{prof['best']}" if prof.get('best') else '—'
        worst = f"P{prof['worst']}" if prof.get('worst') else '—'
        grid = "<div class='pg'>" + ''.join([
            s('Yarış', prof['starts']), s('Puan', prof['points']),
            s('Galibiyet', prof['wins'], 'g'), s('Podyum', prof['podiums']),
            s('Pole (grid P1)', prof['poles']), s('En Hızlı Tur', prof['fastest_laps']),
            s('Yarış Dışı', prof['dnf'], 'r'), s('En İyi / En Kötü', f"{best} / {worst}"),
            s('Ort. Grid', prof['avg_grid']), s('Şampiyonluk', titles, 'g'),
        ]) + "</div>"

    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .ph{{border:1px solid #26313f;border-radius:6px;background:linear-gradient(160deg,#161d28,#11161f);overflow:hidden}}
      .pt{{display:flex;align-items:center;gap:16px;padding:18px 20px;border-bottom:1px solid #26313f;border-left:4px solid {colour}}}
      .pt .c{{font-family:'Antonio','Saira Condensed',sans-serif;font-weight:700;font-size:40px;color:{colour};line-height:1}}
      .pt .w{{flex:1;min-width:0}}
      .pt .w b{{font:800 22px 'Saira Condensed',sans-serif;text-transform:uppercase;letter-spacing:.02em;display:block}}
      .pt .w span{{display:block;font-size:12px;color:#9fb0c0;margin-top:3px}}
      .pt .no{{font-family:'JetBrains Mono',monospace;font-weight:700;color:{colour};font-size:16px}}
      .pg{{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:#1b2330}}
      .pg > div{{background:#11161f;padding:12px 14px}}
      .pg s{{font:700 9.5px 'Saira Condensed',sans-serif;letter-spacing:.11em;text-transform:uppercase;color:#8a9bb0;text-decoration:none}}
      .pg b{{display:block;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:17px;margin-top:6px}}
      .pg b.g{{color:#38e1d0}} .pg b.r{{color:#e10600}}
      @media(max-width:640px){{.pg{{grid-template-columns:repeat(3,1fr)}}}}
      @media(max-width:400px){{.pg{{grid-template-columns:repeat(2,1fr)}}}}
      .ph .cap{{font:700 9.5px 'JetBrains Mono',monospace;letter-spacing:.18em;text-transform:uppercase;color:#63748a;padding:12px 16px 0}}
    </style>
    <div class="ph">
      <div class="cap">Doğrulanmış kariyer · {html_lib.escape(span)}</div>
      {grid}
    </div>
    """


def _num_v33(value):
    try:
        return ('%g' % round(float(value), 1))
    except (TypeError, ValueError):
        return '0'


def _pos_chip_v33(pos_text, dnf):
    """Yaris sonucu cipi -> (arka, yazi, etiket)."""
    if dnf:
        return ('#2a1418', '#ff5f6d', 'DNF')
    t = str(pos_text or '').strip()
    if not t or t in ('—', '-'):
        return ('#161d28', '#9fb0c0', '—')
    if t.isdigit():
        p = int(t)
        if p == 1:
            return ('#3a2f00', '#ffd100', 'P1')
        if p <= 3:
            return ('#08301f', '#38e1d0', 'P%d' % p)
        if p <= 10:
            return ('#0c2036', '#5cc8ff', 'P%d' % p)
        return ('#161d28', '#c9d6e2', 'P%d' % p)
    return ('#161d28', '#c9d6e2', html_lib.escape(t))


def driver_seasons_hud_html(seasons, colour):
    """Sezon dokumu -> timing-tower seridi."""
    if not seasons:
        return ''
    pmax = max((s.get('points') or 0) for s in seasons) or 1
    rows = ''
    for s in seasons:
        pts = s.get('points') or 0
        best = ('P%d' % s['best']) if s.get('best') else '—'
        rows += (
            "<div class='row'>"
            f"<span class='yr'>{html_lib.escape(str(s.get('year', '')))}</span>"
            f"<span class='tm'>{html_lib.escape(str(s.get('team', '') or '—'))}</span>"
            f"<span class='n'>{s.get('races', 0)}</span>"
            f"<span class='bw'><i style='width:{round(pts / pmax * 100)}%'></i>"
            f"<em>{_num_v33(pts)}</em></span>"
            f"<span class='n g'>{s.get('wins', 0)}</span>"
            f"<span class='n'>{best}</span>"
            "</div>"
        )
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .tt{{border:1px solid #26313f;border-radius:6px;overflow:hidden;background:#11161f}}
      .hd,.row{{display:grid;grid-template-columns:60px 1.5fr 40px 1.1fr 42px 50px;gap:10px;align-items:center;padding:9px 15px}}
      .hd{{background:#161d28;border-bottom:1px solid #26313f}}
      .hd span{{font:700 9.5px 'Saira Condensed',sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#8a9bb0}}
      .row{{border-bottom:1px solid #1b2330}}
      .row:last-child{{border-bottom:0}}
      .row:nth-child(odd){{background:#131a24}}
      .yr{{font:700 13px 'JetBrains Mono',monospace;color:{colour}}}
      .tm{{font:600 12px 'Saira',sans-serif;color:#c9d6e2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .n{{font:700 12px 'JetBrains Mono',monospace;text-align:right}}
      .n.g{{color:#38e1d0}}
      .bw{{position:relative;height:16px;background:#07090d;border-radius:3px;overflow:hidden}}
      .bw i{{position:absolute;left:0;top:0;bottom:0;background:{colour};opacity:.45}}
      .bw em{{position:absolute;right:6px;top:0;line-height:16px;font:700 10px 'JetBrains Mono',monospace;font-style:normal}}
      @media(max-width:440px){{.hd,.row{{grid-template-columns:40px 1fr 30px 1fr 30px 38px;gap:6px;padding:8px 10px}}.tm{{font-size:11px}}}}
    </style>
    <div class="tt">
      <div class="hd"><span>Sezon</span><span>Takım</span><span>Yrş</span><span>Puan</span><span>Gal</span><span>En İyi</span></div>
      {rows}
    </div>
    """


def driver_races_hud_height(races):
    """driver_races_hud_html için component yüksekliği (özet bandı + kartlar)."""
    return min(1860, 132 + 66 * max(1, len(races or [])))


def driver_races_hud_html(races, colour):
    """Bir sezonun yarış-yarış sonuçları — sezon özeti bandı + okunur yarış kartları."""
    if not races:
        return ("<div style='font-family:Saira,system-ui,sans-serif;color:#8a9bb0;"
                "padding:22px;text-align:center'>Bu sezon için doğrulanmış yarış kaydı yok.</div>")

    def _p(r):
        t = str(r.get('pos', '')).strip()
        return int(t) if t.isdigit() else None

    wins = sum(1 for r in races if _p(r) == 1)
    podiums = sum(1 for r in races if _p(r) in (1, 2, 3))
    points_races = sum(1 for r in races if (r.get('points') or 0) > 0)
    dnfs = sum(1 for r in races if r.get('dnf'))
    total_pts = sum(float(r.get('points') or 0) for r in races)
    finishes = [p for p in (_p(r) for r in races) if p]
    best = min(finishes) if finishes else None

    summary_cells = "".join(
        f"<div><s>{lbl}</s><b style='color:{col}'>{val}</b></div>"
        for lbl, val, col in [
            ("Yarış", len(races), "#e8eef4"),
            ("Puan", _num_v33(total_pts), colour),
            ("Galibiyet", wins, "#ffd100" if wins else "#e8eef4"),
            ("Podyum", podiums, "#38e1d0" if podiums else "#e8eef4"),
            ("Puan biten", points_races, "#e8eef4"),
            ("Yarış dışı", dnfs, "#ff5f6d" if dnfs else "#e8eef4"),
            ("En iyi", f"P{best}" if best else "—", "#e8eef4"),
        ]
    )

    cards = ""
    for r in races:
        grid = r.get('grid') or 0
        pos_text = str(r.get('pos', '—')).strip()
        bg, fg, label = _pos_chip_v33(pos_text, r.get('dnf'))
        move = "<span class='mv eq'>grid —</span>"
        if grid and pos_text.isdigit():
            d = grid - int(pos_text)
            arrow = (f"<em class='up'>▲{d}</em>" if d > 0
                     else f"<em class='dn'>▼{-d}</em>" if d < 0
                     else "<em class='eq'>±0</em>")
            move = f"<span class='mv'>P{grid} <i>→</i> P{pos_text} {arrow}</span>"
        elif grid:
            move = f"<span class='mv'>grid P{grid}</span>"
        status = str(r.get('status', '') or '').strip()
        status_html = (f"<span class='st'>{html_lib.escape(status)}</span>"
                       if status and status.lower() not in ('finished', 'bitti') else "")
        pts = r.get('points') or 0
        pts_html = (f"<span class='pt'>{_num_v33(pts)}<i>PUAN</i></span>" if pts
                    else "" if r.get('dnf') else "<span class='pt zero'>0</span>")
        cards += (
            f"<div class='card' style='--tier:{fg}'>"
            f"<span class='rnd'>{r.get('round') or '—'}</span>"
            f"<span class='mid'><b>{html_lib.escape(str(r.get('race', '')))}</b>"
            f"<span class='ci'>{html_lib.escape(str(r.get('circuit', '') or ''))}</span></span>"
            f"<span class='right'>{move}"
            f"<span class='chip' style='background:{bg};color:{fg}'>{label}</span>"
            f"{pts_html}{status_html}</span>"
            "</div>"
        )

    return f"""
    <style>
      *{{box-sizing:border-box}}
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .wrap{{border:1px solid #26313f;border-radius:12px;overflow:hidden;background:#11161f}}
      .sum{{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:#1b2330;border-bottom:1px solid #26313f}}
      .sum>div{{background:#141b26;padding:11px 12px}}
      .sum s{{display:block;font:700 8.5px 'Saira Condensed',sans-serif;letter-spacing:.11em;text-transform:uppercase;color:#8a9bb0;text-decoration:none}}
      .sum b{{display:block;font:700 16px 'JetBrains Mono',monospace;margin-top:5px}}
      .list{{display:flex;flex-direction:column;gap:6px;padding:10px}}
      .card{{display:grid;grid-template-columns:40px 1fr auto;gap:13px;align-items:center;
        padding:10px 13px;background:#131a24;border:1px solid #222c39;border-left:3px solid var(--tier);
        border-radius:9px;transition:background .12s ease}}
      .card:hover{{background:#182130}}
      .rnd{{width:32px;height:32px;border-radius:50%;background:#0d1520;border:1px solid #263241;
        display:flex;align-items:center;justify-content:center;font:700 12px 'JetBrains Mono',monospace;color:#7c8ea0}}
      .mid{{min-width:0}}
      .mid b{{display:block;font:700 14.5px 'Saira Condensed',sans-serif;text-transform:uppercase;
        letter-spacing:.01em;color:#f2f5f8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .mid .ci{{display:block;font:500 11px 'Saira',sans-serif;color:#7f8ea0;margin-top:2px;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .right{{display:flex;align-items:center;gap:12px;justify-content:flex-end;flex-wrap:wrap}}
      .mv{{font:600 11px 'JetBrains Mono',monospace;color:#9fb0c0;white-space:nowrap}}
      .mv i{{color:#55657a;font-style:normal;padding:0 2px}}
      .mv em{{font-style:normal;font-weight:700;margin-left:3px}}
      .mv .up{{color:#38e1d0}} .mv .dn{{color:#ff8b78}} .mv .eq{{color:#6b7a8c}}
      .chip{{min-width:46px;text-align:center;font:800 12px 'JetBrains Mono',monospace;
        padding:5px 9px;border-radius:6px;letter-spacing:.02em}}
      .pt{{font:700 14px 'JetBrains Mono',monospace;color:{colour};display:flex;align-items:baseline;gap:4px}}
      .pt i{{font:700 8px 'Saira Condensed',sans-serif;font-style:normal;letter-spacing:.08em;color:#8a9bb0}}
      .pt.zero{{color:#55657a}}
      .st{{font:500 10.5px 'Saira',sans-serif;color:#c98a3f;white-space:nowrap}}
      @media(max-width:620px){{
        .sum{{grid-template-columns:repeat(4,1fr)}}
        .card{{grid-template-columns:32px 1fr;row-gap:8px}}
        .right{{grid-column:1/-1;justify-content:flex-start}}
      }}
    </style>
    <div class="wrap">
      <div class="sum">{summary_cells}</div>
      <div class="list">{cards}</div>
    </div>
    """


def _driver_deep_skeleton_html(colour):
    """Kariyer verisi ilk kez çekilirken gösterilen iskelet — sayfa anında açılır."""
    row = ("<div class='sk-row'><span class='sk-c' style='width:64px'></span>"
           "<span class='sk-c' style='flex:1'></span>"
           "<span class='sk-c' style='width:46px'></span>"
           "<span class='sk-c' style='width:38px'></span></div>")
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#8a9bb0}}
      .sk{{border:1px solid #26313f;border-radius:12px;background:#11161f;padding:16px;overflow:hidden}}
      .sk-hd{{display:flex;align-items:center;gap:10px;font:700 10px 'JetBrains Mono',monospace;
        letter-spacing:.16em;text-transform:uppercase;color:{colour};margin-bottom:14px}}
      .sk-dot{{width:9px;height:9px;border-radius:50%;background:{colour};animation:sk-pulse 1s ease-in-out infinite}}
      .sk-c{{display:inline-block;height:13px;border-radius:4px;
        background:linear-gradient(90deg,#161d28 25%,#212c3a 50%,#161d28 75%);background-size:200% 100%;
        animation:sk-shine 1.3s linear infinite}}
      .sk-row{{display:flex;gap:12px;align-items:center;padding:11px 0;border-top:1px solid #1b2330}}
      .sk-row:first-of-type{{border-top:0}}
      @keyframes sk-shine{{to{{background-position:-200% 0}}}}
      @keyframes sk-pulse{{50%{{opacity:.3}}}}
    </style>
    <div class="sk">
      <div class="sk-hd"><span class="sk-dot"></span>Kariyer kaydı okunuyor…</div>
      {row * 6}
    </div>
    """


@st.fragment
def _render_driver_deep_v39(api, name, code, nation, number, team, colour, titles, info):
    """Derin kariyer bölümü — sayfa kabuğu anında çizilir, bu parça ayrı yüklenir.
    Disk önbelleği doluysa anında; boşsa iskelet gösterilip tek seferlik ağ çekimi
    yapılır ve sadece bu parça yeniden çalışır."""
    state_key = f"_drvprof_v39_{api}"
    if state_key not in st.session_state:
        cached = get_driver_full_profile_v33(api, allow_network=False)
        if cached is not None:
            st.session_state[state_key] = cached
        else:
            _sk = st.empty()
            with _sk.container():
                render_html_hud(_driver_deep_skeleton_html(colour), height=250)
            with st.spinner(T("drivers.reading_career")):
                st.session_state[state_key] = get_driver_full_profile_v33(api)
            _sk.empty()
    prof = st.session_state[state_key]

    render_html_hud(
        driver_profile_header_html(name, code, nation or info.get('team', ''), number, prof, colour, titles),
        height=210 if prof.get('ok') else 150,
    )
    if not prof.get('ok'):
        return

    if prof.get('seasons'):
        st.write("")
        fp_ui.section_title(T("drivers.season_breakdown"))
        render_html_hud(driver_seasons_hud_html(prof['seasons'], colour),
                        height=min(760, 52 + 34 * len(prof['seasons'])))

    _cols = st.columns([1, 1])
    with _cols[0]:
        fp_ui.section_title(T("drivers.circuit_wins"))
        if prof.get('circuit_wins'):
            _top = prof['circuit_wins'][0][1] or 1
            _bars = ''.join(
                f"<div style='display:grid;grid-template-columns:150px 1fr 34px;gap:9px;align-items:center;padding:5px 0'>"
                f"<span style='font:600 12px Saira,sans-serif;color:#9fb0c0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{html_lib.escape(c)}</span>"
                f"<span style='height:6px;background:#07090d;border-radius:99px;overflow:hidden'>"
                f"<i style='display:block;height:100%;width:{round(n / _top * 100)}%;background:{colour}'></i></span>"
                f"<span style='font:700 12px JetBrains Mono,monospace;text-align:right'>×{n}</span></div>"
                for c, n in prof['circuit_wins'][:8]
            )
            st.markdown(f"<div class='hud-card' style='padding:14px 16px'>{_bars}</div>", unsafe_allow_html=True)
        else:
            fp_ui.data_state(T("drivers.no_wins_title"), T("drivers.no_wins_body"), "info")
    with _cols[1]:
        if prof.get('teams'):
            fp_ui.section_title(T("drivers.teams"))
            _tg = ''.join(
                f"<div style='display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid #1b2330'>"
                f"<b style='font:700 13px Saira Condensed,sans-serif;text-transform:uppercase;color:{team_colour(tn)}'>{html_lib.escape(tn)}</b>"
                f"<span style='font:12px JetBrains Mono,monospace;color:#9fb0c0'>{y0}{'–' + str(y1) if y1 != y0 else ''}</span></div>"
                for tn, y0, y1 in prof['teams']
            )
            st.markdown(f"<div class='hud-card' style='padding:14px 16px'>{_tg}</div>", unsafe_allow_html=True)

    st.write("")
    _years = sorted({r['year'] for r in prof['races'] if r['year']}, reverse=True)
    fp_ui.section_title(T("drivers.race_by_race"))
    _yr = st.selectbox(T("drivers.season"), _years, index=0, key=f"drivers_race_year_v39_{api}") if _years else None
    _year_races = sorted((r for r in prof['races'] if r['year'] == _yr), key=lambda r: r['round'])
    st.caption(f"{_yr} · {len(_year_races)} yarış")
    render_html_hud(driver_races_hud_html(_year_races, colour),
                    height=driver_races_hud_height(_year_races))


def _drivers_directory_v33():
    """2026 grid + tarihi pilot havuzu -> [(code, name, api, nation, number, team, colour, is_2026)]."""
    try:
        _db = {str(r.get('api_code', '')).strip(): r for r in _load_stewarlde_database_v29()}
    except Exception as error:
        log_data_error('drivers directory db', error)
        _db = {}
    out, seen = [], set()
    for team_name, team in TEAM_DIRECTORY_2026.items():
        colour = team.get('color', '#63748a')
        for name, code, number, _img in team['drivers']:
            api = STEWARDLE_ACTIVE_API_IDS_V24.get(code, str(code).lower())
            nation = str((_db.get(api) or {}).get('nation', '')).strip()
            out.append((code, name, api, nation, str(number).lstrip('#'), team_name, colour, True))
            seen.add(api)
    for api, row in _db.items():
        if not api or api in seen:
            continue
        seen.add(api)
        out.append((str(row.get('code', api)).upper()[:3] or api[:3].upper(),
                    row.get('name', api), api, str(row.get('nation', '')).strip(),
                    '', row.get('team', ''), '#8a9bb0', False))
    return out


def render_drivers_page_v33():
    fp_ui.page_header(T("page.drivers.title"), T("page.drivers.sub"), eyebrow=T("section.live"))
    directory = _drivers_directory_v33()
    by_api = {d[2]: d for d in directory}
    selected = st.session_state.get('driver_view_v33')

    if selected and selected in by_api:
        code, name, api, nation, number, team, colour, is_2026 = by_api[selected]
        if st.button(T("drivers.back"), key="drivers_back_v33"):
            st.session_state.pop('driver_view_v33', None)
            st.rerun()
        fp_ui.anchor("fp-driver-detail")
        _info = directory_driver_by_code(code)
        colour = team_colour(team) if team else colour
        _titles = _driver_titles_v33(api, code)

        # ANLIK ÜST BİLGİ — paketli veritabanından, ağ beklemeden. Galibiyet /
        # şampiyonluk / start / ilk GP hemen görünür; derin döküm altta yüklenir.
        _bundle = _stewarlde_row_by_api_v33(api)
        st.markdown(_driver_quick_header_html(name, code, nation, number, team, colour, _titles, _bundle),
                    unsafe_allow_html=True)

        fp_ui.how_to_hud(
            [
                ("Üst şerit", "kimlik + paketli veritabanından anında gelen kariyer toplamları: şampiyonluk, galibiyet, yarış, ilk GP."),
                ("Doğrulanmış kariyer", "tüm rakamlar Ergast/Jolpica yarış sonucu kayıtlarından hesaplanır; tahmin yok. İlk açılışta bir kez çekilir, sonra anında gelir."),
                ("Sezon Dökümü", "her satır bir sezon; puan çubuğu o sezonun toplam puanını kariyerin en iyi sezonuna oranlar."),
                ("Yarış-Yarış", "sezon seç; her kart bir yarış. Sol rakam = tur (round). Renkli rozet = bitiş sırası. 'P3 → P1 ▲2' = gridden bitişe kazanılan sıra."),
            ],
            legend=[
                ("#ffd100", "P1 galibiyet"), ("#38e1d0", "podyum (P2–P3)"),
                ("#5cc8ff", "puan bölgesi (P4–P10)"), ("#c9d6e2", "puan dışı"),
                ("#ff5f6d", "yarış dışı (DNF)"),
            ],
            note="Sezon üstündeki özet bandı: o yıla ait galibiyet, podyum, puan ve yarış dışı sayısı.",
        )

        _render_driver_deep_v39(api, name, code, nation, number, team, colour, _titles, _info)

        if st.session_state.pop('_scroll_driver', False):
            fp_ui.scroll_to("fp-driver-detail")
        return

    # --- DIZIN GORUNUMU ---
    only_2026 = st.toggle(T("drivers.only_2026"), value=True, key="drivers_only_2026_v33")
    q = st.text_input(T("drivers.search"), placeholder="Örn. Verstappen, HAM, Alonso", key="drivers_q_v33").strip().lower()
    shown = [d for d in directory if (d[7] or not only_2026) and (not q or q in d[1].lower() or q in d[0].lower())]
    shown.sort(key=lambda d: (not d[7], d[1]))
    st.caption(T("drivers.count", n=len(shown)))
    for i in range(0, len(shown), 3):
        cols = st.columns(3)
        for col, d in zip(cols, shown[i:i + 3]):
            code, name, api, nation, number, team, colour, is_2026 = d
            with col:
                st.markdown(
                    f"<div class='hud-card' style='border-left:3px solid {team_colour(team) if team else colour};padding:11px 13px;min-height:74px'>"
                    f"<div style='font:800 15px Saira Condensed,sans-serif;text-transform:uppercase;letter-spacing:.03em'>{html_lib.escape(name)}</div>"
                    f"<div class='driver-meta'>{html_lib.escape(code)} · {html_lib.escape(team or '—')}{' · 2026' if is_2026 else ''}</div></div>",
                    unsafe_allow_html=True,
                )
                if st.button(T("drivers.open_profile"), key=f"drv_{api}", width='stretch'):
                    st.session_state['driver_view_v33'] = api
                    st.session_state['_scroll_driver'] = True
                    st.rerun()


def driver_deep_stats_hud_html(name, code, team, stats, scope, colour):
    if not stats.get('verified'):
        return ("<div style='padding:16px;color:#9fb0c0;font-family:Saira,sans-serif'>"
                "Bu pilot için doğrulanmış kariyer verisi şu an alınamadı.</div>")
    if stats.get('empty'):
        return (f"<div style='padding:16px;color:#9fb0c0;font-family:Saira,sans-serif'>"
                f"{html_lib.escape(str(name))} icin bu sezona ait tamamlanmis yaris kaydi yok.</div>")

    def cell(label, value, cls=''):
        return (f"<div><s>{html_lib.escape(label)}</s>"
                f"<b class='{cls}'>{html_lib.escape(str(value if value is not None else '—'))}</b></div>")

    best = f"P{stats['best']}" if stats.get('best') else '—'
    worst = f"P{stats['worst']}" if stats.get('worst') else '—'
    scope_label = 'Bu Sezon' if scope == 'season' else 'Kariyer'
    grid = [
        cell(f"{scope_label} Puan", stats['points']),
        cell("Galibiyet", stats['wins'], 'g'),
        cell("Podyum", stats['podiums']),
        cell("Pole (grid P1)", stats['poles']),
        cell("Yarış Dışı (DNF)", stats['dnf'], 'r'),
        cell("En İyi Bitiş", best),
        cell("En Kotu Bitis", worst),
        cell("Ort. Grid", stats['avg_grid']),
    ]
    circ_rows = ''
    if scope == 'career' and stats.get('circuit_wins'):
        top = stats['circuit_wins'][0][1] or 1
        for circ, n in stats['circuit_wins']:
            pct = round(n / top * 100)
            circ_rows += (f"<div class='cr'><span class='cn'>{html_lib.escape(circ)}</span>"
                          f"<span class='bar'><i style='width:{pct}%'></i></span>"
                          f"<span class='cw'>×{n}</span></div>")
        circ_rows = f"<div class='circ'><h4>Pist Bazinda Galibiyet (kariyer)</h4>{circ_rows}</div>"

    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .ds{{border:1px solid #26313f;border-radius:6px;background:linear-gradient(160deg,#161d28,#11161f);overflow:hidden}}
      .dh{{display:flex;align-items:center;gap:14px;padding:15px 18px;border-bottom:1px solid #26313f;border-left:4px solid {colour}}}
      .dh .c{{font-family:'Antonio','Saira Condensed',sans-serif;font-weight:700;font-size:32px;color:{colour};line-height:1}}
      .dh .w{{flex:1}}
      .dh .w b{{font:700 18px 'Saira Condensed',sans-serif;text-transform:uppercase;letter-spacing:.02em}}
      .dh .w span{{display:block;font-size:11px;color:#9fb0c0;text-transform:uppercase;letter-spacing:.06em;margin-top:2px}}
      .g{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#1b2330}}
      .g > div{{background:#11161f;padding:12px 15px}}
      .g s{{font:700 9.5px 'Saira Condensed',sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#8a9bb0;text-decoration:none}}
      .g b{{display:block;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:19px;margin-top:6px}}
      .g b.g{{color:#38e1d0}} .g b.r{{color:#e10600}}
      .circ{{padding:13px 18px}}
      .circ h4{{font:700 10px 'Saira Condensed',sans-serif;letter-spacing:.13em;text-transform:uppercase;color:#63748a;margin-bottom:9px}}
      .cr{{display:grid;grid-template-columns:130px 1fr 40px;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid #1b2330}}
      .cr:last-child{{border-bottom:0}}
      .cr .cn{{font:600 12px 'Saira',sans-serif;color:#9fb0c0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
      .cr .bar{{height:6px;background:#07090d;border-radius:99px;overflow:hidden}}
      .cr .bar i{{display:block;height:100%;background:{colour}}}
      .cr .cw{{font:700 12px 'JetBrains Mono',monospace;text-align:right}}
      @media(max-width:440px){{.g{{grid-template-columns:repeat(2,1fr)}}.cr{{grid-template-columns:96px 1fr 34px}}}}
    </style>
    <div class="ds">
      <div class="dh"><span class="c">{html_lib.escape(str(code))}</span>
        <span class="w"><b>{html_lib.escape(str(name))}</b><span>{html_lib.escape(str(team))} · {stats['races']} yaris</span></span>
      </div>
      <div class="g">{''.join(grid)}</div>
      {circ_rows}
    </div>
    """


def _career_panel_v28(info, stats, colour):
    code = str(info.get('code', '')).upper()
    portrait = current_driver_portrait(info.get('team', ''), info.get('image', ''))
    portrait_html = (
        f"<img src='{html_lib.escape(portrait, quote=True)}' alt='{html_lib.escape(info['name'])}' "
        "onerror=\"this.style.display='none'\">"
        if portrait else ''
    )
    metric_rows = [
        ('GALİBİYET', _career_text_v27(stats.get('wins'))),
        ('DÜNYA ŞAMP.', _career_text_v27(stats.get('titles'))),
        ('POLE / GRID P1', _career_text_v27(stats.get('poles'))),
        ('EN HIZLI TUR', _career_text_v27(stats.get('fastest_laps'))),
        ('GP STARTI', _career_text_v27(stats.get('starts'))),
        ('KARİYER PUANI', _career_text_v27(stats.get('points'))),
        ('PODYUM', _career_text_v27(stats.get('podiums'))),
    ]
    metrics = ''.join(
        f"<div class='career-metric-v28'><span>{label}</span><b>{value}</b></div>"
        for label, value in metric_rows
    )
    teams = stats.get('teams', []) or []
    teams_html = ''.join(
        f"<span>{html_lib.escape(team)}</span>" for team in teams
    ) or '<span>Kaynakta doğrulanamadı</span>'
    source = (
        'Jolpica tarihî yarış kayıtları · sürücü kimliği doğrulandı'
        if stats.get('verified')
        else 'Kaynak geçici olarak yanıt vermedi · değer gösterilmedi'
    )
    return (
        f"<section class='career-panel-v28' style='--team:{colour}'>"
        "<div class='career-hero-v28'>"
        f"{portrait_html}<div><div class='hud-label'>KARİYER DOSYASI</div>"
        f"<h3>{html_lib.escape(info['name'])}</h3>"
        f"<p>{html_lib.escape(code)} · {html_lib.escape(info.get('team', '—'))}</p></div></div>"
        f"<div class='career-metrics-v28'>{metrics}</div>"
        "<div class='career-teams-v28'><small>YARIŞTIĞI TAKIMLAR</small>"
        f"<div>{teams_html}</div></div>"
        f"<div class='career-source-v28'>● {html_lib.escape(source)}</div>"
        "</section>"
    )


# =========================================================
# FAZ 2 · #11 — "BU PİSTTE" KARİYER KAFA KAFAYA
# get_driver_full_profile_v33 (disk önbellekli) kayıtlarından; ağ yok.
# =========================================================

def _circuit_career_v41(prof, circuit):
    """Bir pilotun tek pistteki kariyer özeti — prof['races'] filtrelenerek."""
    out = {'races': 0, 'wins': 0, 'podiums': 0, 'poles': 0, 'dnf': 0,
           'points': 0.0, 'best': None, 'by_year': {}}
    finishes = []
    for race in prof.get('races', []) if isinstance(prof, dict) else []:
        if str(race.get('circuit', '')).strip() != circuit:
            continue
        out['races'] += 1
        out['points'] += float(race.get('points') or 0)
        grid = race.get('grid')
        if grid == 1:
            out['poles'] += 1
        pos_text = str(race.get('pos', '')).strip()
        pos = int(pos_text) if pos_text.isdigit() else None
        if race.get('dnf'):
            out['dnf'] += 1
        elif pos is not None:
            finishes.append(pos)
            if pos == 1:
                out['wins'] += 1
            if pos <= 3:
                out['podiums'] += 1
            if out['best'] is None or pos < out['best']:
                out['best'] = pos
        year = race.get('year')
        if year:
            out['by_year'][str(year)] = {'grid': grid, 'pos': pos_text or '—', 'dnf': bool(race.get('dnf'))}
    out['avg'] = round(sum(finishes) / len(finishes), 1) if finishes else None
    return out


def circuit_h2h_v41(prof_a, prof_b, circuit):
    """İki pilotun aynı pistteki kariyer kafa-kafaya dökümü + ortak yıllar."""
    a = _circuit_career_v41(prof_a, circuit)
    b = _circuit_career_v41(prof_b, circuit)
    shared = sorted(set(a['by_year']) & set(b['by_year']), reverse=True)
    h2h_a = h2h_b = 0
    duel = []
    for year in shared:
        ra, rb = a['by_year'][year], b['by_year'][year]
        pa = int(ra['pos']) if str(ra['pos']).isdigit() else None
        pb = int(rb['pos']) if str(rb['pos']).isdigit() else None
        winner = None
        if pa is not None and pb is not None:
            winner = 'a' if pa < pb else 'b' if pb < pa else None
        elif pa is not None:
            winner = 'a'
        elif pb is not None:
            winner = 'b'
        if winner == 'a':
            h2h_a += 1
        elif winner == 'b':
            h2h_b += 1
        duel.append({'year': year, 'a_pos': ra['pos'], 'b_pos': rb['pos'], 'winner': winner})
    return {'ok': (a['races'] + b['races']) > 0, 'circuit': circuit, 'a': a, 'b': b,
            'h2h_a': h2h_a, 'h2h_b': h2h_b, 'shared': len(shared), 'duel': duel}


def _circuit_options_v41(prof_a, prof_b):
    """İki pilotun birlikte yarıştığı pistler önce, sonra tekil olanlar."""
    def circuits(prof):
        return {str(r.get('circuit', '')).strip() for r in (prof.get('races', []) if isinstance(prof, dict) else [])
                if str(r.get('circuit', '')).strip()}
    ca, cb = circuits(prof_a), circuits(prof_b)
    shared = sorted(ca & cb)
    solo = sorted((ca | cb) - (ca & cb))
    return shared + solo, set(shared)


def circuit_h2h_html(h, name_a, name_b, colour_a, colour_b):
    if not h.get('ok'):
        return ("<div style='padding:20px;color:#8a9bb0;font-family:Saira,sans-serif'>"
                "Bu pistte iki pilottan da doğrulanmış yarış kaydı yok.</div>")
    ca, cb = colour_a or '#e10600', colour_b or '#38e1d0'
    name_a, name_b = str(name_a), str(name_b)

    def stat_col(code, s, col, right=False):
        cells = "".join(
            f"<div><s>{lbl}</s><b>{val}</b></div>" for lbl, val in [
                ("Yarış", s['races']), ("Galibiyet", s['wins']), ("Podyum", s['podiums']),
                ("Pole", s['poles']), ("En iyi", f"P{s['best']}" if s['best'] else "—"),
                ("Ort. bitiş", s['avg'] if s['avg'] is not None else "—"),
                ("Yarış dışı", s['dnf']), ("Puan", _num_v33(s['points'])),
            ]
        )
        return (f"<div class='cc-col{' r' if right else ''}' style='--c:{col}'>"
                f"<div class='cc-name'>{html_lib.escape(code)}</div><div class='cc-grid'>{cells}</div></div>")

    duel_rows = "".join(
        f"<div class='cc-drow'>"
        f"<span class='yr'>{html_lib.escape(str(d['year']))}</span>"
        f"<span class='dp {'w' if d['winner'] == 'a' else ''}'>{html_lib.escape(str(d['a_pos']))}</span>"
        f"<span class='vs'>{'◀' if d['winner'] == 'a' else '▶' if d['winner'] == 'b' else '='}</span>"
        f"<span class='dp {'w' if d['winner'] == 'b' else ''}' style='text-align:left'>{html_lib.escape(str(d['b_pos']))}</span>"
        "</div>"
        for d in h['duel']
    ) or "<div class='cc-empty'>İki pilot bu pistte aynı yıl birlikte yarışmadı.</div>"

    tally = ""
    if h['shared']:
        tw = max(1, h['h2h_a'] + h['h2h_b'])
        tally = (f"<div class='cc-tally'><span class='tl' style='--c:{ca}'>{html_lib.escape(name_a)} "
                 f"<b>{h['h2h_a']}</b></span><span class='tbar'>"
                 f"<i style='width:{round(h['h2h_a'] / tw * 100)}%;background:{ca}'></i></span>"
                 f"<span class='tl r' style='--c:{cb}'><b>{h['h2h_b']}</b> {html_lib.escape(name_b)}</span></div>"
                 f"<div class='cc-sub'>{h['shared']} ortak yarışta önde biten</div>")

    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .cc{{border:1px solid #26313f;border-radius:12px;overflow:hidden;background:#11161f}}
      .cc-hd{{padding:13px 16px;border-bottom:1px solid #26313f;font:800 14px 'Saira Condensed',sans-serif;
        text-transform:uppercase;letter-spacing:.03em}}
      .cc-hd small{{display:block;font:600 10px 'JetBrains Mono',monospace;color:#63748a;letter-spacing:.1em;margin-top:3px}}
      .cc-cols{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1b2330}}
      .cc-col{{background:#131a24;padding:13px 14px;border-top:3px solid var(--c)}}
      .cc-col.r{{text-align:right}}
      .cc-name{{font:800 16px 'Saira Condensed',sans-serif;text-transform:uppercase;color:var(--c);margin-bottom:9px}}
      .cc-grid{{display:grid;grid-template-columns:1fr 1fr;gap:7px}}
      .cc-col.r .cc-grid{{direction:rtl}}
      .cc-grid s{{display:block;font:700 8px 'Saira Condensed',sans-serif;letter-spacing:.09em;color:#63748a;text-decoration:none}}
      .cc-grid b{{font:700 15px 'JetBrains Mono',monospace;margin-top:2px;display:block}}
      .cc-tally{{display:grid;grid-template-columns:1fr 2fr 1fr;gap:9px;align-items:center;padding:13px 16px 4px}}
      .tl{{font:700 11px 'Saira Condensed',sans-serif;text-transform:uppercase;color:var(--c)}}
      .tl.r{{text-align:right}} .tl b{{font-family:'JetBrains Mono',monospace;font-size:15px}}
      .tbar{{height:12px;background:#0a111b;border-radius:3px;overflow:hidden}} .tbar i{{display:block;height:100%}}
      .cc-sub{{text-align:center;font:600 10px 'Saira',sans-serif;color:#8a9bb0;padding:0 16px 10px}}
      .cc-duel{{padding:6px 16px 14px}}
      .cc-drow{{display:grid;grid-template-columns:52px 1fr 34px 1fr;gap:8px;align-items:center;
        padding:6px 0;border-top:1px solid #1b2330;font:700 12px 'JetBrains Mono',monospace}}
      .cc-drow .yr{{color:#63748a;font-size:11px}}
      .cc-drow .dp{{text-align:right;color:#9fb0c0}} .cc-drow .dp.w{{color:#f2f5f8}}
      .cc-drow .vs{{text-align:center;color:#4a5a6c;font-size:10px}}
      .cc-empty,.cc-duel .cc-empty{{padding:12px 0;color:#8a9bb0;font:500 12px 'Saira',sans-serif}}
      @media(max-width:560px){{.cc-grid{{grid-template-columns:1fr 1fr}}.cc-name{{font-size:14px}}}}
    </style>
    <div class="cc">
      <div class="cc-hd">{html_lib.escape(h['circuit'])}<small>KARİYER · BU PİSTTE</small></div>
      <div class="cc-cols">{stat_col(name_a, h['a'], ca)}{stat_col(name_b, h['b'], cb, right=True)}</div>
      {tally}
      <div class="cc-duel">
        <div class="cc-drow" style="border-top:0;color:#63748a"><span class="yr">YIL</span>
          <span class="dp">{html_lib.escape(name_a)}</span><span class="vs"></span>
          <span class="dp" style="text-align:left">{html_lib.escape(name_b)}</span></div>
        {duel_rows}
      </div>
    </div>
    """


def circuit_h2h_component_height(h):
    duel = len((h or {}).get('duel', []) or []) if h else 0
    return min(720, 330 + max(1, duel) * 30)


def render_driver_comparison_centre():
    """Career-only comparison with verified historical rows, never session data."""
    render_page_header(T('page.compare.title'), T('page.compare.sub'))
    driver_rows = []
    for team_name, team in TEAM_DIRECTORY_2026.items():
        for name, code, number, image_path in team.get('drivers', []):
            driver_rows.append({
                'name': name, 'code': code, 'number': number,
                'image': image_path, 'team': team_name,
            })
    driver_rows.sort(key=lambda row: row['name'].casefold())
    if len(driver_rows) < 2:
        st.info('Karşılaştırma için en az iki pilot gerekli.')
        return
    labels = {row['code']: f"{row['name']} — {row['team']}" for row in driver_rows}
    codes = [row['code'] for row in driver_rows]
    default_a = codes.index('NOR') if 'NOR' in codes else 0
    default_b = codes.index('VER') if 'VER' in codes else 1
    select_a, select_b = st.columns(2)
    with select_a:
        code_a = st.selectbox('Birinci pilot', codes, index=default_a, format_func=lambda code: labels[code], key='career_compare_a_v28')
    with select_b:
        code_b = st.selectbox('İkinci pilot', codes, index=default_b, format_func=lambda code: labels[code], key='career_compare_b_v28')
    if code_a == code_b:
        st.warning('Karşılaştırma için iki farklı pilot seç.')
        return
    info_a = next(row for row in driver_rows if row['code'] == code_a)
    info_b = next(row for row in driver_rows if row['code'] == code_b)
    with st.spinner('Kariyer kayıtları sürücü kimliğiyle doğrulanıyor...'):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(get_driver_career_stats_v28, code_a)
            future_b = executor.submit(get_driver_career_stats_v28, code_b)
            stats_a, stats_b = future_a.result(), future_b.result()
    wins_a, wins_b = stats_a.get('wins'), stats_b.get('wins')
    leader = (
        info_a['name'] if wins_a is not None and wins_b is not None and wins_a > wins_b
        else info_b['name'] if wins_a is not None and wins_b is not None and wins_b > wins_a
        else 'Eşit / kaynak bekleniyor'
    )
    summary = st.columns(2)
    summaries = [
        ('GALİBİYET LİDERİ', leader, '#2ee6c9'),
        ('VERİ DURUMU', 'Doğrulandı' if stats_a.get('verified') and stats_b.get('verified') else 'Kaynak bekleniyor', '#f7c948'),
    ]
    for column, (label, value, colour) in zip(summary, summaries):
        with column:
            st.markdown(
                f"<div class='hud-card compare-mini' style='border-top:4px solid {colour}'><div class='hud-label'>{label}</div><div class='hud-value' style='font-size:1.12rem'>{html_lib.escape(value)}</div></div>",
                unsafe_allow_html=True,
            )
    left, right = st.columns(2)
    with left:
        st.markdown(_career_panel_v28(info_a, stats_a, team_colour(info_a['team'])), unsafe_allow_html=True)
    with right:
        st.markdown(_career_panel_v28(info_b, stats_b, team_colour(info_b['team'])), unsafe_allow_html=True)
    st.caption('Kariyer istatistikleri yalnızca seçilen sürücünün tarihî yarış sonucu satırlarından hesaplanır. Kaynak yanıt vermezse istatistik uydurulmaz; “—” görünür.')

    st.write("")
    fp_ui.section_title("Bu Pistte")
    _api_a = STEWARDLE_ACTIVE_API_IDS_V24.get(code_a, str(code_a).lower())
    _api_b = STEWARDLE_ACTIVE_API_IDS_V24.get(code_b, str(code_b).lower())
    with st.spinner('Pist kayıtları hazırlanıyor...'):
        _prof_a = get_driver_full_profile_v33(_api_a)
        _prof_b = get_driver_full_profile_v33(_api_b)
    _circuits, _shared_circuits = _circuit_options_v41(_prof_a, _prof_b)
    if not (_prof_a.get('ok') and _prof_b.get('ok')):
        _who = info_a['name'] if not _prof_a.get('ok') else info_b['name']
        st.info(f'{_who} için kariyer yarış kaydı şu an alınamadı — birazdan tekrar dene. (İlk açılış yavaş olabilir; sonrası önbellekten anında gelir.)')
    elif not _circuits:
        st.info('Bu iki pilotun pist bazında ortak kaydı bulunamadı.')
    else:
        _default_idx = 0
        for _pref in ('Silverstone Circuit', 'Autodromo Nazionale Monza', 'Circuit de Monaco', 'Circuit de Spa-Francorchamps'):
            if _pref in _circuits:
                _default_idx = _circuits.index(_pref)
                break
        _circuit = st.selectbox(
            'Pist', _circuits, index=_default_idx,
            format_func=lambda c: (('★ ' if c in _shared_circuits else '') + c),
            key='compare_circuit_v41',
        )
        _ch2h = circuit_h2h_v41(_prof_a, _prof_b, _circuit)
        render_html_hud(
            circuit_h2h_html(_ch2h, code_a, code_b, team_colour(info_a['team']), team_colour(info_b['team'])),
            height=circuit_h2h_component_height(_ch2h),
            scrolling=False,
        )
        st.caption('★ = iki pilotun da yarıştığı pist. Kafa-kafaya sayacı yalnızca ikisinin aynı sezon birlikte yarıştığı yılları sayar.')


st.markdown(r"""
<style>
.career-panel-v28{min-height:492px;border:1px solid #2b4664;border-top:5px solid var(--team);border-radius:16px;padding:18px;background:linear-gradient(145deg,#111d31,#0b1524);box-shadow:0 14px 30px rgba(0,0,0,.18)}
.career-hero-v28{min-height:118px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #2a4059;padding-bottom:13px}.career-hero-v28 img{width:92px;height:116px;object-fit:contain;object-position:center bottom;flex:0 0 auto}.career-hero-v28 h3{margin:5px 0 4px;color:var(--team);font-size:1.45rem;line-height:1.15}.career-hero-v28 p{margin:0;color:#a8c0d7;font-size:.86rem}
.career-metrics-v28{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:14px}.career-metric-v28{min-height:70px;padding:10px;border:1px solid #29435f;border-radius:10px;background:#0d1829}.career-metric-v28 span,.career-teams-v28 small{display:block;color:#91abd0;font-size:.63rem;font-weight:900;letter-spacing:1.05px}.career-metric-v28 b{display:block;color:#f2f5f8;font-size:1.16rem;margin-top:7px}
.career-teams-v28{margin-top:14px;padding-top:12px;border-top:1px solid #2a4059}.career-teams-v28>div{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.career-teams-v28 span{border:1px solid #2e4a68;border-left:3px solid var(--team);border-radius:99px;padding:5px 8px;color:#c9d9e7;background:#122137;font-size:.74rem;font-weight:760}.career-source-v28{margin-top:13px;color:#83a0bd;font-size:.72rem}
@media(max-width:800px){.career-panel-v28{min-height:0;margin-bottom:12px}.career-hero-v28 img{width:76px;height:98px}.career-metrics-v28{grid-template-columns:repeat(2,minmax(0,1fr))}.career-metric-v28{min-height:63px}}
</style>
""", unsafe_allow_html=True)

# =========================================================
# 3.0 OFFICIAL PIT WALL + LOCAL GAME ENGINE
# Stewardle keeps its verified historical engine. The other games use this
# zero-network layer so Community Cloud never waits for an API to draw them.
# =========================================================


def _pit_wall_card_v30(label, name, colour, detail=""):
    safe_label = html_lib.escape(str(label))
    safe_name = html_lib.escape(str(name or "Kamuya açık değil"))
    safe_detail = html_lib.escape(str(detail))
    return (
        f"<div class='hud-card pit-person-v30' style='border-top:4px solid {colour}'>"
        f"<div class='hud-label'>{safe_label}</div><div class='pit-name-v30'>{safe_name}</div>"
        f"<div class='driver-meta'>{safe_detail}</div></div>"
    )


def render_pit_wall_v30(team_name=None, compact=False):
    """Publicly named team personnel; this is an editorial directory, not a game."""
    chosen = team_name if team_name in TEAM_DIRECTORY_2026 else list(TEAM_DIRECTORY_2026)[0]
    if team_name is None:
        chosen = st.selectbox("Takım", list(TEAM_DIRECTORY_2026), key="pit_wall_team_v30")
    team = TEAM_DIRECTORY_2026[chosen]
    people = PIT_WALL_PERSONNEL_2026.get(chosen, {})
    st.markdown("### Pit Duvarı")
    st.caption("Takımın kamuya açıkladığı 2026 yönetim, strateji ve yarış mühendisliği kadrosu. Açıklanmayan görevlerde isim uydurulmaz.")
    top = st.columns(3)
    with top[0]:
        st.markdown(_pit_wall_card_v30("TAKIM PATRONU", people.get("principal"), team["color"], chosen), unsafe_allow_html=True)
    with top[1]:
        st.markdown(_pit_wall_card_v30("STRATEJİ ŞEFİ", people.get("strategy"), team["color"], "Yarış stratejisi"), unsafe_allow_html=True)
    with top[2]:
        st.markdown(_pit_wall_card_v30("TAKIM BAŞ MÜHENDİSİ", people.get("chief"), team["color"], "Teknik liderlik"), unsafe_allow_html=True)
    engineers = people.get("engineers", [])
    engineer_columns = st.columns(2)
    for column, pair in zip(engineer_columns, engineers):
        with column:
            st.markdown(_pit_wall_card_v30("PİLOT MÜHENDİSİ", pair[1], team["color"], pair[0]), unsafe_allow_html=True)
    source = safe_external_url(people.get("source", ""))
    if source and not compact:
        st.link_button("Takım kaynağını aç ↗", source, width='stretch')


def render_team_personnel_hud(team_name, section='all'):
    """Official team leadership and Pit Wall directory, replacing game roles."""
    team = TEAM_DIRECTORY_2026[team_name]
    people = PIT_WALL_PERSONNEL_2026.get(team_name, {})
    leader = TEAM_LEADERSHIP_2026.get(team_name, {})
    if section in {'all', 'leader'}:
        st.markdown("### Takım yönetimi")
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {team['color']};margin:8px 0 18px'>"
            f"<div class='hud-label'>2026 TAKIM PATRONU</div><div class='pit-name-v30'>{html_lib.escape(people.get('principal', leader.get('name', 'Kamuya açık değil')))}</div>"
            f"<div class='history-copy' style='margin-top:7px'>Takımın sportif ve operasyonel liderliği. Bu kart oyun puanı veya hayalî personel içermez.</div></div>",
            unsafe_allow_html=True,
        )
    if section in {'all', 'engineers'}:
        render_pit_wall_v30(team_name, compact=False)


def _game_shell(title, subtitle="", colour="#e10600"):
    """Her oyun sayfasının TEK başlığı: 'Oyun Merkezi'ne dönüş linki + başlık +
    oyun motoru satırı. Oyunlar bunu bir kez çağırır; ikinci başlık basmaz."""
    if st.button("← Oyun Merkezi", key=f"game_back_{title[:16]}"):
        st.session_state['page'] = 'games'
        st.rerun()
    profile = st.session_state.setdefault('paddock_game_profile_v30', {'xp': 0, 'played': 0, 'best_streak': 0})
    fp_ui.page_header(title, subtitle, eyebrow="Oyunlar")
    st.caption(
        f"Paddock Oyun Motoru 3.0 · XP {profile['xp']} · "
        f"tamamlanan oyun {profile['played']} · en iyi seri {profile['best_streak']}"
    )


def render_paddock_career_alpha_v01():
    """Instant browser prototype while the full Unity WebGL production is prepared."""
    _game_shell(
        "Paddock Career · 2D Yarış",
        "Paddock Ring GP · 6 araçlık grid · DRS + ERS · lastik aşınması · pit yolu · çarpışma.",
        "#e10600",
    )
    game_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paddock_ring_alpha.html")
    try:
        with open(game_path, "r", encoding="utf-8") as game_file:
            game_markup = game_file.read()
        fp_ui._embed_html(game_markup, height=790, scrolling=False)
        st.info("Kontroller: W gaz · S fren · A/D direksiyon · Space ERS · E DRS · P pit isteği · R sıfırla")
    except OSError as error:
        log_data_error("Paddock Career prototype", error)
        st.error("Sürüş paketi yüklenemedi. Oyun dosyasının yayın paketinde bulunduğunu kontrol ediyoruz.")


def games_profile_hud_html(profile):
    xp = int(profile.get('xp', 0))
    played = int(profile.get('played', 0))
    streak = int(profile.get('best_streak', 0))
    ranks = [(0, 'ÇAYLAK', '#9fb0c0'), (150, 'YARIŞÇI', '#38e1d0'),
             (500, 'UZMAN', '#f5c33b'), (1200, 'ŞAMPİYON', '#e10600')]
    rank_name, rank_col = ranks[0][1], ranks[0][2]
    next_xp = ranks[-1][0]
    for i, (threshold, name, col) in enumerate(ranks):
        if xp >= threshold:
            rank_name, rank_col = name, col
            next_xp = ranks[i + 1][0] if i + 1 < len(ranks) else threshold
    progress = 100 if xp >= ranks[-1][0] else min(100, round((xp - 0) / max(1, next_xp) * 100))
    return f"""
    <style>
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,sans-serif;color:#f2f5f8}}
      .gp{{background:linear-gradient(160deg,#161d28,#11161f);border:1px solid #26313f;
        border-left:4px solid {rank_col};border-radius:5px;padding:16px 18px}}
      .gp-top{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}}
      .gp-eyebrow{{font:700 10px 'Saira Condensed',sans-serif;letter-spacing:.16em;text-transform:uppercase;color:#63748a}}
      .gp-rank{{font:800 26px 'Saira Condensed',sans-serif;text-transform:uppercase;color:{rank_col};margin-top:4px;line-height:1}}
      .gp-stats{{display:flex;gap:22px;flex-wrap:wrap}}
      .gp-stat b{{display:block;font:700 20px 'JetBrains Mono',monospace}}
      .gp-stat span{{font:700 9.5px 'Saira Condensed',sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#8a9bb0}}
      .gp-bar{{height:5px;background:#0c1016;border-radius:99px;margin-top:14px;overflow:hidden}}
      .gp-bar i{{display:block;height:100%;width:{progress}%;background:{rank_col}}}
    </style>
    <div class="gp">
      <div class="gp-top">
        <div><div class="gp-eyebrow">Paddock Oyuncu Profili</div><div class="gp-rank">{rank_name}</div></div>
        <div class="gp-stats">
          <div class="gp-stat"><b>{xp}</b><span>XP</span></div>
          <div class="gp-stat"><b>{played}</b><span>Oynanan Oyun</span></div>
          <div class="gp-stat"><b>{streak}</b><span>En İyi Seri</span></div>
        </div>
      </div>
      <div class="gp-bar"><i></i></div>
    </div>
    """


def render_games_hub_v30():
    fp_ui.page_header(T("page.games.title"), T("page.games.sub"), eyebrow=T("section.games"))
    _gp = st.session_state.setdefault('paddock_game_profile_v30', {'xp': 0, 'played': 0, 'best_streak': 0})
    render_html_hud(games_profile_hud_html(_gp), height=98)
    games = [
        ("TARİHÎ BULMACA", "Stewardle", "Gerçek kariyer verisiyle pilotu bul.", "#ff385c", "Stewardle aç", "stewarlde"),
        ("2D YARIŞ", "Paddock Career", "Çok rakipli grid, DRS + ERS, lastik aşınması ve pit yolu ile 2D yarış motoru.", "#e10600", "Motoru çalıştır", "paddock_career"),
    ]
    for start in range(0, len(games), 2):
        columns = st.columns(2)
        for column, game in zip(columns, games[start:start + 2]):
            label, title, description, colour, button_text, page = game
            with column:
                st.markdown(f"<div class='hud-card game-card-v24' style='--gc:{colour}'><div class='hud-label'>{label}</div><div class='game-card-title-v24'>{title}</div><div class='history-copy' style='margin-top:8px'>{description}</div></div>", unsafe_allow_html=True)
                if st.button(button_text, key=f"games_v30_{page}", width='stretch'):
                    st.session_state['page'] = page
                    st.rerun()
    st.markdown("---")
    render_pit_wall_v30()


render_games_hub = render_games_hub_v30

st.markdown(r"""
<style>
.pit-person-v30{min-height:120px!important;margin-top:8px!important}.pit-name-v30{font-size:1.18rem;font-weight:950;color:var(--fp-text);margin:8px 0 5px}.engine-banner-v30{margin-bottom:14px!important}.engine-title-v30{font-size:1.35rem;font-weight:950;color:var(--fp-text);margin:5px 0}.grid-question-v30{border-left:5px solid #f7c948!important;margin-top:14px!important}.grid-prompt-v30{font-size:1.25rem;font-weight:950;margin-top:12px}.grid-clue-v30{font-size:.94rem;color:var(--fp-muted);margin-top:9px;padding:10px;border-radius:9px;background:color-mix(in srgb,var(--fp-panel2) 75%,#f7c948 8%)}.grid-progress-v30{height:7px;background:var(--fp-panel2);border-radius:99px;margin-top:15px;overflow:hidden}.grid-progress-v30 i{display:block;height:100%;background:linear-gradient(90deg,#f7c948,#ff385c);border-radius:99px}.game-card-v24{transition:transform .15s ease,border-color .15s ease}.game-card-v24:hover{transform:translateY(-2px)}
@media(max-width:800px){.pit-person-v30{min-height:98px!important}.grid-prompt-v30{font-size:1.08rem}}
</style>
""", unsafe_allow_html=True)


# Final authoritative theme layer. This comes after legacy visual patches so
# light/dark mode cannot be overwritten by an older hard-coded dark selector.
st.markdown(r"""
<style>
html,body,#root,.stApp,[data-testid="stApp"],[data-testid="stAppViewContainer"]{
  color:var(--fp-text)!important;
  background-color:var(--fp-page)!important;
  background-image:linear-gradient(var(--fp-grid) 1px,transparent 1px),linear-gradient(90deg,var(--fp-grid) 1px,transparent 1px),radial-gradient(circle at 82% 8%,var(--fp-glow),transparent 31%),linear-gradient(135deg,var(--fp-page),var(--fp-page2))!important;
  background-size:44px 44px,44px 44px,100% 100%,100% 100%!important;
  animation:none!important;
}
[data-testid="stHeader"]{background:color-mix(in srgb,var(--fp-page) 92%,transparent)!important}
section[data-testid="stSidebar"]{background:linear-gradient(180deg,var(--fp-panel),var(--fp-panel2))!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important;box-shadow:8px 0 24px var(--fp-shadow)!important}
section[data-testid="stSidebar"] *,section[data-testid="stSidebar"] p,section[data-testid="stSidebar"] label{color:var(--fp-text)!important}
.nav-section-v29{color:var(--fp-muted)!important;background:linear-gradient(90deg,color-mix(in srgb,#e10600 12%,var(--fp-panel)),transparent)!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button{background:linear-gradient(90deg,var(--fp-panel2),var(--fp-panel))!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important;box-shadow:0 5px 13px var(--fp-shadow)!important;transition:border-color .14s ease,transform .14s ease!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button:hover{background:var(--fp-panel2)!important;color:var(--fp-text)!important;border-color:#259ad4!important;transform:translateX(1px)!important}
section[data-testid="stSidebar"] [data-testid="stExpander"]{background:var(--fp-panel2)!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important}
.f1-header,.hud-card,.metric-card,.news-card,.driver-card,.career-panel-v28,.career-metric-v28,[data-testid="stMetric"],[data-testid="stAlert"],div[data-testid="stExpander"]{background:linear-gradient(145deg,var(--fp-panel),var(--fp-panel2))!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important;box-shadow:0 10px 26px var(--fp-shadow)!important}
.f1-header h1,.hud-value,.news-title,.metric-card .value,.career-metric-v28 b,h1,h2,h3,h4{color:var(--fp-text)!important}
.f1-header p,.history-copy,.driver-meta,.news-desc,.metric-card .title,.career-hero-v28 p,.career-source-v28,[data-testid="stCaptionContainer"]{color:var(--fp-muted)!important}
div[data-testid="stButton"]>button,[data-baseweb="select"]>div,input,textarea{background:var(--fp-panel2)!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important}
.status-dot-v31{animation:none!important;box-shadow:0 0 9px rgba(104,231,174,.7)!important}
*{scrollbar-color:var(--fp-line) var(--fp-panel2)}
</style>
""", unsafe_allow_html=True)

# redesign: kabuk temasi (arka plan + slim-rail menu) EN SONDA —
# eski !important bloklarini yener
fp_ui.inject_shell_theme()
fp_ui.control_dock()


# =========================================================
# ROUTER SAYFA GOVDELERI  (eskiden if/elif icinde inline'di)
# =========================================================

def _router_page_home():
    """Ana ekran = açılış hero'su. Sol: 'Veriyle konuşur. Uydurmaz.' —
    Sağ: canlı sıradaki-seans sayacı. Başka içerik yok; gezinme üst bardan.
    (Yarış merkezi / haber akışı ilgili sayfalarda: Seans Takibi, Hafta
    Sonu Merkezi, Haber Merkezi.)"""
    try:
        _hev, _hsn, _hst, _hlive = get_current_or_next_event()
        fp_hero.render(
            str(_hev.get('Location') or _hev.get('EventName') or '').strip(),
            str(_hsn or ''), _hst, bool(_hlive), height=760,
        )
    except Exception as _hero_err:  # noqa: BLE001 — hero asla sayfayı düşürmesin
        log_data_error('hero', _hero_err)
        fp_ui.page_header("Formula Paddock", T("page.home.sub"), eyebrow="Formula Paddock")


# SAYFA 2: CANLI SEANS TAKİBİ


def _router_page_live():
    curr_event, target_s_name, target_s_time, is_live_now = get_current_or_next_event()
    gp_name = curr_event['EventName'] if 'EventName' in curr_event else "Hungarian Grand Prix"
    
    fp_ui.page_header(T("page.live.title"), T("page.live.sub"), eyebrow=T("section.live"))
    st.caption(f"Aktif hafta sonu: {gp_name}")

    timing_tab, replay_tab = st.tabs(["Seans Sonuçları", "Yarış Tekrarı"])
    if False:  # Alpha: doğrulanmış canlı konum altyapısı tamamlanana kadar gizli.
        token, openf1_username, openf1_password = get_openf1_access_v19()
        refresh_live = st.button("🔄 Açık canlı veri paketini yenile", key='refresh_live_v19')
        if refresh_live:
            get_openf1_live_snapshot_v19.clear()

        auto_live = st.toggle(
            "Canlı paket gelirse otomatik yenile",
            value=False,
            key='auto_live_v19',
            disabled=not bool(token),
            help="Bu seçenek yalnızca tokenli gerçek zaman paketi varsa 20 saniyede bir HUD'u yeniler. Token yokken tamamlanmış veriyi canlı diye göstermemek için kapalıdır.",
        )

        def render_live_v19():
            snapshot = get_openf1_live_snapshot_v19(token, openf1_username, openf1_password)
            render_html_hud(live_race_hud_html_v19(snapshot), height=690, scrolling=False)
            if not snapshot.get('ok'):
                st.info(
                    "Şu an doğrulanmış canlı konum paketi yok. Bu normaldir: seans dışında veya açık veri erişimi yokken "
                    "site sahte araç hareketi çizmez. Tamamlanan yarışlar hemen yanındaki Yarış Tekrarı sekmesinden tam 2D HUD ile izlenebilir."
                )
            elif not snapshot.get('authenticated'):
                st.caption("Paket anonim açık veri erişiminden geldi. Sağlayıcı limiti değişirse bu alan yalnızca bekleme durumu gösterebilir.")

        if auto_live and hasattr(st, 'fragment'):
            @st.fragment(run_every="20s")
            def live_race_fragment_v19():
                render_live_v19()
            live_race_fragment_v19()
        else:
            render_live_v19()

        st.caption(
            "Canlı HUD: konum, tur, fark, lastik, pit, hava ve Türkçe Race Control notları aynı açık veri paketinden gelir. "
            "ERS yüzdesi, fren/lastik sıcaklığı veya gerçek Overtake Mode telemetrisi açık veri yoksa gösterilmez."
        )
    with timing_tab:
        st.caption("Aktif hafta sonunun tamamlanan seanslarının doğrulanmış sonuç tablosu. Devam eden seans varken kısmi sonuç göstermeyiz.")
        timing_now = datetime.datetime.now(datetime.timezone.utc)
        session_is_future = target_s_time > timing_now
        timing_load_key = f"load_timing_2026_{gp_name}_{target_s_name}"

        if session_is_future:
            st.info(
                f"{target_s_name} henüz başlamadı. Sonuç çekmeye çalışmıyoruz; "
                "Yarış Tekrarı sekmesi ve diğer sayfalar normal şekilde açık kalır."
            )
        else:
            st.session_state[timing_load_key] = True

            if st.session_state.get(timing_load_key, False):
                try:
                    with st.spinner('Doğrulanmış seans sonuçları çekiliyor...'):
                        live_session_code = {
                            'FP1': 'FP1', 'FP2': 'FP2', 'FP3': 'FP3',
                            'Sıralama Turları': 'Q', 'Sıralama': 'Q', 'Q': 'Q',
                            'Yarış': 'R', 'R': 'R',
                        }.get(target_s_name, 'Q')
                        live_sess = fastf1.get_session(2026, gp_name, live_session_code)
                        live_sess.load(telemetry=False, weather=False, messages=False)

                    available_columns = [
                        column for column in ['Position', 'Abbreviation', 'TeamName', 'Time', 'Status']
                        if column in live_sess.results.columns
                    ]
                    res = live_sess.results[available_columns].copy()
                    res = res.rename(columns={
                        'Position': 'Sıra', 'Abbreviation': 'Pilot', 'TeamName': 'Takım',
                        'Time': 'En Hızlı Tur', 'Status': 'Durum',
                    })
                    if res.empty:
                        st.info(f"{target_s_name} için doğrulanmış sonuç henüz oluşmadı.")
                    else:
                        st.dataframe(res, width='stretch', height=620, hide_index=True)
                except Exception:
                    st.warning(f"Seans verisi henüz FastF1'e düşmedi ({target_s_name}).")
            else:
                st.info("Bu seansın derecelerini görmek için yukarıdaki düğmeye basabilirsin.")

    with replay_tab:
        _cur_year = datetime.datetime.now(datetime.timezone.utc).year
        _yr_opts = list(range(_cur_year, 2017, -1))
        replay_year = st.selectbox(
            "Sezon", _yr_opts, index=0, key="replay_year_pick",
            help="2018 ve sonrası için doğrulanmış konum telemetrisi bulunur; daha eski yıllarda yalnızca sonuç tablosu gelebilir.",
        )
        st.markdown(f"### 🎬 {replay_year} Yarış Tekrar Merkezi")
        st.caption(f"Seçtiğin sezonun tüm hafta sonları. Tamamlanan seansların doğrulanmış sonuçları ve yarış lastik stintleri otomatik gelir; gelecekteki yarışlarda program görünür.")
        replay_events = get_calendar_details(replay_year)
        if not replay_events:
            st.info(f"{replay_year} takvimi şu an alınamadı.")
        else:
            replay_names = [str(event.get('EventName', 'Formula 1')) for event in replay_events]
            replay_event_name = st.selectbox("Yarış seç", replay_names, key=f"replay_event_{replay_year}")
            replay_event = next(event for event in replay_events if str(event.get('EventName', '')) == replay_event_name)
            replay_sessions = event_session_cards(replay_event)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            finished_sessions = [
                item for item in replay_sessions
                if item['estimated_end'] + datetime.timedelta(minutes=15) <= now_utc
            ]

            session_columns = st.columns(len(replay_sessions))
            for column, item in zip(session_columns, replay_sessions):
                with column:
                    st.metric(item['title'], item['time'].tz_convert('Europe/Istanbul').strftime('%d %b • %H:%M'), item['status'])

            if not finished_sessions:
                st.info(
                    "Bu hafta sonu için doğrulanmış, tamamlanmış seans sonucu henüz yok. "
                    "Devam eden bir seansı yanlışlıkla geçmiş sonuç veya replay olarak göstermiyoruz."
                )
            else:
                default_replay_index = next(
                    (index for index, item in enumerate(finished_sessions) if item['code'] == 'R'),
                    0,
                )
                replay_session_title = st.radio(
                    "Tekrar seansı",
                    [item['title'] for item in finished_sessions],
                    index=default_replay_index,
                    horizontal=True,
                    key=f"replay_session_{replay_year}_{replay_event_name}",
                )
                replay_session = next(item for item in finished_sessions if item['title'] == replay_session_title)
                is_race_replay = replay_session['code'] == 'R'
                replay_hud_key = f"clean_race_replay_hud_{replay_year}_{replay_event_name}"

                # 2D butonu sonuç tablosundan önce gelir. Böylece uzun tablo, kullanıcıyı
                # gerçek replay girişinden aşağıya itmez ve boş geçici bir tablo 2D'yi engellemez.
                if is_race_replay:
                    st.markdown("#### 🏎️ Tam yarış 2D pist kontrolü")
                    st.caption(
                        "Pist, tek temiz telemetri turundan çizilir. Araçlar doğrulanmış yarış başlangıcı, tur süresi, "
                        "sıra, pit ve lastik verisiyle akıcı olarak bu yörüngede ilerler; bu alan canlı GPS diye etiketlenmez."
                    )
                    st.session_state[replay_hud_key] = True
                    st.caption("2D tekrar hızlı OpenF1 tarihî paketinden hazırlanır; yalnızca eksik yarışlarda FastF1 yedeği kullanılır.")

                    if st.session_state.get(replay_hud_key, False):
                        render_data_state(
                            "RACE REPLAY STATUS",
                            "Yarış paketi bir kez doğrulanır; sonraki açılışlar önbellekten gelir.",
                            "info",
                        )
                        with st.spinner("Doğrulanmış yarış haritası hazırlanıyor..."):
                            replay_payload = build_stable_race_replay_payload(replay_year, replay_event_name)
                        if replay_payload.get('ok'):
                            render_data_state(
                                "YARIŞ PAKETİ HAZIR",
                                "Pist, tur, sıralama, pit ve lastik kayıtları doğrulama kontrollerini geçti.",
                                "success",
                            )
                            fp_ui.how_to_read([
                                ("Pist", "tek temiz telemetri turundan çizilir; araçlar doğrulanmış tur/sıra/pit verisiyle bu yörüngede ilerler."),
                                ("Sağ panel", "seçili pilotun turu, başlangıç→bitiş sırası, pozisyon değişimi ve lastik seti. Alttaki şeritten pilot değiştir."),
                                ("Lastik barı", "bu setin aşınması soldan sağa dolar; alttaki ince şerit tüm yarışın plan özeti (her blok bir stint, çizgi bir pit)."),
                                ("Hız", "varsayılan 6×. 1× = gerçek yarış süresi (çok yavaş), 60× = tüm yarış birkaç dakikada."),
                            ], [
                                ("#45c8ff", "Straight Mode (≈DRS)"), ("#71e6a1", "Overtake Mode (≈ERS)"),
                                ("#b79cff", "pit giriş/çıkış"), ("#ff3b3b", "Soft"), ("#ffd234", "Medium"), ("#f0f4f8", "Hard"),
                            ], key=f"howto_replay_{replay_year}")
                            render_html_hud(stable_race_replay_html(replay_payload), height=850, scrolling=True)
                            st.markdown("#### 🛞 Tyre Strategy Wall")
                            render_html_hud(
                                strategy_wall_html(replay_payload),
                                height=strategy_wall_component_height(replay_payload),
                                scrolling=False,
                            )
                            # Ağır ikincil paneller varsayılan olarak kapalı — 2D tekrar
                            # anında açılsın, sayfa donmuş hissi vermesin.
                            with st.expander("📈 Tur tur pozisyon akışı", expanded=False):
                                render_html_hud(position_flow_html(replay_payload), height=520, scrolling=True)

                            with st.expander("🌦️ Hava · pit-lane · Race Control", expanded=False):
                                with st.spinner("Hava, pit-lane ve Race Control verisi hazırlanıyor..."):
                                    race_intelligence = get_race_intelligence_v19(replay_year, replay_event_name)
                                render_html_hud(
                                    race_intelligence_hud_html_v19(race_intelligence),
                                    height=530,
                                    scrolling=True,
                                )
                        else:
                            render_data_state(
                                "RACE REPLAY NOT OPENED",
                                replay_payload.get('reason', 'Race replay data is not ready yet.')
                                + " Retry after the verified data package becomes available.",
                                "warning",
                            )
                else:
                    st.caption("Tam 2D yarış tekrarını açmak için üstten **Yarış** seansını seç. Bu seansın sonuç HUD’u aşağıda.")

                # Sonuçlar artık 2D oyuncudan bağımsızdır; geçici FastF1 gecikmesi
                # replay düğmesini ortadan kaldırmaz.
                with st.expander("🏁 Seans sonuçları ve lastik detayları", expanded=not is_race_replay):
                    with st.spinner("Doğrulanmış seans sonuçları hazırlanıyor..."):
                        replay_table, replay_laps = get_session_results_table(replay_year, replay_event_name, replay_session['code'])

                    if replay_table.empty:
                        st.info("Bu seans tamamlanmış görünüyor ama sonuç verisi FastF1'e henüz düşmedi.")
                    else:
                        render_html_hud(
                            session_leaderboard_html(
                                replay_table,
                                f"{replay_year} // {replay_event_name} // {replay_session['title'].upper()}"
                            ),
                            height=leaderboard_component_height(replay_table),
                            scrolling=True,
                        )

                    if is_race_replay:
                        strategy = build_strategy_from_laps(replay_laps)
                        st.markdown("#### 🧾 Lastik ve stint detay tablosu")
                        if strategy.empty:
                            st.info("Bu yarış için stint bilgisi henüz alınamadı.")
                        else:
                            tyre_label = {
                                'SOFT': '🔴 S • SOFT', 'MEDIUM': '🟡 M • MEDIUM', 'HARD': '⚪ H • HARD',
                                'INTERMEDIATE': '🟢 I • INTERMEDIATE', 'WET': '🔵 W • WET'
                            }
                            strategy['Lastik'] = strategy['Lastik'].map(
                                lambda value: tyre_label.get(str(value).upper(), str(value))
                            )
                            st.dataframe(
                                strategy,
                                width='stretch',
                                hide_index=True,
                                height=620,
                                column_config={'Lastik': st.column_config.TextColumn('Lastik')},
                            )

# SAYFA 3: TELEMETRİ VE DOMİNASYON HARİTASI


def _router_page_telemetry():
    fp_ui.page_header(T("page.telemetry.title"), T("page.telemetry.sub"), eyebrow=T("section.data"))

    # --- SEANS SEÇİMİ (artik sayfa govdesinde, sidebar yerine) ---
    fp_ui.section_title("Seans Ayarlari")
    if not st.session_state['telemetry_schedule_requested']:
        fp_ui.data_state("Takvim İsteğe Bağlı", "Sitenin hızlı açılması için takvim yalnızca sen istediğinde yüklenir.", "info")
        if st.button("Telemetri takvimini yukle", key="load_telemetry_schedule", width='stretch'):
            st.session_state['telemetry_schedule_requested'] = True
            st.rerun()
        st.stop()

    _tc = st.columns([1, 2, 1.2])
    year = _tc[0].number_input("Sezon", min_value=2018, max_value=2026, value=2026, key="tel_year")
    _gp_list = get_season_schedule(year)
    if not _gp_list:
        _gp_list = ["Takvim verisi bekleniyor"]
        _tc[1].caption("Takvim gecici olarak alinamadi; biraz sonra tekrar dene.")
    _default_gp_idx = next((i for i, g in enumerate(_gp_list) if "Hungar" in g), 0)
    gp = _tc[1].selectbox("Grand Prix", _gp_list, index=_default_gp_idx, key="tel_gp")
    session_type = _tc[2].selectbox("Seans", ["Q", "R", "FP1", "FP2", "FP3"], key="tel_session")

    target_q = None
    q_sub_session = None
    if session_type == "Q":
        q_sub_session = st.selectbox(
            "Sıralama elemesi",
            ["Q3 (Final / Pole)", "Q2", "Q1", "Tüm Sıralama Seansı"], key="tel_qsub",
        )
        target_q = "Q3" if "Q3" in q_sub_session else "Q2" if "Q2" in q_sub_session else "Q1" if "Q1" in q_sub_session else None

    _MODES = [
        "🗺️ Kuş Bakışı Pist Dominasyonu",
        "🏎️ 2D Tur Düellosu",
        "🛑 Telemetri & Fren Analizi",
        "📊 Top Hız & Sürücü Tablosu",
        "🛞 Lastik Stratejisi & Stintler",
        "🌦️ Hava & Pist Evrimi",
    ]
    _MODE_LABELS = ["Pist Dominasyonu", "2D Tur Düellosu", "Fren Analizi", "Top Hız", "Lastik Stratejisi", "Hava & Evrim"]
    if hasattr(st, "segmented_control"):
        _picked = st.segmented_control("Görünüm", _MODE_LABELS, default=_MODE_LABELS[0], key="tel_mode")
    else:
        _picked = st.radio("Görünüm", _MODE_LABELS, horizontal=True, key="tel_mode")
    analiz_turu = _MODES[_MODE_LABELS.index(_picked)] if _picked in _MODE_LABELS else _MODES[0]

    _HOWTO = {
        "Pist Dominasyonu": ([
            ("Pist çizgisi", "iki pilotun turu üst üste bindirilir; her nokta o an kimin daha hızlı olduğunu gösterir."),
            ("Renk", "kırmızı = 1. pilot daha hızlı, cyan = 2. pilot daha hızlı."),
            ("Ne aramalı", "uzun kırmızı/cyan bloklar = bir pilotun net üstün olduğu bölüm; renk sık değişiyorsa turlar denk."),
        ], [("#e10600", "1. pilot önde"), ("#38bdf8", "2. pilot önde")]),
        "2D Tur Düellosu": ([
            ("Δ (delta)", "aynı pist noktasında iki pilot arasındaki saniye farkı. Δ 0.30 = öndeki 0,30 sn hızlı."),
            ("Oynat / hız", "turu 1×–8× hızda izle; alttaki çubukla istediğin ana atla."),
            ("Sektörler", "hangi pilotun hangi sektörde daha hızlı olduğu alttaki üç kutuda."),
            ("Mini-sektör Δ", "tur 20 dilime bölünür; yukarı-yeşil çubuk 1. pilotun, aşağı-kırmızı 2. pilotun o dilimde kazandığı süre. Zamanın tam nerede kaybedildiğini gösterir."),
        ], [("#4ea981", "1. pilot dilimde hızlı"), ("#d3576a", "2. pilot dilimde hızlı"), ("#45c8ff", "SM (≈DRS)"), ("#71e6a1", "OM (≈ERS)")]),
        "Fren Analizi": ([
            ("Dört iz", "üstten alta: hız, gaz, fren, vites — hepsi pist mesafesine göre hizalı."),
            ("İmleç", "fareyi grafiğin veya pistin üzerinde gezdir; dört iz ve haritadaki nokta aynı anda o mesafeye kilitlenir. Soldaki panelde tam değerler."),
            ("Geç frenleme", "fren izindeki dikey sıçrama fren noktasıdır; daha sağda olan pilot viraja daha geç fren yapmıştır."),
            ("Hız farkı", "hız izinde çizgiler ayrışıyorsa orada bir pilot belirgin hızlı; soldaki Δ hız satırı farkı sayıyla verir."),
        ], [("#e10600", "1. pilot"), ("#38e1d0", "2. pilot"), ("#f4d35e", "sektör sınırı")]),
        "Top Hız": ([
            ("Tablo", "her pilotun o seanstaki en yüksek telemetri hızı, hızlıdan yavaşa."),
            ("Ne anlatır", "yüksek top hız = düşük kanat / iyi güç ünitesi / iyi slipstream; düşük = yüksek kanat tercihi."),
        ], None),
        "Lastik Stratejisi": ([
            ("Yatay barlar", "her pilotun stint'leri; blok uzunluğu o lastikte geçen tur sayısı."),
            ("Renk", "kırmızı Soft, sarı Medium, beyaz Hard, yeşil Intermediate, mavi Wet."),
            ("Ne aramalı", "en anlamlı görünüm yarış seansında; farklı stratejiler (ör. 1 durak vs 2 durak) burada ayrışır."),
        ], [("#ff3b3b", "Soft"), ("#ffd234", "Medium"), ("#f0f4f8", "Hard"), ("#3fd66a", "Inter"), ("#3aa9ff", "Wet")]),
        "Hava & Evrim": ([
            ("Üst grafik", "seans boyunca temsili tur zamanı: mavi = o an atılan en hızlı tur, gri = ortalama. Çizgi aşağı gidiyorsa pist 'lastikleniyor' (hızlanıyor)."),
            ("Pist kazancı", "ilk dilimdeki en hızlı turdan son dilime kaç saniye düştüğü — yağmur yoksa bu tipik pist evrimidir."),
            ("Alt grafik", "pist °C (turuncu), hava °C (sarı), nem % (yeşil); mavi gölge = o anda yağış kaydı."),
            ("İmleç", "fareyi gezdir; alttaki satır o dakikadaki tur zamanı ve hava değerlerini verir."),
        ], [("#45c8ff", "en hızlı tur"), ("#5b6b7e", "ortalama tur"), ("#ff7a45", "pist °C"), ("#ffd23f", "hava °C"), ("#4ea981", "nem %"), ("#3aa9ff", "yağış")]),
    }
    _ht = _HOWTO.get(_picked if _picked in _MODE_LABELS else "Pist Dominasyonu")
    if _ht:
        fp_ui.how_to_read(_ht[0], _ht[1], key=f"howto_tel_{_picked}")

    st.write("")
    try:
        with st.spinner('Telemetri verileri yükleniyor...'):
            try:
                session = fastf1.get_session(year, gp, session_type)
                session.load(laps=True, telemetry=True, weather=False, messages=False)
                if session.laps is None or session.laps.empty:
                    raise RuntimeError('FastF1 tur paketi boş')
                telemetry_source_v31 = 'FastF1'
            except Exception as fastf1_error:
                log_data_error('telemetry FastF1 primary', fastf1_error)
                session = openf1_fallback.load_session(int(year), str(gp), str(session_type))
                telemetry_source_v31 = 'OpenF1 tarihî veri yedeği'

        st.caption(f"Veri kaynağı: {telemetry_source_v31} · Tamamlanmış seans verisi otomatik seçildi.")

        if session_type == "Q" and target_q:
            try:
                res = session.results
                if target_q in res.columns:
                    valid_res = res[pd.notnull(res[target_q])]
                    drivers_list = sorted(valid_res['Abbreviation'].tolist())
                else:
                    drivers_list = sorted(list(set(session.laps['Driver'])))
            except Exception:
                drivers_list = sorted(list(set(session.laps['Driver'])))
        else:
            drivers_list = sorted(list(set(session.laps['Driver'])))

        if not drivers_list:
            st.warning("Seçilen evrede geçerli tur verisi bulunamadı kanka!")
        else:
            driver_options = {}
            for drv in drivers_list:
                try:
                    drv_laps = session.laps.pick_drivers(drv)
                    if not drv_laps.empty:
                        drv_num = drv_laps.iloc[0]['DriverNumber']
                        driver_options[drv] = f"#{drv_num} {drv}"
                except (KeyError, TypeError, ValueError, IndexError):
                    driver_options[drv] = drv

            header_suffix = f" ({target_q})" if target_q else ""

            # --- MOD 1: KUŞ BAKIŞI PİST DOMİNASYON HARİTASI ---
            if analiz_turu == "🗺️ Kuş Bakışı Pist Dominasyonu":
                fp_ui.section_title(f"{session.event['EventName']} · Pist Dominasyonu{header_suffix}")

                col1, col2 = st.columns(2)
                d1_idx = drivers_list.index("VER") if "VER" in drivers_list else 0
                d2_idx = drivers_list.index("NOR") if "NOR" in drivers_list else (1 if len(drivers_list) > 1 else 0)

                d1 = col1.selectbox("1. Sürücü (Kırmızı Bölge)", drivers_list, format_func=lambda x: driver_options.get(x, x), index=d1_idx, key="dom_d1")
                d2 = col2.selectbox("2. Sürücü (Mavi Bölge)", drivers_list, format_func=lambda x: driver_options.get(x, x), index=d2_idx, key="dom_d2")

                if d1 == d2:
                    st.warning("Lütfen iki farklı pilot seç kanka!")
                else:
                    lap1 = get_driver_fastest_lap(session, d1, target_q)
                    lap2 = get_driver_fastest_lap(session, d2, target_q)

                    if lap1 is None or lap2 is None:
                        st.error("Seçilen sürücülerden birinin bu seans evresinde geçerli turu bulunamadı.")
                    else:
                        tel1 = lap1.get_telemetry()
                        tel2 = lap2.get_telemetry()

                        speed_diff = round(tel1['Speed'].max() - tel2['Speed'].max(), 1)
                        m1, m2, m3 = st.columns(3)
                        with m1:
                            fp_ui.stat_tile(f"{d1} turu · {str(lap1.get('Compound', '-'))}",
                                            format_time(lap1['LapTime']), accent="red")
                        with m2:
                            fp_ui.stat_tile(f"{d2} turu · {str(lap2.get('Compound', '-'))}",
                                            format_time(lap2['LapTime']), accent="cyan")
                        with m3:
                            fp_ui.stat_tile("Top speed farki", f"{abs(speed_diff)} km/h", accent="amber")

                        st.write("")

                        dom_payload = _telemetry_trace_payload_v38([
                            (d1, fp_plot.A1, format_time(lap1['LapTime']), tel1),
                            (d2, fp_plot.A2, format_time(lap2['LapTime']), tel2),
                        ])
                        if dom_payload.get('ok'):
                            render_html_hud(dominance_map_html(dom_payload), height=600, scrolling=True)
                        else:
                            st.warning("Bu turlar için pist dominasyon haritası çıkarılamadı (konum verisi eksik).")

                        fp_ui.data_state("BOLGE OKUMA", f"{driver_options.get(d1, d1)} renginde boyalı bölümlerde 1. pilot, {driver_options.get(d2, d2)} renginde boyalı bölümlerde 2. pilot o an daha hızlı. Alttaki bar tur boyunca kimin ne kadar önde olduğunu özetler.", "info")
                        fp_ui.data_state("ICGORU", get_speed_difference_insight(session, d1, d2, tel1, tel2), "success")

            # --- MOD 2: 2D TUR DÜELLOSU ---
            elif analiz_turu == "🏎️ 2D Tur Düellosu":
                fp_ui.section_title(f"{session.event['EventName']} · 2D Tur Duellosu{header_suffix}")
                st.caption("Mesafe modu aynı virajdaki hız farkını; gerçek zaman modu iki turun fiziksel zaman farkını gösterir.")

                duel_col_1, duel_col_2 = st.columns(2)
                default_1 = drivers_list.index("VER") if "VER" in drivers_list else 0
                default_2 = drivers_list.index("NOR") if "NOR" in drivers_list else (1 if len(drivers_list) > 1 else 0)
                duel_driver_1 = duel_col_1.selectbox(
                    "1. araç", drivers_list, index=default_1,
                    format_func=lambda value: driver_options.get(value, value), key="duel_driver_1"
                )
                duel_driver_2 = duel_col_2.selectbox(
                    "2. araç", drivers_list, index=default_2,
                    format_func=lambda value: driver_options.get(value, value), key="duel_driver_2"
                )

                if duel_driver_1 == duel_driver_2:
                    st.warning("Düello için iki farklı pilot seç kanka.")
                else:
                    duel_lap_1 = get_driver_fastest_lap(session, duel_driver_1, target_q)
                    duel_lap_2 = get_driver_fastest_lap(session, duel_driver_2, target_q)
                    if duel_lap_1 is None or duel_lap_2 is None:
                        st.error("Seçilen pilotlardan biri için geçerli tur bulunamadı.")
                    else:
                        duel_tel_1 = duel_lap_1.get_telemetry()
                        duel_tel_2 = duel_lap_2.get_telemetry()
                        def _duel_result_row(driver_code):
                            try:
                                row = session.results[session.results['Abbreviation'] == driver_code]
                                if not row.empty:
                                    return row.iloc[0]
                            except Exception:
                                pass
                            return None

                        def team_for_driver(driver_code):
                            row = _duel_result_row(driver_code)
                            return str(row.get('TeamName', 'Formula 1')) if row is not None else 'Formula 1'

                        def colour_for_driver(driver_code, team_name):
                            # seçilen sezonun gerçek livery rengi — önce seans satırındaki TeamColor
                            row = _duel_result_row(driver_code)
                            if row is not None:
                                raw = row.get('TeamColor')
                                if raw is not None and str(raw).strip() and str(raw).strip().lower() != 'nan':
                                    return '#' + str(raw).strip().lstrip('#')
                            return season_team_colour(team_name, year)

                        team_1 = team_for_driver(duel_driver_1)
                        team_2 = team_for_driver(duel_driver_2)
                        colour_1 = colour_for_driver(duel_driver_1, team_1)
                        colour_2 = colour_for_driver(duel_driver_2, team_2)
                        if colour_1 == colour_2:
                            colour_1, colour_2 = "#E10600", "#38BDF8"
                        gap_seconds = abs(duel_lap_1['LapTime'].total_seconds() - duel_lap_2['LapTime'].total_seconds())
                        duel_overlay = build_track_overlay(duel_tel_1, duel_lap_1, session)
                        duel_sectors_1 = [format_time(duel_lap_1.get(column)) for column in ['Sector1Time', 'Sector2Time', 'Sector3Time']]
                        duel_sectors_2 = [format_time(duel_lap_2.get(column)) for column in ['Sector1Time', 'Sector2Time', 'Sector3Time']]
                        _mc = st.columns(4)
                        with _mc[0]:
                            fp_ui.stat_tile(f"{duel_driver_1} turu", format_time(duel_lap_1['LapTime']), accent="red")
                        with _mc[1]:
                            fp_ui.stat_tile(f"{duel_driver_2} turu", format_time(duel_lap_2['LapTime']), accent="cyan")
                        with _mc[2]:
                            fp_ui.stat_tile("Tur farki", f"{gap_seconds:.3f} sn", accent="amber")
                        with _mc[3]:
                            fp_ui.stat_tile("Onde olan",
                                            duel_driver_1 if duel_lap_1['LapTime'] < duel_lap_2['LapTime'] else duel_driver_2,
                                            accent="green", mono=False)
                        render_html_hud(
                            two_driver_duel_html_repaired(
                                duel_tel_1, duel_tel_2, duel_driver_1, duel_driver_2, team_1, team_2,
                                colour_1, colour_2, format_time(duel_lap_1['LapTime']), format_time(duel_lap_2['LapTime']),
                                duel_lap_1['LapTime'].total_seconds(), duel_lap_2['LapTime'].total_seconds(), duel_overlay,
                                duel_sectors_1, duel_sectors_2
                            ),
                            height=880,
                            scrolling=True
                        )
                        fp_ui.data_state("ICGORU", get_speed_difference_insight(session, duel_driver_1, duel_driver_2, duel_tel_1, duel_tel_2), "success")

            # --- MOD 3: DETAYLI TELEMETRİ & FREN ANALİZİ ---
            elif analiz_turu == "🛑 Telemetri & Fren Analizi":
                fp_ui.section_title(f"{session.event['EventName']} · Telemetri & Fren{header_suffix}")
                
                col1, col2 = st.columns(2)
                d1_idx = drivers_list.index("VER") if "VER" in drivers_list else 0
                d2_idx = drivers_list.index("NOR") if "NOR" in drivers_list else (1 if len(drivers_list) > 1 else 0)

                d1 = col1.selectbox("1. Sürücü", drivers_list, format_func=lambda x: driver_options.get(x, x), index=d1_idx, key="tel_d1")
                d2 = col2.selectbox("2. Sürücü", drivers_list, format_func=lambda x: driver_options.get(x, x), index=d2_idx, key="tel_d2")

                if d1 == d2:
                    st.warning("Lütfen iki farklı pilot seç kanka!")
                else:
                    lap1 = get_driver_fastest_lap(session, d1, target_q)
                    lap2 = get_driver_fastest_lap(session, d2, target_q)

                    if lap1 is None or lap2 is None:
                        st.error("Seçilen sürücülerden birinin bu seans evresinde geçerli turu bulunamadı.")
                    else:
                        tel1 = lap1.get_telemetry()
                        tel2 = lap2.get_telemetry()

                        trace_c1, trace_c2 = fp_plot.A1, fp_plot.A2
                        trace_payload = _telemetry_trace_payload_v38([
                            (d1, trace_c1, format_time(lap1['LapTime']), tel1),
                            (d2, trace_c2, format_time(lap2['LapTime']), tel2),
                        ])
                        if trace_payload.get('ok'):
                            render_html_hud(telemetry_trace_html(trace_payload), height=560, scrolling=True)
                        else:
                            st.warning("Bu turlar için telemetri izi çıkarılamadı (konum/mesafe verisi eksik).")
                        fp_ui.data_state("GEC FRENLEME IPUCU", "Fren izindeki dikey sıçrama fren noktasıdır; hangi pilotunki daha sağdaysa o pilot viraja daha geç fren yapmıştır. Hız izinde çizgiler ayrışan yerde bir pilot belirgin hızlıdır.", "info")
                        fp_ui.data_state("ICGORU", get_speed_difference_insight(session, d1, d2, tel1, tel2), "success")

            # --- MOD 4: TOP HIZ & SÜRÜCÜ TABLOSU ---
            elif analiz_turu == _MODES[3]:
                fp_ui.section_title(f"{session.event['EventName']} · Top Hız Tablosu{header_suffix}")
                
                summary_data = []
                for drv in drivers_list:
                    try:
                        drv_lap = get_driver_fastest_lap(session, drv, target_q)
                        if drv_lap is not None:
                            drv_tel = drv_lap.get_telemetry()
                            max_speed = drv_tel['Speed'].max()
                            lap_distance_km = drv_tel['Distance'].max() / 1000
                            lap_hours = drv_lap['LapTime'].total_seconds() / 3600
                            avg_speed = lap_distance_km / lap_hours if lap_hours > 0 else np.nan
                            drv_number = drv_lap['DriverNumber']
                            compound = drv_lap.get('Compound', '-')
                            def official_speed(column):
                                value = pd.to_numeric(drv_lap.get(column), errors='coerce')
                                return round(float(value), 1) if pd.notna(value) else '—'

                            summary_data.append({
                                "No": f"#{drv_number}",
                                "Pilot": drv,
                                f"Tur Zamanı {header_suffix}": format_time(drv_lap['LapTime']),
                                "Lastik": compound,
                                "Resmî Speed Trap (km/h)": official_speed('SpeedST'),
                                "I1 / I2 (km/h)": f"{official_speed('SpeedI1')} / {official_speed('SpeedI2')}",
                                "Telemetri Maks. Hız (km/h)": round(max_speed, 1),
                                "Tur Ortalama Hızı (km/h)": round(avg_speed, 1),
                                "_saniye": drv_lap['LapTime'].total_seconds()
                            })
                    except Exception:
                        pass

                if summary_data:
                    df_summary = pd.DataFrame(summary_data).sort_values(by="_saniye", ascending=True).drop(columns="_saniye")
                    st.dataframe(df_summary, width='stretch')
                    st.caption("Resmî Speed Trap / I1 / I2 FastF1'in ilgili seans ölçüm alanından gelir. Telemetri Maks. Hız ise turdaki en yüksek örneklenmiş hızdır; ikisi aynı şey değildir.")
                else:
                    st.warning("Veri çekilemedi.")

            # --- MOD 4: LASTİK STRATEJİSİ ---
            elif analiz_turu == "🛞 Lastik Stratejisi & Stintler":
                fp_ui.section_title(f"{session.event['EventName']} · Lastik Stratejisi{header_suffix}")
                if session_type != "R":
                    st.info("En anlamlı strateji görünümü yarış seansında oluşur. Bu seans için mevcut stintler gösteriliyor.")

                strategy = build_strategy_data(session)
                if strategy.empty:
                    st.warning("Bu seans için stint verisi bulunamadı.")
                else:
                    compound_colors = {
                        **fp_plot.COMPOUND
                    }
                    selected_drivers = st.multiselect(
                        "Grafikte gösterilecek pilotlar",
                        strategy['Pilot'].unique().tolist(),
                        default=strategy['Pilot'].unique().tolist()[:10]
                    )
                    chart_data = strategy[strategy['Pilot'].isin(selected_drivers)]
                    figure, axis = plt.subplots(figsize=(12, max(4, len(selected_drivers) * 0.45)))
                    fp_plot.style(figure, axis)
                    for row_index, driver in enumerate(selected_drivers):
                        driver_stints = chart_data[chart_data['Pilot'] == driver]
                        for _, stint in driver_stints.iterrows():
                            color = compound_colors.get(str(stint['Lastik']).upper(), '#64748B')
                            axis.barh(
                                row_index, stint['Tur Sayısı'], left=stint['Başlangıç Turu'],
                                color=color, edgecolor=fp_plot.BG, height=0.6
                            )
                            axis.text(
                                stint['Başlangıç Turu'] + stint['Tur Sayısı'] / 2, row_index,
                                str(stint['Lastik'])[:1], ha='center', va='center',
                                color=fp_plot.BG, fontweight='bold'
                            )
                    axis.set_yticks(range(len(selected_drivers)), selected_drivers)
                    axis.set_xlabel('Tur')
                    st.pyplot(figure, width='stretch')
                    st.dataframe(strategy, width='stretch', hide_index=True)

            # --- MOD 6: HAVA & PİST EVRİMİ ---
            elif analiz_turu == _MODES[5]:
                fp_ui.section_title(f"{session.event['EventName']} · Hava & Pist Evrimi{header_suffix}")
                with st.spinner("Hava ve tur zamanı verisi hazırlanıyor..."):
                    _wx_evo = get_weather_evolution_v42(year, gp, session_type)
                if not _wx_evo.get('ok'):
                    st.warning("Bu seans için hava / pist evrimi verisi henüz alınamadı.")
                else:
                    render_html_hud(
                        weather_evolution_html(_wx_evo),
                        height=545,
                        scrolling=True,
                    )
                    if _wx_evo.get('rained'):
                        fp_ui.data_state("YAĞIŞ KAYDI", "Bu seansta en az bir hava ölçümünde yağış işaretlendi; pist evrimi eğrisi kuru sürtünme değil, ıslak/kuruma etkisini de yansıtır.", "warning")

    except Exception as e:
        st.error(f"Veriler çekilirken hata oluştu: {e}")

# SAYFA 4: TAKVİM VE PİSTLER


def _router_page_calendar():
    fp_ui.page_header(T("page.calendar.title"), T("page.calendar.sub"), eyebrow=T("section.live"))
    calendar_year = st.selectbox("Sezon", [2026, 2025, 2024], index=0, key="calendar_year")
    events = get_calendar_details(calendar_year)
    if not events:
        st.warning("Takvim şu anda alınamadı.")
        st.stop()

    if 'calendar_event' not in st.session_state:
        st.session_state['calendar_event'] = events[0]['EventName']

    for start in range(0, len(events), 3):
        columns = st.columns(3)
        for column, event in zip(columns, events[start:start + 3]):
            event_name = str(event.get('EventName', 'Grand Prix'))
            race_time = pd.to_datetime(event.get('Session5DateUtc'))
            race_time = race_time.tz_localize('UTC') if race_time.tzinfo is None else race_time.tz_convert('UTC')
            now = datetime.datetime.now(datetime.timezone.utc)
            status = "✅ Tamamlandı" if race_time < now else f"⏱️ {max(0, (race_time - now).days)} gün kaldı"
            with column:
                if st.button(f"🏎️ {event_name}\n{status}", key=f"calendar_{calendar_year}_{event_name}", width='stretch'):
                    st.session_state['calendar_event'] = event_name
                    st.rerun()

    selected_event = next((event for event in events if event['EventName'] == st.session_state['calendar_event']), events[0])
    st.markdown("---")
    st.markdown(f"### 📍 {selected_event['EventName']} — {selected_event.get('Location', '')}")
    sessions = event_session_cards(selected_event)
    if not sessions:
        st.info("Bu yarış için seans takvimi henüz alınamadı.")
        st.stop()

    render_html_hud(weekend_overview_hud(selected_event, sessions), height=245, scrolling=False)
    st.caption("Program İstanbul saatine göre gösterilir. Tamamlanan seansların doğrulanmış sonuçlarını aşağıdan açabilirsin.")

    session_columns = st.columns(len(sessions))
    for column, item in zip(session_columns, sessions):
        local_time = item['time'].tz_convert('Europe/Istanbul').strftime('%d %b • %H:%M')
        with column:
            st.metric(item['title'], local_time, item['status'])

    map_key = f"track_map_{calendar_year}_{selected_event['EventName']}"
    if st.button("🗺️ Pist görünümünü aç", width='stretch'):
        st.session_state[map_key] = True
    if st.session_state.get(map_key):
        with st.spinner("Pist çizimi hazırlanıyor..."):
            outline = get_track_outline(calendar_year, selected_event['EventName'])
        if outline:
            figure, axis = plt.subplots(figsize=(8, 4.5))
            axis.plot(outline['X'], outline['Y'], color='#E10600', linewidth=3)
            axis.set_aspect('equal', 'datalim')
            axis.set_facecolor('#07090d')
            figure.patch.set_facecolor('#07090d')
            axis.axis('off')
            st.pyplot(figure, width='stretch')
        else:
            st.info("Bu pistin çizimi için tamamlanmış bir seans verisi henüz bulunamadı.")

    completed_sessions = [item for item in sessions if item['status'] == 'Tamamlandı']
    if completed_sessions:
        selected_session_name = st.radio(
            "Sonuçları görüntüle", [item['title'] for item in completed_sessions], horizontal=True
        )
        selected_session = next(item for item in completed_sessions if item['title'] == selected_session_name)
        session_story = get_session_story(calendar_year, selected_event['EventName'], selected_session['code'])
        if session_story:
            st.markdown("#### 🧠 Bu seansta ne oldu?")
            for item in session_story:
                tone = '#f4cf5a' if item['kind'] in ['POLE', 'WIN'] else '#67d8ff' if item['kind'] == 'PACE' else '#ff8a9b' if item['kind'] == 'RACE CONTROL' else '#a9b9d0'
                st.markdown(
                    f"<div class='hud-card' style='border-left:4px solid {tone};padding:10px 13px;margin:7px 0'>"
                    f"<span class='hud-label' style='color:{tone}'>{html_lib.escape(item['kind'])}</span>"
                    f"<div style='font-weight:750;color:#f2f5f8;margin-top:4px'>{html_lib.escape(item['text'])}</div></div>",
                    unsafe_allow_html=True
                )
        table, _ = get_session_results_table(calendar_year, selected_event['EventName'], selected_session['code'])
        st.markdown(f"#### {selected_session['title']} sonuçları")
        if table.empty:
            st.info("Bu seansın doğrulanmış sonuçları henüz alınamadı.")
        elif selected_session['code'] == 'Q' and any(column in table.columns for column in ['Q1', 'Q2', 'Q3']):
            q_tabs = st.tabs(["Sıralama", "Q1", "Q2", "Q3"])
            with q_tabs[0]:
                render_html_hud(
                    session_leaderboard_html(table, f"{selected_event['EventName']} // SIRALAMA"),
                    height=leaderboard_component_height(table),
                    scrolling=False
                )
            for tab, q_column in zip(q_tabs[1:], ['Q1', 'Q2', 'Q3']):
                with tab:
                    if q_column in table.columns:
                        q_table = table[['Sıra', 'Pilot', 'Takım', q_column]].dropna(subset=[q_column]).copy()
                        render_html_hud(
                            session_leaderboard_html(q_table, f"{selected_event['EventName']} // {q_column}"),
                            height=leaderboard_component_height(q_table),
                            scrolling=False
                        )
                    else:
                        st.info(f"{q_column} verisi yok.")
        else:
            render_html_hud(
                session_leaderboard_html(table, f"{selected_event['EventName']} // {selected_session['title'].upper()}"),
                height=leaderboard_component_height(table),
                scrolling=False
            )
    else:
        st.info("Bu hafta sonu henüz tamamlanan seans yok. İstanbul saatine göre program yukarıda.")

    st.markdown("### 📺 Nereden izlenir?")
    watch_tr, watch_global = st.columns(2)
    with watch_tr:
        st.markdown("""
        <div class='hud-card'>
          <div class='hud-label'>TÜRKİYE</div>
          <div style='font-size:1.12rem;font-weight:900;color:#fff;margin-top:5px'>beIN SPORTS</div>
          <div style='color:#94A3B8;font-size:.86rem;margin-top:6px'>Formula 1'in Türkiye için resmî yayıncı listesinde beIN SPORTS yer alır.</div>
        </div>
        """, unsafe_allow_html=True)
        st.link_button("Türkiye yayın bilgisi →", "https://www.beinsports.com.tr/", width='stretch')
    with watch_global:
        st.markdown("""
        <div class='hud-card'>
          <div class='hud-label'>YURT DIŞI / RESMÎ KAYNAKLAR</div>
          <div style='font-size:1.12rem;font-weight:900;color:#fff;margin-top:5px'>F1 TV ve ülkeye göre yayıncılar</div>
          <div style='color:#94A3B8;font-size:.86rem;margin-top:6px'>Yayın hakları ülkeye ve sezona göre değişir; resmî listeden kontrol et.</div>
        </div>
        """, unsafe_allow_html=True)
        global_a, global_b = st.columns(2)
        with global_a:
            st.link_button("F1 TV uygunluğu →", "https://www.formula1.com/en/subscribe-to-f1-tv", width='stretch')
        with global_b:
            st.link_button("Resmî yayıncı listesi →", "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ", width='stretch')

# SAYFA 5: TAKIMLAR VE PİLOTLAR


def team_driver_cards_html(team_name, team):
    """İki pilot kartını TEK CSS grid'inde çizer — bio uzunluğu farklı olsa da
    kartlar ve iç kutular (galibiyet/podyum, öne çıkan an) satır satır hizalı kalır."""
    acc = team['color']
    cards = []
    for name, code, number, image_path in team['drivers']:
        career = driver_career_profile(code)
        portrait = current_driver_portrait(team_name, image_path)
        cards.append(
            "<div class='tm-drv'>"
            "<div class='stage'>"
            f"<span class='code'>{html_lib.escape(code)}</span>"
            f"<img src='{portrait}' alt='{html_lib.escape(name)}' onerror=\"this.style.display='none'\"></div>"
            "<div class='body'>"
            f"<div class='nm'>{html_lib.escape(name)} <span>{html_lib.escape(number)}</span></div>"
            f"<div class='mt'>{html_lib.escape(code)} · {driver_age(code)} yaş · {html_lib.escape(team_name)}</div>"
            f"<div class='bio'>{html_lib.escape(career['bio'])}</div>"
            "<div class='stats'>"
            f"<div><div class='hud-label'>GP GALİBİYETİ</div><b>{html_lib.escape(str(career['wins']))}</b></div>"
            f"<div><div class='hud-label'>PODYUM</div><b>{html_lib.escape(str(career['podiums']))}</b></div></div>"
            f"<div class='moment'>{html_lib.escape(career['moment'])}</div>"
            "</div></div>"
        )
    return f"""
    <style>
      .tm-drv-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:6px 0 4px;--tm-acc:{acc}}}
      .tm-drv{{display:flex;flex-direction:column;border:1px solid #26313f;border-top:4px solid var(--tm-acc);
        border-radius:9px;background:linear-gradient(160deg,#161d28,#11161f);overflow:hidden;padding-bottom:14px}}
      .tm-drv .stage{{height:180px;position:relative;display:flex;align-items:flex-end;justify-content:center;
        background:linear-gradient(180deg,rgba(15,30,47,.46),rgba(9,13,20,.02));overflow:hidden}}
      .tm-drv .stage .code{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
        color:var(--tm-acc);opacity:.26;font:950 2.4rem/1 'Saira Condensed',sans-serif;letter-spacing:.1em}}
      .tm-drv .stage img{{position:relative;height:170px;max-width:100%;object-fit:contain;object-position:center bottom}}
      .tm-drv .body{{padding:0 14px;display:flex;flex-direction:column;flex:1}}
      .tm-drv .nm{{font:950 1.2rem 'Saira Condensed',sans-serif;color:#f2f5f8;margin-top:11px;line-height:1.1}}
      .tm-drv .nm span{{color:var(--tm-acc)}}
      .tm-drv .mt{{font:600 .78rem 'Saira',sans-serif;color:#9fb0c0;margin-top:4px}}
      .tm-drv .bio{{font:.83rem/1.5 'Saira',sans-serif;color:#b9c8d9;margin-top:9px;min-height:6em;
        display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}}
      .tm-drv .stats{{display:flex;gap:8px;margin-top:12px}}
      .tm-drv .stats > div{{flex:1;background:#11161f;border:1px solid #2d415b;border-radius:8px;padding:8px}}
      .tm-drv .stats b{{display:block;font:950 1.15rem 'JetBrains Mono',monospace;color:var(--tm-acc);margin-top:3px}}
      .tm-drv .moment{{font:.82rem/1.5 'Saira',sans-serif;color:#b9c8d9;margin-top:11px}}
      @media(max-width:640px){{.tm-drv-grid{{grid-template-columns:1fr}}.tm-drv .bio{{min-height:0}}}}
    </style>
    <div class="tm-drv-grid">{''.join(cards)}</div>
    """


def _router_page_teams():
    fp_ui.page_header(T("page.teams.title"), T("page.teams.sub"), eyebrow=T("section.champ"))
    if 'team_focus' not in st.session_state:
        st.session_state['team_focus'] = 'Mercedes'

    team_names = list(TEAM_DIRECTORY_2026.keys())
    for start in range(0, len(team_names), 3):
        columns = st.columns(3)
        for column, team_name in zip(columns, team_names[start:start + 3]):
            team = TEAM_DIRECTORY_2026[team_name]
            with column:
                card_logo = OFFICIAL_TEAM_LOGOS.get(team_name, '')
                st.markdown(
                    f"<div class='hud-card' style='height:118px;border-top:4px solid {team['color']};padding:11px 13px'>"
                    f"<div style='height:47px;display:flex;align-items:center'><img src='{card_logo}' alt='{html_lib.escape(team_name)}' "
                    f"style='max-height:42px;max-width:112px;object-fit:contain' onerror=\"this.style.display='none'\"></div>"
                    f"<div style='font-size:.98rem;font-weight:900;color:{team['color']}'>{html_lib.escape(team_name)}</div>"
                    f"<div class='driver-meta'>{team['drivers'][0][1]} · {team['drivers'][1][1]}</div></div>",
                    unsafe_allow_html=True
                )
                if st.button(f"{team_name}\n{team['drivers'][0][1]} • {team['drivers'][1][1]}", key=f"team_{team_name}", width='stretch'):
                    st.session_state['team_focus'] = team_name
                    st.session_state['_scroll_team'] = True
                    st.rerun()

    selected_team_name = st.session_state['team_focus']
    selected_team = TEAM_DIRECTORY_2026[selected_team_name]
    st.write("")
    fp_ui.anchor("fp-team-detail")
    fp_ui.section_title(f"{selected_team_name} · 2026 Takım Dosyası")
    logo_url = OFFICIAL_TEAM_LOGOS.get(selected_team_name) or get_official_team_logo(selected_team['slug'])
    header_left, header_middle, header_right = st.columns([.85, 2.55, 1.35])
    with header_left:
        st.markdown(
            f"<div class='hud-card' style='height:116px;border-top:4px solid {selected_team['color']};display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden'>"
            f"<img src='{logo_url or ''}' alt='{html_lib.escape(selected_team_name)}' style='max-height:76px;max-width:150px;object-fit:contain' onerror=\"this.style.display='none'\"></div>",
            unsafe_allow_html=True,
        )
    with header_middle:
        st.markdown(f"<div class='history-copy' style='margin-top:8px'>{TEAM_HISTORY.get(selected_team_name, '')}</div>", unsafe_allow_html=True)
        st.link_button("Resmî takım profili ↗", f"https://www.formula1.com/en/teams/{selected_team['slug']}")
    with header_right:
        st.markdown(f"<div class='hud-card' style='margin-top:18px;border-left:4px solid {selected_team['color']}'><div class='hud-label'>KADRO</div><div class='hud-value'>2 Pilot</div><div class='driver-meta'>Resmî 2026 grid</div></div>", unsafe_allow_html=True)

    render_team_personnel_hud(selected_team_name, section='leader')

    st.markdown(team_driver_cards_html(selected_team_name, selected_team), unsafe_allow_html=True)

    render_team_personnel_hud(selected_team_name, section='engineers')

    profile_driver = st.selectbox(
        "Pilot dosyasını aç",
        selected_team['drivers'],
        format_func=lambda item: f"{item[0]} ({item[1]})",
        key=f"driver_profile_{selected_team_name}",
    )
    render_driver_profile_hud(selected_team_name, profile_driver)

    if st.session_state.pop('_scroll_team', False):
        fp_ui.scroll_to("fp-team-detail")

# SAYFA 6: ŞAMPİYONA MERKEZİ


def _router_page_standings():
    fp_ui.page_header(T("page.standings.title"), T("page.standings.sub"), eyebrow=T("section.champ"))

    _cur_year = datetime.datetime.now(datetime.timezone.utc).year
    champ_year = st.selectbox(
        "Sezon", list(range(_cur_year, 2017, -1)), index=0, key="standings_year_pick",
        help="Geçmiş sezonlar da tamamlanmış yarış sonuçlarından hesaplanır; ilk açılış bir sezonun tüm yarışlarını çektiği için sürebilir, sonrası önbellekten gelir.",
    )
    fp_ui.data_state(
        "Sezon Verisi",
        f"{champ_year} şampiyona tablosu, tamamlanmış yarış ve sprint sonuçlarından (FastF1) otomatik hesaplanır. "
        "İlk açılış kısa sürebilir; sonrası yerel önbellekten gelir.",
        "info",
    )
    st.caption("Puan tablosu saatlik önbellekten otomatik güncellenir; elle yenileme gerekmez.")

    driver_standings = pd.DataFrame()
    constructor_standings = pd.DataFrame()
    result_matrix = pd.DataFrame()
    points_matrix = pd.DataFrame()
    completed_rounds = []
    try:
        with st.spinner(f"{champ_year} sonuç tabloları hazırlanıyor..."):
            driver_standings, constructor_standings, result_matrix, points_matrix, completed_rounds = get_championship_data_stable(champ_year)
    except Exception:
        st.warning("Puan verisi şu an alınamadı. Ana sayfa ve diğer bölümler çalışmaya devam eder; daha sonra tekrar deneyebilirsin.")

    if driver_standings.empty:
        st.info(f"{champ_year} için tamamlanmış yarışların doğrulanmış sonuçları henüz alınamadı.")
    else:
        render_html_hud(
            championship_snapshot_hud(driver_standings, constructor_standings, completed_rounds, champ_year),
            height=160,
            scrolling=False,
        )
        st.write("")
        driver_tab, team_tab, scenario_tab, h2h_tab = st.tabs(
            ["Sezon Tablosu", "Takim Puanlari", "Şampiyonluk Senaryoları", "Kafa Kafaya"]
        )
        with driver_tab:
            if 'championship_matrix_mode' not in st.session_state:
                st.session_state['championship_matrix_mode'] = 'sıralama'
            sort_button, points_button = st.columns(2)
            _mm = st.session_state['championship_matrix_mode']
            with sort_button:
                if st.button(("● " if _mm != 'puan' else "○ ") + "Siralama", key='championship_show_positions',
                             width='stretch'):
                    st.session_state['championship_matrix_mode'] = 'sıralama'
            with points_button:
                if st.button(("● " if _mm == 'puan' else "○ ") + "Puan", key='championship_show_points',
                             width='stretch'):
                    st.session_state['championship_matrix_mode'] = 'puan'

            show_points = st.session_state['championship_matrix_mode'] == 'puan'
            active_matrix = points_matrix if show_points else result_matrix
            st.caption(
                "Puan görünümü: her yarıştan alınan puan / sprint puanı."
                if show_points else
                "Sıralama görünümü: yarış bitiş sırası / sprint bitiş sırası."
            )
            render_html_hud(
                championship_matrix_html(active_matrix, completed_rounds),
                height=championship_matrix_component_height(active_matrix),
                scrolling=False,
            )
        with team_tab:
            st.caption("Podyumda ilk üç takım; altında kalan takımlar aynı HUD diliyle sıralanır.")
            render_html_hud(
                constructor_hud_html(constructor_standings),
                height=constructor_hud_component_height(constructor_standings),
                scrolling=False,
            )
        with scenario_tab:
            _rem = _championship_remaining_v40(champ_year, len(completed_rounds))
            _scn = championship_scenarios_v40(driver_standings, _rem)
            _scn_colour = (lambda t: season_team_colour(t, champ_year))
            fp_ui.how_to_hud(
                [
                    ("Tavan", "pilotun güncel puanı + kalan tüm yarış ve sprintleri kazanırsa ulaşabileceği en yüksek puan."),
                    ("Yarışta / Elendi", "tavanı liderin güncel puanına ulaşmıyorsa o pilot matematiksel olarak şampiyon olamaz."),
                    ("Senaryo", "aşağıda bir rakip ve iki pilotun kalan yarışlardaki sabit bitiş sırasını seç; nihai puan matematiğini gösterir."),
                ],
                note="Tüm rakamlar doğrulanmış puan tablosu ve resmî takvimden. Tahmin veya olasılık yok — yalnızca 'bu sırayla biterse ne olur'.",
            )
            if not _scn.get('ok'):
                st.info("Senaryo hesaplaması için yeterli puan verisi yok.")
            else:
                render_html_hud(
                    championship_scenarios_html(_scn, _scn_colour),
                    height=min(620, 170 + 56 * len(_scn['contenders'])),
                    scrolling=False,
                )
                _alive = [c for c in _scn['contenders'] if c['alive']]
                if _rem['races'] > 0 and len(_alive) >= 2:
                    st.write("")
                    fp_ui.section_title("Senaryo Hesaplayıcı")
                    _leader = _alive[0]
                    _rivals = _alive[1:]
                    _rc1, _rc2 = st.columns([2, 1])
                    _riv_code = _rc1.selectbox(
                        "Rakip", [c['code'] for c in _rivals],
                        format_func=lambda c: f"{directory_driver_by_code(c)['name']} ({c})",
                        key="champ_scn_rival",
                    )
                    _riv = next(c for c in _rivals if c['code'] == _riv_code)
                    with _rc2:
                        fp_ui.stat_tile(f"{_riv_code} · lidere fark", f"-{int(_riv['gap'])} P", accent="amber")
                    _finish_opts = ["1.", "2.", "3.", "4.", "5.", "6.", "8.", "10.", "Puan yok"]
                    _to_pos = lambda label: 30 if label == "Puan yok" else int(label.rstrip("."))
                    _pc1, _pc2 = st.columns(2)
                    _l_fin = _pc1.select_slider(
                        f"{_leader['code']} kalan her yarışta", options=_finish_opts, value="2.",
                        key="champ_scn_leader_fin",
                    )
                    _c_fin = _pc2.select_slider(
                        f"{_riv_code} kalan her yarışta", options=_finish_opts, value="1.",
                        key="champ_scn_rival_fin",
                    )
                    render_html_hud(
                        championship_projection_html(
                            _leader['code'], _riv_code, _leader['points'], _riv['points'],
                            _to_pos(_l_fin), _to_pos(_c_fin), _rem['races'], _rem['sprints'],
                            _scn_colour(_leader['team']), _scn_colour(_riv['team']),
                        ),
                        height=290,
                        scrolling=False,
                    )
                elif _rem['races'] == 0:
                    st.caption("Sezon tamamlandığı için senaryo hesaplayıcı kapalı — unvan kesinleşti.")

        with h2h_tab:
            _codes = [str(r.get('Pilot', '')).strip() for _, r in driver_standings.iterrows() if str(r.get('Pilot', '')).strip()]
            _team_of = {str(r.get('Pilot', '')).strip(): str(r.get('Takım', '')).strip() for _, r in driver_standings.iterrows()}
            fp_ui.how_to_hud(
                [
                    ("Fark", "iki pilotun bu sezon puanları arasındaki güncel fark ve kim önde."),
                    ("Yarışta önde biten", "kaç yarışta A, kaç yarışta B daha yüksek sırada bitti — çubukta oran."),
                    ("Form", "son 5 yarışta toplanan puan; hangisi daha sıcak."),
                    ("Tur tur şerit", "her kutu bir yarış; kutunun rengi o hafta sonu önde biteni gösterir (üzerine gelince detay)."),
                ],
                note="Tümü tamamlanmış yarış ve sprint sonuçlarından hesaplanır — Pilot listesindeki tekil kariyer profillerinden farklı olarak burası doğrudan iki pilotun sezonluk düellosudur.",
            )
            if len(_codes) < 2:
                st.info("Karşılaştırma için en az iki pilot gerekiyor.")
            else:
                _hc1, _hc2 = st.columns(2)
                _fmt = lambda c: f"{directory_driver_by_code(c)['name']} ({c})"
                _a = _hc1.selectbox("1. pilot", _codes, index=0, format_func=_fmt, key="champ_h2h_a")
                _b_opts = [c for c in _codes if c != _a] or _codes
                _b = _hc2.selectbox("2. pilot", _b_opts, index=0, format_func=_fmt, key="champ_h2h_b")
                _h2h = season_h2h_v41(result_matrix, points_matrix, completed_rounds, driver_standings, _a, _b)
                _ca = season_team_colour(_team_of.get(_a, ''), champ_year)
                _cb = season_team_colour(_team_of.get(_b, ''), champ_year)
                render_html_hud(
                    season_h2h_html(_h2h, _ca, _cb),
                    height=season_h2h_component_height(_h2h),
                    scrolling=False,
                )
    st.write("")
    fp_ui.section_title("Favorilerin")
    _ft, _fd = st.columns(2)
    with _ft:
        fp_ui.stat_tile("Takim", st.session_state.get('favourite_team', 'Mercedes'), accent="red", mono=False)
    with _fd:
        fp_ui.stat_tile("Pilot", st.session_state.get('favourite_driver', 'George Russell'), accent="cyan", mono=False)

# SAYFA 7: F2 VE F3


# =========================================================
# YARDIMCI SAYFALAR — SSS / Gizlilik / 404
# =========================================================
def render_faq_page():
    fp_ui.page_header("Sık Sorulan Sorular", "Formula Paddock nasıl çalışır — kısa cevaplar.", eyebrow="Bilgi")
    faqs = [
        ("Veriler nereden geliyor?",
         "Zamanlama, sonuç ve takvim verisi **FastF1** ve resmî Formula 1 kaynaklarından; "
         "tarihsel istatistikler Ergast yansımasından çekilir. Hiçbir sonuç elle girilmez veya uydurulmaz."),
        ("Canlı 2D pist neden çoğu zaman kapalı?",
         "Doğrulanmış bir canlı konum sağlayıcısı bağlı olmadığında site sahte canlı konum üretmez. "
         "Seans sırasında doğrulanmış paket gelirse açılır; gelmezse kapalı kalır."),
        ("Haberler Türkçeye nasıl çevriliyor?",
         "İngilizce F1 haber başlık ve özetleri **DeepL API** ile otomatik çevrilir. "
         "Çeviri geçici olarak alınamazsa özgün metin gösterilir."),
        ("Oyunlar ve tahminler gerçek mi?",
         "Stewardle gerçek kariyer verisi kullanır. Paddock Career tamamen bir simülasyondur ve açıkça "
         "öyle etiketlenir; ürettiği sonuçlar gerçek yarış sonucu değildir."),
        ("Neden bazen 'veri yok' yazısı görüyorum?",
         "Seans dışındayken veya kaynak geçici olarak yanıt vermediğinde olur. Hata önbelleğe alınmaz; "
         "sayfayı yenilediğinde sistem yeniden dener."),
        ("Sıralama nasıl hesaplanıyor?",
         "Şampiyona puanları yalnızca **tamamlanmış** yarış ve sprint sonuçlarından üretilir; "
         "tablo saatlik önbellekten otomatik güncellenir."),
        ("Site kişisel veri topluyor mu?",
         "Hayır. Hesap/giriş yok, çerez yok. Ayrıntı için Gizlilik sayfasına bak."),
    ]
    for q, a in faqs:
        with st.expander(q):
            st.markdown(a)


def render_privacy_page():
    fp_ui.page_header("Gizlilik", "Ne toplanır, ne toplanmaz.", eyebrow="Bilgi")
    st.markdown(
        """
Formula Paddock kişisel bir F1 veri projesidir. **Hesap, giriş veya form yoktur;
kişisel veri toplanmaz, saklanmaz veya satılmaz.**

**Çerezler.** Reklam veya takip çerezi kullanılmaz. Yalnızca uygulamayı çalıştıran
Streamlit'in kendi oturum çerezi bulunur.

**Yerel tarayıcı depolaması.** Tema tercihi ve açılış animasyonunun bir kez oynatılması
gibi küçük ayarlar tarayıcının `localStorage` / `sessionStorage` alanında tutulur.
Bu veriler cihazından çıkmaz, sunucuya gönderilmez.

**Dış servisler.** Sayfalar şu servislere istek yapar; herhangi bir web sitesinde
olduğu gibi bu isteklerde IP adresin ilgili servise ulaşır:

- **FastF1 / Formula 1 / Ergast** — yarış verisi
- **DeepL API** — haber çevirisi
- **Google Fonts** — yazı tipleri

**Barındırma.** Uygulama Streamlit Community Cloud üzerinde çalışır; Streamlit'in
kullanım istatistiği toplama özelliği kapalıdır (`gatherUsageStats = false`).

**Analitik.** Ziyaretçi analitiği (Google Analytics vb.) kullanılmaz.
        """
    )
    st.caption(f"Son güncelleme: {datetime.date.today().isoformat()}")


def render_not_found_page(bad):
    fp_ui.page_header("404 · Sayfa bulunamadı", eyebrow="Formula Paddock")
    fp_ui.data_state(
        "Yönlendirme",
        f"“{bad}” diye bir sayfa yok — taşınmış, kaldırılmış ya da bağlantı bozuk olabilir. "
        "Üstteki menüden ya da aşağıdaki bağlantılardan devam et.",
        "warning",
    )
    cols = st.columns(4)
    for col, (lbl, key) in zip(cols, [
        ("Ana Ekran", "home"), ("Haber Merkezi", "news"),
        ("Seans Merkezi", "live"), ("Şampiyonalar", "teams"),
    ]):
        with col:
            if st.button(lbl, key=f"nf_{key}", width='stretch'):
                st.session_state['page'] = key
                st.rerun()


# =========================================================
# ROUTER
# =========================================================
_active_page = '__404__' if _bad_page else st.session_state['page']
# İç sayfalarda kırıntı yolu bölüm bağlamını verir; page_header eyebrow'u bastırılır.
fp_ui._SUPPRESS_EYEBROW = _active_page != 'home'
if _active_page != 'home':
    _render_breadcrumb(_active_page)

if _bad_page:
    render_not_found_page(_bad_page)
elif _active_page == 'home':
    _router_page_home()
elif st.session_state['page'] == 'live':
    _router_page_live()
elif st.session_state['page'] == 'telemetry':
    _router_page_telemetry()
elif st.session_state['page'] == 'calendar':
    _router_page_calendar()
elif st.session_state['page'] == 'teams':
    _router_page_teams()
elif st.session_state['page'] == 'standings':
    _router_page_standings()
elif st.session_state['page'] == 'f2f3':
    _view_f2f3.render()
elif st.session_state['page'] == 'weekend':
    render_weekend_centre()
elif st.session_state['page'] == 'story':
    render_race_story_centre()
elif st.session_state['page'] == 'compare':
    render_driver_comparison_centre()
elif st.session_state['page'] == 'drivers':
    render_drivers_page_v33()
elif st.session_state['page'] == 'learn':
    render_learning_centre()
elif st.session_state['page'] == 'favourites':
    render_favourites_centre()

# SAYFA 8: PADDOCK ASİSTANI
elif st.session_state['page'] == 'news':
    render_news_centre_v19()
elif st.session_state['page'] == 'assistant':
    render_paddock_assistant_v20()
elif st.session_state['page'] == 'games':
    render_games_hub()
elif st.session_state['page'] == 'stewarlde':
    render_stewarlde()
elif st.session_state['page'] == 'paddock_career':
    render_paddock_career_alpha_v01()

# SAYFA 9: F1 SÖZLÜĞÜ
elif st.session_state['page'] == 'glossary':
    _view_glossary.render()

# YARDIMCI SAYFALAR
elif st.session_state['page'] == 'faq':
    render_faq_page()
elif st.session_state['page'] == 'privacy':
    render_privacy_page()

# 404 — bilinmeyen / kaldırılmış sayfa (eski oturum, bozuk link)
else:
    render_not_found_page(st.session_state['page'])

# --- sayfa sonu: veri tanılama (yalnız ?debug=1) ---
render_data_diagnostics_panel()

# --- her sayfanın altında ince ayak (ana ekran hariç — hero tam ekran) ---
if _bad_page or st.session_state['page'] != 'home':
    fp_ui.site_footer(FOOTER_LINKS)
