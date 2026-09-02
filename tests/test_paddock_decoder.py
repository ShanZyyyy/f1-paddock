# -*- coding: utf-8 -*-
"""core/games/paddock_decoder.py — bulanık resim tahmin oyunu çekirdeği. Ağsız, saf."""

import pytest

from core.games import paddock_decoder as deco

MG = deco.MAX_GUESSES  # 3


# ---- veri yapısı ----------------------------------------------------

def test_max_guesses_is_three():
    assert deco.MAX_GUESSES == 3


def test_pools_are_large_enough():
    assert set(deco.CATEGORIES) == {"teams", "drivers", "tracks"}
    assert len(deco.TARGETS["teams"]) >= 7
    assert len(deco.TARGETS["drivers"]) >= 12      # "daha çok pilot"
    assert len(deco.TARGETS["tracks"]) >= 7
    for cat in deco.CATEGORIES:
        for t in deco.TARGETS[cat]:
            assert t.answer and t.image and t.hint and t.category == cat
            assert t.image.startswith("https://")


def test_pool_options_are_the_canonical_names():
    for cat in deco.CATEGORIES:
        opts = deco.pool_options(cat)
        assert opts == [t.answer for t in deco.TARGETS[cat]]
        assert len(set(opts)) == len(opts)         # tekrarsız


def test_hints_do_not_name_the_answer():
    for cat in deco.CATEGORIES:
        for t in deco.TARGETS[cat]:
            words = {w for w in deco.normalize(t.answer).split() if len(w) > 3}
            hint_words = set(deco.normalize(t.hint).split())
            assert not (words & hint_words), (t.answer, t.hint)


# ---- tahmin (açılır listeden seçim) ------------------------------

def test_exact_pick_solves():
    r = deco.new_round("teams", target_index=0)     # Ferrari
    assert r.remaining == MG
    res = deco.submit_guess(r, "Ferrari")
    assert res["correct"] and res["solved"] and r.over
    res2 = deco.submit_guess(r, "McLaren")
    assert res2["accepted"] is False and r.attempts_used == 1


def test_wrong_pick_burns_a_guess():
    r = deco.new_round("drivers", target_index=0)   # Hamilton
    lefts = [deco.submit_guess(r, "Max Verstappen")["remaining"] for _ in range(MG)]
    assert lefts == [MG - 1, MG - 2, 0]
    assert r.failed and not r.solved and r.over


def test_pick_is_case_and_accent_insensitive():
    r = deco.new_round("teams", target_index=0)     # Ferrari
    assert deco.submit_guess(r, "  FERRARI ")["correct"]
    r2 = deco.new_round("drivers", target_index=1)  # Max Verstappen
    assert deco.submit_guess(r2, "max verstappen")["correct"]


def test_guess_after_over_ignored():
    r = deco.new_round("tracks", target_index=2)
    for _ in range(MG):
        deco.submit_guess(r, "Circuit de Monaco")
    assert r.failed
    assert deco.submit_guess(r, r.target.answer)["accepted"] is False
    assert not r.solved


def test_empty_guess_not_counted():
    r = deco.new_round("teams", target_index=1)
    assert deco.submit_guess(r, "  ")["accepted"] is False
    assert r.remaining == MG


# ---- pist rotasyonu ------------------------------------------------

def test_tracks_rotated_until_last_guess():
    r = deco.new_round("tracks", target_index=0)    # Monaco
    deg, mirror = deco.image_orientation(r)
    assert deg == 180 and mirror in (True, False)  # 180° döndürülür
    deco.submit_guess(r, "Suzuka Circuit")          # 2 kaldı
    assert deco.image_orientation(r)[0] == 180
    deco.submit_guess(r, "Suzuka Circuit")          # 1 kaldı (son hak)
    assert deco.image_orientation(r) == (0, False)  # düzelir


def test_tracks_orientation_straight_when_solved_or_failed():
    r = deco.new_round("tracks", target_index=0)
    deco.submit_guess(r, "Circuit de Monaco")
    assert deco.image_orientation(r) == (0, False)


def test_non_track_categories_never_rotate():
    for cat in ("teams", "drivers"):
        r = deco.new_round(cat, target_index=0)
        assert deco.image_orientation(r) == (0, False)


