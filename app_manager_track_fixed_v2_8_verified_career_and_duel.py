# -*- coding: utf-8 -*-
import os
import datetime
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
import json
import re
import base64
import html as html_lib
import logging
import xml.etree.ElementTree as ET
import streamlit as st
import streamlit.components.v1 as components
import fastf1
import fastf1.plotting
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap
import numpy as np
import pandas as pd


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


def log_data_error(context, error):
    """Ekranı kırmadan, geliştiricinin gerçek hatayı terminal günlüklerinde görmesini sağlar."""
    LOGGER.warning("%s | %s | %s", context, type(error).__name__, str(error)[:420])


def _legacy_render_html_hud(markup, height=150, scrolling=False):
    """Yeni Streamlit iframe API'si; eski sürümde güvenli uyumluluk geri dönüşü."""
    if hasattr(st, 'iframe'):
        # st.iframe, HTML metnini doğrudan destekler; yeni API'de scrolling parametresi yoktur.
        return st.iframe(markup, height=height)
    return components.html(markup, height=height, scrolling=scrolling)

# Streamlit ayarı, dosyadaki ilk Streamlit çağrısından önce olmak zorunda.
def current_paddock_theme():
    """Tema motoru kaldırıldı: uygulama tutarlı ve sabit koyu HUD kullanır."""
    return 'Koyu'


def hud_theme_override_css():
    """Her iframe için sabit koyu görünüm; tema geçişi yoktur."""
    return "html{color-scheme:dark} body{background:#090d14!important;color:#edf6ff!important}"


def render_html_hud(markup, height=150, scrolling=False):
    """Tüm etkileşimli HUD'lar için tek, güvenli render kapısı.

    Parça HTML ortak ``f1-hud-shell`` DIV'i ile sarılır. Böylece birbirinden
    bağımsız HUD stilleri Streamlit sayfasını veya diğer kartları bozmaz.
    """
    if not isinstance(markup, str) or not markup.strip():
        st.info('Bu HUD için gösterilecek doğrulanmış veri henüz yok.')
        return None
    document = markup.strip()
    theme_css = '<style>' + hud_theme_override_css() + '</style>'
    if '<html' not in document.lower():
        document = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<style>body{margin:0;background:transparent}.f1-hud-shell{width:100%;overflow:hidden}</style>' + theme_css +
            '</head><body><div class="f1-hud-shell">' + document + '</div></body></html>'
        )
    elif '</head>' in document.lower():
        document = re.sub(r'</head>', theme_css + '</head>', document, count=1, flags=re.IGNORECASE)
    else:
        document = theme_css + document
    if hasattr(st, 'iframe'):
        return st.iframe(document, height=height)
    return components.html(document, height=height, scrolling=scrolling)


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
    initial_sidebar_state="expanded",
)

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
</style>
""", unsafe_allow_html=True)

# Uyarıları küresel olarak kapatmayın; gerçek sorunları görünür bırakın.

# FastF1 Dahili Önbellek Klasörü
cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
os.makedirs(cache_dir, exist_ok=True)
fastf1.Cache.enable_cache(cache_dir)

# Kullanıcının lisanslı ses dosyasını koyacağı klasör. Dosya yoksa ses butonu
# tarayıcının nötr sesini kullanır; resmî takım telsizi uygulamayla gelmez.
assets_dir = os.path.join(os.path.dirname(__file__), 'assets')
os.makedirs(assets_dir, exist_ok=True)

# FastF1 Stil
fastf1.plotting.setup_mpl()

# SAYFA OTURUM DURUMU
if 'page' not in st.session_state:
    st.session_state['page'] = 'home'

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
@st.cache_data(ttl=60)
def get_current_or_next_event():
    """Takvim alınmasa da ana sayfayı kırmadan sıradaki gerçek seansı bulur."""
    now = datetime.datetime.now(datetime.timezone.utc)
    schedules = []
    for candidate_year in dict.fromkeys([now.year, 2026, now.year - 1]):
        try:
            candidate = fastf1.get_event_schedule(int(candidate_year), include_testing=False)
            candidate = candidate[candidate['RoundNumber'] > 0]
            if not candidate.empty:
                schedules.append(candidate)
        except Exception:
            continue

    if not schedules:
        # Sayfa açılmaya devam eder; bu veri sonucu veya hayalî yarış değildir.
        return pd.Series({'EventName': 'Takvim verisi bekleniyor', 'Location': 'Formula 1'}), 'Yarış', now, False

    schedule = schedules[0]
    
    session_cols = [
        ('FP1', 'Session1DateUtc'),
        ('FP2', 'Session2DateUtc'),
        ('FP3', 'Session3DateUtc'),
        ('Sıralama Turları', 'Session4DateUtc'),
        ('Yarış', 'Session5DateUtc')
    ]
    
    for idx, event in schedule.iterrows():
        for s_name, s_col in session_cols:
            if s_col in event and pd.notnull(event[s_col]):
                s_time = pd.to_datetime(event[s_col])
                s_time = s_time.tz_localize('UTC') if s_time.tzinfo is None else s_time.tz_convert('UTC')
                s_end_time = s_time + datetime.timedelta(hours=2)
                
                if s_time <= now <= s_end_time:
                    return event, s_name, s_time, True
                elif s_time > now:
                    return event, s_name, s_time, False
                    
    last_event = schedule.iloc[-1]
    last_time = pd.to_datetime(last_event['Session5DateUtc'])
    last_time = last_time.tz_localize('UTC') if last_time.tzinfo is None else last_time.tz_convert('UTC')
    return last_event, "Yarış", last_time, False

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


@st.cache_data(ttl=900, show_spinner=False)
def get_real_top_drivers(year, gp_name, session_code):
    """Sahte veri üretmez; Q seansında Q3 turunu ve o turun lastiğini kullanır."""
    try:
        sess = fastf1.get_session(int(year), gp_name, session_code)
        sess.load(telemetry=False, weather=False, messages=False)
        results = sess.results
        if results is None or results.empty:
            return [], []

        results = results.sort_values('Position', na_position='last').head(5)
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
    except Exception:
        return [], []


@st.cache_data(ttl=900, show_spinner=False)
def get_session_summary(year, gp_name, session_code):
    """Sonucun tekrarı yerine seansın doğrulanmış dikkat çeken anlarını verir."""
    try:
        session = fastf1.get_session(int(year), gp_name, session_code)
        session.load(laps=False, telemetry=False, weather=False, messages=True)
        results = session.results
        if results is None or results.empty:
            return []
        summary = []
        messages = getattr(session, 'race_control_messages', None)
        if messages is not None and not getattr(messages, 'empty', True):
            keywords = ('RED FLAG', 'SAFETY CAR', 'VIRTUAL SAFETY', 'CRASH', 'STOPPED', 'SPUN', 'YELLOW')
            seen_incidents = set()
            for _, row in messages.iloc[::-1].iterrows():
                message = str(row.get('Message', '')).strip()
                if message and any(word in message.upper() for word in keywords):
                    car_match = re.search(r'CAR\s+(\d+)\s*\(([^)]+)\)', message.upper())
                    driver_label = f"{car_match.group(2)} (#{car_match.group(1)})" if car_match else 'Bir pilot'
                    upper = message.upper()
                    if 'YELLOW FLAG INFRINGEMENT' in upper:
                        clean = f"⚠️ {driver_label} için sarı bayrak ihlali incelemesi başlatıldı."
                        incident_key = f"yellow-{driver_label}"
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
                if len(summary) >= 2:
                    break

        ordered = results.sort_values('Position', na_position='last').copy()
        if session_code == 'Q':
            q3_teams = ordered[pd.to_numeric(ordered.get('Position'), errors='coerce') <= 10].groupby('TeamName').size()
            double_q3 = q3_teams[q3_teams >= 2]
            for team_name in double_q3.index[:1]:
                summary.append(f"📈 {team_name}, iki pilotuyla Q3'e kaldı.")
        elif session_code in ['R', 'S'] and 'GridPosition' in ordered.columns:
            ordered['gain'] = pd.to_numeric(ordered['GridPosition'], errors='coerce') - pd.to_numeric(ordered['Position'], errors='coerce')
            biggest_gain = ordered.sort_values('gain', ascending=False).iloc[0]
            if pd.notnull(biggest_gain.get('gain')) and biggest_gain['gain'] >= 4:
                summary.append(f"⬆️ {biggest_gain.get('Abbreviation', 'Bir pilot')}, start yerine göre {int(biggest_gain['gain'])} sıra kazandı.")

        return summary[:3]
    except Exception:
        return []


# 3. OTOMATİK TÜRKÇE ÇEVİRİ MOTORU
def translate_to_tr(text):
    if not text:
        return ""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=tr&dt=t&q={urllib.parse.quote(text)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            translated = "".join([item[0] for item in data[0] if item[0]])
            return translated
    except Exception:
        return text

# 4. CANLI F1 HABERLERİ RSS MOTORU
@st.cache_data(ttl=600)
def fetch_live_f1_news():
    news_items = []
    rss_urls = [
        "https://www.autosport.com/rss/f1/news/",
        "https://www.skysports.com/rss/12433"
    ]
    
    for url in rss_urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                
                for item in root.findall('.//item')[:6]:
                    title = item.find('title').text if item.find('title') is not None else ""
                    link = item.find('link').text if item.find('link') is not None else "#"
                    pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                    desc = item.find('description').text if item.find('description') is not None else ""
                    
                    if desc and "<" in desc:
                        desc = desc.split("<")[0]
                        
                    news_items.append({
                        "title": translate_to_tr(title),
                        "link": link,
                        "date": pub_date[:16] if len(pub_date) > 16 else pub_date,
                        "desc": translate_to_tr(desc[:150] + "..." if len(desc) > 150 else desc)
                    })
            if news_items:
                break
        except Exception:
            continue
            
    # Akışlar erişilemiyorsa sahte/canlıymış gibi görünen içerik üretme.
    return news_items

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
def get_tyre_html(compound):
    comp = str(compound).upper() if pd.notnull(compound) else "SOFT"
    tyre_colors = {
        "SOFT": {"border": "#FF1801", "text": "#FF1801", "letter": "S"},
        "MEDIUM": {"border": "#FFE11A", "text": "#FFE11A", "letter": "M"},
        "HARD": {"border": "#FFFFFF", "text": "#FFFFFF", "letter": "H"},
        "INTERMEDIATE": {"border": "#39B54A", "text": "#39B54A", "letter": "I"},
        "WET": {"border": "#00AEEF", "text": "#00AEEF", "letter": "W"}
    }
    tc = tyre_colors.get(comp, tyre_colors["SOFT"])
    return f"""<span style="display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:50%; background:#000; border:2px solid {tc['border']}; color:{tc['text']}; font-weight:900; font-size:11px; font-family:sans-serif; margin-left:6px;" title="{comp} Tyre">{tc['letter']}</span>"""

# 7. DRIVER RENK VERİSİ
DRIVER_TEAMS = {
    "NOR": {"color": "#FF8000"}, "PIA": {"color": "#FF8000"},
    "HAM": {"color": "#E8002D"}, "LEC": {"color": "#E8002D"},
    "ANT": {"color": "#27F4D2"}, "RUS": {"color": "#27F4D2"},
    "VER": {"color": "#3671C6"}, "HAD": {"color": "#3671C6"},
    "GAS": {"color": "#FF87BC"}, "COL": {"color": "#FF87BC"},
    "LAW": {"color": "#6692FF"}, "LIN": {"color": "#6692FF"},
    "OCO": {"color": "#B6BABD"}, "BEA": {"color": "#B6BABD"},
    "ALB": {"color": "#64C4FF"}, "SAI": {"color": "#64C4FF"},
    "HUL": {"color": "#F50537"}, "BOR": {"color": "#F50537"},
    "ALO": {"color": "#229971"}, "STR": {"color": "#229971"},
    "PER": {"color": "#C0C0C0"}, "BOT": {"color": "#C0C0C0"},
}

TEAMS_DATA_2026 = {
    "McLaren": {
        "full_name": "McLaren Formula 1 Team (2026)",
        "nationality": "🇬🇧 İngiltere",
        "drivers": [
            {"name": "Lando Norris", "no": "#4", "age": 26, "nat": "🇬🇧 İngiltere", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/L/LANNOR01_Lando_Norris/lannor01.png"},
            {"name": "Oscar Piastri", "no": "#81", "age": 25, "nat": "🇦🇺 Avustralya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/O/OSCPIA01_Oscar_Piastri/oscpia01.png"}
        ]
    },
    "Ferrari": {
        "full_name": "Scuderia Ferrari HP (2026)",
        "nationality": "🇮🇹 İtalya",
        "drivers": [
            {"name": "Lewis Hamilton", "no": "#44", "age": 41, "nat": "🇬🇧 İngiltere", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/L/LEWHAM01_Lewis_Hamilton/lewham01.png"},
            {"name": "Charles Leclerc", "no": "#16", "age": 28, "nat": "🇲🇨 Monako", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/C/CHALEC01_Charles_Leclerc/chalec01.png"}
        ]
    },
    "Mercedes": {
        "full_name": "Mercedes-AMG PETRONAS F1 Team (2026)",
        "nationality": "🇩🇪 Almanya",
        "drivers": [
            {"name": "George Russell", "no": "#63", "age": 28, "nat": "🇬🇧 İngiltere", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/G/GEORUS01_George_Russell/georus01.png"},
            {"name": "Kimi Antonelli", "no": "#12", "age": 19, "nat": "🇮🇹 İtalya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/A/ANDANT01_Andrea_Kimi_Antonelli/andant01.png"}
        ]
    },
    "Red Bull": {
        "full_name": "Oracle Red Bull Racing (2026)",
        "nationality": "🇦🇹 Avusturya",
        "drivers": [
            {"name": "Max Verstappen", "no": "#1", "age": 28, "nat": "🇳🇱 Hollanda", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/M/MAXVER01_Max_Verstappen/maxver01.png"},
            {"name": "Liam Lawson", "no": "#30", "age": 24, "nat": "🇳🇿 Yeni Zelanda", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/L/LIALAW01_Liam_Lawson/lialaw01.png"}
        ]
    },
    "Williams": {
        "full_name": "Williams Racing (2026)",
        "nationality": "🇬🇧 İngiltere",
        "drivers": [
            {"name": "Carlos Sainz", "no": "#55", "age": 31, "nat": "🇪🇸 İspanya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/C/CARSAI01_Carlos_Sainz/carsai01.png"},
            {"name": "Alexander Albon", "no": "#23", "age": 30, "nat": "🇹🇭 Tayland", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/A/ALEALB01_Alexander_Albon/alealb01.png"}
        ]
    },
    "Aston Martin": {
        "full_name": "Aston Martin Aramco F1 Team (2026)",
        "nationality": "🇬🇧 İngiltere",
        "drivers": [
            {"name": "Fernando Alonso", "no": "#14", "age": 45, "nat": "🇪🇸 İspanya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/F/FERALO01_Fernando_Alonso/feralo01.png"},
            {"name": "Lance Stroll", "no": "#18", "age": 27, "nat": "🇨🇦 Kanada", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/L/LANSTR01_Lance_Stroll/lanstr01.png"}
        ]
    },
    "Alpine": {
        "full_name": "BWT Alpine F1 Team (2026)",
        "nationality": "🇫🇷 Fransa",
        "drivers": [
            {"name": "Pierre Gasly", "no": "#10", "age": 30, "nat": "🇫🇷 Fransa", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/P/PIEGAS01_Pierre_Gasly/piegas01.png"},
            {"name": "Jack Doohan", "no": "#7", "age": 23, "nat": "🇦🇺 Avustralya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/J/JACDOO01_Jack_Doohan/jacdoo01.png"}
        ]
    },
    "Haas": {
        "full_name": "MoneyGram Haas F1 Team (2026)",
        "nationality": "🇺🇸 ABD",
        "drivers": [
            {"name": "Esteban Ocon", "no": "#31", "age": 29, "nat": "🇫🇷 Fransa", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/E/ESTOCO01_Esteban_Ocon/estoco01.png"},
            {"name": "Oliver Bearman", "no": "#87", "age": 21, "nat": "🇬🇧 İngiltere", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/O/OLIBEA01_Oliver_Bearman/olibea01.png"}
        ]
    },
    "RB (Racing Bulls)": {
        "full_name": "Visa Cash App RB F1 Team (2026)",
        "nationality": "🇮🇹 İtalya",
        "drivers": [
            {"name": "Yuki Tsunoda", "no": "#22", "age": 26, "nat": "🇯🇵 Japonya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/Y/YUKTSU01_Yuki_Tsunoda/yuktsu01.png"},
            {"name": "Isack Hadjar", "no": "#6", "age": 21, "nat": "🇫🇷 Fransa", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/I/ISAHAD01_Isack_Hadjar/isahad01.png"}
        ]
    },
    "Sauber": {
        "full_name": "Stake F1 Team Kick Sauber (2026)",
        "nationality": "🇨🇭 İsviçre",
        "drivers": [
            {"name": "Nico Hülkenberg", "no": "#27", "age": 38, "nat": "🇩🇪 Almanya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/N/NICHUL01_Nico_Hulkenberg/nichul01.png"},
            {"name": "Gabriel Bortoleto", "no": "#5", "age": 21, "nat": "🇧🇷 Brezilya", "photo": "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/G/GABBOR01_Gabriel_Bortoleto/gabbor01.png"}
        ]
    }
}

# Güncel 2026 grid. Bu liste, eski sidebar verisinden bağımsız olarak
# Takımlar & Pilotlar merkezinde kullanılır.
MEDIA_DRIVER = "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/"
TEAM_DIRECTORY_2026 = {
    "Mercedes": {"slug": "mercedes", "color": "#27F4D2", "drivers": [("George Russell", "RUS", "#63", "G/GEORUS01_George_Russell/georus01.png"), ("Kimi Antonelli", "ANT", "#12", "A/ANDANT01_Andrea_Kimi_Antonelli/andant01.png")]},
    "Ferrari": {"slug": "ferrari", "color": "#E8002D", "drivers": [("Charles Leclerc", "LEC", "#16", "C/CHALEC01_Charles_Leclerc/chalec01.png"), ("Lewis Hamilton", "HAM", "#44", "L/LEWHAM01_Lewis_Hamilton/lewham01.png")]},
    "McLaren": {"slug": "mclaren", "color": "#FF8000", "drivers": [("Lando Norris", "NOR", "#1", "L/LANNOR01_Lando_Norris/lannor01.png"), ("Oscar Piastri", "PIA", "#81", "O/OSCPIA01_Oscar_Piastri/oscpia01.png")]},
    "Red Bull Racing": {"slug": "red-bull-racing", "color": "#3671C6", "drivers": [("Max Verstappen", "VER", "#3", "M/MAXVER01_Max_Verstappen/maxver01.png"), ("Isack Hadjar", "HAD", "#6", "I/ISAHAD01_Isack_Hadjar/isahad01.png")]},
    "Alpine": {"slug": "alpine", "color": "#FF87BC", "drivers": [("Pierre Gasly", "GAS", "#10", "P/PIEGAS01_Pierre_Gasly/piegas01.png"), ("Franco Colapinto", "COL", "#43", "F/FRACOL01_Franco_Colapinto/fracol01.png")]},
    "Racing Bulls": {"slug": "racing-bulls", "color": "#6692FF", "drivers": [("Liam Lawson", "LAW", "#30", "L/LIALAW01_Liam_Lawson/lialaw01.png"), ("Arvid Lindblad", "LIN", "#41", "A/ARVLIND01_Arvid_Lindblad/arvlind01.png")]},
    "Haas F1 Team": {"slug": "haas", "color": "#B6BABD", "drivers": [("Esteban Ocon", "OCO", "#31", "E/ESTOCO01_Esteban_Ocon/estoco01.png"), ("Oliver Bearman", "BEA", "#87", "O/OLIBEA01_Oliver_Bearman/olibea01.png")]},
    "Williams": {"slug": "williams", "color": "#64C4FF", "drivers": [("Carlos Sainz", "SAI", "#55", "C/CARSAI01_Carlos_Sainz/carsai01.png"), ("Alexander Albon", "ALB", "#23", "A/ALEALB01_Alexander_Albon/alealb01.png")]},
    "Audi": {"slug": "audi", "color": "#F50537", "drivers": [("Nico Hulkenberg", "HUL", "#27", "N/NICHUL01_Nico_Hulkenberg/nichul01.png"), ("Gabriel Bortoleto", "BOR", "#5", "G/GABBOR01_Gabriel_Bortoleto/gabbor01.png")]},
    "Aston Martin": {"slug": "aston-martin", "color": "#229971", "drivers": [("Fernando Alonso", "ALO", "#14", "F/FERALO01_Fernando_Alonso/feralo01.png"), ("Lance Stroll", "STR", "#18", "L/LANSTR01_Lance_Stroll/lanstr01.png")]},
    "Cadillac": {"slug": "cadillac", "color": "#C0C0C0", "drivers": [("Sergio Perez", "PER", "#11", "S/SERPER01_Sergio_Perez/serper01.png"), ("Valtteri Bottas", "BOT", "#77", "V/VALBOT01_Valtteri_Bottas/valbot01.png")]},
}

OFFICIAL_TEAM_LOGOS = {
    "Mercedes": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/mercedes/2025mercedeslogowhite.webp",
    "Ferrari": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/ferrari/2025ferrarilogolight.webp",
    "McLaren": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/mclaren/2025mclarenlogowhite.webp",
    "Red Bull Racing": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/redbullracing/2025redbullracinglogowhite.webp",
    "Alpine": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/alpine/2025alpinelogowhite.webp",
    "Racing Bulls": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/racingbulls/2025racingbullslogowhite.webp",
    "Haas F1 Team": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/haas/2025haaslogowhite.webp",
    "Williams": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/williams/2025williamslogowhite.webp",
    "Audi": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2026/audi/2026audilogowhite.webp",
    "Aston Martin": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/astonmartin/2025astonmartinlogowhite.webp",
    "Cadillac": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2026/cadillac/2026cadillaclogowhite.webp",
}

# 2026 takım renkleriyle çekilmiş resmî portreler. Eski sürücünün önceki
# takımındaki fotoğrafını göstermek yerine F1 Media'nın 2026 görüntüsünü kullanır.
TEAM_MEDIA_NAMES = {
    "Mercedes": "mercedes", "Ferrari": "ferrari", "McLaren": "mclaren",
    "Red Bull Racing": "redbullracing", "Alpine": "alpine",
    "Racing Bulls": "racingbulls", "Haas F1 Team": "haas",
    "Williams": "williams", "Audi": "audi", "Aston Martin": "astonmartin",
    "Cadillac": "cadillac",
}


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


TEAM_HISTORY = {
    "Mercedes": "Mercedes, modern hibrit çağın belirleyici takımıdır. Brackley merkezli ekip, 2014 sonrası dönemde üst üste şampiyonluklarla F1 tarihine geçti.",
    "Ferrari": "Ferrari, 1950'den beri Formula 1'de yarışan tek takımdır. Maranello ekibi, serinin en köklü yarış miraslarından birine sahiptir.",
    "McLaren": "McLaren, Bruce McLaren tarafından kuruldu. Takım; Senna, Prost, Hakkinen ve Hamilton gibi isimlerle F1 tarihinin en önemli ekipleri arasına girdi.",
    "Red Bull Racing": "Red Bull Racing 2005'te F1'e katıldı. Takım, önce Vettel dönemi ardından Verstappen dönemiyle şampiyonluklar kazandı.",
    "Alpine": "Alpine markası, Renault'nun Formula 1 mirasını temsil eder. Enstone merkezli takım, geçmişte Renault adıyla dünya şampiyonlukları yaşadı.",
    "Racing Bulls": "Faenza merkezli ekip, Red Bull'un genç yetenek programıyla bağlantılıdır. Takım geçmişte Toro Rosso ve AlphaTauri isimleriyle yarıştı.",
    "Haas F1 Team": "Haas, 2016'da Formula 1'e girdi. Amerikan lisanslı ekip, modern F1'in en genç takımlarından biridir.",
    "Williams": "Williams, Formula 1'in en başarılı bağımsız takımlarındandır. Frank Williams'ın kurduğu ekip, birçok sürücü ve takımlar şampiyonluğu elde etti.",
    "Audi": "Audi 2026'da Formula 1'e fabrika takımı olarak katıldı. Proje, markanın uzun motorsporları geçmişini F1'e taşıyor.",
    "Aston Martin": "Aston Martin adı F1'de ilk kez 1959'da göründü; modern fabrika takımı ise Silverstone merkezli yapının devamıdır.",
    "Cadillac": "Cadillac, 2026'da Formula 1 gridine katılan yeni Amerikan fabrikacı markadır. Takım, serinin 11. ekibi olarak yarışıyor.",
}

# Verified team leadership is kept separate from the game engineer packages.
# Individual race-engineer assignments are not a stable public roster, so the
# game never invents a real person's identity or photo for that role.
TEAM_LEADERSHIP_2026 = {
    "Mercedes": {"name": "Toto Wolff", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/fom-website/2025/Qatar/GettyImages-2249023480.webp", "bio": "Mercedes takım patronu. Oyunda strateji ve liderlik bonusu sağlar.", "strategy": 4, "reliability": 3},
    "Ferrari": {"name": "Fred Vasseur", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Brazil___Previews/2245297636.webp", "bio": "Ferrari takım patronu. Oyunda pit duvarı ve yarış temposu bonusu sağlar.", "strategy": 4, "reliability": 2},
    "McLaren": {"name": "Andrea Stella", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Brazil/2245811681.webp", "bio": "McLaren takım patronu. Oyunda lastik ve strateji bonusu sağlar.", "strategy": 5, "reliability": 2},
    "Red Bull Racing": {"name": "Laurent Mekies", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Abu_Dhabi___Practice/2250117585.webp", "bio": "Red Bull Racing takım patronu. Oyunda performans ve karar hızı bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Alpine": {"name": "Steve Nielsen", "role": "Yönetim Ekibi", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2024/F1_Grand_Prix_of_Austria___Sprint__Qualifying/2159773387.webp", "bio": "Alpine yönetim ekibi. Oyunda denge ve geliştirme bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Racing Bulls": {"name": "Alan Permane", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Abu_Dhabi___Previews/2249891571.webp", "bio": "Racing Bulls takım patronu. Oyunda geliştirme ve pit kararı bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Haas F1 Team": {"name": "Ayao Komatsu", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Las_Vegas___Previews/2247510876.webp", "bio": "Haas takım patronu. Oyunda ayar ve güvenilirlik bonusu sağlar.", "strategy": 2, "reliability": 4},
    "Williams": {"name": "James Vowles", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Las_Vegas___Previews/2247516844.webp", "bio": "Williams takım patronu. Oyunda uzun vadeli gelişim bonusu sağlar.", "strategy": 4, "reliability": 3},
    "Audi": {"name": "Jonathan Wheatley", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/fom-website/2025/Austria/GettyImages-2222409477.webp", "bio": "Audi takım patronu. Oyunda operasyon ve güvenilirlik bonusu sağlar.", "strategy": 3, "reliability": 4},
    "Aston Martin": {"name": "Adrian Newey", "role": "Takım Patronu / Teknik Ortak", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Monaco___Previews/2216517332.webp", "bio": "Aston Martin teknik yönetimi. Oyunda aerodinami ve sıralama bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Cadillac": {"name": "Graeme Lowdon", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/fom-website/2025/Cadillac%20%28GM%29/GettyImages-2233817654.webp", "bio": "Cadillac takım patronu. Oyunda yeni takım uyumu bonusu sağlar.", "strategy": 3, "reliability": 3},
}

GAME_ENGINEERING_PACKAGES = {
    "Strategist": {"title": "Pit Duvarı Stratejisti", "description": "Lastik ömrü, undercut ve pit penceresi odaklı oyun paketi.", "strategy": 5, "pace": 1, "reliability": 0},
    "Performance": {"title": "Araç Performans Lideri", "description": "Ayar, sıralama hızı ve yarış temposu odaklı oyun paketi.", "strategy": 1, "pace": 5, "reliability": 1},
    "Reliability": {"title": "Güvenilirlik Şefi", "description": "Motor, soğutma ve uzun stint riski odaklı oyun paketi.", "strategy": 1, "pace": 1, "reliability": 5},
}

DRIVER_BIRTHDAYS = {
    'RUS': '1998-02-15', 'ANT': '2006-08-25', 'LEC': '1997-10-16', 'HAM': '1985-01-07',
    'NOR': '1999-11-13', 'PIA': '2001-04-06', 'VER': '1997-09-30', 'HAD': '2004-09-28',
    'GAS': '1996-02-07', 'COL': '2003-05-27', 'LAW': '2002-02-11', 'LIN': '2007-08-08',
    'OCO': '1996-09-17', 'BEA': '2005-05-08', 'SAI': '1994-09-01', 'ALB': '1996-03-23',
    'HUL': '1987-08-19', 'BOR': '2004-10-14', 'ALO': '1981-07-29', 'STR': '1998-10-29',
    'PER': '1990-01-26', 'BOT': '1989-08-28',
}

# Takım sayfasındaki kısa biyografiler, sezon sonucu değil kariyer dosyasıdır.
# Böylece pilot kartları yalnızca isim/fotoğraf olmaktan çıkar; her profilde
# izleyenin anlayabileceği bir geçmiş ve hatırlanacak bir yarış anı bulunur.
DRIVER_CAREER_PROFILE = {
    'RUS': {'wins': 7, 'podiums': 29, 'bio': 'Williams ile başlayan kariyerini Mercedes liderliğine taşıyan İngiliz pilot; GP3 ve F2 şampiyonluklarından sonra F1’de istikrarlı hızını öne çıkardı.', 'moment': 'İlk Grand Prix galibiyetini 2022 Sao Paulo’da aldı.'},
    'ANT': {'wins': 5, 'podiums': 8, 'bio': 'Mercedes genç sürücü programından gelen İtalyan pilot, tek koltuklu serilerdeki hızlı yükselişiyle F1’e adım attı.', 'moment': '2026’da ilk galibiyetini alarak Mercedes’in genç kuşağının öne çıkan ismi oldu.'},
    'LEC': {'wins': 8, 'podiums': 46, 'bio': 'Monakolu sürücü Ferrari Akademisi üzerinden F1’e geldi. Tek tur hızı ve lastik yönetimi onu gridin en güçlü isimlerinden biri yaptı.', 'moment': '2019 Belçika GP, ilk F1 galibiyeti ve Ferrari ile dönüm noktasıydı.'},
    'HAM': {'wins': 106, 'podiums': 204, 'bio': 'Yedi kez dünya şampiyonu olan Hamilton, kartingden F1’e uzanan kariyerinde rekorları ve uzun soluklu yarış yönetimiyle tanındı.', 'moment': '2008’de ilk dünya şampiyonluğunu son virajlarda gelen dramatik Brezilya finalinde kazandı.'},
    'NOR': {'wins': 10, 'podiums': 31, 'bio': 'McLaren’in genç programından yetişen Norris, güçlü yağmur sürüşleri ve agresif tek tur temposuyla modern gridin lider isimlerinden biri oldu.', 'moment': '2024 Miami GP ilk F1 galibiyetiydi; ardından şampiyonluk mücadelesine yerleşti.'},
    'PIA': {'wins': 7, 'podiums': 18, 'bio': 'Avustralyalı pilot, Formula Renault, F3 ve F2 şampiyonluklarını art arda kazanarak F1’e yükseldi.', 'moment': '2024 Macaristan GP’de ilk F1 galibiyetini alarak McLaren tarihinde yerini aldı.'},
    'VER': {'wins': 67, 'podiums': 129, 'bio': 'Çok genç yaşta F1’e çıkan Hollandalı, Red Bull ile çok sayıda şampiyonluk ve galibiyet mücadelesi verdi.', 'moment': '2016 İspanya GP’de ilk Red Bull yarışında zafere ulaşarak en genç yarış galibi oldu.'},
    'HAD': {'wins': 0, 'podiums': 1, 'bio': 'Fransız sürücü, Red Bull genç programının F1’e taşıdığı hızlı tek tur yeteneklerinden biri olarak öne çıktı.', 'moment': 'İlk F1 podyumu, genç kariyerinin önemli kilometre taşlarından biri oldu.'},
    'GAS': {'wins': 1, 'podiums': 5, 'bio': 'Normandiya kökenli Gasly, Formula Renault Eurocup şampiyonluğundan sonra F1’e yükseldi ve dayanıklılığıyla tanındı.', 'moment': '2020 İtalya GP’de kazandığı galibiyet, AlphaTauri için unutulmaz bir zaferdi.'},
    'COL': {'wins': 0, 'podiums': 0, 'bio': 'Arjantinli pilot, Williams ile F1’e giriş yaptıktan sonra tek tur temposu ve cesur geçişleriyle dikkat çekti.', 'moment': 'F1’e ilk puanlarını 2024 Azerbaycan GP hafta sonunda getirdi.'},
    'LAW': {'wins': 0, 'podiums': 0, 'bio': 'Yeni Zelandalı sürücü, Super Formula’daki güçlü performansının ardından Red Bull yapısında F1 fırsatı buldu.', 'moment': 'İlk F1 yarışlarında puan alarak programdaki yerini sağlamlaştırdı.'},
    'LIN': {'wins': 0, 'podiums': 0, 'bio': 'İngiliz genç sürücü, karting ve tek koltuklu serilerden F1 gridine yükselen yeni neslin temsilcisi.', 'moment': '2026 F1 başlangıcı, kariyerinin en büyük basamağıdır.'},
    'OCO': {'wins': 1, 'podiums': 3, 'bio': 'Fransız pilot, kartingden F3 ve GP3 şampiyonluğuna uzanan yolculuğun ardından F1’de yerini aldı.', 'moment': '2021 Macaristan GP’de kazandığı yarış, hem kendi hem Alpine/Renault mirası için özel bir zaferdi.'},
    'BEA': {'wins': 0, 'podiums': 1, 'bio': 'Ferrari Akademisi kökenli İngiliz sürücü, F2’deki yükselişinin ardından F1’de hızla dikkat çekti.', 'moment': '2024 Azerbaycan GP’deki podyum, çaylak sezonunun en büyük anıydı.'},
    'SAI': {'wins': 4, 'podiums': 27, 'bio': 'İspanyol sürücü, Red Bull genç programından Toro Rosso üzerinden F1’e yükseldi; temiz yarış yönetimiyle bilinir.', 'moment': '2022 Britanya GP’de ilk F1 galibiyetini aldı.'},
    'ALB': {'wins': 0, 'podiums': 2, 'bio': 'Tayland bayrağı altında yarışan Albon, zorlu bir ilk F1 döneminden sonra Williams ile kariyerini yeniden kurdu.', 'moment': '2020’de aldığı iki podyum, ilk F1 sezonlarının güçlü notlarıydı.'},
    'HUL': {'wins': 0, 'podiums': 0, 'bio': 'Alman sürücü, GP2 şampiyonluğunun ardından uzun F1 deneyimini teknik geri bildirim gücüyle birleştirdi.', 'moment': '2015 Le Mans 24 Saat zaferi, F1 dışındaki en önemli başarısıdır.'},
    'BOR': {'wins': 0, 'podiums': 0, 'bio': 'Brezilyalı sürücü, Formula 3 ve Formula 2 başarılarından sonra F1’e çıkan yeni nesil yeteneklerden biri.', 'moment': 'F1’deki ilk puanları, Audi projesi için önemli bir kilometre taşıdır.'},
    'ALO': {'wins': 32, 'podiums': 106, 'bio': 'İki kez dünya şampiyonu Alonso, uzun kariyerini farklı takımlarda rekabetçi kalmayı başararak sürdürdü.', 'moment': '2005’te aldığı ilk dünya şampiyonluğu, Schumacher dönemini sona erdirdi.'},
    'STR': {'wins': 0, 'podiums': 3, 'bio': 'Kanadalı sürücü, tek turdaki doğal hızı ve yağmur koşullarındaki performansıyla bilinir.', 'moment': '2017 Azerbaycan GP podyumu, çaylak sezonunun unutulmaz anıydı.'},
    'PER': {'wins': 6, 'podiums': 39, 'bio': 'Meksikalı sürücü, uzun stintlerde lastik yönetimi ve savunma becerisiyle öne çıktı.', 'moment': '2020 Sakhir GP’de ilk F1 galibiyetini aldı.'},
    'BOT': {'wins': 10, 'podiums': 67, 'bio': 'Fin sürücü, Williams’tan Mercedes’e geçerek galibiyetler ve takımlar şampiyonlukları mücadelesinde rol aldı.', 'moment': '2017 Rusya GP, ilk F1 galibiyetiydi.'},
}


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


JUNIOR_TEAM_SLUGS = {
    'Invicta Racing': 'invictaracing', 'Hitech': 'hitech', 'Campos Racing': 'camposracing',
    'DAMS Lucas Oil': 'damslucasoil', 'MP Motorsport': 'mpmotorsport', 'PREMA Racing': 'premaracing',
    'Rodin Motorsport': 'rodinmotorsport', 'ART Grand Prix': 'artgrandprix', 'AIX Racing': 'aixracing',
    'Van Amersfoort Racing': 'vanamersfoortracing', 'TRIDENT': 'trident',
}


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


def _legacy_render_junior_team_hud(series, grid, accent, official_base):
    """F2/F3 için takım seçilebilen, logo ve güncel pilot görselli HUD."""
    state_key = f'{series}_team_focus'
    teams = list(grid.keys())
    if state_key not in st.session_state:
        st.session_state[state_key] = teams[0]
    for start in range(0, len(teams), 3):
        columns = st.columns(3)
        for column, team_name in zip(columns, teams[start:start + 3]):
            drivers = grid[team_name].split(' • ')
            with column:
                with st.container(border=True):
                    st.markdown(f"<div style='height:3px;background:{accent};border-radius:6px;margin:-2px -2px 10px'></div>", unsafe_allow_html=True)
                    st.image(junior_team_logo(series, team_name), width=78)
                    if st.button(team_name, key=f'{series}_{team_name}', use_container_width=True):
                        st.session_state[state_key] = team_name
                        st.rerun()
                    st.caption(' • '.join(drivers))

    selected_team = st.session_state[state_key]
    selected_drivers = grid[selected_team].split(' • ')
    team_slug = JUNIOR_TEAM_SLUGS.get(selected_team, re.sub(r'[^a-z0-9]', '', selected_team.lower()))
    st.markdown('---')
    logo_column, copy_column, action_column = st.columns([.7, 2.4, 1.2])
    with logo_column:
        st.image(junior_team_logo(series, selected_team), width=110)
    with copy_column:
        st.markdown(f"<div class='hud-label'>{series.upper()} // 2026 TEAM DOSYASI</div><div style='font-size:1.7rem;font-weight:900'>{selected_team}</div><div class='history-copy' style='margin-top:6px'>Resmî 2026 kadrosu. Pilot kartları takım seçimine göre güncellenir.</div>", unsafe_allow_html=True)
    with action_column:
        st.link_button('Resmî takım profili ↗', f'{official_base}/en/teams/{team_slug}', use_container_width=True)

    driver_columns = st.columns(len(selected_drivers))
    for column, driver_name in zip(driver_columns, selected_drivers):
        with column:
            portrait = junior_driver_portrait(series, selected_team, driver_name)
            st.markdown(
                f"<div class='hud-card' style='border-top:3px solid {accent};text-align:center;min-height:260px'>"
                f"<img src='{portrait}' style='height:175px;max-width:100%;object-fit:contain' onerror=\"this.style.display='none'\">"
                f"<div style='font-weight:900;font-size:1rem;margin-top:7px'>{driver_name}</div>"
                f"<div class='driver-meta'>{selected_team} • 2026</div></div>",
                unsafe_allow_html=True
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
                if st.button(f"Takımı aç: {team_name}", key=f'v19_{series}_{team_name}', use_container_width=True):
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
            f"<div style='font-size:1.7rem;font-weight:950;color:#f5f9ff;margin-top:3px'>{html_lib.escape(selected_team)}</div>"
            f"<div class='history-copy' style='margin-top:7px'>Seçili takımın güncel grid kadrosu. Pilot kartlarında fotoğraf kaynağı yoksa isim ve takım bilgisi yine görünür.</div>",
            unsafe_allow_html=True,
        )
    with head_action:
        st.link_button('Resmî takım profili ↗', f'{official_base}/en/teams/{team_slug}', use_container_width=True)

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
                f"<div style='font-size:1.02rem;font-weight:900;color:#f5f9ff;margin-top:10px'>{html_lib.escape(driver_name)}</div>"
                f"<div class='driver-meta'>{html_lib.escape(selected_team)} · Resmî portre + takım aracı</div></div>",
                unsafe_allow_html=True,
            )


# V19 görünümü eski çağrıyı bozmadan yeni, sabit HUD şablonunu kullanır.
render_junior_team_hud = render_junior_team_hud_v19

# 9. CSS TASARIMI
st.markdown("""
<style>
    .stApp {
        background:
            radial-gradient(circle at 88% -10%, rgba(36, 99, 235, .13), transparent 30%),
            radial-gradient(circle at 12% 8%, rgba(16, 185, 129, .08), transparent 23%),
            #090d14;
        color: #F1F5F9;
    }
    section[data-testid="stSidebar"] {
        background-color: #111827 !important;
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
    .hud-value { color:#f7fbff; font-size:1rem; font-weight:800; margin-top:3px; }
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
        background: linear-gradient(180deg,#0e1728 0%,#101827 100%) !important;
    }

    .news-card {
        background: #111827;
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
        background: #111827;
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


def site_theme_css():
    """Sabit koyu renkler yerine uygulamanın tamamına tema değişkenlerini uygular."""
    if current_paddock_theme() == 'Açık':
        return """
        <style>
        .stApp{color:#10223a!important;background:#f4f8fc!important}
        section[data-testid="stSidebar"]{background:linear-gradient(180deg,#f7fbff,#e9f0f8)!important;border-right:1px solid #cbd9e8!important}
        section[data-testid="stSidebar"] *{color:#152943!important}
        .f1-header,.hud-card,.metric-card,.news-card,.driver-card{background:rgba(255,255,255,.88)!important;border-color:#cdd9e8!important;box-shadow:0 12px 30px rgba(26,58,91,.08)!important}
        .f1-header h1,.hud-value,.news-title,.metric-card .value{color:#10223a!important}.f1-header p,.history-copy,.driver-meta,.news-desc,.metric-card .title{color:#59708a!important}
        div[data-testid="stButton"]>button{background:#ffffff!important;color:#142842!important;border-color:#bfd0e3!important}
        div[data-testid="stButton"]>button:hover{background:#edf5fc!important;border-color:#1677c8!important;color:#0f4d80!important}
        section[data-testid="stSidebar"] div[data-testid="stButton"]>button{background:linear-gradient(135deg,#ffffff,#edf4fb)!important;color:#17304e!important;border-color:#bfd0e3!important}
        section[data-testid="stSidebar"] .stExpander{background:rgba(255,255,255,.72)!important;border-color:#c7d6e5!important}
        div[data-testid="stDataFrame"]{border-color:#cbd9e8!important} [data-testid="stMarkdownContainer"] code{background:#e8eff7!important;color:#124d80!important}
        </style>
        """
    return """
    <style>
    .stApp{position:relative;color:#edf6ff!important;background:#070c13!important}
    section[data-testid="stSidebar"]{background:linear-gradient(180deg,rgba(10,21,39,.98),rgba(12,26,45,.98))!important;border-right:1px solid #28405e!important}
    section[data-testid="stSidebar"] div[data-testid="stButton"]>button{min-height:44px!important;padding:8px 12px!important;background:linear-gradient(135deg,rgba(17,34,57,.92),rgba(12,25,43,.92))!important;border:1px solid #304a69!important;border-left:4px solid #3b82c4!important;border-radius:10px!important;color:#eaf4ff!important;font-size:.88rem!important;font-weight:850!important;letter-spacing:.01em!important;transition:transform .18s ease,border-color .18s ease,background .18s ease!important}
    section[data-testid="stSidebar"] div[data-testid="stButton"]>button:hover{transform:translateX(3px)!important;border-color:#63c7ff!important;background:linear-gradient(135deg,#162c49,#10213a)!important}
    section[data-testid="stSidebar"] .stExpander{background:rgba(15,30,51,.66)!important;border:1px solid #2c4665!important;border-radius:10px!important}
    section[data-testid="stSidebar"] [data-testid="stExpanderDetails"]{padding-top:.25rem!important}
    </style>
    """


st.markdown(site_theme_css(), unsafe_allow_html=True)


@st.cache_data(ttl=3600, show_spinner=False)
def get_season_schedule(year):
    try:
        schedule = fastf1.get_event_schedule(year, include_testing=False)
        schedule = schedule[schedule['RoundNumber'] > 0]
        return schedule['EventName'].dropna().astype(str).tolist()
    except Exception as error:
        log_data_error('season schedule', error)
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def get_calendar_details(year):
    try:
        schedule = fastf1.get_event_schedule(int(year), include_testing=False)
        schedule = schedule[schedule['RoundNumber'] > 0].copy()
        return schedule.to_dict('records')
    except Exception:
        return []


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


@st.cache_data(ttl=900, show_spinner=False)
def get_session_results_table(year, event_name, session_code):
    """Seans türüne göre yalnızca anlamlı sonuç sütunlarını gösterir.

    Antrenmanların resmî sonuç tablosunda pozisyon ve zaman boş olabilir. Bu
    yüzden FP seanslarında hızlı turlardan kendi sıralamamızı üretiriz.
    """
    try:
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
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


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


@st.cache_data(ttl=1800, show_spinner=False)
def get_session_story(year, event_name, session_code):
    """Seans sonucunu ve varsa Race Control notlarını kısa, doğrulanabilir hikâyeye çevirir."""
    try:
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
    except Exception:
        return []


COUNTRY_FLAGS = {
    'Australia': '🇦🇺', 'China': '🇨🇳', 'Japan': '🇯🇵', 'Bahrain': '🇧🇭',
    'Saudi Arabia': '🇸🇦', 'United States': '🇺🇸', 'Italy': '🇮🇹',
    'Monaco': '🇲🇨', 'Spain': '🇪🇸', 'Canada': '🇨🇦', 'Austria': '🇦🇹',
    'Great Britain': '🇬🇧', 'Belgium': '🇧🇪', 'Hungary': '🇭🇺',
    'Netherlands': '🇳🇱', 'Azerbaijan': '🇦🇿', 'Singapore': '🇸🇬',
    'Mexico': '🇲🇽', 'Brazil': '🇧🇷', 'Qatar': '🇶🇦', 'United Arab Emirates': '🇦🇪',
}

COUNTRY_CODES = {
    'Australia': 'au', 'China': 'cn', 'Japan': 'jp', 'Bahrain': 'bh', 'Saudi Arabia': 'sa',
    'United States': 'us', 'Italy': 'it', 'Monaco': 'mc', 'Spain': 'es', 'Canada': 'ca',
    'Austria': 'at', 'Great Britain': 'gb', 'Belgium': 'be', 'Hungary': 'hu', 'Netherlands': 'nl',
    'Azerbaijan': 'az', 'Singapore': 'sg', 'Mexico': 'mx', 'Brazil': 'br', 'Qatar': 'qa',
    'United Arab Emirates': 'ae',
}


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
    """FastF1'in boş/NA puanlarını güvenli şekilde sıfır yapar."""
    try:
        return 0.0 if pd.isnull(value) else float(value)
    except Exception:
        return 0.0


def format_points(value):
    """18.0 yerine 18; gerektiğinde 0.5 gibi gerçek yarım puanı korur."""
    try:
        number = float(value)
        if not np.isfinite(number):
            return '—'
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}".rstrip('0').rstrip('.')
    except (TypeError, ValueError):
        return '—' if value is None or pd.isna(value) else str(value)


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


@st.cache_data(ttl=3600, show_spinner=False)
def get_championship_data(year):
    """Tamamlanan yarış ve sprintlerin gerçek FastF1 sonuçlarından puan merkezi üretir."""
    now = datetime.datetime.now(datetime.timezone.utc)
    schedule = get_calendar_details(int(year))
    completed_events = []
    driver_totals = {}
    team_totals = {}
    per_driver_round = {}

    for event in schedule:
        race_date = event.get('Session5DateUtc')
        if pd.isnull(race_date):
            continue
        race_time = pd.to_datetime(race_date)
        race_time = race_time.tz_localize('UTC') if race_time.tzinfo is None else race_time.tz_convert('UTC')
        if race_time + datetime.timedelta(hours=3) > now:
            continue

        event_name = str(event.get('EventName', ''))
        if not event_name:
            continue
        round_info = {
            'event_name': event_name,
            'badge': round_badge(event),
            'key': round_key(event),
            'country_code': COUNTRY_CODES.get(str(event.get('Country', '')), 'un'),
            'has_sprint': False,
        }
        event_results = {}

        try:
            race_session = fastf1.get_session(int(year), event_name, 'R')
            # Puan Merkezi telemetri ya da tur verisi istemez. Bu ayar ilk
            # girişte indirilen veriyi dramatik biçimde küçültür.
            race_session.load(laps=False, telemetry=False, weather=False, messages=False)
            race_results = race_session.results
            if race_results is None or race_results.empty:
                continue
            for _, row in race_results.iterrows():
                code = str(row.get('Abbreviation', '')).strip()
                if not code or code == 'nan':
                    continue
                position = row.get('Position')
                position_text = str(int(float(position))) if pd.notnull(position) else 'DNF'
                team = str(row.get('TeamName', '—')).strip()
                points = points_value(row.get('Points', 0))
                event_results[code] = {'race': position_text, 'sprint': '', 'team': team}
                driver_totals.setdefault(code, {'Pilot': code, 'Takım': team, 'Puan': 0.0})
                driver_totals[code]['Puan'] += points
                team_totals[team] = team_totals.get(team, 0.0) + points
        except Exception:
            continue

        # Sprint olmayan yarışlarda hata vermeden boş kalır; olanlarda R / S görünür.
        try:
            sprint_session = fastf1.get_session(int(year), event_name, 'S')
            sprint_session.load(laps=False, telemetry=False, weather=False, messages=False)
            sprint_results = sprint_session.results
            if sprint_results is not None and not sprint_results.empty:
                event_has_sprint = False
                for _, row in sprint_results.iterrows():
                    code = str(row.get('Abbreviation', '')).strip()
                    if not code or code == 'nan':
                        continue
                    position = row.get('Position')
                    if code not in event_results:
                        continue
                    event_results[code]['sprint'] = str(int(float(position))) if pd.notnull(position) else 'DNF'
                    points = points_value(row.get('Points', 0))
                    driver_totals[code]['Puan'] += points
                    team = event_results[code]['team']
                    team_totals[team] = team_totals.get(team, 0.0) + points
                    event_has_sprint = True
                round_info['has_sprint'] = event_has_sprint
        except Exception:
            pass

        for code, result in event_results.items():
            per_driver_round.setdefault(code, {})[event_name] = result
        completed_events.append(round_info)

    driver_rows = sorted(driver_totals.values(), key=lambda row: (-row['Puan'], row['Pilot']))
    for index, row in enumerate(driver_rows, start=1):
        row['Sıra'] = index
        row['Puan'] = int(row['Puan']) if float(row['Puan']).is_integer() else round(row['Puan'], 1)

    team_rows = [{'Takım': team, 'Puan': int(points) if float(points).is_integer() else round(points, 1)} for team, points in team_totals.items()]
    team_rows.sort(key=lambda row: (-row['Puan'], row['Takım']))
    for index, row in enumerate(team_rows, start=1):
        row['Sıra'] = index

    matrix_rows = []
    for row in driver_rows:
        code = row['Pilot']
        matrix_row = {'Pilot': code, 'Takım': row['Takım'], 'Puan': row['Puan']}
        for event in completed_events:
            result = per_driver_round.get(code, {}).get(event['event_name'])
            if not result:
                matrix_row[event['key']] = '—'
            elif event['has_sprint']:
                matrix_row[event['key']] = f"{result['race']} / {result['sprint'] or '—'}"
            else:
                matrix_row[event['key']] = result['race']
        matrix_rows.append(matrix_row)

    return pd.DataFrame(driver_rows), pd.DataFrame(team_rows), pd.DataFrame(matrix_rows), completed_events


@st.cache_data(ttl=21600, show_spinner=False)
def get_championship_round_v19(year, event_name):
    """Bir GP'yi ayrı önbelleğe alır; tek bir yarışın hatası tüm Puan Merkezi'ni kilitlemez."""
    output = {'ok': False, 'race': [], 'sprint': []}
    try:
        race = fastf1.get_session(int(year), event_name, 'R')
        race.load(laps=False, telemetry=False, weather=False, messages=False)
        results = race.results
        if results is None or results.empty:
            return output
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
    except Exception:
        return output

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


DRIVER_DISPLAY = {
    'ANT': ('it', 'A. Antonelli'), 'HAM': ('gb', 'L. Hamilton'), 'RUS': ('gb', 'G. Russell'),
    'LEC': ('mc', 'C. Leclerc'), 'NOR': ('gb', 'L. Norris'), 'PIA': ('au', 'O. Piastri'),
    'VER': ('nl', 'M. Verstappen'), 'HAD': ('fr', 'I. Hadjar'), 'GAS': ('fr', 'P. Gasly'),
    'COL': ('ar', 'F. Colapinto'), 'LAW': ('nz', 'L. Lawson'), 'LIN': ('gb', 'A. Lindblad'),
    'OCO': ('fr', 'E. Ocon'), 'BEA': ('gb', 'O. Bearman'), 'SAI': ('es', 'C. Sainz'),
    'ALB': ('th', 'A. Albon'), 'HUL': ('de', 'N. Hülkenberg'), 'BOR': ('br', 'G. Bortoleto'),
    'ALO': ('es', 'F. Alonso'), 'STR': ('ca', 'L. Stroll'), 'PER': ('mx', 'S. Pérez'),
    'BOT': ('fi', 'V. Bottas'),
}


TEAM_NAME_ALIASES = {
    'Red Bull': 'Red Bull Racing',
    'Oracle Red Bull Racing': 'Red Bull Racing',
    'Visa Cash App RB': 'Racing Bulls',
    'RB': 'Racing Bulls',
    'Haas': 'Haas F1 Team',
    'MoneyGram Haas F1 Team': 'Haas F1 Team',
    'Kick Sauber': 'Audi',
    'Stake F1 Team Kick Sauber': 'Audi',
    'Sauber': 'Audi',
    'BWT Alpine F1 Team': 'Alpine',
    'Alpine F1 Team': 'Alpine',
    'Mercedes-AMG PETRONAS F1 Team': 'Mercedes',
    'Scuderia Ferrari': 'Ferrari',
    'Scuderia Ferrari HP': 'Ferrari',
    'McLaren Formula 1 Team': 'McLaren',
}


def canonical_team_name(team_name):
    raw = str(team_name or '').strip()
    if raw in TEAM_DIRECTORY_2026:
        return raw
    return TEAM_NAME_ALIASES.get(raw, raw)


def team_colour(team_name):
    return TEAM_DIRECTORY_2026.get(canonical_team_name(team_name), {}).get('color', '#94a3b8')


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
        body{{margin:0;background:#0b1019;color:#eef5ff;font-family:Inter,Segoe UI,Arial,sans-serif}}
        /* Dikey kaydırma ana Streamlit sayfasında kalır: 13. pilotta ikinci bir
           küçük kaydırma alanı oluşmaz. Yalnızca çok geniş yarış sütunları yatay kayar. */
        .matrix-wrap{{overflow-x:auto;overflow-y:visible;max-height:none;border:1px solid #2a3950;border-radius:12px;background:#101826}}
        table{{border-collapse:separate;border-spacing:0;min-width:1180px;width:100%;font-size:14px}}
        th{{position:sticky;top:0;background:#182232;color:#89a1bd;padding:13px 10px;text-align:center;font-size:11px;letter-spacing:.08em;border-bottom:1px solid #2a3950;z-index:2}}
        td{{padding:12px 10px;text-align:center;border-bottom:1px solid #243044;color:#ecf4ff;font-weight:700}}
        tr:last-child td{{border-bottom:0}} tr:hover td{{background:#162238}}
        .rank{{position:sticky;left:0;z-index:1;background:#101826;width:42px;color:#9db3cb}}
        .driver{{position:sticky;left:42px;z-index:1;background:#101826;text-align:left;min-width:165px}}
        .driver span{{display:block;font-weight:900}} .driver small{{display:block;margin-top:4px;font-size:11px;font-weight:800}}
        .flag{{width:22px;height:15px;object-fit:cover;border-radius:2px;vertical-align:middle;box-shadow:0 1px 4px rgba(0,0,0,.35)}} .driver-flag{{width:18px;height:12px;margin-right:4px}}
        .points{{position:sticky;left:207px;z-index:1;background:#101826;min-width:54px;color:#ffffff}}
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
        body{{margin:0;background:#090d14;color:#edf5ff;font-family:Inter,Segoe UI,Arial,sans-serif}}
        .podium-wrap{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;align-items:end;padding:10px 0 24px}}
        .podium,.team-card{{position:relative;background:#101928;border:1px solid #2b3b52;border-top:4px solid var(--team);border-radius:13px;padding:14px 16px;box-sizing:border-box;overflow:hidden}}
        .podium{{min-height:180px;text-align:center}} .p1{{min-height:220px;order:0;box-shadow:0 12px 30px rgba(0,0,0,.27)}} .p2{{order:-1}} .p3{{order:1}}
        .podium img{{height:66px;max-width:130px;object-fit:contain;margin:12px auto 10px;display:block}} .team-card img{{height:42px;max-width:95px;object-fit:contain;margin-bottom:7px;display:block}}
        .place{{position:absolute;right:12px;top:11px;color:var(--team);font-weight:900;font-size:13px}}
        .team-name{{color:var(--team);font-size:17px;font-weight:900}} .team-points{{margin-top:7px;font-size:24px;font-weight:900}} .team-points small{{font-size:10px;color:#8fa4bc;letter-spacing:.09em}}
        .team-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}} .team-card{{min-height:115px}}
        @media(max-width:760px){{.podium-wrap,.team-grid{{grid-template-columns:1fr}} .p1,.p2,.p3{{order:initial;min-height:150px}}}}
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
      body{{margin:0;background:#090d14;color:#eef6ff;font-family:Inter,Segoe UI,Arial,sans-serif}}
      .weekend-hud{{border:1px solid #2a405a;border-radius:14px;padding:15px;background:linear-gradient(125deg,#111c2c,#0c1420);overflow:hidden}}
      .weekend-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}}
      .eyebrow{{color:#8ba2bc;font-weight:900;font-size:10px;letter-spacing:.12em}}.race-name{{font-size:22px;font-weight:950;margin-top:5px}}.next{{border:1px solid #36506e;background:#122137;border-radius:8px;padding:8px 10px;color:#b8c9db;font-size:11px;font-weight:800}}
      .weekend-sessions{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:14px}}.weekend-session{{min-height:70px;padding:10px;border:1px solid #294057;border-top:3px solid var(--accent);border-radius:9px;background:#0d1724}}.weekend-session small,.weekend-session span{{display:block;color:#8da4bc;font-weight:900;font-size:10px;letter-spacing:.07em}}.weekend-session b{{display:block;color:#f3f8ff;margin:7px 0;font-size:12px}}.weekend-session span{{color:var(--accent)}}
      @media(max-width:760px){{.race-name{{font-size:18px}}.weekend-sessions{{grid-template-columns:repeat(2,1fr)}}.weekend-session:last-child{{grid-column:span 2}}}}
    </style>
    <div class='weekend-hud'><div class='weekend-head'><div><div class='eyebrow'>RACE WEEKEND // İSTANBUL SAATİ</div><div class='race-name'>{html_lib.escape(str(event.get('EventName', 'Formula 1')))}</div></div><div class='next'>{html_lib.escape(next_text)}</div></div><div class='weekend-sessions'>{''.join(cards)}</div></div>
    """


def championship_snapshot_hud(driver_standings, constructor_standings, rounds):
    """Puan Merkezi açıldığında önce görünen hızlı sezon özeti."""
    if driver_standings.empty or constructor_standings.empty:
        return ''
    driver = driver_standings.iloc[0]
    team = constructor_standings.iloc[0]
    driver_team = str(driver.get('Takım', ''))
    driver_colour = team_colour(driver_team)
    team_colour_value = team_colour(str(team.get('Takım', '')))
    return f"""
    <style>
      body{{margin:0;background:#090d14;color:#eef6ff;font-family:Inter,Segoe UI,Arial,sans-serif}}.season-snapshot{{display:grid;grid-template-columns:1.1fr 1fr .8fr;gap:10px}}.season-card{{border:1px solid #2b405a;border-top:4px solid var(--accent);border-radius:11px;padding:13px;background:#101a2a;min-height:108px}}.season-card small{{display:block;color:#8ea5bc;font-size:10px;font-weight:900;letter-spacing:.1em}}.season-card b{{display:block;font-size:20px;color:var(--accent);margin-top:8px}}.season-card span{{display:block;color:#c2d0df;font-size:12px;margin-top:5px;font-weight:750}}@media(max-width:760px){{.season-snapshot{{grid-template-columns:1fr}}}}
    </style>
    <div class='season-snapshot'><div class='season-card' style='--accent:{driver_colour}'><small>PİLOT LİDERİ</small><b>{html_lib.escape(str(driver.get('Pilot', '—')))} · {html_lib.escape(str(driver.get('Puan', '—')))} P</b><span>{html_lib.escape(driver_team)} · Şampiyona sırası #1</span></div><div class='season-card' style='--accent:{team_colour_value}'><small>TAKIM LİDERİ</small><b>{html_lib.escape(str(team.get('Takım', '—')))} · {html_lib.escape(str(team.get('Puan', '—')))} P</b><span>Takımlar klasmanında lider</span></div><div class='season-card' style='--accent:#f7c948'><small>TAMAMLANAN YARIŞ</small><b>{len(rounds)}</b><span>Sprint sonuçları puana dahil</span></div></div>
    """


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
        body{{margin:0;background:#090d14;color:#eff6ff;font-family:Inter,Segoe UI,Arial,sans-serif}}
        .wrap{{border:1px solid #2c3c53;border-radius:13px;background:#101827;overflow:hidden}}
        .head{{padding:13px 16px;background:#151f2f;border-bottom:1px solid #2c3c53;font-weight:900;letter-spacing:.04em}}
        .sub{{font-size:11px;color:#8ea4bc;margin-top:4px;font-weight:700}}
        .tops{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:12px}}
        .top{{background:#111b2a;border:1px solid #2c3c53;border-top:4px solid var(--team);border-radius:10px;padding:12px;position:relative;min-height:64px}}
        .top .rank{{color:var(--team);font-size:20px;font-weight:900;position:absolute;right:12px;top:8px}}
        .pilot{{font-weight:900;color:#f5f9ff}} .pilot small{{display:block;color:var(--team);font-size:11px;margin-top:4px;font-weight:800}}
        .lap{{margin-top:7px;color:#d7e4f4;font-family:ui-monospace,Consolas,monospace;font-weight:800}}
        .tyre{{display:inline-flex;align-items:center;justify-content:center;width:19px;height:19px;border:2px solid var(--tyre);border-radius:50%;font-size:10px;color:var(--tyre);font-weight:900;margin-left:7px}}
        .leader-list{{border-top:1px solid #243145}}
        .leader-row{{display:grid;grid-template-columns:45px 1fr 135px 30px;align-items:center;min-height:53px;padding:0 14px;border-top:1px solid #243145;border-left:4px solid var(--team)}}
        .leader-row .rank{{color:#9fb4cb;font-weight:900}} .leader-row .lap{{margin:0;text-align:right}}
        @media(max-width:760px){{.tops{{grid-template-columns:1fr}} .leader-row{{grid-template-columns:36px 1fr 95px 25px;padding:0 8px}}}}
    </style>
    <div class='wrap'><div class='head'>{html_lib.escape(title)}<div class='sub'>TAKIM RENKLERİ • TUR ZAMANI • LASTİK HAMURU</div></div><div class='tops'>{podium}</div><div class='leader-list'>{rest}</div></div>
    """


def leaderboard_component_height(table):
    """Her sonucu sayfanın normal kaydırmasına bırakır; 13. pilotta kesilmez."""
    row_count = len(table) if table is not None else 0
    return min(1540, max(360, 210 + max(0, row_count - 3) * 54))


def _duel_samples(telemetry, sample_count=360):
    """Telemetriyi 2D düello animasyonu için iki senkron biçime dönüştürür."""
    columns = ['X', 'Y', 'Distance', 'Speed', 'Time']
    source = telemetry[[column for column in columns if column in telemetry.columns]].dropna(subset=['X', 'Y']).copy()
    if source.empty:
        return {'distance': [], 'realtime': [], 'lap_seconds': 0}

    source['Distance'] = pd.to_numeric(source.get('Distance', pd.Series(np.arange(len(source)))), errors='coerce')
    source['Speed'] = pd.to_numeric(source.get('Speed', pd.Series(np.zeros(len(source)))), errors='coerce').fillna(0)
    source = source.dropna(subset=['Distance']).sort_values('Distance').drop_duplicates('Distance')
    if len(source) < 2:
        return {'distance': [], 'realtime': [], 'lap_seconds': 0}

    distance_grid = np.linspace(float(source['Distance'].min()), float(source['Distance'].max()), sample_count)
    distance_points = [
        {
            'x': round(float(np.interp(value, source['Distance'], source['X'])), 2),
            'y': round(float(np.interp(value, source['Distance'], source['Y'])), 2),
            'speed': round(float(np.interp(value, source['Distance'], source['Speed'])), 1)
        }
        for value in distance_grid
    ]

    try:
        elapsed = (pd.to_timedelta(source['Time']) - pd.to_timedelta(source['Time']).iloc[0]).dt.total_seconds()
        duration = float(elapsed.iloc[-1])
        if duration <= 0:
            raise ValueError('Sıfır tur süresi')
        time_source = source.assign(_elapsed=elapsed).sort_values('_elapsed').drop_duplicates('_elapsed')
        time_grid = np.linspace(0, duration, sample_count)
        realtime_points = [
            {
                'x': round(float(np.interp(value, time_source['_elapsed'], time_source['X'])), 2),
                'y': round(float(np.interp(value, time_source['_elapsed'], time_source['Y'])), 2),
                'speed': round(float(np.interp(value, time_source['_elapsed'], time_source['Speed'])), 1)
            }
            for value in time_grid
        ]
    except Exception:
        duration = 0.0
        realtime_points = distance_points

    return {'distance': distance_points, 'realtime': realtime_points, 'lap_seconds': round(duration, 3)}


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


def two_driver_duel_html(telemetry_1, telemetry_2, driver_1, driver_2, colour_1, colour_2, lap_time_1, lap_time_2):
    """İki en hızlı turun 2D HUD animasyonunu üretir; sunucuya tekrar veri yüklemez."""
    payload = {
        'drivers': [
            {'code': str(driver_1), 'colour': colour_1, 'lap': str(lap_time_1), 'samples': _duel_samples(telemetry_1)},
            {'code': str(driver_2), 'colour': colour_2, 'lap': str(lap_time_2), 'samples': _duel_samples(telemetry_2)}
        ]
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    return f"""
    <style>
      *{{box-sizing:border-box}} body{{margin:0;background:#090d14;color:#edf5ff;font-family:Inter,Segoe UI,Arial,sans-serif}}
      .hud{{border:1px solid #2c3d55;border-radius:14px;background:linear-gradient(135deg,#101827,#0b111c);padding:14px}}
      .bar{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}}
      .title{{font-size:13px;font-weight:900;letter-spacing:.1em}} .sub{{font-size:11px;color:#91a4ba;margin-top:4px}}
      .driver{{display:flex;align-items:center;gap:7px;padding:6px 9px;border:1px solid #2e4058;border-radius:7px;background:#101725;font-size:12px;font-weight:900}}
      .dot{{width:9px;height:9px;border-radius:50%;background:var(--team);box-shadow:0 0 10px var(--team)}}
      canvas{{display:block;width:100%;height:455px;border:1px solid #26374f;border-radius:10px;background:radial-gradient(circle at 50% 40%,#141f31,#080c13 70%)}}
      .controls{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:11px}} button{{color:#eff6ff;background:#172235;border:1px solid #344760;border-radius:7px;padding:7px 10px;font-weight:800;cursor:pointer}} button.active{{border-color:#fb4b56;color:#fff;background:#32171c}} .readout{{font-family:ui-monospace,Consolas,monospace;color:#a9bbcf;font-size:12px;margin-left:auto}}
      @media(max-width:650px){{canvas{{height:350px}}.readout{{width:100%;margin-left:0}}}}
    </style>
    <div class='hud'>
      <div class='bar'><div><div class='title'>2D TUR DÜELLOSU</div><div class='sub'>MESAFE SENKRONU: AYNI VİRAJDA KİM HIZLI? • GERÇEK ZAMAN: KİM ÖNDE?</div></div><div id='labels'></div></div>
      <canvas id='duel-canvas'></canvas>
      <div class='controls'><button id='play'>▶ Oynat</button><button id='mode' class='active'>⇄ Mesafeye göre</button><button id='speed'>1×</button><span class='readout' id='readout'>Veri hazırlanıyor</span></div>
    </div>
    <script>
      const duel = {payload_json}; const drivers = duel.drivers; const canvas = document.getElementById('duel-canvas'); const ctx = canvas.getContext('2d');
      let playing = false, mode = 'distance', rate = 1, progress = 0, last = null;
      document.getElementById('labels').innerHTML = drivers.map(d => `<span class='driver'><i class='dot' style='--team:${{d.colour}}'></i>${{d.code}} · ${{d.lap}}</span>`).join('');
      function resized(){{ const box=canvas.getBoundingClientRect(); const ratio=window.devicePixelRatio||1; canvas.width=box.width*ratio; canvas.height=box.height*ratio; ctx.setTransform(ratio,0,0,ratio,0,0); draw(); }}
      function series(d){{return mode==='distance' ? d.samples.distance : d.samples.realtime;}}
      function allPoints(){{return drivers.flatMap(d=>series(d));}}
      function transform(){{ const pts=allPoints(); if(!pts.length)return {{x:0,y:0,s:1,w:canvas.clientWidth,h:canvas.clientHeight}}; const xs=pts.map(p=>p.x), ys=pts.map(p=>p.y), minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys); const w=canvas.clientWidth,h=canvas.clientHeight,pad=34; const scale=Math.min((w-pad*2)/(maxX-minX||1),(h-pad*2)/(maxY-minY||1)); return {{x:minX,y:minY,s:scale,w,h}};}}
      function xy(p,t){{return [((p.x-t.x)*t.s)+(t.w-(Math.max(...allPoints().map(q=>q.x))-t.x)*t.s)/2, ((Math.max(...allPoints().map(q=>q.y))-p.y)*t.s)+(t.h-(Math.max(...allPoints().map(q=>q.y))-t.y)*t.s)/2];}}
      function interpolate(points,p){{ if(!points.length)return null; const index=Math.min(points.length-1,Math.max(0,p*(points.length-1))); const a=points[Math.floor(index)],b=points[Math.min(points.length-1,Math.ceil(index))],f=index-Math.floor(index); return {{x:a.x+(b.x-a.x)*f,y:a.y+(b.y-a.y)*f,speed:a.speed+(b.speed-a.speed)*f}};}}
      function drawPath(points,t,colour){{if(points.length<2)return;ctx.beginPath();points.forEach((p,i)=>{{const [x,y]=xy(p,t);i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.strokeStyle=colour;ctx.globalAlpha=.22;ctx.lineWidth=2;ctx.stroke();ctx.globalAlpha=1;}}
      function draw(){{const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h); const t=transform(); drivers.forEach(d=>drawPath(series(d),t,'#98aabd')); drivers.forEach((d,i)=>{{const points=series(d);let p=progress;if(mode==='realtime'){{const max=Math.max(...drivers.map(x=>x.samples.lap_seconds||1));p=Math.min(1,progress*max/(d.samples.lap_seconds||max));}}const pos=interpolate(points,p);if(!pos)return;const [x,y]=xy(pos,t);ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.fillStyle=d.colour;ctx.shadowColor=d.colour;ctx.shadowBlur=18;ctx.fill();ctx.shadowBlur=0;ctx.fillStyle='#07101a';ctx.font='bold 11px Arial';ctx.textAlign='center';ctx.fillText(d.code,x,y+4);}}); const shown=(progress*100).toFixed(1); document.getElementById('readout').textContent=`${{mode==='distance'?'Pist mesafesi':'Tur zamanı'}}: %${{shown}}  •  ${{rate}}×`;}}
      function frame(now){{if(last===null)last=now;const delta=(now-last)/1000;last=now;if(playing){{progress+=delta*rate/Math.max(...drivers.map(d=>d.samples.lap_seconds||90),90);if(progress>=1){{progress=1;playing=false;document.getElementById('play').textContent='↺ Baştan';}}}}draw();requestAnimationFrame(frame);}}
      document.getElementById('play').onclick=()=>{{if(progress>=1)progress=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat';}};
      document.getElementById('mode').onclick=()=>{{mode=mode==='distance'?'realtime':'distance';document.getElementById('mode').textContent=mode==='distance'?'⇄ Mesafeye göre':'◷ Gerçek zaman';document.getElementById('mode').classList.toggle('active',mode==='distance');progress=0;draw();}};
      document.getElementById('speed').onclick=()=>{{rate=rate===1?2:rate===2?4:1;document.getElementById('speed').textContent=rate+'×';}}; window.addEventListener('resize',resized); resized();requestAnimationFrame(frame);
    </script>
    """


def two_driver_duel_html_v9(telemetry_1, telemetry_2, driver_1, driver_2, team_1, team_2, colour_1, colour_2, lap_time_1, lap_time_2, lap_seconds_1, lap_seconds_2, track_overlay=None, sector_times_1=None, sector_times_2=None):
    """Tur farkını gerçek zamanla gösteren; küçük F1 araçlı, taşmayan 2D HUD."""
    first = _duel_samples_v18(telemetry_1)
    second = _duel_samples_v18(telemetry_2)
    first['lap_seconds'] = float(lap_seconds_1)
    second['lap_seconds'] = float(lap_seconds_2)
    payload = json.dumps({
        'drivers': [
            {'code': str(driver_1), 'team': str(team_1), 'colour': colour_1, 'lap': str(lap_time_1), 'samples': first, 'sectors': sector_times_1 or []},
            {'code': str(driver_2), 'team': str(team_2), 'colour': colour_2, 'lap': str(lap_time_2), 'samples': second, 'sectors': sector_times_2 or []}
        ],
        'overlay': track_overlay or {}
    }, ensure_ascii=False)
    return """
    <style>
      *{box-sizing:border-box} body{margin:0;background:#090d14;color:#eff6ff;font-family:Inter,Segoe UI,Arial,sans-serif}
      .hud{border:1px solid #2d405a;border-radius:13px;background:linear-gradient(135deg,#101827,#0a1019);padding:12px}
      .top{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:9px}.title{font-size:12px;font-weight:950;letter-spacing:.1em}.sub{font-size:10px;color:#90a4ba;margin-top:4px}
      .drivers{display:flex;gap:7px;flex-wrap:wrap}.driver{border:1px solid #31445e;background:#111a28;border-radius:7px;padding:5px 8px;font-size:11px;font-weight:900;color:var(--team)}.driver small{color:#a8b8ca;margin-left:4px;font-weight:700}
      .map{border:1px solid #263950;border-radius:10px;background:radial-gradient(circle at 50% 45%,#142035,#080c13 72%);overflow:hidden}.map canvas{display:block;width:100%;height:398px}
      .bottom{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn{border:1px solid #344a66;background:#162235;border-radius:7px;color:#eff6ff;padding:7px 10px;font-weight:850;font-size:11px;cursor:pointer}.btn.active{border-color:#f04c55;background:#35191e}.gap{font-family:ui-monospace,Consolas,monospace;color:#f5f8fc;font-weight:900;font-size:12px;margin-left:auto}.timeline{width:100%;accent-color:#e10600;margin-top:7px}@media(max-width:650px){.map canvas{height:335px}.gap{width:100%;margin-left:0}}
    </style>
    <div class='hud'>
      <div class='top'><div><div class='title'>2D TUR DÜELLOSU</div><div class='sub'>GERÇEK ZAMAN: FARKI GÖR • MESAFE: AYNI VİRAJDAKİ HIZI KARŞILAŞTIR</div></div><div class='drivers' id='drivers'></div></div>
      <div class='map'><canvas id='duel'></canvas></div>
      <input id='timeline' class='timeline' type='range' min='0' max='1000' value='0' aria-label='Tur zaman çizgisi'>
      <div class='bottom'><button class='btn' id='play'>▶ Oynat</button><button class='btn active' id='mode'>◷ Gerçek zaman</button><button class='btn' id='speed'>1×</button><span class='gap' id='gap'>Fark hesaplanıyor…</span></div>
    </div>
    <script>
      const duel=__DUEL_PAYLOAD__, drivers=duel.drivers, canvas=document.getElementById('duel'), ctx=canvas.getContext('2d'),overlay=duel.overlay||{};
      let playing=false, mode='realtime', rate=1, progress=0, last=null,overlayMode='combined';
      const duelStyle=document.createElement('style');duelStyle.textContent='.sector-hud{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:9px}.sector-card{border:1px solid #344b67;border-top:3px solid var(--sector);border-radius:8px;background:#101a29;padding:8px 10px}.sector-title{font-size:10px;font-weight:950;letter-spacing:.08em;color:#9eb4ca}.sector-line{display:flex;justify-content:space-between;gap:7px;margin-top:5px;font:800 11px ui-monospace,Consolas,monospace}.sector-delta{margin-top:5px;font:900 11px ui-monospace,Consolas,monospace;color:#9eb4ca}.winner{color:#79e7a7}.loser{color:#ff8793}@media(max-width:650px){.sector-hud{grid-template-columns:1fr}}';document.head.appendChild(duelStyle);
      const maxLap=Math.max(...drivers.map(d=>d.samples.lap_seconds||1));
      const fastest=drivers.reduce((a,b)=>a.samples.lap_seconds<=b.samples.lap_seconds?a:b); const delta=Math.abs(drivers[0].samples.lap_seconds-drivers[1].samples.lap_seconds);
      document.getElementById('drivers').innerHTML=drivers.map(d=>`<span class='driver' style='--team:${d.colour}'>🏎 ${d.code}<small>${d.team} · ${d.lap}</small></span>`).join('');
      canvas.insertAdjacentHTML('afterend','<div class="sector-hud" id="sector-hud"></div>');
      function timeSeconds(value){const parts=String(value||'').split(':');if(parts.length!==2)return null;const minutes=Number(parts[0]),seconds=Number(parts[1]);return Number.isFinite(minutes)&&Number.isFinite(seconds)?minutes*60+seconds:null}
      function renderSectors(){const host=document.getElementById('sector-hud');host.innerHTML=[0,1,2].map(index=>{const a=drivers[0].sectors?.[index]||'—',b=drivers[1].sectors?.[index]||'—',ta=timeSeconds(a),tb=timeSeconds(b),diff=ta!==null&&tb!==null?ta-tb:null,ahead=diff===null?'':diff<0?drivers[0].code:diff>0?drivers[1].code:'EŞİT',delta=diff===null?'Veri yok':`${diff<=0?'':'+'}${diff.toFixed(3)} sn`;return `<div class="sector-card" style="--sector:${index===0?'#f4d35e':index===1?'#56cfe1':'#ff7a9f'}"><div class="sector-title">SEKTÖR ${index+1} · ${ahead||'—'}</div><div class="sector-line"><span>${drivers[0].code}</span><b class="${diff!==null&&diff<=0?'winner':'loser'}">${a}</b></div><div class="sector-line"><span>${drivers[1].code}</span><b class="${diff!==null&&diff>=0?'winner':'loser'}">${b}</b></div><div class="sector-delta">Δ ${delta}</div></div>`}).join('')}
      renderSectors();
      function samples(d){return mode==='distance'?d.samples.distance:d.samples.realtime}
      function all(){return drivers.flatMap(d=>samples(d))}
      function project(p,rotate){return rotate?{x:-p.y,y:p.x}:p}
      function transform(){const raw=all();if(!raw.length)return null;const xs=raw.map(p=>p.x),ys=raw.map(p=>p.y);const wide=(canvas.clientWidth/canvas.clientHeight)>1.35;const tall=(Math.max(...ys)-Math.min(...ys))>(Math.max(...xs)-Math.min(...xs));const rotate=wide&&tall;const ps=raw.map(p=>project(p,rotate)),px=ps.map(p=>p.x),py=ps.map(p=>p.y),minX=Math.min(...px),maxX=Math.max(...px),minY=Math.min(...py),maxY=Math.max(...py),pad=28,w=canvas.clientWidth,h=canvas.clientHeight,s=Math.min((w-pad*2)/(maxX-minX||1),(h-pad*2)/(maxY-minY||1));return{rotate,minX,maxX,minY,maxY,w,h,s}}
      function xy(p,t){const q=project(p,t.rotate);return [((q.x-t.minX)*t.s)+(t.w-(t.maxX-t.minX)*t.s)/2,((t.maxY-q.y)*t.s)+(t.h-(t.maxY-t.minY)*t.s)/2]}
      function at(points,p){if(!points.length)return null;const n=Math.min(points.length-1,Math.max(0,p*(points.length-1))),a=points[Math.floor(n)],b=points[Math.min(points.length-1,Math.ceil(n))],f=n-Math.floor(n);return{x:a.x+(b.x-a.x)*f,y:a.y+(b.y-a.y)*f,speed:a.speed+(b.speed-a.speed)*f}}
      function advance(d){return mode==='distance'?progress:Math.min(1,progress*maxLap/(d.samples.lap_seconds||maxLap))}
      function car(x,y,angle,colour,code,finished){ctx.save();ctx.translate(x,y);ctx.rotate(angle);ctx.globalAlpha=finished?.52:1;ctx.fillStyle='#050a10';ctx.fillRect(-8,-8,4,16);ctx.fillRect(6,-9,4,18);ctx.fillStyle=colour;ctx.fillRect(-5,-4,14,8);ctx.fillRect(7,-2,7,4);ctx.fillRect(10,-8,3,16);ctx.fillStyle='#e8f4ff';ctx.fillRect(0,-1,5,2);ctx.restore();ctx.globalAlpha=1;ctx.fillStyle=colour;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-15)}
      function drawLine(points,t){if(points.length<2)return;ctx.beginPath();points.forEach((p,i)=>{const [x,y]=xy(p,t);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.strokeStyle='#708196';ctx.globalAlpha=.42;ctx.lineWidth=2;ctx.stroke();ctx.globalAlpha=1}
      function featureMarker(point,t,label,colour){if(!point)return;const [x,y]=xy(point,t);ctx.fillStyle=colour;ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#eff6ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,x+6,y-6)}
      function featureZone(points,start,end,t,colour,label){if(!points.length)return;const from=Math.max(0,Math.floor(start*(points.length-1))),to=Math.min(points.length-1,Math.ceil(end*(points.length-1)));ctx.beginPath();for(let i=from;i<=to;i++){const [x,y]=xy(points[i],t);i===from?ctx.moveTo(x,y):ctx.lineTo(x,y)}ctx.strokeStyle=colour;ctx.globalAlpha=.9;ctx.lineWidth=6;ctx.stroke();ctx.globalAlpha=1;featureMarker(points[from],t,label,colour)}
      function drawOverlay(t){const route=drivers[0]?.samples?.distance||[];if(!route.length)return;if(overlayMode==='attack'){(overlay.straights||[]).forEach((zone,index)=>featureZone(route,zone.start,zone.end,t,index?'#45c8ff':'#71e6a1',index?'Straight':'Overtake olasiligi'));return}featureMarker(route[0],t,'START / FINISH','#ffffff');(overlay.sectors||[]).forEach(item=>featureMarker(at(route,item.fraction),t,item.label,item.colour||'#f4d35e'));(overlay.pit||[]).forEach(item=>featureMarker(at(route,item.fraction),t,item.label,'#b79cff'));(overlay.corners||[]).forEach(item=>featureMarker(at(route,item.fraction),t,item.label,'#8ca2bb'));(overlay.brakes||[]).forEach(item=>featureMarker(at(route,item.fraction),t,'BRAKE','#ff6b6b'));if(overlay.speed_marker)featureMarker(at(route,overlay.speed_marker.fraction),t,'TOP '+overlay.speed_marker.speed,'#5ddcff')}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const t=transform();if(!t){ctx.fillStyle='#9badbf';ctx.font='bold 13px Arial';ctx.textAlign='center';ctx.fillText('Geçerli telemetri verisi yok',w/2,h/2);return}drawLine(samples(drivers[0]),t);drivers.forEach((d,i)=>{const pts=samples(d),p=advance(d),here=at(pts,p),next=at(pts,Math.min(1,p+.004));if(!here||!next)return;let [x,y]=xy(here,t),[nx,ny]=xy(next,t);let angle=Math.atan2(ny-y,nx-x);if(mode==='distance'&&i===1){x+=Math.cos(angle+Math.PI/2)*7;y+=Math.sin(angle+Math.PI/2)*7}car(x,y,angle,d.colour,d.code,p>=.999);});const percent=Math.round(progress*100);document.getElementById('timeline').value=Math.round(progress*1000);document.getElementById('gap').textContent=`Δ ${delta.toFixed(3)} sn • ${fastest.code} önde • %${percent}`}
      function drawOverlay(t){const route=drivers[0]?.samples?.distance||[];if(!route.length)return;(overlay.straights||[]).forEach((zone,index)=>featureZone(route,zone.start,zone.end,t,index?'#45c8ff':'#71e6a1',index?'Straight':'Overtake'));featureMarker(route[0],t,'START / FINISH','#ffffff');(overlay.sectors||[]).forEach(item=>featureMarker(at(route,item.fraction),t,item.label,item.colour||'#f4d35e'));(overlay.pit||[]).forEach(item=>featureMarker(at(route,item.fraction),t,item.label,'#b79cff'));(overlay.corners||[]).forEach(item=>featureMarker(at(route,item.fraction),t,item.label,'#8ca2bb'));(overlay.brakes||[]).forEach(item=>featureMarker(at(route,item.fraction),t,'BRAKE','#ff6b6b'));if(overlay.speed_marker)featureMarker(at(route,overlay.speed_marker.fraction),t,'TOP '+overlay.speed_marker.speed,'#5ddcff')}
      function currentDelta(){const reference=mode==='distance'?progress:Math.min(advance(drivers[0]),advance(drivers[1])),a=at(drivers[0].samples.distance,reference),b=at(drivers[1].samples.distance,reference);if(!a||!b||!Number.isFinite(a.elapsed)||!Number.isFinite(b.elapsed))return null;const raw=a.elapsed-b.elapsed;return{value:Math.abs(raw),ahead:raw<0?drivers[0].code:raw>0?drivers[1].code:'EQUAL'}}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const t=transform();if(!t){ctx.fillStyle='#9badbf';ctx.font='bold 13px Arial';ctx.textAlign='center';ctx.fillText('Telemetri verisi yok',w/2,h/2);return}drawLine(samples(drivers[0]),t);drawOverlay(t);drivers.forEach((d,i)=>{const pts=samples(d),p=advance(d),here=at(pts,p),next=at(pts,Math.min(1,p+.004));if(!here||!next)return;let [x,y]=xy(here,t),[nx,ny]=xy(next,t);let angle=Math.atan2(ny-y,nx-x);if(mode==='distance'&&i===1){x+=Math.cos(angle+Math.PI/2)*7;y+=Math.sin(angle+Math.PI/2)*7}car(x,y,angle,d.colour,d.code,p>=.999)});const percent=Math.round(progress*100),live=currentDelta();document.getElementById('timeline').value=Math.round(progress*1000);document.getElementById('gap').textContent=live?`Anlık Delta ${live.value.toFixed(3)} sn · ${live.ahead} önde · %${percent}`:`Tur Farkı ${delta.toFixed(3)} sn · ${fastest.code} önde · %${percent}`}
      function resize(){const b=canvas.getBoundingClientRect(),r=devicePixelRatio||1;canvas.width=b.width*r;canvas.height=b.height*r;ctx.setTransform(r,0,0,r,0,0);draw()}
      function frame(now){if(last===null)last=now;const dt=(now-last)/1000;last=now;if(playing){progress+=dt*rate/maxLap;if(progress>=1){progress=1;playing=false;document.getElementById('play').textContent='↺ Baştan'}}draw();requestAnimationFrame(frame)}
      document.getElementById('play').onclick=()=>{if(progress>=1)progress=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};
      document.getElementById('mode').onclick=()=>{mode=mode==='realtime'?'distance':'realtime';const b=document.getElementById('mode');b.textContent=mode==='realtime'?'◷ Gerçek zaman':'⇄ Mesafeye göre';b.classList.toggle('active',mode==='realtime');progress=0;draw()};
      document.getElementById('speed').onclick=()=>{rate=rate===1?2:rate===2?4:1;document.getElementById('speed').textContent=rate+'×'};document.getElementById('timeline').oninput=e=>{progress=Number(e.target.value)/1000;playing=false;document.getElementById('play').textContent='▶ Oynat';draw()};window.addEventListener('resize',resize);resize();requestAnimationFrame(frame);
    </script>
    """.replace('__DUEL_PAYLOAD__', payload)


def two_driver_duel_html_stable(telemetry_1, telemetry_2, driver_1, driver_2, team_1, team_2, colour_1, colour_2, lap_time_1, lap_time_2, lap_seconds_1, lap_seconds_2, track_overlay=None, sector_times_1=None, sector_times_2=None):
    """İki turu ortak gerçek-zaman saatinde ve tek, önbellekli canvas dönüşümünde oynatır."""
    first, second = _duel_samples_v18(telemetry_1), _duel_samples_v18(telemetry_2)
    first['lap_seconds'], second['lap_seconds'] = float(lap_seconds_1), float(lap_seconds_2)
    packed = json.dumps({'drivers': [
        {'code': str(driver_1), 'team': str(team_1), 'colour': colour_1, 'lap': str(lap_time_1), 'samples': first, 'sectors': sector_times_1 or []},
        {'code': str(driver_2), 'team': str(team_2), 'colour': colour_2, 'lap': str(lap_time_2), 'samples': second, 'sectors': sector_times_2 or []},
    ], 'overlay': track_overlay or {}}, ensure_ascii=False, separators=(',', ':'))
    return r'''<style>*{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2d435e;border-radius:13px;padding:12px;background:#101a2a}.head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.title{font-size:13px;font-weight:950;letter-spacing:.09em}.sub{font-size:10px;color:#91a8c0;margin-top:5px}.tag{border:1px solid #35506d;border-radius:7px;padding:6px 8px;font-size:11px;font-weight:900;color:var(--team)}.map{margin-top:10px;border:1px solid #29405a;border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 45%,#17263d,#070c13 74%)}canvas{width:100%;height:400px;display:block}.sectors{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}.sector{border:1px solid #344b67;border-top:3px solid var(--c);border-radius:8px;padding:8px;background:#101a29;font:800 11px ui-monospace,Consolas,monospace}.sector small{display:block;color:#9eb4ca;font-family:Inter,Arial,sans-serif;margin-bottom:6px}.win{color:#79e7a7}.lose{color:#ff8793}.bottom{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn{border:1px solid #39516f;border-radius:7px;background:#142239;color:#edf6ff;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.slider{flex:1;min-width:130px;accent-color:#ff4051}.delta{font:900 12px ui-monospace,Consolas,monospace;margin-left:auto}@media(max-width:650px){canvas{height:330px}.sectors{grid-template-columns:1fr}.delta{width:100%;margin-left:0}}</style><div class="hud"><div class="head"><div><div class="title">2D LAP DUEL // STABLE TIME SYNC</div><div class="sub">AYNI PİST NOKTASINDAKİ GERÇEK ZAMAN DELTASI · SEKTÖR ZAMANLARI</div></div><div id="tags"></div></div><div class="map"><canvas id="duel"></canvas></div><div class="sectors" id="sectors"></div><div class="bottom"><button class="btn" id="play">▶ Oynat</button><button class="btn active" data-rate="1">1×</button><button class="btn" data-rate="2">2×</button><button class="btn" data-rate="4">4×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="delta" id="delta"></span></div></div><script>
const data=__PAYLOAD__,drivers=data.drivers||[],canvas=document.getElementById('duel'),ctx=canvas.getContext('2d');let playing=false,rate=1,p=0,last=performance.now(),lastHud=0,view=null;const maxLap=Math.max(...drivers.map(d=>d.samples.lap_seconds||1));function sec(v){const x=String(v||'').split(':');return x.length===2?+x[0]*60+ +x[1]:NaN}function at(a,f){if(!a?.length)return null;const n=Math.max(0,Math.min(a.length-1,f*(a.length-1))),i=Math.floor(n),r=n-i,x=a[i],y=a[Math.min(a.length-1,i+1)];return{x:x.x+(y.x-x.x)*r,y:x.y+(y.y-x.y)*r,speed:x.speed+(y.speed-x.speed)*r,elapsed:x.elapsed+(y.elapsed-x.elapsed)*r}}function transform(){const raw=drivers[0]?.samples.distance||[],xs=raw.map(x=>x.x),ys=raw.map(x=>x.y),w=canvas.clientWidth,h=canvas.clientHeight,pd=28,s=Math.min((w-pd*2)/(Math.max(...xs)-Math.min(...xs)||1),(h-pd*2)/(Math.max(...ys)-Math.min(...ys)||1));return{minX:Math.min(...xs),maxY:Math.max(...ys),w,h,s}}function xy(x,t){return[(x.x-t.minX)*t.s+(t.w-(Math.max(...(drivers[0]?.samples.distance||[]).map(z=>z.x))-t.minX)*t.s)/2,(t.maxY-x.y)*t.s+(t.h-(t.maxY-Math.min(...(drivers[0]?.samples.distance||[]).map(z=>z.y)))*t.s)/2]}function advance(d){return Math.min(1,p*maxLap/(d.samples.lap_seconds||maxLap))}function drawCar(q,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.globalAlpha=done?.5:1;ctx.fillStyle='#060a10';ctx.fillRect(-12,-7,5,14);ctx.fillStyle=c;ctx.fillRect(-8,-4,22,8);ctx.fillRect(12,-8,3,16);ctx.fillStyle='#f3f7ff';ctx.fillRect(-16,-9,3,18);ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-15)}function draw(){if(!view)return;ctx.clearRect(0,0,view.w,view.h);const route=drivers[0]?.samples.distance||[];ctx.strokeStyle='#8094ad';ctx.globalAlpha=.7;ctx.lineWidth=3;ctx.beginPath();route.forEach((x,i)=>{const q=xy(x,view);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;drivers.forEach(d=>{const a=advance(d),here=at(d.samples.realtime,a),next=at(d.samples.realtime,Math.min(1,a+.004));if(!here||!next)return;const q=xy(here,view),qn=xy(next,view);drawCar(q,d.code,d.colour,a>=.999)})}function update(){const now=performance.now();if(now-lastHud<180)return;lastHud=now;const same=Math.min(advance(drivers[0]),advance(drivers[1])),a=at(drivers[0]?.samples.distance,same),b=at(drivers[1]?.samples.distance,same),raw=(a&&b&&Number.isFinite(a.elapsed)&&Number.isFinite(b.elapsed))?a.elapsed-b.elapsed:null;document.getElementById('delta').textContent=raw===null?'Delta bekleniyor':`Anlık Δ ${Math.abs(raw).toFixed(3)} sn · ${raw<0?drivers[0].code:raw>0?drivers[1].code:'eşit'} önde`;document.getElementById('range').value=Math.round(p*1000)}function sectors(){document.getElementById('tags').innerHTML=drivers.map(d=>`<span class="tag" style="--team:${d.colour}">${d.code} · ${d.lap}</span>`).join(' ');document.getElementById('sectors').innerHTML=[0,1,2].map(i=>{const a=drivers[0].sectors?.[i]||'—',b=drivers[1].sectors?.[i]||'—',d=sec(a)-sec(b),ok=Number.isFinite(d);return`<div class="sector" style="--c:${i===0?'#f4d35e':i===1?'#56cfe1':'#ff7a9f'}"><small>SEKTÖR ${i+1} · ${ok?(d<0?drivers[0].code:d>0?drivers[1].code:'EŞİT'):'—'} önde</small><div class="${ok&&d<=0?'win':'lose'}">${drivers[0].code} ${a}</div><div class="${ok&&d>=0?'win':'lose'}">${drivers[1].code} ${b}</div><div>Δ ${ok?Math.abs(d).toFixed(3)+' sn':'—'}</div></div>`}).join('')}function frame(now){const dt=Math.min(.03,Math.max(0,(now-last)/1000));last=now;if(playing){p+=dt*rate/maxLap;if(p>=1){p=1;playing=false;document.getElementById('play').textContent='↻ Baştan'}}draw();update();requestAnimationFrame(frame)}function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);view=transform();draw()}document.getElementById('play').onclick=()=>{if(p>=1)p=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-rate]').forEach(b=>b.onclick=()=>{rate=+b.dataset.rate;document.querySelectorAll('[data-rate]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{p=+e.target.value/1000;playing=false;draw();update()};window.addEventListener('resize',resize);sectors();resize();requestAnimationFrame(frame);
</script></div>'''.replace('__PAYLOAD__', packed)


def two_driver_duel_html_repaired(*args, **kwargs):
    """2D d\u00fcello: tek ara\u00e7 modeli, \u00f6n kanat ve entegre pist katmanlar\u0131."""
    markup = two_driver_duel_html_stable(*args, **kwargs)
    old_car = "function drawCar(q,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.globalAlpha=done?.5:1;ctx.fillStyle='#060a10';ctx.fillRect(-12,-7,5,14);ctx.fillStyle=c;ctx.fillRect(-8,-4,22,8);ctx.fillRect(12,-8,3,16);ctx.fillStyle='#f3f7ff';ctx.fillRect(-16,-9,3,18);ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-15)}"
    new_car = "function drawCar(q,a,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.rotate(a);ctx.globalAlpha=done?.5:1;ctx.fillStyle='#05080d';[[-9,-10,6,5],[-9,5,6,5],[7,-10,6,5],[7,5,6,5]].forEach(w=>ctx.fillRect(...w));ctx.fillStyle=c;ctx.fillRect(-10,-5,23,10);ctx.fillRect(10,-3,8,6);ctx.fillStyle='#111a27';ctx.beginPath();ctx.ellipse(1,0,5,4,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f3f7ff';ctx.fillRect(17,-10,3,20);ctx.fillRect(14,-8,8,3);ctx.fillRect(14,5,8,3);ctx.fillStyle='#dce8f7';ctx.fillRect(5,-1,6,2);ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-15)}"
    markup = markup.replace(old_car, new_car).replace("drawCar(q,d.code,d.colour,a>=.999)", "drawCar(q,Math.atan2(qn[1]-q[1],qn[0]-q[0]),d.code,d.colour,a>=.999)")
    overlay = "function drawOverlay(){const o=data.overlay||{},route=drivers[0]?.samples?.distance||[];if(!route.length)return;const mark=(f,label,col)=>{const p=at(route,f);if(!p)return;const q=xy(p,view);ctx.fillStyle=col;ctx.beginPath();ctx.arc(q[0],q[1],3.5,0,Math.PI*2);ctx.fill();ctx.fillStyle='#eff6ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)};const zone=(z,label,col)=>{ctx.beginPath();for(let i=0;i<=24;i++){const p=at(route,z.start+(z.end-z.start)*i/24),q=xy(p,view);i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1])}ctx.strokeStyle=col;ctx.lineWidth=5;ctx.globalAlpha=.92;ctx.stroke();ctx.globalAlpha=1;mark(z.start,label,col)};mark(0,'START / FINISH','#ffffff');(o.sectors||[]).forEach(x=>mark(x.fraction,x.label,x.colour||'#f4d35e'));(o.pit||[]).forEach(x=>mark(x.fraction,x.label,'#b79cff'));(o.straights||[]).forEach((z,i)=>zone(z,i===0?'SM - Straight Mode':'OM - Overtake Mode',i===0?'#45c8ff':'#71e6a1'))}"
    markup = markup.replace("function draw(){if(!view)return;", overlay + "function draw(){if(!view)return;")
    markup = markup.replace("ctx.globalAlpha=1;drivers.forEach(d=>{const a=advance(d)", "ctx.globalAlpha=1;drawOverlay();drivers.forEach(d=>{const a=advance(d)")
    return markup
def hammer_time_soundboard_html():
    """Kullanıcının lisanslı sesini veya nötr tarayıcı sesini açan ses butonu."""
    audio_path = os.path.join(assets_dir, 'hammer_time.mp3')
    audio_source = ''
    if os.path.exists(audio_path):
        try:
            audio_source = 'data:audio/mpeg;base64,' + base64.b64encode(open(audio_path, 'rb').read()).decode('ascii')
        except Exception:
            audio_source = ''
    source_json = json.dumps(audio_source)
    return f"""
    <style>body{{margin:0;background:transparent;font-family:Inter,Segoe UI,Arial,sans-serif}}.sound{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border:1px solid #34445d;background:#111a29;border-radius:10px;color:#eef6ff}}.label{{font-size:12px;font-weight:900;letter-spacing:.07em}}.small{{font-size:11px;color:#91a4ba;margin-top:4px}}button{{background:#e10600;color:white;border:0;border-radius:7px;padding:9px 13px;font-weight:900;cursor:pointer;white-space:nowrap}}</style>
    <div class='sound'><div><div class='label'>🔊 PADDOCK SOUNDBOARD</div><div class='small'>Nötr tarayıcı sesi; lisanslı MP3 eklendiğinde onu çalar.</div></div><button id='hammer'>HAMMER TIME</button></div>
    <script>const src={source_json};document.getElementById('hammer').onclick=()=>{{if(src){{const a=new Audio(src);a.play();}}else if('speechSynthesis' in window){{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance("Okay Lewis, it's hammer time.");u.lang='en-GB';u.rate=.96;speechSynthesis.speak(u);}}}};</script>
    """


def live_track_html():
    """OpenF1'in tarayıcıdan canlı konum akışını kullanan deneysel 2D takip HUD'u."""
    return """
    <style>
      *{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf5ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2c3d55;border-radius:14px;background:linear-gradient(135deg,#101827,#0b111c);padding:14px}.bar{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-weight:900;letter-spacing:.1em;font-size:13px}.status{color:#7dd3fc;font-weight:800;font-size:12px}.map{position:relative;margin-top:12px;border:1px solid #26374f;border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 40%,#152237,#080c13 70%)}canvas{width:100%;height:470px;display:block}.legend{display:flex;gap:9px;flex-wrap:wrap;margin-top:10px}.driver{font-size:11px;font-weight:900;padding:5px 7px;border:1px solid #30415a;border-radius:6px;background:#101827}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--colour);margin-right:5px}.note{font-size:11px;color:#91a4ba;margin-top:9px}
    </style>
    <div class='hud'><div class='bar'><div><div class='title'>LIVE 2D TRACKER • BETA</div><div class='note'>Canlı seans sırasında OpenF1 konum akışıyla güncellenir. Video yayını değildir.</div></div><div class='status' id='status'>Bağlantı kuruluyor…</div></div><div class='map'><canvas id='live-map'></canvas></div><div class='legend' id='legend'></div></div>
    <script>
      const api='https://api.openf1.org/v1/', canvas=document.getElementById('live-map'),ctx=canvas.getContext('2d');let cars={{}},trails={{}},meta={{}},busy=false;
      const teamColours={{'mclaren':'#ff8000','ferrari':'#e8002d','mercedes':'#27f4d2','red bull racing':'#3671c6','red bull':'#3671c6','aston martin':'#229971','williams':'#64c4ff','alpine':'#ff87bc','haas f1 team':'#b6babd','racing bulls':'#6692ff','audi':'#52e252','cadillac':'#b8b8b8'}};
      function colour(team){{return teamColours[(team||'').toLowerCase()]||'#e5eef8'}} function resize(){{const b=canvas.getBoundingClientRect(),r=devicePixelRatio||1;canvas.width=b.width*r;canvas.height=b.height*r;ctx.setTransform(r,0,0,r,0,0);draw();}}
      function points(){{return Object.values(trails).flat().concat(Object.values(cars));}} function bounds(){{const p=points();if(!p.length)return null;const xs=p.map(x=>x.x),ys=p.map(x=>x.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),pad=34,w=canvas.clientWidth,h=canvas.clientHeight,s=Math.min((w-pad*2)/(maxX-minX||1),(h-pad*2)/(maxY-minY||1));return{{minX,maxX,minY,maxY,pad,w,h,s}}}} function pos(p,b){{return [((p.x-b.minX)*b.s)+(b.w-(b.maxX-b.minX)*b.s)/2,((b.maxY-p.y)*b.s)+(b.h-(b.maxY-b.minY)*b.s)/2]}}
      function draw(){{const b=bounds(),w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);if(!b){{ctx.fillStyle='#91a4ba';ctx.font='bold 14px Arial';ctx.textAlign='center';ctx.fillText('Canlı konum verisi bekleniyor',w/2,h/2);return}}Object.entries(trails).forEach(([n,trail])=>{{if(trail.length<2)return;ctx.beginPath();trail.forEach((p,i)=>{{const [x,y]=pos(p,b);i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.strokeStyle=colour(meta[n]?.team_name);ctx.globalAlpha=.2;ctx.lineWidth=1.5;ctx.stroke();ctx.globalAlpha=1}});Object.entries(cars).forEach(([n,p])=>{{const [x,y]=pos(p,b),c=colour(meta[n]?.team_name);ctx.beginPath();ctx.arc(x,y,10,0,Math.PI*2);ctx.fillStyle=c;ctx.shadowColor=c;ctx.shadowBlur=16;ctx.fill();ctx.shadowBlur=0;ctx.fillStyle='#06101a';ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(meta[n]?.name_acronym||n,x,y+3)}})}}
      async function update(){{if(busy)return;busy=true;try{{const [locations,drivers]=await Promise.all([fetch(api+'location?session_key=latest').then(r=>r.ok?r.json():Promise.reject()),fetch(api+'drivers?session_key=latest').then(r=>r.ok?r.json():[]) ]);drivers.forEach(d=>meta[d.driver_number]=d);const last={{}};locations.forEach(p=>last[p.driver_number]=p);Object.entries(last).forEach(([n,p])=>{{const point={{x:+p.x,y:+p.y}};cars[n]=point;trails[n]=trails[n]||[];const previous=trails[n][trails[n].length-1];if(!previous||Math.hypot(previous.x-point.x,previous.y-point.y)>1)trails[n].push(point);if(trails[n].length>380)trails[n].shift();}});document.getElementById('status').textContent=`● ${{Object.keys(cars).length}} araç • canlı güncelleme`;document.getElementById('legend').innerHTML=Object.keys(cars).sort((a,b)=>+a-+b).map(n=>`<span class='driver'><i class='dot' style='--colour:${{colour(meta[n]?.team_name)}}'></i>${{meta[n]?.name_acronym||'#'+n}}</span>`).join('');draw();}}catch(e){{document.getElementById('status').textContent='● Canlı veri henüz yok / bağlantı bekleniyor';draw();}}finally{{busy=false}}}}
      window.addEventListener('resize',resize);resize();update();setInterval(update,3000);
    </script>
    """.replace('{{', '{').replace('}}', '}')


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


def _openf1_get(endpoint, token):
    request = urllib.request.Request(
        f'https://api.openf1.org/v1/{endpoint}',
        headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}', 'User-Agent': 'PaddockDataCentre/1.0'}
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode('utf-8'))


def _latest_by_driver(records):
    latest = {}
    for record in records or []:
        number = record.get('driver_number')
        if number is None:
            continue
        current = latest.get(number)
        if current is None or str(record.get('date', '')) >= str(current.get('date', '')):
            latest[number] = record
    return latest


def _api_duration_text(value):
    try:
        return format_time(pd.to_timedelta(float(value), unit='s'))
    except (TypeError, ValueError):
        return '-'


@st.cache_data(ttl=2, show_spinner=False)
def get_openf1_live_snapshot(username, password):
    """Canlı konum, tur, lastik, pit ve delta bilgilerini tek güvenli sunucu çağrısında toplar."""
    token = _openf1_token(username, password)
    if not token:
        return {'ok': False, 'reason': 'OpenF1 canlı erişim bilgisi bulunamadı.', 'cars': [], 'session': {}}
    try:
        endpoints = ['sessions?session_key=latest', 'drivers?session_key=latest', 'location?session_key=latest', 'laps?session_key=latest', 'stints?session_key=latest', 'pit?session_key=latest', 'position?session_key=latest', 'intervals?session_key=latest']
        sessions, drivers, locations, laps, stints, pits, positions, intervals = [_openf1_get(endpoint, token) for endpoint in endpoints]
        session = sessions[0] if sessions else {}
        driver_map = {item.get('driver_number'): item for item in drivers or []}
        location_map = _latest_by_driver(locations)
        lap_map = _latest_by_driver(laps)
        stint_map = _latest_by_driver(stints)
        pit_map = _latest_by_driver(pits)
        position_map = _latest_by_driver(positions)
        interval_map = _latest_by_driver(intervals)
        cars = []
        for number, location in location_map.items():
            driver = driver_map.get(number, {})
            team = str(driver.get('team_name', 'Formula 1'))
            lap = lap_map.get(number, {})
            stint = stint_map.get(number, {})
            pit = pit_map.get(number, {})
            position = position_map.get(number, {})
            interval = interval_map.get(number, {})
            cars.append({
                'number': str(number), 'code': str(driver.get('name_acronym') or driver.get('last_name') or f'#{number}'),
                'team': team, 'colour': team_colour(team), 'x': float(location.get('x', 0)), 'y': float(location.get('y', 0)),
                'position': position.get('position') or '-', 'lap': lap.get('lap_number') or '-',
                'last_lap': _api_duration_text(lap.get('lap_duration')) if lap.get('lap_duration') else '-',
                'compound': str(stint.get('compound') or '-'), 'tyre_age': stint.get('tyre_age_at_start') or '-',
                'gap': str(interval.get('gap_to_leader') or interval.get('interval') or '-'),
                'last_pit_lap': pit.get('lap_number') or '-', 'pit_duration': pit.get('pit_duration') or None,
                'date': str(location.get('date', ''))
            })
        def position_sort_key(item):
            try:
                return int(item['position'])
            except (TypeError, ValueError):
                return 999
        cars.sort(key=lambda item: (position_sort_key(item), item['number']))
        return {'ok': bool(cars), 'reason': '' if cars else 'Aktif seans için konum paketi henüz gelmedi.', 'cars': cars, 'session': session}
    except Exception:
        return {'ok': False, 'reason': 'Canlı veri sağlayıcısına bağlanılamadı; seans başlamamış veya erişim yenileniyor olabilir.', 'cars': [], 'session': {}}


def live_race_hud_html(snapshot):
    """Sunucudan gelen canlı paketi, tıklanabilir F1 araçları ve pit/lastik HUD'u ile çizer."""
    payload = json.dumps(snapshot, ensure_ascii=False)
    return """
    <style>
      *{box-sizing:border-box}body{margin:0;background:#090d14;color:#eef6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2b3d54;border-radius:14px;background:linear-gradient(135deg,#101827,#0a111c);padding:13px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-size:13px;font-weight:950;letter-spacing:.1em}.sub{font-size:11px;color:#93a9c0;margin-top:5px}.signal{font-size:11px;color:#66e7a1;font-weight:900}.layout{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:11px;margin-top:12px}.map{border:1px solid #263950;border-radius:10px;background:radial-gradient(circle at 52% 42%,#152238,#080c13 72%);overflow:hidden}.map canvas{display:block;width:100%;height:475px}.panel{border:1px solid #2b3d54;border-radius:10px;background:#101827;padding:12px}.selected{font-size:18px;font-weight:950;color:var(--team)}.team{font-size:12px;color:#97a9bd;margin:3px 0 12px}.stat{display:flex;justify-content:space-between;border-top:1px solid #26364d;padding:8px 0;font-size:12px}.stat span{color:#93a7bf}.tyre{display:inline-flex;justify-content:center;align-items:center;border:2px solid var(--tyre);color:var(--tyre);height:22px;width:22px;border-radius:50%;font-weight:950}.strip{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.pilot{background:#111b2a;border:1px solid #30435c;border-left:4px solid var(--team);border-radius:7px;color:#eef6ff;padding:6px 8px;cursor:pointer;font-size:11px;font-weight:900}.pilot.active{background:#1a283b}.note{font-size:10px;color:#90a4ba;margin-top:8px}@media(max-width:850px){.layout{grid-template-columns:1fr}.map canvas{height:385px}}
    </style>
    <div class='hud'><div class='top'><div><div class='title'>LIVE RACE CONTROL • 2D TRACKER</div><div class='sub' id='session'>Canlı konum • lastik • pit • delta</div></div><div class='signal' id='signal'>● CANLI AKIŞ</div></div><div class='layout'><div><div class='map'><canvas id='track'></canvas></div><div class='strip' id='strip'></div><div class='note'>Araca veya altındaki pilot kartına bas: tur, lastik, pit ve delta detayları açılır.</div></div><div class='panel' id='panel'></div></div></div>
    <script>
      const data=__LIVE_PAYLOAD__,cars=data.cars||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d');let selected=cars[0]?.number||null;
      const tyres={SOFT:'#ff4048',MEDIUM:'#ffd43b',HARD:'#f0f4f8',INTERMEDIATE:'#39d46a',WET:'#38a8ff'};
      function transform(){if(!cars.length)return null;const xs=cars.map(c=>c.x),ys=cars.map(c=>c.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=32,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}
      function xy(c,t){return[((c.x-t.minX)*t.s)+(t.w-(t.maxX-t.minX)*t.s)/2,((t.maxY-c.y)*t.s)+(t.h-(t.maxY-t.minY)*t.s)/2]}
      function f1car(x,y,colour,label,chosen){ctx.save();ctx.translate(x,y);ctx.fillStyle='#04090e';ctx.fillRect(-8,-8,4,16);ctx.fillRect(7,-9,4,18);ctx.fillStyle=colour;ctx.fillRect(-5,-4,14,8);ctx.fillRect(7,-2,7,4);ctx.fillRect(10,-8,3,16);ctx.fillStyle='#f4f8ff';ctx.fillRect(1,-1,5,2);if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.strokeRect(-10,-11,25,22)}ctx.restore();ctx.fillStyle=colour;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(label,x,y-15)}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const t=transform();if(!t){ctx.fillStyle='#9bacc0';ctx.font='bold 14px Arial';ctx.textAlign='center';ctx.fillText(data.reason||'Canlı konum bekleniyor',w/2,h/2);return}ctx.strokeStyle='#71849b';ctx.globalAlpha=.25;ctx.setLineDash([4,8]);ctx.beginPath();cars.forEach((c,i)=>{const[x,y]=xy(c,t);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;cars.forEach(c=>{const[x,y]=xy(c,t);f1car(x,y,c.colour,c.code,c.number===selected)})}
      function panel(){const c=cars.find(x=>x.number===selected)||cars[0];if(!c){document.getElementById('panel').innerHTML='<div class="selected">Canlı veri bekleniyor</div>';return}const tyre=String(c.compound||'-').toUpperCase(),tc=tyres[tyre]||'#7c8ba1';document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').innerHTML=`<div class='selected'>${c.code} <span style='font-size:12px;color:#9caec2'>P${c.position}</span></div><div class='team'>${c.team}</div><div class='stat'><span>Son tur</span><b>${c.last_lap}</b></div><div class='stat'><span>Tur</span><b>${c.lap}</b></div><div class='stat'><span>Delta / fark</span><b>${c.gap}</b></div><div class='stat'><span>Lastik</span><b><i class='tyre' style='--tyre:${tc}'>${tyre.slice(0,1)}</i> ${tyre}</b></div><div class='stat'><span>Lastik yaşı</span><b>${c.tyre_age} tur</b></div><div class='stat'><span>Son pit</span><b>Tur ${c.last_pit_lap}${c.pit_duration?' • '+Number(c.pit_duration).toFixed(1)+' sn':''}</b></div>`}
      function strip(){document.getElementById('strip').innerHTML=cars.map(c=>`<button class='pilot ${c.number===selected?'active':''}' style='--team:${c.colour}' data-n='${c.number}'>P${c.position} · ${c.code} · ${c.last_lap}</button>`).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.n;draw();panel();strip()})}
      function resize(){const b=canvas.getBoundingClientRect(),r=devicePixelRatio||1;canvas.width=b.width*r;canvas.height=b.height*r;ctx.setTransform(r,0,0,r,0,0);draw()}canvas.onclick=e=>{const t=transform();if(!t)return;const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;let best=null,d=Infinity;cars.forEach(c=>{const[a,b]=xy(c,t),v=Math.hypot(a-x,b-y);if(v<d){best=c;d=v}});if(best&&d<30){selected=best.number;draw();panel();strip()}};document.getElementById('session').textContent=`${data.session?.meeting_name||'Formula 1'} • ${data.session?.session_name||'Aktif seans'} • 2 sn yenileme`;window.addEventListener('resize',resize);resize();panel();strip();
    </script>
    """.replace('__LIVE_PAYLOAD__', payload)


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


@st.cache_data(ttl=3, show_spinner=False)
def get_openf1_live_snapshot_v19(explicit_token='', username='', password=''):
    """Canlı konum + tur + lastik + pit + hava + Race Control paketini güvenli biçimde toplar.

    Bu fonksiyon token yokken anonim erişimi bir kez dener. Sağlayıcı gerçek-zaman
    erişimini vermiyorsa boş sonuç döner; kesinlikle uydurma canlı konum üretmez.
    """
    token = _openf1_token_v19(explicit_token, username, password)
    endpoint_names = [
        'sessions?session_key=latest',
        'drivers?session_key=latest',
        'location?session_key=latest',
        'laps?session_key=latest',
        'stints?session_key=latest',
        'pit?session_key=latest',
        'position?session_key=latest',
        'intervals?session_key=latest',
        'weather?session_key=latest',
        'race_control?session_key=latest',
    ]
    packets = [_openf1_get_optional_v19(endpoint, token) for endpoint in endpoint_names]
    (
        sessions, drivers, locations, laps, stints, pits,
        positions, intervals, weather, race_control,
    ) = packets

    session = sessions[0] if sessions else {}
    driver_map = {
        str(item.get('driver_number')): item
        for item in drivers or []
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
        lap = lap_map.get(number, {})
        stint = stint_map.get(number, {})
        pit = pit_map.get(number, {})
        position = position_map.get(number, {})
        interval = interval_map.get(number, {})
        code = str(driver.get('name_acronym') or driver.get('last_name') or f'#{number}').strip()
        team = str(driver.get('team_name') or 'Formula 1').strip()
        profile = race_driver_profile(code, team)
        pit_duration = pd.to_numeric(pit.get('pit_duration'), errors='coerce')
        cars.append({
            'number': number,
            'code': code,
            'team': team,
            'colour': team_colour(team),
            'x': x,
            'y': y,
            'position': position.get('position') or '—',
            'lap': lap.get('lap_number') or '—',
            'last_lap': _api_duration_text(lap.get('lap_duration')) if lap.get('lap_duration') else '—',
            'compound': str(stint.get('compound') or '—').upper(),
            'tyre_age_start': stint.get('tyre_age_at_start') if stint.get('tyre_age_at_start') is not None else '—',
            'gap': str(interval.get('gap_to_leader') or interval.get('interval') or '—'),
            'last_pit_lap': pit.get('lap_number') or '—',
            'pit_duration': None if pd.isna(pit_duration) else round(float(pit_duration), 2),
            'profile': profile,
            'date': str(location.get('date', '')),
        })

    def sort_key(item):
        try:
            return int(float(item['position']))
        except (TypeError, ValueError):
            return 999

    cars.sort(key=lambda item: (sort_key(item), item['number']))
    has_access = bool(token)
    if cars:
        reason = ''
    elif has_access:
        reason = 'Açık veri sağlayıcısı aktif seans için henüz konum paketi göndermedi.'
    else:
        reason = 'Canlı konum için açık sağlayıcıdan yetkili veri paketi gelmedi. Tamamlanan yarış tekrarı aşağıdaki resmi FastF1 verisiyle çalışmaya devam eder.'
    return {
        'ok': bool(cars),
        'reason': reason,
        'authenticated': has_access,
        'source': 'OpenF1 canlı paket' if has_access else 'OpenF1 anonim denemesi',
        'cars': cars,
        'track': _openf1_track_outline_v19(locations),
        'session': session,
        'weather': _openf1_weather_summary_v19(weather),
        'race_control': _openf1_race_control_v19(race_control),
    }


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
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
    return r"""
    <style>
      *{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf5ff;font-family:Inter,Segoe UI,Arial,sans-serif}
      .hud{border:1px solid #2c425c;border-radius:14px;background:linear-gradient(135deg,#101a2a,#09111b);padding:14px}
      .top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.title{font-size:13px;font-weight:950;letter-spacing:.11em}.sub{font-size:11px;color:#91a9c1;margin-top:5px}.signal{font-size:11px;color:#6ee7a4;font-weight:900;border:1px solid #2d5f4b;background:#102b23;border-radius:7px;padding:6px 8px}
      .layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px;margin-top:12px}.map{border:1px solid #28405a;border-radius:11px;background:radial-gradient(circle at 50% 42%,#17263b,#080d14 73%);overflow:hidden}.map canvas{width:100%;height:455px;display:block}.panel{border:1px solid #2d4057;border-radius:11px;background:#101a2a;padding:12px}
      .hero{position:relative;overflow:hidden;min-height:76px;border-bottom:1px solid #293c53;padding-bottom:10px}.portrait{position:absolute;right:-4px;bottom:0;max-height:94px;max-width:92px;object-fit:contain;opacity:.86}.selected{font-size:20px;font-weight:950;color:var(--team);position:relative;z-index:1}.team{font-size:12px;color:#9ab0c6;margin:4px 0 9px;position:relative;z-index:1}.stat{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #26394f;padding:8px 0;font-size:12px}.stat span{color:#91a7be}.tyre{display:inline-flex;width:22px;height:22px;align-items:center;justify-content:center;border-radius:50%;border:2px solid var(--tyre);color:var(--tyre);font-weight:950}
      .weather{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}.weather div{background:#0d1725;border:1px solid #26394f;border-radius:7px;padding:7px;font-size:10px;color:#96abc0}.weather b{display:block;color:#eef6ff;font-size:13px;margin-top:3px}.strip{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.pilot{border:1px solid #334b68;border-left:4px solid var(--team);border-radius:7px;background:#111d2e;color:#edf5ff;font-size:11px;font-weight:900;padding:6px 8px;cursor:pointer}.pilot.active{background:#21344c;box-shadow:0 0 0 1px var(--team) inset}.control{margin-top:10px;border-top:1px solid #26394f;padding-top:9px}.control h4{margin:0 0 6px;font-size:11px;letter-spacing:.08em}.msg{font-size:10px;color:#b7c7d7;border-left:3px solid #ffcc62;padding:5px 7px;margin:5px 0;background:#171a1b}.note{font-size:10px;color:#8299b3;margin-top:8px}
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
      body{{margin:0;background:#090d14;color:#edf5ff;font-family:Inter,Segoe UI,Arial,sans-serif}}.hud{{border:1px solid #2c425c;border-radius:13px;background:#101a2a;padding:13px}}.head{{font-size:13px;font-weight:950;letter-spacing:.09em}}.sub{{font-size:10px;color:#91a9c0;margin-top:5px}}.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:11px}}.tile{{border:1px solid #2a405a;border-radius:8px;background:#0d1724;padding:9px}}.tile small{{display:block;color:#8ca3bb;font-weight:800;font-size:10px}}.tile b{{display:block;color:#f4f8ff;margin-top:5px;font-size:13px}}.grid{{display:grid;grid-template-columns:1.1fr .9fr;gap:12px;margin-top:12px}}.box{{border:1px solid #293e56;border-radius:9px;background:#0d1623;padding:10px;min-height:135px}}.box h4{{margin:0 0 8px;font-size:11px;letter-spacing:.08em}}.msg{{border-left:3px solid #ffd168;padding:6px 7px;background:#19191a;margin:5px 0;font-size:11px;line-height:1.35}}.msg b{{color:#ffd168;margin-right:5px}}.pit{{display:grid;grid-template-columns:1fr 42px 72px 88px;gap:5px;border-top:1px solid #26394e;padding:7px 0;font-size:11px}}.pit b{{color:#edf6ff}}.pit span{{color:#a9bbcf}}.timeline{{display:flex;gap:7px;overflow:auto;padding-top:8px}}.timeline span{{white-space:nowrap;border:1px solid #2c4059;background:#0c1420;padding:6px 8px;border-radius:6px;font-size:10px;color:#aec1d4}}.muted{{font-size:11px;color:#8da2b8}}@media(max-width:760px){{.tiles{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}.pit{{grid-template-columns:1fr 38px 58px 76px}}}}
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
    if False and driver and row and any(token in text for token in ['kacinci', 'sira', 'siralama', 'nerede bitir', 'pozisyon']):
        return {'title': f"{driver} sonucu", 'answer': f"{driver}, son tamamlanan {latest['display_name']} seansını P{row.get('Sıra', '—')} ile bitirdi.", 'source': source}
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


def render_paddock_assistant_v19():
    """Sayfa içi, anahtarsız çalışan ve kaynak gösteren Paddock Asistan ekranı."""
    st.markdown("## 🧠 Paddock Asistanı")
    st.caption("Ücretsiz veri asistanı: yanıtlarını yalnızca uygulamanın doğrulanmış FastF1/OpenF1 paketlerinden verir; bilmediği veriyi uydurmaz.")
    prompt = st.text_input("F1 ile ilgili sorunu yaz", placeholder="Örn. Alonso kaçıncı oldu? Pole kim? Son seansta ne oldu?", key='paddock_assistant_prompt_v19')
    quick_a, quick_b, quick_c = st.columns(3)
    chosen = ''
    with quick_a:
        if st.button("Pole kim?", use_container_width=True, key='assistant_pole_v19'):
            chosen = 'Pole kim?'
    with quick_b:
        if st.button("Son seansta ne oldu?", use_container_width=True, key='assistant_story_v19'):
            chosen = 'Son seansta ne oldu?'
    with quick_c:
        if st.button("Alonso kaçıncı oldu?", use_container_width=True, key='assistant_alonso_v19'):
            chosen = 'Alonso kaçıncı oldu?'
    if prompt or chosen:
        answer = paddock_assistant_answer_v19(prompt or chosen, 2026)
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid #5ddcff'><div class='hud-label'>{html_lib.escape(answer['title'])}</div>"
            f"<div style='font-size:1.04rem;font-weight:800;color:#eef6ff;margin-top:8px;line-height:1.55'>{html_lib.escape(answer['answer'])}</div>"
            f"<div class='driver-meta' style='margin-top:10px'>Kaynak: {html_lib.escape(answer['source'])}</div></div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div class='hud-card' style='margin-top:14px'><div class='hud-label'>YETKİ SINIRI</div><div class='history-copy' style='margin-top:6px'>"
        "Bu sürüm, sonuç/race-control/lastik gibi sitede doğrulanmış veri üzerinden konuşur. Genel sohbet eden büyük dil modeli eklemek için ayrı bir AI sağlayıcı anahtarı gerekir; anahtar yokken uydurma “AI” cevabı gösterilmez.</div></div>",
        unsafe_allow_html=True,
    )


def render_paddock_assistant_v20():
    """Veri sorularını kesin, genel soruları ise anahtar varsa gerçek ChatGPT ile yanıtlayan sohbet ekranı."""
    st.markdown("## 🧠 Paddock Asistanı")
    ai_ready = bool(configured_openai_api_key())
    state_label = 'CHATGPT + DOĞRULANMIŞ VERİ' if ai_ready else 'DOĞRULANMIŞ VERİ MODU'
    state_copy = (
        'Genel F1 soruları için ChatGPT bağlantısı etkin. Yarış sonucu sorularında önce FastF1 doğrulanmış verisi kullanılır.'
        if ai_ready else
        'Yarış sonucu, lastik, pole ve seans özeti soruları FastF1 verisinden yanıtlanır. Genel ChatGPT sohbeti için OPENAI_API_KEY eklenmelidir.'
    )
    st.markdown(
        f"<div class='hud-card' style='border-left:4px solid {'#22d3ee' if ai_ready else '#f7c948'}'>"
        f"<div class='hud-label'>{state_label}</div><div class='history-copy' style='margin-top:7px'>{state_copy}</div></div>",
        unsafe_allow_html=True,
    )
    if 'paddock_chat_history_v20' not in st.session_state:
        st.session_state['paddock_chat_history_v20'] = []

    quick_a, quick_b, quick_c = st.columns(3)
    chosen = ''
    with quick_a:
        if st.button('Pole kim?', use_container_width=True, key='assistant_pole_v20'):
            chosen = 'Pole kim?'
    with quick_b:
        if st.button('Son seansta ne oldu?', use_container_width=True, key='assistant_story_v20'):
            chosen = 'Son seansta ne oldu?'
    with quick_c:
        if st.button('Alonso kaçıncı oldu?', use_container_width=True, key='assistant_alonso_v20'):
            chosen = 'Alonso kaçıncı oldu?'

    for item in st.session_state['paddock_chat_history_v20'][-8:]:
        with st.chat_message(item['role']):
            st.markdown(item['text'])
            if item.get('source'):
                st.caption(f"Kaynak: {item['source']}")

    prompt = st.chat_input('F1 hakkında bir şey sor… örn. Alonso kaçıncı oldu?')
    question = chosen or prompt
    if question:
        st.session_state['paddock_chat_history_v20'].append({'role': 'user', 'text': question})
        with st.chat_message('user'):
            st.markdown(question)
        with st.chat_message('assistant'):
            with st.spinner('Paddock verisi kontrol ediliyor…'):
                answer = paddock_assistant_answer_v19(question, 2026)
            st.markdown(answer['answer'])
            st.caption(f"Kaynak: {answer['source']}")
        st.session_state['paddock_chat_history_v20'].append({
            'role': 'assistant', 'text': answer['answer'], 'source': answer['source'],
        })


STEWARDLE_META = {
    # ülke kodu, gerçek F1 ilk sezonu, dünya şampiyonluğu
    'RUS': ('GB', 2019, 0), 'ANT': ('IT', 2025, 0),
    'LEC': ('MC', 2018, 0), 'HAM': ('GB', 2007, 7),
    'NOR': ('GB', 2019, 1), 'PIA': ('AU', 2023, 0),
    'VER': ('NL', 2015, 4), 'HAD': ('FR', 2025, 0),
    'GAS': ('FR', 2017, 0), 'COL': ('AR', 2024, 0),
    'LAW': ('NZ', 2023, 0), 'LIN': ('GB', 2026, 0),
    'OCO': ('FR', 2016, 0), 'BEA': ('GB', 2024, 0),
    'SAI': ('ES', 2015, 0), 'ALB': ('TH', 2019, 0),
    'HUL': ('DE', 2010, 0), 'BOR': ('BR', 2025, 0),
    'ALO': ('ES', 2001, 2), 'STR': ('CA', 2017, 0),
    'PER': ('MX', 2011, 0), 'BOT': ('FI', 2013, 0),
}


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


def stewarlde_cell(value, target, numeric=False):
    """Wordle benzeri, ama F1 verisi için dürüst eşleşme göstergesi."""
    if str(value) == str(target):
        return 'match', '✓'
    if numeric:
        try:
            return ('near', '↑' if int(value) < int(target) else '↓')
        except (TypeError, ValueError):
            pass
    return 'miss', '—'


def render_stewarlde():
    """Günün 2026 F1 sürücüsünü tahmin et: sade, yerel ve her gün aynı cevap."""
    drivers = stewarlde_drivers()
    daily_key = datetime.date.today().isoformat()
    target = drivers[datetime.date.today().toordinal() % len(drivers)]
    state_key = 'stewarlde_state_v1'
    if st.session_state.get(state_key, {}).get('day') != daily_key:
        st.session_state[state_key] = {'day': daily_key, 'guesses': [], 'finished': False}
    game = st.session_state[state_key]

    st.markdown('## 🎮 Stewarlde')
    st.caption('Günün resmî 2026 F1 pilotunu altı tahminde bul. Yeşil doğru; sarı sayı için yön ipucu; gri eşleşmedi demek.')
    st.markdown(
        "<div class='hud-card' style='border-left:4px solid #ff385c'><div class='hud-label'>GÜNLÜK PADDOCK BULMACASI</div>"
        "<div class='history-copy' style='margin-top:7px'>Her gün tek cevap vardır; 22 pilot cevap havuzunda sırayla döner. Takım, numara, ülke kodu, gerçek F1 başlangıç yılı ve dünya şampiyonluğu ipuçlarını kullan.</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['code'] not in used]
        pick = st.selectbox(
            'Pilot tahminin', options, format_func=lambda item: f"{item['name']} ({item['code']})",
            key=f"stewarlde_pick_{daily_key}_{len(game['guesses'])}",
        )
        if st.button('Tahmini gönder', type='primary', use_container_width=True, key=f"stewarlde_submit_{daily_key}_{len(game['guesses'])}"):
            game['guesses'].append(pick['code'])
            game['finished'] = pick['code'] == target['code'] or len(game['guesses']) >= 6
            st.session_state[state_key] = game
            st.rerun()

    lookup = {driver['code']: driver for driver in drivers}
    if game['guesses']:
        cards = []
        for code in game['guesses']:
            guess = lookup[code]
            values = [
                ('Pilot', guess['name'], guess['code'] == target['code'], ''),
                ('Takım', guess['team'], guess['team'] == target['team'], ''),
                ('No', guess['number'], *stewarlde_cell(guess['number'], target['number'], True)),
                ('Ülke', guess['nation'], guess['nation'] == target['nation'], ''),
                ('İlk F1 yılı', guess['debut'], *stewarlde_cell(guess['debut'], target['debut'], True)),
                ('Şampiyonluk', guess['titles'], *stewarlde_cell(guess['titles'], target['titles'], True)),
            ]
            cells = []
            for label, value, status, hint in values:
                css = 'match' if status is True or status == 'match' else 'near' if status == 'near' else 'miss'
                cells.append(f"<div class='steardle-cell {css}'><small>{html_lib.escape(label)}</small><b>{html_lib.escape(str(value))}</b><i>{html_lib.escape(str(hint))}</i></div>")
            cards.append("<div class='steardle-row'>" + ''.join(cells) + '</div>')
        st.markdown(
            "<style>.steardle-row{display:grid;grid-template-columns:1.55fr 1.35fr repeat(4,1fr);gap:7px;margin:9px 0}.steardle-cell{min-height:58px;border:1px solid #2d435c;border-radius:8px;padding:8px;background:#111b29}.steardle-cell small{display:block;color:#9aafc4;font-size:.68rem;font-weight:800}.steardle-cell b{display:block;color:#f4f8fc;font-size:.92rem;margin-top:5px}.steardle-cell i{float:right;font-style:normal;font-weight:950}.steardle-cell.match{background:#123f31;border-color:#45d991}.steardle-cell.near{background:#4c3d16;border-color:#efc84a}.steardle-cell.miss{background:#252c36;border-color:#465463}@media(max-width:760px){.steardle-row{grid-template-columns:repeat(2,1fr)}.steardle-cell{min-height:52px}}</style>"
            + ''.join(cards), unsafe_allow_html=True,
        )

    if game['finished']:
        won = game['guesses'][-1] == target['code']
        if won:
            st.success(f"Pole pozisyonu! Bugünün pilotu: {target['name']}. {len(game['guesses'])}/6 tahminde buldun.")
        else:
            st.error(f"Bugünkü altı tahmin bitti. Doğru cevap: {target['name']} ({target['code']}).")
        profile_colour = team_colour(target['team'])
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {profile_colour};margin-top:12px'>"
            f"<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>"
            f"<img src='{html_lib.escape(target['photo'], quote=True)}' style='width:130px;height:160px;object-fit:contain;object-position:center bottom' alt='{html_lib.escape(target['name'])}'>"
            f"<div><div class='hud-label'>GÜNÜN PİLOTU</div><div style='font-size:1.7rem;font-weight:950;color:{profile_colour};margin-top:4px'>{html_lib.escape(target['name'])}</div>"
            f"<div class='driver-meta' style='margin-top:7px'>{html_lib.escape(target['team'])} · #{html_lib.escape(target['number'])} · {html_lib.escape(target['nation'])} · {html_lib.escape(str(target['age']))} yaş</div>"
            f"<div class='history-copy' style='margin-top:8px'>F1 başlangıcı: {html_lib.escape(str(target['debut']))} · Dünya şampiyonluğu: {html_lib.escape(str(target['titles']))}</div></div></div></div>",
            unsafe_allow_html=True,
        )
        if st.button('Bu günün tahminlerini temizle', key=f'stewarlde_reset_{daily_key}'):
            st.session_state[state_key] = {'day': daily_key, 'guesses': [], 'finished': False}
            st.rerun()


def _gridmaster_options(values, correct, offset):
    """Tekrarsız, günlük olarak değişen dört şıklı oyun seçeneği üretir."""
    distinct = []
    for value in values:
        text = str(value)
        if text not in distinct:
            distinct.append(text)
    answer = str(correct)
    alternatives = [value for value in distinct if value != answer]
    picks = [answer]
    for index in range(min(3, len(alternatives))):
        picks.append(alternatives[(offset + index * 5) % len(alternatives)])
    # Dört seçenek her zaman aynı sırada kalmasın; gün anahtarı sıralamayı değiştirir.
    return [picks[(index + offset) % len(picks)] for index in range(len(picks))]


def gridmaster_questions():
    """GridMaster: sadece yerel 2026 kadro verisiyle çalışan günlük F1 Sprint Quiz."""
    drivers = stewarlde_drivers()
    day = datetime.date.today().toordinal()
    questions = []
    templates = [
        ('team', 'Hangi takımda yarışıyor?', lambda item: item['team'], lambda items: [item['team'] for item in items]),
        ('number', 'Araç numarası kaç?', lambda item: '#' + str(item['number']), lambda items: ['#' + str(item['number']) for item in items]),
        ('nation', 'Ülke kodu nedir?', lambda item: item['nation'], lambda items: [item['nation'] for item in items]),
        ('debut', 'İlk F1 sezonu hangisi?', lambda item: str(item['debut']), lambda items: [str(item['debut']) for item in items]),
        ('titles', 'Kaç dünya şampiyonluğu var?', lambda item: str(item['titles']), lambda items: [str(item['titles']) for item in items]),
    ]
    for turn in range(10):
        driver = drivers[(day * 7 + turn * 5) % len(drivers)]
        field, prompt, value_fn, pool_fn = templates[(day + turn) % len(templates)]
        answer = value_fn(driver)
        questions.append({
            'id': f'{driver["code"]}_{field}_{turn}',
            'driver': driver,
            'prompt': prompt,
            'answer': str(answer),
            'options': _gridmaster_options(pool_fn(drivers), answer, day + turn * 3),
        })
    return questions


def render_gridmaster():
    """Yeni oyun: 10 soruluk, hata toleranslı günlük F1 Sprint Quiz."""
    questions = gridmaster_questions()
    today = datetime.date.today().isoformat()
    state_key = 'gridmaster_state_v1'
    state = st.session_state.get(state_key, {})
    if state.get('day') != today:
        state = {'day': today, 'index': 0, 'score': 0, 'answers': [], 'finished': False}
        st.session_state[state_key] = state

    st.markdown('## ⚡ GridMaster')
    st.caption('10 soruluk günlük F1 Sprint Quiz. Her cevap 1 puan; bütün sorular 2026 kadro verisinden üretilir.')
    st.markdown(
        "<div class='hud-card' style='border-left:4px solid #f7c948'><div class='hud-label'>PIT WALL // SPRINT QUIZ</div>"
        "<div class='history-copy' style='margin-top:7px'>Pilot, takım, numara, ülke kodu, ilk F1 yılı ve şampiyonluk bilgilerini kullan. "
        "Sorular her gün değişir; tahmin hakkı sınırsız değildir, cevap geri alınamaz.</div></div>",
        unsafe_allow_html=True,
    )

    if not state['finished']:
        question = questions[state['index']]
        driver = question['driver']
        colour = team_colour(driver['team'])
        progress = int(state['index'] / len(questions) * 100)
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {colour};margin-top:14px'>"
            f"<div class='hud-label'>SORU {state['index'] + 1} / {len(questions)} · PUAN {state['score']}</div>"
            f"<div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:10px'>"
            f"<img src='{html_lib.escape(driver['photo'], quote=True)}' alt='' style='width:82px;height:94px;object-fit:contain;object-position:center bottom'>"
            f"<div><div style='font-size:1.35rem;font-weight:950;color:{colour}'>{html_lib.escape(driver['name'])}</div>"
            f"<div class='driver-meta'>{html_lib.escape(driver['code'])} · {html_lib.escape(driver['team'])}</div>"
            f"<div style='font-size:1.05rem;font-weight:850;margin-top:8px'>{html_lib.escape(question['prompt'])}</div></div></div>"
            f"<div style='height:7px;background:#09111b;border-radius:99px;margin-top:13px;overflow:hidden'><div style='height:100%;width:{progress}%;background:{colour}'></div></div></div>",
            unsafe_allow_html=True,
        )
        answer = st.radio(
            'Cevabın', question['options'], index=None, horizontal=True,
            key=f"gridmaster_pick_{today}_{state['index']}",
            label_visibility='collapsed',
        )
        if st.button('Cevabı kilitle', type='primary', use_container_width=True, disabled=answer is None, key=f"gridmaster_lock_{today}_{state['index']}"):
            correct = answer == question['answer']
            state['answers'].append({'question': question, 'answer': answer, 'correct': correct})
            state['score'] += int(correct)
            state['index'] += 1
            state['finished'] = state['index'] >= len(questions)
            st.session_state[state_key] = state
            st.rerun()
    else:
        score = state['score']
        if score == len(questions):
            title, note = '🏆 Kusursuz tur!', 'Pit wall seni baş mühendis ilan etti: 10/10.'
        elif score >= 7:
            title, note = '🟢 Güçlü hafta sonu', f'{score}/10 — sağlam bir yarış mühendisi performansı.'
        elif score >= 4:
            title, note = '🟡 Orta grup mücadelesi', f'{score}/10 — verileri biraz daha kurcalamalısın.'
        else:
            title, note = '🔴 Zor bir seans', f'{score}/10 — yarın yeni bir Sprint Quiz gelecek.'
        st.markdown(f"<div class='hud-card' style='border-left:4px solid #f7c948;margin-top:14px'><div style='font-size:1.45rem;font-weight:950'>{title}</div><div class='history-copy' style='margin-top:7px'>{note}</div></div>", unsafe_allow_html=True)
        result_rows = []
        for item in state['answers']:
            question = item['question']
            result_rows.append({
                'Pilot': question['driver']['name'], 'Soru': question['prompt'],
                'Cevabın': item['answer'], 'Doğru cevap': question['answer'],
                'Durum': '✓ Doğru' if item['correct'] else '✕ Yanlış',
            })
        st.dataframe(pd.DataFrame(result_rows), use_container_width=True, hide_index=True)
        if st.button('Bugünün Sprint Quizini yeniden başlat', use_container_width=True, key=f'gridmaster_reset_{today}'):
            st.session_state[state_key] = {'day': today, 'index': 0, 'score': 0, 'answers': [], 'finished': False}
            st.rerun()


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


@st.cache_data(ttl=86400, show_spinner=False)
def _legacy_position_replay_payload(year, event_name):
    """Tamamlanmış bir yarışın gerçek araç konumlarını tarayıcıda oynatılacak pakete dönüştürür."""
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=True, weather=False, messages=False)

        if session.results is None or session.results.empty or not getattr(session, 'pos_data', None):
            return {'ok': False, 'reason': 'Bu yarışın araç konum paketi henüz FastF1 verisinde bulunmuyor.'}

        raw_cars = []
        all_starts = []
        for _, result in session.results.iterrows():
            code = str(result.get('Abbreviation', '')).strip()
            raw_number = result.get('DriverNumber', '')
            try:
                number = str(int(float(raw_number)))
            except (TypeError, ValueError):
                number = str(raw_number).strip()
            if not code:
                continue

            positions = None
            for candidate in [number, int(number) if number.isdigit() else number, code]:
                try:
                    candidate_data = session.pos_data[candidate]
                    if candidate_data is not None and not candidate_data.empty:
                        positions = candidate_data
                        break
                except Exception:
                    continue
            if positions is None:
                continue

            source = positions[[column for column in ['Time', 'X', 'Y'] if column in positions.columns]].copy()
            if not {'Time', 'X', 'Y'}.issubset(source.columns):
                continue
            source = source.dropna(subset=['Time', 'X', 'Y'])
            if source.empty:
                continue
            source['_time'] = pd.to_timedelta(source['Time']).dt.total_seconds()
            source['X'] = pd.to_numeric(source['X'], errors='coerce')
            source['Y'] = pd.to_numeric(source['Y'], errors='coerce')
            source = source.dropna(subset=['_time', 'X', 'Y']).sort_values('_time').drop_duplicates('_time')
            if len(source) < 2:
                continue

            all_starts.append(float(source['_time'].iloc[0]))
            laps = []
            try:
                driver_laps = session.laps.pick_drivers(code)
                for _, lap in driver_laps.iterrows():
                    lap_start = _timedelta_seconds(lap.get('LapStartTime'))
                    lap_time = _timedelta_seconds(lap.get('LapTime'))
                    if lap_start is None or lap_time is None or lap_time <= 0:
                        continue
                    laps.append({
                        'lap': int(lap.get('LapNumber', len(laps) + 1)),
                        'start': round(lap_start, 3),
                        'end': round(lap_start + lap_time, 3),
                        'compound': str(lap.get('Compound', '')).upper(),
                        'stint': int(lap.get('Stint', 0)) if pd.notna(lap.get('Stint')) else 0,
                        'pit_in': _timedelta_seconds(lap.get('PitInTime')),
                        'pit_out': _timedelta_seconds(lap.get('PitOutTime')),
                    })
            except Exception:
                laps = []

            raw_cars.append({
                'code': code,
                'number': number,
                'team': str(result.get('TeamName', 'Takım')),
                'colour': team_colour(str(result.get('TeamName', ''))),
                'status': str(result.get('Status', 'Finished')),
                'position': result.get('Position', 99),
                'source': source,
                'laps': laps,
            })

        if not raw_cars or not all_starts:
            return {'ok': False, 'reason': 'Bu yarış için yeterli araç konumu bulunamadı.'}

        # İlk turdaki tek bir bozuk/erken zaman kaydı bütün araçları dakikalarca
        # bekletebiliyordu. Ortanca çevresindeki normal başlangıç kümesini alıyoruz.
        start_values = np.asarray(all_starts, dtype=float)
        start_median = float(np.median(start_values))
        normal_starts = start_values[np.abs(start_values - start_median) <= 20.0]
        race_start = float(np.median(normal_starts if len(normal_starts) else start_values))
        cars = []
        track = []
        for car_index, item in enumerate(raw_cars):
            source = item.pop('source').copy()
            source['_time'] = source['_time'] - race_start
            duration = float(source['_time'].iloc[-1])
            # 20 araç için tarayıcıyı yormayan, ama akıcı görünmesini sağlayan gerçek konum örnekleri.
            point_count = max(180, min(850, int(max(duration, 1) / 1.15)))
            grid = np.linspace(float(source['_time'].iloc[0]), duration, point_count)
            points = [
                [round(float(value), 2), round(float(np.interp(value, source['_time'], source['X'])), 1), round(float(np.interp(value, source['_time'], source['Y'])), 1)]
                for value in grid
            ]
            if car_index == 0:
                outline = source.iloc[::max(1, len(source) // 520)]
                track = [[round(float(row['X']), 1), round(float(row['Y']), 1)] for _, row in outline.iterrows()]

            for lap in item['laps']:
                lap['start'] = round(lap['start'] - race_start, 3)
                lap['end'] = round(lap['end'] - race_start, 3)
                if lap['pit_in'] is not None:
                    lap['pit_in'] = round(lap['pit_in'] - race_start, 3)
                if lap['pit_out'] is not None:
                    lap['pit_out'] = round(lap['pit_out'] - race_start, 3)

            try:
                final_position = int(float(item['position']))
            except (TypeError, ValueError):
                final_position = 99
            cars.append({
                'code': item['code'], 'number': item['number'], 'team': item['team'], 'colour': item['colour'],
                'status': item['status'], 'final_position': final_position, 'points': points, 'laps': item['laps'],
            })

        race_events = []
        try:
            seen = set()
            status_names = {'4': 'GÜVENLİK ARACI', '5': 'KIRMIZI BAYRAK', '6': 'SANAL GÜVENLİK ARACI', '7': 'VSC BİTTİ'}
            for _, item in session.track_status.iterrows():
                code = str(item.get('Status', ''))
                if code not in status_names:
                    continue
                event_time = _timedelta_seconds(item.get('Time'))
                if event_time is None:
                    continue
                key = (round(event_time - race_start), code)
                if key not in seen:
                    seen.add(key)
                    race_events.append({'time': round(event_time - race_start, 2), 'label': status_names[code]})
        except Exception:
            pass

        return {
            'ok': True,
            'event': str(session.event.get('EventName', event_name)),
            'total_seconds': round(
                max((car['points'][-1][0] for car in cars if car.get('points')), default=0.0),
                1,
            ),
            'track': track,
            'cars': cars,
            'events': race_events,
        }
    except Exception as error:
        return {'ok': False, 'reason': f'Yarış tekrar verisi hazırlanamadı: {error}'}


def race_replay_html(payload):
    """Gerçek FastF1 konum örneklerini yaklaşık iki dakikalık, etkileşimli yarış tekrarına çizer."""
    packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return r"""
    <style>
      *{box-sizing:border-box} body{margin:0;background:#090d14;color:#eef6ff;font-family:Inter,Segoe UI,Arial,sans-serif}
      .hud{border:1px solid #2d415a;border-radius:14px;background:linear-gradient(135deg,#101a2a,#0a111b);padding:14px}
      .top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.title{font-size:14px;font-weight:950;letter-spacing:.1em}.sub{font-size:11px;color:#96abc3;margin-top:5px}.badge{border:1px solid #38506f;background:#132037;border-radius:8px;padding:7px 10px;color:#77e5af;font-size:11px;font-weight:900}
      .layout{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:12px;margin-top:12px}.map{border:1px solid #2a4059;border-radius:11px;background:radial-gradient(circle at 50% 45%,#16243a,#080d14 72%);overflow:hidden}.map canvas{display:block;width:100%;height:510px}
      .controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}.btn{border:1px solid #3b5270;border-radius:7px;background:#142238;color:#edf6ff;padding:7px 10px;font-weight:900;cursor:pointer}.btn.active{border-color:#ff354a;background:#3a1822}.slider{flex:1;min-width:130px;accent-color:#ff354a}.clock{font-family:ui-monospace,Consolas,monospace;font-weight:900;color:#e9f3ff;font-size:12px}
      .panel{border:1px solid #2d415a;border-radius:11px;background:#101a2a;padding:12px;min-width:0}.selected{font-size:21px;font-weight:950;color:var(--team)}.team{color:#99aec3;font-size:12px;margin:4px 0 12px}.stat{display:flex;justify-content:space-between;gap:8px;border-top:1px solid #26384e;padding:8px 0;font-size:12px}.stat span{color:#95a8bd}.tyre{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border:2px solid var(--tyre);border-radius:50%;color:var(--tyre);font-weight:950}.mini{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}.events{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}.event{border:1px solid #7b6034;background:#2b2315;color:#ffd57d;border-radius:6px;padding:5px 7px;font-size:10px;font-weight:900}
      .strip{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.pilot{background:#111d2e;border:1px solid #304661;border-left:4px solid var(--team);border-radius:7px;color:#eef6ff;padding:6px 8px;cursor:pointer;font-size:11px;font-weight:900}.pilot.active{background:#1c2e46;box-shadow:0 0 0 1px var(--team) inset}
      @media(max-width:850px){.layout{grid-template-columns:1fr}.map canvas{height:410px}}
    </style>
    <div class="hud"><div class="top"><div><div class="title">RACE REPLAY // 2D TRACK HUD</div><div class="sub" id="subtitle">Gerçek FastF1 konum verisi • tüm araçlar • lastik ve pit akışı</div></div><div class="badge">● TAMAMLANMIŞ YARIŞ TEKRARI</div></div>
    <div class="layout"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn" data-speed="0.5">0.5×</button><button class="btn active" data-speed="1">2 dk</button><button class="btn" data-speed="2">1 dk</button><input class="slider" id="progress" type="range" min="0" max="1000" value="0"><span class="clock" id="clock">00:00</span></div><div class="events" id="events"></div><div class="strip" id="strip"></div></div><aside class="panel" id="panel"></aside></div></div>
    <script>
      const data=__RACE_REPLAY_PAYLOAD__,cars=data.cars||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d');
      const tyres={SOFT:'#ff4654',MEDIUM:'#ffd23e',HARD:'#f0f4f8',INTERMEDIATE:'#45d875',WET:'#42a9ff'};let selected=cars[0]?.number||'',playing=true,speed=1,progress=0,last=performance.now(); const runtime=120;
      function fmt(value){value=Math.max(0,Math.round(value));return String(Math.floor(value/60)).padStart(2,'0')+':'+String(value%60).padStart(2,'0')}
      function point(car,t){const a=car.points||[];if(!a.length)return null;if(t<=a[0][0])return {x:a[0][1],y:a[0][2],out:false};if(t>=a[a.length-1][0])return {x:a[a.length-1][1],y:a[a.length-1][2],out:true};let lo=0,hi=a.length-1;while(lo<hi-1){const m=(lo+hi)>>1;if(a[m][0]<t)lo=m;else hi=m}const p=a[lo],n=a[hi],r=(t-p[0])/(n[0]-p[0]||1);return{x:p[1]+(n[1]-p[1])*r,y:p[2]+(n[2]-p[2])*r,out:false}}
      function lap(car,t){return (car.laps||[]).find(x=>t>=x.start&&t<=x.end)||(car.laps||[]).filter(x=>x.start<=t).slice(-1)[0]||null}
      function transform(){const pts=data.track||cars.flatMap(c=>c.points.map(p=>[p[1],p[2]]));if(!pts.length)return null;const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=28,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}
      function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
      function f1(x,y,colour,code,chosen,out){ctx.save();ctx.translate(x,y);ctx.globalAlpha=out?.35:1;ctx.fillStyle='#050a11';ctx.fillRect(-11,-7,5,14);ctx.fillRect(8,-8,4,16);ctx.fillStyle=colour;ctx.fillRect(-7,-4,17,8);ctx.fillRect(8,-2,8,4);ctx.fillRect(11,-8,3,16);ctx.fillStyle='#ecf6ff';ctx.fillRect(0,-1,6,2);if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.strokeRect(-13,-10,30,20)}ctx.restore();ctx.fillStyle=colour;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-14)}
      function rank(c,t){const current=lap(c,t);const lapNo=current?.lap||0;const frac=current?Math.min(1,Math.max(0,(t-current.start)/(current.end-current.start||1))):0;return lapNo+frac}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const tr=transform();if(!tr)return;const outline=data.track||[];ctx.strokeStyle='#7990aa';ctx.globalAlpha=.48;ctx.lineWidth=3;ctx.beginPath();outline.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},tr);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;cars.map(c=>({c,p:point(c,progress)})).filter(x=>x.p).forEach(({c,p})=>{const q=xy(p,tr);f1(q[0],q[1],c.colour,c.code,c.number===selected,p.out)})}
      function ordered(){return cars.slice().sort((a,b)=>rank(b,progress)-rank(a,progress))}
      function panel(){const c=cars.find(x=>x.number===selected)||cars[0];if(!c)return;const l=lap(c,progress),tyre=(l?.compound||'—').toUpperCase(),tc=tyres[tyre]||'#788aa0',position=ordered().findIndex(x=>x.number===c.number)+1;const pit=l&&((l.pit_in&&Math.abs(progress-l.pit_in)<18)||(l.pit_out&&Math.abs(progress-l.pit_out)<18));document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').innerHTML=`<div class="selected">${c.code} <span style="font-size:12px;color:#a6b8cc">P${position}</span></div><div class="team">${c.team}</div><div class="stat"><span>Yarış zamanı</span><b>${fmt(progress)}</b></div><div class="stat"><span>Tur</span><b>${l?'#'+l.lap:'Başlangıç'}</b></div><div class="stat"><span>Stint</span><b>${l?.stint||'—'}</b></div><div class="stat"><span>Lastik</span><b><i class="tyre" style="--tyre:${tc}">${tyre.slice(0,1)}</i> ${tyre}</b></div><div class="stat"><span>Pit durumu</span><b style="color:${pit?'#ffcf62':'#8ee5b1'}">${pit?'PIT AKIŞI':'PİSTTE'}</b></div><div class="stat"><span>Yarış sonucu</span><b>${c.final_position<90?'P'+c.final_position:c.status}</b></div><div class="mini">Sıra, tur ilerleme oranından hesaplanan yarış akışıdır. Araçların pist koordinatları gerçek FastF1 kaydından gelir.</div>`}
      function strip(){document.getElementById('strip').innerHTML=ordered().map((c,i)=>`<button class="pilot ${c.number===selected?'active':''}" style="--team:${c.colour}" data-n="${c.number}">P${i+1} · ${c.code}</button>`).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.n;draw();panel();strip()})}
      function ui(){document.getElementById('progress').value=Math.round(1000*progress/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(progress)+' / '+fmt(data.total_seconds);const active=(data.events||[]).filter(e=>Math.abs(e.time-progress)<18);document.getElementById('events').innerHTML=active.map(e=>`<span class="event">⚑ ${e.label}</span>`).join('')||'<span class="event" style="opacity:.55">Yarış akışı oynatılıyor</span>';draw();panel();strip()}
      function step(now){const dt=(now-last)/1000;last=now;if(playing){progress+=dt*(data.total_seconds||1)*speed/runtime;if(progress>=data.total_seconds){progress=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan oynat'}}ui();requestAnimationFrame(step)}
      function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);ui()} document.getElementById('play').onclick=()=>{if(progress>=data.total_seconds)progress=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('progress').oninput=e=>{progress=(Number(e.target.value)/1000)*(data.total_seconds||0);ui()};canvas.onclick=e=>{const tr=transform(),r=canvas.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let best=null,dist=1e9;cars.forEach(c=>{const p=point(c,progress);if(!p)return;const q=xy(p,tr),d=Math.hypot(q[0]-mx,q[1]-my);if(d<dist){dist=d;best=c}});if(best&&dist<32){selected=best.number;ui()}};document.getElementById('subtitle').textContent=(data.event||'Formula 1')+' • gerçek araç konumları • tam yarış';window.addEventListener('resize',resize);resize();requestAnimationFrame(step);
    </script>
    """.replace('__RACE_REPLAY_PAYLOAD__', packed)


def _race_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


TEAM_LIVERY_ACCENTS = {
    'Mercedes': '#071f22', 'Ferrari': '#ffe7df', 'McLaren': '#17191d',
    'Red Bull Racing': '#ffcf32', 'Alpine': '#1540a0', 'Racing Bulls': '#ffffff',
    'Haas F1 Team': '#d92431', 'Williams': '#163e8c', 'Audi': '#111111',
    'Aston Martin': '#d7f6ee', 'Cadillac': '#1e1e24',
}


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


@st.cache_data(ttl=1800, show_spinner=False)
def _legacy_clean_replay_payload(year, event_name):
    """Gerçek Position Data ile akıcı, tam yarış ölçekli 2D replay paketi üretir.

    Tur süresiyle pist üzerinde ilerleme tahmini yerine, FastF1'in araç konum
    kaydını kullanır. Böylece ilk turda araçların beklemesi / aniden öne atlaması
    engellenir; arazi çizimi de V18'deki gerçek pist kaynağına geri döner.
    """
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=True, weather=False, messages=False)
        if session.results is None or session.results.empty or session.laps is None or session.laps.empty:
            return {'ok': False, 'reason': 'Bu yarışın doğrulanmış tur verisi henüz hazır değil.'}

        reference_lap = session.laps.pick_fastest()
        if reference_lap is None:
            return {'ok': False, 'reason': 'Temiz pist çizimi için referans tur bulunamadı.'}
        telemetry = reference_lap.get_telemetry()
        telemetry_track = telemetry[[column for column in ['Distance', 'X', 'Y'] if column in telemetry.columns]].dropna().copy()
        if not {'Distance', 'X', 'Y'}.issubset(telemetry_track.columns):
            return {'ok': False, 'reason': 'Pist koordinatı bu yarışın telemetri kaydında yok.'}
        telemetry_track['Distance'] = pd.to_numeric(telemetry_track['Distance'], errors='coerce')
        telemetry_track = telemetry_track.dropna().sort_values('Distance').drop_duplicates('Distance')
        if len(telemetry_track) < 20:
            return {'ok': False, 'reason': 'Pist çizimi için yeterli telemetri noktası yok.'}
        track_overlay = build_track_overlay(telemetry, reference_lap, session)

        def position_source(result, code):
            """FastF1'in değişebilen anahtar tipleri arasında gerçek araç konumunu bulur."""
            raw_number = result.get('DriverNumber', '')
            try:
                number = str(int(float(raw_number)))
            except (TypeError, ValueError):
                number = str(raw_number).strip()

            positions = None
            for candidate in [number, int(number) if number.isdigit() else number, code]:
                try:
                    candidate_data = session.pos_data[candidate]
                    if candidate_data is not None and not candidate_data.empty:
                        positions = candidate_data
                        break
                except Exception:
                    continue
            if positions is None:
                return pd.DataFrame()

            source = positions[[column for column in ['Time', 'X', 'Y'] if column in positions.columns]].copy()
            if not {'Time', 'X', 'Y'}.issubset(source.columns):
                return pd.DataFrame()
            source = source.dropna(subset=['Time', 'X', 'Y'])
            if source.empty:
                return pd.DataFrame()
            source['_time'] = pd.to_timedelta(source['Time']).dt.total_seconds()
            source['X'] = pd.to_numeric(source['X'], errors='coerce')
            source['Y'] = pd.to_numeric(source['Y'], errors='coerce')
            return source.dropna(subset=['_time', 'X', 'Y']).sort_values('_time').drop_duplicates('_time')

        raw_cars, lap_start_candidates, position_start_candidates, total_laps = [], [], [], 0
        for _, result in session.results.iterrows():
            code = str(result.get('Abbreviation', '')).strip()
            if not code:
                continue

            driver_laps = session.laps.pick_drivers(code)
            race_laps = []
            for _, lap in driver_laps.iterrows():
                lap_number = _race_int(lap.get('LapNumber'))
                lap_time = _timedelta_seconds(lap.get('LapTime'))
                lap_start = _timedelta_seconds(lap.get('LapStartTime'))
                lap_end = _timedelta_seconds(lap.get('Time'))
                if lap_start is None and lap_end is not None and lap_time is not None:
                    lap_start = lap_end - lap_time
                if lap_number is None or lap_time is None or lap_time <= 0:
                    continue
                lap_position = _race_position(lap.get('Position'))
                race_laps.append({
                    'lap': int(lap_number),
                    '_raw_start': lap_start,
                    '_raw_end': lap_end,
                    '_duration': float(lap_time),
                    'position': lap_position, 'compound': str(lap.get('Compound', '')).upper(),
                    'stint': int(lap.get('Stint', 0)) if pd.notna(lap.get('Stint')) else 0,
                    'pit_in': _timedelta_seconds(lap.get('PitInTime')), 'pit_out': _timedelta_seconds(lap.get('PitOutTime')),
                })
            if not race_laps:
                continue

            race_laps.sort(key=lambda item: item['lap'])
            if race_laps[0]['_raw_start'] is not None:
                lap_start_candidates.append(race_laps[0]['_raw_start'])
            pos_source = position_source(result, code)
            if not pos_source.empty:
                position_start_candidates.append(float(pos_source['_time'].iloc[0]))

            total_laps = max(total_laps, max(item['lap'] for item in race_laps))
            raw_cars.append({
                'code': code,
                'team': str(result.get('TeamName', 'Takım')),
                'colour': team_colour(str(result.get('TeamName', ''))),
                'accent': TEAM_LIVERY_ACCENTS.get(str(result.get('TeamName', '')), '#f2f7ff'),
                'profile': race_driver_profile(code, str(result.get('TeamName', ''))),
                'grid': _race_position(result.get('GridPosition')),
                'final_position': _race_position(result.get('Position')),
                'status': str(result.get('Status', 'Finished')),
                'laps': race_laps,
                '_positions': pos_source,
            })

        if not raw_cars:
            return {'ok': False, 'reason': 'Bu yarışta araç tur geçmişi bulunamadı.'}

        # Öncelik gerçek konum kaydının ilk zamanına verilir. Böylece yarışın
        # açılışında herkes aynı zaman çizelgesinden başlar.
        race_start = min(position_start_candidates or lap_start_candidates or [0.0])
        cars = []
        for car in raw_cars:
            timeline, previous_end = [], 0.0
            for index, lap in enumerate(car['laps']):
                raw_end = lap.pop('_raw_end', None)
                duration = max(0.1, float(lap.pop('_duration', 0.1)))
                lap.pop('_raw_start', None)
                start = 0.0 if index == 0 else previous_end
                candidate_end = (raw_end - race_start) if raw_end is not None else None
                if candidate_end is None or candidate_end <= start + 0.1:
                    end = start + duration
                else:
                    end = candidate_end
                lap['start'] = round(start, 3)
                lap['end'] = round(end, 3)
                if lap['pit_in'] is not None:
                    lap['pit_in'] = round(lap['pit_in'] - race_start, 3)
                if lap['pit_out'] is not None:
                    lap['pit_out'] = round(lap['pit_out'] - race_start, 3)
                previous_end = end
                timeline.append(lap)
            car['laps'] = timeline

            source = car.pop('_positions', pd.DataFrame()).copy()
            points = []
            if not source.empty:
                source['_time'] = source['_time'] - race_start
                source = source[source['_time'] >= -0.25].copy()
                if not source.empty:
                    source['_time'] = source['_time'].clip(lower=0)
                    source = source.sort_values('_time').drop_duplicates('_time')
                    duration = float(source['_time'].iloc[-1])
                    if duration > 0:
                        point_count = max(220, min(1050, int(duration / 0.9)))
                        sample_times = np.linspace(float(source['_time'].iloc[0]), duration, point_count)
                        points = [
                            [
                                round(float(value), 2),
                                round(float(np.interp(value, source['_time'], source['X'])), 1),
                                round(float(np.interp(value, source['_time'], source['Y'])), 1),
                            ]
                            for value in sample_times
                        ]
            car['points'] = points
            cars.append(car)

        # Pist şekli V18'deki gibi gerçek Position Data'dan, referans turun
        # zaman aralığı kesilerek çıkarılır. Bu, hız telemetrisiyle oluşan yanlış
        # / deforme pist görüntüsünü engeller.
        track = []
        reference_code = str(reference_lap.get('Driver', '')).strip()
        reference_car = next((car for car in raw_cars if car['code'] == reference_code), None)
        if reference_car is not None:
            ref_source = position_source(
                next((row for _, row in session.results.iterrows() if str(row.get('Abbreviation', '')).strip() == reference_code), {}),
                reference_code,
            )
            ref_start = _timedelta_seconds(reference_lap.get('LapStartTime'))
            ref_end = _timedelta_seconds(reference_lap.get('Time'))
            if not ref_source.empty and ref_start is not None and ref_end is not None:
                ref_source = ref_source[(ref_source['_time'] >= ref_start - 1.0) & (ref_source['_time'] <= ref_end + 1.0)]
                if len(ref_source) >= 20:
                    sample_times = np.linspace(float(ref_source['_time'].iloc[0]), float(ref_source['_time'].iloc[-1]), 560)
                    track = [
                        [
                            round(float(np.interp(value, ref_source['_time'], ref_source['X'])), 1),
                            round(float(np.interp(value, ref_source['_time'], ref_source['Y'])), 1),
                        ]
                        for value in sample_times
                    ]
        if not track:
            grid = np.linspace(float(telemetry_track['Distance'].min()), float(telemetry_track['Distance'].max()), 560)
            track = [
                [
                    round(float(np.interp(value, telemetry_track['Distance'], telemetry_track['X'])), 1),
                    round(float(np.interp(value, telemetry_track['Distance'], telemetry_track['Y'])), 1),
                ]
                for value in grid
            ]

        total_seconds = max(
            [lap['end'] for car in cars for lap in car['laps']] +
            [car['points'][-1][0] for car in cars if car.get('points')] +
            [0.0]
        )
        cars.sort(key=lambda item: item['final_position'] if item['final_position'] is not None else 99)
        return {
            'ok': True,
            'event': str(session.event.get('EventName', event_name)),
            'track': track,
            'overlay': track_overlay,
            'cars': cars,
            'total_laps': total_laps,
            'total_seconds': round(total_seconds, 2),
            'position_source': 'FastF1 Position Data',
        }
    except Exception as error:
        return {'ok': False, 'reason': f'Yarış verisi hazırlanamadı: {error}'}


@st.cache_data(ttl=86400, show_spinner=False)
def _legacy_build_verified_race_replay_payload(year, event_name):
    """Alpha replay paketi: temiz pist + doğrulanmış tur zamanları.

    Bu motor ham araç koordinatlarını doğrudan çizmez. FastF1 Position Data
    paketleri seans başlangıcından da kayıt tutabildiği için yarış saatiyle
    kayabiliyor ve araçları ilk turda dondurabiliyor. Bunun yerine pist, temiz
    bir referans turdan; araç akışı ise resmî tur başlangıcı, tur süresi, sıra,
    pit ve lastik kayıtlarından kurulur.
    """
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=True, weather=False, messages=False)
        if session.results is None or session.results.empty or session.laps is None or session.laps.empty:
            return {'ok': False, 'reason': 'Bu yarışın doğrulanmış tur verisi henüz hazır değil.'}

        reference_lap = session.laps.pick_fastest()
        if reference_lap is None:
            return {'ok': False, 'reason': 'Gerçek pist çizimi için temiz referans tur bulunamadı.'}

        telemetry = reference_lap.get_telemetry()
        track_source = telemetry[
            [column for column in ['Distance', 'X', 'Y'] if column in telemetry.columns]
        ].dropna().copy()
        if not {'Distance', 'X', 'Y'}.issubset(track_source.columns):
            return {'ok': False, 'reason': 'Bu yarışın referans turunda pist koordinatı yok.'}
        track_source['Distance'] = pd.to_numeric(track_source['Distance'], errors='coerce')
        track_source['X'] = pd.to_numeric(track_source['X'], errors='coerce')
        track_source['Y'] = pd.to_numeric(track_source['Y'], errors='coerce')
        track_source = (
            track_source.dropna()
            .sort_values('Distance')
            .drop_duplicates('Distance')
        )
        if len(track_source) < 40:
            return {'ok': False, 'reason': 'Temiz pist çizimi için yeterli telemetri noktası yok.'}

        track_distance = np.linspace(
            float(track_source['Distance'].min()),
            float(track_source['Distance'].max()),
            900,
        )
        track = [
            [
                round(float(np.interp(value, track_source['Distance'], track_source['X'])), 1),
                round(float(np.interp(value, track_source['Distance'], track_source['Y'])), 1),
            ]
            for value in track_distance
        ]
        overlay = build_track_overlay(telemetry, reference_lap, session)

        raw_cars, first_lap_starts, total_laps = [], [], 0
        for _, result in session.results.iterrows():
            code = str(result.get('Abbreviation', '')).strip()
            if not code or code.lower() == 'nan':
                continue

            driver_laps = session.laps.pick_drivers(code).sort_values('LapNumber')
            race_laps = []
            for _, raw_lap in driver_laps.iterrows():
                lap_number = _race_int(raw_lap.get('LapNumber'))
                lap_time = _timedelta_seconds(raw_lap.get('LapTime'))
                lap_start = _timedelta_seconds(raw_lap.get('LapStartTime'))
                lap_end = _timedelta_seconds(raw_lap.get('Time'))
                if lap_start is None and lap_end is not None and lap_time is not None:
                    lap_start = lap_end - lap_time
                if lap_number is None or lap_start is None or lap_time is None or lap_time <= 0:
                    continue

                race_laps.append({
                    'lap': int(lap_number),
                    '_start': float(lap_start),
                    '_duration': float(lap_time),
                    'position': _race_position(raw_lap.get('Position')),
                    'compound': str(raw_lap.get('Compound', '')).upper(),
                    'stint': int(raw_lap.get('Stint', 0)) if pd.notna(raw_lap.get('Stint')) else 0,
                    '_pit_in': _timedelta_seconds(raw_lap.get('PitInTime')),
                    '_pit_out': _timedelta_seconds(raw_lap.get('PitOutTime')),
                })

            if not race_laps:
                continue
            race_laps.sort(key=lambda item: item['lap'])
            first_lap_starts.append(race_laps[0]['_start'])
            total_laps = max(total_laps, max(item['lap'] for item in race_laps))
            team_name = str(result.get('TeamName', 'Takım'))
            raw_cars.append({
                'code': code,
                'team': team_name,
                'colour': team_colour(team_name),
                'accent': TEAM_LIVERY_ACCENTS.get(team_name, '#f2f7ff'),
                'profile': race_driver_profile(code, team_name),
                'grid': _race_position(result.get('GridPosition')),
                'final_position': _race_position(result.get('Position')),
                'status': str(result.get('Status', 'Finished')),
                'laps': race_laps,
            })

        if not raw_cars or not first_lap_starts:
            return {'ok': False, 'reason': 'Bu yarış için doğrulanmış tur geçmişi bulunamadı.'}

        # Yarış saati yalnızca resmî tur başlangıçlarından oluşturulur.
        # Position Data'nın seans öncesi kayıtları bu saate kesinlikle dahil edilmez.
        race_start = min(first_lap_starts)
        cars = []
        for car in raw_cars:
            timeline, previous_end = [], 0.0
            for index, raw_lap in enumerate(car['laps']):
                proposed_start = max(0.0, raw_lap['_start'] - race_start)
                if index == 0:
                    # Gridde herkes aynı yarış başlangıcından çıkar; birkaç saniyelik
                    # ölçüm farkı korunur ama eksik kayıt nedeniyle yarım turdan başlanmaz.
                    start = min(proposed_start, 12.0)
                elif proposed_start < previous_end - 0.5 or proposed_start > previous_end + 15.0:
                    start = previous_end
                else:
                    start = max(previous_end, proposed_start)

                end = start + max(0.1, raw_lap['_duration'])
                pit_in = raw_lap['_pit_in']
                pit_out = raw_lap['_pit_out']
                pit_in = pit_in - race_start if pit_in is not None else None
                pit_out = pit_out - race_start if pit_out is not None else None
                if pit_in is not None and not (start - 5 <= pit_in <= end + 25):
                    pit_in = None
                if pit_out is not None and not (start - 5 <= pit_out <= end + 25):
                    pit_out = None

                timeline.append({
                    'lap': raw_lap['lap'],
                    'start': round(start, 3),
                    'end': round(end, 3),
                    'position': raw_lap['position'],
                    'compound': raw_lap['compound'],
                    'stint': raw_lap['stint'],
                    'pit_in': round(pit_in, 3) if pit_in is not None else None,
                    'pit_out': round(pit_out, 3) if pit_out is not None else None,
                })
                previous_end = end

            car['laps'] = timeline
            car['points'] = []
            cars.append(car)

        total_seconds = max(
            [lap['end'] for car in cars for lap in car['laps']] + [0.0]
        )
        cars.sort(key=lambda item: item['final_position'] if item['final_position'] is not None else 99)
        return {
            'ok': True,
            'event': str(session.event.get('EventName', event_name)),
            'track': track,
            'overlay': overlay,
            'cars': cars,
            'total_laps': total_laps,
            'total_seconds': round(total_seconds, 2),
            'replay_source': 'FastF1 doğrulanmış tur zamanları, sıralama, pit ve lastik verisi',
        }
    except Exception as error:
        log_data_error('verified race replay', error)
        return {'ok': False, 'reason': f'Yarış tekrar paketi hazırlanamadı: {error}'}


def _legacy_clean_race_replay_html(payload):
    """Temiz tek pist üzerinde gerçek yarış süreleriyle oynayan 2D yarış HUD'u."""
    packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return r"""
    <style>
      *{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#101a2b,#09101a)}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.title{font-weight:950;font-size:14px;letter-spacing:.1em}.sub{font-size:11px;color:#91a8c0;margin-top:5px}.badge{border:1px solid #365170;background:#122239;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.layout{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:12px;margin-top:12px}.map{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#070c13 74%);overflow:hidden}.map canvas{width:100%;height:500px;display:block}.panel{border:1px solid #2c425d;border-radius:11px;background:#101a2a;padding:12px}.selected{font-size:22px;font-weight:950;color:var(--team)}.team{font-size:12px;color:#9bafc5;margin:4px 0 12px}.stat{display:flex;justify-content:space-between;gap:8px;padding:8px 0;border-top:1px solid #26394f;font-size:12px}.stat span{color:#92a7bc}.tyre{display:inline-flex;align-items:center;justify-content:center;height:22px;width:22px;border-radius:50%;border:2px solid var(--tyre);color:var(--tyre);font-weight:950}.controls,.strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn,.pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#edf6ff;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.pilot{border-left:4px solid var(--team);font-size:11px}.pilot.active{box-shadow:0 0 0 1px var(--team) inset;background:#1c3049}.slider{accent-color:#ff4051;flex:1;min-width:135px}.clock{font:900 12px ui-monospace,Consolas,monospace}.note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.layout{grid-template-columns:1fr}.map canvas{height:395px}}
    </style><div class="hud"><div class="top"><div><div class="title">RACE CONTROL // CLEAN TRACK REPLAY</div><div class="sub" id="sub">Gerçek tur zamanı • temiz telemetri pisti • lastik ve pit akışı</div></div><div class="badge">● GERÇEK ZAMAN ÖLÇEĞİ</div></div><div class="layout"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn active" data-speed="1">1× Gerçek</button><button class="btn" data-speed="5">5×</button><button class="btn" data-speed="20">20×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="clock" id="clock">00:00</span></div><div class="strip" id="strip"></div></div><aside class="panel" id="panel"></aside></div></div>
    <script>
      const data=__CLEAN_RACE_PAYLOAD__,cars=data.cars||[],route=data.track||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d'),tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};let selected=cars[0]?.code||'',playing=true,speed=1,time=0,last=performance.now();
      function fmt(n){n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')}
      function lap(c,t){return(c.laps||[]).find(x=>t>=x.start&&t<=x.end)||(c.laps||[]).filter(x=>x.start<=t).slice(-1)[0]||null}
      function state(c,t){const l=lap(c,t);if(!l)return{lap:0,frac:0,pos:c.grid||20,out:false};const frac=Math.min(1,Math.max(0,(t-l.start)/(l.end-l.start||1)));return{lap:l.lap,frac,pos:l.position||c.final_position||20,out:t>l.end&&l.lap>=data.total_laps}}
      function point(frac){const n=route.length;if(!n)return{x:0,y:0,angle:0};const p=(frac%1)*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,angle:Math.atan2(b[1]-a[1],b[0]-a[0])}}
      function actualPoint(c,t){const samples=c.points||[];if(samples.length>=2){if(t<=samples[0][0]){const a=samples[0],b=samples[1];return{x:a[1],y:a[2],angle:Math.atan2(b[2]-a[2],b[1]-a[1])}}const lastSample=samples[samples.length-1];if(t>=lastSample[0]){const a=samples[samples.length-2],b=lastSample;return{x:b[1],y:b[2],angle:Math.atan2(b[2]-a[2],b[1]-a[1])}}let low=0,high=samples.length-1;while(low+1<high){const mid=(low+high)>>1;if(samples[mid][0]<=t)low=mid;else high=mid}const a=samples[low],b=samples[high],ratio=Math.max(0,Math.min(1,(t-a[0])/(b[0]-a[0]||1)));return{x:a[1]+(b[1]-a[1])*ratio,y:a[2]+(b[2]-a[2])*ratio,angle:Math.atan2(b[2]-a[2],b[1]-a[1])}}const s=state(c,t),launch=Math.max(0,1-Math.min(1,t/9)),gridOffset=((c.grid||1)-1)*.0018*launch;return point((s.frac-gridOffset+1)%1)}
      function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=32,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}
      function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
      function car(x,y,a,colour,code,chosen,out){ctx.save();ctx.translate(x,y);ctx.rotate(-a);ctx.globalAlpha=out?.35:1;ctx.fillStyle='#050a10';ctx.fillRect(-11,-7,5,14);ctx.fillRect(8,-8,4,16);ctx.fillStyle=colour;ctx.fillRect(-7,-4,17,8);ctx.fillRect(8,-2,8,4);ctx.fillRect(11,-8,3,16);ctx.fillStyle='#f5f8ff';ctx.fillRect(0,-1,6,2);if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.strokeRect(-13,-10,30,20)}ctx.restore();ctx.fillStyle=colour;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-15)}
      function overlayMarker(fraction,label,colour,tr){const p=point(fraction),q=xy(p,tr);ctx.fillStyle=colour;ctx.beginPath();ctx.arc(q[0],q[1],4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#edf6ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)}
      function overlayZone(start,end,label,colour,tr){ctx.beginPath();for(let i=0;i<=26;i++){const p=point(start+(end-start)*i/26),q=xy(p,tr);i?ctx.lineTo(...q):ctx.moveTo(...q)}ctx.strokeStyle=colour;ctx.globalAlpha=.92;ctx.lineWidth=6;ctx.stroke();ctx.globalAlpha=1;overlayMarker(start,label,colour,tr)}
      function drawRaceOverlay(tr){if(overlayMode==='straight'){(raceOverlay.straights||[]).forEach((zone,index)=>overlayZone(zone.start,zone.end,index?'Straight':'Straight Mode',index?'#48c8ff':'#71e6a1',tr));return}overlayMarker(0,'START / FINISH','#ffffff',tr);(raceOverlay.sectors||[]).forEach(item=>overlayMarker(item.fraction,item.label,item.colour||'#f4d35e',tr));(raceOverlay.pit||[]).forEach(item=>overlayMarker(item.fraction,item.label,'#b79cff',tr))}
      function order(){return cars.slice().sort((a,b)=>{const aa=state(a,time),bb=state(b,time);return aa.pos-bb.pos||(bb.lap+bb.frac)-(aa.lap+aa.frac)})}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);if(!route.length)return;const tr=transform();ctx.strokeStyle='#8094ad';ctx.globalAlpha=.7;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},tr);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;cars.forEach(c=>{const s=state(c,time),p=point(s.frac),q=xy(p,tr);car(q[0],q[1],p.angle,c.colour,c.code,c.code===selected,s.out)})}
      function panel(){const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),compound=(l?.compound||'—').toUpperCase(),tc=tyres[compound]||'#8292a7',pit=l&&((l.pit_in&&Math.abs(time-l.pit_in)<18)||(l.pit_out&&Math.abs(time-l.pit_out)<18));document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').innerHTML=`<div class="selected">${c.code} <span style="font-size:12px;color:#a5b8ce">P${s.pos}</span></div><div class="team">${c.team}</div><div class="stat"><span>Yarış zamanı</span><b>${fmt(time)}</b></div><div class="stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="stat"><span>Başlangıç</span><b>P${c.grid||'—'}</b></div><div class="stat"><span>Bitiş</span><b>P${c.final_position||c.status}</b></div><div class="stat"><span>Stint</span><b>${l?.stint||'—'}</b></div><div class="stat"><span>Lastik</span><b><i class="tyre" style="--tyre:${tc}">${compound.slice(0,1)}</i> ${compound}</b></div><div class="stat"><span>Pit durumu</span><b style="color:${pit?'#ffd46b':'#81e6ac'}">${pit?'PIT AKIŞI':'PİSTTE'}</b></div><div class="note">Araç konumu, her pilotun gerçek tur başlangıcı ve tur süresiyle senkron hesaplanır. Pist şekli tek temiz telemetri turundan alınır.</div>`}
      function strip(){document.getElementById('strip').innerHTML=order().map(c=>{const s=state(c,time);return`<button class="pilot ${c.code===selected?'active':''}" style="--team:${c.colour}" data-c="${c.code}">P${s.pos} · ${c.code} · T${s.lap}</button>`}).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.c;ui()})}
      function ui(){document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds);draw();panel();strip()}
      function loop(now){const dt=(now-last)/1000;last=now;if(playing){time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan oynat'}}ui();requestAnimationFrame(loop)}
      function resize(){const b=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=b.width*d;canvas.height=b.height*d;ctx.setTransform(d,0,0,d,0,0);ui()}document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{time=(Number(e.target.value)/1000)*data.total_seconds;ui()};document.getElementById('sub').textContent=(data.event||'Formula 1')+' • '+data.total_laps+' tur • gerçek zaman ölçeği';window.addEventListener('resize',resize);resize();requestAnimationFrame(loop);
    </script>""".replace('__CLEAN_RACE_PAYLOAD__', packed)


def _legacy_premium_race_replay_html(payload):
    """Akıcı canvas hareketi, seçilebilir araçlar ve portreli yarış mühendisi HUD'u."""
    packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return r"""
    <style>
      *{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#101a2b,#09101a)}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.title{font-weight:950;font-size:14px;letter-spacing:.1em}.sub{font-size:11px;color:#91a8c0;margin-top:5px}.badge{border:1px solid #365170;background:#122239;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px;margin-top:12px}.map{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#070c13 74%);overflow:hidden}.map canvas{width:100%;height:500px;display:block}.panel{border:1px solid #2c425d;border-radius:11px;background:#101a2a;padding:12px;overflow:hidden}.hero{position:relative;min-height:112px;border-bottom:1px solid #2b4058;margin:-12px -12px 11px;padding:13px;overflow:hidden;background:linear-gradient(110deg,#101a2a 0%,color-mix(in srgb,var(--team) 19%,#101a2a) 100%)}.portrait{position:absolute;right:7px;bottom:0;height:104px;max-width:40%;object-fit:contain;object-position:center bottom;filter:drop-shadow(0 8px 11px rgba(0,0,0,.42));opacity:.94}.identity{position:relative;z-index:1;max-width:67%}.identity h2{margin:0;color:var(--team);font-size:20px;line-height:1.02}.meta{font-size:11px;color:#b6c6d8;margin-top:6px;font-weight:800}.team{font-size:11px;color:#9bafc5;margin-top:4px}.stat{display:flex;justify-content:space-between;gap:8px;padding:8px 0;border-top:1px solid #26394f;font-size:12px}.stat span{color:#92a7bc}.tyre{display:inline-flex;align-items:center;justify-content:center;height:22px;width:22px;border-radius:50%;border:2px solid var(--tyre);color:var(--tyre);font-weight:950}.strategy-mini{display:flex;height:9px;overflow:hidden;border-radius:99px;background:#08101a;margin:9px 0 2px;gap:2px}.strategy-mini i{display:block;background:var(--tyre);min-width:4px}.strategy-label{font-size:10px;color:#95abc1;margin-bottom:7px}.change-up{color:#7fe4aa}.change-down{color:#ff7683}.controls,.strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn,.pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#edf6ff;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.pilot{border-left:4px solid var(--team);font-size:11px;transition:background .15s,box-shadow .15s}.pilot.active{box-shadow:0 0 0 1px var(--team) inset;background:#1c3049}.slider{accent-color:#ff4051;flex:1;min-width:135px}.clock{font:900 12px ui-monospace,Consolas,monospace}.note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.layout{grid-template-columns:1fr}.map canvas{height:395px}}
    </style><div class="hud"><div class="top"><div><div class="title">RACE CONTROL // 2026 RACE REPLAY</div><div class="sub" id="sub">Doğrulanmış tur zamanı • telemetri pisti • akıcı konum yeniden kurulumu</div></div><div class="badge">● GERÇEK ZAMAN ÖLÇEĞİ</div></div><div class="layout"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn active" data-speed="1">1× Gerçek</button><button class="btn" data-speed="5">5×</button><button class="btn" data-speed="20">20×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="clock" id="clock">00:00</span></div><div class="strip" id="strip"></div><div class="note">Bir araca veya alttaki pilot kartına bas: sağdaki yarış HUD’u o pilota geçer.</div></div><aside class="panel" id="panel"></aside></div></div>
    <script>
      const data=__PREMIUM_RACE_PAYLOAD__,cars=data.cars||[],route=data.track||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d'),tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};let selected=cars[0]?.code||'',playing=true,speed=1,time=0,last=performance.now(),lastHud=0,lastStripKey='';
      const raceOverlay=data.overlay||{};
      const replayStyle=document.createElement('style');replayStyle.textContent='.strip{align-content:flex-start}.out-zone{display:none;margin-top:9px;padding:9px;border:1px solid #723442;background:#25131b;border-radius:8px}.out-zone.show{display:block}.out-title{font-size:10px;font-weight:950;color:#ff9aa5;letter-spacing:.09em;margin-bottom:6px}.out-strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.pilot{transition:transform .22s ease,background .15s,box-shadow .15s}.pilot.out{border-left-color:#e84d5b!important;background:#25131b;color:#ffc2c8;opacity:.88}';document.head.appendChild(replayStyle);
      function fmt(n){n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')}
      function lap(c,t){return(c.laps||[]).find(x=>t>=x.start&&t<=x.end)||(c.laps||[]).filter(x=>x.start<=t).slice(-1)[0]||null}
      function isRetired(c){return /(retired|accident|disqualified|withdrawn|did not finish|dnf|excluded)/.test(String(c.status||'').toLowerCase())}
      function state(c,t){const l=lap(c,t),retired=isRetired(c),all=c.laps||[],lastEnd=Math.max(0,...all.map(x=>Number(x.end)||0)),completed=all.filter(x=>x.end<=t&&Number.isFinite(x.position)).slice(-1)[0],retiredNow=retired&&t>=lastEnd;if(!l)return{lap:0,frac:0,pos:c.grid||20,out:retiredNow,pit:false,retired:retiredNow};const frac=Math.min(1,Math.max(0,(t-l.start)/(l.end-l.start||1))),pit=!!((l.pit_in&&Math.abs(t-l.pit_in)<15)||(l.pit_out&&Math.abs(t-l.pit_out)<15)),lapPosition=completed?.position||c.grid||20;return{lap:l.lap,frac,pos:lapPosition,out:retiredNow||(t>l.end&&l.lap>=data.total_laps),pit,retired:retiredNow}}
      function point(frac){const n=route.length;if(!n)return{x:0,y:0,angle:0};const p=(frac%1)*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,angle:Math.atan2(b[1]-a[1],b[0]-a[0])}}
      function actualPoint(c,t){const samples=c.points||[];if(samples.length>=2){if(t<=samples[0][0]){const a=samples[0],b=samples[1];return{x:a[1],y:a[2],angle:Math.atan2(b[2]-a[2],b[1]-a[1])}}const lastSample=samples[samples.length-1];if(t>=lastSample[0]){const a=samples[samples.length-2],b=lastSample;return{x:b[1],y:b[2],angle:Math.atan2(b[2]-a[2],b[1]-a[1])}}let low=0,high=samples.length-1;while(low+1<high){const mid=(low+high)>>1;if(samples[mid][0]<=t)low=mid;else high=mid}const a=samples[low],b=samples[high],ratio=Math.max(0,Math.min(1,(t-a[0])/(b[0]-a[0]||1)));return{x:a[1]+(b[1]-a[1])*ratio,y:a[2]+(b[2]-a[2])*ratio,angle:Math.atan2(b[2]-a[2],b[1]-a[1])}}const s=state(c,t),gridOffset=((c.grid||1)-1)*.0018;return point((s.frac-gridOffset+1)%1)}
      function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=32,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}
      function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
      function f1car(x,y,a,primary,accent,code,chosen,out,pitting){ctx.save();ctx.translate(x,y);ctx.rotate(-a);ctx.scale(.78,.78);ctx.globalAlpha=out?.33:1;ctx.fillStyle='rgba(0,0,0,.38)';ctx.beginPath();ctx.ellipse(0,5,19,8,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#05080d';[[-9,-10,6,6],[6,-10,6,6],[-9,4,6,6],[6,4,6,6]].forEach(w=>ctx.fillRect(...w));ctx.fillStyle='#080c12';ctx.fillRect(-15,-5,5,10);ctx.fillRect(12,-7,4,14);ctx.fillStyle=primary;ctx.beginPath();ctx.moveTo(-11,-4);ctx.lineTo(-2,-7);ctx.lineTo(10,-5);ctx.lineTo(15,0);ctx.lineTo(10,5);ctx.lineTo(-2,7);ctx.lineTo(-11,4);ctx.closePath();ctx.fill();ctx.fillStyle=accent;ctx.fillRect(-16,-8,5,16);ctx.fillRect(12,-10,4,20);ctx.fillRect(-4,-1,17,2);ctx.fillStyle='#111927';ctx.beginPath();ctx.ellipse(1,0,5,4,0,0,Math.PI*2);ctx.fill();ctx.strokeStyle=accent;ctx.lineWidth=1.7;ctx.beginPath();ctx.arc(1,0,5,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#f4f7ff';ctx.fillRect(5,-1,5,2);ctx.fillStyle=primary;ctx.fillRect(-20,-9,5,18);ctx.fillStyle=accent;ctx.fillRect(-21,-10,2,20);if(pitting){ctx.strokeStyle='#ffd44b';ctx.lineWidth=2;ctx.strokeRect(-22,-13,40,26)}if(chosen){ctx.strokeStyle='#ffffff';ctx.lineWidth=1.5;ctx.strokeRect(-24,-15,45,30)}ctx.restore();ctx.fillStyle=primary;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-15)}
      function overlayMarker(fraction,label,colour,tr){const q=xy(point(fraction),tr);ctx.fillStyle=colour;ctx.beginPath();ctx.arc(q[0],q[1],3.7,0,Math.PI*2);ctx.fill();ctx.fillStyle='#edf6ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)}
      function overlayZone(start,end,label,colour,tr){ctx.beginPath();for(let i=0;i<=24;i++){const q=xy(point(start+(end-start)*i/24),tr);i?ctx.lineTo(...q):ctx.moveTo(...q)}ctx.strokeStyle=colour;ctx.globalAlpha=.9;ctx.lineWidth=5;ctx.stroke();ctx.globalAlpha=1;overlayMarker(start,label,colour,tr)}
      function drawRaceOverlay(tr){overlayMarker(0,'START / FINISH','#ffffff',tr);(raceOverlay.sectors||[]).forEach(item=>overlayMarker(item.fraction,item.label,item.colour||'#f4d35e',tr));(raceOverlay.pit||[]).forEach(item=>overlayMarker(item.fraction,item.label,'#b79cff',tr));(raceOverlay.straights||[]).forEach((zone,index)=>overlayZone(zone.start,zone.end,index?'OM - Overtake Mode':'SM - Straight Mode',index?'#48c8ff':'#71e6a1',tr))}
      function order(){return cars.slice().sort((a,b)=>{const aa=state(a,time),bb=state(b,time);return aa.pos-bb.pos||(bb.lap+bb.frac)-(aa.lap+aa.frac)})}
      function draw(){const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);if(!route.length)return;const tr=transform();ctx.strokeStyle='#8094ad';ctx.globalAlpha=.72;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},tr);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;drawRaceOverlay(tr);cars.forEach(c=>{const s=state(c,time),p=actualPoint(c,time),q=xy(p,tr);f1car(q[0],q[1],p.angle,c.colour,c.accent||'#fff',c.code,c.code===selected,s.out,s.pit)})}
      function selection(c){selected=c.code;lastStripKey='';refreshHud(true)}
      function createStrip(){const host=document.getElementById('strip');host.innerHTML=cars.map(c=>`<button class="pilot" style="--team:${c.colour}" data-c="${c.code}"></button>`).join('');host.insertAdjacentHTML('afterend','<div class="out-zone" id="out-zone"><div class="out-title">OUT / DNF</div><div class="out-strip" id="out-strip"></div></div>');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>selection(cars.find(c=>c.code===b.dataset.c)))}
      function moveCards(host,list){const before=new Map([...host.children].map(el=>[el.dataset.c,el.getBoundingClientRect()]));list.forEach(c=>host.appendChild(document.querySelector(`.pilot[data-c="${c.code}"]`)));list.forEach(c=>{const el=document.querySelector(`.pilot[data-c="${c.code}"]`),old=before.get(c.code),next=el.getBoundingClientRect();if(old&&(old.left!==next.left||old.top!==next.top)){el.style.transition='none';el.style.transform=`translate(${old.left-next.left}px,${old.top-next.top}px)`;requestAnimationFrame(()=>{el.style.transition='transform .22s ease,background .15s,box-shadow .15s';el.style.transform=''})}})}
      function updateStrip(){const active=order().filter(c=>!state(c,time).out),retired=cars.filter(c=>state(c,time).out).sort((a,b)=>state(b,time).lap-state(a,time).lap),key=active.map(c=>{const s=state(c,time);return c.code+':'+s.pos+':'+s.lap}).join('|')+'|'+retired.map(c=>c.code).join('|')+'|'+selected;if(key===lastStripKey)return;lastStripKey=key;const host=document.getElementById('strip'),outHost=document.getElementById('out-strip'),zone=document.getElementById('out-zone');moveCards(host,active);retired.forEach(c=>outHost.appendChild(document.querySelector(`.pilot[data-c="${c.code}"]`)));zone.classList.toggle('show',retired.length>0);cars.forEach(c=>{const b=document.querySelector(`.pilot[data-c="${c.code}"]`),s=state(c,time);if(s.out){b.textContent=`OUT · ${c.code} · T${s.lap}`;b.classList.add('out')}else{const rank=active.findIndex(x=>x.code===c.code)+1;b.textContent=`P${rank} · ${c.code} · T${s.lap}`;b.classList.remove('out')}b.classList.toggle('active',c.code===selected)})}
      function miniStrategy(c,currentLap){const laps=(c.laps||[]).filter(l=>l.lap<=Math.max(1,currentLap)),total=Math.max(1,data.total_laps||1),groups=[];laps.forEach(l=>{if(groups.length&&groups.at(-1).compound===l.compound&&l.lap===groups.at(-1).end+1)groups.at(-1).end=l.lap;else groups.push({compound:l.compound,start:l.lap,end:l.lap})});return groups.map(g=>`<i title="${g.compound} ${g.start}–${g.end}" style="--tyre:${tyres[g.compound]||'#718198'};width:${Math.max(3,(g.end-g.start+1)/total*100)}%"></i>`).join('')}
      function lastPit(c,currentTime){const found=(c.laps||[]).filter(l=>(l.pit_in&&l.pit_in<=currentTime)||(l.pit_out&&l.pit_out<=currentTime)).slice(-1)[0];return found?'Tur '+found.lap:'Henüz yok'}
      function panel(){const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),p=c.profile||{},compound=(l?.compound||'—').toUpperCase(),tc=tyres[compound]||'#8292a7',gain=(c.grid&&s.pos)?c.grid-s.pos:0,change=gain>0?`↑ ${gain} SIRA`:gain<0?`↓ ${Math.abs(gain)} SIRA`:'→ DEĞİŞMEDİ',visibleEnd=Math.max(1,s.lap);document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').innerHTML=`<div class="hero" style="--team:${c.colour}"><img class="portrait" src="${p.photo||''}" alt="" onerror="this.style.display='none'"><div class="identity"><h2>${p.name||c.code}</h2><div class="meta">${p.number||'—'} · <img src="https://flagcdn.com/w40/${p.flag||'un'}.png" style="height:11px;vertical-align:middle;border-radius:1px"> ${c.code} · ${p.age||'—'} yaş</div><div class="team">${c.team}</div></div></div><div class="stat"><span>Anlık sıra</span><b>P${s.pos}</b></div><div class="stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="stat"><span>Başlangıç → bitiş</span><b>P${c.grid||'—'} → P${c.final_position||'—'}</b></div><div class="stat"><span>Şu ana kadarki değişim</span><b class="${gain>0?'change-up':gain<0?'change-down':''}">${change}</b></div><div class="stat"><span>Stint / lastik</span><b>${l?.stint||'—'} · <i class="tyre" style="--tyre:${tc}">${compound.slice(0,1)}</i> ${compound}</b></div><div class="stat"><span>Son pit</span><b>${lastPit(c,time)}</b></div><div class="stat"><span>Pit durumu</span><b style="color:${s.pit?'#ffd46b':'#81e6ac'}">${s.pit?'PIT AKIŞI':'PİSTTE'}</b></div><div class="strategy-mini">${miniStrategy(c,visibleEnd)}</div><div class="strategy-label">${c.code} • tur 1–${visibleEnd} lastik akışı</div><div class="note">Lastik şeridi yarış ilerledikçe dolar; pit sonrası yeni hamur otomatik eklenir.</div>`}
      function refreshHud(force=false){const now=performance.now();if(force||now-lastHud>380){lastHud=now;document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds);updateStrip();panel()}}
      function loop(now){const dt=Math.min(.025,Math.max(0,(now-last)/1000));last=now;if(playing){time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan oynat'}}draw();refreshHud();requestAnimationFrame(loop)}
      function resize(){const b=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=b.width*d;canvas.height=b.height*d;ctx.setTransform(d,0,0,d,0,0);draw();refreshHud(true)}canvas.onclick=e=>{const tr=transform(),r=canvas.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let found=null,best=30;cars.forEach(c=>{const p=actualPoint(c,time),q=xy(p,tr),distance=Math.hypot(q[0]-mx,q[1]-my);if(distance<best){best=distance;found=c}});if(found)selection(found)};document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{time=(Number(e.target.value)/1000)*data.total_seconds;lastStripKey='';draw();refreshHud(true)};document.getElementById('sub').textContent=(data.event||'Formula 1')+' • '+data.total_laps+' tur • doğrulanmış tur, sıra, pit ve lastik akışı';window.addEventListener('resize',resize);createStrip();resize();requestAnimationFrame(loop);
    </script>""".replace('__PREMIUM_RACE_PAYLOAD__', packed)


@st.cache_data(ttl=86400, show_spinner=False)
def build_verified_race_replay_payload(year, event_name):
    """Alpha 0.6: tek saat, tek temiz pist ve kesintisiz tur zaman çizelgesi.

    FastF1'in ham PositionData akışı yarıştan yarışa farklı başlangıç anları
    içerebilir. Bu nedenle bu yeniden oynatma motoru ham GPS'i kullanmaz;
    resmi tur sürelerinden kesintisiz bir saat üretir ve bütün araçları aynı
    temiz telemetri pistinde hareket ettirir. Böylece araçlar pist dışına
    fırlamaz veya ilk turda donmuş kalmaz.
    """
    try:
        session = fastf1.get_session(int(year), event_name, 'R')
        session.load(telemetry=True, weather=False, messages=False)
        if session.results is None or session.results.empty or session.laps is None or session.laps.empty:
            return {'ok': False, 'reason': 'Bu yarışın doğrulanmış tur verisi henüz hazır değil.'}

        reference_lap = session.laps.pick_fastest()
        if reference_lap is None:
            return {'ok': False, 'reason': 'Pist çizimi için temiz bir referans tur bulunamadı.'}
        telemetry = reference_lap.get_telemetry()
        source = telemetry[[key for key in ('Distance', 'X', 'Y') if key in telemetry.columns]].dropna().copy()
        if not {'Distance', 'X', 'Y'}.issubset(source.columns):
            return {'ok': False, 'reason': 'Referans turda gerekli pist koordinatları yok.'}
        source = source.apply(pd.to_numeric, errors='coerce').dropna().sort_values('Distance').drop_duplicates('Distance')
        if len(source) < 40:
            return {'ok': False, 'reason': 'Pist çizimi için yeterli temiz telemetri noktası yok.'}
        distances = np.linspace(float(source['Distance'].min()), float(source['Distance'].max()), 720)
        track = [[round(float(np.interp(value, source['Distance'], source['X'])), 1), round(float(np.interp(value, source['Distance'], source['Y'])), 1)] for value in distances]

        prepared, total_laps = [], 0
        for _, result in session.results.iterrows():
            code = str(result.get('Abbreviation', '')).strip()
            if not code or code.lower() == 'nan':
                continue
            raw_laps = []
            for _, lap in session.laps.pick_drivers(code).sort_values('LapNumber').iterrows():
                number = _race_int(lap.get('LapNumber'))
                duration = _timedelta_seconds(lap.get('LapTime'))
                if number is None or duration is None or duration <= 0:
                    continue
                raw_laps.append({
                    'lap': int(number), 'duration': float(duration),
                    'position': _race_position(lap.get('Position')),
                    'compound': str(lap.get('Compound', '')).upper(),
                    'stint': int(lap.get('Stint', 0)) if pd.notna(lap.get('Stint')) else 0,
                    'pit_in': _timedelta_seconds(lap.get('PitInTime')),
                    'pit_out': _timedelta_seconds(lap.get('PitOutTime')),
                })
            if not raw_laps:
                continue
            total_laps = max(total_laps, raw_laps[-1]['lap'])
            team = str(result.get('TeamName', 'Takım'))
            prepared.append({'code': code, 'team': team, 'result': result, 'raw_laps': raw_laps})

        if not prepared:
            return {'ok': False, 'reason': 'Bu yarış için geçerli tur geçmişi bulunamadı.'}

        # Her sürücünün saatini kendi ardışık tur sürelerinden kuruyoruz.
        # Ham zaman damgaları burada bilerek kullanılmaz: Macaristan gibi
        # seanslarda bu damgalar aynı yarış başlangıcını göstermeyebiliyor.
        cars = []
        for item in prepared:
            elapsed, timeline = 0.0, []
            previous_position = _race_position(item['result'].get('GridPosition')) or 20
            for raw in item['raw_laps']:
                start, end = elapsed, elapsed + max(0.1, raw['duration'])
                position = raw['position'] or previous_position
                timeline.append({
                    'lap': raw['lap'], 'start': round(start, 3), 'end': round(end, 3),
                    'position': position, 'start_position': previous_position,
                    'compound': raw['compound'], 'stint': raw['stint'],
                    # Pit zamanları, tur içi mutlak saat değil; güvenli görsel pencere olarak saklanır.
                    'pit_in': round(end - min(10.0, raw['duration'] * .12), 3) if raw['pit_in'] is not None else None,
                    'pit_out': round(start + min(10.0, raw['duration'] * .12), 3) if raw['pit_out'] is not None else None,
                })
                elapsed, previous_position = end, position
            result = item['result']
            cars.append({
                'code': item['code'], 'team': item['team'], 'colour': team_colour(item['team']),
                'accent': TEAM_LIVERY_ACCENTS.get(item['team'], '#f2f7ff'),
                'profile': race_driver_profile(item['code'], item['team']),
                'grid': _race_position(result.get('GridPosition')), 'final_position': _race_position(result.get('Position')),
                'status': str(result.get('Status', 'Finished')), 'laps': timeline,
            })
        cars.sort(key=lambda car: car['final_position'] if car['final_position'] is not None else 99)
        validated_seconds = round(max(lap['end'] for car in cars for lap in car['laps']), 2)
        valid, reason = validate_stable_replay_payload({
            'cars': cars,
            'track': track,
            'total_seconds': validated_seconds,
        })
        if not valid:
            return {'ok': False, 'reason': reason}
        total_seconds = max(lap['end'] for car in cars for lap in car['laps'])
        return {
            'ok': True, 'event': str(session.event.get('EventName', event_name)), 'track': track,
            'overlay': build_track_overlay(telemetry, reference_lap, session), 'cars': cars,
            'total_laps': total_laps, 'total_seconds': round(total_seconds, 2),
            'replay_source': 'FastF1 doğrulanmış tur, sıra, pit ve lastik verisinden yeniden kurulan yarış akışı',
            'alpha': '0.6',
        }
    except Exception as error:
        log_data_error('alpha 0.6 race replay', error)
        return {'ok': False, 'reason': f'Yarış tekrar paketi hazırlanamadı: {error}'}


def premium_race_replay_html(payload):
    """Alpha 0.6 ortak DIV HUD: sadece canvas çizimi her karede güncellenir."""
    packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return r'''<!doctype html><html><head><meta charset="utf-8"><style>
    *{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.f1-hud{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#101a2b,#09101a)}.f1-hud__top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.f1-hud__title{font-weight:950;font-size:14px;letter-spacing:.1em}.f1-hud__sub{font-size:11px;color:#91a8c0;margin-top:5px}.f1-hud__badge{border:1px solid #365170;background:#122239;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.f1-hud__grid{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px;margin-top:12px}.f1-hud__track{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#070c13 74%);overflow:hidden}.f1-hud__track canvas{width:100%;height:500px;display:block}.f1-hud__panel{border:1px solid #2c425d;border-radius:11px;background:#101a2a;padding:12px;overflow:hidden}.f1-hud__hero{position:relative;min-height:101px;border-bottom:1px solid #2b4058;margin:-12px -12px 11px;padding:13px;overflow:hidden;background:linear-gradient(110deg,#101a2a 0%,color-mix(in srgb,var(--team) 18%,#101a2a) 100%)}.f1-hud__portrait{position:absolute;right:8px;bottom:0;height:88px;max-width:34%;object-fit:contain;object-position:center bottom;filter:drop-shadow(0 8px 11px rgba(0,0,0,.42));opacity:.96}.f1-hud__identity{position:relative;z-index:1;max-width:70%}.f1-hud__identity h2{margin:0;color:var(--team);font-size:20px;line-height:1.02}.f1-hud__meta{font-size:11px;color:#b6c6d8;margin-top:6px;font-weight:800}.f1-hud__team{font-size:11px;color:#9bafc5;margin-top:4px}.f1-hud__stat{display:flex;justify-content:space-between;gap:8px;padding:8px 0;border-top:1px solid #26394f;font-size:12px}.f1-hud__stat span{color:#92a7bc}.f1-hud__tyre{display:inline-flex;align-items:center;justify-content:center;height:22px;width:22px;border-radius:50%;border:2px solid var(--tyre);color:var(--tyre);font-weight:950}.f1-hud__strategy{display:flex;height:9px;overflow:hidden;border-radius:99px;background:#08101a;margin:9px 0 2px;gap:2px}.f1-hud__strategy i{display:block;background:var(--tyre);min-width:4px}.f1-hud__label{font-size:10px;color:#95abc1;margin-bottom:7px}.f1-hud__controls,.f1-hud__strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.f1-hud__button,.f1-hud__pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#edf6ff;font-weight:900;padding:7px 9px;cursor:pointer}.f1-hud__button.is-active{border-color:#ff4757;background:#3b1822}.f1-hud__pilot{border-left:4px solid var(--team);font-size:11px}.f1-hud__pilot.is-active{box-shadow:0 0 0 1px var(--team) inset;background:#1c3049}.f1-hud__slider{accent-color:#ff4051;flex:1;min-width:135px}.f1-hud__clock{font:900 12px ui-monospace,Consolas,monospace}.f1-hud__note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.f1-hud__grid{grid-template-columns:1fr}.f1-hud__track canvas{height:395px}}
    </style></head><body><div class="f1-hud" id="race-hud"><div class="f1-hud__top"><div><div class="f1-hud__title">RACE CONTROL // ALPHA 0.6 REPLAY</div><div class="f1-hud__sub" id="sub"></div></div><div class="f1-hud__badge">● DOĞRULANMIŞ YARIŞ AKIŞI</div></div><div class="f1-hud__grid"><div><div class="f1-hud__track"><canvas id="track"></canvas></div><div class="f1-hud__controls"><button class="f1-hud__button is-active" id="play">❚❚ Duraklat</button><button class="f1-hud__button is-active" data-speed="1">1× Gerçek</button><button class="f1-hud__button" data-speed="5">5×</button><button class="f1-hud__button" data-speed="20">20×</button><input id="range" class="f1-hud__slider" type="range" min="0" max="1000" value="0"><span class="f1-hud__clock" id="clock"></span></div><div class="f1-hud__strip" id="strip"></div><div class="f1-hud__note">Araçların konumu ham canlı GPS değildir: doğrulanmış tur sürelerinden, tek gerçek pist yörüngesinde akıcı biçimde yeniden kurulmuştur.</div></div><aside class="f1-hud__panel" id="panel"></aside></div></div><script>
    const data=__PAYLOAD__,cars=data.cars||[],route=data.track||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d'),tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};let selected=cars[0]?.code||'',playing=true,speed=1,time=0,last=performance.now(),lastHud=0,lastStrip='';
    const fmt=n=>{n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')};
    function lap(c,t){const a=c.laps||[];for(let i=0;i<a.length;i++)if(t<=a[i].end)return a[i];return a[a.length-1]||null}
    function state(c,t){const l=lap(c,t),all=c.laps||[];if(!l)return{lap:0,frac:0,pos:c.grid||20,pit:false};const frac=Math.max(0,Math.min(1,(t-l.start)/(l.end-l.start||1))),previous=all[Math.max(0,all.indexOf(l)-1)]?.position||l.start_position||c.grid||20,pos=frac>.985?(l.position||previous):previous,pit=!!((l.pit_in&&Math.abs(t-l.pit_in)<8)||(l.pit_out&&Math.abs(t-l.pit_out)<8));return{lap:l.lap,frac,pos,pit}}
    function point(frac){const n=route.length;if(!n)return{x:0,y:0,angle:0};const p=((frac%1)+1)%1*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,angle:Math.atan2(b[1]-a[1],b[0]-a[0])}}
    function visualPoint(c,t){const s=state(c,t),launch=Math.max(0,1-Math.min(1,t/5)),grid=((c.grid||1)-1)*.0016*launch,spacing=((s.pos||1)-1)*.0009;return point(s.frac-grid-spacing)}
    function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=32,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}
    function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
    function car(x,y,a,primary,accent,code,chosen,pitting){ctx.save();ctx.translate(x,y);ctx.rotate(-a);ctx.fillStyle='#070b10';ctx.fillRect(-18,-8,5,16);ctx.fillRect(13,-9,4,18);ctx.fillStyle=primary;ctx.fillRect(-12,-5,25,10);ctx.fillRect(9,-3,10,6);ctx.fillRect(15,-10,4,20);ctx.fillStyle=accent;ctx.fillRect(-19,-10,2,20);ctx.fillRect(-4,-1,18,2);ctx.fillStyle='#111927';ctx.beginPath();ctx.ellipse(1,0,5,4,0,0,Math.PI*2);ctx.fill();if(pitting){ctx.strokeStyle='#ffd44b';ctx.lineWidth=2;ctx.strokeRect(-22,-13,42,26)}if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.strokeRect(-24,-15,46,30)}ctx.restore();ctx.fillStyle=primary;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-16)}
    function marker(f,label,color,tr){const q=xy(point(f),tr);ctx.fillStyle=color;ctx.beginPath();ctx.arc(q[0],q[1],3.7,0,Math.PI*2);ctx.fill();ctx.fillStyle='#edf6ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)}
    function overlay(tr){const o=data.overlay||{};marker(0,'START / FINISH','#fff',tr);(o.sectors||[]).forEach(x=>marker(x.fraction,x.label,x.colour||'#f4d35e',tr));(o.pit||[]).forEach(x=>marker(x.fraction,x.label,'#b79cff',tr));(o.straights||[]).forEach((z,i)=>{ctx.beginPath();for(let j=0;j<=20;j++){const q=xy(point(z.start+(z.end-z.start)*j/20),tr);j?ctx.lineTo(...q):ctx.moveTo(...q)}ctx.strokeStyle=i?'#48c8ff':'#71e6a1';ctx.lineWidth=5;ctx.stroke()})}
    function order(){return cars.slice().sort((a,b)=>{const x=state(a,time),y=state(b,time);return x.pos-y.pos||(y.lap+y.frac)-(x.lap+x.frac)})}
    function draw(){if(!route.length)return;const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const tr=transform();ctx.strokeStyle='#8094ad';ctx.globalAlpha=.72;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},tr);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;overlay(tr);cars.forEach(c=>{const s=state(c,time),p=visualPoint(c,time),q=xy(p,tr);car(q[0],q[1],p.angle,c.colour,c.accent||'#fff',c.code,c.code===selected,s.pit)})}
    function miniStrategy(c,current){const groups=[];(c.laps||[]).filter(l=>l.lap<=Math.max(1,current)).forEach(l=>{const last=groups.at(-1);if(last&&last.compound===l.compound&&l.lap===last.end+1)last.end=l.lap;else groups.push({compound:l.compound,start:l.lap,end:l.lap})});return groups.map(g=>`<i style="--tyre:${tyres[g.compound]||'#718198'};width:${Math.max(3,(g.end-g.start+1)/Math.max(1,data.total_laps)*100)}%"></i>`).join('')}
    function renderPanel(){const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),p=c.profile||{},compound=(l?.compound||'—').toUpperCase(),tc=tyres[compound]||'#8292a7',gain=(c.grid&&s.pos)?c.grid-s.pos:0,move=gain>0?`↑ ${gain} SIRA`:gain<0?`↓ ${Math.abs(gain)} SIRA`:'→ DEĞİŞMEDİ';const host=document.getElementById('panel');host.style.setProperty('--team',c.colour);host.innerHTML=`<div class="f1-hud__hero"><img class="f1-hud__portrait" src="${p.photo||''}" alt="" onerror="this.remove()"><div class="f1-hud__identity"><h2>${p.name||c.code}</h2><div class="f1-hud__meta">${p.number||'—'} · ${c.code} · ${p.age||'—'} yaş</div><div class="f1-hud__team">${c.team}</div></div></div><div class="f1-hud__stat"><span>Anlık sıra</span><b>P${s.pos}</b></div><div class="f1-hud__stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="f1-hud__stat"><span>Başlangıç → bitiş</span><b>P${c.grid||'—'} → P${c.final_position||'—'}</b></div><div class="f1-hud__stat"><span>Şu ana kadarki değişim</span><b>${move}</b></div><div class="f1-hud__stat"><span>Stint / lastik</span><b>${l?.stint||'—'} · <i class="f1-hud__tyre" style="--tyre:${tc}">${compound.slice(0,1)}</i> ${compound}</b></div><div class="f1-hud__strategy">${miniStrategy(c,s.lap)}</div><div class="f1-hud__label">${c.code} · tur 1–${Math.max(1,s.lap)} lastik akışı</div><div class="f1-hud__note">Sıralar resmi tur sonu verisinden gelir; pist üstündeki hareket bu zamanlara bağlı, akıcı bir yeniden kurulumdur.</div>`}
    function updateStrip(){const list=order(),key=list.map(c=>{const s=state(c,time);return c.code+s.pos+s.lap}).join('|')+selected;if(key===lastStrip)return;lastStrip=key;document.getElementById('strip').innerHTML=list.map(c=>{const s=state(c,time);return`<button class="f1-hud__pilot ${c.code===selected?'is-active':''}" style="--team:${c.colour}" data-c="${c.code}">P${s.pos} · ${c.code} · T${s.lap}</button>`}).join('');document.querySelectorAll('.f1-hud__pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.c;lastStrip='';updateHud(true)})}
    function updateHud(force=false){const now=performance.now();if(force||now-lastHud>300){lastHud=now;document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds);updateStrip();renderPanel()}}
    function loop(now){let dt=Math.min(.035,Math.max(0,(now-last)/1000));last=now;if(document.hidden)dt=0;if(playing){time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan oynat'}}draw();updateHud();requestAnimationFrame(loop)}
    function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);draw();updateHud(true)}
    canvas.onclick=e=>{const tr=transform(),r=canvas.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let winner=null,best=34;cars.forEach(c=>{const q=xy(visualPoint(c,time),tr),d=Math.hypot(q[0]-mx,q[1]-my);if(d<best){best=d;winner=c}});if(winner){selected=winner.code;lastStrip='';updateHud(true)}};document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('is-active',x===b))});document.getElementById('range').oninput=e=>{time=Number(e.target.value)/1000*data.total_seconds;lastStrip='';draw();updateHud(true)};document.getElementById('sub').textContent=(data.event||'Formula 1')+' · '+data.total_laps+' tur · '+(data.replay_source||'doğrulanmış yarış akışı');document.addEventListener('visibilitychange',()=>last=performance.now());window.addEventListener('resize',resize);resize();requestAnimationFrame(loop);
    </script></div></body></html>'''.replace('__PAYLOAD__', packed)


@st.cache_data(ttl=604800, show_spinner=False)
def build_stable_race_replay_payload(year, event_name):
    """Tek ortak SessionTime saatiyle doğrulanmış, akıcı yarış tekrar paketi.

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
        distances = np.linspace(float(source['Distance'].min()), float(source['Distance'].max()), 720)
        track = [[round(float(np.interp(value, source['Distance'], source['X'])), 1), round(float(np.interp(value, source['Distance'], source['Y'])), 1)] for value in distances]

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
            finished_like = status.lower() in {'finished', 'lapped'} or status.startswith('+')
            cars.append({'code': item['code'], 'team': item['team'], 'colour': team_colour(item['team']),
                         'accent': TEAM_LIVERY_ACCENTS.get(item['team'], '#f2f7ff'),
                         'profile': race_driver_profile(item['code'], item['team']),
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


def stable_race_replay_html(payload):
    """Her karede yalnızca canvas çizer; HUD DOM'u düşük sıklıkta güncellenir."""
    packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return r'''<!doctype html><html><head><meta charset="utf-8"><style>
    *{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#101a2b,#09101a)}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-size:14px;font-weight:950;letter-spacing:.1em}.sub{font-size:11px;color:#91a8c0;margin-top:5px}.badge{border:1px solid #365170;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.grid{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:12px;margin-top:12px}.map{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#070c13 74%);overflow:hidden}.map canvas{width:100%;height:500px;display:block}.panel{border:1px solid #2c425d;border-radius:11px;background:#101a2a;padding:12px}.hero{border-bottom:1px solid #2b4058;padding-bottom:10px;margin-bottom:8px}.hero b{font-size:21px;color:var(--team)}.hero small{display:block;color:#a9bbcd;margin-top:5px}.stat{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #26394f;font-size:12px}.stat span{color:#92a7bc}.pit{color:#ffd46b}.on{color:#81e6ac}.controls,.strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn,.pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#edf6ff;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.pilot{border-left:4px solid var(--team);font-size:11px}.pilot.active{background:#1c3049;box-shadow:0 0 0 1px var(--team) inset}.slider{accent-color:#ff4051;flex:1;min-width:135px}.clock{font:900 12px ui-monospace,Consolas,monospace}.note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.grid{grid-template-columns:1fr}.map canvas{height:390px}}
    </style></head><body><div class="hud"><div class="top"><div><div class="title">RACE CONTROL // STABLE REPLAY</div><div class="sub" id="sub"></div></div><div class="badge">● VERIFIED RACE FLOW</div></div><div class="grid"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn active" data-speed="1">1× Gerçek</button><button class="btn" data-speed="5">5×</button><button class="btn" data-speed="20">20×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="clock" id="clock"></span></div><div class="strip" id="strip"></div><div class="note">Araçlar ortak yarış saatine göre ilerler. Pit zamanları doğrulanmış giriş/çıkış kayıtlarıdır; pit şeridi görsel olarak şematiktir.</div></div><aside class="panel" id="panel"></aside></div></div><script>
    const data=__PAYLOAD__,cars=data.cars||[],route=data.track||[],canvas=document.getElementById('track'),ctx=canvas.getContext('2d');let selected=cars[0]?.code||'',playing=true,speed=1,time=0,last=performance.now(),lastHud=0,lastKey='',view=null;const tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};
    const fmt=n=>{n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')};
    function lap(c,t){const a=c.laps||[];for(let i=0;i<a.length;i++)if(t<=a[i].end)return a[i];return a[a.length-1]||null}function pitEvent(c,t){return(c.pit_events||[]).find(e=>t>=e.start&&t<=e.end)||null}function state(c,t){const l=lap(c,t),a=c.laps||[],last=a[a.length-1],out=!!c.retired&&t>=(last?.end||0);if(!l)return{lap:0,frac:0,pos:c.grid||20,pit:false,out};const i=a.indexOf(l),previous=a[Math.max(0,i-1)]?.position||l.start_position||c.grid||20,frac=Math.max(0,Math.min(1,(t-l.start)/(l.end-l.start||1)));return{lap:l.lap,frac,pos:frac>.997?(l.position||previous):previous,pit:!out&&!!pitEvent(c,t),out}}
    function point(f){const n=route.length;if(!n)return{x:0,y:0,a:0};const p=((f%1)+1)%1*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,a:Math.atan2(b[1]-a[1],b[0]-a[0])}}function visual(c,t){const s=state(c,t),start=Math.max(0,1-Math.min(1,t/4)),grid=((c.grid||1)-1)*.0013*start;return point(s.frac-grid)}
    function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=30,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
    function car(x,y,a,c,code,chosen,pit){ctx.save();ctx.translate(x,y);ctx.rotate(-a);ctx.fillStyle='#050a10';ctx.fillRect(-12,-7,5,14);ctx.fillRect(10,-8,4,16);ctx.fillStyle=c;ctx.fillRect(-8,-4,21,8);ctx.fillRect(8,-2,9,4);ctx.fillRect(13,-8,3,16);ctx.fillStyle='#f4f7ff';ctx.fillRect(-16,-9,3,18);ctx.fillRect(0,-1,8,2);if(pit){ctx.strokeStyle='#ffd44b';ctx.lineWidth=2;ctx.strokeRect(-19,-12,39,24)}if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.3;ctx.strokeRect(-21,-14,43,28)}ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-15)}
    function draw(){if(!view||!route.length)return;ctx.clearRect(0,0,view.w,view.h);ctx.strokeStyle='#8094ad';ctx.globalAlpha=.72;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},view);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;cars.forEach(c=>{const s=state(c,time);if(s.out)return;const p=visual(c,time),q=xy(p,view);if(s.pit){q[0]+=18;q[1]+=18;ctx.fillStyle='#ffd44b';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText('PIT',q[0]+9,q[1]+12)}car(q[0],q[1],p.a,c.colour,c.code,c.code===selected,s.pit)})}
    function order(){return cars.filter(c=>!state(c,time).out).sort((a,b)=>{const x=state(a,time),y=state(b,time);return x.pos-y.pos||(y.lap+y.frac)-(x.lap+x.frac)})}function lastPit(c){const e=(c.pit_events||[]).filter(x=>x.end<=time).at(-1);return e?'Tur '+e.lap:'Henüz yok'}
    function update(){const now=performance.now();if(now-lastHud<260)return;lastHud=now;const list=order(),key=list.map(c=>c.code+state(c,time).pos+state(c,time).lap).join('|')+selected;if(key!==lastKey){lastKey=key;document.getElementById('strip').innerHTML=list.map(c=>{const s=state(c,time);return`<button class="pilot ${c.code===selected?'active':''}" style="--team:${c.colour}" data-c="${c.code}">P${s.pos} · ${c.code} · T${s.lap}</button>`}).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.c;lastKey='';update()})}const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),compound=(l?.compound||'—').toUpperCase(),p=pitEvent(c,time),move=(c.grid&&s.pos)?c.grid-s.pos:0;document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').innerHTML=`<div class="hero"><b>${c.code} · P${s.pos}</b><small>${c.team}</small></div><div class="stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="stat"><span>Başlangıç → bitiş</span><b>P${c.grid||'—'} → P${c.final_position||'—'}</b></div><div class="stat"><span>Pozisyon değişimi</span><b>${move>0?'↑ '+move:move<0?'↓ '+Math.abs(move):'→ 0'} sıra</b></div><div class="stat"><span>Stint / lastik</span><b>${l?.stint||'—'} · ${compound}</b></div><div class="stat"><span>Son pit</span><b>${lastPit(c)}</b></div><div class="stat"><span>Pit durumu</span><b class="${p?'pit':'on'}">${p?'PIT LANE':'PİSTTE'}</b></div>`;document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds)}
    function frame(now){let dt=Math.min(.04,Math.max(0,(now-last)/1000));last=now;if(playing){time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan'}}draw();update();requestAnimationFrame(frame)}function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);view=transform();draw();lastHud=0;update()}document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{time=Number(e.target.value)/1000*data.total_seconds;lastHud=0;draw();update()};document.getElementById('sub').textContent=(data.event||'Formula 1')+' · '+data.total_laps+' tur · ortak doğrulanmış yarış saati';window.addEventListener('resize',resize);resize();requestAnimationFrame(frame);
    </script></div></body></html>'''.replace('__PAYLOAD__',packed)



# Shared replay HUD: portrait, tyre history, pits and track-mode overlays.
def stable_race_replay_html(payload):
    return premium_race_replay_html(payload)

def strategy_wall_html(payload):
    """Stint tablosunu yarış mühendisliği strateji duvarı HUD'una dönüştürür."""
    total = max(1, int(payload.get('total_laps', 1)))
    tyre = {'SOFT': '#ef3340', 'MEDIUM': '#ffd23f', 'HARD': '#eef2f7', 'INTERMEDIATE': '#36c96a', 'WET': '#39a9ff'}
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
        rows.append(f"<div class='row' style='--team:{car['colour']}'><div class='driver'>{html_lib.escape(car['code'])}<small>{html_lib.escape(car['team'])}</small></div><div class='stints'>{blocks}</div><div class='finish'>P{car['final_position'] or '—'}<small>{len(groups)-1} PIT</small></div></div>")
    return f"""<style>body{{margin:0;background:#090d14;color:#eef6ff;font-family:Inter,Segoe UI,Arial,sans-serif}}.wall{{border:1px solid #2c425c;border-radius:13px;background:#101a2a;overflow:hidden}}.head{{padding:13px 15px;border-bottom:1px solid #2b4058;font-size:13px;font-weight:950;letter-spacing:.08em}}.sub{{font-size:10px;color:#91a8bf;margin-top:5px}}.row{{display:grid;grid-template-columns:110px 1fr 58px;gap:10px;align-items:center;min-height:54px;padding:8px 12px;border-top:1px solid #23364b;border-left:4px solid var(--team)}}.driver{{font-weight:950;color:var(--team)}}.driver small,.finish small{{display:block;font-size:10px;color:#8fa6bd;margin-top:4px}}.stints{{display:flex;min-width:380px;height:29px;border-radius:6px;overflow:hidden;background:#0a111b;gap:2px}}.stint{{min-width:20px;display:flex;align-items:center;justify-content:center;gap:5px;background:color-mix(in srgb,var(--tyre) 23%,#101a2a);border-top:3px solid var(--tyre);color:#f6f9ff;font-size:11px;font-weight:950}}.stint small{{font-size:9px;color:#bdcadd}}.finish{{font-weight:950;text-align:right}}@media(max-width:700px){{.row{{grid-template-columns:84px 1fr 42px;padding:8px}}.stints{{min-width:220px}}.stint small{{display:none}}}}</style><div class='wall'><div class='head'>TYRE STRATEGY WALL<div class='sub'>HER BLOK BİR STINT • ÇİZGİLER PIT STOP GEÇİŞLERİNİ GÖSTERİR • TOPLAM {total} TUR</div></div><div class='scroll'>{''.join(rows)}</div></div>"""


def strategy_wall_component_height(payload):
    """Lastik duvarının tüm 20+ pilotunu ana sayfada görünür tutar."""
    return min(1560, max(320, 105 + len(payload.get('cars', [])) * 54))


def position_flow_html(payload):
    """Pilotun tur tur sıra değişimini takım renkli, seçilebilir HUD grafiğine dönüştürür."""
    packed = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return r"""<style>*{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #2c425c;border-radius:13px;background:#101a2a;padding:13px}.head{font-size:13px;font-weight:950;letter-spacing:.08em}.sub{font-size:10px;color:#90a7be;margin-top:5px}.chips{display:flex;gap:6px;flex-wrap:wrap;margin:11px 0}.chip{border:1px solid #36506e;border-left:4px solid var(--team);border-radius:6px;background:#132137;color:#f1f7ff;padding:6px 8px;font-weight:900;font-size:11px;cursor:pointer}.chip.active{background:#20334d;box-shadow:0 0 0 1px var(--team) inset}.layout{display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:12px}.graph{border:1px solid #29405a;border-radius:9px;background:#0b121c}.graph canvas{display:block;width:100%;height:270px}.summary{border:1px solid #2b405a;border-radius:9px;padding:11px;background:#111d2d}.name{font-size:19px;font-weight:950;color:var(--team)}.line{display:flex;justify-content:space-between;border-top:1px solid #293b50;padding:8px 0;font-size:12px}.line span{color:#96aac0}.up{color:#79e5a7}.down{color:#ff7380}@media(max-width:720px){.layout{grid-template-columns:1fr}}</style><div class='hud'><div class='head'>RACE POSITION FLOW</div><div class='sub'>TUR TUR SIRA DEĞİŞİMİ • YUKARI OK POZİSYON KAZANCI, AŞAĞI OK POZİSYON KAYBI</div><div class='chips' id='chips'></div><div class='layout'><div class='graph'><canvas id='chart'></canvas></div><aside class='summary' id='summary'></aside></div></div><script>const data=__POSITION_FLOW_PAYLOAD__,cars=data.cars||[],canvas=document.getElementById('chart'),ctx=canvas.getContext('2d');let chosen=cars[0]?.code||'';function info(c){const a=(c.laps||[]).filter(x=>Number.isFinite(x.position));const start=c.grid||a[0]?.position||'—',finish=c.final_position||a[a.length-1]?.position||'—',values=a.map(x=>x.position),best=values.length?Math.min(...values):'—',worst=values.length?Math.max(...values):'—';return{a,start,finish,best,worst,change:(typeof start==='number'&&typeof finish==='number')?start-finish:0}}function draw(){const c=cars.find(x=>x.code===chosen)||cars[0],d=info(c),w=canvas.clientWidth,h=canvas.clientHeight,p={l:35,r:14,t:16,b:25},laps=Math.max(1,data.total_laps||1),maxP=Math.max(20,...cars.flatMap(x=>x.laps.map(y=>y.position||0)));ctx.clearRect(0,0,w,h);ctx.strokeStyle='#23384f';ctx.fillStyle='#8fa6bd';ctx.font='10px Arial';for(let pos=1;pos<=maxP;pos+=4){const y=p.t+(pos-1)/(maxP-1)*(h-p.t-p.b);ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText('P'+pos,4,y+3)}for(let x=1;x<=laps;x+=Math.max(1,Math.ceil(laps/8))){const px=p.l+(x-1)/(laps-1||1)*(w-p.l-p.r);ctx.fillText(x,px-4,h-7)}if(!d.a.length)return;ctx.strokeStyle=c.colour;ctx.lineWidth=3;ctx.beginPath();d.a.forEach((item,i)=>{const x=p.l+(item.lap-1)/(laps-1||1)*(w-p.l-p.r),y=p.t+(item.position-1)/(maxP-1)*(h-p.t-p.b);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();d.a.forEach(item=>{const x=p.l+(item.lap-1)/(laps-1||1)*(w-p.l-p.r),y=p.t+(item.position-1)/(maxP-1)*(h-p.t-p.b);ctx.fillStyle=c.colour;ctx.beginPath();ctx.arc(x,y,2.5,0,Math.PI*2);ctx.fill()})}function render(){const c=cars.find(x=>x.code===chosen)||cars[0],d=info(c),arrow=d.change>0?'↑ '+d.change+' SIRA':d.change<0?'↓ '+Math.abs(d.change)+' SIRA':'→ DEĞİŞMEDİ';document.getElementById('chips').innerHTML=cars.map(x=>`<button class='chip ${x.code===chosen?'active':''}' style='--team:${x.colour}' data-c='${x.code}'>${x.code}</button>`).join('');document.querySelectorAll('.chip').forEach(b=>b.onclick=()=>{chosen=b.dataset.c;render()});document.getElementById('summary').style.setProperty('--team',c.colour);document.getElementById('summary').innerHTML=`<div class='name'>${c.code}</div><div class='line'><span>Başlangıç</span><b>P${d.start}</b></div><div class='line'><span>En iyi sıra</span><b>P${d.best}</b></div><div class='line'><span>En kötü sıra</span><b>P${d.worst}</b></div><div class='line'><span>Bitiş</span><b>P${d.finish}</b></div><div class='line'><span>Toplam değişim</span><b class='${d.change>0?'up':d.change<0?'down':''}'>${arrow}</b></div>`;resize()}function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);draw()}window.addEventListener('resize',resize);render();</script>""".replace('__POSITION_FLOW_PAYLOAD__', packed)

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
        f"<div><div style='font-size:1.45rem;font-weight:950;color:{team['color']}'>{html_lib.escape(name)} <span style='color:#f7fbff'>{html_lib.escape(number)}</span></div>"
        f"<div class='driver-meta' style='margin-top:5px'>{html_lib.escape(team_name)} · {html_lib.escape(nation)} · {driver_age(code)} yaş</div>"
        f"<div class='history-copy' style='margin-top:7px'>F1 başlangıcı: {html_lib.escape(str(debut))} · Kariyer GP galibiyeti: {html_lib.escape(str(career['wins']))} · Podyum: {html_lib.escape(str(career['podiums']))}</div>"
        f"<div class='history-copy' style='margin-top:7px'>{html_lib.escape(career['bio'])}</div>"
        f"<div style='margin-top:8px;padding:8px 10px;border-left:3px solid {team['color']};background:rgba(15,28,46,.55);font-size:.86rem;line-height:1.45'><b>Öne çıkan an:</b> {html_lib.escape(career['moment'])}</div>"
        f"</div></div></div>",
        unsafe_allow_html=True,
    )



def render_team_personnel_hud(team_name, section='all'):
    """Takim patronunu ve oyun ekibi rollerini sahte kisi atamadan gosterir."""
    team = TEAM_DIRECTORY_2026[team_name]
    leader = TEAM_LEADERSHIP_2026.get(team_name)
    if leader and section in {'all', 'leader'}:
        st.markdown("### Takim yonetimi")
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {team['color']};margin:8px 0 18px;overflow:hidden'>"
            f"<div style='display:flex;gap:16px;align-items:center;flex-wrap:wrap'>"
            f"<img src='{html_lib.escape(leader['photo'], quote=True)}' alt='{html_lib.escape(leader['name'])}' style='width:146px;height:94px;object-fit:cover;object-position:center;border-radius:8px;border:1px solid #31425c' onerror=\"this.style.display='none'\">"
            f"<div style='min-width:220px;flex:1'><div class='hud-label'>2026 TAKIM PATRONU</div><div style='font-size:1.35rem;font-weight:950;color:{team['color']};margin-top:4px'>{html_lib.escape(leader['name'])}</div><div class='driver-meta'>{html_lib.escape(leader['role'])}</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(leader['bio'])}</div></div>"
            f"<div style='display:flex;gap:8px'><div class='mini-stat'><span>STRATEJI</span><b>{leader['strategy']}/5</b></div><div class='mini-stat'><span>GUVENILIRLIK</span><b>{leader['reliability']}/5</b></div></div></div></div>",
            unsafe_allow_html=True,
        )
    if section not in {'all', 'engineers'}:
        return
    st.markdown("### Pit duvari // oyun ekibi")
    st.caption("Takim patronu dogrulanmis kisi/fotografiyla gosterilir. Diger kartlar kariyer oyunundaki rolleridir; gercek yaris muhendisi adi veya fotografi uydurulmaz.")
    columns = st.columns(3)
    for column, (_key, pack) in zip(columns, GAME_ENGINEERING_PACKAGES.items()):
        with column:
            logo = OFFICIAL_TEAM_LOGOS.get(team_name, '')
            st.markdown(
                f"<div class='hud-card' style='min-height:222px;border-top:3px solid {team['color']};overflow:hidden'>"
                f"<div style='height:76px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,{team['color']}22,#0d1625);border-radius:8px'><img src='{html_lib.escape(logo, quote=True)}' alt='{html_lib.escape(team_name)}' style='max-height:58px;max-width:132px;object-fit:contain' onerror=\"this.style.display='none'\"></div>"
                f"<div class='hud-label' style='margin-top:11px'>OYUN EKIBI ROLU</div><div style='font-weight:950;font-size:1.03rem;color:#f7fbff;margin-top:4px'>{html_lib.escape(pack['title'])}</div>"
                f"<div class='driver-meta' style='margin-top:5px'>{html_lib.escape(team_name)} pit duvari</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(pack['description'])}</div>"
                f"<div style='margin-top:10px;color:{team['color']};font-weight:900;font-size:.82rem'>STR {pack['strategy']} | TEMPO {pack['pace']} | GUV {pack['reliability']}</div></div>",
                unsafe_allow_html=True,
            )
def game_driver_pool():
    rows = []
    for team_name, team in TEAM_DIRECTORY_2026.items():
        for name, code, number, image_path in team['drivers']:
            rows.append({'name': name, 'code': code, 'number': number, 'team': team_name, 'image': image_path})
    return rows


DRIVER_GAME_STATS = {
    'VER': (96, 97, 94, 92), 'NOR': (95, 95, 94, 92), 'PIA': (93, 94, 89, 93), 'LEC': (95, 93, 90, 88),
    'HAM': (91, 92, 93, 91), 'RUS': (92, 93, 91, 91), 'ANT': (88, 89, 86, 88), 'HAD': (83, 84, 82, 83),
    'GAS': (85, 86, 87, 88), 'COL': (80, 82, 84, 80), 'LAW': (82, 83, 83, 82), 'LIN': (78, 79, 80, 77),
    'OCO': (84, 85, 86, 86), 'BEA': (85, 84, 82, 81), 'SAI': (88, 89, 88, 90), 'ALB': (87, 88, 90, 88),
    'HUL': (83, 84, 86, 89), 'BOR': (80, 81, 82, 80), 'ALO': (91, 91, 94, 93), 'STR': (78, 80, 81, 79),
    'PER': (84, 85, 86, 90), 'BOT': (83, 84, 86, 88),
}

TEAM_GAME_PACE = {
    'McLaren': 9, 'Mercedes': 8, 'Ferrari': 8, 'Red Bull Racing': 7, 'Aston Martin': 5,
    'Williams': 4, 'Racing Bulls': 3, 'Alpine': 2, 'Haas F1 Team': 2, 'Audi': 2, 'Cadillac': 1,
}

CAREER_ROUNDS = [
    ('Bahrain', 'technical'), ('Monaco', 'qualifying'), ('Silverstone', 'high_speed'), ('Hungaroring', 'tyres'),
    ('Spa', 'mixed'), ('Monza', 'power'), ('Singapore', 'street'), ('Austin', 'mixed'), ('Interlagos', 'wet'), ('Abu Dhabi', 'technical'),
]
F1_GAME_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


def game_driver_score(driver, round_index, player_setup):
    """Deterministic, replayable score used only by the career game."""
    quali, race, wet, tyres = DRIVER_GAME_STATS.get(driver['code'], (75, 75, 75, 75))
    track_name, track_type = CAREER_ROUNDS[round_index]
    base = race * .58 + quali * .24 + tyres * .10 + wet * .08 + TEAM_GAME_PACE.get(driver['team'], 1) * 1.25
    if track_type == 'qualifying':
        base += (quali - 80) * .23
    elif track_type == 'tyres':
        base += (tyres - 80) * .22
    elif track_type == 'wet':
        base += (wet - 80) * .23
    elif track_type == 'high_speed':
        base += (quali - 80) * .12
    if driver['code'] in {player_setup['driver_1'], player_setup['driver_2']}:
        lead = TEAM_LEADERSHIP_2026[player_setup['team']]
        engineer = GAME_ENGINEERING_PACKAGES[player_setup['engineer']]
        base += lead['strategy'] * .45 + lead['reliability'] * .18 + engineer['strategy'] * .32 + engineer['pace'] * .34 + engineer['reliability'] * .18
    seed = (round_index + 1) * 1009 + sum(ord(char) for char in driver['code']) + sum(ord(char) for char in player_setup['team'])
    return base + float(np.random.default_rng(seed).normal(0, 4.2))


def simulate_career_round(state):
    round_index = state['round']
    ranking = sorted(
        game_driver_pool(),
        key=lambda driver: game_driver_score(driver, round_index, state['setup']),
        reverse=True,
    )
    result = []
    for position, driver in enumerate(ranking, start=1):
        points = F1_GAME_POINTS[position - 1] if position <= len(F1_GAME_POINTS) else 0
        result.append({'Rank': position, 'Driver': driver['code'], 'Team': driver['team'], 'Points': points})
    state['results'].append({'round': CAREER_ROUNDS[round_index][0], 'table': result})
    state['round'] += 1


def normalise_career_state(state):
    """Migrates the first game patch's encoded table headers without losing a season."""
    if not state or not state.get('results'):
        return state
    for race in state['results']:
        clean_table = []
        for row in race.get('table', []):
            clean_table.append({
                'Rank': row.get('Rank', row.get('SÄ±ra', row.get('Sıra', '-'))),
                'Driver': row.get('Driver', row.get('Pilot', '-')),
                'Team': row.get('Team', row.get('TakÄ±m', row.get('Takım', '-'))),
                'Points': row.get('Points', row.get('Puan', 0)),
            })
        race['table'] = clean_table
    return state


def render_team_manager_game():
    st.markdown("## Takım Patronu Kariyeri")
    st.caption("2026 grid kadrosuyla oynanan kariyer oyunu. Sonuçlar oyun motoru tarafından üretilir; gerçek yarış sonucu değildir.")
    pool = game_driver_pool()
    code_to_driver = {driver['code']: driver for driver in pool}
    state_key = 'team_manager_career_v1'

    if state_key not in st.session_state:
        st.session_state[state_key] = None
    state = st.session_state[state_key]
    state = normalise_career_state(state)
    st.session_state[state_key] = state

    if state is None:
        team_name = st.selectbox('Takımın', list(TEAM_DIRECTORY_2026.keys()), key='manager_team')
        team = TEAM_DIRECTORY_2026[team_name]
        leader = TEAM_LEADERSHIP_2026[team_name]
        header_left, header_right = st.columns([1, 2])
        with header_left:
            st.markdown(
                f"<div class='hud-card' style='border-left:4px solid {team['color']};overflow:hidden'>"
                f"<img src='{html_lib.escape(leader['photo'], quote=True)}' alt='{html_lib.escape(leader['name'])}' style='width:100%;height:145px;object-fit:cover;border-radius:8px' onerror=\"this.style.display='none'\">"
                f"<div class='hud-label' style='margin-top:9px'>TAKIM PATRONU</div><div style='font-size:1.3rem;font-weight:950;color:{team['color']}'>{html_lib.escape(leader['name'])}</div><div class='driver-meta'>{html_lib.escape(leader['role'])}</div></div>",
                unsafe_allow_html=True,
            )
        with header_right:
            st.markdown("### Pilot kadronu kur")
            available = [driver['code'] for driver in pool]
            display = lambda code: f"{code_to_driver[code]['name']} ({code}) - {code_to_driver[code]['team']}"
            driver_1 = st.selectbox('Birinci pilot', available, format_func=display, key='manager_driver_1')
            driver_2 = st.selectbox('İkinci pilot', [code for code in available if code != driver_1], format_func=display, key='manager_driver_2')
            engineer = st.radio('Oyun mühendisi paketi', list(GAME_ENGINEERING_PACKAGES.keys()), format_func=lambda item: GAME_ENGINEERING_PACKAGES[item]['title'], horizontal=True, key='manager_engineer')
            pack = GAME_ENGINEERING_PACKAGES[engineer]
            st.markdown(f"<div class='hud-card' style='border-left:3px solid {team['color']}'><b>{html_lib.escape(pack['title'])}</b><div class='history-copy' style='margin-top:4px'>{html_lib.escape(pack['description'])}</div></div>", unsafe_allow_html=True)
            if st.button('Kariyer sezonunu başlat', key='manager_start', use_container_width=True):
                st.session_state[state_key] = {'setup': {'team': team_name, 'driver_1': driver_1, 'driver_2': driver_2, 'engineer': engineer}, 'round': 0, 'results': []}
                st.rerun()
        return

    setup = state['setup']
    team = TEAM_DIRECTORY_2026[setup['team']]
    selected = [code_to_driver[setup['driver_1']], code_to_driver[setup['driver_2']]]
    total_points = sum(row['Points'] for race in state['results'] for row in race['table'] if row['Driver'] in {setup['driver_1'], setup['driver_2']})
    st.markdown(f"<div class='hud-card' style='border-top:4px solid {team['color']}'><div class='hud-label'>KARİYER SEZONU</div><div class='hud-value'>{html_lib.escape(setup['team'])} | {total_points} P</div><div class='driver-meta'>{selected[0]['code']} + {selected[1]['code']} | {GAME_ENGINEERING_PACKAGES[setup['engineer']]['title']}</div></div>", unsafe_allow_html=True)
    cards = st.columns(2)
    for column, driver in zip(cards, selected):
        with column:
            portrait = current_driver_portrait(driver['team'], driver['image'])
            st.markdown(f"<div class='hud-card' style='border-left:3px solid {team['color']};display:flex;gap:12px;align-items:center'><img src='{html_lib.escape(portrait, quote=True)}' alt='' style='width:66px;height:88px;object-fit:contain' onerror=\"this.style.display='none'\"><div><b style='font-size:1.1rem'>{html_lib.escape(driver['name'])}</b><div class='driver-meta'>{driver['code']} | QUALI {DRIVER_GAME_STATS.get(driver['code'], (75,))[0]} | RACE {DRIVER_GAME_STATS.get(driver['code'], (0,75))[1]}</div></div></div>", unsafe_allow_html=True)
    if state['round'] < len(CAREER_ROUNDS):
        round_name, track_type = CAREER_ROUNDS[state['round']]
        st.markdown(f"### Sıradaki yarış: {round_name}")
        st.caption(f"Hafta sonu profili: {track_type}. Seçtiğin kadro için bu yarış yalnızca bir kez oluşturulur.")
        if st.button(f'{round_name} simülasyonunu çalıştır', key='manager_next_round', use_container_width=True):
            simulate_career_round(state)
            st.session_state[state_key] = state
            st.rerun()
    else:
        st.success(f"Sezon tamamlandı: {total_points} puan. Yeni bir kadroyla yeni kariyer başlatabilirsin.")
    if state['results']:
        selected_codes = {setup['driver_1'], setup['driver_2']}
        driver_totals = {driver['code']: {'points': 0, 'wins': 0, 'podiums': 0} for driver in pool}
        team_totals = {name: {'points': 0, 'wins': 0, 'podiums': 0} for name in TEAM_DIRECTORY_2026}
        team_totals.setdefault(setup['team'], {'points': 0, 'wins': 0, 'podiums': 0})
        round_history = []

        for race in state['results']:
            player_round = []
            for row in race['table']:
                code = row['Driver']
                rank = int(row['Rank'])
                points = int(row['Points'])
                driver_totals.setdefault(code, {'points': 0, 'wins': 0, 'podiums': 0})
                driver_totals[code]['points'] += points
                driver_totals[code]['wins'] += int(rank == 1)
                driver_totals[code]['podiums'] += int(rank <= 3)

                # Recruited drivers score for the player's selected team.
                # Drivers displaced from that team do not score for it.
                listed_team = row['Team']
                if code in selected_codes:
                    points_team = setup['team']
                elif listed_team == setup['team']:
                    points_team = None
                else:
                    points_team = listed_team
                if points_team:
                    team_totals.setdefault(points_team, {'points': 0, 'wins': 0, 'podiums': 0})
                    team_totals[points_team]['points'] += points
                    team_totals[points_team]['wins'] += int(rank == 1)
                    team_totals[points_team]['podiums'] += int(rank <= 3)
                if code in selected_codes:
                    player_round.append({'code': code, 'rank': rank, 'points': points})
            round_history.append({'round': race['round'], 'drivers': player_round})

        driver_order = sorted(
            driver_totals,
            key=lambda code: (-driver_totals[code]['points'], -driver_totals[code]['wins'], code),
        )
        team_order = sorted(
            team_totals,
            key=lambda name: (-team_totals[name]['points'], -team_totals[name]['wins'], name),
        )
        driver_rank = {code: position for position, code in enumerate(driver_order, start=1)}
        team_rank = {name: position for position, name in enumerate(team_order, start=1)}

        player_driver_cards_list = []
        for code in (setup['driver_1'], setup['driver_2']):
            driver_info = code_to_driver[code]
            driver_portrait = current_driver_portrait(driver_info['team'], driver_info['image'])
            player_driver_cards_list.append(
                f"<div style='flex:1;min-width:260px;background:#0d1625;border:1px solid #314560;border-top:4px solid {team['color']};border-radius:10px;padding:13px;display:flex;gap:13px;align-items:center'>"
                f"<img src='{html_lib.escape(driver_portrait, quote=True)}' alt='{html_lib.escape(driver_info['name'])}' style='width:76px;height:106px;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\">"
                f"<div style='min-width:0;flex:1'><div class='hud-label'>PILOT SEZONU</div><div style='font-size:1.18rem;font-weight:950;color:#f7fbff'>{html_lib.escape(driver_info['name'])}</div><div class='driver-meta'>{html_lib.escape(code)} | Grid sirasi P{driver_rank[code]}</div>"
                f"<div style='display:flex;gap:7px;margin-top:10px;flex-wrap:wrap'><div class='mini-stat'><span>PUAN</span><b>{driver_totals[code]['points']}</b></div><div class='mini-stat'><span>GALIBIYET</span><b>{driver_totals[code]['wins']}</b></div><div class='mini-stat'><span>PODYUM</span><b>{driver_totals[code]['podiums']}</b></div></div></div></div>"
            )
        player_driver_cards = ''.join(player_driver_cards_list)
        player_team = team_totals[setup['team']]
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {team['color']};margin-top:18px'>"
            f"<div class='hud-label'>SEZON BİTİŞ HUD</div><div style='display:flex;gap:12px;flex-wrap:wrap;align-items:stretch'>"
            f"<div style='flex:1;min-width:245px;background:linear-gradient(135deg,{team['color']}22,#0d1625);border:1px solid {team['color']};border-radius:10px;padding:13px'>"
            f"<div class='hud-label'>SENİN TAKIMIN</div><div style='font-size:1.5rem;font-weight:950;color:{team['color']}'>{html_lib.escape(setup['team'])} - P{team_rank[setup['team']]}</div>"
            f"<div class='driver-meta'>Sezon puanı: {player_team['points']} | Galibiyet: {player_team['wins']} | Podyum: {player_team['podiums']}</div></div>"
            f"{player_driver_cards}</div></div>",
            unsafe_allow_html=True,
        )

        latest = state['results'][-1]
        podium = latest['table'][:3]
        podium_cards = []
        for row in podium:
            driver_info = code_to_driver.get(row['Driver'], {'name': row['Driver'], 'team': row['Team'], 'image': ''})
            portrait = current_driver_portrait(driver_info.get('team', row['Team']), driver_info.get('image', '')) if driver_info.get('image') else ''
            podium_cards.append(
                f"<div style='flex:1;min-width:205px;background:#0d1625;border:1px solid #314560;border-top:4px solid {team_color(row['Team'])};border-radius:10px;padding:12px;display:flex;gap:11px;align-items:center'>"
                f"<img src='{html_lib.escape(portrait, quote=True)}' alt='{html_lib.escape(driver_info.get('name', row['Driver']))}' style='width:58px;height:78px;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\">"
                f"<div><div class='hud-label'>P{row['Rank']} | {row['Points']} PUAN</div><div style='font-size:1.1rem;font-weight:950;color:#f7fbff'>{html_lib.escape(driver_info.get('name', row['Driver']))}</div><div class='driver-meta'>{html_lib.escape(row['Team'])}</div></div></div>"
            )
        podium_html = ''.join(podium_cards)
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {team['color']};margin-top:18px'>"
            f"<div class='hud-label'>SON YARIŞ HUD</div><div style='font-size:1.35rem;font-weight:950;margin:5px 0 12px'>{html_lib.escape(latest['round'])}</div>"
            f"<div style='display:flex;gap:10px;flex-wrap:wrap'>{podium_html}</div></div>",
            unsafe_allow_html=True,
        )
        st.markdown(f"### Yarış sonucu: {latest['round']}")
        result_frame = pd.DataFrame(latest['table'])
        with st.expander('Tüm yarış sıralamasını aç', expanded=False):
            st.dataframe(result_frame, use_container_width=True, hide_index=True)

        journey_html = ''.join(
            f"<div style='min-width:145px;flex:1;background:#0d1625;border:1px solid #314560;border-radius:9px;padding:10px'>"
            f"<div class='hud-label'>{html_lib.escape(item['round'])}</div>"
            + ''.join(
                f"<div style='font-weight:900;margin-top:5px'>{entry['code']} <span style='color:{team['color']}'>P{entry['rank']}</span> <span class='driver-meta'>+{entry['points']} P</span></div>"
                for entry in item['drivers']
            )
            + "</div>"
            for item in round_history
        )
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid #7dd3fc;margin-top:18px'><div class='hud-label'>SEZON YOLCULUĞU</div>"
            f"<div class='driver-meta' style='margin:5px 0 12px'>Her yarış, seçtiğin iki pilotun oyun içindeki gerçek sırasını ve puanını gösterir.</div>"
            f"<div style='display:flex;gap:9px;flex-wrap:wrap'>{journey_html}</div></div>",
            unsafe_allow_html=True,
        )
        player_history = []
        for item in round_history:
            for entry in item['drivers']:
                player_history.append({'Round': item['round'], 'Driver': entry['code'], 'Rank': entry['rank'], 'Points': entry['points']})
        with st.expander('Sezon sonuç tablosunu aç', expanded=False):
            st.dataframe(pd.DataFrame(player_history), use_container_width=True, hide_index=True)
        render_manager_season_standings_hud(driver_totals, team_totals, driver_order, team_order, code_to_driver, setup['team'], team['color'])
    if st.button('Reset career', key='manager_reset'):
        st.session_state[state_key] = None
        st.rerun()


MANAGER_RACE_CALENDAR = [
    ('Bahrain', 57, 'Gece yarışı', 'Kum ve sıcaklık lastikleri yorar.'),
    ('Monaco', 78, 'Sokak pisti', 'Sıralama ve temiz hava çok önemlidir.'),
    ('Silverstone', 52, 'Yüksek hızlı', 'Enerji kullanımı ve dengeli tempo öne çıkar.'),
    ('Hungaroring', 70, 'Teknik pist', 'Lastik yönetimi ve undercut belirleyicidir.'),
    ('Spa', 44, 'Değişken hava', 'Yağmur ihtimali stratejiyi değiştirebilir.'),
    ('Monza', 53, 'Düşük yere basma', 'Düzlük hızı ve savunma önemlidir.'),
    ('Singapore', 62, 'Gece sokak yarışı', 'Güvenlik aracı ve lastik aşınması risktir.'),
    ('Austin', 56, 'Karma pist', 'Pilot yeteneği ile araç dengesi birlikte çalışır.'),
    ('Interlagos', 71, 'Kısa tur', 'Hava ve trafik yarışın yönünü değiştirir.'),
    ('Abu Dhabi', 58, 'Sezon finali', 'Her puan kariyer hedefi için önem taşır.'),
]

MANAGER_TYRES = {
    'SOFT': {'letter': 'S', 'color': '#ff385c', 'decay': 1.85, 'pace': -0.33},
    'MEDIUM': {'letter': 'M', 'color': '#f7c948', 'decay': 1.25, 'pace': -0.12},
    'HARD': {'letter': 'H', 'color': '#eef4ff', 'decay': .82, 'pace': .12},
    'INTERMEDIATE': {'letter': 'I', 'color': '#41d27d', 'decay': 1.15, 'pace': .05},
    'WET': {'letter': 'W', 'color': '#50c4ff', 'decay': .95, 'pace': .18},
}

MANAGER_SAVE_PATH = os.path.join(os.path.dirname(__file__), 'manager_career_save.json')


def manager_load_save():
    """Yerel kariyeri güvenli biçimde geri yükler; bozuk kayıt oyunu açmayı engellemez."""
    try:
        if os.path.exists(MANAGER_SAVE_PATH):
            with open(MANAGER_SAVE_PATH, 'r', encoding='utf-8') as handle:
                state = json.load(handle)
                if isinstance(state, dict) and state.get('manager_version') == 2:
                    return state
    except Exception as error:
        log_data_error('manager save load', error)
    return None


def manager_save_state(state):
    try:
        with open(MANAGER_SAVE_PATH, 'w', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
    except Exception as error:
        log_data_error('manager save write', error)


def manager_new_state(team_name, driver_1, driver_2, engineer):
    return {
        'manager_version': 2,
        'season': 2026,
        'team': team_name,
        'drivers': [driver_1, driver_2],
        'engineer': engineer,
        'round': 0,
        'phase': 'garage',
        'race': None,
        'season_results': [],
        'career_history': [],
        'budget': 72,
        'upgrades': {'aero': 0, 'reliability': 0, 'pit': 0},
    }


def manager_driver_rating(code, category='race'):
    quali, race, wet, tyres = DRIVER_GAME_STATS.get(code, (75, 75, 75, 75))
    return {'quali': quali, 'race': race, 'wet': wet, 'tyres': tyres}.get(category, race)


def team_color(team_name):
    """Oyun HUD'u için takım rengini güvenli şekilde döndürür."""
    return TEAM_DIRECTORY_2026.get(str(team_name), {}).get('color', '#94a3b8')


def manager_tyre_visual(compound, life, large=False):
    tyre = MANAGER_TYRES.get(compound, MANAGER_TYRES['MEDIUM'])
    size = 86 if large else 56
    used = max(0, min(100, float(life)))
    return (
        f"<div title='{compound}: %{used:.0f} ömür kaldı' style='width:{size}px;height:{size}px;border-radius:50%;"
        f"background:conic-gradient({tyre['color']} 0 {used}%,#26364d {used}% 100%);padding:6px;box-sizing:border-box;display:inline-flex;align-items:center;justify-content:center'>"
        f"<div style='width:100%;height:100%;border-radius:50%;background:#0a111b;border:2px solid #0d1520;display:flex;align-items:center;justify-content:center;color:{tyre['color']};font-weight:950;font-size:{'1.45rem' if large else '.95rem'}'>{tyre['letter']}</div></div>"
    )


def manager_create_race(state, tyre_one, tyre_two):
    round_name, laps, profile, note = MANAGER_RACE_CALENDAR[state['round'] % len(MANAGER_RACE_CALENDAR)]
    player_tyres = {state['drivers'][0]: tyre_one, state['drivers'][1]: tyre_two}
    cars = {}
    for position, driver in enumerate(game_driver_pool(), start=1):
        code = driver['code']
        compound = player_tyres.get(code, ('MEDIUM', 'HARD', 'SOFT')[position % 3])
        cars[code] = {
            'team': driver['team'], 'position': position, 'time': position * .16,
            'tyre': compound, 'life': 100.0, 'stint': 1, 'pit_stops': 0,
            'pace': 'Dengeli', 'ers': 'Dengeli', 'last_lap': None,
        }
    state['race'] = {
        'name': round_name, 'laps': laps, 'profile': profile, 'note': note, 'lap': 0,
        'cars': cars, 'log': [f'{round_name} için grid hazır. Başlangıç lastikleri kilitlendi.'],
        'pending_pits': {}, 'finished': False,
    }
    state['phase'] = 'race'


def manager_rank_race(race):
    ordered = sorted(race['cars'], key=lambda code: race['cars'][code]['time'])
    for position, code in enumerate(ordered, start=1):
        race['cars'][code]['position'] = position
    return ordered


def manager_advance_race(state, laps=3):
    race = state.get('race')
    if not race or race.get('finished'):
        return
    start_lap = race['lap']
    end_lap = min(race['laps'], start_lap + laps)
    for lap in range(start_lap + 1, end_lap + 1):
        for driver in game_driver_pool():
            code = driver['code']
            car = race['cars'][code]
            tyre = MANAGER_TYRES[car['tyre']]
            rating = manager_driver_rating(code, 'race')
            seed = state['season'] * 100000 + state['round'] * 1000 + lap * 37 + sum(ord(ch) for ch in code)
            variance = float(np.random.default_rng(seed).normal(0, .18))
            pace_delta = {'Atak': -.23, 'Dengeli': 0.0, 'Koru': .20}.get(car['pace'], 0.0)
            ers_delta = {'Saldır': -.12, 'Dengeli': 0.0, 'Şarj Et': .11}.get(car['ers'], 0.0)
            upgrade_delta = 0.0
            if code in state['drivers']:
                upgrade_delta = -state['upgrades']['aero'] * .035
            tyre_penalty = max(0.0, (58 - car['life']) * .014)
            lap_time = 88.8 + (100 - rating) * .052 + tyre['pace'] + tyre_penalty + pace_delta + ers_delta + variance + upgrade_delta
            if code in race['pending_pits']:
                new_tyre = race['pending_pits'].pop(code)
                car['tyre'] = new_tyre
                car['life'] = 100.0
                car['stint'] += 1
                car['pit_stops'] += 1
                lap_time += max(17.2, 20.8 - state['upgrades']['pit'] * .7)
                race['log'].append(f'Tur {lap}: {code} pitte {new_tyre} lastiğe geçti.')
            car['time'] += lap_time
            car['last_lap'] = lap_time
            car['life'] = max(0.0, car['life'] - tyre['decay'] * (1.22 if car['pace'] == 'Atak' else .86 if car['pace'] == 'Koru' else 1.0))
        race['lap'] = lap
        manager_rank_race(race)
    if race['lap'] >= race['laps']:
        race['finished'] = True
        ordered = manager_rank_race(race)
        result = []
        for position, code in enumerate(ordered, start=1):
            result.append({'Sıra': position, 'Pilot': code, 'Takım': race['cars'][code]['team'], 'Puan': F1_GAME_POINTS[position - 1] if position <= len(F1_GAME_POINTS) else 0})
        state['season_results'].append({'yarış': race['name'], 'sonuç': result})
        state['log_message'] = f"{race['name']} tamamlandı. Sonuçlar sezon puanına işlendi."


def manager_event_name(round_name):
    '''Kariyer pistini, FastF1 takvimindeki güvenli etkinlik adına çevirir.'''
    names = {
        'Bahrain': 'Bahrain Grand Prix',
        'Monaco': 'Monaco Grand Prix',
        'Silverstone': 'British Grand Prix',
        'Hungaroring': 'Hungarian Grand Prix',
        'Spa': 'Belgian Grand Prix',
        'Monza': 'Italian Grand Prix',
        'Singapore': 'Singapore Grand Prix',
        'Austin': 'United States Grand Prix',
        'Interlagos': 'São Paulo Grand Prix',
        'Abu Dhabi': 'Abu Dhabi Grand Prix',
    }
    return names.get(str(round_name), str(round_name))


def manager_game_track_payload(race, state):
    '''Oyunda gerçek FastF1 pist yörüngesini kullanır.

    Bu oyun bir simülasyondur; araçlar gerçek GPS değildir. Ama çizilen pist,
    aynı Grand Prix'nin FastF1 telemetrisinden alınır. Bağlantı yoksa dürüstçe
    'geçici yörünge' etiketiyle güvenli bir yedek görünüm kullanılır.
    '''
    event_name = manager_event_name(race.get('name', ''))
    outline = {}
    try:
        outline = get_track_outline(2026, event_name) or {}
    except Exception as error:
        log_data_error('manager game track', error)

    raw_x = outline.get('X', []) if isinstance(outline, dict) else []
    raw_y = outline.get('Y', []) if isinstance(outline, dict) else []
    track = []
    try:
        for x, y in zip(raw_x, raw_y):
            x_value, y_value = float(x), float(y)
            if np.isfinite(x_value) and np.isfinite(y_value):
                track.append([round(x_value, 2), round(y_value, 2)])
    except (TypeError, ValueError):
        track = []

    # FastF1 verisi ilk yüklemede yoksa oyun yine açılır; bunu gerçek pist diye
    # göstermiyoruz. Bir sonraki girişte get_track_outline önbellekten gelir.
    source = 'FastF1 telemetri pisti'
    if len(track) < 50:
        source = 'Geçici oyun yörüngesi · FastF1 pisti yüklenince otomatik değişir'
        track = [
            [0, 18], [10, 2], [36, -5], [62, 2], [78, 20], [86, 44], [78, 70],
            [60, 82], [37, 76], [24, 62], [11, 73], [-4, 62], [-10, 38], [0, 18],
        ]

    ordered = manager_rank_race(race)
    leader_code = ordered[0] if ordered else ''
    leader_time = float(race['cars'][leader_code]['time']) if leader_code else 0.0
    player_rows = []
    for code in selected:
        car = race['cars'][code]
        driver = driver_map[code]
        gap = max(0.0, float(car['time']) - leader_time)
        player_rows.append(
            f"<div class='mini-stat' style='border-left:4px solid {team_color(driver['team'])}'>"
            f"<span>{html_lib.escape(driver['name'])} · P{car['position']}</span>"
            f"<b>+{gap:.1f} sn · {car['tyre']} · %{car['life']:.0f}</b></div>"
        )
    command_rows = []
    for code in selected:
        car = race['cars'][code]
        command_rows.append(
            f"<div class='mini-stat'><span>{code} komut</span>"
            f"<b>{html_lib.escape(car['pace'])} · ERS {html_lib.escape(car['ers'])} · Pit {car['pit_stops']}</b></div>"
        )
    leaderboard_html = ''.join(
        f"<div style='display:flex;justify-content:space-between;gap:10px;padding:7px 10px;border-left:3px solid {team_color(race['cars'][code]['team'])};border-bottom:1px solid #26374c'><b>P{race['cars'][code]['position']} · {code}</b><span>+{max(0.0, float(race['cars'][code]['time']) - leader_time):.1f} sn · {race['cars'][code]['tyre']} · %{race['cars'][code]['life']:.0f}</span></div>"
        for code in ordered[:8]
    )
    log_html = ''.join(
        f"<div style='padding:7px 0;border-bottom:1px solid #26374c'>{html_lib.escape(item)}</div>"
        for item in race['log'][-4:]
    ) or "<div class='driver-meta'>Henüz yeni pit veya strateji olayı yok.</div>"
    left, right = st.columns([2, 1])
    with left:
        st.markdown(
            f"<div class='hud-card' style='border-left:4px solid {team['color']}'><div class='hud-label'>PIT WALL // YARIŞ DURUMU</div>"
            f"<div style='display:flex;gap:9px;flex-wrap:wrap;margin:9px 0'>{''.join(player_rows)}</div>"
            f"<div class='hud-label' style='margin-top:14px'>AKTİF TALİMATLAR</div><div style='display:flex;gap:9px;flex-wrap:wrap;margin:8px 0'>{''.join(command_rows)}</div>"
            f"<div class='hud-label' style='margin-top:14px'>SON KARARLAR</div>{log_html}</div>",
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f"<div class='hud-card'><div class='hud-label'>CANLI OYUN SIRALAMASI · İLK 8</div>{leaderboard_html}"
            f"<div class='driver-meta' style='margin-top:8px'>Sıralama gerçek zaman farkıyla güncellenir.</div></div>",
            unsafe_allow_html=True,
        )
    if race['finished']:
        st.success(state.get('log_message', 'Yarış tamamlandı.'))
        final_rows = [row for row in state['season_results'][-1]['sonuç'] if row['Pilot'] in selected]
        st.markdown(f"<div class='hud-card' style='border-left:4px solid #f7c948'><div class='hud-label'>YARIŞ SONU HUD</div><div style='display:flex;gap:12px;flex-wrap:wrap'>{''.join(f'<div class="mini-stat"><span>{row["Pilot"]}</span><b>P{row["Sıra"]} · {row["Puan"]} P</b></div>' for row in final_rows)}</div></div>", unsafe_allow_html=True)
        if state['round'] + 1 < len(MANAGER_RACE_CALENDAR):
            if st.button('Sonraki yarışa geç', key='manager_v2_next_race', use_container_width=True):
                state['round'] += 1
                state['phase'] = 'garage'
                state['race'] = None
                st.session_state[state_key] = state
                manager_save_state(state)
                st.rerun()
        else:
            if st.button('Yeni sezona geç', key='manager_v2_new_season', use_container_width=True):
                state['career_history'].append({'season': state['season'], 'points': total_points, 'team': state['team']})
                state['season'] += 1
                state['round'] = 0
                state['phase'] = 'garage'
                state['race'] = None
                state['season_results'] = []
                state['budget'] += 18
                st.session_state[state_key] = state
                manager_save_state(state)
                st.rerun()
    if st.button('Kariyeri sıfırla', key='manager_v2_reset'):
        st.session_state[state_key] = None
        try:
            if os.path.exists(MANAGER_SAVE_PATH):
                os.remove(MANAGER_SAVE_PATH)
        except Exception as error:
            log_data_error('manager save reset', error)
        st.rerun()


def render_paddock_predictor():
    st.markdown("## Paddock Predictor")
    st.caption("Lock pole and podium predictions for the next Grand Prix. The score is calculated once verified FastF1 results are available.")
    pool = game_driver_pool()
    labels = {driver['code']: f"{driver['name']} ({driver['code']})" for driver in pool}
    try:
        event, _session_name, event_time, _live = get_current_or_next_event()
        event_name = str(event.get('EventName', 'Next Grand Prix'))
        event_year = int(pd.to_datetime(event_time).year)
    except Exception:
        event_name, event_year = 'Next Grand Prix', 2026
    state_key = 'paddock_predictor_v1'
    if state_key not in st.session_state:
        st.session_state[state_key] = None
    state = st.session_state[state_key]
    st.markdown(f"<div class='hud-card' style='border-left:4px solid #f7c948'><div class='hud-label'>PREDICTION TARGET</div><div class='hud-value'>{html_lib.escape(event_name)}</div><div class='driver-meta'>{event_year} | Prediction locks to this event.</div></div>", unsafe_allow_html=True)
    if state is None or state.get('event_name') != event_name:
        with st.form('predictor_form'):
            pole = st.selectbox('Pole', list(labels), format_func=lambda code: labels[code])
            winner = st.selectbox('Race winner', list(labels), format_func=lambda code: labels[code])
            p2 = st.selectbox('P2', list(labels), format_func=lambda code: labels[code])
            p3 = st.selectbox('P3', list(labels), format_func=lambda code: labels[code])
            submitted = st.form_submit_button('Lock prediction', use_container_width=True)
        if submitted:
            if len({winner, p2, p3}) < 3:
                st.error('The three podium predictions must be different drivers. Pole may be one of them.')
            else:
                st.session_state[state_key] = {'event_name': event_name, 'year': event_year, 'pole': pole, 'winner': winner, 'p2': p2, 'p3': p3}
                st.rerun()
        return

    st.markdown(f"<div class='hud-card'><b>Locked prediction:</b> Pole {state['pole']} | P1 {state['winner']} | P2 {state['p2']} | P3 {state['p3']}</div>", unsafe_allow_html=True)
    race_table, _ = get_session_results_table(state['year'], state['event_name'], 'R')
    quali_table, _ = get_session_results_table(state['year'], state['event_name'], 'Q')
    if race_table.empty or quali_table.empty:
        st.info('Race results are not verified yet. Your prediction remains locked and will score automatically when results arrive.')
    else:
        race_order = race_table['Pilot'].astype(str).tolist()
        pole_order = quali_table['Pilot'].astype(str).tolist()
        score = 0
        score += 10 if pole_order and state['pole'] == pole_order[0] else 0
        score += 15 if race_order and state['winner'] == race_order[0] else 0
        score += 8 if len(race_order) > 1 and state['p2'] == race_order[1] else 0
        score += 8 if len(race_order) > 2 and state['p3'] == race_order[2] else 0
        st.success(f"Verified result. Your prediction score: {score} / 41")
        st.dataframe(race_table.head(10), use_container_width=True, hide_index=True)
    if st.button('Clear prediction for a new weekend', key='predictor_reset'):
        st.session_state[state_key] = None
        st.rerun()



def render_manager_season_standings_hud(driver_totals, team_totals, driver_order, team_order, code_to_driver, player_team, accent):
    """Kariyer oyununun sezon boyu g\u00fcncel puan HUD'u."""
    def driver_card(code, place):
        info = code_to_driver.get(code, {'name': code, 'team': '\u2014'})
        stat = driver_totals.get(code, {})
        colour = team_color(info.get('team', ''))
        return (
            f"<div class='mini-stat' style='border-left:4px solid {colour};min-width:175px'>"
            f"<span>P{place} \u00b7 {html_lib.escape(str(info.get('name', code)))}</span>"
            f"<b>{int(stat.get('points', 0))} P \u00b7 {int(stat.get('wins', 0))} galibiyet</b></div>"
        )

    def team_card(name, place):
        stat = team_totals.get(name, {})
        return (
            f"<div class='mini-stat' style='border-left:4px solid {team_color(name)};min-width:175px'>"
            f"<span>P{place} \u00b7 {html_lib.escape(str(name))}</span>"
            f"<b>{int(stat.get('points', 0))} P \u00b7 {int(stat.get('wins', 0))} galibiyet</b></div>"
        )

    driver_rank = {code: place for place, code in enumerate(driver_order, 1)}
    team_rank = {name: place for place, name in enumerate(team_order, 1)}
    player = team_totals.get(player_team, {'points': 0, 'wins': 0, 'podiums': 0})
    st.markdown(
        f"<div class='hud-card' style='border-left:4px solid {accent};margin-top:18px'>"
        f"<div class='hud-label'>SEZON PUAN MERKEZ\u0130</div>"
        f"<div style='font-size:1.35rem;font-weight:950;color:{accent};margin:5px 0'>{html_lib.escape(str(player_team))} \u00b7 Tak\u0131mlar P{team_rank.get(player_team, '\u2014')}</div>"
        f"<div class='driver-meta'>Toplam {int(player.get('points', 0))} puan \u00b7 {int(player.get('wins', 0))} galibiyet \u00b7 {int(player.get('podiums', 0))} podyum</div>"
        f"<div class='hud-label' style='margin-top:14px'>P\u0130LOTLAR \u00b7 \u0130LK 5</div><div style='display:flex;gap:9px;flex-wrap:wrap;margin-top:8px'>{''.join(driver_card(code, driver_rank[code]) for code in driver_order[:5])}</div>"
        f"<div class='hud-label' style='margin-top:14px'>TAKIMLAR \u00b7 \u0130LK 5</div><div style='display:flex;gap:9px;flex-wrap:wrap;margin-top:8px'>{''.join(team_card(name, team_rank[name]) for name in team_order[:5])}</div></div>",
        unsafe_allow_html=True,
    )
    driver_rows = [{
        'S\u0131ra': place,
        'Pilot': code,
        'Tak\u0131m': code_to_driver.get(code, {}).get('team', '\u2014'),
        'Puan': int(driver_totals.get(code, {}).get('points', 0)),
        'Galibiyet': int(driver_totals.get(code, {}).get('wins', 0)),
        'Podyum': int(driver_totals.get(code, {}).get('podiums', 0)),
    } for place, code in enumerate(driver_order, 1)]
    team_rows = [{
        'S\u0131ra': place,
        'Tak\u0131m': name,
        'Puan': int(team_totals.get(name, {}).get('points', 0)),
        'Galibiyet': int(team_totals.get(name, {}).get('wins', 0)),
        'Podyum': int(team_totals.get(name, {}).get('podiums', 0)),
    } for place, name in enumerate(team_order, 1)]
    with st.expander('Sezon sonu pilot ve tak\u0131m puan tablolar\u0131n\u0131 a\u00e7', expanded=False):
        pilots, teams = st.tabs(['Pilot Puanlar\u0131', 'Tak\u0131m Puanlar\u0131'])
        with pilots:
            st.dataframe(pd.DataFrame(driver_rows), use_container_width=True, hide_index=True, height=min(760, 78 + 38 * len(driver_rows)))
        with teams:
            st.dataframe(pd.DataFrame(team_rows), use_container_width=True, hide_index=True, height=min(540, 78 + 38 * len(team_rows)))


def render_pitwall_challenge_game():
    """K\u0131sa ve tekrar oynanabilen strateji oyunu; ger\u00e7ek yar\u0131\u015f sonucu de\u011fildir."""
    key = 'pitwall_challenge_v1'
    if key not in st.session_state:
        st.session_state[key] = {'score': 0, 'round': 1, 'message': '', 'history': []}
    game = st.session_state[key]
    scenarios = [
        ('Monaco \u00b7 dar pist', 'HARD', 25, 'Temiz hava ve uzun stint de\u011ferli.'),
        ('Hungaroring \u00b7 y\u00fcksek a\u015f\u0131nma', 'MEDIUM', 20, 'Dengeli hamur s\u0131cak pistte g\u00fcvenli.'),
        ('Silverstone \u00b7 d\u00fc\u015f\u00fck tutunma', 'SOFT', 14, 'K\u0131sa atakla pozisyon kazanabilirsin.'),
        ('Bahrain \u00b7 gece yar\u0131\u015f\u0131', 'MEDIUM', 18, 'Erken pit trafik yaratabilir.'),
    ]
    name, target_tyre, target_lap, clue = scenarios[(game['round'] - 1) % len(scenarios)]
    st.markdown("<div class='hud-card' style='border-top:4px solid #a78bfa;margin-top:20px'><div class='hud-label'>YEN\u0130 OYUN \u00b7 PIT WALL CHALLENGE</div><div style='font-size:1.25rem;font-weight:950;margin-top:5px'>Strateji Kart\u0131</div><div class='history-copy' style='margin-top:6px'>Lasti\u011fi ve pit penceresini se\u00e7; karar\u0131n oyun puan\u0131na d\u00f6n\u00fc\u015fs\u00fcn. Bu bir strateji sim\u00fclasyonudur.</div></div>", unsafe_allow_html=True)
    left, right = st.columns([2, 1])
    with left:
        st.markdown(f"<div class='hud-card' style='border-left:4px solid #a78bfa'><div class='hud-label'>TUR {game['round']} \u00b7 STRATEJ\u0130 BR\u0130F\u0130NG\u0130</div><div class='hud-value'>{html_lib.escape(name)}</div><div class='driver-meta' style='margin-top:6px'>{html_lib.escape(clue)}</div></div>", unsafe_allow_html=True)
        tyre = st.radio('Yeni lastik', ['SOFT', 'MEDIUM', 'HARD'], horizontal=True, key=f"pitwall_tyre_{game['round']}")
        window = st.slider('Pit penceresi (tur)', 8, 32, 18, key=f"pitwall_lap_{game['round']}")
        if st.button('Stratejiyi uygula', key=f"pitwall_play_{game['round']}", use_container_width=True):
            gained = int((60 if tyre == target_tyre else 20) + max(0, 40 - abs(window - target_lap) * 5))
            game['score'] += gained
            game['history'].append({'Tur': game['round'], 'Senaryo': name, 'Lastik': tyre, 'Pit': window, 'Puan': gained})
            game['message'] = f"Karar i\u015flendi: +{gained} oyun puan\u0131."
            game['round'] += 1
            st.session_state[key] = game
            st.rerun()
    with right:
        st.markdown(f"<div class='hud-card' style='border-left:4px solid #f7c948'><div class='hud-label'>PIT WALL SKORU</div><div class='hud-value'>{game['score']} P</div><div class='driver-meta'>Tamamlanan karar: {len(game['history'])}</div></div>", unsafe_allow_html=True)
    if game['message']:
        st.success(game['message'])
    if game['history']:
        with st.expander('Strateji karar ge\u00e7mi\u015fi', expanded=False):
            st.dataframe(pd.DataFrame(game['history']), use_container_width=True, hide_index=True)
    if st.button('Strateji Kart\u0131 skorunu s\u0131f\u0131rla', key='pitwall_reset'):
        st.session_state[key] = {'score': 0, 'round': 1, 'message': '', 'history': []}
        st.rerun()

def render_games_hub():
    """Yerel ve veri-bağlı oyunları tek, basit oyun merkezinde toplar."""
    st.markdown("## 🎮 Oyun Merkezi")
    st.caption("Oyunlar 2026 pilot diziniyle çalışır. Kariyer sonucu oyundur; tahmin oyunu ise tamamlanan yarışın gerçek sonucuyla puanlanır.")
    top_left, top_right = st.columns(2)
    with top_left:
        st.markdown("<div class='hud-card' style='border-top:4px solid #ff385c;min-height:166px'><div class='hud-label'>GÜNLÜK BULMACA</div><div style='font-size:1.35rem;font-weight:950;margin-top:7px'>Stewarlde</div><div class='history-copy' style='margin-top:8px'>Altı tahminde günün F1 pilotunu bul. Takım, numara, ülke ve kariyer ipuçları yol gösterir.</div></div>", unsafe_allow_html=True)
        if st.button("Stewarlde'ı aç", key='games_open_stewarlde', use_container_width=True):
            st.session_state['page'] = 'stewarlde'
            st.rerun()
    with top_right:
        st.markdown("<div class='hud-card' style='border-top:4px solid #f7c948;min-height:166px'><div class='hud-label'>GÜNLÜK QUIZ</div><div style='font-size:1.35rem;font-weight:950;margin-top:7px'>GridMaster</div><div class='history-copy' style='margin-top:8px'>On soruluk kısa F1 testi. Her gün değişen sorularla kendi puanını yükselt.</div></div>", unsafe_allow_html=True)
        if st.button("GridMaster'ı aç", key='games_open_gridmaster', use_container_width=True):
            st.session_state['page'] = 'gridmaster'
            st.rerun()
    bottom_left, bottom_right = st.columns(2)
    with bottom_left:
        st.markdown("<div class='hud-card' style='border-top:4px solid #2ee6c9;min-height:166px'><div class='hud-label'>KALICI KARİYER OYUNU</div><div style='font-size:1.35rem;font-weight:950;margin-top:7px'>Takım Patronu Kariyeri</div><div class='history-copy' style='margin-top:8px'>İki pilotunu kur, lastikleri ve pit kararlarını kendin ver. Kariyer bilgisayara kaydolur; sezon bitince yeni sezona geçebilirsin.</div></div>", unsafe_allow_html=True)
        if st.button("Kariyeri başlat", key='games_open_manager', use_container_width=True):
            st.session_state['page'] = 'team_manager'
            st.rerun()
    with bottom_right:
        st.markdown("<div class='hud-card' style='border-top:4px solid #7dd3fc;min-height:166px'><div class='hud-label'>GERÇEK YARIŞ TAHMİNİ</div><div style='font-size:1.35rem;font-weight:950;margin-top:7px'>Paddock Tahmin</div><div class='history-copy' style='margin-top:8px'>Yaklaşan hafta sonu için pole ve podyumu seç. Yarış bittiğinde tahminin FastF1 sonucuyla puanlansın.</div></div>", unsafe_allow_html=True)
        if st.button("Tahmin oyununu aç", key='games_open_predictor', use_container_width=True):
            st.session_state['page'] = 'predictor'
            st.rerun()
    render_data_trust_hud()
    render_pitwall_challenge_game()




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


def pitwall_state():
    key = 'pitwall_strategy_lab_v2'
    if key not in st.session_state:
        st.session_state[key] = {
            'team': 'McLaren', 'lap': 1, 'score': 0, 'fuel': 100, 'tyre': 'MEDIUM',
            'tyre_age': 0, 'stops': 0, 'log': [], 'message': 'Baslangic lastigini sec ve ilk uc turluk plani yap.',
        }
    return st.session_state[key], key


def tyre_palette(compound):
    return {'SOFT': '#ff385c', 'MEDIUM': '#f7c948', 'HARD': '#e8eef8'}.get(str(compound).upper(), '#94a3b8')


def tyre_hud_html(compound, age, color):
    limit = {'SOFT': 15, 'MEDIUM': 23, 'HARD': 31}.get(compound, 22)
    health = max(0, min(100, int(100 - (age / limit) * 100)))
    return (
        f"<div style='display:flex;align-items:center;gap:11px;margin-top:10px'>"
        f"<div style='width:54px;height:54px;border-radius:50%;background:conic-gradient({color} {health}%,#243044 {health}%);display:grid;place-items:center'>"
        "<div style='width:40px;height:40px;border-radius:50%;background:#101826;display:grid;place-items:center;font-weight:950'>"
        f"{compound[0]}</div></div><div><div style='font-weight:900'>{compound}</div>"
        f"<div class='driver-meta'>Lastik omru: %{health} | {age}. tur</div></div></div>"
    )


def render_pitwall_challenge_game():
    """Standalone, repeatable tyre-and-pit strategy game. It never claims to be live F1."""
    game, state_key = pitwall_state()
    teams = list(TEAM_DIRECTORY_2026.keys())
    if game['team'] not in teams:
        game['team'] = teams[0]
    team_index = teams.index(game['team'])

    st.markdown("---")
    st.markdown("## Pit Duvari Strateji Laboratuvari")
    st.caption("Bu ayri oyun bolumu gercek yarisi simule etmez. Lastik, tempo ve pit kararlarinin sonucunu ogreten tekrar oynanabilir bir strateji deneyimidir.")

    chosen_team = st.selectbox('Takimini sec', teams, index=team_index, key='pitwall_lab_team')
    if chosen_team != game['team']:
        game.update({'team': chosen_team, 'lap': 1, 'score': 0, 'fuel': 100, 'tyre': 'MEDIUM', 'tyre_age': 0, 'stops': 0, 'log': [], 'message': 'Yeni takim icin strateji tahtasi hazir.'})
        st.session_state[state_key] = game
        st.rerun()

    team = TEAM_DIRECTORY_2026[game['team']]
    driver_data = team['drivers'][:2]
    track_profiles = [
        ('Bahrain', 'Sicak asfalt: medium ile dengeli basla, softu gec kullan.'),
        ('Hungaroring', 'Trafik onemli: lastigi bitirmeden temiz hava bul.'),
        ('Silverstone', 'Ruzgarli kosul: hard uzun stinti guvenceye alir.'),
        ('Monaco', 'Pozisyon kritik: pit penceresini gereksiz erken acma.'),
    ]
    track_name, briefing = track_profiles[(game['lap'] - 1) % len(track_profiles)]
    stint_color = tyre_palette(game['tyre'])

    st.markdown(
        f"<div class='hud-card' style='border-top:4px solid {team['color']};margin:10px 0 14px'>"
        "<div class='hud-label'>PIT WALL // STRATEJI OTURUMU</div>"
        f"<div class='hud-value'>{html_lib.escape(game['team'])} | {html_lib.escape(track_name)} | Tur {game['lap']} / 18</div>"
        f"<div class='driver-meta' style='margin-top:6px'>{html_lib.escape(briefing)}</div></div>", unsafe_allow_html=True
    )

    car_cols = st.columns(2)
    for col, item in zip(car_cols, driver_data):
        name, code, number, image_path = item
        portrait = current_driver_portrait(game['team'], image_path)
        with col:
            st.markdown(
                f"<div class='hud-card' style='border-left:4px solid {team['color']};display:flex;align-items:center;gap:14px'>"
                f"{strategy_game_image(portrait, name, team['color'])}<div><div class='hud-label'>SURUCU // {html_lib.escape(code)}</div>"
                f"<div style='font-size:1.2rem;font-weight:950;color:{team['color']}'>{html_lib.escape(name)}</div>"
                f"<div class='driver-meta'>{html_lib.escape(number)} | Yaris temposu {manager_driver_rating(code, 'race')}</div></div></div>", unsafe_allow_html=True
            )

    left, centre, right = st.columns([1.25, 1.5, 1.0])
    with left:
        st.markdown(f"<div class='hud-card' style='border-left:4px solid {stint_color}'><div class='hud-label'>AKTIF LASTIK</div>{tyre_hud_html(game['tyre'], game['tyre_age'], stint_color)}</div>", unsafe_allow_html=True)
    with centre:
        st.markdown(
            f"<div class='hud-card'><div class='hud-label'>PIT DUVARI DURUMU</div>"
            f"<div style='font-size:1.15rem;font-weight:950'>Yakit: %{game['fuel']} | Pit: {game['stops']}</div>"
            f"<div class='driver-meta' style='margin-top:7px'>{html_lib.escape(game['message'])}</div></div>", unsafe_allow_html=True
        )
    with right:
        st.markdown(f"<div class='hud-card' style='border-left:4px solid #a78bfa'><div class='hud-label'>KARIYER SKORU</div><div class='hud-value'>{game['score']} P</div><div class='driver-meta'>Karar turu: {len(game['log'])}</div></div>", unsafe_allow_html=True)

    decision_col, pace_col = st.columns(2)
    with decision_col:
        decision = st.selectbox('Pit karari', ['Pitte kal', 'SOFT tak', 'MEDIUM tak', 'HARD tak'], key=f"pitwall_decision_{game['lap']}")
    with pace_col:
        pace = st.selectbox('Tempo', ['Dengeli', 'Atak', 'Koru'], key=f"pitwall_pace_{game['lap']}")

    if game['lap'] <= 18:
        if st.button('Karari uygula ve 3 tur ilerlet', key=f"pitwall_advance_{game['lap']}", use_container_width=True):
            old_tyre = game['tyre']
            pit = decision != 'Pitte kal'
            if pit:
                game['tyre'] = decision.split()[0]
                game['tyre_age'] = 0
                game['stops'] += 1
            wear = {'SOFT': 5, 'MEDIUM': 3, 'HARD': 2}.get(game['tyre'], 3)
            if pace == 'Atak':
                wear += 2
            elif pace == 'Koru':
                wear = max(1, wear - 1)
            game['tyre_age'] += wear * 3
            game['fuel'] = max(0, game['fuel'] - 11)
            target = 'MEDIUM' if track_name in ['Bahrain', 'Hungaroring'] else 'HARD'
            gain = 18 + (14 if game['tyre'] == target else 4) + (7 if pace == 'Dengeli' else 3)
            if pit and old_tyre == game['tyre']:
                gain -= 9
            if game['tyre_age'] > 23:
                gain -= 8
            gain = max(0, gain)
            game['score'] += gain
            game['log'].append({'Tur': f"{game['lap']}-{min(18, game['lap'] + 2)}", 'Karar': decision, 'Tempo': pace, 'Lastik': game['tyre'], 'Puan': gain})
            game['message'] = f"{decision} / {pace}: +{gain} strateji puani."
            game['lap'] = min(19, game['lap'] + 3)
            st.session_state[state_key] = game
            st.rerun()
    else:
        st.success(f"Strateji oturumu tamamlandi: {game['score']} puan. Farkli bir takim veya yaklasimla tekrar deneyebilirsin.")

    if game['log']:
        with st.expander('Pit duvari karar gecmisi', expanded=False):
            st.dataframe(pd.DataFrame(game['log']), use_container_width=True, hide_index=True)
    if st.button('Bu strateji oturumunu sifirla', key='pitwall_lab_reset'):
        st.session_state.pop(state_key, None)
        st.rerun()


def render_games_hub():
    """Clean games home with the tyre-and-pit game as its own lower section."""
    st.markdown("## Oyun Merkezi")
    st.caption("Oyun sonuclari simulasyondur. Gercek yarislardan gelen veri sadece tahmin ve bilgi alanlarinda kullanilir.")
    cards = [
        ('GUNLUK BULMACA', 'Stewarlde', 'Alti tahminde gunun F1 pilotunu bul.', '#ff385c', 'Stewarlde ac', 'stewarlde'),
        ('GUNLUK QUIZ', 'GridMaster', 'Kisa F1 testiyle puanini yukseltebilirsin.', '#f7c948', 'GridMaster ac', 'gridmaster'),
        ('KARIYER OYUNU', 'Takim Patronu Kariyeri', 'Pilot sec, sezonunu kur ve yarislari yonet.', '#2ee6c9', 'Kariyeri baslat', 'team_manager'),
        ('GERCEK YARIS TAHMINI', 'Paddock Tahmin', 'Pole ve podyum tahminini tamamlanmis sonuc ile karsilastir.', '#7dd3fc', 'Tahmin oyununu ac', 'predictor'),
    ]
    for row in [cards[:2], cards[2:]]:
        columns = st.columns(2)
        for col, card in zip(columns, row):
            label, title, copy, color, button_text, page = card
            with col:
                st.markdown(f"<div class='hud-card' style='border-top:4px solid {color};min-height:146px'><div class='hud-label'>{label}</div><div style='font-size:1.3rem;font-weight:950;margin-top:7px'>{title}</div><div class='history-copy' style='margin-top:8px'>{copy}</div></div>", unsafe_allow_html=True)
                if st.button(button_text, key=f"games_open_{page}", use_container_width=True):
                    st.session_state['page'] = page
                    st.rerun()
    render_data_trust_hud()
    render_pitwall_challenge_game()






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
    st.markdown(
        f"<div class='hud-card' style='border-left:5px solid {accent};margin:6px 0 20px'>"
        f"<div class='hud-label'>PADDOCK // YENI MERKEZ</div>"
        f"<div style='font-size:1.7rem;font-weight:950;margin-top:6px'>{html_lib.escape(title)}</div>"
        f"<div class='history-copy' style='margin-top:6px'>{html_lib.escape(subtitle)}</div></div>",
        unsafe_allow_html=True,
    )


def completed_session_options(event):
    return [item for item in event_session_cards(event) if item.get('status') == 'Tamamlandı']


def render_weekend_centre():
    render_page_header('Hafta Sonu Merkezi', 'Bir Grand Prix sec; programi, tamamlanan seanslari ve sonuc ekranlarini tek yerde gor.', '#7dd3fc')
    events = get_calendar_details(2026)
    if not events:
        st.warning('Takvim verisi su anda alinamadi. Biraz sonra tekrar dene.')
        return
    event_names = [str(event.get('EventName', 'Grand Prix')) for event in events]
    selected_name = st.selectbox('Grand Prix sec', event_names, key='weekend_centre_event')
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
        st.info('Bu hafta sonunda henuz tamamlanan seans yok. Program yukarida Istanbul saatine gore gorunur.')
        return
    code_map = {f"{item['title']} // {item['time'].tz_convert('Europe/Istanbul').strftime('%d %b %H:%M')}": item for item in completed}
    selected_label = st.radio('Tamamlanan seans', list(code_map), horizontal=True, key='weekend_centre_session')
    selected = code_map[selected_label]
    with st.spinner('Dogrulanmis seans sonucu yukleniyor...'):
        table, _ = get_session_results_table(2026, selected_name, selected['code'])
        story = get_session_story(2026, selected_name, selected['code'])
    if story:
        st.markdown('#### Bu seansta ne oldu?')
        story_cols = st.columns(min(3, len(story)))
        for col, entry in zip(story_cols, story[:3]):
            with col:
                st.markdown(f"<div class='hud-card' style='border-top:3px solid #f7c948;min-height:94px'><div class='hud-label'>{html_lib.escape(entry.get('kind', 'NOT'))}</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(entry.get('text', ''))}</div></div>", unsafe_allow_html=True)
    if table.empty:
        st.info('Bu seansin dogrulanmis sonuclari henuz paketlenmedi.')
        return
    st.markdown(f"#### {html_lib.escape(selected['title'])} sonuclari", unsafe_allow_html=True)
    render_html_hud(session_leaderboard_html(table, f'{selected_name} // {selected["title"].upper()}'), height=leaderboard_component_height(table), scrolling=False)


def render_race_story_centre():
    render_page_header('Yaris Hikayesi', 'Sonuclari teknik bir tablo olmaktan cikarip pole, kazanan, en cok yukselen pilot ve onemli notlara donusturur.', '#ff385c')
    events = get_calendar_details(2026)
    if not events:
        st.warning('Yaris takvimi su anda alinamadi.')
        return
    completed_events = []
    for event in events:
        if any(item.get('code') == 'R' for item in completed_session_options(event)):
            completed_events.append(event)
    if not completed_events:
        st.info('Hikaye olusturmak icin henuz tamamlanmis bir yaris sonucu yok.')
        return
    names = [str(event.get('EventName')) for event in completed_events]
    event_name = st.selectbox('Yaris sec', names, index=len(names)-1, key='story_centre_event')
    with st.spinner('Yaris hikayesi hazirlaniyor...'):
        table, _ = get_session_results_table(2026, event_name, 'R')
        notes = get_session_story(2026, event_name, 'R')
    if table.empty:
        st.info('Bu yarisin dogrulanmis sonucu henuz alinamadi.')
        return
    cards = []
    winner = table.iloc[0] if len(table) else None
    if winner is not None:
        cards.append(('KAZANAN', f"{winner.get('Pilot', '-')}", str(winner.get('Takım', '-')), '#f7c948'))
    if 'Sıra' in table.columns:
        top10 = table[table['Sıra'].astype(str).str.match(r'^\d+$', na=False)].head(10)
        if not top10.empty:
            cards.append(('PUAN ALAN SON PILOT', str(top10.iloc[-1].get('Pilot', '-')), 'Ilk 10 puan alir', '#7dd3fc'))
    if 'Points' in table.columns:
        points_text = 'Resmi puan paketi'
    else:
        points_text = 'Sonuc ve strateji ozeti'
    cards.append(('YARIS DOSYASI', event_name, points_text, '#2ee6c9'))
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        label, main, small, color = card
        with col:
            st.markdown(f"<div class='hud-card' style='border-top:4px solid {color};min-height:106px'><div class='hud-label'>{label}</div><div style='font-size:1.2rem;font-weight:950;margin-top:8px'>{html_lib.escape(main)}</div><div class='driver-meta' style='margin-top:6px'>{html_lib.escape(small)}</div></div>", unsafe_allow_html=True)
    if notes:
        st.markdown('#### Dikkat ceken anlar')
        for entry in notes:
            st.markdown(f"<div class='hud-card' style='border-left:4px solid #ff385c;padding:11px 14px;margin:7px 0'><b>{html_lib.escape(entry.get('kind','NOT'))}</b><div class='history-copy' style='margin-top:5px'>{html_lib.escape(entry.get('text',''))}</div></div>", unsafe_allow_html=True)
    st.markdown('#### Tam sonuc')
    render_html_hud(session_leaderboard_html(table, f'{event_name} // YARIS SONUCU'), height=leaderboard_component_height(table), scrolling=False)


def render_driver_comparison_centre():
    render_page_header('Pilot Karsilastirma', 'Iki pilotu ayni tamamlanmis seanstaki gercek sonuc, takim ve tur verisiyle karsilastir.', '#a78bfa')
    latest = get_latest_completed_session(2026)
    if not latest:
        st.info('Karsilastirma icin tamamlanmis bir seans bekleniyor.')
        return
    event_name = latest['event_name']
    session_code = latest['session_code']
    with st.spinner('Karsilastirma verisi yukleniyor...'):
        table, _ = get_session_results_table(latest['year'], event_name, session_code)
    if table.empty or 'Pilot' not in table.columns:
        st.info('Bu seans icin pilot sonucu henuz alinmadi.')
        return
    driver_codes = table['Pilot'].dropna().astype(str).tolist()
    left, right = st.columns(2)
    default_b = 1 if len(driver_codes) > 1 else 0
    with left:
        driver_a = st.selectbox('Birinci pilot', driver_codes, key='compare_driver_a')
    with right:
        driver_b = st.selectbox('Ikinci pilot', driver_codes, index=default_b, key='compare_driver_b')
    if driver_a == driver_b:
        st.warning('Iki farkli pilot sec.')
        return
    selected_rows = table[table['Pilot'].astype(str).isin([driver_a, driver_b])].copy()
    rows = []
    card_cols = st.columns(2)
    for col, code in zip(card_cols, [driver_a, driver_b]):
        info = directory_driver_by_code(code)
        row = selected_rows[selected_rows['Pilot'].astype(str) == code].iloc[0]
        colour = team_colour(str(row.get('Takım', info['team'])))
        portrait = current_driver_portrait(info['team'], info['image']) if info['image'] else ''
        with col:
            st.markdown(f"<div class='hud-card' style='border-left:4px solid {colour};display:flex;gap:14px;align-items:center'>{strategy_game_image(portrait, info['name'], colour)}<div><div class='hud-label'>SEANS KARSILASTIRMA</div><div style='font-size:1.25rem;font-weight:950;color:{colour}'>{html_lib.escape(info['name'])}</div><div class='driver-meta'>{html_lib.escape(code)} | {html_lib.escape(str(row.get('Takım','-')))}</div><div style='font-size:1.12rem;font-weight:900;margin-top:8px'>Sira: {html_lib.escape(str(row.get('Sıra','-')))}</div></div></div>", unsafe_allow_html=True)
        compact = {'Pilot': code, 'Takim': str(row.get('Takım', '-')), 'Sira': row.get('Sıra', '-')}
        for key in ['En Hızlı Tur', 'Zaman', 'Q1', 'Q2', 'Q3', 'Durum', 'Puan']:
            if key in table.columns:
                compact[key] = row.get(key, '-')
        rows.append(compact)
    st.markdown(f"#### {html_lib.escape(event_name)} // {html_lib.escape(latest['display_name'])}", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption('Bu ekran sadece secilen tamamlanmis seansin dogrulanmis sonucunu karsilastirir; tahmini sezon ratingi kullanmaz.')


def render_learning_centre():
    render_page_header('F1 Baslangic Garaji', 'Formula 1 bilmeyen biri icin hafta sonu, lastik, pit stop ve puan sistemini bes dakikada anlatan mini rehber.', '#f7c948')
    lessons = [
        ('1. Hafta sonu nasil isler?', 'FP1-FP3 arac hazirligi icindir. Siralama grid sirasini, yaris ise puanlari belirler.'),
        ('2. Lastik neden onemli?', 'Soft hizli ama cabuk asinabilir. Hard daha dayaniklidir. Dogru pit zamani tur kaybini azaltir.'),
        ('3. Pole nedir?', 'Siralamada en hizli turu atan pilot pole pozisyonundan, yani ilk siradan baslar.'),
        ('4. Puan nasil gelir?', 'Ana yarista ilk 10 pilot puan alir. Sprint hafta sonlarinda sprint de ek puan getirir.'),
        ('5. Telemetri ne soyler?', 'Hiz, fren, gaz ve vites grafigi iki pilotun turu nerede kazandigini gosterir.'),
    ]
    for title, copy in lessons:
        with st.expander(title, expanded=False):
            st.write(copy)
    st.markdown('### Mini kontrol')
    answer = st.radio('Pole pozisyonu neyi belirler?', ['Pit stop sirasini', 'Yaris baslangic sirasini', 'Takim puanini'], key='learning_pole_quiz')
    if st.button('Cevabi kontrol et', key='learning_check'):
        if answer == 'Yaris baslangic sirasini':
            st.success('Dogru. Pole alan pilot ana yarisa ilk siradan baslar.')
        else:
            st.info('Ipuçu: Pole, siralamadaki en hizli turun oduludur.')
    if st.button('GridMaster oyununu ac', key='learning_gridmaster', use_container_width=True):
        st.session_state['page'] = 'gridmaster'
        st.rerun()


def render_favourites_centre():
    team_name = st.session_state.get('favourite_team', 'Mercedes')
    driver_name = st.session_state.get('favourite_driver', 'George Russell')
    render_page_header('Favori Paddock', 'Sevdigin takim ve pilot icin hizli baslangic alani.', team_colour(team_name))
    team = TEAM_DIRECTORY_2026.get(team_name, TEAM_DIRECTORY_2026['Mercedes'])
    st.markdown(f"<div class='hud-card' style='border-top:4px solid {team['color']}'><div class='hud-label'>FAVORI TAKIM</div><div class='hud-value' style='color:{team['color']}'>{html_lib.escape(team_name)}</div><div class='driver-meta'>{html_lib.escape(driver_name)} secili pilotun.</div></div>", unsafe_allow_html=True)
    cols = st.columns(2)
    with cols[0]:
        if st.button('Hafta Sonu Merkezine git', key='favourite_weekend', use_container_width=True):
            st.session_state['page'] = 'weekend'
            st.rerun()
    with cols[1]:
        if st.button('Pilot karsilastirmasini ac', key='favourite_compare', use_container_width=True):
            st.session_state['page'] = 'compare'
            st.rerun()
    st.markdown('#### Takim kadrosi')
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

F1_WORLD_CHAMPIONS = {
    1950:'Giuseppe Farina',1951:'Juan Manuel Fangio',1952:'Alberto Ascari',1953:'Alberto Ascari',1954:'Juan Manuel Fangio',1955:'Juan Manuel Fangio',1956:'Jack Brabham',1957:'Juan Manuel Fangio',1958:'Mike Hawthorn',1959:'Jack Brabham',
    1960:'Jack Brabham',1961:'Phil Hill',1962:'Graham Hill',1963:'Jim Clark',1964:'John Surtees',1965:'Jim Clark',1966:'Jack Brabham',1967:'Denny Hulme',1968:'Graham Hill',1969:'Jackie Stewart',
    1970:'Jochen Rindt',1971:'Jackie Stewart',1972:'Emerson Fittipaldi',1973:'Jackie Stewart',1974:'Emerson Fittipaldi',1975:'Niki Lauda',1976:'James Hunt',1977:'Niki Lauda',1978:'Mario Andretti',1979:'Jody Scheckter',
    1980:'Alan Jones',1981:'Nelson Piquet',1982:'Keke Rosberg',1983:'Nelson Piquet',1984:'Niki Lauda',1985:'Alain Prost',1986:'Alain Prost',1987:'Nelson Piquet',1988:'Ayrton Senna',1989:'Alain Prost',
    1990:'Ayrton Senna',1991:'Ayrton Senna',1992:'Nigel Mansell',1993:'Alain Prost',1994:'Michael Schumacher',1995:'Michael Schumacher',1996:'Damon Hill',1997:'Jacques Villeneuve',1998:'Mika Hakkinen',1999:'Mika Hakkinen',
    2000:'Michael Schumacher',2001:'Michael Schumacher',2002:'Michael Schumacher',2003:'Michael Schumacher',2004:'Michael Schumacher',2005:'Fernando Alonso',2006:'Fernando Alonso',2007:'Kimi Raikkonen',2008:'Lewis Hamilton',2009:'Jenson Button',
    2010:'Sebastian Vettel',2011:'Sebastian Vettel',2012:'Sebastian Vettel',2013:'Sebastian Vettel',2014:'Lewis Hamilton',2015:'Lewis Hamilton',2016:'Nico Rosberg',2017:'Lewis Hamilton',2018:'Lewis Hamilton',2019:'Lewis Hamilton',
    2020:'Lewis Hamilton',2021:'Max Verstappen',2022:'Max Verstappen',2023:'Max Verstappen',2024:'Max Verstappen',
}


def paddock_history_answer_v18(question):
    """Stable historic F1 answer set; no API key or network needed."""
    normal = _normalise_question_v19(question)
    year_match = re.search(r'\b(19[5-9][0-9]|20[0-2][0-9])\b', normal)
    wants_champion = any(token in normal for token in ('sampiyon', 'champion', 'wcd', 'dunya birincisi'))
    if year_match and wants_champion:
        year = int(year_match.group(1))
        champion = F1_WORLD_CHAMPIONS.get(year)
        if champion:
            return f'{year} Formula 1 dunya sampiyonu {champion} oldu.'
    if ('1985' in normal or '85' in normal) and wants_champion:
        return '1985 Formula 1 dunya sampiyonu Alain Prost oldu.'
    if ('en cok sampiyon' in normal or 'en fazla sampiyon' in normal or '7 sampiyon' in normal):
        return 'Formula 1 tarihinde rekor yedi sampiyonlukla Lewis Hamilton ve Michael Schumacher tarafindan paylasilir.'
    return ''


def paddock_assistant_answer_v18(question, year=2026):
    """Verified current data first; historic/local answer second; optional OpenAI last."""
    historic = paddock_history_answer_v18(question)
    if historic:
        return {'title': 'F1 tarih bilgisi', 'answer': historic, 'source': 'Yerel F1 dunya sampiyonlari arsivi'}
    answer = paddock_assistant_answer_v19(question, year)
    # Existing function returns a clear verified answer for result questions.
    return answer


def render_paddock_assistant_v20():
    st.markdown('## Paddock Asistani')
    ai_ready = bool(configured_openai_api_key())
    label = 'AI + DOGRULANMIS VERI' if ai_ready else 'DOGRULANMIS VERI + F1 TARIH ARSIVI'
    colour = '#5ddcff' if ai_ready else '#f7c948'
    copy = (
        'Yaris sonucu sorulari once FastF1 sonucundan cevaplanir. Genel F1 sorulari ve tarih bilgisi icin OpenAI destegi aktif.'
        if ai_ready else
        'Son seans, pole, lastik, bitis sirasi ve 1950-2024 dunya sampiyonlari anahtar gerektirmeden cevaplanir. Genel sohbet icin istege bagli OpenAI anahtari eklenebilir.'
    )
    st.markdown(
        f"<div class='hud-card paddock-ai-intro' style='border-left:5px solid {colour}'><div class='hud-label'>{label}</div><div class='history-copy' style='margin-top:7px'>{copy}</div></div>",
        unsafe_allow_html=True,
    )
    if 'paddock_chat_history_v18' not in st.session_state:
        st.session_state['paddock_chat_history_v18'] = []

    quick = st.columns(4)
    quick_questions = ['1985 sampiyonu kim?', 'Pole kim?', 'Son seansta ne oldu?', 'Alonso kacinci oldu?']
    for col, item in zip(quick, quick_questions):
        with col:
            if st.button(item, use_container_width=True, key='assistant_quick_v18_' + item):
                st.session_state['paddock_pending_v18'] = item
                st.rerun()

    for item in st.session_state['paddock_chat_history_v18'][-8:]:
        with st.chat_message(item['role']):
            st.markdown(item['text'])
            if item.get('source'):
                st.caption('Kaynak: ' + item['source'])

    prompt = st.chat_input('F1 hakkinda sor... Ornek: 1985 sampiyonu kim?')
    question = st.session_state.pop('paddock_pending_v18', '') or prompt
    if question:
        st.session_state['paddock_chat_history_v18'].append({'role': 'user', 'text': question})
        with st.chat_message('user'):
            st.markdown(question)
        with st.chat_message('assistant'):
            with st.spinner('Paddock kayitlari kontrol ediliyor...'):
                answer = paddock_assistant_answer_v18(question, 2026)
            st.markdown(answer['answer'])
            st.caption('Kaynak: ' + answer['source'])
        st.session_state['paddock_chat_history_v18'].append({'role': 'assistant', 'text': answer['answer'], 'source': answer['source']})

    if not ai_ready:
        with st.expander('Genel sorular icin OpenAI baglantisi (istege bagli)'):
            st.write('Bilgisayarinda OPENAI_API_KEY ortam degiskeni veya Streamlit secrets icine OPENAI_API_KEY eklersen, asistan genel F1 sorularinda da yanit verebilir. Anahtar eklenmezse mevcut veri ve tarih arsivi normal calisir.')


def _row_value_v18(row, keys):
    for key in keys:
        value = row.get(key, '-')
        if pd.notnull(value) and str(value).strip() not in {'', '-', '—', 'nan', 'None'}:
            return str(value)
    return '-'


def _position_v18(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 99


def render_driver_comparison_centre():
    render_page_header('Pilot Karsilastirma', 'Ayni tamamlanmis seanstan iki pilotun gercek sonucunu, sektorlerini ve takimlarini HUD ekranda gor.', '#a78bfa')
    latest = get_latest_completed_session(2026)
    if not latest:
        st.info('Karsilastirma icin tamamlanmis bir seans bekleniyor.')
        return
    event_name, session_code = latest['event_name'], latest['session_code']
    with st.spinner('Karsilastirma verisi yukleniyor...'):
        table, _ = get_session_results_table(latest['year'], event_name, session_code)
    if table is None or table.empty or 'Pilot' not in table.columns:
        st.info('Bu seans icin pilot sonucu henuz alinmadi.')
        return
    codes = table['Pilot'].dropna().astype(str).tolist()
    if len(codes) < 2:
        st.info('Karsilastirma icin en az iki pilot sonucu gerekli.')
        return
    left, right = st.columns(2)
    with left:
        code_a = st.selectbox('Birinci pilot', codes, key='compare_driver_a_v18')
    with right:
        default_b = 1 if codes[0] == code_a else 0
        code_b = st.selectbox('Ikinci pilot', codes, index=default_b, key='compare_driver_b_v18')
    if code_a == code_b:
        st.warning('Iki farkli pilot sec.')
        return
    row_a = table[table['Pilot'].astype(str) == code_a].iloc[0]
    row_b = table[table['Pilot'].astype(str) == code_b].iloc[0]
    rank_a, rank_b = _position_v18(row_a.get('Sıra', row_a.get('SÄ±ra'))), _position_v18(row_b.get('Sıra', row_b.get('SÄ±ra')))
    info_a, info_b = directory_driver_by_code(code_a), directory_driver_by_code(code_b)
    team_a = str(row_a.get('Takım', row_a.get('TakÄ±m', info_a['team'])))
    team_b = str(row_b.get('Takım', row_b.get('TakÄ±m', info_b['team'])))
    colour_a, colour_b = team_colour(team_a), team_colour(team_b)
    time_keys = ['Q3', 'Q2', 'Q1', 'En Hızlı Tur', 'En HÄ±zlÄ± Tur', 'Zaman']
    time_a, time_b = _row_value_v18(row_a, time_keys), _row_value_v18(row_b, time_keys)
    leader = code_a if rank_a < rank_b else code_b if rank_b < rank_a else 'Esit'
    gap_label = 'Sira farki: ' + str(abs(rank_a - rank_b)) if rank_a < 99 and rank_b < 99 else 'Sira verisi yok'
    header_cards = st.columns(3)
    hero = [('SEANS', f"{event_name} // {latest['display_name']}", '#7dd3fc'), ('ONE CIKAN', leader, '#2ee6c9'), ('KARSILASTIRMA', gap_label, '#f7c948')]
    for col, (label, value, colour) in zip(header_cards, hero):
        with col:
            st.markdown(f"<div class='hud-card compare-mini' style='border-top:4px solid {colour}'><div class='hud-label'>{label}</div><div class='hud-value' style='font-size:1.25rem'>{html_lib.escape(value)}</div></div>", unsafe_allow_html=True)
    cards = st.columns(2)
    for col, code, row, info, team, colour, rank, time_value in [
        (cards[0], code_a, row_a, info_a, team_a, colour_a, rank_a, time_a),
        (cards[1], code_b, row_b, info_b, team_b, colour_b, rank_b, time_b),
    ]:
        portrait = current_driver_portrait(info['team'], info['image']) if info['image'] else ''
        with col:
            st.markdown(
                f"<div class='hud-card compare-driver-card' style='border-top:5px solid {colour}'><div class='compare-driver-main'>{strategy_game_image(portrait, info['name'], colour)}<div><div class='hud-label'>PILOT DOSYASI</div><div style='font-size:1.38rem;font-weight:950;color:{colour}'>{html_lib.escape(info['name'])}</div><div class='driver-meta'>{html_lib.escape(code)} · {html_lib.escape(team)}</div></div></div><div class='compare-stat-grid'><div><span>SIRA</span><b>P{rank if rank < 99 else '-'}</b></div><div><span>ANA ZAMAN</span><b>{html_lib.escape(time_value)}</b></div></div></div>",
                unsafe_allow_html=True,
            )
    detail_rows = []
    for code, row, team in [(code_a, row_a, team_a), (code_b, row_b, team_b)]:
        detail = {'Pilot': code, 'Takim': team, 'Sira': 'P' + str(_position_v18(row.get('Sıra', row.get('SÄ±ra'))))}
        for column in ['Q1', 'Q2', 'Q3', 'En Hızlı Tur', 'En HÄ±zlÄ± Tur', 'Zaman', 'Lastik', 'Puan', 'Durum']:
            if column in table.columns:
                detail[column] = row.get(column, '-')
        detail_rows.append(detail)
    st.markdown('#### Detayli seans verisi')
    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
    st.caption('HUD yalnizca FastF1 tarafindan saglanan tamamlanmis seans sonucunu kullanir; tahmini sezon ratingi kullanilmaz.')


def render_home_command_hud_v18(event_name, location_name, next_session, latest, drivers, is_live):
    latest_copy = f"{latest.get('event_name', '-') } // {latest.get('display_name', '-') }" if latest else 'Son tamamlanan seans bekleniyor'
    leader = drivers[0]['name'] if drivers else 'Veri bekleniyor'
    cards = [
        ('SON SEANS', latest_copy, '#2ee6c9'),
        ('SIRADAKI', f'{location_name} // {next_session}', '#7dd3fc'),
        ('DURUM', 'CANLI SEANS' if is_live else 'PROGRAM BEKLENIYOR', '#ff385c'),
        ('SON LIDER', leader, '#f7c948'),
    ]
    cols = st.columns(4)
    for col, (label, value, colour) in zip(cols, cards):
        with col:
            st.markdown(f"<div class='hud-card home-command-card' style='border-top:4px solid {colour}'><div class='hud-label'>{label}</div><div style='font-size:1rem;font-weight:900;margin-top:8px;line-height:1.3'>{html_lib.escape(str(value))}</div></div>", unsafe_allow_html=True)


st.markdown(r"""
<style>
/* 1.8 safe visual pass: CSS only, no layers, no animations, no request loop. */
.f1-header{background:linear-gradient(120deg,#0f1d32 0%,#101b2d 56%,#151123 100%)!important;border:1px solid #29446c!important;border-radius:18px!important;box-shadow:0 16px 36px rgba(0,0,0,.25)!important;padding:20px 24px!important;}
.f1-header h1{letter-spacing:1.7px!important;font-size:1.6rem!important;}
.paddock-topline{display:flex;align-items:center;gap:12px}.paddock-topline img{width:74px;height:auto;filter:invert(1) sepia(1) saturate(8) hue-rotate(125deg)}
.paddock-side-brand{padding:8px 6px 18px;text-align:center}.paddock-side-brand img{width:76px;filter:invert(1) sepia(1) saturate(8) hue-rotate(125deg);margin-bottom:7px}.paddock-side-brand .brand-sub{font-size:.62rem;letter-spacing:2.2px;color:#74d9ff;font-weight:900}.paddock-side-brand .brand-title{font-size:.88rem;letter-spacing:1.2px;color:#f4f8ff;font-weight:900;margin-top:3px}
section[data-testid="stSidebar"] button{border:1px solid #2b527a!important;border-left:4px solid #2d8fda!important;border-radius:12px!important;background:linear-gradient(90deg,rgba(13,34,59,.96),rgba(13,27,47,.85))!important;color:#eef7ff!important;font-weight:760!important;letter-spacing:.05px!important;box-shadow:none!important;transition:transform .15s ease,border-color .15s ease!important}
section[data-testid="stSidebar"] button:hover{border-left-color:#3be5d1!important;border-color:#3b82c4!important;transform:translateX(2px)!important}
.hud-card{border-radius:15px!important;background:linear-gradient(145deg,rgba(18,31,52,.96),rgba(13,23,39,.96))!important;border-color:#294566!important;box-shadow:0 12px 24px rgba(0,0,0,.14)!important}.hud-label{letter-spacing:1.45px!important;color:#92abd0!important}.hud-value{margin-top:8px!important}.home-command-card{min-height:100px}.compare-mini{min-height:95px}.compare-driver-card{min-height:178px}.compare-driver-main{display:flex;align-items:center;gap:15px}.compare-driver-main img{width:92px!important;height:112px!important;object-fit:contain!important}.compare-stat-grid{display:grid;grid-template-columns:1fr 1.4fr;gap:10px;margin-top:14px}.compare-stat-grid>div{background:#0b1627;border:1px solid #25405f;border-radius:10px;padding:10px}.compare-stat-grid span{display:block;color:#89a3c7;font-size:.65rem;letter-spacing:1.1px;font-weight:800}.compare-stat-grid b{display:block;color:#f5fbff;font-size:1.05rem;margin-top:4px}
.paddock-ai-intro{margin-bottom:15px!important}@media(max-width:800px){.home-command-card{min-height:78px}.compare-driver-main img{width:72px!important;height:92px!important}.f1-header{padding:16px!important}}
</style>
""", unsafe_allow_html=True)




# =========================================================
# 1.9 PROFESSIONAL NEWS + AI KNOWLEDGE + GAME PATCH
# Additive patch. It does not change replay, telemetry or FastF1 loading.
# =========================================================

F1_RECORD_FACTS_V19 = {
    'most_wins_single_season': 'Bir sezonda en cok Grand Prix galibiyeti rekoru, 2023 sezonunda 19 galibiyet alan Max Verstappen\'e aittir.',
    'most_titles': 'Dunya sampiyonlugu rekoru yedi ile Lewis Hamilton ve Michael Schumacher tarafindan paylasilir.',
    'most_wins': 'Grand Prix galibiyeti rekoru Lewis Hamilton\'a aittir.',
    'most_poles': 'Pole pozisyonu rekoru Lewis Hamilton\'a aittir.',
    'youngest_champion': 'En genc Formula 1 dunya sampiyonu Sebastian Vettel\'dir; 2010 sezonunda 23 yasindayken sampiyon oldu.',
}


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
        return {'title': 'F1 rekor arsivi', 'answer': record, 'source': 'F1 tarih arsivi'}
    historic = paddock_history_answer_v18(question)
    if historic:
        return {'title': 'F1 tarih bilgisi', 'answer': historic, 'source': 'Yerel F1 dunya sampiyonlari arsivi'}
    return paddock_assistant_answer_v19(question, year)


def render_paddock_assistant_v20():
    st.markdown('## Paddock Asistani')
    api_ready = bool(configured_openai_api_key())
    accent = '#2ee6c9' if api_ready else '#f7c948'
    mode = 'OPENAI + DOGRULANMIS VERI' if api_ready else 'F1 VERI VE TARIH ARSIVI'
    description = (
        'OpenAI baglantisi aktif. Sonuc sorularinda FastF1 verisi once gelir; genel F1 sorularinda AI yaniti devreye girer.'
        if api_ready else
        'Sonuc, pole, lastik, tarihi sampiyon ve temel rekor sorularini anahtarsiz cevaplar. Genel F1 sohbeti icin istege bagli OpenAI anahtari gerekir.'
    )
    st.markdown(f"<div class='hud-card ai-command-card' style='border-top:5px solid {accent}'><div class='hud-label'>{mode}</div><div style='font-size:1.25rem;font-weight:950;margin-top:7px'>F1 sorunu yaz, kaynakli yanit al.</div><div class='history-copy' style='margin-top:6px'>{description}</div></div>", unsafe_allow_html=True)
    if 'paddock_chat_history_v19' not in st.session_state:
        st.session_state['paddock_chat_history_v19'] = []

    examples = ['1985 sampiyonu kim?', '1 sezonda en cok galibiyet alan isim kim?', 'Pole kim?', 'Alonso kacinci oldu?']
    columns = st.columns(4)
    for col, question in zip(columns, examples):
        with col:
            if st.button(question, key='assistant_v19_' + question, use_container_width=True):
                st.session_state['paddock_pending_v19'] = question
                st.rerun()

    for item in st.session_state['paddock_chat_history_v19'][-10:]:
        with st.chat_message(item['role']):
            st.markdown(item['text'])
            if item.get('source'):
                st.caption('Kaynak: ' + item['source'])

    prompt = st.chat_input('F1 hakkinda sor... Ornek: 1 sezonda en cok galibiyet alan isim kim?')
    question = st.session_state.pop('paddock_pending_v19', '') or prompt
    if question:
        st.session_state['paddock_chat_history_v19'].append({'role': 'user', 'text': question})
        with st.chat_message('user'):
            st.markdown(question)
        with st.chat_message('assistant'):
            with st.spinner('Paddock kaynaklari kontrol ediliyor...'):
                answer = paddock_assistant_answer_v19_pro(question, 2026)
            st.markdown(answer['answer'])
            st.caption('Kaynak: ' + answer['source'])
        st.session_state['paddock_chat_history_v19'].append({'role': 'assistant', 'text': answer['answer'], 'source': answer['source']})

    if not api_ready:
        with st.expander('Genel sorular icin OpenAI baglantisi'):
            st.write('ChatGPT hesabinin kendisi siteye baglanmaz; OpenAI API anahtari gerekir. Proje klasorundeki .streamlit/secrets.toml dosyasina OPENAI_API_KEY eklediginde asistan genel F1 sorularinda OpenAI yaniti da verir. Anahtar yokken bu ekran yine kaynakli F1 veri modunda calisir.')


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


@st.cache_data(ttl=900, show_spinner=False)
def fetch_f1_news_catalog_v19(limit=30):
    """A small multi-RSS catalog. It never fabricates articles if providers are down."""
    sources = [
        ('Autosport', 'https://www.autosport.com/rss/f1/news/'),
        ('Sky Sports', 'https://www.skysports.com/rss/12433'),
        ('Motorsport', 'https://www.motorsport.com/rss/f1/news/'),
    ]
    catalog, seen = [], set()
    for source_name, url in sources:
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 FormulaPaddock/1.9'})
            with urllib.request.urlopen(request, timeout=7) as response:
                root = ET.fromstring(response.read())
            for item in root.findall('.//item'):
                title = _rss_text_v19(item, 'title')
                link = safe_external_url(_rss_text_v19(item, 'link'))
                if not title or not link:
                    continue
                fingerprint = (title.lower(), link.lower())
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                description = _rss_text_v19(item, 'description')
                description = re.sub(r'<[^>]*>', ' ', description)
                description = re.sub(r'\s+', ' ', description).strip()
                catalog.append({
                    'title': title,
                    'link': link,
                    'date': _rss_text_v19(item, 'pubDate')[:22],
                    'desc': description[:220] + ('...' if len(description) > 220 else ''),
                    'source': source_name,
                    'image': _rss_image_v19(item),
                })
                if len(catalog) >= int(limit):
                    return catalog
        except Exception as error:
            log_data_error('news catalog source', error)
            continue
    return catalog[:int(limit)]


def news_matches_team_v19(item, team_name):
    if team_name == 'Genel F1':
        return True
    team = TEAM_DIRECTORY_2026.get(team_name, {})
    words = [team_name.lower(), str(team.get('slug', '')).lower()]
    words.extend(str(driver[0]).lower().split()[-1] for driver in team.get('drivers', []))
    haystack = (str(item.get('title', '')) + ' ' + str(item.get('desc', ''))).lower()
    return any(word and word in haystack for word in words)


def render_news_centre_v19():
    render_page_header('Haber Merkezi', 'Tek bir merkezden genel F1 veya secili takiminla ilgili son haberleri takip et.', '#ff385c')
    teams = ['Genel F1'] + list(TEAM_DIRECTORY_2026.keys())
    selected = st.selectbox('Haber akisini sec', teams, key='news_team_filter_v19')
    with st.spinner('Haber akisi yenileniyor...'):
        news = fetch_f1_news_catalog_v19(30)
    filtered = [item for item in news if news_matches_team_v19(item, selected)]
    title = 'Genel F1 akisi' if selected == 'Genel F1' else selected + ' haberleri'
    st.markdown(f"<div class='hud-card news-command-card'><div class='hud-label'>HABER RADARI</div><div class='hud-value'>{html_lib.escape(title)}</div><div class='driver-meta'>{len(filtered)} haber goruntuleniyor · Kaynaklar: Autosport, Sky Sports, Motorsport</div></div>", unsafe_allow_html=True)
    if not filtered:
        st.info('Bu takim icin katalogda eslesen haber bulunamadi. Genel F1 filtresini deneyebilir veya biraz sonra yenileyebilirsin.')
        return
    for start in range(0, len(filtered), 3):
        cols = st.columns(3)
        for col, item in zip(cols, filtered[start:start + 3]):
            with col:
                image = safe_external_url(item.get('image'))
                image_html = f"<img class='news-thumb-v19' src='{html_lib.escape(image, quote=True)}' alt='' onerror=\"this.style.display='none'\">" if image else "<div class='news-thumb-v19 news-thumb-empty-v19'>F1</div>"
                st.markdown(
                    f"<div class='news-card news-card-v19'>{image_html}<div class='news-date'>{html_lib.escape(item['source'])} · {html_lib.escape(item['date'])}</div><div class='news-title'>{html_lib.escape(item['title'])}</div><div class='news-desc'>{html_lib.escape(item['desc'])}</div><a href='{html_lib.escape(item['link'], quote=True)}' target='_blank' rel='noopener noreferrer' class='news-link'>Haberi ac ↗</a></div>",
                    unsafe_allow_html=True,
                )


def draft_driver_rating_v19(code):
    known = {'VER':96,'NOR':94,'LEC':93,'HAM':92,'RUS':91,'PIA':91,'ALO':89,'SAI':88,'ANT':86,'ALB':85,'GAS':84,'HAD':82,'OCO':81,'BEA':81,'HUL':80,'LAW':80,'STR':78,'BOR':77,'COL':76,'LIN':75}
    return known.get(str(code), 75)


def paddock_draft_state_v19():
    key = 'paddock_draft_state_v19'
    if key not in st.session_state:
        st.session_state[key] = {'picks': [], 'budget': 100, 'score': None, 'message': 'Iki pilotluk kadronu sec. Butceyi asma.'}
    return st.session_state[key], key


def render_paddock_draft_game_v19():
    state, key = paddock_draft_state_v19()
    pool = []
    for team_name, team in TEAM_DIRECTORY_2026.items():
        for name, code, number, image_path in team['drivers']:
            rating = draft_driver_rating_v19(code)
            cost = max(22, int((rating - 58) * 2.1))
            pool.append({'name': name, 'code': code, 'team': team_name, 'image': image_path, 'rating': rating, 'cost': cost})
    available = [driver for driver in pool if driver['code'] not in state['picks']]
    chosen = [driver for driver in pool if driver['code'] in state['picks']]
    spent = sum(driver['cost'] for driver in chosen)
    state['budget'] = 100 - spent

    st.markdown('---')
    st.markdown('## Paddock Draft')
    st.caption('Iki pilot sec, butceyi yonet ve mini sezon simulesinde kadronun gucunu test et. Bu bir oyun simulasyonudur; gercek sampiyona sonucu degildir.')
    st.markdown(f"<div class='hud-card' style='border-top:4px solid #a78bfa'><div class='hud-label'>DRAFT ODASI</div><div class='hud-value'>Kalan butce: {state['budget']} M</div><div class='driver-meta'>Secim: {len(chosen)} / 2 · {html_lib.escape(state['message'])}</div></div>", unsafe_allow_html=True)
    if len(chosen) < 2:
        recommendations = sorted(available, key=lambda item: (-item['rating'], item['cost']))[:6]
        for start in range(0, len(recommendations), 3):
            cols = st.columns(3)
            for col, driver in zip(cols, recommendations[start:start + 3]):
                team = TEAM_DIRECTORY_2026[driver['team']]
                portrait = current_driver_portrait(driver['team'], driver['image'])
                with col:
                    st.markdown(f"<div class='hud-card' style='border-top:4px solid {team['color']};text-align:center;min-height:188px'>{strategy_game_image(portrait, driver['name'], team['color'])}<div style='font-weight:950;color:{team['color']}'>{html_lib.escape(driver['name'])}</div><div class='driver-meta'>{driver['team']} · Rating {driver['rating']} · {driver['cost']} M</div></div>", unsafe_allow_html=True)
                    disabled = driver['cost'] > state['budget']
                    if st.button('Kadroyu sec', key='draft_pick_v19_' + driver['code'], disabled=disabled, use_container_width=True):
                        state['picks'].append(driver['code'])
                        state['message'] = driver['name'] + ' kadroya eklendi.'
                        st.session_state[key] = state
                        st.rerun()
    else:
        cards = st.columns(2)
        for col, driver in zip(cards, chosen):
            team = TEAM_DIRECTORY_2026[driver['team']]
            portrait = current_driver_portrait(driver['team'], driver['image'])
            with col:
                st.markdown(f"<div class='hud-card' style='border-left:5px solid {team['color']};display:flex;gap:14px;align-items:center'>{strategy_game_image(portrait, driver['name'], team['color'])}<div><div class='hud-label'>KADRO PILOTU</div><div style='font-size:1.22rem;font-weight:950;color:{team['color']}'>{html_lib.escape(driver['name'])}</div><div class='driver-meta'>Rating {driver['rating']} · Maliyet {driver['cost']} M</div></div></div>", unsafe_allow_html=True)
        if state['score'] is None:
            if st.button('Uc yarislik mini sezonu simule et', key='draft_simulate_v19', use_container_width=True):
                seed = sum(ord(ch) for code in state['picks'] for ch in code)
                points = sum(item['rating'] for item in chosen) + (seed % 31)
                state['score'] = points
                state['message'] = 'Mini sezon tamamlandi. Kadrondaki pilotlarin ratingi ve takim uyumu puana donustu.'
                st.session_state[key] = state
                st.rerun()
        else:
            grade = 'A' if state['score'] >= 205 else 'B' if state['score'] >= 185 else 'C'
            st.success(f"Mini sezon sonucu: {state['score']} puan · Takim notu: {grade}")
            st.caption('Iyi bir Draft kadrosu rating, maliyet ve pilot uyumunu dengeler.')
    if st.button('Drafti sifirla', key='draft_reset_v19'):
        st.session_state.pop(key, None)
        st.rerun()


def render_games_hub():
    st.markdown('## Oyun Merkezi')
    st.caption('Tum oyunlar acikca simulasyon olarak etiketlenir. Gercek veriler sadece bilgi ve tahmin alanlarinda kullanilir.')
    cards = [
        ('GUNLUK BULMACA', 'Stewarlde', 'Alti tahminde gunun F1 pilotunu bul.', '#ff385c', 'Stewarlde ac', 'stewarlde'),
        ('GUNLUK QUIZ', 'GridMaster', 'Kisa F1 testiyle puanini yukseltebilirsin.', '#f7c948', 'GridMaster ac', 'gridmaster'),
        ('KARIYER OYUNU', 'Takim Patronu Kariyeri', 'Pilot sec, sezonunu kur ve yarislari yonet.', '#2ee6c9', 'Kariyeri baslat', 'team_manager'),
        ('GERCEK YARIS TAHMINI', 'Paddock Tahmin', 'Pole ve podyum tahminini tamamlanmis sonuc ile karsilastir.', '#7dd3fc', 'Tahmin oyununu ac', 'predictor'),
        ('YENI OYUN', 'Paddock Draft', 'Butceyle iki pilot sec, mini sezonda kadronu test et.', '#a78bfa', 'Draft odasini ac', 'draft'),
    ]
    for start in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, card in zip(cols, cards[start:start + 3]):
            label, title, copy, colour, button_text, page = card
            with col:
                st.markdown(f"<div class='hud-card game-choice-v19' style='border-top:4px solid {colour}'><div class='hud-label'>{label}</div><div style='font-size:1.26rem;font-weight:950;margin-top:7px'>{title}</div><div class='history-copy' style='margin-top:8px'>{copy}</div></div>", unsafe_allow_html=True)
                if st.button(button_text, key='games_v19_' + page, use_container_width=True):
                    st.session_state['page'] = page
                    st.rerun()
    render_data_trust_hud()
    render_pitwall_challenge_game()


st.markdown(r"""
<style>
/* 1.9: safe component styling only. No fixed overlays, canvas or animations. */
.ai-command-card{margin-bottom:16px!important}.ai-command-card .history-copy{max-width:900px}.news-command-card{margin:12px 0 18px!important}.news-card-v19{min-height:340px!important;display:flex;flex-direction:column;gap:7px}.news-thumb-v19{width:100%;height:118px;object-fit:cover;border-radius:10px;border:1px solid #2b4669;background:#0b1627}.news-thumb-empty-v19{display:grid;place-items:center;font-size:2rem;font-weight:950;color:#2ee6c9;background:linear-gradient(135deg,#112846,#0c182b)}.news-card-v19 .news-title{margin-top:2px}.news-card-v19 .news-desc{flex:1}.game-choice-v19{min-height:150px}.stChatMessage{border:1px solid rgba(56,108,160,.35);border-radius:14px;padding:8px 12px}
section[data-testid="stSidebar"] .stButton,section[data-testid="stSidebar"] div[data-testid="stButton"]{width:100%!important;margin:0!important}section[data-testid="stSidebar"] .stButton>button,section[data-testid="stSidebar"] div[data-testid="stButton"]>button{width:100%!important;min-height:48px!important;padding:0 16px!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;gap:8px!important;line-height:1.1!important}section[data-testid="stSidebar"] .stButton>button p,section[data-testid="stSidebar"] div[data-testid="stButton"]>button p{width:100%!important;margin:0!important;text-align:left!important;white-space:normal!important}section[data-testid="stSidebar"] [data-testid="stExpander"]{border-radius:12px!important;overflow:hidden!important}section[data-testid="stSidebar"] [data-testid="stExpander"] summary{min-height:48px!important;display:flex!important;align-items:center!important;padding-left:14px!important}
</style>
""", unsafe_allow_html=True)


st.markdown("""
<div class="f1-header">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap">
        <div class="paddock-topline">
            <img src="https://upload.wikimedia.org/wikipedia/commons/3/33/F1.svg" alt="Formula Paddock">
            <div><div class="hud-label">FORMULA PADDOCK</div><h1 style="margin:3px 0 0">DATA // STRATEGY // RACE</h1><p>Dogurlanmis seans sonuclari, telemetri ve oyun merkezi</p></div>
        </div>
        <div style="text-align:right"><div class="hud-label">BETA 1.8 // PADDOCK EDITION</div><div style="color:#6ee7b7;font-size:.8rem;font-weight:850;margin-top:5px">● FASTF1 HAZIR · VERI ODAKLI</div></div>
    </div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# SOL MENÜ (SIDEBAR & NAVİGASYON)
# ==========================================

st.sidebar.markdown("""
<div class="paddock-side-brand">
    <img src="https://upload.wikimedia.org/wikipedia/commons/3/33/F1.svg" alt="Formula Paddock">
    <div class="brand-title">PADDOCK CONTROL</div>
    <div class="brand-sub">DATA · STRATEGY · PLAY</div>
</div>
""", unsafe_allow_html=True)

# 1. ANA SAYFA VE HABERLER BUTONU
if st.sidebar.button("🏠 Ana Sayfa & Haberler", use_container_width=True):
    st.session_state['page'] = 'home'

# NEWS CENTRE
if st.sidebar.button('📰 Haberler // 30 Haber', use_container_width=True):
    st.session_state['page'] = 'news'

# 2. TELEMETRİ SEANS AYARLARI
with st.sidebar.expander("📊 TELEMETRİ SEANS AYARLARI", expanded=(st.session_state['page'] == 'telemetry')):
    year = st.number_input("Sezon Yılı", min_value=2018, max_value=2026, value=2026)

    # Kapalı expander'ın içi de Streamlit tarafından çalıştırılır. Bu çağrıyı
    # burada koşulsuz bırakmak, ana sayfayı açarken FastF1'e bağlanıp tüm siteyi
    # beyaz ekranda bekletiyordu.
    if st.session_state['telemetry_schedule_requested']:
        gp_list = get_season_schedule(year)
        telemetry_schedule_missing = not gp_list
        if telemetry_schedule_missing:
            gp_list = ["Takvim verisi bekleniyor"]
            st.caption("Takvim geçici olarak alınamadı; biraz sonra yeniden deneyebilirsin.")
    else:
        gp_list = ["Önce takvim verisini yükle"]
        telemetry_schedule_missing = True
        st.caption("Ana sayfanın hızlı açılması için takvim isteğe bağlı yüklenir.")
        if st.button("📥 Telemetri takvimini yükle", key="load_telemetry_schedule", use_container_width=True):
            st.session_state['telemetry_schedule_requested'] = True
            st.rerun()
    default_gp_idx = 0
    for idx, gp_item in enumerate(gp_list):
        if "Hungary" in gp_item or "Hungarian" in gp_item:
            default_gp_idx = idx
            break

    gp = st.selectbox("Grand Prix", gp_list, index=default_gp_idx)
    session_type = st.selectbox("Seans", ["Q", "R", "FP1", "FP2", "FP3"])

    target_q = None
    q_sub_session = None
    if session_type == "Q":
        st.markdown("---")
        q_sub_session = st.selectbox(
            "⏱️ Sıralama Elemeleri:",
            ["Q3 (Final / Pole Mücadelesi)", "Q2", "Q1", "Tüm Sıralama Seansı"]
        )
        if "Q3" in q_sub_session:
            target_q = "Q3"
        elif "Q2" in q_sub_session:
            target_q = "Q2"
        elif "Q1" in q_sub_session:
            target_q = "Q1"

    st.markdown("---")
    analiz_turu = st.radio(
        "📌 Görünüm Seçiniz:",
        [
            "🗺️ Kuş Bakışı Pist Dominasyonu",
            "🏎️ 2D Tur Düellosu",
            "🛑 Telemetri & Fren Analizi",
            "📊 Top Speed & SÜRÜCÜ Tablosu",
            "🛞 Lastik Stratejisi & Stintler"
        ]
    )
    
    st.markdown("---")
    if st.button("⚡ Analiz Modunu Çalıştır", use_container_width=True, disabled=telemetry_schedule_missing):
        st.session_state['page'] = 'telemetry'

# 3. SEANS TAKİBİ VE YENİ MERKEZLER
if st.sidebar.button("📡 Seans Takibi", use_container_width=True):
    st.session_state['page'] = 'live'

if st.sidebar.button("🏁 Takvim & Pistler", use_container_width=True):
    st.session_state['page'] = 'calendar'

if st.sidebar.button("📅 Hafta Sonu Merkezi", use_container_width=True):
    st.session_state['page'] = 'weekend'

if st.sidebar.button("📖 Yaris Hikayesi", use_container_width=True):
    st.session_state['page'] = 'story'

if st.sidebar.button("⚔️ Pilot Karsilastirma", use_container_width=True):
    st.session_state['page'] = 'compare'

if st.sidebar.button("🎓 F1 Baslangic Garaji", use_container_width=True):
    st.session_state['page'] = 'learn'

if st.sidebar.button("⭐ Favori Paddock", use_container_width=True):
    st.session_state['page'] = 'favourites'

if st.sidebar.button("👥 2026 Takımlar & Pilotlar", use_container_width=True):
    st.session_state['page'] = 'teams'

if st.sidebar.button("🏆 Şampiyona Merkezi", use_container_width=True):
    st.session_state['page'] = 'standings'

if st.sidebar.button("🏎️ F2 & F3 Takip", use_container_width=True):
    st.session_state['page'] = 'f2f3'

if st.sidebar.button("❓ F1 Sözlüğü", use_container_width=True):
    st.session_state['page'] = 'glossary'

if st.sidebar.button("🧠 Paddock Asistanı", use_container_width=True):
    st.session_state['page'] = 'assistant'

if st.sidebar.button("🎮 Oyun Merkezi", use_container_width=True):
    st.session_state['page'] = 'games'

with st.sidebar.expander("⭐ Hızlı Favori", expanded=False):
    favourite_team = st.selectbox("Takım", list(TEAM_DIRECTORY_2026.keys()), key="favourite_team")
    favourite_drivers = TEAM_DIRECTORY_2026[favourite_team]['drivers']
    favourite_driver = st.selectbox("Pilot", [driver[0] for driver in favourite_drivers], key="favourite_driver")
    st.caption(f"Favorin: {favourite_team} — {favourite_driver}")

st.sidebar.markdown("""
<div style="background: #181820; border: 1px solid #2A2A36; border-radius: 10px; padding: 10px; margin-top: 15px; text-align: center;">
    <div style="font-size: 0.7rem; color: #8E8E9F; font-weight: 600;">SİSTEM DURUMU</div>
    <div style="font-size: 0.8rem; color: #00FF66; font-weight: 700;">🟢 FastF1 Engine Active</div>
</div>
""", unsafe_allow_html=True)

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


def table_column_v20(frame, starts_with):
    for column in getattr(frame, 'columns', []):
        if str(column).lower().startswith(starts_with.lower()):
            return column
    return None


def table_value_v20(row, frame, starts_with, default='-'):
    column = table_column_v20(frame, starts_with)
    if not column:
        return default
    value = row.get(column, default)
    return repair_text_v20(value) if value not in (None, '', 'nan') else default


def news_item_is_f1_v20(item, link, title, description):
    categories = ' '.join(
        str(node.text or '')
        for node in item.findall('category')
    )
    haystack = ' '.join([link, title, description, categories]).lower()
    return '/f1/' in haystack or 'formula 1' in haystack or 'formula1' in haystack or haystack.startswith('f1 ')


@st.cache_data(ttl=86400, show_spinner=False)
def translate_news_text_v20(text):
    clean = repair_text_v20(text).strip()
    if not clean:
        return ''
    return repair_text_v20(translate_to_tr(clean))


@st.cache_data(ttl=900, show_spinner=False)
def fetch_f1_news_catalog_v20(limit=30):
    """Turkish-first feed. English sources only fill the gap and are translated on display."""
    sources = [
        ('Motorsport Türkiye', 'https://tr.motorsport.com/rss/', 'tr'),
        ('Autosport', 'https://www.autosport.com/rss/f1/news/', 'en'),
        ('Sky Sports', 'https://www.skysports.com/rss/12433', 'en'),
        ('Motorsport', 'https://www.motorsport.com/rss/f1/news/', 'en'),
    ]
    catalog, seen = [], set()
    for source_name, url, language in sources:
        try:
            request = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 FormulaPaddock/2.0'},
            )
            with urllib.request.urlopen(request, timeout=7) as response:
                root = ET.fromstring(response.read())
            for item in root.findall('.//item'):
                raw_title = _rss_text_v19(item, 'title')
                raw_link = safe_external_url(_rss_text_v19(item, 'link'))
                raw_description = re.sub(r'<[^>]*>', ' ', _rss_text_v19(item, 'description'))
                raw_description = re.sub(r'\s+', ' ', raw_description).strip()
                if not raw_title or not raw_link:
                    continue
                if source_name == 'Motorsport Türkiye' and not news_item_is_f1_v20(item, raw_link, raw_title, raw_description):
                    continue
                fingerprint = (raw_title.lower(), raw_link.lower())
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                catalog.append({
                    'title': repair_text_v20(raw_title),
                    'link': raw_link,
                    'date': repair_text_v20(_rss_text_v19(item, 'pubDate'))[:30],
                    'desc': repair_text_v20(raw_description)[:260],
                    'source': source_name,
                    'language': language,
                    'image': _rss_image_v19(item),
                })
                if len(catalog) >= int(limit):
                    return catalog
        except Exception as error:
            log_data_error('news catalog v2', error)
    return catalog[:int(limit)]


def localise_news_item_v20(item):
    if item.get('language') == 'tr':
        return dict(item)
    local = dict(item)
    local['title'] = translate_news_text_v20(item.get('title', ''))
    local['desc'] = translate_news_text_v20(item.get('desc', ''))
    return local


def render_news_centre_v20():
    render_page_header(
        'Haber Merkezi',
        'Türkçe Formula 1 haberleri, kapak seçkisi ve takımına göre filtrelenmiş akış.',
        '#ff385c',
    )
    teams = ['Genel F1'] + list(TEAM_DIRECTORY_2026.keys())
    selected = st.selectbox('\u0130zlemek istedi\u011fin ak\u0131\u015f', teams, key='news_team_filter_v20')
    with st.spinner('Türkçe haber akışı hazırlanıyor...'):
        raw_news = fetch_f1_news_catalog_v20(30)
    filtered = [item for item in raw_news if news_matches_team_v19(item, selected)]
    localized = [localise_news_item_v20(item) for item in filtered]
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
    remaining = localized[1:]
    for start in range(0, len(remaining), 3):
        columns = st.columns(3)
        for column, item in zip(columns, remaining[start:start + 3]):
            with column:
                image = safe_external_url(item.get('image'))
                media = (
                    f"<img class='news-thumb-v19' src='{html_lib.escape(image, quote=True)}' alt='' onerror=\"this.style.display='none'\">"
                    if image else "<div class='news-thumb-v19 news-thumb-empty-v19'>F1</div>"
                )
                st.markdown(
                    f"<div class='news-card news-card-v20'>{media}<div class='news-date'>{html_lib.escape(item.get('source', 'Kaynak'))} · {html_lib.escape(item.get('date', ''))}</div>"
                    f"<div class='news-title'>{html_lib.escape(item.get('title', ''))}</div><div class='news-desc'>{html_lib.escape(item.get('desc', ''))}</div>"
                    f"<a href='{html_lib.escape(item.get('link', '#'), quote=True)}' target='_blank' rel='noopener noreferrer' class='news-link'>Haberi aç ↗</a></div>",
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
    render_page_header(
        'Yarış Hikâyesi',
        'Tabloyu tek başına bırakmaz: kazanan, podyum, yükseliş, pitler ve FIA notlarını Türkçe bir yarış akışına dönüştürür.',
        '#ff385c',
    )
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
        st.dataframe(pd.DataFrame(points).rename(columns={'position':'Sıra', 'code':'Pilot', 'team':'Takım', 'points':'Puan'}), use_container_width=True, hide_index=True)
    with st.expander('Tam yarış sonucu', expanded=False):
        render_html_hud(session_leaderboard_html(table, f'{selected} // YARIŞ SONUCU'), height=leaderboard_component_height(table), scrolling=False)


def render_learning_centre_v20():
    render_page_header(
        'F1 Başlangıç Garajı',
        'Sözlük değil: Formula 1’i ilk kez izleyen biri için yarış izleme rehberi, mini kararlar ve pratik yol haritası.',
        '#f7c948',
    )
    st.markdown("<div class='hud-card learning-hero-v20'><div class='hud-label'>F1'E BAŞLA // 5 DAKİKALIK ROTA</div><div class='hud-value'>Önce yarışı anla, sonra veriyi oku.</div><div class='history-copy'>Buradaki kartlar terim ezberletmez; bir hafta sonunda ekranda neye bakacağını öğretir.</div></div>", unsafe_allow_html=True)
    tracks = [
        ('1', 'Hafta sonu', 'FP1–FP3 hazırlıktır. Sıralama başlangıç sırasını, yarış ise puanları belirler.', '#7dd3fc'),
        ('2', 'Start ve ilk tur', 'İlk virajda konum kazanmak önemlidir; ama lastiği gereksiz yıpratmak sonraki turları zorlaştırır.', '#ff385c'),
        ('3', 'Lastik kararı', 'Soft hız verir, Hard uzun sürer. Doğru seçim pist sıcaklığına ve pit penceresine bağlıdır.', '#f7c948'),
        ('4', 'Pit duvarı', 'Takım, trafiği ve lastik ömrünü izleyerek pit zamanını seçer. Bir tur erken veya geç karar sonucu değiştirir.', '#2ee6c9'),
        ('5', 'Geçiş ve enerji', 'Düzlükte Straight Mode, mücadelede Overtake Mode hız avantajı için kullanılır.', '#a78bfa'),
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
        'Kim önde?': 'Seans Takibi ve Hafta Sonu Merkezi ile sıralama, tur ve farkları takip et.',
        'Lastikler ne durumda?': 'Yarış Hikâyesi ve Lastik Stratejisi ekranında hamur, stint ve pit geçişlerini izle.',
        'Neden pit yaptılar?': 'Pit zamanı; lastik aşınması, trafik, hava ve rakibin hamlesiyle birlikte değerlendirilir.',
        'İki pilot arasındaki fark nerede?': 'Pilot Karşılaştırma bölümünde tur, sektör, fren ve gaz verilerini aç.',
    }
    st.markdown(f"<div class='hud-card' style='border-left:4px solid #f7c948'><div class='hud-label'>SANA ÖNERİ</div><div class='history-copy' style='margin-top:7px'>{html_lib.escape(watch_copy[watch])}</div></div>", unsafe_allow_html=True)
    buttons = st.columns(3)
    with buttons[0]:
        if st.button('Hafta Sonu Merkezini aç', key='learn_weekend_v20', use_container_width=True):
            st.session_state['page'] = 'weekend'
            st.rerun()
    with buttons[1]:
        if st.button('Yarış Hikâyesini aç', key='learn_story_v20', use_container_width=True):
            st.session_state['page'] = 'story'
            st.rerun()
    with buttons[2]:
        if st.button('60+ terimlik sözlüğe git', key='learn_glossary_v20', use_container_width=True):
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
.news-feature-v20{display:grid;grid-template-columns:minmax(240px,.9fr) minmax(0,1.1fr);gap:18px;align-items:stretch;padding:16px;border:1px solid #325174;border-left:5px solid #ff385c;border-radius:15px;background:linear-gradient(135deg,#101c2d,#111a28);margin:18px 0}.news-feature-image-v20{width:100%;height:230px;object-fit:cover;border-radius:11px;border:1px solid #29435f;background:#0b1422}.news-feature-copy-v20{display:flex;flex-direction:column;gap:9px;justify-content:center}.news-feature-title-v20{font-size:1.45rem;font-weight:950;line-height:1.22;color:#f6f9ff}.news-card-v20{min-height:355px!important}.story-lead-v20{padding:18px;border:1px solid #3a526f;border-left:5px solid #ff385c;border-radius:14px;background:linear-gradient(135deg,#131b2a,#0f1928);margin:16px 0}.story-lead-title-v20{font-size:1.38rem;font-weight:950;line-height:1.3;margin:7px 0}.story-metric-v20{min-height:112px}.story-metric-value-v20{font-size:1.23rem;font-weight:950;margin-top:8px}.story-note-v20{border:1px solid #314964;border-left:4px solid #ff385c;border-radius:10px;background:#111b2a;padding:12px;margin-bottom:8px;line-height:1.5}.learning-hero-v20{border-top:5px solid #f7c948;margin-bottom:18px}.learning-step-v20{min-height:170px;position:relative;overflow:hidden}.learning-number-v20{font-size:2.7rem;font-weight:950;line-height:1;color:rgba(255,255,255,.14);margin-bottom:9px}
@media(max-width:800px){.news-feature-v20{grid-template-columns:1fr}.news-feature-image-v20{height:190px}.story-metric-value-v20{font-size:1.05rem}.learning-step-v20{min-height:auto}.stButton>button{min-height:46px!important}}
</style>
""", unsafe_allow_html=True)


# =========================================================
# 2.1 GAMES + SIDEBAR HUD PATCH
# Historical Stewarlde uses an external historical-results catalog.  It does
# not fabricate old driver statistics when the catalog cannot be reached.
# =========================================================

STEWARDLE_HISTORY_SOURCE_V21 = 'Jolpica historical F1 database'


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


def stewarlde_current_roster_v21():
    """Uses the local current-grid package only as an offline fallback mode."""
    rows = []
    for driver in stewarlde_drivers():
        rows.append({
            'name': driver['name'],
            'code': driver['code'],
            'team': driver['team'],
            'nation': driver['nation'],
            'wins': 0,
            'champion': 1 if _stewarlde_safe_int_v21(driver.get('titles'), 0) > 0 else 0,
            'standing': 99,
            'points': 0,
            'season': 2026,
            'photo': driver.get('photo', ''),
        })
    return sorted(rows, key=lambda row: row['name'])


def stewarlde_cell_v21(value, target, numeric=False):
    if str(value) == str(target):
        return 'match', '\u2713'
    if numeric:
        try:
            return ('near', '\u2191' if int(value) < int(target) else '\u2193')
        except (TypeError, ValueError):
            pass
    return 'miss', '\u2014'


def stewarlde_target_index_v21(length, mode, season, round_number):
    if length < 1:
        return 0
    if mode == 'Gunluk':
        return (datetime.date.today().toordinal() + int(season)) % length
    # A deterministic prime step lets unlimited rounds circulate through the
    # actual season roster without relying on browser randomness.
    return ((int(round_number) - 1) * 11 + int(season)) % length


def stewarlde_profile_v21(driver, colour):
    photo = safe_external_url(driver.get('photo', ''))
    if photo:
        visual = (
            f"<img src='{html_lib.escape(photo, quote=True)}' alt='{html_lib.escape(driver['name'])}' "
            "style='width:112px;height:142px;object-fit:contain;object-position:center bottom' "
            "onerror=\"this.style.display='none'\">"
        )
    else:
        initials = ''.join(piece[:1] for piece in str(driver['name']).split()[-2:]).upper()
        visual = (
            f"<div class='stewarlde-id-v21' style='border-color:{colour};color:{colour}'>"
            f"{html_lib.escape(initials)}</div>"
        )
    return (
        f"<div class='hud-card' style='border-left:5px solid {colour};margin-top:14px'>"
        "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>"
        + visual
        + "<div><div class='hud-label'>BULMACA PILOTU</div>"
        + f"<div style='font-size:1.62rem;font-weight:950;color:{colour};margin-top:4px'>{html_lib.escape(driver['name'])}</div>"
        + f"<div class='driver-meta' style='margin-top:7px'>{html_lib.escape(driver['team'])} \u00b7 {html_lib.escape(driver['nation'])}</div>"
        + f"<div class='history-copy' style='margin-top:8px'>Sezon: {driver['season']} \u00b7 Sezon galibiyeti: {driver['wins']} \u00b7 Sezon sampiyonlugu: {driver['champion']}</div>"
        + "</div></div></div>"
    )


def render_stewarlde_v21():
    st.markdown('## \U0001f3ae Stewarlde')
    st.caption('2010-2025 tamamlanmis sezonlarindan gercek surucu ve sonuc verileriyle F1 bulmacasi.')

    top_a, top_b = st.columns([1, 1])
    with top_a:
        mode = st.radio('Oyun modu', ['Gunluk', 'Sinirsiz'], horizontal=True, key='stewarlde_mode_v21')
    with top_b:
        season = st.selectbox('Tarihsel sezon', list(range(2025, 2009, -1)), key='stewarlde_season_v21')

    state_key = 'stewarlde_state_v21'
    if state_key not in st.session_state:
        st.session_state[state_key] = {'scope': None, 'round': 1, 'guesses': [], 'finished': False}
    game = st.session_state[state_key]
    scope = f'{mode}:{season}'
    if game.get('scope') != scope:
        game = {'scope': scope, 'round': 1, 'guesses': [], 'finished': False}
        st.session_state[state_key] = game

    with st.spinner('Tarihsel grid dogrulaniyor...'):
        drivers = fetch_stewarlde_historic_roster_v21(season)
    if not drivers:
        st.error('Bu sezonun dogrulanmis surucu paketi su an yuklenemedi. Baglantini kontrol edip Yeniden dene ile tekrar deneyebilirsin; oyun uydurma tarihsel veri gostermez.')
        if st.button('Yeniden dene', key='stewarlde_retry_v21'):
            fetch_stewarlde_historic_roster_v21.clear()
            st.rerun()
        return

    target = drivers[stewarlde_target_index_v21(len(drivers), mode, season, game['round'])]
    st.markdown(
        "<div class='hud-card stewarlde-brief-v21'><div class='hud-label'>TARIHSEL PADDOCK BULMACASI</div>"
        f"<div class='history-copy' style='margin-top:7px'>{season} sezonunun resmi surucu klasmanindan bir pilotu bul. "
        "Yesil dogru; sari sayisal yon ipucu; gri eslesme yok demek. Galibiyet, sampiyonluk bilgisinin hemen onundedir.</div>"
        f"<div class='driver-meta' style='margin-top:8px'>Mod: {html_lib.escape(mode)} \u00b7 Sinirsiz tur: {game['round'] if mode == 'Sinirsiz' else '-'} \u00b7 Kaynak: {STEWARDLE_HISTORY_SOURCE_V21}</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['code'] not in used]
        pick = st.selectbox(
            'Pilot tahminin', options,
            format_func=lambda item: f"{item['name']} ({item['team']})",
            key=f"stewarlde_pick_v21_{scope}_{game['round']}_{len(game['guesses'])}",
        )
        if st.button('Tahmini gonder', type='primary', use_container_width=True, key=f"stewarlde_submit_v21_{scope}_{game['round']}_{len(game['guesses'])}"):
            game['guesses'].append(pick['code'])
            game['finished'] = pick['code'] == target['code'] or len(game['guesses']) >= 6
            st.session_state[state_key] = game
            st.rerun()

    lookup = {driver['code']: driver for driver in drivers}
    if game['guesses']:
        rows = []
        for code in game['guesses']:
            guess = lookup.get(code)
            if not guess:
                continue
            values = [
                ('Pilot', guess['name'], guess['code'] == target['code'], ''),
                ('Takim', guess['team'], guess['team'] == target['team'], ''),
                ('Ulke', guess['nation'], guess['nation'] == target['nation'], ''),
                ('Galibiyet', guess['wins'], *stewarlde_cell_v21(guess['wins'], target['wins'], True)),
                ('Sampiyonluk', guess['champion'], *stewarlde_cell_v21(guess['champion'], target['champion'], True)),
                ('Klasman', guess['standing'], *stewarlde_cell_v21(guess['standing'], target['standing'], True)),
            ]
            cells = []
            for label, value, status, hint in values:
                css = 'match' if status is True or status == 'match' else 'near' if status == 'near' else 'miss'
                cells.append(
                    f"<div class='stewarlde-cell-v21 {css}'><small>{html_lib.escape(label)}</small>"
                    f"<b>{html_lib.escape(str(value))}</b><i>{html_lib.escape(str(hint))}</i></div>"
                )
            rows.append("<div class='stewarlde-row-v21'>" + ''.join(cells) + '</div>')
        st.markdown("<div class='stewarlde-table-v21'>" + ''.join(rows) + '</div>', unsafe_allow_html=True)

    if game['finished']:
        won = bool(game['guesses']) and game['guesses'][-1] == target['code']
        if won:
            st.success(f"Dogru cevap: {target['name']}. {len(game['guesses'])}/6 tahminde buldun.")
        else:
            st.error(f"Bu tur bitti. Dogru cevap: {target['name']} ({target['team']}).")
        colour = team_colour(target['team']) if target['team'] in TEAM_DIRECTORY_2026 else '#52d6ff'
        st.markdown(stewarlde_profile_v21(target, colour), unsafe_allow_html=True)
        if mode == 'Sinirsiz':
            if st.button('Yeni sinirsiz bulmaca', key=f"stewarlde_next_v21_{scope}_{game['round']}", use_container_width=True):
                st.session_state[state_key] = {'scope': scope, 'round': game['round'] + 1, 'guesses': [], 'finished': False}
                st.rerun()
        elif st.button('Bugunun tahminlerini temizle', key=f"stewarlde_reset_v21_{scope}"):
            st.session_state[state_key] = {'scope': scope, 'round': game['round'], 'guesses': [], 'finished': False}
            st.rerun()


_render_games_hub_v20 = render_games_hub


def render_games_hub_v21():
    st.markdown(
        "<div class='hud-card games-hub-v21'><div class='hud-label'>OYUN MERKEZI // 2.1</div>"
        "<div class='hud-value'>Veri, strateji ve tekrar oynanabilirlik</div>"
        "<div class='history-copy' style='margin-top:7px'>Stewarlde artik 2010-2025 tamamlanmis sezonlarini, sinirsiz modu ve resmi sezon galibiyeti alanini destekler. Diger oyunlarin mevcut kayitlari korunur.</div></div>",
        unsafe_allow_html=True,
    )
    _render_games_hub_v20()


# Keep all existing page routes and engines intact; only the renderer names change.
render_stewarlde = render_stewarlde_v21
render_games_hub = render_games_hub_v21


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


def paddock_draft_state_v22():
    key = 'paddock_draft_state_v22'
    if key not in st.session_state:
        st.session_state[key] = {
            'picks': [],
            'budget_limit': 165,
            'market_seed': 1,
            'score': None,
            'sponsor_bonus': 0,
            'season': 1,
            'message': 'Tum 22 pilot pazarda. Iki pilotunu sec; pazar sirasi yenilenebilir.',
        }
    return st.session_state[key], key


def paddock_draft_pool_v22():
    """Builds the entire local 2026 grid, rather than a six-driver shortlist."""
    pool = []
    for team_name, team in TEAM_DIRECTORY_2026.items():
        for name, code, number, image_path in team.get('drivers', []):
            rating = int(draft_driver_rating_v19(code))
            # The market is intentionally wide enough for any two-driver
            # combination, including Hamilton + Antonelli/Lawson.
            cost = max(36, min(84, int(round(24 + (rating - 70) * 2.35))))
            pool.append({
                'name': name,
                'code': code,
                'team': team_name,
                'number': str(number),
                'image': image_path,
                'rating': rating,
                'cost': cost,
            })
    return pool


def paddock_draft_market_order_v22(pool, seed):
    """Deterministic market shuffle: stable during a rerun, fresh on request."""
    def score(driver):
        code_score = sum((index + 1) * ord(letter) for index, letter in enumerate(driver['code']))
        return (code_score * 17 + int(seed) * 97) % 1009
    return sorted(pool, key=lambda driver: (score(driver), driver['name']))


def paddock_draft_card_v22(driver, selectable, disabled, key):
    team = TEAM_DIRECTORY_2026[driver['team']]
    portrait = current_driver_portrait(driver['team'], driver['image'])
    st.markdown(
        f"<div class='hud-card draft-driver-v22' style='border-top:5px solid {team['color']}'>"
        f"<div class='draft-driver-visual-v22'>{strategy_game_image(portrait, driver['name'], team['color'])}</div>"
        f"<div class='hud-label'>#{html_lib.escape(driver['number'].replace('#',''))} // RATING {driver['rating']}</div>"
        f"<div class='draft-driver-name-v22' style='color:{team['color']}'>{html_lib.escape(driver['name'])}</div>"
        f"<div class='driver-meta'>{html_lib.escape(driver['team'])} \u00b7 Kontrat: {driver['cost']} M</div></div>",
        unsafe_allow_html=True,
    )
    if selectable:
        return st.button('Kadroyu sec', key=key, disabled=disabled, use_container_width=True)
    return False


def render_paddock_draft_game_v22():
    state, state_key = paddock_draft_state_v22()
    pool = paddock_draft_pool_v22()
    lookup = {driver['code']: driver for driver in pool}
    state['picks'] = [code for code in state.get('picks', []) if code in lookup][:2]
    chosen = [lookup[code] for code in state['picks']]
    spent = sum(driver['cost'] for driver in chosen)
    remaining = max(0, int(state['budget_limit']) - spent)

    st.markdown('## Paddock Draft')
    st.caption('Butce, rating ve pilot uyumuyla kendi iki kisilik kadronu kur. Bu oyun simulasyonudur; gercek sezon sonucu degildir.')
    head_a, head_b, head_c = st.columns(3)
    with head_a:
        st.markdown(f"<div class='hud-card draft-summary-v22' style='border-top:5px solid #a78bfa'><div class='hud-label'>DRAFT BUTCESI</div><div class='hud-value'>{state['budget_limit']} M</div><div class='driver-meta'>Kalan: {remaining} M</div></div>", unsafe_allow_html=True)
    with head_b:
        st.markdown(f"<div class='hud-card draft-summary-v22' style='border-top:5px solid #2ee6c9'><div class='hud-label'>KADRO</div><div class='hud-value'>{len(chosen)} / 2 pilot</div><div class='driver-meta'>Harcanan: {spent} M</div></div>", unsafe_allow_html=True)
    with head_c:
        st.markdown(f"<div class='hud-card draft-summary-v22' style='border-top:5px solid #f7c948'><div class='hud-label'>SEZON {state['season']}</div><div class='hud-value'>Sponsor +{state.get('sponsor_bonus', 0)} M</div><div class='driver-meta'>Basarili kadro sonraki sezonu buyutur.</div></div>", unsafe_allow_html=True)

    if len(chosen) < 2:
        st.markdown(
            "<div class='hud-card draft-brief-v22'><div class='hud-label'>22 PILOTLUK KONTRAT PAZARI</div>"
            "<div class='history-copy' style='margin-top:7px'>Butun grid gorunur. Pazar yenile ile kartlarin sirasi degisir; pilotlar asla gizli altili listeye dusmez.</div></div>",
            unsafe_allow_html=True,
        )
        controls_a, controls_b = st.columns([2, 1])
        with controls_a:
            team_filter = st.selectbox('Takima gore filtrele', ['Tum takimlar'] + list(TEAM_DIRECTORY_2026.keys()), key='draft_filter_v22')
        with controls_b:
            st.write('')
            if st.button('Pazari yenile', key='draft_market_refresh_v22', use_container_width=True):
                state['market_seed'] = int(state.get('market_seed', 1)) + 1
                state['message'] = 'Pazar sirasi yenilendi. Tum 22 pilot yeniden siralandi.'
                st.session_state[state_key] = state
                st.rerun()

        market = paddock_draft_market_order_v22(pool, state.get('market_seed', 1))
        if team_filter != 'Tum takimlar':
            market = [driver for driver in market if driver['team'] == team_filter]
        for start in range(0, len(market), 3):
            columns = st.columns(3)
            for column, driver in zip(columns, market[start:start + 3]):
                with column:
                    disabled = driver['cost'] > remaining
                    picked = paddock_draft_card_v22(
                        driver,
                        selectable=True,
                        disabled=disabled,
                        key='draft_pick_v22_' + driver['code'],
                    )
                    if picked:
                        state['picks'].append(driver['code'])
                        state['message'] = driver['name'] + ' kadroya eklendi. Ikinci pilotunu sec.'
                        st.session_state[state_key] = state
                        st.rerun()
        st.caption('Pazar durumu: ' + str(len(pool)) + ' / 22 pilot. Butce yeterliyse her pilot secilebilir.')
    else:
        st.markdown('### Secilen kadro')
        columns = st.columns(2)
        for column, driver in zip(columns, chosen):
            with column:
                paddock_draft_card_v22(driver, selectable=False, disabled=False, key='')
                if st.button('Pilotu kadrodan cikar', key='draft_remove_v22_' + driver['code'], use_container_width=True):
                    state['picks'] = [code for code in state['picks'] if code != driver['code']]
                    state['score'] = None
                    state['message'] = driver['name'] + ' serbest birakildi; butce kadroya geri dondu.'
                    st.session_state[state_key] = state
                    st.rerun()

        average = round(sum(driver['rating'] for driver in chosen) / len(chosen), 1)
        synergy = max(0, 12 - abs(chosen[0]['rating'] - chosen[1]['rating']))
        st.markdown(
            f"<div class='hud-card draft-brief-v22'><div class='hud-label'>KADRO RAPORU</div><div class='hud-value'>Ortalama rating: {average} \u00b7 Uyum bonusu: +{synergy}</div>"
            "<div class='history-copy' style='margin-top:7px'>Dengeli iki pilot uyumu ve yuksek rating sponsor gelirini artirir. Yani sadece pahali isim toplamak her zaman en iyi yol degildir.</div></div>",
            unsafe_allow_html=True,
        )
        if state.get('score') is None:
            if st.button('Bes yarislk mini sezonu simule et', key='draft_simulate_v22', use_container_width=True):
                seed = sum(ord(letter) for code in state['picks'] for letter in code) + int(state['season']) * 31
                score = int(sum(driver['rating'] for driver in chosen) + synergy + (seed % 27))
                sponsor = max(8, min(32, 8 + (score - 160) // 3))
                state['score'] = score
                state['sponsor_bonus'] = sponsor
                state['message'] = f'Mini sezon tamamlandi: {score} oyun puani. Sonraki draft butcene +{sponsor} M sponsor geliri eklendi.'
                st.session_state[state_key] = state
                st.rerun()
        else:
            grade = 'A' if state['score'] >= 205 else 'B' if state['score'] >= 180 else 'C'
            st.success(f"Mini sezon sonucu: {state['score']} puan \u00b7 Takim notu: {grade} \u00b7 Sonraki butce bonusu: +{state['sponsor_bonus']} M")
            if st.button('Yeni draft sezonuna gec', key='draft_next_season_v22', use_container_width=True):
                state = {
                    'picks': [],
                    'budget_limit': min(220, int(state['budget_limit']) + int(state['sponsor_bonus'])),
                    'market_seed': int(state.get('market_seed', 1)) + 1,
                    'score': None,
                    'sponsor_bonus': 0,
                    'season': int(state['season']) + 1,
                    'message': 'Yeni sezon acildi. Sponsor geliri butcene eklendi; tum grid yeniden pazarda.',
                }
                st.session_state[state_key] = state
                st.rerun()

    st.caption(state.get('message', ''))
    if st.button('Draft kariyerini sifirla', key='draft_reset_v22'):
        st.session_state.pop(state_key, None)
        st.rerun()


def stewarlde_season_v22(mode, round_number):
    """Chooses a completed historical season automatically; no manual date picker."""
    today_number = datetime.date.today().toordinal()
    if mode == 'G\u00fcnl\u00fck':
        return 2010 + ((today_number * 17 + 5) % 16)
    return 2010 + ((today_number + int(round_number) * 7) % 16)


def render_stewarlde_v22():
    st.markdown('## \U0001f3ae Stewarlde')
    st.caption('2010-2025 tamamlanmis sezonlarindan gercek surucu ve sonuc verileriyle F1 bulmacasi.')

    mode = st.radio('Oyun modu', ['G\u00fcnl\u00fck', 'S\u0131n\u0131rs\u0131z'], horizontal=True, key='stewarlde_mode_v22')
    state_key = 'stewarlde_state_v22'
    if state_key not in st.session_state:
        st.session_state[state_key] = {'mode': None, 'day': None, 'round': 1, 'guesses': [], 'finished': False}
    game = st.session_state[state_key]
    day_key = datetime.date.today().isoformat()
    if game.get('mode') != mode or (mode == 'G\u00fcnl\u00fck' and game.get('day') != day_key):
        game = {'mode': mode, 'day': day_key, 'round': 1, 'guesses': [], 'finished': False}
        st.session_state[state_key] = game

    season = stewarlde_season_v22(mode, game['round'])
    with st.spinner('Tarihsel grid dogrulaniyor...'):
        drivers = fetch_stewarlde_historic_roster_v21(season)
    if not drivers:
        st.error('Bu turun dogrulanmis surucu paketi su an yuklenemedi. Oyun uydurma tarihsel veri gostermez.')
        if st.button('Yeniden dene', key='stewarlde_retry_v22'):
            fetch_stewarlde_historic_roster_v21.clear()
            st.rerun()
        return

    if mode == 'G\u00fcnl\u00fck':
        target_index = (datetime.date.today().toordinal() + season) % len(drivers)
    else:
        target_index = ((game['round'] - 1) * 11 + season) % len(drivers)
    target = drivers[target_index]

    hud_a, hud_b, hud_c = st.columns(3)
    with hud_a:
        st.markdown(f"<div class='hud-card stewarlde-stat-v22' style='border-top:5px solid #ff385c'><div class='hud-label'>BUGUNUN TARIHSEL SEZONU</div><div class='hud-value'>{season}</div><div class='driver-meta'>Sayfa tarafindan rastgele secildi</div></div>", unsafe_allow_html=True)
    with hud_b:
        st.markdown(f"<div class='hud-card stewarlde-stat-v22' style='border-top:5px solid #a78bfa'><div class='hud-label'>OYUN MODU</div><div class='hud-value'>{html_lib.escape(mode)}</div><div class='driver-meta'>{'Her gun tek cevap' if mode == 'G\u00fcnl\u00fck' else 'Her biten turda yeni rastgele pilot'}</div></div>", unsafe_allow_html=True)
    with hud_c:
        rights = max(0, 6 - len(game['guesses']))
        st.markdown(f"<div class='hud-card stewarlde-stat-v22' style='border-top:5px solid #2ee6c9'><div class='hud-label'>TAHMIN HAKKI</div><div class='hud-value'>{rights} / 6</div><div class='driver-meta'>{'Tur ' + str(game['round']) if mode == 'S\u0131n\u0131rs\u0131z' else 'Gunluk bulmaca'}</div></div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='hud-card stewarlde-brief-v21'><div class='hud-label'>TARIHSEL PADDOCK BULMACASI</div>"
        "<div class='history-copy' style='margin-top:7px'>Tarih secmezsin: sistem tamamlanmis 2010-2025 sezonlarindan rastgele bir grid ve pilot getirir. Yesil dogru; sari sayisal yon ipucu; gri eslesme yok demek.</div>"
        f"<div class='driver-meta' style='margin-top:8px'>Kaynak: {STEWARDLE_HISTORY_SOURCE_V21} \u00b7 Galibiyet alani sampiyonluktan once gelir.</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['code'] not in used]
        pick = st.selectbox('Pilot tahminin', options, format_func=lambda item: f"{item['name']} ({item['team']})", key=f"stewarlde_pick_v22_{mode}_{season}_{game['round']}_{len(game['guesses'])}")
        if st.button('Tahmini gonder', type='primary', use_container_width=True, key=f"stewarlde_submit_v22_{mode}_{season}_{game['round']}_{len(game['guesses'])}"):
            game['guesses'].append(pick['code'])
            game['finished'] = pick['code'] == target['code'] or len(game['guesses']) >= 6
            st.session_state[state_key] = game
            st.rerun()

    lookup = {driver['code']: driver for driver in drivers}
    if game['guesses']:
        rows = []
        for code in game['guesses']:
            guess = lookup.get(code)
            if not guess:
                continue
            values = [
                ('Pilot', guess['name'], guess['code'] == target['code'], ''),
                ('Takim', guess['team'], guess['team'] == target['team'], ''),
                ('Ulke', guess['nation'], guess['nation'] == target['nation'], ''),
                ('Galibiyet', guess['wins'], *stewarlde_cell_v21(guess['wins'], target['wins'], True)),
                ('Sampiyonluk', guess['champion'], *stewarlde_cell_v21(guess['champion'], target['champion'], True)),
                ('Klasman', guess['standing'], *stewarlde_cell_v21(guess['standing'], target['standing'], True)),
            ]
            cells = []
            for label, value, status, hint in values:
                css = 'match' if status is True or status == 'match' else 'near' if status == 'near' else 'miss'
                cells.append(f"<div class='stewarlde-cell-v21 {css}'><small>{html_lib.escape(label)}</small><b>{html_lib.escape(str(value))}</b><i>{html_lib.escape(str(hint))}</i></div>")
            rows.append("<div class='stewarlde-row-v21'>" + ''.join(cells) + '</div>')
        st.markdown("<div class='stewarlde-table-v21'>" + ''.join(rows) + '</div>', unsafe_allow_html=True)

    if game['finished']:
        won = bool(game['guesses']) and game['guesses'][-1] == target['code']
        if won:
            st.success(f"Dogru cevap: {target['name']}. {len(game['guesses'])}/6 tahminde buldun.")
        else:
            st.error(f"Bu tur bitti. Dogru cevap: {target['name']} ({target['team']}).")
        colour = team_colour(target['team']) if target['team'] in TEAM_DIRECTORY_2026 else '#52d6ff'
        st.markdown(stewarlde_profile_v21(target, colour), unsafe_allow_html=True)
        if mode == 'S\u0131n\u0131rs\u0131z':
            if st.button('Yeni rastgele pilot', key=f"stewarlde_next_v22_{season}_{game['round']}", use_container_width=True):
                st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'] + 1, 'guesses': [], 'finished': False}
                st.rerun()
        elif st.button('Gunluk tahminleri temizle', key=f"stewarlde_reset_v22_{day_key}"):
            st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'], 'guesses': [], 'finished': False}
            st.rerun()


# Existing routes remain unchanged and now call the repaired market and no-date Stewarlde.
render_paddock_draft_game_v19 = render_paddock_draft_game_v22
render_stewarlde = render_stewarlde_v22


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


STEWARDLE_PRE_2010_TITLES_V23 = {
    # Titles won before the first Stewarlde season (2010). Later titles come
    # directly from season champions returned by the historical data source.
    'michael_schumacher': 7,
    'schumacher': 7,
    'alonso': 2,
    'raikkonen': 1,
    'button': 1,
    'hamilton': 1,
}


def _stewarlde_request_json_v23(endpoint):
    request = urllib.request.Request(
        endpoint,
        headers={'User-Agent': 'FormulaPaddock/2.3 (Stewarlde historical game)'},
    )
    with urllib.request.urlopen(request, timeout=9) as response:
        return json.loads(response.read().decode('utf-8'))


@st.cache_data(ttl=60 * 60 * 24 * 14, show_spinner=False)
def fetch_stewarlde_universe_v23():
    """Loads every actual driver appearing in the 2010-2025 final standings.

    The requests are made in a small parallel group and then cached. Current
    2026 local-grid drivers are added afterwards, so the selector always
    includes the complete 2026 grid as well. We do not create fake careers
    when the source is unavailable.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    seasons = list(range(2010, 2026))

    def load_season(season):
        return season, fetch_stewarlde_historic_roster_v21(season)

    loaded = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(load_season, season) for season in seasons]
            for future in as_completed(futures):
                season, rows = future.result()
                if rows:
                    loaded[season] = rows
    except Exception as error:
        log_data_error('stewarlde universe', error)

    if not loaded:
        return []

    merged = {}
    for season in sorted(loaded):
        for row in loaded[season]:
            code = str(row.get('code', '')).strip()
            if not code:
                continue
            item = merged.setdefault(code, {
                'name': row['name'], 'code': code, 'team': row['team'],
                'nation': row['nation'], 'photo': '', 'latest_season': season,
                'season_wins': 0, 'titles_since_2010': 0,
            })
            item['season_wins'] += _stewarlde_safe_int_v21(row.get('wins'), 0)
            item['titles_since_2010'] += 1 if _stewarlde_safe_int_v21(row.get('champion'), 0) == 1 else 0
            if season >= item['latest_season']:
                item.update({
                    'name': row['name'], 'team': row['team'],
                    'nation': row['nation'], 'latest_season': season,
                })

    for row in stewarlde_current_roster_v21():
        code = str(row.get('code', '')).strip()
        if not code:
            continue
        item = merged.setdefault(code, {
            'name': row['name'], 'code': code, 'team': row['team'],
            'nation': row['nation'], 'photo': row.get('photo', ''),
            'latest_season': 2026, 'season_wins': 0, 'titles_since_2010': 0,
        })
        item['name'] = row['name']
        item['team'] = row['team']
        item['nation'] = row['nation']
        item['photo'] = row.get('photo', item.get('photo', ''))
        item['latest_season'] = max(2026, item.get('latest_season', 0))

    for item in merged.values():
        pre_titles = STEWARDLE_PRE_2010_TITLES_V23.get(item['code'], 0)
        item['titles'] = pre_titles + int(item.get('titles_since_2010', 0))
        item['wins'] = int(item.get('season_wins', 0))
        item['starts'] = None
    return sorted(merged.values(), key=lambda item: item['name'].casefold())


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def fetch_stewarlde_career_totals_v23(driver_code):
    """Gets real all-time GP starts and wins for a selected historical driver."""
    code = urllib.parse.quote(str(driver_code).strip())
    if not code:
        return {'starts': None, 'wins': None}
    try:
        base = 'https://api.jolpi.ca/ergast/f1/drivers/' + code
        all_results = _stewarlde_request_json_v23(base + '/results.json?limit=1')
        win_results = _stewarlde_request_json_v23(base + '/results/1.json?limit=1')
        all_total = all_results.get('MRData', {}).get('total')
        win_total = win_results.get('MRData', {}).get('total')
        return {
            'starts': _stewarlde_safe_int_v21(all_total, None),
            'wins': _stewarlde_safe_int_v21(win_total, None),
        }
    except Exception as error:
        log_data_error('stewarlde driver career totals', error)
        return {'starts': None, 'wins': None}


def stewarlde_stats_v23(driver):
    totals = fetch_stewarlde_career_totals_v23(driver['code'])
    return {
        'wins': totals.get('wins') if totals.get('wins') is not None else driver.get('wins', 0),
        'titles': int(driver.get('titles', 0)),
        'starts': totals.get('starts'),
    }


def stewarlde_numeric_cell_v23(value, target):
    if value is None or target is None:
        return 'miss', '\u2014'
    return stewarlde_cell_v21(value, target, True)


def stewarlde_profile_v23(driver, stats, colour):
    photo = safe_external_url(driver.get('photo', ''))
    if photo:
        visual = (
            f"<img src='{html_lib.escape(photo, quote=True)}' alt='{html_lib.escape(driver['name'])}' "
            "style='width:112px;height:142px;object-fit:contain;object-position:center bottom' "
            "onerror=\"this.style.display='none'\">"
        )
    else:
        initials = ''.join(piece[:1] for piece in str(driver['name']).split()[-2:]).upper()
        visual = f"<div class='stewarlde-id-v21' style='border-color:{colour};color:{colour}'>{html_lib.escape(initials)}</div>"
    starts = stats.get('starts')
    starts_text = str(starts) if starts is not None else 'Kaynak bekleniyor'
    return (
        f"<div class='hud-card' style='border-left:5px solid {colour};margin-top:14px'>"
        "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>" + visual +
        "<div><div class='hud-label'>BULMACA PILOTU</div>" +
        f"<div style='font-size:1.62rem;font-weight:950;color:{colour};margin-top:4px'>{html_lib.escape(driver['name'])}</div>" +
        f"<div class='driver-meta' style='margin-top:7px'>{html_lib.escape(driver['team'])} \\u00b7 {html_lib.escape(driver['nation'])}</div>" +
        f"<div class='history-copy' style='margin-top:8px'>Kariyer galibiyeti: {stats.get('wins', 0)} \\u00b7 D\\u00fcnya \\u015fampiyonlu\\u011fu: {stats.get('titles', 0)} \\u00b7 GP start\\u0131: {starts_text}</div>" +
        "</div></div></div>"
    )


def stewarlde_target_index_v23(length, mode, round_number):
    if length < 1:
        return 0
    day = datetime.date.today().toordinal()
    if mode == 'G\u00fcnl\u00fck':
        return (day * 31 + 17) % length
    return (day * 19 + int(round_number) * 37 + 11) % length


def render_stewarlde_v23():
    st.markdown('## \U0001f3ae Stewarlde')
    st.caption('2010-2026 pilot havuzundan, ger\u00e7ek kariyer istatistikleriyle F1 bulmacas\u0131.')

    mode = st.radio('Oyun modu', ['G\u00fcnl\u00fck', 'S\u0131n\u0131rs\u0131z'], horizontal=True, key='stewarlde_mode_v23')
    state_key = 'stewarlde_state_v23'
    if state_key not in st.session_state:
        st.session_state[state_key] = {'mode': None, 'day': None, 'round': 1, 'guesses': [], 'finished': False}
    game = st.session_state[state_key]
    day_key = datetime.date.today().isoformat()
    if game.get('mode') != mode or (mode == 'G\u00fcnl\u00fck' and game.get('day') != day_key):
        game = {'mode': mode, 'day': day_key, 'round': 1, 'guesses': [], 'finished': False}
        st.session_state[state_key] = game

    with st.spinner('2010-2026 pilot havuzu do\u011frulan\u0131yor...'):
        drivers = fetch_stewarlde_universe_v23()
    if not drivers:
        st.error('Tarih\u00ee pilot havuzu \u015fu an y\u00fcklenemedi. Oyun veri uydurmaz; ba\u011flant\u0131 geldi\u011finde tekrar dene.')
        if st.button('Yeniden dene', key='stewarlde_retry_v23'):
            fetch_stewarlde_universe_v23.clear()
            fetch_stewarlde_historic_roster_v21.clear()
            st.rerun()
        return

    target = drivers[stewarlde_target_index_v23(len(drivers), mode, game['round'])]
    stat_a, stat_b, stat_c, stat_d = st.columns(4)
    with stat_a:
        st.markdown(f"<div class='hud-card stewarlde-stat-v23'><div class='hud-label'>PILOT HAVUZU</div><div class='hud-value'>{len(drivers)} s\u00fcr\u00fcc\u00fc</div><div class='driver-meta'>2010-2026 ger\u00e7ek gridleri</div></div>", unsafe_allow_html=True)
    with stat_b:
        st.markdown("<div class='hud-card stewarlde-stat-v23'><div class='hud-label'>TAR\u0130H SE\u00c7\u0130M\u0130</div><div class='hud-value'>Yok</div><div class='driver-meta'>Hedef sistem taraf\u0131ndan rastgele gelir</div></div>", unsafe_allow_html=True)
    with stat_c:
        st.markdown(f"<div class='hud-card stewarlde-stat-v23'><div class='hud-label'>OYUN MODU</div><div class='hud-value'>{html_lib.escape(mode)}</div><div class='driver-meta'>{'G\u00fcnde tek bulmaca' if mode == 'G\u00fcnl\u00fck' else 'Her turda yeni rastgele pilot'}</div></div>", unsafe_allow_html=True)
    with stat_d:
        rights = max(0, 6 - len(game['guesses']))
        st.markdown(f"<div class='hud-card stewarlde-stat-v23'><div class='hud-label'>TAHM\u0130N HAKKI</div><div class='hud-value'>{rights} / 6</div><div class='driver-meta'>{'G\u00fcnl\u00fck' if mode == 'G\u00fcnl\u00fck' else 'Tur ' + str(game['round'])}</div></div>", unsafe_allow_html=True)

    st.markdown(
        "<div class='hud-card stewarlde-brief-v23'><div class='hud-label'>TARIH\u00ce PADDOCK BULMACASI</div>"
        "<div class='history-copy' style='margin-top:8px'>Sen tarih se\u00e7mezsin. Hedef pilot rastgele gelir; ancak tahmin men\u00fcs\u00fcnde 2010-2026 havuzundaki t\u00fcm pilotlar vard\u0131r. Ye\u015fil do\u011fru, sar\u0131 say\u0131sal y\u00f6n, gri e\u015fle\u015fme yok demek.</div>"
        "<div class='driver-meta' style='margin-top:8px'>Alanlar: toplam GP galibiyeti \u00b7 d\u00fcnya \u015fampiyonlu\u011fu \u00b7 GP start\u0131. Kaynak: Jolpica F1 verisi.</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['code'] not in used]
        pick = st.selectbox(
            'Pilot tahminin', options,
            format_func=lambda item: f"{item['name']} \u2014 {item['team']} ({item['latest_season']})",
            key=f"stewarlde_pick_v23_{mode}_{game['round']}_{len(game['guesses'])}",
        )
        if st.button('Tahmini g\u00f6nder', type='primary', use_container_width=True, key=f"stewarlde_submit_v23_{mode}_{game['round']}_{len(game['guesses'])}"):
            game['guesses'].append(pick['code'])
            game['finished'] = pick['code'] == target['code'] or len(game['guesses']) >= 6
            st.session_state[state_key] = game
            st.rerun()

    lookup = {driver['code']: driver for driver in drivers}
    target_stats = stewarlde_stats_v23(target) if game['guesses'] else None
    if game['guesses']:
        rows = []
        for code in game['guesses']:
            guess = lookup.get(code)
            if not guess:
                continue
            guess_stats = stewarlde_stats_v23(guess)
            values = [
                ('Pilot', guess['name'], guess['code'] == target['code'], ''),
                ('Tak\u0131m', guess['team'], guess['team'] == target['team'], ''),
                ('\u00dclke', guess['nation'], guess['nation'] == target['nation'], ''),
                ('Galibiyet', guess_stats['wins'], *stewarlde_numeric_cell_v23(guess_stats['wins'], target_stats['wins'])),
                ('\u015eampiyonluk', guess_stats['titles'], *stewarlde_numeric_cell_v23(guess_stats['titles'], target_stats['titles'])),
                ('Yar\u0131\u015f say\u0131s\u0131', guess_stats['starts'], *stewarlde_numeric_cell_v23(guess_stats['starts'], target_stats['starts'])),
            ]
            cells = []
            for label, value, status, hint in values:
                css = 'match' if status is True or status == 'match' else 'near' if status == 'near' else 'miss'
                display_value = value if value is not None else '?'
                cells.append(f"<div class='stewarlde-cell-v23 {css}'><small>{html_lib.escape(label)}</small><b>{html_lib.escape(str(display_value))}</b><i>{html_lib.escape(str(hint))}</i></div>")
            rows.append("<div class='stewarlde-row-v23'>" + ''.join(cells) + '</div>')
        st.markdown("<div class='stewarlde-table-v23'>" + ''.join(rows) + '</div>', unsafe_allow_html=True)

    if game['finished']:
        won = bool(game['guesses']) and game['guesses'][-1] == target['code']
        if won:
            st.success(f"Do\u011fru cevap: {target['name']}. {len(game['guesses'])}/6 tahminde buldun.")
        else:
            st.error(f"Bu tur bitti. Do\u011fru cevap: {target['name']} ({target['team']}).")
        colour = team_colour(target['team']) if target['team'] in TEAM_DIRECTORY_2026 else '#52d6ff'
        st.markdown(stewarlde_profile_v23(target, target_stats or {}, colour), unsafe_allow_html=True)
        if mode == 'S\u0131n\u0131rs\u0131z':
            if st.button('Yeni rastgele pilot', key=f"stewarlde_next_v23_{game['round']}", use_container_width=True):
                st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'] + 1, 'guesses': [], 'finished': False}
                st.rerun()
        elif st.button('G\u00fcnl\u00fck tahminleri temizle', key=f"stewarlde_reset_v23_{day_key}"):
            st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'], 'guesses': [], 'finished': False}
            st.rerun()


render_stewarlde = render_stewarlde_v23


st.markdown(r"""
<style>
/* A light CSS-only motion layer. No canvas, iframe, or positioned overlay. */
@keyframes paddock-grid-drift-v23{0%{background-position:0 0,0 0,0 0,0 0}50%{background-position:0 0,0 0,22px 16px,-22px -16px}100%{background-position:0 0,0 0,0 0,0 0}}
@media (prefers-reduced-motion:no-preference){[data-testid="stAppViewContainer"],.stApp{animation:paddock-grid-drift-v23 34s ease-in-out infinite!important}}
.stewarlde-stat-v23{min-height:118px!important;border-top:5px solid #52d6ff!important;background:linear-gradient(145deg,rgba(17,34,55,.96),rgba(12,21,34,.96))!important}.stewarlde-brief-v23{border-left:5px solid #ff385c!important;margin:16px 0 18px!important;background:linear-gradient(120deg,rgba(20,34,54,.96),rgba(14,24,38,.96))!important}.stewarlde-row-v23{display:grid;grid-template-columns:1.45fr 1.2fr repeat(4,1fr);gap:8px;margin:10px 0}.stewarlde-cell-v23{min-height:68px;border:1px solid #2d435c;border-radius:11px;padding:10px;background:#111b29;position:relative;box-shadow:inset 0 1px 0 rgba(255,255,255,.035)}.stewarlde-cell-v23 small{display:block;color:#9cb5d0;font-size:.67rem;font-weight:900;letter-spacing:.42px;text-transform:uppercase}.stewarlde-cell-v23 b{display:block;color:#f5f9ff;font-size:.96rem;margin-top:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stewarlde-cell-v23 i{position:absolute;right:10px;bottom:8px;font-style:normal;font-size:1rem;font-weight:950}.stewarlde-cell-v23.match{background:linear-gradient(145deg,#123f31,#103528);border-color:#45d991}.stewarlde-cell-v23.near{background:linear-gradient(145deg,#4c3d16,#392e13);border-color:#efc84a}.stewarlde-cell-v23.miss{background:linear-gradient(145deg,#29313d,#232a34);border-color:#4b5a69}
@media(max-width:900px){.stewarlde-row-v23{grid-template-columns:repeat(2,1fr)}.stewarlde-stat-v23{min-height:100px!important}}
</style>
""", unsafe_allow_html=True)



# =========================================================
# 2.4 GAME ENGINE STABILITY PATCH
# Canonical driver identities, source-only career statistics and one shared
# game HUD. This block intentionally does not touch FastF1/replay routes.
# =========================================================


def stewarlde_identity_v24(value):
    """Stable key for merging a historical driver ID with the current grid."""
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '', text.casefold())


# Jolpica IDs are only used to ask the historical source for a driver's own
# results. If an ID is unavailable, the game shows an honest dash -- never a
# guessed career total.
STEWARDLE_ACTIVE_API_IDS_V24 = {
    'RUS': 'russell', 'ANT': 'antonelli', 'HAM': 'hamilton', 'LEC': 'leclerc',
    'NOR': 'norris', 'PIA': 'piastri', 'VER': 'max_verstappen', 'HAD': 'hadjar',
    'LAW': 'lawson', 'LIN': 'lindblad', 'GAS': 'gasly', 'COL': 'colapinto',
    'OCO': 'ocon', 'BEA': 'bearman', 'SAI': 'sainz', 'ALB': 'albon',
    'HUL': 'hulkenberg', 'BOR': 'bortoleto', 'ALO': 'alonso', 'STR': 'stroll',
    'PER': 'perez', 'BOT': 'bottas',
}


@st.cache_data(ttl=60 * 60 * 24 * 14, show_spinner=False)
def fetch_stewarlde_universe_v24():
    """One unique record per real person, not per API spelling or abbreviation."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    seasons = list(range(2010, 2026))
    loaded = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fetch_stewarlde_historic_roster_v21, year): year for year in seasons}
            for future in as_completed(futures):
                year = futures[future]
                rows = future.result()
                if rows:
                    loaded[year] = rows
    except Exception as error:
        log_data_error('stewarlde 2.4 universe', error)

    if not loaded:
        return []

    merged = {}
    for year in sorted(loaded):
        for row in loaded[year]:
            identity = stewarlde_identity_v24(row.get('name'))
            api_code = str(row.get('code') or '').strip()
            if not identity or not api_code:
                continue
            item = merged.setdefault(identity, {
                'identity': identity, 'name': row['name'], 'api_code': api_code,
                'code': api_code, 'team': row['team'], 'nation': row['nation'],
                'photo': '', 'latest_season': year, 'titles_since_2010': 0,
                'local_code': '',
            })
            item['titles_since_2010'] += 1 if _stewarlde_safe_int_v21(row.get('champion'), 0) == 1 else 0
            if year >= item['latest_season']:
                item.update({'name': row['name'], 'api_code': api_code, 'code': api_code,
                             'team': row['team'], 'nation': row['nation'], 'latest_season': year})

    # Current grid enriches the same person with a portrait and current team.
    # It never creates a second record for RUS/russell, VER/max_verstappen etc.
    for row in stewarlde_current_roster_v21():
        identity = stewarlde_identity_v24(row.get('name'))
        local_code = str(row.get('code') or '').strip().upper()
        if not identity or not local_code:
            continue
        item = merged.get(identity)
        if item is None:
            api_code = STEWARDLE_ACTIVE_API_IDS_V24.get(local_code, '')
            item = {
                'identity': identity, 'name': row['name'], 'api_code': api_code,
                'code': api_code or local_code, 'team': row['team'], 'nation': row['nation'],
                'photo': row.get('photo', ''), 'latest_season': 2026,
                'titles_since_2010': 0, 'local_code': local_code,
            }
            merged[identity] = item
        else:
            item.update({
                'name': row['name'], 'team': row['team'], 'nation': row['nation'],
                'photo': row.get('photo', item.get('photo', '')),
                'local_code': local_code, 'latest_season': max(2026, item.get('latest_season', 0)),
            })

    for item in merged.values():
        api_code = item.get('api_code', '')
        item['titles'] = int(item.get('titles_since_2010', 0)) + int(STEWARDLE_PRE_2010_TITLES_V23.get(api_code, 0))
    return sorted(merged.values(), key=lambda item: item['name'].casefold())


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def fetch_stewarlde_career_totals_v24(api_code):
    """Read exact all-time starts and wins from the source, or report absent data."""
    clean_code = str(api_code or '').strip()
    if not clean_code:
        return {'starts': None, 'wins': None}
    try:
        base = 'https://api.jolpi.ca/ergast/f1/drivers/' + urllib.parse.quote(clean_code)
        starts_payload = _stewarlde_request_json_v23(base + '/results.json?limit=1')
        wins_payload = _stewarlde_request_json_v23(base + '/results/1.json?limit=1')
        return {
            'starts': _stewarlde_safe_int_v21(starts_payload.get('MRData', {}).get('total'), None),
            'wins': _stewarlde_safe_int_v21(wins_payload.get('MRData', {}).get('total'), None),
        }
    except Exception as error:
        log_data_error('stewarlde 2.4 career totals', error)
        return {'starts': None, 'wins': None}


def stewarlde_stats_v24(driver):
    totals = fetch_stewarlde_career_totals_v24(driver.get('api_code'))
    return {
        'wins': totals.get('wins'),
        'titles': int(driver.get('titles', 0)),
        'starts': totals.get('starts'),
    }


def stewarlde_profile_v24(driver, stats, colour):
    photo = safe_external_url(driver.get('photo', ''))
    visual = (
        f"<img src='{html_lib.escape(photo, quote=True)}' alt='{html_lib.escape(driver['name'])}' "
        "style='width:108px;height:138px;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\">"
        if photo else
        f"<div class='stewarlde-id-v21' style='border-color:{colour};color:{colour}'>{html_lib.escape(''.join(piece[:1] for piece in str(driver['name']).split()[-2:]).upper())}</div>"
    )
    wins = stats.get('wins')
    starts = stats.get('starts')
    wins_text = str(wins) if wins is not None else 'Kaynakta yok'
    starts_text = str(starts) if starts is not None else 'Kaynakta yok'
    return (
        f"<div class='hud-card game-result-v24' style='border-left:5px solid {colour}'>"
        "<div style='display:flex;align-items:center;gap:18px;flex-wrap:wrap'>" + visual +
        "<div><div class='hud-label'>DOĞRU CEVAP</div>" +
        f"<div style='font-size:1.58rem;font-weight:950;color:{colour};margin-top:4px'>{html_lib.escape(driver['name'])}</div>" +
        f"<div class='driver-meta' style='margin-top:7px'>{html_lib.escape(driver['team'])} · {html_lib.escape(driver['nation'])}</div>" +
        f"<div class='history-copy' style='margin-top:8px'>Kariyer galibiyeti: {wins_text} · Dünya şampiyonluğu: {stats.get('titles', 0)} · GP startı: {starts_text}</div>" +
        "</div></div></div>"
    )


def render_stewarlde_v24():
    st.markdown('## 🎮 Stewarlde')
    st.caption('2010–2026 F1 pilot havuzuyla; gerçek galibiyet, şampiyonluk ve GP start verilerine dayalı bulmaca.')
    mode = st.radio('Oyun modu', ['Günlük', 'Sınırsız'], horizontal=True, key='stewarlde_mode_v24')
    state_key = 'stewarlde_state_v24'
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
        if st.button('Yeniden dene', key='stewarlde_retry_v24'):
            fetch_stewarlde_universe_v24.clear()
            fetch_stewarlde_historic_roster_v21.clear()
            st.rerun()
        return

    target = drivers[stewarlde_target_index_v23(len(drivers), mode, game['round'])]
    cards = st.columns(4)
    values = [
        ('TEKİL PİLOT HAVUZU', f"{len(drivers)} pilot", 'Aynı kişi yalnızca bir kez listelenir'),
        ('OYUN MODU', mode, 'Günlük hedef veya sınırsız tur'),
        ('TAHMİN HAKKI', f"{max(0, 6-len(game['guesses']))} / 6", f"Tur {game['round']}"),
        ('VERİ MOTORU', 'Kaynak doğrulamalı', 'Uydurma galibiyet veya start yok'),
    ]
    for col, (label, value, note) in zip(cards, values):
        with col:
            st.markdown(f"<div class='hud-card game-stat-v24'><div class='hud-label'>{label}</div><div class='hud-value'>{html_lib.escape(value)}</div><div class='driver-meta'>{html_lib.escape(note)}</div></div>", unsafe_allow_html=True)

    st.markdown(
        "<div class='hud-card game-brief-v24'><div class='hud-label'>STEWARDLE // GERÇEK KARİYER VERİSİ</div>"
        "<div class='history-copy' style='margin-top:7px'>Pilot menüsünde 2010–2026 boyunca yarışmış tüm tekil isimler bulunur. Yeşil doğru; sarı sayısal yön ipucu; gri eşleşme yok demektir. Galibiyet ve GP startı doğrudan tarihî kaynaktan gelir.</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['identity'] not in used]
        pick = st.selectbox('Pilot tahminin', options, format_func=lambda item: f"{item['name']} — {item['team']} ({item['latest_season']})", key=f"stewarlde_pick_v24_{mode}_{game['round']}_{len(game['guesses'])}")
        if st.button('Tahmini gönder', type='primary', use_container_width=True, key=f"stewarlde_submit_v24_{mode}_{game['round']}_{len(game['guesses'])}"):
            game['guesses'].append(pick['identity'])
            game['finished'] = pick['identity'] == target['identity'] or len(game['guesses']) >= 6
            st.session_state[state_key] = game
            st.rerun()

    lookup = {driver['identity']: driver for driver in drivers}
    target_stats = stewarlde_stats_v24(target) if game['guesses'] else None
    if game['guesses']:
        rows = []
        for identity in game['guesses']:
            guess = lookup.get(identity)
            if not guess:
                continue
            stats = stewarlde_stats_v24(guess)
            cells_data = [
                ('Pilot', guess['name'], guess['identity'] == target['identity'], ''),
                ('Takım', guess['team'], guess['team'] == target['team'], ''),
                ('Ülke', guess['nation'], guess['nation'] == target['nation'], ''),
                ('Galibiyet', stats['wins'], *stewarlde_numeric_cell_v23(stats['wins'], target_stats['wins'])),
                ('Şampiyonluk', stats['titles'], *stewarlde_numeric_cell_v23(stats['titles'], target_stats['titles'])),
                ('GP startı', stats['starts'], *stewarlde_numeric_cell_v23(stats['starts'], target_stats['starts'])),
            ]
            cells = []
            for label, value, status, hint in cells_data:
                state = 'match' if status is True or status == 'match' else 'near' if status == 'near' else 'miss'
                display = value if value is not None else '—'
                cells.append(f"<div class='stewarlde-cell-v23 {state}'><small>{html_lib.escape(label)}</small><b>{html_lib.escape(str(display))}</b><i>{html_lib.escape(str(hint))}</i></div>")
            rows.append("<div class='stewarlde-row-v23'>" + ''.join(cells) + '</div>')
        st.markdown("<div class='stewarlde-table-v23'>" + ''.join(rows) + '</div>', unsafe_allow_html=True)

    if game['finished']:
        won = bool(game['guesses']) and game['guesses'][-1] == target['identity']
        if won:
            st.success(f"Doğru cevap: {target['name']}. {len(game['guesses'])}/6 tahminde buldun.")
        else:
            st.error(f"Bu tur bitti. Doğru cevap: {target['name']} ({target['team']}).")
        colour = team_colour(target['team']) if target['team'] in TEAM_DIRECTORY_2026 else '#52d6ff'
        st.markdown(stewarlde_profile_v24(target, target_stats or {}, colour), unsafe_allow_html=True)
        if mode == 'Sınırsız':
            if st.button('Yeni rastgele pilot', key=f"stewarlde_next_v24_{game['round']}", use_container_width=True):
                st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'] + 1, 'guesses': [], 'finished': False}
                st.rerun()
        elif st.button('Günlük tahminleri temizle', key=f"stewarlde_reset_v24_{day_key}"):
            st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'], 'guesses': [], 'finished': False}
            st.rerun()


def render_games_hub_v24():
    """Stable common HUD for every existing game; routes stay unchanged."""
    st.markdown('## 🎮 Oyun Merkezi')
    st.caption('Oyun sonuçları simülasyondur. Gerçek tarihî veriler yalnızca açıkça etiketlenen Stewarlde alanında kullanılır.')
    games = [
        ('TARİHÎ BULMACA', 'Stewarlde', 'Tekil sürücü kimliği, gerçek kariyer verisi ve sınırsız tur.', '#ff385c', 'Stewarlde aç', 'stewarlde'),
        ('F1 BİLGİ TESTİ', 'GridMaster', 'Kısa ve tekrar oynanabilir F1 bilgi testi.', '#f7c948', 'GridMaster aç', 'gridmaster'),
        ('KARİYER YÖNETİMİ', 'Takım Patronu', 'Pilot, lastik, tempo ve bütçe kararlarıyla kalıcı kariyer.', '#2ee6c9', 'Kariyeri aç', 'team_manager'),
        ('KADRO PAZARI', 'Paddock Draft', 'Bütün aktif grid içinden bütçene göre iki pilot seç.', '#a78bfa', 'Draftı aç', 'draft'),
        ('YARIŞ TAHMİNİ', 'Paddock Tahmin', 'Pole ve podyum tahminini gerçek sonuçla karşılaştır.', '#7dd3fc', 'Tahmini aç', 'predictor'),
        ('STRATEJİ LABI', 'Pit Wall', 'Lastik ve pit penceresi kararlarını güvenli simülasyonda dene.', '#f7c948', 'Strateji laboratuvarını aç', 'pitwall'),
    ]
    for start in range(0, len(games), 2):
        columns = st.columns(2)
        for column, game in zip(columns, games[start:start+2]):
            label, title, description, colour, button_text, page = game
            with column:
                st.markdown(f"<div class='hud-card game-card-v24' style='border-top:5px solid {colour}'><div class='hud-label'>{label}</div><div class='game-card-title-v24'>{title}</div><div class='history-copy' style='margin-top:8px'>{description}</div></div>", unsafe_allow_html=True)
                if st.button(button_text, key=f'games_v24_{page}', use_container_width=True):
                    if page == 'pitwall':
                        st.session_state['game_hub_open_pitwall_v24'] = True
                    else:
                        st.session_state['page'] = page
                        st.rerun()
    if st.session_state.get('game_hub_open_pitwall_v24'):
        render_pitwall_challenge_game()


render_stewarlde = render_stewarlde_v24
render_games_hub = render_games_hub_v24


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


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def fetch_stewarlde_career_record_v25(api_code):
    """Return exact source totals plus the earliest source race year.

    The result history is paged so long careers are not silently shortened.
    A field remains empty when Jolpica cannot verify it; we never substitute
    a hand-written career number.
    """
    clean_code = str(api_code or '').strip()
    empty = {'starts': None, 'wins': None, 'first_gp_date': None}
    if not clean_code:
        return empty
    try:
        base = 'https://api.jolpi.ca/ergast/f1/drivers/' + urllib.parse.quote(clean_code)
        starts_payload = _stewarlde_request_json_v23(base + '/results.json?limit=1')
        wins_payload = _stewarlde_request_json_v23(base + '/results/1.json?limit=1')
        starts = _stewarlde_safe_int_v21(starts_payload.get('MRData', {}).get('total'), None)
        wins = _stewarlde_safe_int_v21(wins_payload.get('MRData', {}).get('total'), None)
        first_gp_date = None
        if starts and starts > 0:
            first_dates = []
            for offset in range(0, starts, 100):
                page = _stewarlde_request_json_v23(base + f'/results.json?limit=100&offset={offset}')
                races = page.get('MRData', {}).get('RaceTable', {}).get('Races', [])
                for race in races:
                    race_date = str(race.get('date') or '').strip()
                    if re.fullmatch(r'19\d{2}|20\d{2}', race_date[:4]) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', race_date):
                        first_dates.append(race_date)
            if first_dates:
                first_gp_date = min(first_dates)
        return {'starts': starts, 'wins': wins, 'first_gp_date': first_gp_date}
    except Exception as error:
        log_data_error('stewarlde 2.5 career record', error)
        return empty


def stewarlde_stats_v25(driver):
    record = fetch_stewarlde_career_record_v25(driver.get('api_code'))
    return {
        'wins': record.get('wins'),
        'titles': int(driver.get('titles', 0)),
        'starts': record.get('starts'),
        'first_gp_date': record.get('first_gp_date'),
    }


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
    st.markdown('## 🎮 Stewarlde')
    st.caption('2010–2026 F1 pilot havuzuyla; kaynak doğrulamalı galibiyet, şampiyonluk, GP startı ve ilk GP yılı bulmacası.')
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
    cards = st.columns(4)
    values = [
        ('TEKİL PİLOT HAVUZU', f"{len(drivers)} pilot", 'Aynı kişi yalnızca bir kez listelenir'),
        ('OYUN MODU', mode, 'Günlük hedef veya sınırsız tur'),
        ('TAHMİN HAKKI', f"{max(0, 6-len(game['guesses']))} / 6", f"Tur {game['round']}"),
        ('VERİ MOTORU', 'Kaynak doğrulamalı', 'Uydurma galibiyet, start veya giriş yılı yok'),
    ]
    for col, (label, value, note) in zip(cards, values):
        with col:
            st.markdown(f"<div class='hud-card game-stat-v24'><div class='hud-label'>{label}</div><div class='hud-value'>{html_lib.escape(value)}</div><div class='driver-meta'>{html_lib.escape(note)}</div></div>", unsafe_allow_html=True)

    st.markdown(
        "<div class='hud-card game-brief-v24'><div class='hud-label'>STEWARDLE // GERÇEK KARİYER VERİSİ</div>"
        "<div class='history-copy' style='margin-top:7px'>Pilot menüsünde 2010–2026 boyunca yarışmış tüm tekil isimler bulunur. Yeşil doğru; sarı sayısal yön ipucu; gri eşleşme yok demektir. Galibiyet, şampiyonluk, GP startı ve ilk GP yılı tarihî kaynaktan gelir.</div></div>",
        unsafe_allow_html=True,
    )

    if not game['finished'] and len(game['guesses']) < 6:
        used = set(game['guesses'])
        options = [driver for driver in drivers if driver['identity'] not in used]
        pick = st.selectbox('Pilot tahminin', options, format_func=lambda item: f"{item['name']} — {item['team']} ({item['latest_season']})", key=f"stewarlde_pick_v25_{mode}_{game['round']}_{len(game['guesses'])}")
        if st.button('Tahmini gönder', type='primary', use_container_width=True, key=f"stewarlde_submit_v25_{mode}_{game['round']}_{len(game['guesses'])}"):
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
            if st.button('Yeni rastgele pilot', key=f"stewarlde_next_v25_{game['round']}", use_container_width=True):
                st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'] + 1, 'guesses': [], 'finished': False}
                st.rerun()
        elif st.button('Günlük tahminleri temizle', key=f"stewarlde_reset_v25_{day_key}"):
            st.session_state[state_key] = {'mode': mode, 'day': day_key, 'round': game['round'], 'guesses': [], 'finished': False}
            st.rerun()


render_stewarlde = render_stewarlde_v25


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


_build_stable_race_replay_payload_v25 = build_stable_race_replay_payload
_two_driver_duel_html_repaired_v25 = two_driver_duel_html_repaired


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
    return payload


def build_stable_race_replay_payload(year, event_name):
    """Use the verified FastF1 package for every available circuit, then normalize it.

    This wrapper never invents a race for a track FastF1 cannot yet provide.
    Its job is only to preserve annotations and a safe pit-lane visualization.
    """
    payload = _build_stable_race_replay_payload_v25(year, event_name)
    if not isinstance(payload, dict) or not payload.get('ok'):
        return payload
    payload = _replay_overlay_v26(payload)
    payload['version'] = '2.6'
    payload['replay_source'] = (
        'FastF1 doğrulanmış tur, sıra, pit giriş/çıkış ve lastik kaydı. '
        'Pit şeridi görünümü şematiktir; canlı GPS değildir.'
    )
    return payload


# The existing retry button calls `.clear()`. Preserve that API after wrapping.
build_stable_race_replay_payload.clear = _build_stable_race_replay_payload_v25.clear


def stable_race_replay_html(payload):
    """Canvas-only replay HUD with an explicit schematic pit lane.

    The track is a clean FastF1 telemetry lap. Pit entry/exit timestamps are
    verified session data, but their exact coordinates are not part of the
    public lap telemetry. The off-track lane is therefore visibly labelled
    *schematic* instead of being presented as GPS.
    """
    packed = json.dumps(_replay_overlay_v26(dict(payload)), ensure_ascii=False, separators=(',', ':'))
    return r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#090d14;color:#edf6ff;font-family:Inter,Segoe UI,Arial,sans-serif}.r{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#101a2b,#09101a)}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-size:14px;font-weight:950;letter-spacing:.1em}.sub{font-size:11px;color:#91a8c0;margin-top:5px}.badge{border:1px solid #365170;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.legend{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.key{border:1px solid #334d69;border-radius:99px;padding:5px 8px;font-size:10px;font-weight:850;color:#bcd0e4;background:#101d2f}.key i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}.grid{display:grid;grid-template-columns:minmax(0,1fr) 292px;gap:12px;margin-top:12px}.map{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#070c13 74%);overflow:hidden}.map canvas{width:100%;height:510px;display:block}.panel{border:1px solid #2c425d;border-radius:11px;background:#101a2a;padding:12px}.hero{border-bottom:1px solid #2b4058;padding:0 0 10px;margin-bottom:8px;min-height:74px}.hero b{font-size:21px;color:var(--team)}.hero small{display:block;color:#a9bbcd;margin-top:5px}.hero img{float:right;width:65px;height:82px;object-fit:contain;object-position:right bottom;margin:-8px -4px -2px 8px}.stat{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #26394f;font-size:12px;gap:8px}.stat span{color:#92a7bc}.pit{color:#ffd46b}.on{color:#81e6ac}.tyrebar{height:8px;border-radius:99px;background:#07101a;overflow:hidden;margin:7px 0 2px}.tyrebar i{display:block;height:100%;background:var(--tyre);width:var(--wear)}.controls,.strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn,.pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#edf6ff;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.pilot{border-left:4px solid var(--team);font-size:11px}.pilot.active{background:#1c3049;box-shadow:0 0 0 1px var(--team) inset}.slider{accent-color:#ff4051;flex:1;min-width:135px}.clock{font:900 12px ui-monospace,Consolas,monospace}.note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.grid{grid-template-columns:1fr}.map canvas{height:390px}}
</style></head><body><div class="r"><div class="top"><div><div class="title">RACE CONTROL // VERIFIED REPLAY</div><div class="sub" id="sub"></div></div><div class="badge">● DOĞRULANMIŞ YARIŞ AKIŞI</div></div><div class="legend"><span class="key"><i style="background:#45c8ff"></i>Straight Mode</span><span class="key"><i style="background:#71e6a1"></i>Overtake olasılığı</span><span class="key"><i style="background:#b79cff"></i>Pit giriş / çıkış</span><span class="key"><i style="background:#ffd46b"></i>Pit şeridi şematik</span></div><div class="grid"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn active" data-speed="1">1× Gerçek</button><button class="btn" data-speed="5">5×</button><button class="btn" data-speed="20">20×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="clock" id="clock"></span></div><div class="strip" id="strip"></div><div class="note">Pist: temiz FastF1 telemetrisi. Sıra, tur, lastik ve pit zamanları doğrulanmış kayıttır. Pit şeridi koordinatı yayımlanmadığı için görsel şematiktir.</div></div><aside class="panel" id="panel"></aside></div></div><script>
const data=__PAYLOAD__,cars=data.cars||[],route=data.track||[],overlay=data.overlay||{},canvas=document.getElementById('track'),ctx=canvas.getContext('2d');let selected=cars[0]?.code||'',playing=true,speed=1,time=0,last=performance.now(),lastHud=0,lastKey='',view=null;const tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));const fmt=n=>{n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')};
function lap(c,t){const a=c.laps||[];for(let i=0;i<a.length;i++)if(t<=a[i].end)return a[i];return a[a.length-1]||null}function pitEvent(c,t){return(c.pit_events||[]).find(e=>t>=e.start&&t<=e.end)||null}function state(c,t){const l=lap(c,t),a=c.laps||[],last=a[a.length-1],out=!!c.retired&&t>=(last?.end||0);if(!l)return{lap:0,frac:0,pos:c.grid||20,pit:false,out};const i=a.indexOf(l),previous=a[Math.max(0,i-1)]?.position||l.start_position||c.grid||20,frac=Math.max(0,Math.min(1,(t-l.start)/(l.end-l.start||1)));return{lap:l.lap,frac,pos:frac>.997?(l.position||previous):previous,pit:!out&&!!pitEvent(c,t),out}}
function point(f){const n=route.length;if(!n)return{x:0,y:0,a:0};const p=((f%1)+1)%1*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,a:Math.atan2(b[1]-a[1],b[0]-a[0])}}function visual(c,t){const s=state(c,t),start=Math.max(0,1-Math.min(1,t/4)),grid=((c.grid||1)-1)*.0013*start;return point(s.frac-grid)}
function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=30,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
function mark(f,label,col){const q=xy(point(f),view);ctx.fillStyle=col;ctx.beginPath();ctx.arc(q[0],q[1],4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#eaf4ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)}function zone(z,label,col){if(!Number.isFinite(z.start)||!Number.isFinite(z.end))return;ctx.beginPath();for(let i=0;i<=28;i++){const q=xy(point(z.start+(z.end-z.start)*i/28),view);i?ctx.lineTo(...q):ctx.moveTo(...q)}ctx.strokeStyle=col;ctx.lineWidth=6;ctx.globalAlpha=.9;ctx.stroke();ctx.globalAlpha=1;mark(z.start,label,col)}
function pitPath(){const a=xy(point(.984),view),b=xy(point(.026),view),dx=b[0]-a[0],dy=b[1]-a[1],len=Math.max(1,Math.hypot(dx,dy)),nx=-dy/len,ny=dx/len,dir=(a[0]+b[0])/2<view.w/2?1:-1,offset=Math.min(58,Math.max(34,view.w*.065))*dir;return{a,b,c:[a[0]+nx*offset,a[1]+ny*offset],d:[b[0]+nx*offset,b[1]+ny*offset]}}function bez(p0,p1,p2,p3,u){const v=1-u;return{x:v*v*v*p0[0]+3*v*v*u*p1[0]+3*v*u*u*p2[0]+u*u*u*p3[0],y:v*v*v*p0[1]+3*v*v*u*p1[1]+3*v*u*u*p2[1]+u*u*u*p3[1]}}function pitPoint(event,t){const p=pitPath(),u=Math.max(0,Math.min(1,(t-event.start)/(event.end-event.start||1))),q=bez(p.a,p.c,p.d,p.b,u),q2=bez(p.a,p.c,p.d,p.b,Math.min(1,u+.012));return{x:q.x,y:q.y,a:Math.atan2(q2.y-q.y,q2.x-q.x)}}
function drawLane(){const p=pitPath();ctx.beginPath();ctx.moveTo(...p.a);ctx.bezierCurveTo(...p.c,...p.d,...p.b);ctx.strokeStyle='#ffd46b';ctx.lineWidth=4;ctx.setLineDash([7,5]);ctx.globalAlpha=.9;ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;ctx.fillStyle='#ffd46b';ctx.font='bold 9px Arial';ctx.textAlign='center';ctx.fillText('PIT LANE (şematik)',(p.c[0]+p.d[0])/2,(p.c[1]+p.d[1])/2-8)}
function car(x,y,a,c,code,chosen,pit){ctx.save();ctx.translate(x,y);ctx.rotate(-a);ctx.fillStyle='#050a10';ctx.fillRect(-12,-7,5,14);ctx.fillRect(10,-8,4,16);ctx.fillStyle=c;ctx.fillRect(-8,-4,21,8);ctx.fillRect(8,-2,9,4);ctx.fillRect(13,-8,3,16);ctx.fillStyle='#f4f7ff';ctx.fillRect(-16,-9,3,18);ctx.fillRect(0,-1,8,2);if(pit){ctx.strokeStyle='#ffd44b';ctx.lineWidth=2;ctx.strokeRect(-19,-12,39,24)}if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.3;ctx.strokeRect(-21,-14,43,28)}ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-15)}
function draw(){if(!view||!route.length)return;ctx.clearRect(0,0,view.w,view.h);ctx.strokeStyle='#8094ad';ctx.globalAlpha=.72;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},view);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;(overlay.straights||[]).forEach((z,i)=>zone(z,i?'Overtake olasılığı':'Straight Mode',i?'#71e6a1':'#45c8ff'));mark(0,'START / FINISH','#fff');(overlay.sectors||[]).forEach(x=>mark(x.fraction,x.label,x.colour||'#f4d35e'));(overlay.pit||[]).forEach(x=>mark(x.fraction,x.label,'#b79cff'));drawLane();cars.forEach(c=>{const s=state(c,time);if(s.out)return;const e=pitEvent(c,time);let q,p;if(e){p=pitPoint(e,time);q=[p.x,p.y]}else{p=visual(c,time);q=xy(p,view)}car(q[0],q[1],p.a,c.colour,c.code,c.code===selected,!!e)})}
function order(){return cars.filter(c=>!state(c,time).out).sort((a,b)=>{const x=state(a,time),y=state(b,time);return x.pos-y.pos||(y.lap+y.frac)-(x.lap+x.frac)})}function lastPit(c){const e=(c.pit_events||[]).filter(x=>x.end<=time).at(-1);return e?'Tur '+e.lap:'Henüz yok'}
function update(){const now=performance.now();if(now-lastHud<220)return;lastHud=now;const list=order(),key=list.map(c=>c.code+state(c,time).pos+state(c,time).lap).join('|')+selected;if(key!==lastKey){lastKey=key;document.getElementById('strip').innerHTML=list.map(c=>{const s=state(c,time);return`<button class="pilot ${c.code===selected?'active':''}" style="--team:${c.colour}" data-c="${c.code}">P${s.pos} · ${c.code} · T${s.lap}</button>`}).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.c;lastKey='';lastHud=0;update()})}const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),compound=(l?.compound||'—').toUpperCase(),p=pitEvent(c,time),move=(c.grid&&s.pos)?c.grid-s.pos:0,wear=Math.max(8,100-Math.round(100*(s.frac||0)));const profile=c.profile||{},photo=profile.photo?`<img src="${esc(profile.photo)}" alt="">`:'';document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').style.setProperty('--tyre',tyres[compound]||'#9db1c8');document.getElementById('panel').innerHTML=`<div class="hero">${photo}<b>${esc(profile.name||c.code)} · P${s.pos}</b><small>${esc(c.team)} · ${esc(profile.flag||'')} ${esc(c.code)}</small></div><div class="stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="stat"><span>Başlangıç → bitiş</span><b>P${c.grid||'—'} → P${c.final_position||'—'}</b></div><div class="stat"><span>Pozisyon değişimi</span><b>${move>0?'↑ '+move:move<0?'↓ '+Math.abs(move):'→ 0'} sıra</b></div><div class="stat"><span>Stint / lastik</span><b>${l?.stint||'—'} · ${compound}</b></div><div class="tyrebar" style="--wear:${wear}%"><i></i></div><div class="stat"><span>Son pit</span><b>${lastPit(c)}</b></div><div class="stat"><span>Pit durumu</span><b class="${p?'pit':'on'}">${p?'PIT LANE':'PİSTTE'}</b></div>`;document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds)}
function frame(now){let dt=Math.min(.04,Math.max(0,(now-last)/1000));last=now;if(playing){time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan'}}draw();update();requestAnimationFrame(frame)}function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);view=transform();draw();lastHud=0;update()}document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{time=Number(e.target.value)/1000*data.total_seconds;lastHud=0;draw();update()};document.getElementById('sub').textContent=(data.event||'Formula 1')+' · '+data.total_laps+' tur · FastF1 ortak yarış saati';window.addEventListener('resize',resize);resize();requestAnimationFrame(frame);
</script></div></body></html>""".replace('__PAYLOAD__', packed)


def two_driver_duel_html_repaired(*args, **kwargs):
    """Keep all telemetry-derived track markers permanently visible in the duel HUD."""
    markup = _two_driver_duel_html_repaired_v25(*args, **kwargs)
    legend = (
        "<div class='duel-mode-legend-v26'><span>SM · Straight Mode</span>"
        "<span>OM · Overtake olasılığı</span><span>PIT IN / OUT · şematik</span></div>"
    )
    css = (
        "<style>.duel-mode-legend-v26{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0 0}"
        ".duel-mode-legend-v26 span{border:1px solid #35506d;border-radius:99px;padding:5px 8px;"
        "font:850 10px Inter,Arial,sans-serif;color:#bfd2e5;background:#102038}</style>"
    )
    if 'duel-mode-legend-v26' not in markup:
        markup = markup.replace('</style>', css + '</style>', 1)
        markup = markup.replace("<div class='map'>", legend + "<div class='map'>", 1)
        markup = markup.replace('<div class="map">', legend + '<div class="map">', 1)
    return markup


st.markdown(r"""
<style>
/* One calm navigation grid. This does not use animation, fixed overlays or JS. */
section[data-testid="stSidebar"] div[data-testid="stButton"]{width:100%!important;margin:0 0 9px!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button{width:100%!important;min-height:50px!important;padding:0 16px!important;display:flex!important;align-items:center!important;justify-content:flex-start!important;text-align:left!important;border:1px solid #315578!important;border-left:4px solid #3fa9ff!important;border-radius:10px!important;background:linear-gradient(90deg,#10213a,#0d192a)!important;box-shadow:0 7px 18px rgba(0,0,0,.14)!important;font-weight:850!important}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button:hover{border-left-color:#6ee7ff!important;background:linear-gradient(90deg,#142b49,#101e33)!important;transform:translateX(1px)}
section[data-testid="stSidebar"] [data-testid="stExpander"]{margin:0 0 9px!important;border:1px solid #315578!important;border-radius:10px!important;background:#0f1d30!important;overflow:hidden}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary{min-height:50px!important;padding:0 16px!important;display:flex!important;align-items:center!important;font-weight:850!important}
.hud-card.game-stat-v24,.hud-card.game-brief-v24,.hud-card.game-result-v24,.hud-card.draft-driver-v22{box-shadow:0 14px 28px rgba(0,0,0,.18)!important;border-radius:14px!important;background:linear-gradient(145deg,#111d31,#0d1728)!important}
</style>
""", unsafe_allow_html=True)



# =========================================================
# 2.7 CAREER COMPARISON CENTRE
# This deliberately replaces only the comparison page. It does not load a
# FastF1 session, so a current/unfinished session can never distort a career
# profile. Career results are read from Jolpica's historical F1 database and
# cached locally; unavailable source fields are shown as "—", never guessed.
# =========================================================


CAREER_TITLES_V27 = {
    'HAM': 7,
    'VER': 4,
    'ALO': 2,
}


def _career_api_json_v27(endpoint):
    """Short-timeout reader for the optional historical comparison source."""
    request = urllib.request.Request(
        endpoint,
        headers={'User-Agent': 'FormulaPaddock/2.7 (career comparison)'},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
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


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def get_driver_career_stats_v27(driver_code):
    """Career-only totals for comparison HUDs.

    The function intentionally has no FastF1 dependency. A source outage is
    isolated to this card and returns visible unavailable values.
    """
    code = str(driver_code or '').upper().strip()
    empty = {
        'verified': False, 'wins': None, 'podiums': None, 'poles': None,
        'fastest_laps': None, 'starts': None, 'points': None, 'teams': [],
        'titles': CAREER_TITLES_V27.get(code, 0),
    }
    api_code = STEWARDLE_ACTIVE_API_IDS_V24.get(code, '')
    if not api_code:
        return empty
    try:
        races, starts = _career_races_v27(api_code)
        wins = podiums = fastest_laps = 0
        points = 0.0
        teams = []
        for race in races:
            results = race.get('Results', []) or []
            if not results:
                continue
            result = results[0]
            position = _career_number_v27(result.get('position'))
            wins += int(position == 1)
            podiums += int(position in {1, 2, 3})
            fastest = result.get('FastestLap', {}) or {}
            fastest_laps += int(str(fastest.get('rank', '')) == '1')
            points += _career_float_v27(result.get('points')) or 0.0
            team_name = str((result.get('Constructor', {}) or {}).get('name', '')).strip()
            if team_name and team_name not in teams:
                teams.append(team_name)

        # Pole total has its own authoritative endpoint; it does not require
        # treating a race grid position as a qualifying result.  This endpoint
        # is optional: if the historical source cannot provide it, the other
        # verified career totals must remain visible.
        try:
            pole_payload = _career_api_json_v27(
                'https://api.jolpi.ca/ergast/f1/drivers/'
                + urllib.parse.quote(api_code)
                + '/qualifying/1.json?limit=1'
            )
            poles = _career_number_v27(pole_payload.get('MRData', {}).get('total'))
        except Exception:
            poles = None
        return {
            'verified': True,
            'wins': wins,
            'podiums': podiums,
            'poles': poles,
            'fastest_laps': fastest_laps,
            'starts': starts,
            'points': points,
            'teams': teams,
            'titles': CAREER_TITLES_V27.get(code, 0),
        }
    except Exception as error:
        log_data_error('career comparison source', error)
        return empty


def _career_text_v27(value, suffix=''):
    if value is None:
        return '—'
    if isinstance(value, float):
        text = f'{value:,.1f}'.rstrip('0').rstrip('.')
    else:
        text = f'{int(value):,}' if isinstance(value, int) else str(value)
    return text + suffix


def _career_panel_v27(info, stats, colour):
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
        ('POLE', _career_text_v27(stats.get('poles'))),
        ('EN HIZLI TUR', _career_text_v27(stats.get('fastest_laps'))),
        ('GP STARTI', _career_text_v27(stats.get('starts'))),
        ('KARİYER PUANI', _career_text_v27(stats.get('points'))),
        ('PODYUM', _career_text_v27(stats.get('podiums'))),
    ]
    metrics = ''.join(
        f"<div class='career-metric-v27'><span>{label}</span><b>{value}</b></div>"
        for label, value in metric_rows
    )
    teams = stats.get('teams', []) or []
    teams_html = ''.join(
        f"<span>{html_lib.escape(team)}</span>" for team in teams
    ) or '<span>Kaynakta doğrulanamadı</span>'
    source = 'Jolpica tarihî F1 verisi' if stats.get('verified') else 'Kaynak geçici olarak yanıt vermedi'
    return (
        f"<section class='career-panel-v27' style='--team:{colour}'>"
        "<div class='career-hero-v27'>"
        f"{portrait_html}<div><div class='hud-label'>KARİYER DOSYASI</div>"
        f"<h3>{html_lib.escape(info['name'])}</h3>"
        f"<p>{html_lib.escape(code)} · {html_lib.escape(info.get('team', '—'))}</p></div></div>"
        f"<div class='career-metrics-v27'>{metrics}</div>"
        "<div class='career-teams-v27'><small>YARIŞTIĞI TAKIMLAR</small>"
        f"<div>{teams_html}</div></div>"
        f"<div class='career-source-v27'>● {html_lib.escape(source)}</div>"
        "</section>"
    )


def render_driver_comparison_centre():
    """Pure career comparison: no session/timing/position data is rendered."""
    render_page_header(
        'Pilot Karşılaştırma',
        'İki sürücünün kariyer istatistiklerini tek HUD üzerinde karşılaştır. Bu sayfada seans, tur zamanı veya yarış sırası kullanılmaz.',
        '#a78bfa',
    )
    driver_rows = []
    for team_name, team in TEAM_DIRECTORY_2026.items():
        for name, code, number, image_path in team.get('drivers', []):
            driver_rows.append({
                'name': name, 'code': code, 'number': number,
                'image': image_path, 'team': team_name,
            })
    driver_rows.sort(key=lambda row: row['name'].casefold())
    if len(driver_rows) < 2:
        st.info('Karşılaştırma için en az iki sürücü gerekli.')
        return
    labels = {row['code']: f"{row['name']} — {row['team']}" for row in driver_rows}
    codes = [row['code'] for row in driver_rows]
    default_a = codes.index('NOR') if 'NOR' in codes else 0
    default_b = codes.index('VER') if 'VER' in codes else 1
    select_a, select_b = st.columns(2)
    with select_a:
        code_a = st.selectbox('Birinci pilot', codes, index=default_a, format_func=lambda code: labels[code], key='career_compare_a_v27')
    with select_b:
        code_b = st.selectbox('İkinci pilot', codes, index=default_b, format_func=lambda code: labels[code], key='career_compare_b_v27')
    if code_a == code_b:
        st.warning('Karşılaştırma için iki farklı pilot seç.')
        return
    info_a = next(row for row in driver_rows if row['code'] == code_a)
    info_b = next(row for row in driver_rows if row['code'] == code_b)
    with st.spinner('Kariyer kayıtları doğrulanıyor...'):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(get_driver_career_stats_v27, code_a)
            future_b = executor.submit(get_driver_career_stats_v27, code_b)
            stats_a, stats_b = future_a.result(), future_b.result()
    wins_a = stats_a.get('wins')
    wins_b = stats_b.get('wins')
    leader = (
        info_a['name'] if wins_a is not None and wins_b is not None and wins_a > wins_b
        else info_b['name'] if wins_a is not None and wins_b is not None and wins_b > wins_a
        else 'Eşit / doğrulanıyor'
    )
    summary = st.columns(3)
    summaries = [
        ('KARİYER MODU', 'Seans verisi yok', '#7dd3fc'),
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
        st.markdown(_career_panel_v27(info_a, stats_a, team_colour(info_a['team'])), unsafe_allow_html=True)
    with right:
        st.markdown(_career_panel_v27(info_b, stats_b, team_colour(info_b['team'])), unsafe_allow_html=True)
    st.caption('Kariyer istatistikleri, seans tablosundan değil tarihî yarış kayıtlarından gelir. Veri kaynağı geçici olarak ulaşılamazsa değer uydurulmaz; “—” görünür.')


st.markdown(r"""
<style>
/* Career Comparison 2.7: equal visual columns with career-only data. */
.career-panel-v27{min-height:492px;border:1px solid #2b4664;border-top:5px solid var(--team);border-radius:16px;padding:18px;background:linear-gradient(145deg,#111d31,#0b1524);box-shadow:0 14px 30px rgba(0,0,0,.18)}
.career-hero-v27{min-height:118px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #2a4059;padding-bottom:13px}.career-hero-v27 img{width:92px;height:116px;object-fit:contain;object-position:center bottom;flex:0 0 auto}.career-hero-v27 h3{margin:5px 0 4px;color:var(--team);font-size:1.45rem;line-height:1.15}.career-hero-v27 p{margin:0;color:#a8c0d7;font-size:.86rem}
.career-metrics-v27{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:14px}.career-metric-v27{min-height:70px;padding:10px;border:1px solid #29435f;border-radius:10px;background:#0d1829}.career-metric-v27 span,.career-teams-v27 small{display:block;color:#91abd0;font-size:.63rem;font-weight:900;letter-spacing:1.05px}.career-metric-v27 b{display:block;color:#f7fbff;font-size:1.16rem;margin-top:7px}
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


def _career_verified_rows_v28(api_code):
    """Load, de-duplicate and verify historical result rows for one driver."""
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


def render_driver_comparison_centre():
    """Career-only comparison with verified historical rows, never session data."""
    render_page_header(
        'Pilot Karşılaştırma',
        'İki sürücünün doğrulanmış kariyer istatistiklerini aynı HUD üzerinde karşılaştır.',
        '#a78bfa',
    )
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


# The existing duel already follows real telemetry. This layer replaces only the
# car glyph and time interpolation so there is no new data source or page flow.
_two_driver_duel_html_repaired_v28 = two_driver_duel_html_repaired


def two_driver_duel_html_repaired(*args, **kwargs):
    markup = _two_driver_duel_html_repaired_v28(*args, **kwargs)
    old_at_start = 'function at(a,f){'
    old_at_end = 'function transform()'
    at_start = markup.find(old_at_start)
    at_end = markup.find(old_at_end, at_start)
    if at_start >= 0 and at_end > at_start:
        smooth_at = r"""function at(a,f){if(!a?.length)return null;const p=Math.max(0,Math.min(1,Number(f)||0));const last=a[a.length-1]||{};if(Number.isFinite(last.elapsed)&&last.elapsed>0){const goal=p*last.elapsed;let lo=0,hi=a.length-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if((a[mid].elapsed||0)<goal)lo=mid+1;else hi=mid}const i=Math.max(0,lo-1),x=a[i],y=a[Math.min(a.length-1,lo)],span=(y.elapsed||0)-(x.elapsed||0),r=span?Math.max(0,Math.min(1,(goal-(x.elapsed||0))/span)):0;return{x:x.x+(y.x-x.x)*r,y:x.y+(y.y-x.y)*r,distance:x.distance+(y.distance-x.distance)*r,elapsed:goal}}const n=p*(a.length-1),i=Math.floor(n),r=n-i,x=a[i],y=a[Math.min(a.length-1,i+1)];return{x:x.x+(y.x-x.x)*r,y:x.y+(y.y-x.y)*r,distance:x.distance+(y.distance-x.distance)*r,elapsed:x.elapsed+(y.elapsed-x.elapsed)*r}}"""
        markup = markup[:at_start] + smooth_at + markup[at_end:]

    old_car_start = 'function drawCar(q,a,n,c,done){'
    old_car_end = 'function drawOverlay'
    car_start = markup.find(old_car_start)
    car_end = markup.find(old_car_end, car_start)
    if car_start >= 0 and car_end > car_start:
        f1_car = r"""function drawCar(q,a,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.rotate(a);ctx.scale(.82,.82);ctx.globalAlpha=done?.52:1;ctx.shadowColor=c;ctx.shadowBlur=10;ctx.fillStyle='#05080d';[[-12,-11,7,6],[-12,5,7,6],[7,-11,7,6],[7,5,7,6]].forEach(w=>ctx.fillRect(...w));ctx.shadowBlur=0;ctx.fillStyle=c;ctx.fillRect(-10,-5,23,10);ctx.fillRect(-2,-7,10,14);ctx.fillRect(7,-3,12,6);ctx.fillStyle='#101924';ctx.beginPath();ctx.ellipse(-1,0,5.5,4.5,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#dce9f8';ctx.fillRect(-18,-11,4,22);ctx.fillRect(-15,-9,7,4);ctx.fillRect(-15,5,7,4);ctx.fillStyle='#f7fbff';ctx.fillRect(18,-13,4,26);ctx.fillRect(14,-10,9,4);ctx.fillRect(14,6,9,4);ctx.fillStyle='#89a4bd';ctx.fillRect(0,-1,8,2);ctx.restore();ctx.fillStyle=c;ctx.font='900 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-17)}"""
        markup = markup[:car_start] + f1_car + markup[car_end:]

    markup = markup.replace('Math.min(.03,Math.max(0,(now-last)/1000))', 'Math.min(.018,Math.max(0,(now-last)/1000))')
    spec = "<div class='duel-car-spec-v28'><span>TAKIM RENKLERİ</span><span>ÖN KANAT</span><span>HALO</span><span>ARKA KANAT</span></div>"
    style = "<style>.duel-car-spec-v28{display:flex;gap:7px;flex-wrap:wrap;margin:9px 0 4px}.duel-car-spec-v28 span{font:850 9px Inter,Arial,sans-serif;letter-spacing:.7px;color:#bcd2e8;border:1px solid #35506d;border-radius:99px;background:#0e1b2d;padding:5px 8px}</style>"
    if 'duel-car-spec-v28' not in markup:
        markup = markup.replace('</style>', style + '</style>', 1)
        markup = markup.replace("<div class='map'>", spec + "<div class='map'>", 1)
        markup = markup.replace('<div class="map">', spec + '<div class="map">', 1)
    return markup


st.markdown(r"""
<style>
.career-panel-v28{min-height:492px;border:1px solid #2b4664;border-top:5px solid var(--team);border-radius:16px;padding:18px;background:linear-gradient(145deg,#111d31,#0b1524);box-shadow:0 14px 30px rgba(0,0,0,.18)}
.career-hero-v28{min-height:118px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #2a4059;padding-bottom:13px}.career-hero-v28 img{width:92px;height:116px;object-fit:contain;object-position:center bottom;flex:0 0 auto}.career-hero-v28 h3{margin:5px 0 4px;color:var(--team);font-size:1.45rem;line-height:1.15}.career-hero-v28 p{margin:0;color:#a8c0d7;font-size:.86rem}
.career-metrics-v28{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:14px}.career-metric-v28{min-height:70px;padding:10px;border:1px solid #29435f;border-radius:10px;background:#0d1829}.career-metric-v28 span,.career-teams-v28 small{display:block;color:#91abd0;font-size:.63rem;font-weight:900;letter-spacing:1.05px}.career-metric-v28 b{display:block;color:#f7fbff;font-size:1.16rem;margin-top:7px}
.career-teams-v28{margin-top:14px;padding-top:12px;border-top:1px solid #2a4059}.career-teams-v28>div{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.career-teams-v28 span{border:1px solid #2e4a68;border-left:3px solid var(--team);border-radius:99px;padding:5px 8px;color:#c9d9e7;background:#122137;font-size:.74rem;font-weight:760}.career-source-v28{margin-top:13px;color:#83a0bd;font-size:.72rem}
@media(max-width:800px){.career-panel-v28{min-height:0;margin-bottom:12px}.career-hero-v28 img{width:76px;height:98px}.career-metrics-v28{grid-template-columns:repeat(2,minmax(0,1fr))}.career-metric-v28{min-height:63px}}
</style>
""", unsafe_allow_html=True)

if st.session_state['page'] == 'home':
    # İlk kare hiçbir dış kaynağı beklemez. Böylece FastF1/cache bağlantısı
    # problemliyken bile navigasyon ve arayüz görünür kalır.
    if not st.session_state['home_data_requested']:
        render_data_state(
            "PADDOCK BAĞLANTISI HAZIR",
            "Site güvenli modda anında açıldı. Yarış merkezi ve haberleri yalnızca sen istediğinde doğrulanmış kaynaktan yükler.",
            "success",
        )
        if st.button("⚡ Yarış merkezi verilerini yükle", key="load_home_data", use_container_width=True):
            st.session_state['home_data_requested'] = True
            st.rerun()

        target_s_time = datetime.datetime.now(datetime.timezone.utc)
        curr_event = pd.Series({'EventName': 'Formula 1 Yarış Merkezi', 'Location': 'Takvim henüz yüklenmedi'})
        target_s_name = 'Veri bekleniyor'
        is_live_now = False
        event_name = curr_event['EventName']
        location_name = curr_event['Location']
        calendar_waiting = True
        last_session = None
        last_session_label = "Yükleme düğmesine bastığında son tamamlanan gerçek seans gelir"
        real_drivers = []
        session_summary = []
    else:
        curr_event, target_s_name, target_s_time, is_live_now = get_current_or_next_event()
        event_name = curr_event['EventName'] if 'EventName' in curr_event else "Formula 1"
        location_name = curr_event['Location'] if 'Location' in curr_event else "Pist"
        calendar_waiting = str(event_name) == 'Takvim verisi bekleniyor'

        # Sayaç sıradaki seansı gösterir; şerit ise son tamamlanan gerçek seansı gösterir.
        last_session = get_latest_completed_session(target_s_time.year)
        last_session_label = "Doğrulanmış son seans sonucu bekleniyor"
        real_drivers = []
        session_summary = []
        if last_session:
            last_session_label = (
                f"{last_session['event_name']} | "
                f"Son Seans Sonuçları ({last_session['display_name']})"
            )
            real_drivers, _ = get_real_top_drivers(
                last_session['year'],
                last_session['event_name'],
                last_session['session_code']
            )
            session_summary = get_session_summary(
                last_session['year'],
                last_session['event_name'],
                last_session['session_code']
            )

    target_timestamp_ms = int(target_s_time.timestamp() * 1000)
    
    ticker_html_items = ""
    for d in real_drivers:
        code = d["code"]
        t_data = DRIVER_TEAMS.get(code, {"color": "#FFFFFF"})
        tyre_badge = get_tyre_html(d["tyre"])
        
        ticker_html_items += f"""
        <div class="rc-driver-box" style="border-left-color: {t_data['color']};">
            <span class="rc-driver-item" style="color: {t_data['color']};">{d['name']}</span>
            <span class="rc-gap">{d['time']}</span>
            {tyre_badge}
        </div>
        """

    if not ticker_html_items:
        ticker_html_items = """
        <div style="width:100%; text-align:center; color:#94A3B8; font-weight:700;">
            Son seansın doğrulanmış sıralaması henüz yüklenemedi.
        </div>
        """

    status_badge_text = (
        "TAKVİM VERİSİ BEKLENİYOR"
        if calendar_waiting
        else ("CANLI YAYINDA 🔴" if is_live_now else "BEKLENİYOR ⏱️")
    )
    countdown_title = (
        "TAKVİM VE SEANS SAATİ DOĞRULANIYOR"
        if calendar_waiting
        else f"📍 {location_name.upper()} — {target_s_name.upper()} (Sıradaki Seans) Başlangıcına Kalan Süre:"
    )

    # RACECENTER HTML (SON YAPILAN SEANS BİLGİSİ İLE)
    racecenter_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body {{
            background-color: transparent;
            color: #F1F5F9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 0;
        }}
        .racecenter-card {{
            background: #111622;
            border: 1px solid #222C3E;
            border-radius: 12px;
            padding: 18px 22px;
            box-shadow: 0 6px 18px rgba(0,0,0,0.5);
            box-sizing: border-box;
        }}
        .rc-title-bar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.95rem;
            font-weight: 800;
            color: #94A3B8;
            border-bottom: 1px solid #1E293B;
            padding-bottom: 10px;
            margin-bottom: 14px;
        }}
        .rc-badge {{
            background: {'#E10600' if is_live_now else '#1E293B'};
            border: 1px solid {'#FF1801' if is_live_now else '#334155'};
            padding: 4px 12px;
            border-radius: 6px;
            color: #FFF;
            font-size: 0.82rem;
            font-weight: 800;
        }}
        .rc-ticker {{
            display: flex;
            align-items: center;
            gap: 12px;
            background: #182030;
            border: 1px solid #28354A;
            padding: 10px 14px;
            border-radius: 8px;
            overflow-x: auto;
            margin-bottom: 16px;
        }}
        .rc-driver-box {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 14px;
            background: #0D121D;
            border-radius: 6px;
            border-left: 4px solid #FFF;
        }}
        .rc-driver-item {{
            font-size: 0.95rem;
            font-weight: 900;
            white-space: nowrap;
        }}
        .rc-gap {{
            font-size: 0.82rem;
            color: #94A3B8;
            font-weight: 700;
            margin-left: 3px;
        }}
        .rc-timer-wide-box {{
            background: linear-gradient(135deg, #0D121D 0%, #161E2E 100%);
            border: 1px solid #222C3E;
            border-radius: 12px;
            padding: 16px 20px;
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        .rc-track-title {{
            font-size: 1.15rem;
            color: #E10600;
            font-weight: 900;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-bottom: 6px;
        }}
        .rc-timer-val-big {{
            font-size: 2.8rem;
            font-weight: 900;
            color: #FFFFFF;
            font-family: monospace;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .time-num {{ color: #FFFFFF; }}
        .time-label {{
            font-size: 0.9rem;
            color: #8E8E9F;
            font-weight: 800;
            margin-left: 2px;
            margin-right: 14px;
        }}
    </style>
    </head>
    <body>
        <div class="racecenter-card">
            <div class="rc-title-bar">
                <span>F1 RACE CENTER <span style="color:#FFF;">• {last_session_label}</span></span>
                <span class="rc-badge">{status_badge_text}</span>
            </div>
            
            <div class="rc-ticker">
                {ticker_html_items}
            </div>

            <div class="rc-timer-wide-box">
                <div class="rc-track-title">{countdown_title}</div>
                <div id="timer-container" class="rc-timer-val-big">
                    <span class="time-num" id="rc-d">00</span><span class="time-label">GÜN</span>
                    <span style="color:#E10600;">:</span>
                    <span class="time-num" id="rc-h">00</span><span class="time-label">SAAT</span>
                    <span style="color:#E10600;">:</span>
                    <span class="time-num" id="rc-m">00</span><span class="time-label">DK</span>
                    <span style="color:#E10600;">:</span>
                    <span class="time-num" id="rc-s">00</span><span class="time-label">SN</span>
                </div>
            </div>
        </div>

        <script>
            var targetTime = {target_timestamp_ms};
            var calendarWaiting = {str(calendar_waiting).lower()};
            function updateTimer() {{
                if (calendarWaiting) {{
                    document.getElementById("timer-container").innerHTML = '<div style="color:#9eb6cf; font-size:1.1rem; font-weight:800;">Takvim bağlantısı yeniden denenecek. Doğrulanmamış bir seans için sahte sayaç gösterilmez.</div>';
                    return;
                }}
                var now = new Date().getTime();
                var distance = targetTime - now;
                
                if (distance <= 0) {{
                    document.getElementById("timer-container").innerHTML = '<div style="color:#00FF66; font-size:1.8rem; font-weight:900;">🔴 SEANS BAŞLADI! CANLI SİNYAL AKTİF</div>';
                    return;
                }}
                
                var days = Math.floor(distance / (1000 * 60 * 60 * 24));
                var hours = Math.floor((distance % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
                var minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
                var seconds = Math.floor((distance % (1000 * 60)) / 1000);
                
                document.getElementById("rc-d").innerText = days < 10 ? "0" + days : days;
                document.getElementById("rc-h").innerText = hours < 10 ? "0" + hours : hours;
                document.getElementById("rc-m").innerText = minutes < 10 ? "0" + minutes : minutes;
                document.getElementById("rc-s").innerText = seconds < 10 ? "0" + seconds : seconds;
            }}
            setInterval(updateTimer, 1000);
            updateTimer();
        </script>
    </body>
    </html>
    """

    render_html_hud(racecenter_html, height=270)

    render_home_command_hud_v18(event_name, location_name, target_s_name, last_session, real_drivers, is_live_now)

    if session_summary:
        st.markdown("#### 🧠 Bu seansta ne oldu?")
        insight_columns = st.columns(len(session_summary))
        for column, insight in zip(insight_columns, session_summary):
            with column:
                st.info(insight)

    if is_live_now:
        if st.button(f"🔴 CANLI TAKİP: {target_s_name.upper()} SEANSI BAŞLADI — TIKLA VE İNCELE ➔", use_container_width=True):
            st.session_state['page'] = 'live'

    st.write("")
    st.markdown("### 📰 Paddock Live News — Son Dakika Türkçe F1 Haberleri")
    if not st.session_state['news_requested']:
        st.caption("Haber akışı ilk açılışta siteyi bekletmez.")
        if st.button("📰 Haber akışını getir", key="load_live_news", use_container_width=True):
            st.session_state['news_requested'] = True
            st.rerun()
        live_news = []
    else:
        live_news = fetch_live_f1_news()
        if st.button('Tum haberleri ve takim filtrelerini ac', key='open_news_centre_v19', use_container_width=True):
            st.session_state['page'] = 'news'
            st.rerun()
    
    col_n1, col_n2 = st.columns(2)
    for idx, item in enumerate(live_news):
        target_col = col_n1 if idx % 2 == 0 else col_n2
        with target_col:
            news_date = html_lib.escape(str(item.get('date', '')))
            news_title = html_lib.escape(str(item.get('title', '')))
            news_desc = html_lib.escape(str(item.get('desc', '')))
            news_link = safe_external_url(item.get('link')) or 'https://www.formula1.com/'
            st.markdown(f"""
            <div class="news-card">
                <div class="news-date">🕒 {news_date} — F1 CANLI HABER AKIŞI</div>
                <div class="news-title">{news_title}</div>
                <div class="news-desc">{news_desc}</div>
                <a href="{html_lib.escape(news_link, quote=True)}" target="_blank" rel="noopener noreferrer" class="news-link">Orijinal Haberi Oku (İngilizce) ↗</a>
            </div>
            """, unsafe_allow_html=True)

# SAYFA 2: CANLI SEANS TAKİBİ
elif st.session_state['page'] == 'live':
    curr_event, target_s_name, target_s_time, is_live_now = get_current_or_next_event()
    gp_name = curr_event['EventName'] if 'EventName' in curr_event else "Hungarian Grand Prix"
    
    st.markdown(f"## 📡 Seans & Yarış Merkezi — {gp_name}")
    st.info(
        "💡 **Alpha odağı:** Dereceler ve tamamlanmış yarış tekrarları. Canlı 2D pist, "
        "doğrulanmış bir konum sağlayıcısı hazır olana kadar kapalı tutulur; site sahte canlı konum üretmez."
    )

    timing_tab, replay_tab = st.tabs(["📊 Dereceler", "🎬 2026 Yarış Tekrarı"])
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
        st.caption("Sonuç listesi isteyince yüklenir. Böylece gelecekteki bir seansın verisi diğer sekmeleri kilitlemez.")
        timing_now = datetime.datetime.now(datetime.timezone.utc)
        session_is_future = target_s_time > timing_now
        timing_load_key = f"load_timing_2026_{gp_name}_{target_s_name}"

        if session_is_future:
            st.info(
                f"{target_s_name} henüz başlamadı. Sonuç çekmeye çalışmıyoruz; "
                "Yarış Takrarı sekmesi ve diğer sayfalar normal şekilde açık kalır."
            )
        else:
            if st.button("🔄 Dereceleri yükle / yenile", key=timing_load_key, use_container_width=True):
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
                        st.dataframe(res, use_container_width=True, height=620, hide_index=True)
                except Exception:
                    st.warning(f"Seans verisi henüz FastF1'e düşmedi ({target_s_name}).")
            else:
                st.info("Bu seansın derecelerini görmek için yukarıdaki düğmeye basabilirsin.")

    with replay_tab:
        st.markdown("### 🎬 2026 Yarış Tekrar Merkezi")
        st.caption("Tüm 2026 hafta sonları burada tek yerde. Tamamlanan seansların doğrulanmış sonuçları ve yarış lastik stintleri otomatik gelir; gelecekteki yarışlarda program görünür.")
        replay_events = get_calendar_details(2026)
        if not replay_events:
            st.info("2026 takvimi şu an alınamadı.")
        else:
            replay_names = [str(event.get('EventName', 'Formula 1')) for event in replay_events]
            replay_event_name = st.selectbox("2026 yarışını seç", replay_names, key="replay_event_2026")
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
                    key=f"replay_session_2026_{replay_event_name}",
                )
                replay_session = next(item for item in finished_sessions if item['title'] == replay_session_title)
                is_race_replay = replay_session['code'] == 'R'
                replay_hud_key = f"clean_race_replay_hud_2026_{replay_event_name}"

                # 2D butonu sonuç tablosundan önce gelir. Böylece uzun tablo, kullanıcıyı
                # gerçek replay girişinden aşağıya itmez ve boş geçici bir tablo 2D'yi engellemez.
                if is_race_replay:
                    st.markdown("#### 🏎️ Tam yarış 2D pist kontrolü")
                    st.caption(
                        "Pist, tek temiz telemetri turundan çizilir. Araçlar doğrulanmış yarış başlangıcı, tur süresi, "
                        "sıra, pit ve lastik verisiyle akıcı olarak bu yörüngede ilerler; bu alan canlı GPS diye etiketlenmez."
                    )
                    replay_action, retry_action = st.columns([4, 1])
                    with replay_action:
                        if st.button("▶ 2D yarış tekrarını aç", key=f"load_{replay_hud_key}", use_container_width=True):
                            st.session_state[replay_hud_key] = True
                    with retry_action:
                        if st.button("↻ Yeniden dene", key=f"retry_{replay_hud_key}", use_container_width=True):
                            build_stable_race_replay_payload.clear()
                            st.session_state[replay_hud_key] = True

                    if st.session_state.get(replay_hud_key, False):
                        render_data_state(
                            "RACE REPLAY STATUS",
                            "Race data is being verified once; later opens use the local cache.",
                            "info",
                        )
                        with st.spinner("İlk açılışta FastF1 yarış paketi indiriliyor; birkaç dakika sürebilir..."):
                            replay_payload = build_stable_race_replay_payload(2026, replay_event_name)
                        if replay_payload.get('ok'):
                            render_data_state(
                                "RACE PACKAGE READY",
                                "Track, lap, position, pit and tyre records passed the replay safety checks.",
                                "success",
                            )
                            render_html_hud(stable_race_replay_html(replay_payload), height=850, scrolling=True)
                            st.markdown("#### 🛞 Tyre Strategy Wall")
                            render_html_hud(
                                strategy_wall_html(replay_payload),
                                height=strategy_wall_component_height(replay_payload),
                                scrolling=False,
                            )
                            st.markdown("#### 📈 Tur tur pozisyon akışı")
                            render_html_hud(position_flow_html(replay_payload), height=520, scrolling=True)

                            intelligence_key = f"race_intelligence_2026_{replay_event_name}"
                            if st.button("🧭 Race Intelligence'ı yükle", key=intelligence_key, use_container_width=True):
                                st.session_state[intelligence_key] = True
                            if st.session_state.get(intelligence_key, False):
                                with st.spinner("Hava, pit-lane ve Race Control verisi hazırlanıyor..."):
                                    race_intelligence = get_race_intelligence_v19(2026, replay_event_name)
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
                        replay_table, replay_laps = get_session_results_table(2026, replay_event_name, replay_session['code'])

                    if replay_table.empty:
                        st.info("Bu seans tamamlanmış görünüyor ama sonuç verisi FastF1'e henüz düşmedi.")
                    else:
                        render_html_hud(
                            session_leaderboard_html(
                                replay_table,
                                f"2026 // {replay_event_name} // {replay_session['title'].upper()}"
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
                                use_container_width=True,
                                hide_index=True,
                                height=620,
                                column_config={'Lastik': st.column_config.TextColumn('Lastik')},
                            )

# SAYFA 3: TELEMETRİ VE DOMİNASYON HARİTASI
elif st.session_state['page'] == 'telemetry':
    try:
        with st.spinner('Telemetri verileri yükleniyor...'):
            session = fastf1.get_session(year, gp, session_type)
            session.load()

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
                st.markdown(f"### 🏁 {session.event['EventName']} — Track Dominance{header_suffix}")
                
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

                        lap_time1 = format_time(lap1['LapTime'])
                        lap_time2 = format_time(lap2['LapTime'])
                        
                        tyre1 = get_tyre_html(lap1.get('Compound', 'SOFT'))
                        tyre2 = get_tyre_html(lap2.get('Compound', 'SOFT'))

                        m1, m2, m3 = st.columns(3)
                        with m1:
                            st.markdown(f"""
                            <div class="metric-card">
                                <div class="title">{d1} TURU {tyre1}</div>
                                <div class="value" style="color:#E10600;">{lap_time1}</div>
                            </div>
                            """, unsafe_allow_html=True)
                        with m2:
                            st.markdown(f"""
                            <div class="metric-card">
                                <div class="title">{d2} TURU {tyre2}</div>
                                <div class="value" style="color:#38BDF8;">{lap_time2}</div>
                            </div>
                            """, unsafe_allow_html=True)
                        with m3:
                            speed_diff = round(tel1['Speed'].max() - tel2['Speed'].max(), 1)
                            st.markdown(f"""
                            <div class="metric-card">
                                <div class="title">TOP SPEED FARKI</div>
                                <div class="value">{abs(speed_diff)} km/h</div>
                            </div>
                            """, unsafe_allow_html=True)

                        st.write("")

                        max_dist = max(tel1['Distance'].max(), tel2['Distance'].max())
                        distance = np.linspace(0, max_dist, 1000)

                        speed1 = np.interp(distance, tel1['Distance'], tel1['Speed'])
                        speed2 = np.interp(distance, tel2['Distance'], tel2['Speed'])
                        x = np.interp(distance, tel1['Distance'], tel1['X'])
                        y = np.interp(distance, tel1['Distance'], tel1['Y'])

                        delta_speed = speed1 - speed2
                        points = np.array([x, y]).T.reshape(-1, 1, 2)
                        segments = np.concatenate([points[:-1], points[1:]], axis=1)

                        cmap = ListedColormap(['#38BDF8', '#E10600'])
                        norm = plt.Normalize(-1, 1)
                        dominance = np.where(delta_speed[:-1] >= 0, 1, -1)

                        c_left, c_center, c_right = st.columns([1, 3, 1])
                        with c_center:
                            fig, ax = plt.subplots(figsize=(6, 4.5))
                            lc = LineCollection(segments, cmap=cmap, norm=norm, linewidth=4)
                            lc.set_array(dominance)

                            ax.add_collection(lc)
                            ax.autoscale()
                            ax.set_aspect('equal', 'datalim')
                            ax.set_facecolor('#0B0E14')
                            fig.patch.set_facecolor('#0B0E14')
                            plt.axis('off')

                            st.pyplot(fig)

                        st.info(f"🔴 **Kırmızı BÖLGELER:** {driver_options.get(d1, d1)} pilotunun daha hızlı olduğu noktalar.\n\n🔵 **Mavi BÖLGELER:** {driver_options.get(d2, d2)} pilotunun daha hızlı olduğu noktalar.")
                        st.success("📍 " + get_speed_difference_insight(session, d1, d2, tel1, tel2))

            # --- MOD 2: 2D TUR DÜELLOSU ---
            elif analiz_turu == "🏎️ 2D Tur Düellosu":
                st.markdown(f"### 🏎️ {session.event['EventName']} — 2D Tur Düellosu{header_suffix}")
                st.caption("İki mod var: Mesafeye göre, aynı virajdaki hız farkını gösterir. Gerçek zaman ise iki turun fiziksel zaman farkını gösterir.")

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
                        def team_for_driver(driver_code):
                            try:
                                row = session.results[session.results['Abbreviation'] == driver_code]
                                if not row.empty:
                                    return str(row.iloc[0].get('TeamName', 'Formula 1'))
                            except Exception:
                                pass
                            return 'Formula 1'

                        team_1 = team_for_driver(duel_driver_1)
                        team_2 = team_for_driver(duel_driver_2)
                        colour_1 = team_colour(team_1)
                        colour_2 = team_colour(team_2)
                        if colour_1 == colour_2:
                            colour_1, colour_2 = "#E10600", "#38BDF8"
                        gap_seconds = abs(duel_lap_1['LapTime'].total_seconds() - duel_lap_2['LapTime'].total_seconds())
                        duel_overlay = build_track_overlay(duel_tel_1, duel_lap_1, session)
                        duel_sectors_1 = [format_time(duel_lap_1.get(column)) for column in ['Sector1Time', 'Sector2Time', 'Sector3Time']]
                        duel_sectors_2 = [format_time(duel_lap_2.get(column)) for column in ['Sector1Time', 'Sector2Time', 'Sector3Time']]
                        metric_1, metric_2, metric_3, metric_4 = st.columns(4)
                        metric_1.metric(f"{duel_driver_1} turu", format_time(duel_lap_1['LapTime']))
                        metric_2.metric(f"{duel_driver_2} turu", format_time(duel_lap_2['LapTime']))
                        metric_3.metric("Tur farkı", f"{gap_seconds:.3f} sn")
                        metric_4.metric("Önde olan", duel_driver_1 if duel_lap_1['LapTime'] < duel_lap_2['LapTime'] else duel_driver_2)
                        render_html_hud(
                            two_driver_duel_html_repaired(
                                duel_tel_1, duel_tel_2, duel_driver_1, duel_driver_2, team_1, team_2,
                                colour_1, colour_2, format_time(duel_lap_1['LapTime']), format_time(duel_lap_2['LapTime']),
                                duel_lap_1['LapTime'].total_seconds(), duel_lap_2['LapTime'].total_seconds(), duel_overlay,
                                duel_sectors_1, duel_sectors_2
                            ),
                            height=620,
                            scrolling=False
                        )
                        st.success("📍 " + get_speed_difference_insight(session, duel_driver_1, duel_driver_2, duel_tel_1, duel_tel_2))

            # --- MOD 3: DETAYLI TELEMETRİ & FREN ANALİZİ ---
            elif analiz_turu == "🛑 Telemetri & Fren Analizi":
                st.markdown(f"### 🏁 {session.event['EventName']} — Telemetry Overlay{header_suffix}")
                
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

                        fig, (ax_speed, ax_brake, ax_throttle, ax_gear) = plt.subplots(4, 1, figsize=(10, 8), sharex=True)

                        for ax in [ax_speed, ax_brake, ax_throttle, ax_gear]:
                            ax.set_facecolor('#111827')
                            ax.tick_params(colors='#94A3B8')
                            ax.xaxis.label.set_color('#94A3B8')
                            ax.yaxis.label.set_color('#94A3B8')
                            ax.grid(True, color='#1E293B', linestyle='--', alpha=0.6)

                        fig.patch.set_facecolor('#0B0E14')

                        ax_speed.plot(tel1['Distance'], tel1['Speed'], label=f"{driver_options.get(d1, d1)}", color='#E10600', linewidth=1.8)
                        ax_speed.plot(tel2['Distance'], tel2['Speed'], label=f"{driver_options.get(d2, d2)}", color='#38BDF8', linewidth=1.8)
                        ax_speed.set_ylabel("Hız (km/h)", fontsize=9)
                        ax_speed.legend(loc="upper right", facecolor='#111827', edgecolor='none', labelcolor='white')

                        ax_brake.plot(tel1['Distance'], tel1['Brake'], color='#E10600', linewidth=1.5)
                        ax_brake.plot(tel2['Distance'], tel2['Brake'], color='#38BDF8', linewidth=1.5)
                        ax_brake.set_ylabel("Fren", fontsize=9)

                        ax_throttle.plot(tel1['Distance'], tel1['Throttle'], color='#E10600', linewidth=1.5)
                        ax_throttle.plot(tel2['Distance'], tel2['Throttle'], color='#38BDF8', linewidth=1.5)
                        ax_throttle.set_ylabel("Gaz %", fontsize=9)

                        ax_gear.plot(tel1['Distance'], tel1['nGear'], color='#E10600', linewidth=1.5)
                        ax_gear.plot(tel2['Distance'], tel2['nGear'], color='#38BDF8', linewidth=1.5)
                        ax_gear.set_ylabel("Vites", fontsize=9)
                        ax_gear.set_xlabel("Pist Mesafesi (Metre)", fontsize=10)

                        st.pyplot(fig)
                        st.info("💡 **Late Braking (Geç Frenleme) İpucu:** Fren grafiğindeki dikey sıçramalara bak. Dikey çizgi daha sağda olan pilot, viraja daha geç fren yaparak girmiş demektir!")
                        st.success("📍 " + get_speed_difference_insight(session, d1, d2, tel1, tel2))

            # --- MOD 3: TOP SPEED & SÜRÜCÜ TABLOSU ---
            elif analiz_turu == "📊 Top Speed & SÜRÜCÜ Tablosu":
                st.markdown(f"### 🏁 {session.event['EventName']} — Leaderboard{header_suffix}")
                
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
                    st.dataframe(df_summary, use_container_width=True)
                    st.caption("Resmî Speed Trap / I1 / I2 FastF1'in ilgili seans ölçüm alanından gelir. Telemetri Maks. Hız ise turdaki en yüksek örneklenmiş hızdır; ikisi aynı şey değildir.")
                else:
                    st.warning("Veri çekilemedi.")

            # --- MOD 4: LASTİK STRATEJİSİ ---
            elif analiz_turu == "🛞 Lastik Stratejisi & Stintler":
                st.markdown(f"### 🛞 {session.event['EventName']} — Lastik Stratejisi{header_suffix}")
                if session_type != "R":
                    st.info("En anlamlı strateji görünümü yarış seansında oluşur. Bu seans için mevcut stintler gösteriliyor.")

                strategy = build_strategy_data(session)
                if strategy.empty:
                    st.warning("Bu seans için stint verisi bulunamadı.")
                else:
                    compound_colors = {
                        "SOFT": "#FF1801", "MEDIUM": "#FFE11A", "HARD": "#FFFFFF",
                        "INTERMEDIATE": "#39B54A", "WET": "#00AEEF"
                    }
                    selected_drivers = st.multiselect(
                        "Grafikte gösterilecek pilotlar",
                        strategy['Pilot'].unique().tolist(),
                        default=strategy['Pilot'].unique().tolist()[:10]
                    )
                    chart_data = strategy[strategy['Pilot'].isin(selected_drivers)]
                    figure, axis = plt.subplots(figsize=(12, max(4, len(selected_drivers) * 0.45)))
                    figure.patch.set_facecolor('#0B0E14')
                    axis.set_facecolor('#111827')
                    for row_index, driver in enumerate(selected_drivers):
                        driver_stints = chart_data[chart_data['Pilot'] == driver]
                        for _, stint in driver_stints.iterrows():
                            color = compound_colors.get(str(stint['Lastik']).upper(), '#64748B')
                            axis.barh(
                                row_index, stint['Tur Sayısı'], left=stint['Başlangıç Turu'],
                                color=color, edgecolor='#0B0E14', height=0.6
                            )
                            axis.text(
                                stint['Başlangıç Turu'] + stint['Tur Sayısı'] / 2, row_index,
                                str(stint['Lastik'])[:1], ha='center', va='center',
                                color='#0B0E14', fontweight='bold'
                            )
                    axis.set_yticks(range(len(selected_drivers)), selected_drivers)
                    axis.set_xlabel('Tur', color='#94A3B8')
                    axis.tick_params(colors='#94A3B8')
                    axis.grid(axis='x', color='#1E293B', linestyle='--', alpha=0.6)
                    st.pyplot(figure, use_container_width=True)
                    st.dataframe(strategy, use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Veriler çekilirken hata oluştu: {e}")

# SAYFA 4: TAKVİM VE PİSTLER
elif st.session_state['page'] == 'calendar':
    st.markdown("## 🏁 Takvim & Pistler")
    st.caption("Bir yarış seç: pist görünümü, hafta sonu programı ve tamamlanan seans sonuçları aynı yerde.")
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
                if st.button(f"🏎️ {event_name}\n{status}", key=f"calendar_{calendar_year}_{event_name}", use_container_width=True):
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
    if st.button("🗺️ Pist görünümünü aç", use_container_width=True):
        st.session_state[map_key] = True
    if st.session_state.get(map_key):
        with st.spinner("Pist çizimi hazırlanıyor..."):
            outline = get_track_outline(calendar_year, selected_event['EventName'])
        if outline:
            figure, axis = plt.subplots(figsize=(8, 4.5))
            axis.plot(outline['X'], outline['Y'], color='#E10600', linewidth=3)
            axis.set_aspect('equal', 'datalim')
            axis.set_facecolor('#0B0E14')
            figure.patch.set_facecolor('#0B0E14')
            axis.axis('off')
            st.pyplot(figure, use_container_width=True)
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
                    f"<div style='font-weight:750;color:#edf5ff;margin-top:4px'>{html_lib.escape(item['text'])}</div></div>",
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
        st.link_button("Türkiye yayın bilgisi →", "https://www.beinsports.com.tr/", use_container_width=True)
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
            st.link_button("F1 TV uygunluğu →", "https://www.formula1.com/en/subscribe-to-f1-tv", use_container_width=True)
        with global_b:
            st.link_button("Resmî yayıncı listesi →", "https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ", use_container_width=True)

# SAYFA 5: TAKIMLAR VE PİLOTLAR
elif st.session_state['page'] == 'teams':
    st.markdown("## 👥 2026 Takımlar & Pilotlar")
    st.caption("2026 grid: 11 takım, 22 pilot. Logolar ve pilot portreleri güncel takım renkleriyle gösterilir.")
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
                if st.button(f"{team_name}\n{team['drivers'][0][1]} • {team['drivers'][1][1]}", key=f"team_{team_name}", use_container_width=True):
                    st.session_state['team_focus'] = team_name
                    st.rerun()

    selected_team_name = st.session_state['team_focus']
    selected_team = TEAM_DIRECTORY_2026[selected_team_name]
    st.markdown("---")
    logo_url = OFFICIAL_TEAM_LOGOS.get(selected_team_name) or get_official_team_logo(selected_team['slug'])
    header_left, header_middle, header_right = st.columns([.85, 2.55, 1.35])
    with header_left:
        st.markdown(
            f"<div class='hud-card' style='height:116px;border-top:4px solid {selected_team['color']};display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden'>"
            f"<img src='{logo_url or ''}' alt='{html_lib.escape(selected_team_name)}' style='max-height:76px;max-width:150px;object-fit:contain' onerror=\"this.style.display='none'\"></div>",
            unsafe_allow_html=True,
        )
    with header_middle:
        st.markdown(f"<div class='hud-label'>2026 TAKIM DOSYASI</div><div style='font-size:2rem;font-weight:900;color:#f8fbff;margin-top:2px'>{selected_team_name}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='history-copy' style='margin-top:8px'>{TEAM_HISTORY.get(selected_team_name, '')}</div>", unsafe_allow_html=True)
        st.link_button("Resmî takım profili ↗", f"https://www.formula1.com/en/teams/{selected_team['slug']}")
    with header_right:
        st.markdown(f"<div class='hud-card' style='margin-top:18px;border-left:4px solid {selected_team['color']}'><div class='hud-label'>KADRO</div><div class='hud-value'>2 Pilot</div><div class='driver-meta'>Resmî 2026 grid</div></div>", unsafe_allow_html=True)

    render_team_personnel_hud(selected_team_name, section='leader')

    driver_columns = st.columns(2)
    for column, driver in zip(driver_columns, selected_team['drivers']):
        name, code, number, image_path = driver
        career = driver_career_profile(code)
        with column:
            portrait = current_driver_portrait(selected_team_name, image_path)
            st.markdown(
                f"<div class='hud-card' style='min-height:382px;border-top:4px solid {selected_team['color']};overflow:hidden'>"
                f"<div style='height:182px;position:relative;display:flex;align-items:flex-end;justify-content:center;background:linear-gradient(180deg,rgba(15,30,47,.46),rgba(9,13,20,.02));border-radius:8px;overflow:hidden'>"
                f"<span style='position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:{selected_team['color']};opacity:.3;font-size:2.2rem;font-weight:950;letter-spacing:.1em'>{html_lib.escape(code)}</span>"
                f"<img src='{portrait}' alt='{html_lib.escape(name)}' style='position:relative;height:170px;max-width:100%;object-fit:contain;object-position:center bottom' onerror=\"this.style.display='none'\"></div>"
                f"<div style='font-size:1.22rem;font-weight:950;color:#f7fbff;margin-top:10px'>{html_lib.escape(name)} <span style='color:{selected_team['color']}'>{html_lib.escape(number)}</span></div>"
                f"<div class='driver-meta'>{html_lib.escape(code)} · {driver_age(code)} yaş · {html_lib.escape(selected_team_name)}</div>"
                f"<div class='history-copy' style='margin-top:9px'>{html_lib.escape(career['bio'])}</div>"
                f"<div style='display:flex;gap:8px;margin-top:10px'><div style='flex:1;background:#101a2b;border:1px solid #2d415b;border-radius:8px;padding:8px'><div class='hud-label'>GP GALİBİYETİ</div><div style='font-weight:950;color:{selected_team['color']};font-size:1.15rem;margin-top:3px'>{html_lib.escape(str(career['wins']))}</div></div><div style='flex:1;background:#101a2b;border:1px solid #2d415b;border-radius:8px;padding:8px'><div class='hud-label'>PODYUM</div><div style='font-weight:950;color:{selected_team['color']};font-size:1.15rem;margin-top:3px'>{html_lib.escape(str(career['podiums']))}</div></div></div>"
                f"<div style='margin-top:9px;color:#b9c8d9;font-size:.83rem;line-height:1.45'><b style='color:#f4f8ff'>Öne çıkan an:</b> {html_lib.escape(career['moment'])}</div></div>",
                unsafe_allow_html=True,
            )

    render_team_personnel_hud(selected_team_name, section='engineers')

    profile_driver = st.selectbox(
        "Pilot dosyasını aç",
        selected_team['drivers'],
        format_func=lambda item: f"{item[0]} ({item[1]})",
        key=f"driver_profile_{selected_team_name}",
    )
    render_driver_profile_hud(selected_team_name, profile_driver)

# SAYFA 6: ŞAMPİYONA MERKEZİ
elif st.session_state['page'] == 'standings':
    st.markdown("## 🏆 Puan Merkezi")
    st.caption("Puanlar tamamlanmış yarış ve sprint sonuçlarından hazırlanır. Sıra hücreleri: `yarış / sprint`.")
    st.markdown("<div class='hud-card'><div class='hud-label'>SEZON VERİSİ</div><div class='hud-value'>2026 sonuç tablosu</div><div class='history-copy' style='margin-top:6px'>Sayfa açıldığında tamamlanmış yarış ve sprint sonuçları doğrulanmış FastF1 paketinden otomatik hazırlanır. İlk açılışta kısa süre alabilir; sonraki açılışlar yerel önbellekten gelir.</div></div>", unsafe_allow_html=True)
    load_key = 'championship_data_ready_2026'
    if not st.session_state.get(load_key, False):
        st.session_state[load_key] = True
    if st.button("🔄 2026 puan verisini yenile", key='refresh_championship_2026', use_container_width=True):
        get_championship_data_v19.clear()
        st.session_state[load_key] = True

    driver_standings = pd.DataFrame()
    constructor_standings = pd.DataFrame()
    result_matrix = pd.DataFrame()
    points_matrix = pd.DataFrame()
    completed_rounds = []
    if st.session_state.get(load_key, False):
        try:
            with st.spinner("Sonuç tabloları hazırlanıyor..."):
                driver_standings, constructor_standings, result_matrix, points_matrix, completed_rounds = get_championship_data_stable(2026)
        except Exception:
            st.warning("Puan verisi şu an alınamadı. Ana sayfa ve diğer bölümler çalışmaya devam eder; daha sonra tekrar deneyebilirsin.")

    if st.session_state.get(load_key, False) and driver_standings.empty:
        st.info("Tamamlanmış yarışların doğrulanmış sonuçları henüz alınamadı.")
    elif not driver_standings.empty:
        render_html_hud(
            championship_snapshot_hud(driver_standings, constructor_standings, completed_rounds),
            height=150,
            scrolling=False,
        )
        round_labels = " • ".join(round_info['badge'] for round_info in completed_rounds)
        st.markdown(f"<div style='background:#111827;border:1px solid #263246;border-radius:10px;padding:14px 16px;margin:8px 0 20px'><div style='font-weight:800;color:#fff'>🏁 Tamamlanan pistler</div><div style='color:#94A3B8;margin-top:6px'>{round_labels}</div><div style='color:#94A3B8;font-size:.78rem;margin-top:8px'>Sprint hafta sonlarında hücre: yarış sırası / sprint sırası</div></div>", unsafe_allow_html=True)
        driver_tab, team_tab = st.tabs(["🏁 Sezon Tablosu", "🏭 Takım Puanları"])
        with driver_tab:
            if 'championship_matrix_mode' not in st.session_state:
                st.session_state['championship_matrix_mode'] = 'sıralama'
            sort_button, points_button = st.columns(2)
            with sort_button:
                if st.button("🏁 Sıralama", key='championship_show_positions', use_container_width=True):
                    st.session_state['championship_matrix_mode'] = 'sıralama'
            with points_button:
                if st.button("🟡 Puan", key='championship_show_points', use_container_width=True):
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
    st.markdown("### Favorilerin")
    st.write(f"Takım: **{st.session_state.get('favourite_team', 'Mercedes')}** | Pilot: **{st.session_state.get('favourite_driver', 'George Russell')}**")

# SAYFA 7: F2 VE F3
elif st.session_state['page'] == 'f2f3':
    st.markdown("## 🏎️ Formula 2 & Formula 3 Takip Merkezi")
    st.caption("2026 resmî kadroları. F1 ile aynı HUD düzeninde, fakat ayrı veri kaynağı ve ayrı puan merkezleriyle çalışır.")
    st.markdown("<div class='hud-card' style='border-left:4px solid #00b3ff;margin:8px 0 16px'><div class='hud-label'>JUNIOR PADDOCK // BETA 1.0</div><div class='history-copy' style='margin-top:6px'>Takım ve pilot kartları yerel 2026 kadro dizininden gelir. Sonuç/puan akışı ayrı bir doğrulanmış F2/F3 kaynağı bağlanana kadar F1 puan tablosuyla karıştırılmaz.</div></div>", unsafe_allow_html=True)
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
        st.markdown("### F2 // 2026 Grid")
        st.markdown("<div class='hud-card'><div class='hud-label'>FORMULA 2</div><div class='history-copy'>11 takım • 22 pilot • Sprint ve Feature Race sonuçları F1 puan merkezinden ayrı tutulur.</div></div>", unsafe_allow_html=True)
        render_junior_team_hud('f2', f2_grid, '#00b3ff', 'https://www.fiaformula2.com')
        st.link_button("Resmî Formula 2 merkezi ↗", "https://www.fiaformula2.com/", use_container_width=True)
    with series[1]:
        st.markdown("### F3 // 2026 Grid")
        st.markdown("<div class='hud-card'><div class='hud-label'>FORMULA 3</div><div class='history-copy'>10 takım • 30 pilot • F3 verileri F1 ve F2 ile karışmadan kendi yarış merkezi altında tutulur.</div></div>", unsafe_allow_html=True)
        render_junior_team_hud('f3', f3_grid, '#ffbe2e', 'https://www.fiaformula3.com')
        st.link_button("Resmî Formula 3 merkezi ↗", "https://www.fiaformula3.com/", use_container_width=True)

elif st.session_state['page'] == 'weekend':
    render_weekend_centre()

elif st.session_state['page'] == 'story':
    render_race_story_centre()

elif st.session_state['page'] == 'compare':
    render_driver_comparison_centre()

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

elif st.session_state['page'] == 'gridmaster':
    render_gridmaster()

elif st.session_state['page'] == 'team_manager':
    render_team_manager_game()

elif st.session_state['page'] == 'predictor':
    render_paddock_predictor()

elif st.session_state['page'] == 'draft':
    render_paddock_draft_game_v19()

# SAYFA 9: F1 SÖZLÜĞÜ
elif st.session_state['page'] == 'glossary':
    st.markdown("## ❓ F1 Sözlüğü")
    st.caption("2026 kuralları ve telemetri ekranlarımız için hazırlanmış 60 temel terim.")
    terms = [
        ('2026 Teknolojisi', 'Active Aero', 'Ön ve arka kanadın sürüş koşuluna göre aktif açı değiştirmesidir.', True),
        ('2026 Teknolojisi', 'Corner Mode', 'Virajlarda daha fazla yere basma için kullanılan aktif aero ayarıdır.', True),
        ('2026 Teknolojisi', 'Straight Mode', 'Düzlükte sürtünmeyi azaltan aktif aero ayarıdır.', True),
        ('2026 Teknolojisi', 'Overtake Mode', 'Öndeki araca yakın pilotun geçiş için kullanabildiği ek elektrik enerjisi desteğidir.', True),
        ('2026 Teknolojisi', 'Boost Mode', 'Pilotun savunma veya hücum için enerji dağıtımını kullandığı güç modudur.', True),
        ('2026 Teknolojisi', 'Recharge', 'Frenleme ve gaz kesme anlarında bataryanın yeniden enerji toplamasıdır.', True),
        ('2026 Teknolojisi', 'MGU-K', 'Fren enerjisini elektrik enerjisine çeviren ve güce katkı sağlayan motor-jeneratördür.', True),
        ('2026 Teknolojisi', 'ERS', 'Enerji geri kazanım ve elektrik enerjisi kullanım sistemidir.', True),
        ('2026 Teknolojisi', 'Power Unit', 'İçten yanmalı motor ve hibrit elektrik bileşenlerinin tamamına verilen isimdir.', True),
        ('2026 Teknolojisi', 'Sürdürülebilir Yakıt', '2026 güç ünitelerinde kullanılan sentetik ve sürdürülebilir kaynaklı yakıttır.', True),
        ('Yarış Hafta Sonu', 'FP1', 'Birinci antrenman seansıdır.', False),
        ('Yarış Hafta Sonu', 'FP2', 'İkinci antrenman seansıdır.', False),
        ('Yarış Hafta Sonu', 'FP3', 'Sıralama öncesindeki son antrenman seansıdır.', False),
        ('Yarış Hafta Sonu', 'Sıralama', 'Yarış başlangıç sırasını belirleyen seanstır.', False),
        ('Yarış Hafta Sonu', 'Q1', 'Sıralamanın ilk eleme bölümüdür.', False),
        ('Yarış Hafta Sonu', 'Q2', 'Sıralamanın ikinci eleme bölümüdür.', False),
        ('Yarış Hafta Sonu', 'Q3', 'Pole pozisyonunu belirleyen son eleme bölümüdür.', False),
        ('Yarış Hafta Sonu', 'Sprint Sıralaması', 'Sprint yarışının başlangıç dizilimini belirleyen kısa sıralama formatıdır.', False),
        ('Yarış Hafta Sonu', 'Sprint', 'Ana yarıştan daha kısa mesafeli ve puan veren yarıştır.', False),
        ('Yarış Hafta Sonu', 'Pole Pozisyonu', 'Ana yarışa ilk sıradan başlama hakkıdır.', False),
        ('Yarış Hafta Sonu', 'Parc Fermé', 'Araç ayarlarının büyük ölçüde kilitlendiği teknik kural dönemidir.', False),
        ('Yarış Hafta Sonu', 'Grid', 'Yarışın başlangıç dizilimidir.', False),
        ('Lastik & Strateji', 'Soft', 'En hızlı fakat genellikle en kısa ömürlü kuru zemin lastiğidir.', False),
        ('Lastik & Strateji', 'Medium', 'Hız ve dayanıklılık dengesi sunan kuru zemin lastiğidir.', False),
        ('Lastik & Strateji', 'Hard', 'Daha dayanıklı, ısınması daha zor kuru zemin lastiğidir.', False),
        ('Lastik & Strateji', 'Intermediate', 'Hafif veya değişken yağmur koşulları için lastiktir.', False),
        ('Lastik & Strateji', 'Wet', 'Yoğun yağmur ve çok ıslak pist için lastiktir.', False),
        ('Lastik & Strateji', 'Stint', 'Aynı lastik setiyle pit stop olmadan atılan tur bölümüdür.', False),
        ('Lastik & Strateji', 'Undercut', 'Rakibinden önce pite girip yeni lastikle avantaj aramaktır.', False),
        ('Lastik & Strateji', 'Overcut', 'Rakip pite girdikten sonra pistte kalıp avantaj aramaktır.', False),
        ('Lastik & Strateji', 'Degradation', 'Lastiğin tur geçtikçe performans kaybetmesidir.', False),
        ('Lastik & Strateji', 'Graining', 'Lastik yüzeyinde oluşan taneciklenmenin yol tutuşunu düşürmesidir.', False),
        ('Lastik & Strateji', 'Blistering', 'Aşırı sıcaklık nedeniyle lastik yüzeyinde kabarcık oluşmasıdır.', False),
        ('Lastik & Strateji', 'Pit Stop', 'Lastik değişimi veya onarım için pit alanına girilmesidir.', False),
        ('Veri & Telemetri', 'Delta', 'İki tur veya iki pilot arasındaki zaman farkıdır.', False),
        ('Veri & Telemetri', 'Sektör', 'Pistin zaman ölçülen üç ana parçasından biridir.', False),
        ('Veri & Telemetri', 'Mor Sektör', 'Seansta atılmış en hızlı sektör zamanıdır.', False),
        ('Veri & Telemetri', 'Speed Trap', 'Pistin belirli bir ölçüm noktasındaki resmî hızdır.', False),
        ('Veri & Telemetri', 'Top Speed', 'Bir turdaki en yüksek telemetri hızıdır.', False),
        ('Veri & Telemetri', 'Ortalama Hız', 'Pist uzunluğu ve tur süresinden türetilen ortalama hızdır.', False),
        ('Veri & Telemetri', 'Throttle', 'Gaz pedalının kullanım oranıdır.', False),
        ('Veri & Telemetri', 'Brake', 'Fren pedalının uygulandığı anları gösteren telemetri kanalıdır.', False),
        ('Veri & Telemetri', 'RPM', 'Motorun dakikadaki devir sayısıdır.', False),
        ('Veri & Telemetri', 'Telemetri', 'Araçtan gelen hız, gaz, fren, vites ve konum verilerinin bütünüdür.', False),
        ('Yarış Olayları', 'Safety Car', 'Pistte tehlike olduğunda araçları kontrollü hızda toplayan güvenlik aracıdır.', False),
        ('Yarış Olayları', 'VSC', 'Pistte fiziksel güvenlik aracı olmadan hız sınırı uygulayan sistemdir.', False),
        ('Yarış Olayları', 'Kırmızı Bayrak', 'Seansın güvenlik nedeniyle durdurulduğunu gösterir.', False),
        ('Yarış Olayları', 'Sarı Bayrak', 'Pistte tehlike olduğunu ve geçiş yasağı bulunduğunu gösterir.', False),
        ('Yarış Olayları', 'Track Limits', 'Pist sınırlarının ihlali nedeniyle tur veya ceza riski oluşmasıdır.', False),
        ('Yarış Olayları', 'DNF', 'Pilotun yarışı tamamlayamadığını gösterir.', False),
        ('Yarış Olayları', 'DNS', 'Pilotun yarışa başlayamadığını gösterir.', False),
        ('Yarış Olayları', 'DSQ', 'Pilotun veya takımın yarıştan diskalifiye edilmesidir.', False),
        ('Yarış Olayları', 'Race Control', 'Seans güvenliği, bayraklar ve kararları yöneten yarış kontrol birimidir.', False),
        ('Yarış Olayları', 'Ceza', 'Kural ihlali karşılığında verilen zaman, grid veya yarış içi yaptırımdır.', False),
        ('Pilotluk', 'Apex', 'Virajın ideal çizgideki en iç noktasıdır.', False),
        ('Pilotluk', 'Racing Line', 'Pistte en hızlı tur için tercih edilen ideal çizgidir.', False),
        ('Pilotluk', 'Slipstream', 'Öndeki aracın hava koridorunda sürtünme azalmasıyla hız kazanmadır.', False),
        ('Pilotluk', 'Dirty Air', 'Öndeki aracın bozduğu havanın takip eden aracın yere basmasını azaltmasıdır.', False),
        ('Pilotluk', 'Lift and Coast', 'Yakıt veya enerji yönetimi için fren öncesi gazdan erken çekilmektir.', False),
        ('Pilotluk', 'Late Braking', 'Viraja rakibinden daha geç fren yaparak atak denemektir.', False),
    ]
    category_names = ['Tümü'] + sorted({term[0] for term in terms})
    filter_col, search_col = st.columns([1, 2])
    with filter_col:
        selected_category = st.selectbox('Kategori', category_names)
    with search_col:
        search_text = st.text_input('Terim ara', placeholder='Örn. Active Aero, Under cut, Delta...').strip().lower()
    visible_terms = [term for term in terms if (selected_category == 'Tümü' or term[0] == selected_category) and (not search_text or search_text in (term[1] + ' ' + term[2]).lower())]
    st.caption(f"{len(visible_terms)} terim gösteriliyor")
    for category, term, explanation, is_new in visible_terms:
        badge = " <span class='new-badge'>2026 YENİ</span>" if is_new else f" <span class='term-badge'>{category.upper()}</span>"
        with st.expander(term):
            st.markdown(f"{badge}<p style='margin-top:10px'>{explanation}</p>", unsafe_allow_html=True)
