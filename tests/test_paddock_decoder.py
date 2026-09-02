# -*- coding: utf-8 -*-
"""core/games/paddock_decoder.py — bulanık resim tahmin oyunu çekirdeği.
Ağsız, saf. Tahmin SINIRSIZ; tur yalnızca doğru seçim ya da 'geç' ile biter."""

import pytest

from core.games import paddock_decoder as deco

RS = deco.REVEAL_STEPS  # 4


# ---- veri yapısı ----------------------------------------------------

def test_reveal_steps_and_unlimited_guesses():
    assert deco.REVEAL_STEPS == 4
    r = deco.new_round("teams", target_index=0)     # Ferrari
    for _ in range(9):                              # 9 yanlış — tur bitmez
        res = deco.submit_guess(r, "McLaren")
        assert res["accepted"] and not res["over"]
    assert r.attempts_used == 9 and not r.over


def test_pools_are_large():
    assert len(deco.TARGETS["teams"]) >= 7
    assert len(deco.TARGETS["drivers"]) >= 12
    assert len(deco.TARGETS["tracks"]) >= 7
    for cat in deco.CATEGORIES:
        for t in deco.TARGETS[cat]:
            assert t.answer and t.image.startswith("https://") and t.hint


def test_pool_options_are_the_canonical_names():
    for cat in deco.CATEGORIES:
        assert deco.pool_options(cat) == [t.answer for t in deco.TARGETS[cat]]


def test_hints_do_not_name_the_answer():
    for cat in deco.CATEGORIES:
        for t in deco.TARGETS[cat]:
            words = {w for w in deco.normalize(t.answer).split() if len(w) > 3}
            assert not (words & set(deco.normalize(t.hint).split())), (t.answer, t.hint)


# ---- tahmin (açılır listeden seçim) ------------------------------

def test_correct_pick_solves():
    r = deco.new_round("teams", target_index=0)
    assert deco.submit_guess(r, "McLaren")["correct"] is False and not r.over
    res = deco.submit_guess(r, "Ferrari")
    assert res["correct"] and res["solved"] and r.over
    assert deco.submit_guess(r, "McLaren")["accepted"] is False


def test_pick_case_and_accent_insensitive():
    r = deco.new_round("drivers", target_index=1)   # Max Verstappen
    assert deco.submit_guess(r, "  max verstappen ")["correct"]


def test_skip_ends_round_as_failed():
    r = deco.new_round("tracks", target_index=0)
    deco.submit_guess(r, "Suzuka Circuit")
    deco.skip_round(r)
    assert r.over and r.failed and not r.solved
    assert deco.submit_guess(r, r.target.answer)["accepted"] is False


def test_empty_guess_not_counted():
    r = deco.new_round("teams", target_index=1)
    assert deco.submit_guess(r, "  ")["accepted"] is False
    assert r.attempts_used == 0


# ---- açılma eğrisi ------------------------------------------------

def test_revealed_progresses_then_fixes():
    r = deco.new_round("drivers", target_index=0)
    assert deco.public_state(r)["revealed"] == 0.0
    for i in range(1, RS + 3):
        deco.submit_guess(r, "Max Verstappen")
        assert deco.public_state(r)["revealed"] == pytest.approx(min(1.0, i / RS))


def test_blur_never_reaches_zero_while_playing():
    r = deco.new_round("teams", target_index=0)
    for _ in range(RS + 5):
        deco.submit_guess(r, "McLaren")
    assert deco.public_state(r)["blur_px"] > 0        # sabit ama açık değil


# ---- pist rotasyonu ------------------------------------------------

def test_tracks_flipped_for_first_two_guesses_only():
    r = deco.new_round("tracks", target_index=0)
    assert deco.image_orientation(r) == (180, False)
    deco.submit_guess(r, "Suzuka Circuit")
    assert deco.image_orientation(r) == (180, False)
    deco.submit_guess(r, "Suzuka Circuit")           # 2. yanlış
    assert deco.image_orientation(r) == (0, False)   # düzelir