def test_orientation_deterministic_per_target():
    a = deco.image_orientation(deco.new_round("tracks", target_index=3))
    b = deco.image_orientation(deco.new_round("tracks", target_index=3))
    assert a == b and a[0] != 0


# ---- görünüm: spoiler yok ---------------------------------------

def test_answer_hidden_until_over():
    r = deco.new_round("teams", target_index=2)     # McLaren
    assert deco.public_state(r)["answer"] is None
    deco.submit_guess(r, "Ferrari")
    assert deco.public_state(r)["answer"] is None
    for _ in range(MG - 1):
        deco.submit_guess(r, "Ferrari")
    v = deco.public_state(r)
    assert v["over"] and v["answer"] == "McLaren"


def test_hint_only_on_last_guess():
    r = deco.new_round("drivers", target_index=0)
    assert deco.public_state(r)["hint"] is None
    deco.submit_guess(r, "Max Verstappen")          # 1. yanlış
    assert deco.public_state(r)["hint"] is None
    deco.submit_guess(r, "Max Verstappen")          # 2. yanlış → 1 hak → ipucu
    assert deco.public_state(r)["hint"]


def test_public_state_carries_pool_and_orientation():
    r = deco.new_round("tracks", target_index=0)
    v = deco.public_state(r)
    assert v["pool"] == deco.pool_options("tracks")
    assert v["orientation"][0] == 180
    assert v["max_guesses"] == 3


# ---- serileştirme ---------------------------------------------

def test_round_roundtrips():
    r = deco.new_round("drivers", target_index=1)
    deco.submit_guess(r, "Lewis Hamilton")
    deco.submit_guess(r, "Max Verstappen")
    back = deco.DecoderRound.from_dict(r.to_dict())
    assert back.guesses == ["Lewis Hamilton", "Max Verstappen"]
    assert back.solved and back.remaining == r.remaining


def test_seedless_sessions_vary():
    seen = {deco.new_session().current.target_index for _ in range(25)}
    assert len(seen) >= 2


def test_seedless_session_stable_across_roundtrip():
    s = deco.new_session()
    idx = s.current.target_index
    back = deco.DecoderSession.from_dict(s.to_dict())
    assert back.current.target_index == idx


# ---- puanlama ------------------------------------------------------

def test_score_curve():
    r = deco.new_round("teams", target_index=0)
    deco.submit_guess(r, "Ferrari")
    assert deco.score_round(r) == 50

    r2 = deco.new_round("teams", target_index=0)
    deco.submit_guess(r2, "McLaren"); deco.submit_guess(r2, "Ferrari")
    assert deco.score_round(r2) == 30

    r3 = deco.new_round("teams", target_index=0)
    deco.submit_guess(r3, "McLaren"); deco.submit_guess(r3, "McLaren"); deco.submit_guess(r3, "Ferrari")
    assert deco.score_round(r3) == 15

    r4 = deco.new_round("teams", target_index=0)
    for _ in range(MG):
        deco.submit_guess(r4, "McLaren")
    assert deco.score_round(r4) == 2


# ---- oturum ------------------------------------------------------

def test_session_three_categories_in_order_and_sweep():
    s = deco.new_session()
    assert s.order == ["teams", "drivers", "tracks"]
    for _ in range(3):
        deco.submit_guess(s.current, s.current.target.answer)
        s.advance()
    assert s.done and s.swept
    assert s.total_score == 50 * 3 + 40            # üç kez 1. deneme + sweep 40


def test_session_advance_blocked_until_round_over():
    s = deco.new_session()
    deco.submit_guess(s.current, "___yanlış___")
    assert s.advance().category == "teams" and s.index == 0


def test_session_no_sweep_if_one_missed():
    s = deco.new_session()
    deco.submit_guess(s.current, s.current.target.answer); s.advance()
    for _ in range(MG):
        deco.submit_guess(s.current, "___yanlış___")
    s.advance()
    deco.submit_guess(s.current, s.current.target.answer)
    assert s.done and not s.swept
    assert s.total_score == 50 + 2 + 50


def test_session_public_state_shape():
    v = deco.new_session().public_state()
    assert v["step"] == "1/3"
    assert v["round"]["category"] == "teams"
    assert v["round"]["pool"] == deco.pool_options("teams")
