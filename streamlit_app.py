# -*- coding: utf-8 -*-
import os
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
import streamlit.components.v1 as components
import fastf1
import fastf1.plotting
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap
import numpy as np
import pandas as pd
import openf1_fallback

# Yeniden yapilandirma (redesign) — tasarim sistemi
from core import nav as fp_nav
from core import ui as fp_ui
from core import plot as fp_plot
from core import i18n as fp_i18n
from core.i18n import t as T


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
    initial_sidebar_state="expanded",
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
                    _st = str(row.get('Status', '')).strip()
                    if _st and _st.lower() != 'finished' and not _st.startswith('+') and 'lap' not in _st.lower():
                        _dnf.append(str(row.get('Abbreviation', '')).strip())
                if _dnf:
                    _names = ', '.join(d for d in _dnf[:3] if d)
                    _extra = f" +{len(_dnf) - 3}" if len(_dnf) > 3 else ""
                    summary.append(f"🔧 Yarışı tamamlayamayan: {_names}{_extra}.")

        return summary[:5]
    except Exception:
        return []


# 3. OTOMATİK TÜRKÇE ÇEVİRİ MOTORU
@st.cache_data(ttl=86400, show_spinner=False)
def translate_to_tr(text):
    """Gunluk onbellekli. Ayni basliklar tekrar cevrilmez -> hiz limiti sorunu azalir."""
    if not text:
        return ""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=tr&dt=t&q={urllib.parse.quote(text)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            translated = "".join([item[0] for item in data[0] if item[0]])
            return translated or text
    except Exception as error:
        log_data_error('translate_to_tr', error)
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
            f"<div style='font-size:1.7rem;font-weight:950;color:#f2f5f8;margin-top:3px'>{html_lib.escape(selected_team)}</div>"
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
                f"<div style='font-size:1.02rem;font-weight:900;color:#f2f5f8;margin-top:10px'>{html_lib.escape(driver_name)}</div>"
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


def championship_snapshot_hud(driver_standings, constructor_standings, rounds):
    """Puan Merkezi açıldığında önce görünen hızlı sezon özeti."""
    if driver_standings.empty or constructor_standings.empty:
        return ''
    driver = driver_standings.iloc[0]
    team = constructor_standings.iloc[0]
    driver_team = str(driver.get('Takım', ''))
    dc = team_colour(driver_team)
    tc = team_colour(str(team.get('Takım', '')))
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
      <div class='ss-c' style='--a:{dc}'><s>Pilot Lideri</s><b>{d_name}</b><i>{d_pts} P · {html_lib.escape(driver_team)}</i></div>
      <div class='ss-c' style='--a:{tc}'><s>Takım Lideri</s><b>{t_name}</b><i>{t_pts} P{(' · ' + gap) if gap else ''}</i></div>
      <div class='ss-c' style='--a:#f5c33b'><s>Kalan Yarış</s><b>{remaining}</b><i>{len(rounds)} tamamlandı</i></div>
    </div>
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
    return r'''<style>*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}.hud{border:1px solid #26313f;border-radius:13px;padding:12px;background:#11161f}.head{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.title{font-size:13px;font-weight:950;letter-spacing:.09em}.sub{font-size:10px;color:#9fb0c0;margin-top:5px}.tag{border:1px solid #35506d;border-radius:7px;padding:6px 8px;font-size:11px;font-weight:900;color:var(--team)}.map{margin-top:10px;border:1px solid #26313f;border-radius:10px;overflow:hidden;background:radial-gradient(circle at 50% 45%,#161d28,#07090d 74%)}canvas{width:100%;height:400px;display:block}.sectors{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}.sector{border:1px solid #2b3a4d;border-top:3px solid var(--c);border-radius:8px;padding:8px;background:#161d28;font:800 11px ui-monospace,Consolas,monospace}.sector small{display:block;color:#9fb0c0;font-family:Inter,Arial,sans-serif;margin-bottom:6px}.win{color:#79e7a7}.lose{color:#ff8793}.bottom{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn{border:1px solid #2b3a4d;border-radius:7px;background:#161d28;color:#f2f5f8;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3a0f12}.slider{flex:1;min-width:130px;accent-color:#ff4051}.delta{font:900 12px ui-monospace,Consolas,monospace;margin-left:auto}@media(max-width:650px){canvas{height:330px}.sectors{grid-template-columns:1fr}.delta{width:100%;margin-left:0}}</style><div class="hud"><div class="head"><div><div class="title">2D LAP DUEL // STABLE TIME SYNC</div><div class="sub">AYNI PİST NOKTASINDAKİ GERÇEK ZAMAN DELTASI · SEKTÖR ZAMANLARI</div></div><div id="tags"></div></div><div class="map"><canvas id="duel"></canvas></div><div class="sectors" id="sectors"></div><div class="bottom"><button class="btn" id="play">▶ Oynat</button><button class="btn active" data-rate="1">1×</button><button class="btn" data-rate="2">2×</button><button class="btn" data-rate="4">4×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="delta" id="delta"></span></div></div><script>
const data=__PAYLOAD__,drivers=data.drivers||[],canvas=document.getElementById('duel'),ctx=canvas.getContext('2d');let playing=false,rate=1,p=0,last=performance.now(),lastHud=0,view=null;const maxLap=Math.max(...drivers.map(d=>d.samples.lap_seconds||1));function sec(v){const x=String(v||'').split(':');return x.length===2?+x[0]*60+ +x[1]:NaN}function at(a,f){if(!a?.length)return null;const n=Math.max(0,Math.min(a.length-1,f*(a.length-1))),i=Math.floor(n),r=n-i,x=a[i],y=a[Math.min(a.length-1,i+1)];return{x:x.x+(y.x-x.x)*r,y:x.y+(y.y-x.y)*r,speed:x.speed+(y.speed-x.speed)*r,elapsed:x.elapsed+(y.elapsed-x.elapsed)*r}}function transform(){const raw=drivers[0]?.samples.distance||[],xs=raw.map(x=>x.x),ys=raw.map(x=>x.y),w=canvas.clientWidth,h=canvas.clientHeight,pd=28,s=Math.min((w-pd*2)/(Math.max(...xs)-Math.min(...xs)||1),(h-pd*2)/(Math.max(...ys)-Math.min(...ys)||1));return{minX:Math.min(...xs),maxY:Math.max(...ys),w,h,s}}function xy(x,t){return[(x.x-t.minX)*t.s+(t.w-(Math.max(...(drivers[0]?.samples.distance||[]).map(z=>z.x))-t.minX)*t.s)/2,(t.maxY-x.y)*t.s+(t.h-(t.maxY-Math.min(...(drivers[0]?.samples.distance||[]).map(z=>z.y)))*t.s)/2]}function advance(d){return Math.min(1,p*maxLap/(d.samples.lap_seconds||maxLap))}function drawCar(q,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.globalAlpha=done?.5:1;ctx.fillStyle='#060a10';ctx.fillRect(-12,-7,5,14);ctx.fillStyle=c;ctx.fillRect(-8,-4,22,8);ctx.fillRect(12,-8,3,16);ctx.fillStyle='#f3f7ff';ctx.fillRect(-16,-9,3,18);ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-15)}function draw(){if(!view)return;ctx.clearRect(0,0,view.w,view.h);const route=drivers[0]?.samples.distance||[];ctx.strokeStyle='#8094ad';ctx.globalAlpha=.7;ctx.lineWidth=3;ctx.beginPath();route.forEach((x,i)=>{const q=xy(x,view);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;drivers.forEach(d=>{const a=advance(d),here=at(d.samples.realtime,a),next=at(d.samples.realtime,Math.min(1,a+.004));if(!here||!next)return;const q=xy(here,view),qn=xy(next,view);drawCar(q,d.code,d.colour,a>=.999)})}function update(){const now=performance.now();if(now-lastHud<180)return;lastHud=now;const same=Math.min(advance(drivers[0]),advance(drivers[1])),a=at(drivers[0]?.samples.distance,same),b=at(drivers[1]?.samples.distance,same),raw=(a&&b&&Number.isFinite(a.elapsed)&&Number.isFinite(b.elapsed))?a.elapsed-b.elapsed:null;document.getElementById('delta').textContent=raw===null?'Delta bekleniyor':`Anlık Δ ${Math.abs(raw).toFixed(3)} sn · ${raw<0?drivers[0].code:raw>0?drivers[1].code:'eşit'} önde`;document.getElementById('range').value=Math.round(p*1000)}function sectors(){document.getElementById('tags').innerHTML=drivers.map(d=>`<span class="tag" style="--team:${d.colour}">${d.code} · ${d.lap}</span>`).join(' ');document.getElementById('sectors').innerHTML=[0,1,2].map(i=>{const a=drivers[0].sectors?.[i]||'—',b=drivers[1].sectors?.[i]||'—',d=sec(a)-sec(b),ok=Number.isFinite(d);return`<div class="sector" style="--c:${i===0?'#f4d35e':i===1?'#56cfe1':'#ff7a9f'}"><small>SEKTÖR ${i+1} · ${ok?(d<0?drivers[0].code:d>0?drivers[1].code:'EŞİT'):'—'} önde</small><div class="${ok&&d<=0?'win':'lose'}">${drivers[0].code} ${a}</div><div class="${ok&&d>=0?'win':'lose'}">${drivers[1].code} ${b}</div><div>Δ ${ok?Math.abs(d).toFixed(3)+' sn':'—'}</div></div>`}).join('')}function frame(now){const dt=Math.min(.03,Math.max(0,(now-last)/1000));last=now;if(playing){p+=dt*rate/maxLap;if(p>=1){p=1;playing=false;document.getElementById('play').textContent='↻ Baştan'}}draw();update();requestAnimationFrame(frame)}function resize(){const r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);view=transform();draw()}document.getElementById('play').onclick=()=>{if(p>=1)p=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat'};document.querySelectorAll('[data-rate]').forEach(b=>b.onclick=()=>{rate=+b.dataset.rate;document.querySelectorAll('[data-rate]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{p=+e.target.value/1000;playing=false;draw();update()};window.addEventListener('resize',resize);sectors();resize();requestAnimationFrame(frame);
</script></div>'''.replace('__PAYLOAD__', packed)


def two_driver_duel_html_repaired(*args, **kwargs):
    """2D d\u00fcello: tek ara\u00e7 modeli, \u00f6n kanat ve entegre pist katmanlar\u0131."""
    markup = two_driver_duel_html_stable(*args, **kwargs)
    old_car = "function drawCar(q,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.globalAlpha=done?.5:1;ctx.fillStyle='#060a10';ctx.fillRect(-12,-7,5,14);ctx.fillStyle=c;ctx.fillRect(-8,-4,22,8);ctx.fillRect(12,-8,3,16);ctx.fillStyle='#f3f7ff';ctx.fillRect(-16,-9,3,18);ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-15)}"
    new_car = "function drawCar(q,a,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.rotate(a);ctx.globalAlpha=done?.5:1;ctx.fillStyle='#05080d';[[-9,-10,6,5],[-9,5,6,5],[7,-10,6,5],[7,5,6,5]].forEach(w=>ctx.fillRect(...w));ctx.fillStyle=c;ctx.fillRect(-10,-5,23,10);ctx.fillRect(10,-3,8,6);ctx.fillStyle='#111a27';ctx.beginPath();ctx.ellipse(1,0,5,4,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f3f7ff';ctx.fillRect(17,-10,3,20);ctx.fillRect(14,-8,8,3);ctx.fillRect(14,5,8,3);ctx.fillStyle='#dce8f7';ctx.fillRect(5,-1,6,2);ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-15)}"
    markup = markup.replace(old_car, new_car).replace("drawCar(q,d.code,d.colour,a>=.999)", "drawCar(q,Math.atan2(qn[1]-q[1],qn[0]-q[0]),d.code,d.colour,a>=.999)")
    overlay = "function drawOverlay(){const o=data.overlay||{},route=drivers[0]?.samples?.distance||[];if(!route.length)return;const mark=(f,label,col)=>{const p=at(route,f);if(!p)return;const q=xy(p,view);ctx.fillStyle=col;ctx.beginPath();ctx.arc(q[0],q[1],3.5,0,Math.PI*2);ctx.fill();ctx.fillStyle='#f2f5f8';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)};const zone=(z,label,col)=>{ctx.beginPath();for(let i=0;i<=24;i++){const p=at(route,z.start+(z.end-z.start)*i/24),q=xy(p,view);i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1])}ctx.strokeStyle=col;ctx.lineWidth=5;ctx.globalAlpha=.92;ctx.stroke();ctx.globalAlpha=1;mark(z.start,label,col)};mark(0,'START / FINISH','#ffffff');(o.sectors||[]).forEach(x=>mark(x.fraction,x.label,x.colour||'#f4d35e'));(o.pit||[]).forEach(x=>mark(x.fraction,x.label,'#b79cff'));(o.straights||[]).forEach((z,i)=>zone(z,i===0?'SM - Straight Mode':'OM - Overtake Mode',i===0?'#45c8ff':'#71e6a1'))}"
    markup = markup.replace("function draw(){if(!view)return;", overlay + "function draw(){if(!view)return;")
    markup = markup.replace("ctx.globalAlpha=1;drivers.forEach(d=>{const a=advance(d)", "ctx.globalAlpha=1;drawOverlay();drivers.forEach(d=>{const a=advance(d)")
    return markup




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
      .weather{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}.weather div{background:#0d1725;border:1px solid #26394f;border-radius:7px;padding:7px;font-size:10px;color:#96abc0}.weather b{display:block;color:#f2f5f8;font-size:13px;margin-top:3px}.strip{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.pilot{border:1px solid #334b68;border-left:4px solid var(--team);border-radius:7px;background:#111d2e;color:#f2f5f8;font-size:11px;font-weight:900;padding:6px 8px;cursor:pointer}.pilot.active{background:#21344c;box-shadow:0 0 0 1px var(--team) inset}.control{margin-top:10px;border-top:1px solid #26394f;padding-top:9px}.control h4{margin:0 0 6px;font-size:11px;letter-spacing:.08em}.msg{font-size:10px;color:#b7c7d7;border-left:3px solid #ffcc62;padding:5px 7px;margin:5px 0;background:#171a1b}.note{font-size:10px;color:#8299b3;margin-top:8px}
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





# Shared replay HUD: portrait, tyre history, pits and track-mode overlays.

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
    return f"""<style>body{{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}}.wall{{border:1px solid #2c425c;border-radius:13px;background:#11161f;overflow:hidden}}.head{{padding:13px 15px;border-bottom:1px solid #2b4058;font-size:13px;font-weight:950;letter-spacing:.08em}}.sub{{font-size:10px;color:#91a8bf;margin-top:5px}}.row{{display:grid;grid-template-columns:110px 1fr 58px;gap:10px;align-items:center;min-height:54px;padding:8px 12px;border-top:1px solid #23364b;border-left:4px solid var(--team)}}.driver{{font-weight:950;color:var(--team)}}.driver small,.finish small{{display:block;font-size:10px;color:#8fa6bd;margin-top:4px}}.stints{{display:flex;min-width:380px;height:29px;border-radius:6px;overflow:hidden;background:#0a111b;gap:2px}}.stint{{min-width:20px;display:flex;align-items:center;justify-content:center;gap:5px;background:color-mix(in srgb,var(--tyre) 23%,#11161f);border-top:3px solid var(--tyre);color:#f6f9ff;font-size:11px;font-weight:950}}.stint small{{font-size:9px;color:#bdcadd}}.finish{{font-weight:950;text-align:right}}@media(max-width:700px){{.row{{grid-template-columns:84px 1fr 42px;padding:8px}}.stints{{min-width:220px}}.stint small{{display:none}}}}</style><div class='wall'><div class='head'>TYRE STRATEGY WALL<div class='sub'>HER BLOK BİR STINT • ÇİZGİLER PIT STOP GEÇİŞLERİNİ GÖSTERİR • TOPLAM {total} TUR</div></div><div class='scroll'>{''.join(rows)}</div></div>"""


def strategy_wall_component_height(payload):
    """Lastik duvarının tüm 20+ pilotunu ana sayfada görünür tutar."""
    return min(1560, max(320, 105 + len(payload.get('cars', [])) * 54))


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
                f"<div style='min-width:0;flex:1'><div class='hud-label'>PILOT SEZONU</div><div style='font-size:1.18rem;font-weight:950;color:#f2f5f8'>{html_lib.escape(driver_info['name'])}</div><div class='driver-meta'>{html_lib.escape(code)} | Grid sirasi P{driver_rank[code]}</div>"
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
                f"<div><div class='hud-label'>P{row['Rank']} | {row['Points']} PUAN</div><div style='font-size:1.1rem;font-weight:950;color:#f2f5f8'>{html_lib.escape(driver_info.get('name', row['Driver']))}</div><div class='driver-meta'>{html_lib.escape(row['Team'])}</div></div></div>"
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












def manager_driver_rating(code, category='race'):
    quali, race, wet, tyres = DRIVER_GAME_STATS.get(code, (75, 75, 75, 75))
    return {'quali': quali, 'race': race, 'wet': wet, 'tyres': tyres}.get(category, race)


def team_color(team_name):
    """Oyun HUD'u için takım rengini güvenli şekilde döndürür."""
    return TEAM_DIRECTORY_2026.get(str(team_name), {}).get('color', '#94a3b8')














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








def render_favourites_centre():
    team_name = st.session_state.get('favourite_team', 'Mercedes')
    driver_name = st.session_state.get('favourite_driver', 'George Russell')
    render_page_header(T('page.favourites.title'), T('page.favourites.sub'))
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
    fp_ui.page_header(T("page.assistant.title"), T("page.assistant.sub"), eyebrow=T("section.paddock"))
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




def news_matches_team_v19(item, team_name):
    if team_name == 'Genel F1':
        return True
    team = TEAM_DIRECTORY_2026.get(team_name, {})
    words = [team_name.lower(), str(team.get('slug', '')).lower()]
    words.extend(str(driver[0]).lower().split()[-1] for driver in team.get('drivers', []))
    haystack = (str(item.get('title', '')) + ' ' + str(item.get('desc', ''))).lower()
    return any(word and word in haystack for word in words)




def draft_driver_rating_v19(code):
    known = {'VER':96,'NOR':94,'LEC':93,'HAM':92,'RUS':91,'PIA':91,'ALO':89,'SAI':88,'ANT':86,'ALB':85,'GAS':84,'HAD':82,'OCO':81,'BEA':81,'HUL':80,'LAW':80,'STR':78,'BOR':77,'COL':76,'LIN':75}
    return known.get(str(code), 75)






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


# redesign: global .f1-header banner kaldirildi — her sayfa kendi fp_ui.page_header'ini
# (veya mevcut "## Baslik" basligini) kullaniyor.

# ==========================================
# SOL MENÜ (SIDEBAR & NAVİGASYON)
# ==========================================

# redesign: slim-rail marka kilidi (eski brand + inline style kaldirildi)
fp_ui.sidebar_brand()


# redesign: kucuk TR | EN dil secici (gercek i18n — core/i18n.py)
fp_i18n.lang_toggle()

# redesign: acik/koyu artik sag-alt kontrol panosunda (istemci tarafi, aninda).
light_mode_v31 = False
st.markdown(
    "<style>.status-dot-v31{display:inline-block;width:8px;height:8px;border-radius:50%;"
    "background:var(--fp-green);box-shadow:0 0 8px var(--fp-green)}</style>",
    unsafe_allow_html=True,
)

_nav_now = st.session_state['page']


def _nav(label, icon, key):
    """redesign: slim-rail nav satiri. Tiklaninca sayfayi degistirir + rerun."""
    if fp_ui.nav_button(label, icon, key, _nav_now):
        st.session_state['page'] = key
        st.rerun()


def _navk(icon, key):
    _nav(T(f"nav.{key}"), icon, key)


fp_ui.sidebar_section(T("section.general"))
_navk("home", "home")
_navk("newspaper", "news")

fp_ui.sidebar_section(T("section.data"))
_navk("monitoring", "telemetry")

fp_ui.sidebar_section(T("section.live"))
_navk("sensors", "live")
_navk("calendar_month", "calendar")
_navk("flag", "weekend")
_navk("menu_book", "story")
_navk("compare_arrows", "compare")
_navk("badge", "drivers")

fp_ui.sidebar_section(T("section.paddock"))
_navk("school", "learn")
_navk("star", "favourites")

fp_ui.sidebar_section(T("section.champ"))
_navk("groups", "teams")
_navk("emoji_events", "standings")
_navk("stacked_line_chart", "f2f3")
_navk("quiz", "glossary")
_navk("smart_toy", "assistant")

fp_ui.sidebar_section(T("section.games"))
_navk("sports_esports", "games")

with st.sidebar.expander("⭐ Hızlı Favori", expanded=False):
    favourite_team = st.selectbox("Takım", list(TEAM_DIRECTORY_2026.keys()), key="favourite_team")
    favourite_drivers = TEAM_DIRECTORY_2026[favourite_team]['drivers']
    favourite_driver = st.selectbox("Pilot", [driver[0] for driver in favourite_drivers], key="favourite_driver")
    st.caption(f"Favorin: {favourite_team} — {favourite_driver}")

st.sidebar.caption("Formula Paddock · Bağımsız F1 veri ve oyun merkezi")

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
    render_page_header(T('page.news.title'), T('page.news.sub'))
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
        st.dataframe(pd.DataFrame(points).rename(columns={'position':'Sıra', 'code':'Pilot', 'team':'Takım', 'points':'Puan'}), use_container_width=True, hide_index=True)
    with st.expander('Tam yarış sonucu', expanded=False):
        render_html_hud(session_leaderboard_html(table, f'{selected} // YARIŞ SONUCU'), height=leaderboard_component_height(table), scrolling=False)


def render_learning_centre_v20():
    render_page_header(T('page.learn.title'), T('page.learn.sub'))
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








_render_games_hub_v20 = render_games_hub




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






# Existing routes remain unchanged and now call the repaired market and no-date Stewarlde.
render_paddock_draft_game_v19 = render_paddock_draft_game_v22


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
STEWARDLE_ACTIVE_API_IDS_V24 = {
    'RUS': 'russell', 'ANT': 'antonelli', 'HAM': 'hamilton', 'LEC': 'leclerc',
    'NOR': 'norris', 'PIA': 'piastri', 'VER': 'max_verstappen', 'HAD': 'hadjar',
    'LAW': 'lawson', 'LIN': 'lindblad', 'GAS': 'gasly', 'COL': 'colapinto',
    'OCO': 'ocon', 'BEA': 'bearman', 'SAI': 'sainz', 'ALB': 'albon',
    'HUL': 'hulkenberg', 'BOR': 'bortoleto', 'ALO': 'alonso', 'STR': 'stroll',
    'PER': 'perez', 'BOT': 'bottas',
}
















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
    fp_ui.page_header(
        "Stewardle",
        "2010–2026 F1 pilot havuzu · kaynak dogrulamali galibiyet, sampiyonluk, GP starti ve ilk GP yili bulmacasi.",
        eyebrow="Oyunlar",
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


@st.cache_data(ttl=21600, show_spinner=False)
def build_stable_race_replay_payload(year, event_name):
    """Load compact OpenF1 history first and use heavy FastF1 only as fallback."""
    openf1_payload = openf1_fallback.build_race_replay(int(year), str(event_name))
    if isinstance(openf1_payload, dict) and openf1_payload.get('ok'):
        valid, reason = validate_stable_replay_payload(openf1_payload)
        if valid:
            payload = _replay_overlay_v26(openf1_payload)
            payload['replay_source'] = 'OpenF1 doğrulanmış tur, konum, sıra, pit ve lastik kayıtları.'
            payload['version'] = '3.2-openf1-fast'
            return payload
        openf1_reason = reason
    else:
        openf1_reason = openf1_payload.get('reason', '') if isinstance(openf1_payload, dict) else ''

    payload = _build_stable_race_replay_payload_v25(year, event_name)
    if not isinstance(payload, dict) or not payload.get('ok'):
        fastf1_reason = payload.get('reason', '') if isinstance(payload, dict) else ''
        return {'ok': False, 'reason': ' · '.join(item for item in (openf1_reason, fastf1_reason) if item)}
    payload = _replay_overlay_v26(payload)
    payload['version'] = '3.2-fastf1-fallback'
    payload['replay_source'] = 'FastF1 doğrulanmış tur, sıra, pit ve lastik kayıtları.'
    return payload


def stable_race_replay_html(payload):
    """Canvas-only replay HUD with an explicit schematic pit lane.

    The track is a clean FastF1 telemetry lap. Pit entry/exit timestamps are
    verified session data, but their exact coordinates are not part of the
    public lap telemetry. The off-track lane is therefore visibly labelled
    *schematic* instead of being presented as GPS.
    """
    packed = fp_ui.json_for_script(_replay_overlay_v26(dict(payload)))
    return r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#f2f5f8;font-family:Inter,Segoe UI,Arial,sans-serif}.r{border:1px solid #2d435e;border-radius:14px;padding:14px;background:linear-gradient(135deg,#11161f,#09101a)}.top{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-size:14px;font-weight:950;letter-spacing:.1em}.sub{font-size:11px;color:#91a8c0;margin-top:5px}.badge{border:1px solid #365170;border-radius:8px;padding:7px 10px;color:#79e7ae;font-size:11px;font-weight:900}.legend{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.key{border:1px solid #334d69;border-radius:99px;padding:5px 8px;font-size:10px;font-weight:850;color:#bcd0e4;background:#101d2f}.key i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}.grid{display:grid;grid-template-columns:minmax(0,1fr) 292px;gap:12px;margin-top:12px}.map{border:1px solid #29405a;border-radius:11px;background:radial-gradient(circle at 50% 45%,#17263d,#07090d 74%);overflow:hidden}.map canvas{width:100%;height:510px;display:block}.panel{border:1px solid #2c425d;border-radius:11px;background:#11161f;padding:12px}.hero{border-bottom:1px solid #2b4058;padding:0 0 10px;margin-bottom:8px;min-height:74px}.hero b{font-size:21px;color:var(--team)}.hero small{display:block;color:#a9bbcd;margin-top:5px}.hero img{float:right;width:65px;height:82px;object-fit:contain;object-position:right bottom;margin:-8px -4px -2px 8px}.stat{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #26394f;font-size:12px;gap:8px}.stat span{color:#92a7bc}.pit{color:#ffd46b}.on{color:#81e6ac}.tyrebar{height:8px;border-radius:99px;background:#07101a;overflow:hidden;margin:7px 0 2px}.tyrebar i{display:block;height:100%;background:var(--tyre);width:var(--wear)}.controls,.strip{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px}.btn,.pilot{border:1px solid #39516f;border-radius:7px;background:#142239;color:#f2f5f8;font-weight:900;padding:7px 9px;cursor:pointer}.btn.active{border-color:#ff4757;background:#3b1822}.pilot{border-left:4px solid var(--team);font-size:11px}.pilot.active{background:#1c3049;box-shadow:0 0 0 1px var(--team) inset}.slider{accent-color:#ff4051;flex:1;min-width:135px}.clock{font:900 12px ui-monospace,Consolas,monospace}.note{font-size:10px;color:#8ea4bc;line-height:1.45;margin-top:10px}@media(max-width:850px){.grid{grid-template-columns:1fr}.map canvas{height:390px}}
</style></head><body><div class="r"><div class="top"><div><div class="title">RACE CONTROL // VERIFIED REPLAY</div><div class="sub" id="sub"></div></div><div class="badge">● DOĞRULANMIŞ YARIŞ AKIŞI</div></div><div class="legend"><span class="key"><i style="background:#45c8ff"></i>Straight Mode</span><span class="key"><i style="background:#71e6a1"></i>Overtake olasılığı</span><span class="key"><i style="background:#b79cff"></i>Pit giriş / çıkış</span><span class="key"><i style="background:#ffd46b"></i>Pit şeridi şematik</span></div><div class="grid"><div><div class="map"><canvas id="track"></canvas></div><div class="controls"><button class="btn active" id="play">❚❚ Duraklat</button><button class="btn active" data-speed="1">1× Gerçek</button><button class="btn" data-speed="5">5×</button><button class="btn" data-speed="20">20×</button><input id="range" class="slider" type="range" min="0" max="1000" value="0"><span class="clock" id="clock"></span></div><div class="strip" id="strip"></div><div class="note">Pist: temiz FastF1 telemetrisi. Sıra, tur, lastik ve pit zamanları doğrulanmış kayıttır. Pit şeridi koordinatı yayımlanmadığı için görsel şematiktir.</div></div><aside class="panel" id="panel"></aside></div></div><script>
const data=__PAYLOAD__,cars=data.cars||[],route=data.track||[],overlay=data.overlay||{},canvas=document.getElementById('track'),ctx=canvas.getContext('2d');let selected=cars[0]?.code||'',playing=true,speed=1,time=0,last=performance.now(),lastHud=0,lastKey='',view=null;const tyres={SOFT:'#ff4655',MEDIUM:'#ffd344',HARD:'#f1f4f8',INTERMEDIATE:'#45dc78',WET:'#42a9ff'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));const fmt=n=>{n=Math.max(0,Math.round(n));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')};
function lap(c,t){const a=c.laps||[];for(let i=0;i<a.length;i++)if(t<=a[i].end)return a[i];return a[a.length-1]||null}function pitEvent(c,t){return(c.pit_events||[]).find(e=>t>=e.start&&t<=e.end)||null}function state(c,t){const l=lap(c,t),a=c.laps||[],last=a[a.length-1],out=!!c.retired&&t>=(last?.end||0);if(!l)return{lap:0,frac:0,pos:c.grid||20,pit:false,out};const i=a.indexOf(l),previous=a[Math.max(0,i-1)]?.position||l.start_position||c.grid||20,frac=Math.max(0,Math.min(1,(t-l.start)/(l.end-l.start||1)));return{lap:l.lap,frac,pos:frac>.997?(l.position||previous):previous,pit:!out&&!!pitEvent(c,t),out}}
function point(f){const n=route.length;if(!n)return{x:0,y:0,a:0};const p=((f%1)+1)%1*n,i=Math.floor(p),r=p-i,a=route[i],b=route[(i+1)%n];return{x:a[0]+(b[0]-a[0])*r,y:a[1]+(b[1]-a[1])*r,a:Math.atan2(b[1]-a[1],b[0]-a[0])}}function visual(c,t){const s=state(c,t),start=Math.max(0,1-Math.min(1,t/4)),grid=((c.grid||1)-1)*.0013*start;return point(s.frac-grid)}
function transform(){const xs=route.map(p=>p[0]),ys=route.map(p=>p[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=canvas.clientWidth,h=canvas.clientHeight,p=30,s=Math.min((w-p*2)/(maxX-minX||1),(h-p*2)/(maxY-minY||1));return{minX,maxX,minY,maxY,w,h,s}}function xy(p,t){return[(p.x-t.minX)*t.s+(t.w-(t.maxX-t.minX)*t.s)/2,(t.maxY-p.y)*t.s+(t.h-(t.maxY-t.minY)*t.s)/2]}
function mark(f,label,col){const q=xy(point(f),view);ctx.fillStyle=col;ctx.beginPath();ctx.arc(q[0],q[1],4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#eaf4ff';ctx.font='bold 9px Arial';ctx.textAlign='left';ctx.fillText(label,q[0]+6,q[1]-6)}function zone(z,label,col){if(!Number.isFinite(z.start)||!Number.isFinite(z.end))return;ctx.beginPath();for(let i=0;i<=28;i++){const q=xy(point(z.start+(z.end-z.start)*i/28),view);i?ctx.lineTo(...q):ctx.moveTo(...q)}ctx.strokeStyle=col;ctx.lineWidth=6;ctx.globalAlpha=.9;ctx.stroke();ctx.globalAlpha=1;mark(z.start,label,col)}
function pitPath(){const marks=overlay.pit||[],pin=marks.find(x=>String(x.label||'').includes('IN'))?.fraction??.972,pout=marks.find(x=>String(x.label||'').includes('OUT'))?.fraction??.032,a=xy(point(pin),view),b=xy(point(pout),view),dx=b[0]-a[0],dy=b[1]-a[1],len=Math.max(1,Math.hypot(dx,dy)),nx=-dy/len,ny=dx/len,dir=(a[0]+b[0])/2<view.w/2?1:-1,offset=Math.min(58,Math.max(34,view.w*.065))*dir;return{a,b,c:[a[0]+nx*offset,a[1]+ny*offset],d:[b[0]+nx*offset,b[1]+ny*offset]}}function bez(p0,p1,p2,p3,u){const v=1-u;return{x:v*v*v*p0[0]+3*v*v*u*p1[0]+3*v*u*u*p2[0]+u*u*u*p3[0],y:v*v*v*p0[1]+3*v*v*u*p1[1]+3*v*u*u*p2[1]+u*u*u*p3[1]}}function pitPoint(event,t){const p=pitPath(),u=Math.max(0,Math.min(1,(t-event.start)/(event.end-event.start||1))),q=bez(p.a,p.c,p.d,p.b,u),q2=bez(p.a,p.c,p.d,p.b,Math.min(1,u+.012));return{x:q.x,y:q.y,a:Math.atan2(q2.y-q.y,q2.x-q.x)}}
function drawLane(){const p=pitPath();ctx.beginPath();ctx.moveTo(...p.a);ctx.bezierCurveTo(...p.c,...p.d,...p.b);ctx.strokeStyle='#ffd46b';ctx.lineWidth=4;ctx.setLineDash([7,5]);ctx.globalAlpha=.9;ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;ctx.fillStyle='#ffd46b';ctx.font='bold 9px Arial';ctx.textAlign='center';ctx.fillText('PIT LANE (şematik)',(p.c[0]+p.d[0])/2,(p.c[1]+p.d[1])/2-8)}
function car(x,y,a,c,code,chosen,pit){ctx.save();ctx.translate(x,y);ctx.rotate(-a);ctx.fillStyle='#050a10';ctx.fillRect(-12,-7,5,14);ctx.fillRect(10,-8,4,16);ctx.fillStyle=c;ctx.fillRect(-8,-4,21,8);ctx.fillRect(8,-2,9,4);ctx.fillRect(13,-8,3,16);ctx.fillStyle='#f4f7ff';ctx.fillRect(-16,-9,3,18);ctx.fillRect(0,-1,8,2);if(pit){ctx.strokeStyle='#ffd44b';ctx.lineWidth=2;ctx.strokeRect(-19,-12,39,24)}if(chosen){ctx.strokeStyle='#fff';ctx.lineWidth=1.3;ctx.strokeRect(-21,-14,43,28)}ctx.restore();ctx.fillStyle=c;ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.fillText(code,x,y-15)}
function draw(){if(!view||!route.length)return;ctx.clearRect(0,0,view.w,view.h);ctx.strokeStyle='#8094ad';ctx.globalAlpha=.72;ctx.lineWidth=4;ctx.beginPath();route.forEach((p,i)=>{const q=xy({x:p[0],y:p[1]},view);i?ctx.lineTo(...q):ctx.moveTo(...q)});ctx.closePath();ctx.stroke();ctx.globalAlpha=1;(overlay.straights||[]).forEach((z,i)=>zone(z,i?'OM · OVERTAKE MODE':'SM · STRAIGHT MODE',i?'#71e6a1':'#45c8ff'));mark(0,'START / FINISH','#fff');(overlay.sectors||[]).forEach(x=>mark(x.fraction,x.label,x.colour||'#f4d35e'));(overlay.pit||[]).forEach(x=>mark(x.fraction,x.label,'#b79cff'));drawLane();cars.forEach(c=>{const s=state(c,time);if(s.out)return;const e=pitEvent(c,time);let q,p;if(e){p=pitPoint(e,time);q=[p.x,p.y]}else{p=visual(c,time);q=xy(p,view)}car(q[0],q[1],p.a,c.colour,c.code,c.code===selected,!!e)})}
function order(){return cars.filter(c=>!state(c,time).out).sort((a,b)=>{const x=state(a,time),y=state(b,time);return x.pos-y.pos||(y.lap+y.frac)-(x.lap+x.frac)})}function lastPit(c){const e=(c.pit_events||[]).filter(x=>x.end<=time).at(-1);return e?'Tur '+e.lap:'Henüz yok'}
function update(){const now=performance.now();if(now-lastHud<220)return;lastHud=now;const list=order(),key=list.map(c=>c.code+state(c,time).pos+state(c,time).lap).join('|')+selected;if(key!==lastKey){lastKey=key;document.getElementById('strip').innerHTML=list.map(c=>{const s=state(c,time);return`<button class="pilot ${c.code===selected?'active':''}" style="--team:${c.colour}" data-c="${c.code}">P${s.pos} · ${c.code} · T${s.lap}</button>`}).join('');document.querySelectorAll('.pilot').forEach(b=>b.onclick=()=>{selected=b.dataset.c;lastKey='';lastHud=0;update()})}const c=cars.find(x=>x.code===selected)||cars[0],s=state(c,time),l=lap(c,time),compound=(l?.compound||'—').toUpperCase(),p=pitEvent(c,time),move=(c.grid&&s.pos)?c.grid-s.pos:0,wear=Math.max(8,100-Math.round(100*(s.frac||0)));const profile=c.profile||{},photo=profile.photo?`<img src="${esc(profile.photo)}" alt="">`:'';document.getElementById('panel').style.setProperty('--team',c.colour);document.getElementById('panel').style.setProperty('--tyre',tyres[compound]||'#9db1c8');document.getElementById('panel').innerHTML=`<div class="hero">${photo}<b>${esc(profile.name||c.code)} · P${s.pos}</b><small>${esc(c.team)} · ${esc(profile.flag||'')} ${esc(c.code)}</small></div><div class="stat"><span>Tur</span><b>${s.lap} / ${data.total_laps}</b></div><div class="stat"><span>Başlangıç → bitiş</span><b>P${c.grid||'—'} → P${c.final_position||'—'}</b></div><div class="stat"><span>Pozisyon değişimi</span><b>${move>0?'↑ '+move:move<0?'↓ '+Math.abs(move):'→ 0'} sıra</b></div><div class="stat"><span>Stint / lastik</span><b>${l?.stint||'—'} · ${compound}</b></div><div class="tyrebar" style="--wear:${wear}%"><i></i></div><div class="stat"><span>Son pit</span><b>${lastPit(c)}</b></div><div class="stat"><span>Pit durumu</span><b class="${p?'pit':'on'}">${p?'PIT LANE':'PİSTTE'}</b></div>`;document.getElementById('range').value=Math.round(1000*time/(data.total_seconds||1));document.getElementById('clock').textContent=fmt(time)+' / '+fmt(data.total_seconds)}
let raf=0,lastPaint=0;function startLoop(){if(!raf){last=performance.now();raf=requestAnimationFrame(frame)}}function frame(now){raf=0;const dt=Math.min(.05,Math.max(0,(now-last)/1000));last=now;if(!playing)return;time+=dt*speed;if(time>=data.total_seconds){time=data.total_seconds;playing=false;document.getElementById('play').textContent='↻ Baştan'}if(now-lastPaint>=33||!playing){lastPaint=now;draw();update()}if(playing)raf=requestAnimationFrame(frame)}function resize(){const r=canvas.getBoundingClientRect(),d=Math.min(1.5,devicePixelRatio||1);canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);view=transform();draw();lastHud=0;update()}document.getElementById('play').onclick=()=>{if(time>=data.total_seconds)time=0;playing=!playing;document.getElementById('play').textContent=playing?'❚❚ Duraklat':'▶ Oynat';if(playing)startLoop();else{draw();update()}};document.querySelectorAll('[data-speed]').forEach(b=>b.onclick=()=>{speed=Number(b.dataset.speed);document.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('active',x===b))});document.getElementById('range').oninput=e=>{time=Number(e.target.value)/1000*data.total_seconds;playing=false;document.getElementById('play').textContent='▶ Oynat';lastHud=0;draw();update()};document.addEventListener('visibilitychange',()=>{if(document.hidden){playing=false;document.getElementById('play').textContent='▶ Oynat'}});document.getElementById('sub').textContent=(data.event||'Formula 1')+' · '+data.total_laps+' tur · doğrulanmış yarış saati';window.addEventListener('resize',resize);resize();startLoop();
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
        is_dnf = bool(status) and status.lower() != 'finished' and not status.startswith('+') and 'lap' not in status.lower()
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


def get_driver_full_profile_v33(api_code):
    """Pilotlar sayfasi icin tam profil. Aktarim hatasi ONBELLEGE ALINMAZ:
    ham hesap istisna firlatir (Streamlit istisnayi cache'lemez), gecici ag
    sorunu pilotu 12 saat 'veri yok' durumunda birakmaz."""
    if not api_code:
        return {'ok': False}
    try:
        return _driver_full_profile_raw_v33(api_code)
    except LookupError:
        return {'ok': False, 'empty': True}
    except Exception as error:
        log_data_error('driver full profile', error)
        return {'ok': False}


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _driver_full_profile_raw_v33(api_code):
    rows = _career_verified_rows_v28(api_code)
    if not rows:
        raise LookupError('no verified career rows for ' + str(api_code))

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
        is_dnf = bool(status) and status.lower() != 'finished' and not status.startswith('+') and 'lap' not in status.lower()

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
      .pg s{{font:700 8.5px 'Saira Condensed',sans-serif;letter-spacing:.11em;text-transform:uppercase;color:#63748a;text-decoration:none}}
      .pg b{{display:block;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:17px;margin-top:6px}}
      .pg b.g{{color:#38e1d0}} .pg b.r{{color:#e10600}}
      @media(max-width:640px){{.pg{{grid-template-columns:repeat(3,1fr)}}}}
      @media(max-width:400px){{.pg{{grid-template-columns:repeat(2,1fr)}}}}
    </style>
    <div class="ph">
      <div class="pt"><span class="c">{html_lib.escape(str(code))}</span>
        <span class="w"><b>{html_lib.escape(str(name))}</b>
          <span>{flag_img} {meta} {no_html}</span>
        </span>
      </div>
      {grid}
    </div>
    """


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
        if st.button("← Pilot listesine dön", key="drivers_back_v33"):
            st.session_state.pop('driver_view_v33', None)
            st.rerun()
        fp_ui.anchor("fp-driver-detail")
        _info = directory_driver_by_code(code)
        colour = team_colour(team) if team else colour
        with st.spinner("Kariyer kaydı okunuyor…"):
            prof = get_driver_full_profile_v33(api)
        _titles = _driver_titles_v33(api, code)
        render_html_hud(driver_profile_header_html(name, code, nation or _info.get('team', ''), number, prof, colour, _titles),
                        height=340)

        if prof.get('ok'):
            if prof.get('seasons'):
                st.write("")
                fp_ui.section_title("Sezon Dökümü")
                _sdf = pd.DataFrame([{
                    'Sezon': s['year'], 'Takım': s['team'], 'Yarış': s['races'],
                    'Puan': s['points'], 'Galibiyet': s['wins'],
                    'En İyi': f"P{s['best']}" if s['best'] else '—',
                } for s in prof['seasons']])
                st.dataframe(_sdf, use_container_width=True, hide_index=True)

            _cols = st.columns([1, 1])
            with _cols[0]:
                if prof.get('circuit_wins'):
                    fp_ui.section_title("Pist Bazında Galibiyet")
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
                    fp_ui.section_title("Pist Bazında Galibiyet")
                    fp_ui.data_state("Galibiyet Yok", "Bu pilotun doğrulanmış kayıtlarında Grand Prix galibiyeti bulunmuyor.", "info")
            with _cols[1]:
                if prof.get('teams'):
                    fp_ui.section_title("Takımlar")
                    _tg = ''.join(
                        f"<div style='display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid #1b2330'>"
                        f"<b style='font:700 13px Saira Condensed,sans-serif;text-transform:uppercase;color:{team_colour(tn)}'>{html_lib.escape(tn)}</b>"
                        f"<span style='font:12px JetBrains Mono,monospace;color:#9fb0c0'>{y0}{'–' + str(y1) if y1 != y0 else ''}</span></div>"
                        for tn, y0, y1 in prof['teams']
                    )
                    st.markdown(f"<div class='hud-card' style='padding:14px 16px'>{_tg}</div>", unsafe_allow_html=True)

            st.write("")
            _years = sorted({r['year'] for r in prof['races'] if r['year']}, reverse=True)
            fp_ui.section_title("Yarış-Yarış Sonuçlar")
            _yr = st.selectbox("Sezon", _years, index=0, key="drivers_race_year_v33") if _years else None
            _year_races = [r for r in prof['races'] if r['year'] == _yr]
            _rdf = pd.DataFrame([{
                'Tur': r['round'] or '—', 'Yarış': r['race'], 'Grid': r['grid'] or '—',
                'Sonuç': ('DNF' if r['dnf'] else f"P{r['pos']}" if str(r['pos']).isdigit() else r['pos']),
                'Puan': r['points'], 'Durum': r['status'],
            } for r in sorted(_year_races, key=lambda r: r['round'])])
            st.caption(f"{_yr} · {len(_year_races)} yarış")
            st.dataframe(_rdf, use_container_width=True, hide_index=True, height=min(430, 44 + 35 * max(1, len(_year_races))))

        if st.session_state.pop('_scroll_driver', False):
            fp_ui.scroll_to("fp-driver-detail")
        return

    # --- DIZIN GORUNUMU ---
    only_2026 = st.toggle("Yalnızca 2026 gridi", value=True, key="drivers_only_2026_v33")
    q = st.text_input("Pilot ara", placeholder="Örn. Verstappen, HAM, Alonso", key="drivers_q_v33").strip().lower()
    shown = [d for d in directory if (d[7] or not only_2026) and (not q or q in d[1].lower() or q in d[0].lower())]
    shown.sort(key=lambda d: (not d[7], d[1]))
    st.caption(f"{len(shown)} pilot")
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
                if st.button("Profili aç", key=f"drv_{api}", use_container_width=True):
                    st.session_state['driver_view_v33'] = api
                    st.session_state['_scroll_driver'] = True
                    st.rerun()


def driver_deep_stats_hud_html(name, code, team, stats, scope, colour):
    if not stats.get('verified'):
        return ("<div style='padding:16px;color:#9fb0c0;font-family:Saira,sans-serif'>"
                "Bu pilot icin dogrulanmis kariyer verisi su an alinamadi.</div>")
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
        cell("Yaris Disi (DNF)", stats['dnf'], 'r'),
        cell("En Iyi Bitis", best),
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
      .g s{{font:700 9px 'Saira Condensed',sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#63748a;text-decoration:none}}
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
        f1_car = r"""function drawCar(q,a,n,c,done){ctx.save();ctx.translate(q[0],q[1]);ctx.rotate(a);ctx.scale(.82,.82);ctx.globalAlpha=done?.52:1;ctx.shadowColor=c;ctx.shadowBlur=10;ctx.fillStyle='#05080d';[[-12,-11,7,6],[-12,5,7,6],[7,-11,7,6],[7,5,7,6]].forEach(w=>ctx.fillRect(...w));ctx.shadowBlur=0;ctx.fillStyle=c;ctx.fillRect(-10,-5,23,10);ctx.fillRect(-2,-7,10,14);ctx.fillRect(7,-3,12,6);ctx.fillStyle='#101924';ctx.beginPath();ctx.ellipse(-1,0,5.5,4.5,0,0,Math.PI*2);ctx.fill();ctx.fillStyle='#dce9f8';ctx.fillRect(-18,-11,4,22);ctx.fillRect(-15,-9,7,4);ctx.fillRect(-15,5,7,4);ctx.fillStyle='#f2f5f8';ctx.fillRect(18,-13,4,26);ctx.fillRect(14,-10,9,4);ctx.fillRect(14,6,9,4);ctx.fillStyle='#89a4bd';ctx.fillRect(0,-1,8,2);ctx.restore();ctx.fillStyle=c;ctx.font='900 10px Arial';ctx.textAlign='center';ctx.fillText(n,q[0],q[1]-17)}"""
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

PIT_WALL_PERSONNEL_2026 = {
    "Mercedes": {
        "principal": "Toto Wolff",
        "strategy": "Rosie Wait",
        "chief": "James Allison",
        "engineers": [("George Russell", "Marcus Dudley"), ("Kimi Antonelli", "Peter Bonnington")],
        "source": "https://www.mercedesamgf1.com/team",
    },
    "Ferrari": {
        "principal": "Fred Vasseur",
        "strategy": "Ravin Jain",
        "chief": "Loïc Serra",
        "engineers": [("Charles Leclerc", "Bryan Bozzi"), ("Lewis Hamilton", "Riccardo Adami")],
        "source": "https://www.ferrari.com/en-EN/formula1/team",
    },
    "McLaren": {
        "principal": "Andrea Stella",
        "strategy": "Randeep Singh",
        "chief": "Rob Marshall",
        "engineers": [("Lando Norris", "Will Joseph"), ("Oscar Piastri", "Tom Stallard")],
        "source": "https://www.mclaren.com/racing/formula-1/2026/who-sits-on-mclarens-pit-wall/",
    },
    "Red Bull Racing": {
        "principal": "Laurent Mekies",
        "strategy": "Hannah Schmitz",
        "chief": "Pierre Waché",
        "engineers": [("Max Verstappen", "Gianpiero Lambiase"), ("Isack Hadjar", "Richard Wood")],
        "source": "https://www.redbullracing.com/int-en/projects/bulls-guide-to-the-pit-wall/bulls-guide-to-the-pit-wall-hot-seats",
    },
    "Alpine": {
        "principal": "Steve Nielsen",
        "strategy": "Kamuya açık değil",
        "chief": "David Sanchez",
        "engineers": [("Pierre Gasly", "John Howard"), ("Franco Colapinto", "Stuart Barlow")],
        "source": "https://www.alpinef1.com/team",
    },
    "Racing Bulls": {
        "principal": "Alan Permane",
        "strategy": "Kamuya açık değil",
        "chief": "Guillaume Cattelani",
        "engineers": [("Liam Lawson", "Mattia Spini"), ("Arvid Lindblad", "Pierre Hamelin")],
        "source": "https://www.visacashapprb.com/en/team",
    },
    "Haas F1 Team": {
        "principal": "Ayao Komatsu",
        "strategy": "Mike Caulfield",
        "chief": "Andrea De Zordo",
        "engineers": [("Esteban Ocon", "Francesco Nenci"), ("Oliver Bearman", "Ronan O'Hare")],
        "source": "https://www.haasf1team.com/our-team",
    },
    "Williams": {
        "principal": "James Vowles",
        "strategy": "Kamuya açık değil",
        "chief": "Pat Fry",
        "engineers": [("Carlos Sainz", "Gaëtan Jego"), ("Alexander Albon", "James Urwin")],
        "source": "https://www.williamsf1.com/team",
    },
    "Audi": {
        "principal": "Jonathan Wheatley",
        "strategy": "Kamuya açık değil",
        "chief": "Mattia Binotto",
        "engineers": [("Nico Hülkenberg", "Steven Petrik"), ("Gabriel Bortoleto", "José Manuel López")],
        "source": "https://www.audi.com/en/sport/motorsport/formula-1/",
    },
    "Aston Martin": {
        "principal": "Adrian Newey",
        "strategy": "Andy Cowell",
        "chief": "Enrico Cardile",
        "engineers": [("Fernando Alonso", "Chris Cronin"), ("Lance Stroll", "Andrew Vizard")],
        "source": "https://www.astonmartinf1.com/en-GB/news/announcement/aston-martin-aramco-announces-changes-to-leadership-structure",
    },
    "Cadillac": {
        "principal": "Graeme Lowdon",
        "strategy": "Kamuya açık değil",
        "chief": "Pat Symonds",
        "engineers": [("Sergio Perez", "Kamuya açık değil"), ("Valtteri Bottas", "Kamuya açık değil")],
        "source": "https://www.cadillacf1team.com/",
    },
}


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
        st.link_button("Takım kaynağını aç ↗", source, use_container_width=True)


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


def _rotate_options_v30(values, answer, seed):
    unique = []
    for value in values:
        text = str(value)
        if text not in unique:
            unique.append(text)
    correct = str(answer)
    alternatives = [item for item in unique if item != correct]
    if alternatives:
        shift = seed % len(alternatives)
        alternatives = alternatives[shift:] + alternatives[:shift]
    picks = [correct] + alternatives[:3]
    if len(picks) > 1:
        shift = seed % len(picks)
        picks = picks[shift:] + picks[:shift]
    return picks


def gridmaster_questions_v30(difficulty="Zor"):
    drivers = stewarlde_drivers()
    day = datetime.date.today().toordinal()
    driver_by_code = {item['code']: item for item in drivers}
    teammate = {}
    for team in TEAM_DIRECTORY_2026.values():
        first, second = team['drivers'][0][1], team['drivers'][1][1]
        teammate[first], teammate[second] = second, first
    names = [item['name'] for item in drivers]
    teams = list(TEAM_DIRECTORY_2026)
    numbers = ['#' + item['number'] for item in drivers]
    nations = [item['nation'] for item in drivers]
    debuts = [item['debut'] for item in drivers]
    questions = []
    for turn in range(10):
        driver = drivers[(day * 5 + turn * 7) % len(drivers)]
        mate = driver_by_code[teammate[driver['code']]]
        kind = (day + turn) % 10
        base = {'id': f"v30_{day}_{turn}_{driver['code']}", 'driver': driver, 'points': 140 if difficulty == 'Zor' else 200}
        if kind == 0:
            base.update(prompt="Bu üç ipucunun anlattığı pilot kim?", clue=f"{driver['nation']} · F1 başlangıcı {driver['debut']} · Araç #{driver['number']}", answer=driver['name'], options=_rotate_options_v30(names, driver['name'], day + turn))
        elif kind == 1:
            base.update(prompt=f"{driver['name']} ile aynı takımda yarışan pilot kim?", clue=f"Takım adı gizli · Kod {driver['code']}", answer=mate['name'], options=_rotate_options_v30(names, mate['name'], day + turn * 2))
        elif kind == 2:
            answer = driver['name'] if driver['age'] > mate['age'] else mate['name']
            base.update(prompt="Bu takım arkadaşlarından hangisi daha yaşlı?", clue=f"{driver['name']} / {mate['name']}", answer=answer, options=[driver['name'], mate['name']])
        elif kind == 3:
            base.update(prompt="Bu pilot ikilisinin 2026 takımı hangisi?", clue=f"{driver['name']} + {mate['name']}", answer=driver['team'], options=_rotate_options_v30(teams, driver['team'], day + turn))
        elif kind == 4:
            base.update(prompt=f"{driver['name']} F1'e hangi sezonda başladı?", clue="Çaylak yılı bilgisi", answer=str(driver['debut']), options=_rotate_options_v30(debuts, driver['debut'], day + turn))
        elif kind == 5:
            base.update(prompt="Pilot kimliğini araç numarasından bul.", clue=f"2026 araç numarası #{driver['number']} · Ülke {driver['nation']}", answer=driver['name'], options=_rotate_options_v30(names, driver['name'], day + turn))
        elif kind == 6:
            base.update(prompt=f"{driver['name']} hangi ülke koduyla yarışıyor?", clue=f"{driver['team']} · #{driver['number']}", answer=driver['nation'], options=_rotate_options_v30(nations, driver['nation'], day + turn))
        elif kind == 7:
            base.update(prompt=f"{driver['name']} için doğru araç numarası hangisi?", clue=f"{driver['team']} · F1 başlangıcı {driver['debut']}", answer='#' + driver['number'], options=_rotate_options_v30(numbers, '#' + driver['number'], day + turn))
        elif kind == 8:
            base.update(prompt="Bu kariyer ipuçları hangi pilota ait?", clue=f"{driver['titles']} dünya şampiyonluğu · başlangıç {driver['debut']} · ülke {driver['nation']}", answer=driver['name'], options=_rotate_options_v30(names, driver['name'], day + turn * 3))
        else:
            answer = driver['name'] if driver['debut'] < mate['debut'] else mate['name']
            base.update(prompt="Hangisi Formula 1'e daha önce başladı?", clue=f"{driver['name']} / {mate['name']}", answer=answer, options=[driver['name'], mate['name']])
        questions.append(base)
    return questions


def render_game_engine_banner_v30(title, colour):
    profile = st.session_state.setdefault('paddock_game_profile_v30', {'xp': 0, 'played': 0, 'best_streak': 0})
    fp_ui.page_header(
        title,
        f"Paddock Oyun Motoru 3.0 · XP {profile['xp']} · tamamlanan oyun {profile['played']} · en iyi seri {profile['best_streak']}",
        eyebrow="Oyunlar",
    )


def render_gridmaster_v30():
    fp_ui.page_header("GridMaster", "10 soruluk mucadele · seri carpani · telsiz jokeri · rutbe sistemi.", eyebrow="Oyunlar")
    difficulty = st.segmented_control("Seviye", ["Zor", "Uzman"], default="Zor", key="gridmaster_level_v30") if hasattr(st, 'segmented_control') else st.radio("Seviye", ["Zor", "Uzman"], horizontal=True, key="gridmaster_level_v30")
    questions = gridmaster_questions_v30(difficulty)
    today = datetime.date.today().isoformat()
    state_key = 'gridmaster_state_v30'
    state = st.session_state.get(state_key)
    if not state or state.get('day') != today or state.get('difficulty') != difficulty:
        state = {'day': today, 'difficulty': difficulty, 'index': 0, 'score': 0, 'streak': 0, 'best': 0, 'answers': [], 'feedback': None, 'radio_used': False, 'finished': False}
        st.session_state[state_key] = state
    render_game_engine_banner_v30("10 Soruluk F1 Bilgi Mücadelesi", "#f7c948")
    st.caption("Sorular pilotun cevabını ele vermez. Seri yaptıkça puan çarpanın yükselir; bir kez telsiz ipucu kullanabilirsin.")
    if not state['finished']:
        question = questions[state['index']]
        progress = (state['index'] / 10) * 100
        multiplier = min(3.0, 1.0 + state['streak'] * .25)
        st.markdown(
            f"<div class='hud-card grid-question-v30'><div class='hud-label'>SORU {state['index'] + 1}/10 · {state['score']} PUAN · x{multiplier:.2f} SERİ</div>"
            f"<div class='grid-prompt-v30'>{html_lib.escape(question['prompt'])}</div><div class='grid-clue-v30'>{html_lib.escape(question['clue'])}</div>"
            f"<div class='grid-progress-v30'><i style='width:{progress}%'></i></div></div>", unsafe_allow_html=True)
        if not state['radio_used']:
            if st.button("📻 Telsiz ipucu", key=f"grid_radio_v30_{state['index']}"):
                state['radio_used'] = True
                st.session_state[state_key] = state
                st.rerun()
        else:
            answer_text = str(question['answer'])
            st.info(f"Telsiz: Doğru cevap {len(answer_text)} karakterden oluşuyor ve '{answer_text[0]}' ile başlıyor.")
        selected = st.radio("Cevabın", question['options'], index=None, key=f"grid_pick_v30_{today}_{difficulty}_{state['index']}", disabled=state['feedback'] is not None)
        if state['feedback'] is None:
            if st.button("Cevabı kilitle", type="primary", use_container_width=True, disabled=selected is None, key=f"grid_lock_v30_{state['index']}"):
                correct = str(selected) == str(question['answer'])
                gained = int(question['points'] * min(3.0, 1.0 + state['streak'] * .25)) if correct else 0
                state['streak'] = state['streak'] + 1 if correct else 0
                state['best'] = max(state['best'], state['streak'])
                state['score'] += gained
                state['feedback'] = {'correct': correct, 'selected': selected, 'gained': gained}
                state['answers'].append({'Soru': state['index'] + 1, 'Cevabın': selected, 'Doğru cevap': question['answer'], 'Puan': gained})
                st.session_state[state_key] = state
                st.rerun()
        else:
            feedback = state['feedback']
            if feedback['correct']:
                st.success(f"Doğru! +{feedback['gained']} puan. Seri devam ediyor.")
            else:
                st.error(f"Yanlış. Doğru cevap: {question['answer']}")
            if st.button("Sonraki soru →" if state['index'] < 9 else "Sonucu gör →", type="primary", use_container_width=True, key=f"grid_next_v30_{state['index']}"):
                state['index'] += 1
                state['feedback'] = None
                state['radio_used'] = False
                state['finished'] = state['index'] >= 10
                if state['finished']:
                    profile = st.session_state.setdefault('paddock_game_profile_v30', {'xp': 0, 'played': 0, 'best_streak': 0})
                    profile['xp'] += state['score']
                    profile['played'] += 1
                    profile['best_streak'] = max(profile['best_streak'], state['best'])
                st.session_state[state_key] = state
                st.rerun()
    else:
        rank = "PIT WALL ELİT" if state['score'] >= 2400 else "BAŞ MÜHENDİS" if state['score'] >= 1600 else "YARIŞ MÜHENDİSİ" if state['score'] >= 900 else "ÇAYLAK ANALİST"
        st.success(f"10 soru tamamlandı · {state['score']} puan · En iyi seri {state['best']} · Rütbe: {rank}")
        st.dataframe(pd.DataFrame(state['answers']), use_container_width=True, hide_index=True)
        if st.button("Yeni 10 soruluk mücadele", use_container_width=True, key="grid_reset_v30"):
            st.session_state.pop(state_key, None)
            st.rerun()


_render_team_manager_before_v30 = render_team_manager_game
_render_predictor_before_v30 = render_paddock_predictor
_render_draft_before_v30 = render_paddock_draft_game_v19


def render_team_manager_game_v30():
    render_game_engine_banner_v30("Takım Patronu Kariyeri", "#2ee6c9")
    st.caption("Kararlar anında yerel simülasyonda işlenir; sayfanın açılması için dış veri beklenmez.")
    _render_team_manager_before_v30()


def render_paddock_predictor_v30():
    render_game_engine_banner_v30("Paddock Tahmin", "#7dd3fc")
    st.caption("Pole ve podyum tahminini kaydet; tamamlanan yarış geldiğinde doğrulanmış sonuçla puanlanır.")
    _render_predictor_before_v30()


def render_paddock_draft_game_v30():
    render_game_engine_banner_v30("Paddock Draft Kariyeri", "#a78bfa")
    st.caption("Tüm aktif grid yerel pazardan anında açılır; bütçe, uyum ve sponsor bonusu sonraki sezona taşınır.")
    _render_draft_before_v30()


def render_paddock_career_alpha_v01():
    """Instant browser prototype while the full Unity WebGL production is prepared."""
    fp_ui.page_header(
        "Paddock Career · Surus Prototipi",
        "Alpha 0.3 · Paddock Ring GP · sabit yakin takip · tam-pist minimap · fizik tabanli AI.",
        eyebrow="Oyunlar",
    )
    game_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paddock_ring_alpha.html")
    try:
        with open(game_path, "r", encoding="utf-8") as game_file:
            game_markup = game_file.read()
        components.html(game_markup, height=790, scrolling=False)
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
      .gp-stat span{{font:700 9px 'Saira Condensed',sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#63748a}}
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
    render_html_hud(games_profile_hud_html(_gp), height=126)
    games = [
        ("TARİHÎ BULMACA", "Stewardle", "Gerçek kariyer verisiyle pilotu bul.", "#ff385c", "Stewardle aç", "stewarlde"),
        ("10 SORULUK MÜCADELE", "GridMaster", "Zor sorular, seri çarpanı, telsiz jokeri ve rütbe sistemi.", "#f7c948", "Mücadeleyi aç", "gridmaster"),
        ("KARİYER", "Takım Patronu", "Pilot, lastik, tempo ve bütçe kararlarıyla sezon yönet.", "#2ee6c9", "Kariyeri aç", "team_manager"),
        ("KADRO PAZARI", "Paddock Draft", "İki pilot seç, uyum kur ve sponsor bütçeni büyüt.", "#a78bfa", "Draftı aç", "draft"),
        ("YARIŞ TAHMİNİ", "Paddock Tahmin", "Pole ve podyum tahminini gerçek sonuçla karşılaştır.", "#7dd3fc", "Tahmini aç", "predictor"),
        ("SÜRÜŞ ALPHA 0.3", "Paddock Career", "Yeni yumuşak GP pistinde yakın takip kamerası, sabit minimap ve fren yapan AI ile yarış.", "#e10600", "Motoru çalıştır", "paddock_career"),
    ]
    for start in range(0, len(games), 2):
        columns = st.columns(2)
        for column, game in zip(columns, games[start:start + 2]):
            label, title, description, colour, button_text, page = game
            with column:
                st.markdown(f"<div class='hud-card game-card-v24' style='border-top:5px solid {colour}'><div class='hud-label'>{label}</div><div class='game-card-title-v24'>{title}</div><div class='history-copy' style='margin-top:8px'>{description}</div></div>", unsafe_allow_html=True)
                if st.button(button_text, key=f"games_v30_{page}", use_container_width=True):
                    st.session_state['page'] = page
                    st.rerun()
    st.markdown("---")
    render_pit_wall_v30()


render_gridmaster = render_gridmaster_v30
render_team_manager_game = render_team_manager_game_v30
render_paddock_predictor = render_paddock_predictor_v30
render_paddock_draft_game_v19 = render_paddock_draft_game_v30
render_games_hub = render_games_hub_v30

st.markdown(r"""
<style>
.pit-person-v30{min-height:120px!important;margin-top:8px!important}.pit-name-v30{font-size:1.18rem;font-weight:950;color:var(--fp-text);margin:8px 0 5px}.engine-banner-v30{margin-bottom:14px!important}.engine-title-v30{font-size:1.35rem;font-weight:950;color:var(--fp-text);margin:5px 0}.grid-question-v30{border-left:5px solid #f7c948!important;margin-top:14px!important}.grid-prompt-v30{font-size:1.25rem;font-weight:950;margin-top:12px}.grid-clue-v30{font-size:.94rem;color:var(--fp-muted);margin-top:9px;padding:10px;border-radius:9px;background:color-mix(in srgb,var(--fp-panel2) 75%,#f7c948 8%)}.grid-progress-v30{height:7px;background:var(--fp-panel2);border-radius:99px;margin-top:15px;overflow:hidden}.grid-progress-v30 i{display:block;height:100%;background:linear-gradient(90deg,#f7c948,#ff385c);border-radius:99px}.game-card-v24{transition:transform .15s ease,border-color .15s ease}.game-card-v24:hover{transform:translateY(-2px)}
@media(max-width:800px){.pit-person-v30{min-height:98px!important}.grid-prompt-v30{font-size:1.08rem}}
</style>
""", unsafe_allow_html=True)


# Final authoritative theme layer. This comes after legacy visual patches so
# light/dark mode cannot be overwritten by an older hard-coded dark selector.
st.markdown(f"""
<style>
html,body,#root,.stApp,[data-testid="stApp"],[data-testid="stAppViewContainer"]{{
  color:var(--fp-text)!important;
  background-color:var(--fp-page)!important;
  background-image:linear-gradient(var(--fp-grid) 1px,transparent 1px),linear-gradient(90deg,var(--fp-grid) 1px,transparent 1px),radial-gradient(circle at 82% 8%,var(--fp-glow),transparent 31%),linear-gradient(135deg,var(--fp-page),var(--fp-page2))!important;
  background-size:44px 44px,44px 44px,100% 100%,100% 100%!important;
  animation:none!important;
}}
[data-testid="stAppViewContainer"]::before,[data-testid="stAppViewContainer"]::after{{display:none!important;animation:none!important}}
[data-testid="stHeader"]{{background:color-mix(in srgb,var(--fp-page) 92%,transparent)!important}}
section[data-testid="stSidebar"]{{background:linear-gradient(180deg,var(--fp-panel),var(--fp-panel2))!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important;box-shadow:8px 0 24px var(--fp-shadow)!important}}
section[data-testid="stSidebar"] *,section[data-testid="stSidebar"] p,section[data-testid="stSidebar"] label{{color:var(--fp-text)!important}}
.nav-section-v29{{color:var(--fp-muted)!important;background:linear-gradient(90deg,color-mix(in srgb,#e10600 12%,var(--fp-panel)),transparent)!important}}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button{{background:linear-gradient(90deg,var(--fp-panel2),var(--fp-panel))!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important;box-shadow:0 5px 13px var(--fp-shadow)!important;transition:border-color .14s ease,transform .14s ease!important}}
section[data-testid="stSidebar"] div[data-testid="stButton"]>button:hover{{background:var(--fp-panel2)!important;color:var(--fp-text)!important;border-color:#259ad4!important;transform:translateX(1px)!important}}
section[data-testid="stSidebar"] [data-testid="stExpander"]{{background:var(--fp-panel2)!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important}}
.f1-header,.hud-card,.metric-card,.news-card,.driver-card,.career-panel-v28,.career-metric-v28,[data-testid="stMetric"],[data-testid="stAlert"],div[data-testid="stExpander"]{{background:linear-gradient(145deg,var(--fp-panel),var(--fp-panel2))!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important;box-shadow:0 10px 26px var(--fp-shadow)!important}}
.f1-header h1,.hud-value,.news-title,.metric-card .value,.career-metric-v28 b,h1,h2,h3,h4{{color:var(--fp-text)!important}}
.f1-header p,.history-copy,.driver-meta,.news-desc,.metric-card .title,.career-hero-v28 p,.career-source-v28,[data-testid="stCaptionContainer"]{{color:var(--fp-muted)!important}}
div[data-testid="stButton"]>button,[data-baseweb="select"]>div,input,textarea{{background:var(--fp-panel2)!important;color:var(--fp-text)!important;border-color:var(--fp-line)!important}}
.status-dot-v31{{animation:none!important;box-shadow:0 0 9px rgba(104,231,174,.7)!important}}
*{{scrollbar-color:var(--fp-line) var(--fp-panel2)}}
</style>
""", unsafe_allow_html=True)

# redesign: kabuk temasi (arka plan + slim-rail menu) EN SONDA —
# eski !important bloklarini yener
fp_ui.inject_shell_theme()
fp_ui.control_dock()

if st.session_state['page'] == 'home':
    # redesign: F1 TV yonu — page_header + yaris sonuc basligi + sakin race center
    fp_ui.page_header(T("page.home.title"), T("page.home.sub"), eyebrow="Formula Paddock")

    # İlk kare hiçbir dış kaynağı beklemez. Böylece FastF1/cache bağlantısı
    # problemliyken bile navigasyon ve arayüz görünür kalır.
    if not st.session_state['home_data_requested']:
        fp_ui.data_state(
            "PADDOCK BAGLANTISI HAZIR",
            "Site guvenli modda aninda acildi. Yaris merkezi ve haberleri yalnizca sen istediginde dogrulanmis kaynaktan yukler.",
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

    # --- YARIS SONUC BASLIGI: kim, hangi takim, kac saniye farkla kazandi ---
    # Sadece son tamamlanan seans YARIS ise ve dogrulanmis sonuc varsa gorunur.
    if last_session and str(last_session.get('session_code')) == 'R' and real_drivers:
        _w = real_drivers[0]
        _wd = directory_driver_by_code(_w['code'])
        _gap = real_drivers[1]['time'] if len(real_drivers) > 1 else ''
        if isinstance(_gap, str) and _gap.startswith('+'):
            _gap = _gap + ' sn'
        _runners = []
        for _r in real_drivers[1:3]:
            _rd = directory_driver_by_code(_r['code'])
            _pos = str(_r['name']).split('.')[0].strip() + '.'
            _runners.append((_pos, _r['code'], _rd['team'] or 'Formula 1'))
        fp_ui.result_hero(
            last_session['event_name'], 'Yaris',
            _wd['name'] or _w['code'], _wd['team'] or '', _gap, _runners,
        )

    # Gercek Pirelli hamur renkleri — yuvarlak lastik rozeti (yan duvar seridi gibi)
    _TYRE_COL = {'S': '#da291c', 'M': '#ffd100', 'H': '#f0f0f0', 'I': '#43b02a', 'W': '#0067ad'}
    ticker_html_items = ""
    for d in real_drivers:
        colour = DRIVER_TEAMS.get(d["code"], {"color": "#63748a"})["color"]
        letter = (str(d.get("tyre", "")).strip()[:1] or "").upper()
        pill = ""
        if letter in _TYRE_COL:
            _tc = _TYRE_COL[letter]
            pill = (f"<span style='display:inline-flex;align-items:center;justify-content:center;"
                    f"width:20px;height:20px;border-radius:50%;border:3px solid {_tc};"
                    f"background:radial-gradient(circle at 50% 40%,#20262f,#0b0e13);color:{_tc};"
                    f"font:800 9px JetBrains Mono,monospace;flex:0 0 auto;box-shadow:inset 0 0 4px rgba(0,0,0,.6)'>{letter}</span>")
        ticker_html_items += (
            f"<div style='flex:0 0 auto;min-width:130px;padding:10px 13px;border-left:3px solid {colour};"
            f"border-right:1px solid #1b2330;display:flex;flex-direction:column;gap:4px'>"
            f"<span style='font:700 15px Saira Condensed,sans-serif;letter-spacing:.04em;color:{colour}'>{html_lib.escape(str(d['name']))}</span>"
            f"<span style='font:12px JetBrains Mono,monospace;color:#9fb0c0'>{html_lib.escape(str(d['time']))}</span>"
            f"{pill}</div>"
        )

    _ticker_marquee = bool(real_drivers)
    if _ticker_marquee:
        # LED panosu gibi: icerik iki kez -> kesintisiz sola akis
        _ticker_dur = max(24, len(real_drivers) * 3)
        ticker_body = (
            f"<div class='rc-track' style='animation-duration:{_ticker_dur}s'>"
            f"{ticker_html_items}{ticker_html_items}</div>"
        )
    else:
        ticker_body = ("<div style='width:100%;text-align:center;color:#63748a;font-weight:600;padding:12px'>"
                       "Son seansin dogrulanmis siralamasi henuz yuklenemedi.</div>")

    status_badge_text = (
        "TAKVIM VERISI BEKLENIYOR"
        if calendar_waiting
        else ("CANLI YAYINDA" if is_live_now else "BEKLENIYOR")
    )
    countdown_title = (
        "TAKVIM VE SEANS SAATI DOGRULANIYOR"
        if calendar_waiting
        else f"{location_name.upper()} — {target_s_name.upper()} · SIRADAKI SEANSA KALAN"
    )

    # redesign: sakin F1-TV race center — sirit + geri sayim (canli JS)
    racecenter_html = f"""
    <style>
      *{{box-sizing:border-box}}
      body{{margin:0;background:transparent;font-family:'Saira',system-ui,'Segoe UI',sans-serif;color:#f2f5f8}}
      .rc{{background:linear-gradient(160deg,#161d28,#11161f);border:1px solid #26313f;border-radius:5px;overflow:hidden}}
      .rc-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;
        padding:11px 16px;border-bottom:1px solid #26313f}}
      .rc-head .t{{font:700 13px 'Saira Condensed',sans-serif;letter-spacing:.06em;text-transform:uppercase;color:#9fb0c0}}
      .rc-head .t b{{color:#f2f5f8}}
      .rc-flag{{font:700 10px 'JetBrains Mono',monospace;letter-spacing:.1em;padding:4px 9px;border-radius:3px;
        background:{'#e10600' if is_live_now else '#1e2836'};border:1px solid {'#ff1801' if is_live_now else '#26313f'};color:#fff}}
      .rc-ticker{{overflow:hidden;position:relative;border-bottom:1px solid #26313f;
        -webkit-mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent);
        mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent)}}
      .rc-track{{display:flex;width:max-content;animation-name:rc-scroll;animation-timing-function:linear;animation-iteration-count:infinite}}
      .rc-ticker:hover .rc-track{{animation-play-state:paused}}
      @keyframes rc-scroll{{from{{transform:translateX(0)}}to{{transform:translateX(-50%)}}}}
      @media(prefers-reduced-motion:reduce){{.rc-track{{animation:none}}.rc-ticker{{overflow-x:auto}}}}
      .rc-timer{{padding:16px;text-align:center;border-top:1px solid #26313f;background:#0c1016}}
      .rc-timer .lab{{font:700 10px 'Saira Condensed',sans-serif;letter-spacing:.16em;text-transform:uppercase;color:#63748a;margin-bottom:8px}}
      .rc-timer .val{{font:700 34px 'JetBrains Mono',monospace;color:#f2f5f8;display:flex;align-items:baseline;justify-content:center;gap:6px;flex-wrap:wrap}}
      .rc-timer .val u{{font:700 11px 'Saira',sans-serif;color:#63748a;text-decoration:none;margin-right:10px}}
      .rc-timer .val i{{color:#e10600;font-style:normal}}
      .rc-wait{{color:#9fb0c0;font:600 13px 'Saira',sans-serif}}
    </style>
    <div class="rc">
      <div class="rc-head">
        <span class="t">F1 RACE CENTER <b>· {html_lib.escape(str(last_session_label))}</b></span>
        <span class="rc-flag">{status_badge_text}</span>
      </div>
      <div class="rc-ticker">{ticker_body}</div>
      <div class="rc-timer">
        <div class="lab">{html_lib.escape(str(countdown_title))}</div>
        <div class="val" id="rc-timer">
          <span id="rc-d">00</span><u>GUN</u><i>:</i>
          <span id="rc-h">00</span><u>SAAT</u><i>:</i>
          <span id="rc-m">00</span><u>DK</u><i>:</i>
          <span id="rc-s">00</span><u>SN</u>
        </div>
      </div>
    </div>
    <script>
      var target={target_timestamp_ms}, waiting={str(calendar_waiting).lower()};
      function tick(){{
        var box=document.getElementById('rc-timer');
        if(waiting){{box.innerHTML='<span class="rc-wait">Takvim baglantisi yeniden denenecek. Dogrulanmamis seans icin sahte sayac gosterilmez.</span>';return;}}
        var d=target-Date.now();
        if(d<=0){{box.innerHTML='<span style="color:#4ade80;font-weight:700">SEANS BASLADI · CANLI SINYAL AKTIF</span>';return;}}
        var D=Math.floor(d/864e5),H=Math.floor(d%864e5/36e5),M=Math.floor(d%36e5/6e4),S=Math.floor(d%6e4/1e3);
        var z=n=>n<10?'0'+n:n;
        document.getElementById('rc-d').innerText=z(D);document.getElementById('rc-h').innerText=z(H);
        document.getElementById('rc-m').innerText=z(M);document.getElementById('rc-s').innerText=z(S);
      }}
      setInterval(tick,1000);tick();
    </script>
    """

    fp_ui.render_html_hud(racecenter_html, height=250)

    # redesign: komut seridi -> 4 stat tile (deger tek satir + alt satir)
    _sess_name = last_session['event_name'] if last_session else 'Veri bekleniyor'
    _sess_sub = last_session['display_name'] if last_session else '—'
    _leader = real_drivers[0]['name'] if real_drivers else 'Veri bekleniyor'
    _cmd = st.columns(4)
    with _cmd[0]:
        fp_ui.stat_tile("Son Seans", _sess_name, sub=_sess_sub, accent="cyan", mono=False)
    with _cmd[1]:
        fp_ui.stat_tile("Siradaki", location_name, sub=target_s_name, accent="purple", mono=False)
    with _cmd[2]:
        fp_ui.stat_tile("Durum", "CANLI SEANS" if is_live_now else "BEKLENIYOR",
                        sub="program açıklandı" if is_live_now else "seans saati doğrulanıyor",
                        accent="red" if is_live_now else "amber", mono=False)
    with _cmd[3]:
        fp_ui.stat_tile("Son Lider", _leader, sub="doğrulanmış sonuç", accent="amber", mono=False)


    if session_summary:
        st.write("")
        fp_ui.section_title("Bu Seansta Ne Oldu?")
        _si_tones = ["cyan", "amber", "pink", "purple", "green"]
        _per_row = 3 if len(session_summary) > 2 else len(session_summary)
        for _start in range(0, len(session_summary), _per_row):
            _row = session_summary[_start:_start + _per_row]
            _cols = st.columns(_per_row)
            for _j, _insight in enumerate(_row):
                with _cols[_j]:
                    fp_ui.mini_note(_insight, _si_tones[(_start + _j) % len(_si_tones)])

    if is_live_now:
        if st.button(f"CANLI TAKIP: {target_s_name.upper()} SEANSI BASLADI — TIKLA VE INCELE", use_container_width=True):
            st.session_state['page'] = 'live'

    st.write("")
    fp_ui.section_title("Paddock Live News · Turkce")
    if not st.session_state['news_requested']:
        st.caption("Haber akisi ilk acilista siteyi bekletmez.")
        if st.button("Haber akisini getir", key="load_live_news", use_container_width=True):
            st.session_state['news_requested'] = True
            st.rerun()
        live_news = []
    else:
        # Haber merkeziyle ayni dogrulanmis + gunluk-onbellekli Turkce katalog
        live_news = [localise_news_item_v20(_i) for _i in fetch_f1_news_catalog_v20(8)]
        if st.button('Tum haberleri ve takim filtrelerini ac', key='open_news_centre_v19', use_container_width=True):
            st.session_state['page'] = 'news'
            st.rerun()

    _nn = st.columns(2)
    for _idx, _item in enumerate(live_news):
        with _nn[_idx % 2]:
            fp_ui.news_card(
                _item.get('title', ''),
                source=str(_item.get('source', 'F1 Haber')),
                date=str(_item.get('date', '')),
                excerpt=str(_item.get('desc', '')),
                link=safe_external_url(_item.get('link')) or 'https://www.formula1.com/',
                image=safe_external_url(_item.get('image')),
            )

# SAYFA 2: CANLI SEANS TAKİBİ
elif st.session_state['page'] == 'live':
    curr_event, target_s_name, target_s_time, is_live_now = get_current_or_next_event()
    gp_name = curr_event['EventName'] if 'EventName' in curr_event else "Hungarian Grand Prix"
    
    fp_ui.page_header(T("page.live.title"), f"{gp_name}", eyebrow=T("section.live"))
    fp_ui.data_state(
        "Alpha Odagi",
        "Canli 2D pist, dogrulanmis bir konum saglayicisi hazir olana kadar kapali tutulur; site sahte canli konum uretmez.",
        "warning",
    )

    timing_tab, replay_tab = st.tabs(["Dereceler", "2026 Yaris Tekrari"])
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
                    st.session_state[replay_hud_key] = True
                    st.caption("2D tekrar hızlı OpenF1 tarihî paketinden hazırlanır; yalnızca eksik yarışlarda FastF1 yedeği kullanılır.")

                    if st.session_state.get(replay_hud_key, False):
                        render_data_state(
                            "RACE REPLAY STATUS",
                            "Yarış paketi bir kez doğrulanır; sonraki açılışlar önbellekten gelir.",
                            "info",
                        )
                        with st.spinner("Doğrulanmış yarış haritası hazırlanıyor..."):
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
    fp_ui.page_header(T("page.telemetry.title"), T("page.telemetry.sub"), eyebrow=T("section.data"))

    # --- SEANS SEÇİMİ (artik sayfa govdesinde, sidebar yerine) ---
    fp_ui.section_title("Seans Ayarlari")
    if not st.session_state['telemetry_schedule_requested']:
        fp_ui.data_state("Takvim Istege Bagli", "Sitenin hizli acilmasi icin takvim yalnizca sen istediginde yuklenir.", "info")
        if st.button("Telemetri takvimini yukle", key="load_telemetry_schedule", use_container_width=True):
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
            "Siralama elemesi",
            ["Q3 (Final / Pole)", "Q2", "Q1", "Tum Siralama Seansi"], key="tel_qsub",
        )
        target_q = "Q3" if "Q3" in q_sub_session else "Q2" if "Q2" in q_sub_session else "Q1" if "Q1" in q_sub_session else None

    _MODES = [
        "🗺️ Kuş Bakışı Pist Dominasyonu",
        "🏎️ 2D Tur Düellosu",
        "🛑 Telemetri & Fren Analizi",
        "📊 Top Speed & SÜRÜCÜ Tablosu",
        "🛞 Lastik Stratejisi & Stintler",
    ]
    _MODE_LABELS = ["Pist Dominasyonu", "2D Tur Duellosu", "Fren Analizi", "Top Speed", "Lastik Stratejisi"]
    if hasattr(st, "segmented_control"):
        _picked = st.segmented_control("Gorunum", _MODE_LABELS, default=_MODE_LABELS[0], key="tel_mode")
    else:
        _picked = st.radio("Gorunum", _MODE_LABELS, horizontal=True, key="tel_mode")
    analiz_turu = _MODES[_MODE_LABELS.index(_picked)] if _picked in _MODE_LABELS else _MODES[0]

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

                        max_dist = max(tel1['Distance'].max(), tel2['Distance'].max())
                        distance = np.linspace(0, max_dist, 1000)

                        speed1 = np.interp(distance, tel1['Distance'], tel1['Speed'])
                        speed2 = np.interp(distance, tel2['Distance'], tel2['Speed'])
                        x = np.interp(distance, tel1['Distance'], tel1['X'])
                        y = np.interp(distance, tel1['Distance'], tel1['Y'])

                        delta_speed = speed1 - speed2
                        points = np.array([x, y]).T.reshape(-1, 1, 2)
                        segments = np.concatenate([points[:-1], points[1:]], axis=1)

                        cmap = ListedColormap([fp_plot.A2, fp_plot.A1])
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
                            fig.patch.set_facecolor(fp_plot.BG)
                            ax.set_facecolor(fp_plot.BG)
                            plt.axis('off')

                            st.pyplot(fig)

                        fp_ui.data_state("BOLGE OKUMA", f"Kirmizi bolgeler: {driver_options.get(d1, d1)} daha hizli. Cyan bolgeler: {driver_options.get(d2, d2)} daha hizli.", "info")
                        fp_ui.data_state("ICGORU", get_speed_difference_insight(session, d1, d2, tel1, tel2), "success")

            # --- MOD 2: 2D TUR DÜELLOSU ---
            elif analiz_turu == "🏎️ 2D Tur Düellosu":
                fp_ui.section_title(f"{session.event['EventName']} · 2D Tur Duellosu{header_suffix}")
                st.caption("Mesafe modu ayni virajdaki hiz farkini; gercek zaman modu iki turun fiziksel zaman farkini gosterir.")

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
                            height=620,
                            scrolling=False
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

                        fig, (ax_speed, ax_brake, ax_throttle, ax_gear) = plt.subplots(4, 1, figsize=(10, 8), sharex=True)

                        fp_plot.style(fig, ax_speed, ax_brake, ax_throttle, ax_gear)

                        ax_speed.plot(tel1['Distance'], tel1['Speed'], label=f"{driver_options.get(d1, d1)}", color=fp_plot.A1, linewidth=1.8)
                        ax_speed.plot(tel2['Distance'], tel2['Speed'], label=f"{driver_options.get(d2, d2)}", color=fp_plot.A2, linewidth=1.8)
                        ax_speed.set_ylabel("Hız (km/h)", fontsize=9)
                        ax_speed.legend(loc="upper right", facecolor=fp_plot.PANEL, edgecolor='none', labelcolor=fp_plot.TEXT)

                        ax_brake.plot(tel1['Distance'], tel1['Brake'], color=fp_plot.A1, linewidth=1.5)
                        ax_brake.plot(tel2['Distance'], tel2['Brake'], color=fp_plot.A2, linewidth=1.5)
                        ax_brake.set_ylabel("Fren", fontsize=9)

                        ax_throttle.plot(tel1['Distance'], tel1['Throttle'], color='#E10600', linewidth=1.5)
                        ax_throttle.plot(tel2['Distance'], tel2['Throttle'], color='#38BDF8', linewidth=1.5)
                        ax_throttle.set_ylabel("Gaz %", fontsize=9)

                        ax_gear.plot(tel1['Distance'], tel1['nGear'], color='#E10600', linewidth=1.5)
                        ax_gear.plot(tel2['Distance'], tel2['nGear'], color='#38BDF8', linewidth=1.5)
                        ax_gear.set_ylabel("Vites", fontsize=9)
                        ax_gear.set_xlabel("Pist Mesafesi (Metre)", fontsize=10)

                        st.pyplot(fig)
                        fp_ui.data_state("GEC FRENLEME IPUCU", "Fren grafigindeki dikey sicramalara bak. Dikey cizgi daha sagda olan pilot viraja daha gec fren yapmis demektir.", "info")
                        fp_ui.data_state("ICGORU", get_speed_difference_insight(session, d1, d2, tel1, tel2), "success")

            # --- MOD 3: TOP SPEED & SÜRÜCÜ TABLOSU ---
            elif analiz_turu == "📊 Top Speed & SÜRÜCÜ Tablosu":
                fp_ui.section_title(f"{session.event['EventName']} · Top Speed Tablosu{header_suffix}")
                
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
                    st.pyplot(figure, use_container_width=True)
                    st.dataframe(strategy, use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Veriler çekilirken hata oluştu: {e}")

# SAYFA 4: TAKVİM VE PİSTLER
elif st.session_state['page'] == 'calendar':
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
            axis.set_facecolor('#07090d')
            figure.patch.set_facecolor('#07090d')
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
                if st.button(f"{team_name}\n{team['drivers'][0][1]} • {team['drivers'][1][1]}", key=f"team_{team_name}", use_container_width=True):
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
                f"<div style='font-size:1.22rem;font-weight:950;color:#f2f5f8;margin-top:10px'>{html_lib.escape(name)} <span style='color:{selected_team['color']}'>{html_lib.escape(number)}</span></div>"
                f"<div class='driver-meta'>{html_lib.escape(code)} · {driver_age(code)} yaş · {html_lib.escape(selected_team_name)}</div>"
                f"<div class='history-copy' style='margin-top:9px'>{html_lib.escape(career['bio'])}</div>"
                f"<div style='display:flex;gap:8px;margin-top:10px'><div style='flex:1;background:#11161f;border:1px solid #2d415b;border-radius:8px;padding:8px'><div class='hud-label'>GP GALİBİYETİ</div><div style='font-weight:950;color:{selected_team['color']};font-size:1.15rem;margin-top:3px'>{html_lib.escape(str(career['wins']))}</div></div><div style='flex:1;background:#11161f;border:1px solid #2d415b;border-radius:8px;padding:8px'><div class='hud-label'>PODYUM</div><div style='font-weight:950;color:{selected_team['color']};font-size:1.15rem;margin-top:3px'>{html_lib.escape(str(career['podiums']))}</div></div></div>"
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

    if st.session_state.pop('_scroll_team', False):
        fp_ui.scroll_to("fp-team-detail")

# SAYFA 6: ŞAMPİYONA MERKEZİ
elif st.session_state['page'] == 'standings':
    fp_ui.page_header(T("page.standings.title"), T("page.standings.sub"), eyebrow=T("section.champ"))
    fp_ui.data_state(
        "Sezon Verisi",
        "2026 sonuclari dogrulanmis FastF1 paketinden otomatik hazirlanir. Ilk acilis kisa surebilir; sonrasi yerel onbellekten gelir.",
        "info",
    )
    load_key = 'championship_data_ready_2026'
    if not st.session_state.get(load_key, False):
        st.session_state[load_key] = True
    st.caption("Puan tablosu saatlik önbellekten otomatik güncellenir; elle yenileme gerekmez.")

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
            height=160,
            scrolling=False,
        )
        st.write("")
        driver_tab, team_tab, stat_tab = st.tabs(["Sezon Tablosu", "Takim Puanlari", "Pilot Istatistikleri"])
        with driver_tab:
            if 'championship_matrix_mode' not in st.session_state:
                st.session_state['championship_matrix_mode'] = 'sıralama'
            sort_button, points_button = st.columns(2)
            _mm = st.session_state['championship_matrix_mode']
            with sort_button:
                if st.button(("● " if _mm != 'puan' else "○ ") + "Siralama", key='championship_show_positions',
                             use_container_width=True):
                    st.session_state['championship_matrix_mode'] = 'sıralama'
            with points_button:
                if st.button(("● " if _mm == 'puan' else "○ ") + "Puan", key='championship_show_points',
                             use_container_width=True):
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
        with stat_tab:
            _codes = [str(r.get('Pilot', '')).strip() for _, r in driver_standings.iterrows() if str(r.get('Pilot', '')).strip()]
            _team_of = {str(r.get('Pilot', '')).strip(): str(r.get('Takım', '')).strip() for _, r in driver_standings.iterrows()}
            _sc1, _sc2 = st.columns([2, 1])
            _pick = _sc1.selectbox("Pilot", _codes, format_func=lambda c: f"{directory_driver_by_code(c)['name']} ({c})", key="champ_stat_driver")
            _scope_label = _sc2.radio("Kapsam", ["Bu Sezon", "Kariyer"], horizontal=True, key="champ_stat_scope")
            _scope = "career" if _scope_label == "Kariyer" else "season"
            _info = directory_driver_by_code(_pick)
            _dstats = get_driver_deep_stats_v32(_pick, _scope, "2026")
            _dcol = team_colour(_team_of.get(_pick) or _info.get('team') or '')
            _rows_n = len(_dstats.get('circuit_wins', [])) if _scope == 'career' else 0
            render_html_hud(
                driver_deep_stats_hud_html(_info['name'], _pick, _team_of.get(_pick) or _info.get('team') or '—', _dstats, _scope, _dcol),
                height=(255 if (_dstats.get('verified') and not _dstats.get('empty')) else 90) + _rows_n * 33 + (36 if _rows_n else 0),
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
elif st.session_state['page'] == 'f2f3':
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
        st.link_button("Resmî Formula 2 merkezi ↗", "https://www.fiaformula2.com/", use_container_width=True)
    with series[1]:
        fp_ui.section_title("F3 · 2026 Grid")
        fp_ui.hud_card("Formula 3", "10 takim · 30 pilot", "F3 verileri F1 ve F2 ile karismadan kendi yaris merkezi altinda tutulur.", accent="amber")
        render_junior_team_hud('f3', f3_grid, '#ffbe2e', 'https://www.fiaformula3.com')
        st.link_button("Resmî Formula 3 merkezi ↗", "https://www.fiaformula3.com/", use_container_width=True)

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

elif st.session_state['page'] == 'gridmaster':
    render_gridmaster()

elif st.session_state['page'] == 'team_manager':
    render_team_manager_game()

elif st.session_state['page'] == 'predictor':
    render_paddock_predictor()

elif st.session_state['page'] == 'draft':
    render_paddock_draft_game_v19()

elif st.session_state['page'] == 'paddock_career':
    render_paddock_career_alpha_v01()

# SAYFA 9: F1 SÖZLÜĞÜ
elif st.session_state['page'] == 'glossary':
    fp_ui.page_header(T("page.glossary.title"), T("page.glossary.sub"), eyebrow=T("section.paddock"))
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
