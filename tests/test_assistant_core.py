# -*- coding: utf-8 -*-
"""core/paddock_ai/assistant_core.py — bağımsız RAG çekirdek iskeleti.
Ağ yok, LLM yok, dış veri yok (mock)."""

from core.paddock_ai.assistant_core import (
    OUT_OF_SCOPE_REPLY,
    ask,
    generate_response,
    parse_query,
    route_to_data,
)


# ---- 1) parse_query ---------------------------------------------------

def test_parse_race_winner_year_track():
    q = parse_query("1967 Monako GP'sini kim kazandı?")
    assert q.intent == "race_winner"
    assert q.entities == {"year": 1967, "track": "Monaco"}


def test_parse_car_upgrades_team():
    q = parse_query("Aston Martin son güncellemeler neler?")
    assert q.intent == "car_upgrades"
    assert q.entities["team"] == "Aston Martin"


def test_parse_season_champion_needs_year():
    q = parse_query("1994 dünya şampiyonu kim oldu?")
    assert q.intent == "season_champion"
    assert q.entities["year"] == 1994


def test_parse_pole_position():
    q = parse_query("1988 Monako'da pole kimin?")
    assert q.intent == "pole_position"
    assert q.entities == {"year": 1988, "track": "Monaco"}


def test_parse_track_alias_longest_match():
    assert parse_query("sao paulo yarışını kim kazandı").entities["track"] == "Brazilian"


def test_parse_unknown_when_no_signal():
    q = parse_query("bugün hava nasıl?")
    assert q.intent == "unknown"
    assert not q.has_f1_signal


def test_parse_team_only_defaults_to_upgrades():
    q = parse_query("Ferrari")
    assert q.intent == "car_upgrades" and q.entities["team"] == "Ferrari"


# ---- 2) route_to_data ----------------------------------------------

def test_route_race_winner_ok():
    d = route_to_data("race_winner", {"year": 1967, "track": "Monaco"})
    assert d["status"] == "ok" and d["winner"] == "Denny Hulme"


def test_route_race_winner_not_found():
    d = route_to_data("race_winner", {"year": 1967, "track": "Gibraltar"})
    assert d["status"] == "not_found"


def test_route_race_winner_need_more():
    d = route_to_data("race_winner", {"year": 1967})
    assert d["status"] == "need_more" and d["missing"] == ["track"]


def test_route_car_upgrades_ok():
    d = route_to_data("car_upgrades", {"team": "Aston Martin"})
    assert d["status"] == "ok" and len(d["updates"]) == 2


def test_route_car_upgrades_not_found():
    assert route_to_data("car_upgrades", {"team": "Williams"})["status"] == "not_found"


def test_route_unsupported_intent():
    assert route_to_data("weather", {})["status"] == "unsupported"


# ---- 3) generate_response ----------------------------------------

def test_generate_race_winner_sentence():
    d = {"intent": "race_winner", "status": "ok", "year": 1967, "track": "Monaco",
         "winner": "Denny Hulme", "constructor": "Brabham-Repco"}
    out = generate_response(d)
    assert out == "1967 Monaco GP'sini Denny Hulme (Brabham-Repco) kazanmıştır."


def test_generate_champion_sentence():
    d = {"intent": "season_champion", "status": "ok", "year": 1994,
         "driver": "Michael Schumacher", "constructor": "Benetton-Ford"}
    assert "1994 Formula 1 Dünya Şampiyonu Michael Schumacher" in generate_response(d)


def test_generate_upgrades_multiline():
    d = route_to_data("car_upgrades", {"team": "Aston Martin"})
    out = generate_response(d)
    assert out.count("•") == 2 and "Aston Martin" in out


def test_generate_not_found_is_honest():
    out = generate_response({"intent": "race_winner", "status": "not_found",
                             "year": 1967, "track": "Gibraltar"})
    assert "bulamadım" in out


def test_generate_need_more_asks():
    out = generate_response({"intent": "race_winner", "status": "need_more",
                             "missing": ["track"]})
    assert "yarışı" in out or "pist" in out


def test_generate_empty_is_out_of_scope():
    assert generate_response({}) == OUT_OF_SCOPE_REPLY


# ---- uçtan uca ask() --------------------------------------------

def test_ask_full_flow_race_winner():
    r = ask("1967 Monako GP'sini kim kazandı?")
    assert r.ok and r.intent == "race_winner"
    assert r.text == "1967 Monaco GP'sini Denny Hulme (Brabham-Repco) kazanmıştır."


def test_ask_car_upgrades():
    r = ask("Aston Martin en son hangi güncellemeleri getirdi?")
    assert r.ok and r.intent == "car_upgrades" and "•" in r.text


def test_ask_rejects_off_topic_with_engineer_line():
    for q in ("Nasılsın?", "Bugün hava nasıl?", "Bana bir şiir yaz",
              "iPhone fiyatı", "Merhaba"):
        r = ask(q)
        assert not r.ok
        assert r.text == OUT_OF_SCOPE_REPLY
        assert r.intent == "out_of_scope"


def test_ask_not_found_flows_through():
    r = ask("1967 Cebeli Tarık GP'sini kim kazandı?")
    assert not r.ok and r.intent == "race_winner" and "bulamadım" in r.text


def test_ask_does_not_touch_existing_pipeline():
    # bağımsızlık kanıtı: assistant_core, pipeline.answer'ı import/çağırmaz
    import core.paddock_ai.assistant_core as ac
    src = ac.__file__
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "from .pipeline" not in body and "import pipeline" not in body
