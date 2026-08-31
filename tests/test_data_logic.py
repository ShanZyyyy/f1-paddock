# -*- coding: utf-8 -*-
"""Altin veri testleri — ag YOK.

Sabit fixture ve saf fonksiyonlar uzerinden hesap mantigini dogrular.
smoke_test.py "sayfa patliyor mu" sorusuna bakar; bu dosya "sayilar dogru mu".

    .venv/Scripts/python -m pytest tests/test_data_logic.py -q
"""

import json
import os

import pytest

import streamlit_app as app
from core import i18n, theme, ui

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


# --------------------------------------------------------------------------
# is_dnf_status — Alonso DNF=104 hatasinin kok nedeni buydu
# --------------------------------------------------------------------------
@pytest.mark.parametrize("status, expected", [
    ("Finished", False),
    ("finished", False),
    ("", False),
    (None, False),
    ("+1 Lap", False),
    ("+2 Laps", False),
    ("Lapped", False),
    ("Accident", True),
    ("Collision", True),
    ("Engine", True),
    ("Retired", True),
    ("Disqualified", True),
    ("Withdrew", True),
    ("Power Unit", True),
])
def test_is_dnf_status(status, expected):
    assert app.is_dnf_status(status) is expected


# --------------------------------------------------------------------------
# aggregate_driver_career_v33 — sabit 5 yarislik fixture
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def career():
    with open(os.path.join(FIXTURES, "career_sample.json"), encoding="utf-8") as handle:
        rows = [(race, result) for race, result in json.load(handle)]
    return app.aggregate_driver_career_v33(rows)


def test_career_totals(career):
    assert career["ok"] is True
    assert career["starts"] == 5
    assert career["points"] == 68.0          # 25 + 18 + 0 + 25 + 0
    assert career["wins"] == 2
    assert career["podiums"] == 3            # P1, P2, P1
    assert career["poles"] == 2              # grid == 1 iki kez
    assert career["fastest_laps"] == 1
    assert career["dnf"] == 1                # yalniz "Accident"
    assert career["best"] == 1
    assert career["worst"] == 11             # "+1 Lap" bitisi DNF degil, sayilir
    assert career["avg_grid"] == 2.4         # (1+3+2+1+5)/5


def test_career_circuit_wins(career):
    circuit_wins = dict(career["circuit_wins"])
    assert circuit_wins["Bahrain International Circuit"] == 2


def test_career_seasons_and_teams(career):
    assert career["first_season"] == "2023"
    assert career["last_season"] == "2024"
    teams = {name: (lo, hi) for name, lo, hi in career["teams"]}
    assert teams["Alpha F1"] == (2023, 2023)
    assert teams["Beta F1"] == (2024, 2024)
    by_year = {season["year"]: season for season in career["seasons"]}
    assert by_year["2023"]["races"] == 3
    assert by_year["2023"]["wins"] == 1
    assert by_year["2024"]["points"] == 25.0
    # yarislar yeni -> eski
    assert career["races"][0]["year"] == "2024"


# --------------------------------------------------------------------------
# _pos_chip_v33 — Pilotlar timing HUD renk mantigi
# --------------------------------------------------------------------------
def test_pos_chip_podium_points_dnf():
    assert app._pos_chip_v33("1", False)[2] == "P1"
    assert app._pos_chip_v33("3", False)[2] == "P3"
    assert app._pos_chip_v33("7", False)[2] == "P7"
    assert app._pos_chip_v33("18", False)[2] == "P18"
    assert app._pos_chip_v33("5", True)[2] == "DNF"
    assert app._pos_chip_v33("", False)[2] == "—"
    # podium ve puan disi farkli renk
    assert app._pos_chip_v33("2", False)[1] != app._pos_chip_v33("15", False)[1]


def test_num_v33():
    assert app._num_v33(25) == "25"
    assert app._num_v33(18.0) == "18"
    assert app._num_v33(7.5) == "7.5"
    assert app._num_v33(None) == "0"
    assert app._num_v33("x") == "0"


# --------------------------------------------------------------------------
# points_value / format_time
# --------------------------------------------------------------------------
def test_points_value():
    assert app.points_value(25) == 25.0
    assert app.points_value(None) == 0.0
    assert app.points_value("nan") == 0.0
    assert app.points_value("12") == 12.0


# --------------------------------------------------------------------------
# safe_external_url — XSS / acik yonlendirme kapisi
# --------------------------------------------------------------------------
def test_safe_external_url():
    assert app.safe_external_url("https://www.autosport.com/f1/news/x").startswith("https://")
    assert app.safe_external_url("javascript:alert(1)") == ""
    assert app.safe_external_url("") == ""
    assert app.safe_external_url("ftp://example.com/x") == ""
    assert app.safe_external_url("https://evil.com", allowed_hosts=["autosport.com"]) == ""
    assert app.safe_external_url("https://autosport.com/x", allowed_hosts=["autosport.com"]) != ""


