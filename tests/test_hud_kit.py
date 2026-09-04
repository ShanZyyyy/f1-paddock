# -*- coding: utf-8 -*-
"""Yayın HUD kiti + yeniden tasarlanan HUD'lar — saf string testleri, ağ YOK.

    .venv/Scripts/python -m pytest tests/test_hud_kit.py -q
"""

import pytest

import streamlit_app as app
from core import hud_kit


# --------------------------------------------------------------------------
# hud_kit — jeton tabanı
# --------------------------------------------------------------------------
def test_kit_css_has_core_tokens():
    css = hud_kit.kit_css()
    for token in ("--k-night", "--k-panel", "--k-line", "--k-cyan", "--k-f-data", "--k-r-l"):
        assert token in css
    assert "color-scheme:dark" in css
    assert css.count("{") == css.count("}")


def test_kit_css_has_responsive_and_tooltip_primitives():
    css = hud_kit.kit_css()
    # Faz 1 — mobil responsive yardımcıları + jargon baloncuğu
    assert "@media (max-width:768px)" in css
    assert ".k-resp" in css and ".k-info" in css
    assert "data-tip" in css                       # :hover ile açılan saf-CSS tooltip
    assert css.count("{") == css.count("}")


def test_info_helper_escapes_and_shapes():
    out = hud_kit.info('Delta <b>fark</b> ("Δ")')
    assert out.startswith('<span class="k-info"') and 'data-tip="' in out
    assert "&quot;" in out and "&lt;b&gt;" in out and "<b>" not in out
    assert 'class="k-info up"' in hud_kit.info("x", up=True)


@pytest.mark.parametrize("name, expected", [
    ("SOFT", "#ff5b5b"), ("soft", "#ff5b5b"), ("Medium", "#ffd23f"),
    ("HARD", "#eef2f7"), ("INTERMEDIATE", "#3ecf8e"), ("", "#8fa0b4"), (None, "#8fa0b4"),
])
def test_compound_hex(name, expected):
    assert hud_kit.compound_hex(name) == expected


def test_google_fonts_link_is_googleapis_only():
    link = hud_kit.google_fonts_link()
    assert "fonts.googleapis.com" in link and "fonts.gstatic.com" in link
    assert "JetBrains+Mono" in link and "Inter" in link


# --------------------------------------------------------------------------
# Strateji Duvarı — Gantt
# --------------------------------------------------------------------------
@pytest.fixture
def replay_payload():
    return {
        "total_laps": 20,
        "fastest_lap": {"code": "VER", "lap": 14, "seconds": 78.211},
        "events": [
            {"kind": "undercut", "lap": 8, "code": "LEC",
             "text": "VER pit — HAM üzerine undercut"},
        ],
        "cars": [
            {
                "code": "VER", "team": "Red Bull Racing", "colour": "#3671c6",
                "grid": 2, "final_position": 1,
                "fastest": {"lap": 14, "seconds": 78.211},
                "pit_events": [{"lap": 10, "start": 100.0, "end": 122.4}],
                "laps": [
                    {"lap": i, "position": 1, "stint": 0 if i <= 10 else 1,
                     "compound": "SOFT" if i <= 10 else "HARD",
                     "start": 0.0, "end": 80.0} for i in range(1, 21)
                ],
            },
            {
                "code": "LEC", "team": "Ferrari", "colour": "#e8002d",
                "grid": 1, "final_position": 2,
                "fastest": {"lap": 15, "seconds": 78.6},
                "pit_events": [{"lap": 8, "start": 90.0, "end": 113.1}],
                "laps": [
                    {"lap": i, "position": 2, "stint": 0 if i <= 8 else 1,
                     "compound": "SOFT" if i <= 8 else "MEDIUM",
                     "start": 0.0, "end": 80.5} for i in range(1, 21)
                ],
            },
            {
                "code": "DNF", "team": "Alpine", "colour": "#0093cc",
                "grid": 10, "final_position": None, "retired": True,
                "pit_events": [], "fastest": None,
                "laps": [
                    {"lap": i, "position": 12, "stint": 0, "compound": "MEDIUM",
                     "start": 0.0, "end": 82.0} for i in range(1, 6)
                ],
            },
        ],
    }