def test_tracks_no_mirror_ever():
    for i in range(len(deco.TARGETS["tracks"])):
        r = deco.new_round("tracks", target_index=i)
        assert deco.image_orientation(r)[1] is False


def test_non_track_never_rotates():
    for cat in ("teams", "drivers"):
        assert deco.image_orientation(deco.new_round(cat, target_index=0)) == (0, False)


def test_orientation_straight_when_over():
    r = deco.new_round("tracks", target_index=0)
    deco.submit_guess(r, "Circuit de Monaco")
    assert deco.image_orientation(r) == (0, False)


# ---- görünüm ------------------------------------------------------

def test_answer_hidden_until_over():
    r = deco.new_round("teams", target_index=2)      # McLaren
    for _ in range(6):
        deco.submit_guess(r, "Ferrari")
    assert deco.public_state(r)["answer"] is None    # hâlâ sürüyor
    deco.skip_round(r)
    assert deco.public_state(r)["answer"] == "McLaren"


def test_hint_after_reveal_fixed():
    r = deco.new_round("drivers", target_index=0)
    for i in range(1, RS):
        deco.submit_guess(r, "Max Verstappen")
        assert deco.public_state(r)["hint"] is None
    deco.submit_guess(r, "Max Verstappen")           # RS. yanlış
    assert deco.public_state(r)["hint"]


def test_public_state_shape():
    v = deco.public_state(deco.new_round("tracks", target_index=0))
    assert v["pool"] == deco.pool_options("tracks")
    assert v["orientation"] == (180, False)
    assert v["reveal_steps"] == RS and v["revealed"] == 0.0


# ---- serileştirme (skipped alanı dâhil) --------------------------

def test_round_roundtrips_with_skip():
    r = deco.new_round("drivers", target_index=1)
    deco.submit_guess(r, "Lewis Hamilton")
    deco.skip_round(r)
    back = deco.DecoderRound.from_dict(r.to_dict())
    assert back.guesses == ["Lewis Hamilton"] and back.skipped and back.failed


def test_seedless_sessions_vary():
    assert len({deco.new_session().current.target_index for _ in range(25)}) >= 2


# ---- puanlama ------------------------------------------------------

def test_score_decays_with_wrong_guesses():
    def solve_on(nth):
        r = deco.new_round("teams", target_index=0)
        for _ in range(nth - 1):
            deco.submit_guess(r, "McLaren")
        deco.submit_guess(r, "Ferrari")
        return deco.score_round(r)
    assert solve_on(1) == 50
    assert solve_on(2) == 41
    assert solve_on(3) == 32
    assert solve_on(30) == 8               # taban
    rs = deco.new_round("teams", target_index=0); deco.skip_round(rs)
    assert deco.score_round(rs) == 2


# ---- oturum ------------------------------------------------------

def test_session_sweep_all_first_try():
    s = deco.new_session()
    assert s.order == ["teams", "drivers", "tracks"]
    for _ in range(3):
        deco.submit_guess(s.current, s.current.target.answer)
        s.advance()
    assert s.done and s.swept and s.total_score == 50 * 3 + 40


def test_session_advance_blocked_until_solved_or_skipped():
    s = deco.new_session()
    deco.submit_guess(s.current, "___yok___")
    assert s.advance().category == "teams" and s.index == 0
    deco.skip_round(s.current)
    assert s.advance().category == "drivers"


def test_session_no_sweep_if_one_skipped():
    s = deco.new_session()
    deco.submit_guess(s.current, s.current.target.answer); s.advance()
    deco.skip_round(s.current); s.advance()
    deco.submit_guess(s.current, s.current.target.answer)
    assert s.done and not s.swept
    assert s.total_score == 50 + 2 + 50


def test_session_public_state():
    v = deco.new_session().public_state()
    assert v["step"] == "1/3" and v["round"]["category"] == "teams"