# --------------------------------------------------------------------------
# translate_race_control_message — anlam kaybetmeden TR
# --------------------------------------------------------------------------
def test_translate_race_control_message():
    assert "Güvenlik aracı" in app.translate_race_control_message("SAFETY CAR DEPLOYED")
    assert "Kırmızı bayrak" in app.translate_race_control_message("RED FLAG")
    assert app.translate_race_control_message("") == ""


# --------------------------------------------------------------------------
# DeepL çeviri: anahtar yoksa devreye girmez, ham çağrı hata verir
# --------------------------------------------------------------------------
def test_deepl_translate_without_key(monkeypatch):
    monkeypatch.setattr(app, "_secret_or_environment", lambda name: "")
    assert app.deepl_configured() is False
    with pytest.raises(Exception):
        app._deepl_translate_raw("Hello world")   # anahtar yok -> hemen hata, ağ yok


# --------------------------------------------------------------------------
# i18n
# --------------------------------------------------------------------------
def test_i18n_switch():
    i18n.set_lang("tr")
    assert i18n.t("nav.drivers") == "Pilotlar"
    i18n.set_lang("en")
    assert i18n.t("nav.drivers") == "Drivers"
    assert i18n.t("does.not.exist") == "does.not.exist"
    i18n.set_lang("tr")


# --------------------------------------------------------------------------
# safe_html — XSS gomme kapisi
# --------------------------------------------------------------------------
def test_safe_html():
    assert ui.safe_html("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert ui.safe_html(None) == ""
    assert '"' not in ui.safe_html('a"b', quote=True)


# --------------------------------------------------------------------------
# kalici tercih kodlayici — favori/takip/tahmin round-trip (Faz 4 #1)
# --------------------------------------------------------------------------
def test_prefs_encode_roundtrip():
    prefs = {
        "fav_drv": "VER", "fav_team": "Red Bull Racing",
        "follow": ["HAM", "LEC", "NOR"], "seen_ts": 1735689600,
        "unicode": "Kimi Räikkönen",
    }
    blob = ui._prefs_encode(prefs)
    assert "=" not in blob                       # url-safe, padding kirpik
    assert ui._prefs_decode(blob) == prefs
    # sira bagimsiz kararli blob (sort_keys)
    assert ui._prefs_encode(dict(reversed(list(prefs.items())))) == blob


def test_cell_pts_v57():
    import pandas as _pd
    assert app._cell_pts_v57(None, 'r1') == 0.0
    row = _pd.Series({'r1': 25, 'r2': '18 / 8', 'r3': '—', 'r4': '', 'r5': 'DNF'})
    assert app._cell_pts_v57(row, 'r1') == 25.0
    assert app._cell_pts_v57(row, 'r2') == 26.0     # yaris + sprint
    assert app._cell_pts_v57(row, 'r3') == 0.0
    assert app._cell_pts_v57(row, 'r4') == 0.0
    assert app._cell_pts_v57(row, 'r5') == 0.0
    assert app._cell_pts_v57(row, 'missing') == 0.0


def test_score_prediction_v55():
    race = [
        {'code': 'VER', 'position': '1', 'grid': 1},
        {'code': 'NOR', 'position': '2', 'grid': 3},
        {'code': 'LEC', 'position': '3', 'grid': 2},
        {'code': 'HAM', 'position': '4', 'grid': 4},
    ]
    # tam isabet: pole + P1 tam + P2 (NOR) tam + P3 (LEC) tam
    perfect = app._score_prediction_v55({'pl': 'VER', 'po': ['VER', 'NOR', 'LEC']}, race)
    assert perfect['points'] == 5 + 5 + 5 + 5           # pole 5, 3× P-tam 5
    # podyumda ama yanlış yer: HAM tahmin, aslında P4 -> 0; LEC P2 tahmin ama P3 -> 3
    partial = app._score_prediction_v55({'pl': 'NOR', 'po': ['HAM', 'LEC', 'VER']}, race)
    assert partial['points'] == 0 + 3 + 3               # pole yanlış, LEC & VER podyumda yanlış yerde
    assert app._score_prediction_v55(None, race) is None
    assert app._score_prediction_v55({'pl': 'VER'}, []) is None


def test_prefs_decode_bad_input():
    assert ui._prefs_decode("") == {}
    assert ui._prefs_decode(None) == {}
    assert ui._prefs_decode("!!! not base64 !!!") == {}
    import base64 as _b64
    not_a_dict = _b64.urlsafe_b64encode(b"[1,2,3]").decode().rstrip("=")
    assert ui._prefs_decode(not_a_dict) == {}


# --------------------------------------------------------------------------
# tema CSS ureticileri lru_cache'li ve kararli (her rerun'da yeniden uretilmiyor)
# --------------------------------------------------------------------------
def test_theme_css_cached_and_stable():
    a = theme.shell_style()
    b = theme.shell_style()
    assert a is b                        # ayni nesne -> lru_cache calisiyor
    assert "data:image/svg" in a         # pist silueti gomulu
    assert theme.page_style() is theme.page_style()
