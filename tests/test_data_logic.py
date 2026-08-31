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


def test_prefs_for_url_strips_bulky_keys():
    # Faz 6-B #4 / 7-A — paylasilan linke YALNIZ kimlik girer; ilerleme/sayaclar cikar
    full = {
        "fav_driver": "PIA", "fav_team": "McLaren", "follow": ["NOR", "VER"], "ob": "done",
        "plog": [{"g": "A", "p": 12}] * 12, "lv": 1788000000, "sr": "Dutch GP",
        "slc": "ANT", "slp": 242, "ps": 55, "pn": 4, "gp": {"xp": 40}, "hl": {"s": 3},
        "rw": 5, "cmp": {"VER": 3},
    }
    slim = ui._prefs_for_url(full)
    assert set(slim) == {"fav_driver", "fav_team", "follow", "ob"}
    assert "plog" not in slim and "gp" not in slim and "ps" not in slim
    # localStorage aynasi tam kalir (kirpma yalniz URL icin)
    assert ui._prefs_for_url({}) == {}


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


def test_score_prediction_sprint_v63():
    race = [
        {'code': 'VER', 'position': '1', 'grid': 2},
        {'code': 'NOR', 'position': '2', 'grid': 1},
        {'code': 'LEC', 'position': '3', 'grid': 3},
    ]
    sprint = [{'code': 'PIA', 'position': '1'}, {'code': 'NOR', 'position': '2'}]
    # sprint galibi dogru -> +3
    hit = app._score_prediction_v55({'pl': 'NOR', 'po': ['VER', 'NOR', 'LEC'], 'sw': 'PIA'},
                                    race, sprint)
    assert hit['actual_sprint'] == 'PIA'
    assert ('Sprint galibi doğru', 3) in hit['detail']
    # sprint galibi yanlis -> bonus yok
    miss = app._score_prediction_v55({'pl': 'NOR', 'po': ['VER', 'NOR', 'LEC'], 'sw': 'VER'},
                                     race, sprint)
    assert hit['points'] == miss['points'] + 3
    # sprint_entries verilmezse sw yok sayilir
    none_sp = app._score_prediction_v55({'pl': 'NOR', 'po': ['VER', 'NOR', 'LEC'], 'sw': 'PIA'}, race)
    assert none_sp['actual_sprint'] is None


def test_prediction_badges_v63():
    empty = app._prediction_badges_v63([], 0, 0)
    assert all(b['got'] is False for b in empty)
    log = [
        {'g': 'A', 'p': 18, 'pl': 1, 'ex': 3},   # kusursuz hafta sonu
        {'g': 'B', 'p': 6, 'pl': 1, 'ex': 0},
        {'g': 'C', 'p': 9, 'pl': 1, 'ex': 1},
    ]
    got = {b['name'] for b in app._prediction_badges_v63(log, 55, 3) if b['got']}
    assert 'İlk isabet' in got
    assert 'Pole avcısı' in got                  # 3 pole
    assert 'Keskin nişancı' in got               # ex >= 1
    assert 'Kusursuz hafta sonu' in got
    assert 'Seri x3' in got                      # 3 ard arda puanli
    assert 'Yarım yüz' in got                    # 55 >= 50


def test_pred_streak_v63():
    assert app._pred_streak_v63([]) == 0
    assert app._pred_streak_v63([{'p': 0}, {'p': 5}, {'p': 3}, {'p': 0}, {'p': 1}]) == 2
    assert app._pred_streak_v63([{'p': 2}, {'p': 2}, {'p': 2}]) == 3


def test_weekend_ics_v63():
    import pandas as _pd
    start = _pd.Timestamp('2026-09-06 13:00', tz='UTC')
    sessions = [{
        'title': 'Yarış', 'code': 'R', 'time': start,
        'estimated_end': start + _pd.Timedelta(hours=2),
    }]
    text, slug = app._weekend_ics_v63('Italian Grand Prix', 'Monza', sessions)
    assert slug == 'italian-grand-prix'
    assert text.startswith('BEGIN:VCALENDAR')
    assert 'BEGIN:VEVENT' in text and text.rstrip().endswith('END:VCALENDAR')
    assert 'DTSTART:20260906T130000Z' in text
    assert 'SUMMARY:Italian Grand Prix - Yarış' in text
    assert app._ics_escape_v63('a, b; c\\d') == r'a\, b\; c\\d'


def test_rss_root_lenient_v64():
    # temiz XML — olduğu gibi ayrışır
    ok = b"<rss><channel><item><title>Norris kazandi</title></item></channel></rss>"
    assert app._rss_root_lenient_v64(ok).findtext('.//title') == "Norris kazandi"
    # kaçırılmamış '&' ve kontrol karakteri — temizlenip ayrışır
    broken = b"<rss><channel><item><title>Red Bull \x07& Ferrari</title></item></channel></rss>"
    root = app._rss_root_lenient_v64(broken)
    assert root.findtext('.//title') == "Red Bull & Ferrari"


def test_free_translate_strict_v64_empty():
    # ağ yok: boş girdi anında '' döner
    assert app._free_translate_strict_v64("") == ""
    assert app._free_translate_strict_v64("   ") == ""
    assert app._mymemory_translate_v64("") == ""


