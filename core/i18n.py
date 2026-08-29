# -*- coding: utf-8 -*-
"""Formula Paddock — arayuz metinleri (TR / EN).

Kapsam: menu, bolum basliklari, sayfa basliklari, kontrol panosu ve sik
kullanilan etiketler. Yaris verisi (pilot/takim adi) zaten evrenseldir;
sozluk / oyun ici uzun metinler simdilik yalniz TR (kademeli eklenebilir).
"""

import streamlit as st

_LANG_KEY = "fp_lang"
DEFAULT = "tr"
LANGS = ("tr", "en")

STRINGS = {
    # ---- kenar menu bolumleri ----
    "section.general": {"tr": "Genel", "en": "General"},
    "section.data": {"tr": "Veri & Analiz", "en": "Data & Analysis"},
    "section.live": {"tr": "Canlı & Yarış", "en": "Live & Race"},
    "section.paddock": {"tr": "Paddock", "en": "Paddock"},
    "section.champ": {"tr": "Şampiyonalar", "en": "Championships"},
    "section.games": {"tr": "Oyunlar", "en": "Games"},

    # ---- kenar menu satirlari (anahtar = page_key) ----
    "nav.home": {"tr": "Ana Sayfa & Haberler", "en": "Home & News"},
    "nav.news": {"tr": "Haber Merkezi", "en": "News Centre"},
    "nav.telemetry": {"tr": "Telemetri Merkezi", "en": "Telemetry Centre"},
    "nav.live": {"tr": "Seans Takibi", "en": "Session Tracker"},
    "nav.calendar": {"tr": "Takvim & Pistler", "en": "Calendar & Circuits"},
    "nav.weekend": {"tr": "Hafta Sonu Merkezi", "en": "Weekend Centre"},
    "nav.story": {"tr": "Yarış Hikayesi", "en": "Race Story"},
    "nav.compare": {"tr": "Pilot Karşılaştırma", "en": "Driver Comparison"},
    "nav.drivers": {"tr": "Pilotlar", "en": "Drivers"},
    "nav.learn": {"tr": "F1 Başlangıç Garajı", "en": "F1 Starter Garage"},
    "nav.favourites": {"tr": "Favori Paddock", "en": "Favourites"},
    "nav.teams": {"tr": "2026 Takımlar & Pilotlar", "en": "2026 Teams & Drivers"},
    "nav.standings": {"tr": "Şampiyona Merkezi", "en": "Championship Centre"},
    "nav.f2f3": {"tr": "F2 & F3 Takip", "en": "F2 & F3 Tracker"},
    "nav.glossary": {"tr": "F1 Sözlüğü", "en": "F1 Glossary"},
    "nav.assistant": {"tr": "Paddock Asistanı", "en": "Paddock Assistant"},
    "nav.games": {"tr": "Oyun Merkezi", "en": "Games Hub"},

    # ---- sayfa basliklari: eyebrow / title / sub ----
    "page.home.title": {"tr": "Race Intelligence", "en": "Race Intelligence"},
    "page.home.sub": {"tr": "F1 yarışları, şampiyona verileri, pilot analizleri ve tarihî oyunlar.",
                      "en": "F1 races, championship data, driver analysis and history games."},
    "page.telemetry.title": {"tr": "Telemetri Merkezi", "en": "Telemetry Centre"},
    "page.telemetry.sub": {"tr": "Tamamlanmış bir seans seç: tur düellosu, fren analizi, lastik stratejisi.",
                           "en": "Pick a completed session: lap duel, braking analysis, tyre strategy."},
    "page.standings.title": {"tr": "Şampiyona Merkezi", "en": "Championship Centre"},
    "page.standings.sub": {"tr": "Puanlar tamamlanmış yarış ve sprint sonuçlarından hazırlanır.",
                           "en": "Points are built from completed race and sprint results."},
    "page.teams.title": {"tr": "2026 Takımlar & Pilotlar", "en": "2026 Teams & Drivers"},
    "page.teams.sub": {"tr": "2026 grid: 11 takım, 22 pilot. Güncel takım renkleriyle.",
                       "en": "2026 grid: 11 teams, 22 drivers, in current team colours."},
    "page.calendar.title": {"tr": "Takvim & Pistler", "en": "Calendar & Circuits"},
    "page.calendar.sub": {"tr": "Bir yarış seç: pist görünümü, hafta sonu programı ve tamamlanan seans sonuçları.",
                          "en": "Pick a race: circuit map, weekend schedule and completed session results."},
    "page.glossary.title": {"tr": "F1 Sözlüğü", "en": "F1 Glossary"},
    "page.glossary.sub": {"tr": "2026 kuralları ve telemetri ekranları için 60 temel terim.",
                          "en": "60 core terms for the 2026 rules and our telemetry screens."},
    "page.live.title": {"tr": "Seans Takibi", "en": "Session Tracker"},
    "page.f2f3.title": {"tr": "Formula 2 & Formula 3", "en": "Formula 2 & Formula 3"},
    "page.f2f3.sub": {"tr": "2026 resmî kadroları. Ayrı veri kaynağı ve ayrı puan merkezleri.",
                      "en": "2026 official line-ups. Separate data source and points centres."},
    "page.games.title": {"tr": "Oyun Merkezi", "en": "Games Hub"},
    "page.games.sub": {"tr": "Stewardle doğrulanmış tarihsel motorunda; diğerleri Paddock Oyun Motoru 3.0.",
                       "en": "Stewardle runs on the verified history engine; the rest on Paddock Game Engine 3.0."},
    "page.weekend.title": {"tr": "Hafta Sonu Merkezi", "en": "Weekend Centre"},
    "page.weekend.sub": {"tr": "Bir Grand Prix seç; program, tamamlanan seanslar ve sonuç ekranları tek yerde.",
                         "en": "Pick a Grand Prix; schedule, completed sessions and result screens in one place."},
    "page.story.title": {"tr": "Yarış Hikayesi", "en": "Race Story"},
    "page.story.sub": {"tr": "Sonuçları pole, kazanan, en çok yükselen ve önemli notlara dönüştürür.",
                       "en": "Turns results into pole, winner, biggest mover and key notes."},
    "page.compare.title": {"tr": "Pilot Karşılaştırma", "en": "Driver Comparison"},
    "page.compare.sub": {"tr": "İki pilotu aynı tamamlanmış seanstaki gerçek sonuç ve tur verisiyle karşılaştır.",
                         "en": "Compare two drivers with real result and lap data from the same completed session."},
    "page.drivers.title": {"tr": "Pilotlar", "en": "Drivers"},
    "page.drivers.sub": {"tr": "Bir pilot seç: kariyer, sezon dökümü ve yarış-yarış sonuçlar — doğrulanmış kayıttan.",
                         "en": "Pick a driver: career, season breakdown and race-by-race results — from verified records."},
    "page.learn.title": {"tr": "F1 Başlangıç Garajı", "en": "F1 Starter Garage"},
    "page.learn.sub": {"tr": "Hafta sonu, lastik, pit stop ve puan sistemini beş dakikada anlatan mini rehber.",
                       "en": "A five-minute guide to the weekend, tyres, pit stops and the points system."},
    "page.favourites.title": {"tr": "Favori Paddock", "en": "Favourites"},
    "page.favourites.sub": {"tr": "Sevdiğin takım ve pilot için hızlı başlangıç alanı.",
                            "en": "A quick-start area for your favourite team and driver."},
    "page.news.title": {"tr": "Haber Merkezi", "en": "News Centre"},
    "page.news.sub": {"tr": "Türkçe Formula 1 haberleri, kapak seçkisi ve takımına göre filtrelenmiş akış.",
                      "en": "Formula 1 news, a cover selection and a feed filtered by your team."},
    "page.assistant.title": {"tr": "Paddock Asistanı", "en": "Paddock Assistant"},
    "page.assistant.sub": {"tr": "Sonuç, pole, lastik ve tarihî sorular doğrulanmış F1 verisinden yanıtlanır.",
                           "en": "Result, pole, tyre and history questions answered from verified F1 data."},

    # ---- kontrol panosu ----
    "dock.view": {"tr": "Görünüm", "en": "Theme"},
    "dock.dark": {"tr": "Koyu", "en": "Dark"},
    "dock.light": {"tr": "Açık", "en": "Light"},
    "dock.music": {"tr": "Ortam müziği", "en": "Ambient music"},

    # ---- sik etiketler ----
    "eyebrow.paddock": {"tr": "Paddock", "en": "Paddock"},
    "common.source": {"tr": "Kaynak", "en": "Source"},
    "common.loading": {"tr": "Yükleniyor…", "en": "Loading…"},
}


def get_lang():
    lang = st.session_state.get(_LANG_KEY, DEFAULT)
    return lang if lang in LANGS else DEFAULT


def set_lang(lang):
    if lang in LANGS:
        st.session_state[_LANG_KEY] = lang


def t(key, **fmt):
    entry = STRINGS.get(key)
    if not entry:
        return key
    value = entry.get(get_lang()) or entry.get(DEFAULT) or key
    return value.format(**fmt) if fmt else value


def lang_toggle():
    """Kenar menu ustunde kucuk TR | EN secici."""
    current = get_lang()
    cols = st.sidebar.columns(2)
    if cols[0].button("TR", key="fp_lang_tr", use_container_width=True,
                      type="primary" if current == "tr" else "secondary"):
        set_lang("tr")
        st.rerun()
    if cols[1].button("EN", key="fp_lang_en", use_container_width=True,
                      type="primary" if current == "en" else "secondary"):
        set_lang("en")
        st.rerun()
