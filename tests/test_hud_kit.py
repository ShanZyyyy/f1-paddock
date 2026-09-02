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