def test_hl_bucket_v66():
    assert app._hl_bucket_v66(0.05) == "< 0.1 sn"
    assert app._hl_bucket_v66(0.1) == "0.1 – 0.3 sn"
    assert app._hl_bucket_v66(0.319) == "0.3 – 0.6 sn"
    assert app._hl_bucket_v66(0.9) == "0.6 – 1.0 sn"
    assert app._hl_bucket_v66(2.4) == "1.0 sn+"


def test_top_trumps_deck_and_resolve_v66():
    deck = app._tt_deck_v66()
    assert len(deck) >= 6
    for c in deck:
        assert {"code", "wins", "podiums", "poles", "titles", "starts", "ppr"} <= set(c)
        assert c["starts"] >= 5
    # yüksek stat kazanır; kartlar 'Devam'a kadar taşınmaz
    g = {"p": [0, 1], "c": [2, 3], "pot": [], "turn": "p", "phase": "pick",
         "round": 1, "reveal": None}
    hi = max(range(len(deck)), key=lambda i: deck[i]["starts"])
    lo = min(range(len(deck)), key=lambda i: deck[i]["starts"])
    g["p"], g["c"] = [hi], [lo]
    app._tt_compare_v66(g, deck, "starts")
    assert g["phase"] == "reveal" and g["reveal"]["win"] == "p"
    assert g["p"] == [hi] and g["c"] == [lo]      # henüz taşınmadı
    app._tt_advance_v66(g)
    assert not g["c"] and sorted(g["p"]) == sorted([hi, lo])   # kazanan hepsini aldı


def test_podium_score_v67():
    pod = ["VER", "HAM", "LEC"]
    assert app._podium_score_v67(pod, pod)[0] == 5 * 3 + 3 + 5          # kusursuz
    assert app._podium_score_v67(["HAM", "VER", "LEC"], pod)[0] == 5 + 2 + 2 + 3  # 1 tam + 2 podyumda + hepsi
    assert app._podium_score_v67(["NOR", "RUS", "SAI"], pod)[0] == 0    # hiçbiri
    assert app._podium_score_v67(["VER", "NOR", "SAI"], pod)[0] == 5    # 1 tam


def _fake_strat_model_v67(sc=(20, 23)):
    b, laps = 90.0, 40
    return {
        "year": 2020, "gp": "Test GP", "total_laps": laps, "driver": "XXX",
        "driver_name": "Test", "grid": 5, "field": 20,
        "base_lap_s": b, "fuel_effect": -0.03, "pit_loss_s": 22.0,
        "compounds": {
            "SOFT": {"off": -0.5, "deg": 0.10, "cliff": 12},
            "MEDIUM": {"off": 0.0, "deg": 0.05, "cliff": 24},
            "HARD": {"off": 0.4, "deg": 0.03, "cliff": 36},
        },
        "sc_windows": [sc] if sc else [], "vsc_windows": [],
        "rivals": [
            {"code": "AAA", "cum_s": [b * i for i in range(1, laps + 1)],
             "stops": [{"lap": 15, "compound": "HARD"}], "start": "MEDIUM", "finish": 3},
            {"code": "BBB", "cum_s": [(b + 0.6) * i for i in range(1, laps + 1)],
             "stops": [{"lap": 18, "compound": "HARD"}], "start": "MEDIUM", "finish": 6},
        ],
        "actual": {"stops": [{"lap": 16, "compound": "HARD"}],
                   "start_compound": "MEDIUM", "finish_pos": 4},
    }


def test_strat_simulate_sc_and_cliff_v67():
    m = _fake_strat_model_v67(sc=(20, 23))
    # lap-21 pit is inside the SC window → bedava stop
    sim = app._strat_simulate_v67(m, {"start_compound": "MEDIUM",
                                      "stops": [{"lap": 21, "compound": "HARD"}]})
    pit_evs = [e for f in sim["frames"] for e in f["ev"] if e["t"] == "PIT"]
    assert pit_evs and pit_evs[0]["sc"] is True
    assert any(t == "Safety Car altında pit — bedava stop" for t, _ in sim["result"]["breakdown"])
    assert len(sim["frames"]) == m["total_laps"]

    # 1-stop MEDIUM taken far past its cliff (24) → TYRE_CLIFF + ceza
    m2 = _fake_strat_model_v67(sc=None)
    sim2 = app._strat_simulate_v67(m2, {"start_compound": "MEDIUM",
                                        "stops": [{"lap": 38, "compound": "SOFT"}]})
    assert any(e["t"] == "TYRE_CLIFF" for f in sim2["frames"] for e in f["ev"])
    assert any(p < 0 for _, p in sim2["result"]["breakdown"])


def test_strat_lin_slope_v67():
    import pandas as _pd
    x = _pd.Series([1, 2, 3, 4, 5])
    y = _pd.Series([10.0, 10.2, 10.4, 10.6, 10.8])       # eğim 0.2
    assert abs(app._strat_lin_slope_v67(x, y) - 0.2) < 1e-6
    assert app._strat_lin_slope_v67(_pd.Series([1]), _pd.Series([1])) == 0.0


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