def test_strategy_wall_html_structure(replay_payload):
    html = app.strategy_wall_html(replay_payload)
    assert isinstance(html, str) and len(html) > 500
    assert "__PAYLOAD__" not in html
    assert html.count("<style>") == 1 and html.count("</style>") == 1
    # her pilot bir lane
    for code in ("VER", "LEC", "DNF"):
        assert f">{code}</span>" in html
    # stint blokları yüzde konumlu
    assert "class='st'" in html and "left:" in html and "width:" in html
    # pit-lane süresi işarette
    assert "data-t='22.4s'" in html
    # en hızlı tur mor rozet (seansın en hızlısı VER)
    assert "class='fl ov'" in html
    # tespit edilen hamle etiketi
    assert "class='uc'" in html
    # tur ekseni
    assert "class='tk'" in html
    assert "toplam 20 tur" in html
    # Faz 1 — jargon tooltip'leri (stint + undercut/overcut) + 768px kırılım
    assert 'class="k-info"' in html and "data-tip=" in html
    assert "@media(max-width:768px)" in html


def test_strategy_wall_html_handles_empty():
    html = app.strategy_wall_html({"total_laps": 0, "cars": []})
    assert isinstance(html, str)
    assert "Lastik Strateji Duvarı" in html
    assert html.count("{") >= 0  # patlamadan döner


def test_strategy_wall_height_monotonic():
    small = app.strategy_wall_component_height({"cars": [{}] * 4})
    big = app.strategy_wall_component_height({"cars": [{}] * 20})
    assert 320 <= small < big <= 2800


# --------------------------------------------------------------------------
# Telemetri — enstrüman rayı
# --------------------------------------------------------------------------
@pytest.fixture
def trace_payload():
    n = 40
    grid = [round(i * 50.0, 0) for i in range(n)]
    def drv(code, colour, base):
        return {
            "code": code, "colour": colour, "lap": "1:18.211",
            "speed": [base + (i % 7) * 10 for i in range(n)],
            "throttle": [100 if i % 3 else 0 for i in range(n)],
            "brake": [0 if i % 3 else 80 for i in range(n)],
            "gear": [min(8, 2 + i % 6) for i in range(n)],
            "x": [float(i) for i in range(n)],
            "y": [float((i * i) % 50) for i in range(n)],
        }
    return {
        "ok": True,
        "drivers": [drv("VER", "#3671c6", 250), drv("NOR", "#ff8000", 240)],
        "distance": grid,
        "track": [[float(i), float((i * i) % 50)] for i in range(n)],
        "sectors": [],
    }


def test_telemetry_trace_html_structure(trace_payload):
    html = app.telemetry_trace_html(trace_payload)
    assert isinstance(html, str) and len(html) > 800
    assert "__PAYLOAD__" not in html
    assert html.count("<style>") == 1 and html.count("</style>") == 1
    assert html.count("<script>") == 1 and html.count("</script>") == 1
    # enstrüman kümesi + çip şeridi
    assert 'id="cluster"' in html and "drawCluster" in html
    assert "k-chip" in html
    # dört iz kanvası korunur
    for key in ("speed", "throttle", "brake", "gear"):
        assert f'data-k="{key}"' in html
    assert "fonts.googleapis.com" in html
    # Faz 1 — mobil responsive + jargon tooltip'i
    assert "@media(max-width:768px)" in html
    assert 'class="k-info"' in html and "data-tip=" in html    # Δ / Gaz-Fren baloncuğu


def test_telemetry_trace_height_is_int():
    assert isinstance(app.telemetry_trace_component_height(), int)
    assert app.telemetry_trace_component_height({}) > 400


# --------------------------------------------------------------------------
# Faz 4 — kit diline restyle edilen grafik HUD'ları
# --------------------------------------------------------------------------
def test_dominance_map_html_uses_kit(trace_payload):
    html = app.dominance_map_html(trace_payload)
    assert "__PAYLOAD__" not in html
    assert "--k-panel" in html and "fonts.googleapis.com" in html
    assert html.count("<style>") == 1 and html.count("</style>") == 1
    assert 'id="dom"' in html and "KUŞ BAKIŞI" in html


def test_stint_pace_html_uses_kit(replay_payload):
    html = app.stint_pace_html(replay_payload)
    assert "__STINT_PACE_PAYLOAD__" not in html
    assert "--k-void" in html and "fonts.googleapis.com" in html
    assert html.count("<style>") == 1 and html.count("</style>") == 1
    assert "STINT TEMPOSU" in html and "id='sp'" in html


