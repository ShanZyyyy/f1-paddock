# -*- coding: utf-8 -*-
"""Kenar menu yapisi — tek veri kaynagi.

Router (app tarafi) bu listeyi gezerek slim-rail menuyu cizer.
Sayfa anahtarlari mevcut ``st.session_state['page']`` degerleriyle BIREBIR ayni;
degistirme — yoksa yonlendirme kirilir.

icon = Google Material Symbols adi -> Streamlit'te ":material/<icon>:"
"""

# (bolum_basligi, [(etiket, icon, page_key), ...])
SECTIONS = [
    ("Genel", [
        ("Ana Sayfa & Haberler", "home", "home"),
        ("Haber Merkezi", "newspaper", "news"),
    ]),
    ("Veri & Analiz", [
        ("Telemetri Merkezi", "monitoring", "telemetry"),
        ("Sampiyona Merkezi", "emoji_events", "standings"),
    ]),
    ("Canli & Yaris", [
        ("Seans Takibi", "sensors", "live"),
        ("Takvim & Pistler", "calendar_month", "calendar"),
        ("Hafta Sonu Merkezi", "flag", "weekend"),
        ("Yaris Hikayesi", "menu_book", "story"),
        ("Pilot Karsilastirma", "compare_arrows", "compare"),
        ("Pilotlar", "badge", "drivers"),
    ]),
    ("Sampiyonalar", [
        ("2026 Takimlar & Pilotlar", "groups", "teams"),
        ("F2 & F3 Takip", "stacked_line_chart", "f2f3"),
    ]),
    ("Paddock", [
        ("Paddock Asistani", "smart_toy", "assistant"),
        ("F1 Baslangic Garaji", "school", "learn"),
        ("Favori Paddock", "star", "favourites"),
        ("F1 Sozlugu", "quiz", "glossary"),
    ]),
    ("Oyunlar", [
        ("Oyun Merkezi", "sports_esports", "games"),
    ]),
]

# Menude butonu olmayan ama router'da gecerli olan alt sayfalar
# (oyun ic sayfalari, telemetri sonuc ekrani vb.)
CHILD_PAGES = {
    "stewarlde", "predict", "cards", "hotlap", "podium", "stratwall",
}

ALL_PAGES = {key for _, items in SECTIONS for _, _, key in items} | CHILD_PAGES