def test_position_flow_html_uses_kit(replay_payload):
    html = app.position_flow_html(replay_payload)
    assert "__POSITION_FLOW_PAYLOAD__" not in html
    assert "--k-line" in html and "fonts.googleapis.com" in html
    assert html.count("<style>") == 1 and html.count("</style>") == 1
    assert "RACE POSITION FLOW" in html and "id='chart'" in html


def test_session_leaderboard_html_uses_kit():
    import pandas as pd
    df = pd.DataFrame([
        {"Sıra": 1, "Pilot": "Lando Norris", "Takım": "McLaren", "Zaman": "1:30:41", "Lastik": "HARD"},
        {"Sıra": 2, "Pilot": "Max Verstappen", "Takım": "Red Bull Racing", "Zaman": "+2.4", "Lastik": "HARD"},
        {"Sıra": 3, "Pilot": "Charles Leclerc", "Takım": "Ferrari", "Zaman": "+6.1", "Lastik": "MEDIUM"},
        {"Sıra": 4, "Pilot": "George Russell", "Takım": "Mercedes", "Zaman": "+14.2", "Lastik": "MEDIUM"},
    ])
    html = app.session_leaderboard_html(df, "SUZUKA // YARIŞ")
    assert "--k-panel" in html and "fonts.googleapis.com" in html
    assert html.count("color-scheme:dark") == 1
    assert "SUZUKA // YARIŞ" in html and "Lando Norris" in html
    assert app.session_leaderboard_html(pd.DataFrame(), "x") == ""


def test_race_intelligence_hud_uses_kit():
    info = {
        "ok": True, "weather": {"air": 22, "track": 38, "wind": 3},
        "speed_trap": {"driver": "VER", "speed": 331},
        "race_control": [{"time": "14:32", "text": "Pist limitleri izleniyor."}],
        "pits": [{"driver": "NOR", "lap": 16, "compound": "HARD", "lane_time": 2.3}],
        "weather_timeline": [{"time": "14:00", "air": 21, "track": 35, "rain": False}],
        "pit_note": "İlk pit T12.",
    }
    html = app.race_intelligence_hud_html_v19(info)
    assert "--k-panel" in html and "fonts.googleapis.com" in html
    assert html.count("color-scheme:dark") == 1
    assert html.count("{") == html.count("}")
    assert "VER" in html and "Race Control" in html


def test_two_driver_duel_html_uses_kit():
    import numpy as np
    import pandas as pd
    n = 120
    ang = np.linspace(0, 2 * np.pi, n)

    def tel(off):
        return pd.DataFrame({
            "X": 500 * np.cos(ang) + 150 * np.cos(2 * ang + off), "Y": 350 * np.sin(ang),
            "Distance": np.linspace(0, 5000, n), "Speed": 180 + 50 * np.sin(ang * 3 + off),
            "Time": pd.to_timedelta(np.linspace(0, 88.0, n), unit="s"),
        })

    html = app.two_driver_duel_html_stable(
        tel(0), tel(0.6), "VER", "NOR", "Red Bull Racing", "McLaren",
        "#3671c6", "#ff8000", "1:28.2", "1:28.5", 88.2, 88.5,
        {"straights": [], "sectors": []}, [27.1, 31.2, 29.9], [27.3, 31.0, 30.2],
        "SOFT", 9,
    )
    assert "--k-panel" in html and "fonts.googleapis.com" in html
    assert html.count("color-scheme:dark") == 1
    assert "__PAYLOAD__" not in html
    # ana motor <script>'u + sağ canlı telemetri şeridi <script>'u
    assert html.count("<script>") == html.count("</script>") == 2
    assert 'id="rl-spd"' in html and 'id="rl-gear"' in html and "d2-rail" in html
    assert app.two_driver_duel_html_repaired is not None


# --------------------------------------------------------------------------
# Şampiyona + hafta sonu HUD'ları — kit göçü / elevasyon
# --------------------------------------------------------------------------
def _kit_ok(html):
    return (
        "--k-panel" in html
        and "fonts.googleapis.com" in html
        and html.count("color-scheme:dark") == 1
        and html.count("{") == html.count("}")
    )


def test_weekend_overview_hud_uses_kit():
    import datetime
    import pandas as pd
    now = datetime.datetime.now(datetime.timezone.utc)

    def sess(title, code, off_h):
        t = now + datetime.timedelta(hours=off_h)
        return {"title": title, "code": code,
                "time": pd.Timestamp(t).tz_convert("UTC"),
                "estimated_end": t + datetime.timedelta(hours=1.5), "status": ""}

    sessions = [sess("Antrenman 1", "FP1", -50), sess("Sıralama", "Q", -26), sess("Yarış", "R", 5)]
    html = app.weekend_overview_hud({"EventName": "Japonya GP", "Location": "Suzuka", "RoundNumber": 17}, sessions)
    assert _kit_ok(html)
    assert "Japonya GP" in html and "wk-rail" in html
    assert isinstance(app.weekend_overview_component_height(sessions), int)


def test_championship_snapshot_hud_uses_kit():
    import pandas as pd
    ds = pd.DataFrame([{"Pilot": "Lando Norris", "Takım": "McLaren", "Puan": 331},
                       {"Pilot": "Max Verstappen", "Takım": "Red Bull Racing", "Puan": 312}])
    cs = pd.DataFrame([{"Takım": "McLaren", "Sıra": 1, "Puan": 623},
                       {"Takım": "Ferrari", "Sıra": 2, "Puan": 540}])
    html = app.championship_snapshot_hud(ds, cs, list(range(1, 18)), 2026)
    assert _kit_ok(html)
    assert "Lando Norris" in html and "ss-hero" in html
    assert app.championship_snapshot_hud(pd.DataFrame(), cs, [], 2026) == ""


def test_championship_matrix_html_uses_kit():
    import pandas as pd
    rounds = [{"key": f"r{i}", "event_name": f"GP {i}", "country_code": "jp"} for i in range(1, 5)]
    mx = pd.DataFrame([{"Pilot": "NOR", "Takım": "McLaren", "Puan": 331, **{f"r{i}": 25 for i in range(1, 5)}}])
    html = app.championship_matrix_html(mx, rounds)
    assert _kit_ok(html)
    assert "matrix-wrap" in html and "sticky-driver" in html
    assert app.championship_matrix_html(pd.DataFrame(), rounds) == ""


def test_constructor_hud_html_uses_kit():
    import pandas as pd
    cs = pd.DataFrame([{"Takım": "McLaren", "Sıra": 1, "Puan": 623},
                       {"Takım": "Ferrari", "Sıra": 2, "Puan": 540},
                       {"Takım": "Red Bull Racing", "Sıra": 3, "Puan": 511},
                       {"Takım": "Mercedes", "Sıra": 4, "Puan": 468}])
    html = app.constructor_hud_html(cs)
    assert _kit_ok(html)
    assert "podium-wrap" in html


def test_championship_scenarios_html_uses_kit():
    scn = {"ok": True, "races": 4, "sprints": 1, "swing": 108, "leader": "NOR",
           "clinched": False, "still_alive": 2,
           "contenders": [
               {"code": "NOR", "team": "McLaren", "points": 331, "rank": 1, "ceiling": 439, "gap": 0, "alive": True},
               {"code": "VER", "team": "Red Bull Racing", "points": 312, "rank": 2, "ceiling": 420, "gap": 19, "alive": True},
           ]}
    html = app.championship_scenarios_html(scn, lambda t: "#3671c6")
    assert _kit_ok(html)
    assert "scn-list" in html and "YARIŞTA" in html
    assert "yeterli puan verisi yok" in app.championship_scenarios_html({"ok": False}, lambda t: "#fff")


def test_championship_projection_html_uses_kit():
    html = app.championship_projection_html("NOR", "VER", 331, 312, 2, 1, 4, 1, "#ff8000", "#3671c6")
    assert _kit_ok(html)
    assert "pj-verdict" in html and "--pjc:" in html


def test_season_h2h_html_uses_kit():
    h = {"ok": True, "a": "NOR", "b": "VER", "team_a": "McLaren", "team_b": "Red Bull Racing",
         "pts_a": 374, "pts_b": 331, "race_w_a": 11, "race_w_b": 8, "spr_w_a": 3, "spr_w_b": 2,
         "has_sprints": True, "mom_a": 58, "mom_b": 44, "mom_span": 5,
         "rounds": [{"winner": "a", "badge": "GP Suzuka", "a_pos": 1, "b_pos": 2, "a_pts": 25, "b_pts": 18}]}
    html = app.season_h2h_html(h, "#ff8000", "#3671c6")
    assert _kit_ok(html)
    assert "h2h-strip" in html and "--ca:#ff8000" in html
    assert "verisi yok" in app.season_h2h_html({"ok": False}, "#fff", "#fff")


def test_career_h2h_html_uses_kit():
    h = {"ok": True,
         "career_a": {"span": "2019-26", "races": 148, "wins": 9, "podiums": 41, "poles": 12, "points": 1204.5},
         "career_b": {"span": "2015-26", "races": 224, "wins": 65, "podiums": 118, "poles": 44, "points": 3111},
         "teammate_years": 0, "seasons": []}
    html = app.career_h2h_html(h, "NOR", "VER", "#ff8000", "#3671c6", 0, 4)
    assert _kit_ok(html)
    assert "ch-cols" in html and "ch-col.r .ch-grid>div" in html


def test_kit_css_neutralises_legacy_component_selectors():
    css = app.fp_kit.kit_css()
    assert ".r,.box,.tile,.panel,.card,.summary,.hud{background:transparent" in css


def test_stable_race_replay_html_chrome_uses_kit():
    import math
    n = 60
    track = [[math.cos(i / n * 6.28) * 100, math.sin(i / n * 6.28) * 80] for i in range(n)]
    car = {"code": "VER", "team": "Red Bull Racing", "colour": "#3671c6", "grid": 1, "final_position": 1,
           "pit_events": [{"lap": 14, "start": 1200.0, "end": 1222.0}],
           "laps": [{"lap": lp, "position": 1, "compound": "SOFT" if lp <= 14 else "HARD",
                     "stint": 0 if lp <= 14 else 1, "start": (lp - 1) * 90.0, "end": lp * 90.0}
                    for lp in range(1, 31)],
           "profile": {"name": "VER"}}
    payload = {"ok": True, "event": "Test GP", "total_laps": 30, "total_seconds": 2700.0,
               "track": track, "cars": [car]}
    html = app.stable_race_replay_html(payload)
    # chrome restyled to kit, engine untouched
    assert "fonts.googleapis.com" in html and "--k-panel" in html
    assert html.count("color-scheme:dark") == 1
    assert "__PAYLOAD__" not in html
    # motion engine markers still present (claude-a3's perf code)
    assert "function states(t)" in html and "function frame(now)" in html
    # every element id the JS writes to is preserved
    for el_id in ('id="clock"', 'id="sub"', 'id="panel"', 'id="strip"',
                  'id="evbar"', 'id="evnow"', 'id="evlist"', 'id="range"',
                  'id="play"', 'id="track"', 'id="tourbtn"'):
        assert el_id in html, el_id
    # .r-scoped so it beats theme.hud_iframe_style bare selectors
    assert "body>.r{" in html and ".r .pilot{" in html


# --------------------------------------------------------------------------
# Oyun "Nasıl Oynanır" kapısı — sabit-ortalanmış glassmorphism panel
# --------------------------------------------------------------------------
def test_game_intro_copy_is_condensed():
    for key, meta in app._GAME_INTRO_V8.items():
        rules = meta[2]
        assert 2 <= len(rules) <= 3, f"{key}: {len(rules)} kural"
        assert all(len(r) <= 130 for r in rules), key


def test_game_intro_modal_css():
    css = app._GAME_INTRO_MODAL_CSS
    assert 'st-key-giwrap_' in css
    assert 'position:fixed' in css and 'backdrop-filter:blur' in css
    assert css.count("{") == css.count("}")


def test_game_intro_gate_flow(monkeypatch):
    import streamlit as st
    store = {}
    monkeypatch.setattr(app.fp_ui, "get_pref", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(app.fp_ui, "set_pref", lambda k, v: store.__setitem__(k, v))
    rendered = {"n": 0}
    monkeypatch.setattr(app.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(app.st, "button", lambda *a, **k: False)

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(app.st, "container", lambda *a, **k: _Ctx())
    monkeypatch.setattr(app.st, "expander", lambda *a, **k: _Ctx())

    # ilk açılış -> True (oyun verisi gösterilmez)
    assert app._game_intro_gate_v8("podium") is True
    # görülmüş kabul et -> False
    store["gi"] = ["podium"]
    assert app._game_intro_gate_v8("podium") is False
    # tanımsız anahtar -> False
    assert app._game_intro_gate_v8("bilinmez") is False


# --------------------------------------------------------------------------
# Yarış Mühendisi Odası — 4 HUD (strategy_engine §6 çıktısından)
# --------------------------------------------------------------------------
@pytest.fixture
def re_report():
    """analyze_practice() biçiminde sentetik rapor (ağsız)."""
    return {
        "ok": True, "year": 2024, "gp": "Hungary", "session": "FP2",
        "surface_temp_c": 47.5,
        "race_pace": {
            "ok": True, "reference_s": 92.1, "method": "yakıt 0.033 sn/tur",
            "drivers": [
                {"driver": "VER", "team": "Red Bull", "pace_s": 92.1, "gap_s": 0.0, "laps": 9, "compound": "MEDIUM"},
                {"driver": "NOR", "team": "McLaren", "pace_s": 92.3, "gap_s": 0.2, "laps": 8, "compound": "SOFT"},
                {"driver": "HAM", "team": "Mercedes", "pace_s": 92.7, "gap_s": 0.6, "laps": 10, "compound": "HARD"},
            ],
            "teams": [
                {"team": "Red Bull", "pace_s": 92.1, "gap_s": 0.0, "drivers": 1, "no_data": False},
                {"team": "McLaren", "pace_s": 92.3, "gap_s": 0.2, "drivers": 1, "no_data": False},
                {"team": "Mercedes", "pace_s": 92.7, "gap_s": 0.6, "drivers": 1, "no_data": False},
                {"team": "Cadillac", "pace_s": None, "gap_s": None, "drivers": 0, "no_data": True},
                {"team": "Audi", "pace_s": None, "gap_s": None, "drivers": 0, "no_data": True},
            ],
        },
        "degradation": {
            "VER": {"ok": True, "compound": "MEDIUM", "deg_rate_s_per_lap": 0.06,
                    "deg_rate_temp_adjusted": 0.07, "r2": 0.95, "clean_laps": 9,
                    "projected_loss_10_laps_s": 0.7, "dropoff_lap": 14, "dropoff_loss_s": 0.22},
            "NOR": {"ok": True, "compound": "SOFT", "deg_rate_s_per_lap": 0.11,
                    "deg_rate_temp_adjusted": 0.13, "r2": 0.9, "clean_laps": 8,
                    "projected_loss_10_laps_s": 1.3, "dropoff_lap": 9, "dropoff_loss_s": 0.4},
        },
        "compound_dropoff": {
            "SOFT": {"dropoff_lap": 9, "measured": True, "sample_size": 1},
            "MEDIUM": {"dropoff_lap": 14, "measured": True, "sample_size": 1},
            "HARD": {"dropoff_lap": 22, "measured": False, "sample_size": 0},
        },
        "straights": {
            "ok": True, "v_max_kmh": 318.0, "drs_available": True, "drs_gain_kmh": 12.4,
            "estimated": False, "source": "",
            "straights": [{"start_m": 400.0, "end_m": 1300.0, "length_m": 900.0,
                           "v_max_kmh": 318.0, "v_mean_kmh": 280.0, "drs_open_frac": 0.5}],
        },
        "track_dominance": {
            "ok": True,
            "zones": [{"zone": "low", "start_m": 0.0, "end_m": 300.0, "length_m": 300.0},
                     {"zone": "medium", "start_m": 300.0, "end_m": 700.0, "length_m": 400.0},
                     {"zone": "high", "start_m": 700.0, "end_m": 1300.0, "length_m": 600.0}],
            "by_team": {"Red Bull": {"low": 11.6, "medium": 6.0, "high": 3.4},
                       "McLaren": {"low": 11.9, "medium": 6.0, "high": 3.4}},
            "best_by_zone": {"low": {"team": "Red Bull", "seconds": 11.6}},
            "insights": [{"team": "Red Bull", "zone": "low", "zone_tr": "Düşük Hız",
                         "advantage_s": 0.2, "vs": "McLaren"}],
        },
        "critical_corners": [{"corner_id": 1, "distance_m": 1980.0, "v_min_kmh": 78.0,
                              "entry_speed_kmh": 250.0, "severity": 0.8}],
        "corner_compare": [{"corner_id": 1, "distance_m": 1980.0,
                            "v_min_by_driver": {"VER": 78.0, "HAM": 74.0},
                            "delta_to_best": {"VER": 0.0, "HAM": 4.0}}],
        "top_speed": {"v_max_by_driver": {"VER": 318.0, "HAM": 312.0},
                      "delta_to_best": {"VER": 0.0, "HAM": 6.0}},
        "driving_style": {
            "VER": {"ok": True, "full_throttle_frac": 0.62, "coast_frac": 0.05,
                    "throttle_aggression": 0.8, "brake_aggression": 0.7,
                    "trail_brake_index": 0.3, "peak_decel_kmh_s": 900.0, "label": "agresif",
                    "brake_zones": 7, "mean_brake_len_m": 92.0,
                    "brake_point_consistency": 0.83, "lift_and_coast_frac": 0.04,
                    "per_corner": [
                        {"corner_id": 1, "distance_m": 1980.0, "matched": True,
                         "brake_point_m": 1850.0, "entry_speed_kmh": 250.0,
                         "apex_speed_kmh": 78.0, "brake_delta_kmh": 172.0,
                         "trail_brake": 0.4, "brake_aggression": 0.9}]},
            "HAM": {"ok": True, "full_throttle_frac": 0.58, "coast_frac": 0.12,
                    "throttle_aggression": 0.4, "brake_aggression": 0.45,
                    "trail_brake_index": 0.5, "peak_decel_kmh_s": 700.0, "label": "yumuşak",
                    "brake_zones": 7, "mean_brake_len_m": 110.0,
                    "brake_point_consistency": 0.6, "lift_and_coast_frac": 0.11,
                    "per_corner": [
                        {"corner_id": 1, "distance_m": 1980.0, "matched": True,
                         "brake_point_m": 1880.0, "entry_speed_kmh": 246.0,
                         "apex_speed_kmh": 74.0, "brake_delta_kmh": 172.0,
                         "trail_brake": 0.6, "brake_aggression": 0.6}]},
        },
    }


def _no_streamlit_widgets(html):
    for bad in ("data-testid=\"stMetric\"", "stDataFrame", "stProgress", "__PAYLOAD__"):
        assert bad not in html
    assert "color-scheme:dark" in html          # kit_css gömülü


def test_race_pace_deg_hud_structure(re_report):
    html = app.race_pace_deg_hud(re_report)
    _no_streamlit_widgets(html)
    assert "Yarış Temposu" in html and "Red Bull" in html
    assert "+0.200" in html or "+0.2" in html          # McLaren farkı
    assert "<polyline" in html and "sn/tur" in html     # aşınma eğrileri
    assert "°C" in html and "pist" in html              # pist sıcaklığı satırı
    # eksik takımlar (11 kadro) — pace_s=None → "tur yok"
    assert "Cadillac" in html and "Audi" in html and "tur yok" in html
    # per-compound drop-off monospace paneli
    assert "Drop-off" in html and "re-mono" in html and ". tur" in html


def test_race_pace_deg_hud_shows_estimated_teams(re_report):
    re_report["race_pace"]["teams"].append(
        {"team": "Williams", "pace_s": 93.4, "gap_s": 1.3, "drivers": 0,
         "no_data": True, "estimated": True})
    html = app.race_pace_deg_hud(re_report)
    assert "Williams" in html and "~+1.300" in html and "re-row est" in html


def test_race_pace_deg_hud_uses_real_team_colors(re_report):
    html = app.race_pace_deg_hud(re_report)
    assert "re-dot" in html
    assert "--acc:var(--k-cyan)" not in html and "--acc:var(--k-amber)" not in html   # jenerik renk yok
    assert app.season_team_colour("Red Bull", 2024) in html
    assert app.season_team_colour("McLaren", 2024) in html


def test_race_pace_deg_hud_has_axis_grid(re_report):
    html = app.race_pace_deg_hud(re_report)
    assert "re-svgtx" in html                            # eksen referans değerleri
    assert html.count("<line") >= 8                       # yatay+dikey ızgara + eksenler


def test_speed_hierarchy_hud_vmax_vmin(re_report):
    html = app.speed_hierarchy_hud(re_report)
    _no_streamlit_widgets(html)
    assert "Pist Geneli Hız Analizi" in html and "VMIN" in html
    assert "318" in html and "km/s" in html
    # top_speed.v_max_by_driver'a düşen Hız Tuzağı tam-grid paneli (speed_trap yoksa)
    assert "Hız Tuzağı" in html and "st-grid" in html and "st-row" in html
    assert "class='re-big" not in html                  # eski dikey rakam sütunu kaldırıldı
    # VMIN hâlâ ortalamayı merkez alan diverging bar
    assert "re-div-row" in html and "re-div-bar" in html and "ORTALAMA" in html
    # gerçek F1 takım rengi (VER=Red Bull, HAM=Mercedes) — jenerik cyan/amber yok
    assert app.season_team_colour("Red Bull", 2024) in html
    assert app.season_team_colour("Mercedes", 2024) in html


def test_speed_hierarchy_hud_uses_full_speed_trap_grid(re_report):
    # router tüm pilot gridini rep['speed_trap']'e ekleyince panel ONU kullanır
    re_report["speed_trap"] = [
        {"code": "LEC", "team": "Ferrari", "v": 341.0},
        {"code": "VER", "team": "Red Bull", "v": 338.0},
        {"code": "HAM", "team": "Mercedes", "v": 335.0},
        {"code": "NOR", "team": "McLaren", "v": 333.0},
    ]
    html = app.speed_hierarchy_hud(re_report)
    assert "341" in html and "#1" in html
    assert "-6" in html                                  # LEC-HAM farkı (341-335)
    assert html.count("st-row") >= 4
    assert app.season_team_colour("Ferrari", 2024) in html


def test_speed_hierarchy_hud_hides_when_truly_empty():
    html = app.speed_hierarchy_hud({"top_speed": {}, "corner_compare": []})
    assert "<div class='re'>" not in html


def test_driving_character_hud_deep_metrics(re_report):
    html = app.driving_character_hud(re_report)
    _no_streamlit_widgets(html)
    assert "VER" in html and "HAM" in html
    assert "agresif" in html and "yumuşak" in html
    assert "re-pp" in html
    assert "LIFT&amp;COAST" in html and "FREN BÖLGESİ" in html and "TUTARLILIK" in html
    # viraj bazlı apeks + trail-brake karşılaştırması
    assert "re-cmp" in html and "APEKS km/s" in html and "TRAIL-BRAKE" in html
    # pilot kartında gerçek takım rengi (nokta + isim rengi)
    assert "re-dot" in html
    assert app.season_team_colour("Red Bull", 2024) in html


def test_driving_character_hud_hides_when_empty():
    html = app.driving_character_hud({"driving_style": {}})
    assert "<div class='re'>" not in html


def test_track_dominance_hud_shows_zone_advantage(re_report):
    html = app.track_dominance_hud(re_report)
    _no_streamlit_widgets(html)
    assert "Pist Hakimiyeti" in html
    assert "Red Bull" in html and "Düşük Hız" in html and "McLaren" in html
    assert "+0.20s" in html
    assert "re-ins" in html


def test_track_dominance_hud_no_insights_is_safe():
    out = app.track_dominance_hud({"track_dominance": {"ok": False}})
    assert "Pist Hakimiyeti" in out and "en az iki pilotun" in out


def test_race_engineer_room_is_single_iframe(re_report):
    html = app.race_engineer_room_html(re_report)
    _no_streamlit_widgets(html)
    # tek <style>, panel başlıkları, Düzlük Modu kaldırıldı
    assert html.count("<style>") == 1
    assert "class='rr'" in html
    for title in ("Yarış Temposu", "Pist Geneli Hız Analizi", "Pist Hakimiyeti",
                  "Sürüş Karakteristiği"):
        assert title in html
    assert "Düzlük Modu" not in html and "Straight Mode" not in html
    assert "rr-foot" in html                            # "nasıl okunur" gömülü


def test_race_engineer_room_shows_dominance_badges(re_report):
    # ekranın üstünde fosforlu "Pist Hakimiyeti" rozetleri — gerçek türetilmiş veri
    html = app.race_engineer_room_html(re_report)
    assert "re-tags" in html and "re-tagbadge" in html
    assert "KÖŞE CANAVARI" in html and "Red Bull" in html


def test_race_engineer_room_hides_empty_sub_panels():
    # karakter + hız verisi yok -> o paneller hiç basılmaz; oda yine de dolu panellerle döner
    rep = {
        "ok": True, "year": 2024,
        "race_pace": {"teams": [{"team": "Ferrari", "pace_s": 90.0, "gap_s": 0.0,
                                  "drivers": 1, "no_data": False}]},
        "degradation": {}, "straights": {}, "driving_style": {},
        "corner_compare": [], "top_speed": {},
    }
    html = app.race_engineer_room_html(rep)
    assert "Sürüş Karakteristiği" not in html
    assert "Pist Geneli Hız Analizi" not in html          # speed_trap yok + top_speed boş -> panel de gizlenir


def test_re_huds_degrade_gracefully_on_empty():
    for fn in (app.race_pace_deg_hud, app.speed_hierarchy_hud,
               app.driving_character_hud, app.track_dominance_hud,
               app.race_engineer_room_html):
        out = fn({"ok": True})
        assert isinstance(out, str) and "color-scheme:dark" in out



