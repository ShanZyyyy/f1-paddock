# -*- coding: utf-8 -*-
"""core/games/paddock_decoder.py — bulanık tahmin oyunu çekirdeği. Ağsız, saf."""

import pytest

from core.games import paddock_decoder as deco


# ---- veri yapısı ----------------------------------------------------

def test_three_categories_three_targets_each():
    assert set(deco.CATEGORIES) == {"teams", "drivers", "tracks"}
    for cat in deco.CATEGORIES:
        assert len(deco.TARGETS[cat]) == 3
        for t in deco.TARGETS[cat]:
            assert t.answer and t.image and t.hint
            assert t.category == cat


def test_max_guesses_is_five():
    assert deco.MAX_GUESSES == 5


# ---- Levenshtein / bulanık eşleşme --------------------------------

def test_levenshtein_basics():
    assert deco.levenshtein("ferrari", "ferrari") == 0
    assert deco.levenshtein("ferari", "ferrari") == 1
    assert deco.levenshtein("", "abc") == 3
    assert deco.levenshtein("kitten", "sitting") == 3


def test_normalize_folds_turkish_and_punct():
    assert deco.normalize("  Ferrari!  ") == "ferrari"
    assert deco.normalize("Nürburgring") == "nurburgring"
    assert deco.normalize("Işık-Öz") == "isik oz"


def test_typo_is_accepted():
    ferrari = deco.TARGETS["teams"][0]
    assert deco.is_correct("ferari", ferrari)       # alias
    assert deco.is_correct("ferrai", ferrari)       # fuzzy (aliaslarda yok)
    assert deco.is_correct("Ferrari", ferrari)
    assert deco.is_correct("scuderia ferrari", ferrari)
    assert not deco.is_correct("mercedes", ferrari)
    assert not deco.is_correct("red bull", ferrari)


def test_alias_and_partial_word_match():
    monaco = deco.TARGETS["tracks"][0]           # Circuit de Monaco
    assert deco.is_correct("monaco", monaco)
    assert deco.is_correct("monako", monaco)     # TR yazım
    assert deco.is_correct("monte carlo", monaco)


def test_driver_typo_tolerated():
    verst = deco.TARGETS["drivers"][1]           # Max Verstappen
    assert deco.is_correct("verstapen", verst)
    assert deco.is_correct("verstappen", verst)
    assert not deco.is_correct("hamilton", verst)


# ---- tur state'i ---------------------------------------------------

def test_new_round_validates_category():
    with pytest.raises(ValueError):
        deco.new_round("engines")


def test_seed_is_deterministic():
    a = deco.new_round("teams", seed="2026-05-01")
    b = deco.new_round("teams", seed="2026-05-01")
    c = deco.new_round("teams", seed="2026-05-02")
    assert a.target_index == b.target_index
    # farklı gün genelde farklı hedef (3 hedef; çakışma olabilir ama nadiren)
    assert isinstance(c.target_index, int)


def test_seedless_sessions_vary():
    # seed'siz oyunlar zamanla farklı hedefler üretmeli (art arda 20'de en az 2 farklı)
    seen = {deco.new_session().current.target_index for _ in range(20)}
    assert len(seen) >= 2


def test_seedless_session_stable_across_roundtrip():
    s = deco.new_session()
    idx = s.current.target_index
    restored = deco.DecoderSession.from_dict(s.to_dict())
    assert restored.current.target_index == idx
    # ilerleyince de aynı seed'i kullanır (rerun'da hedef kaymaz)
    deco.submit_guess(restored.current, restored.current.target.answer)
    restored.advance()
    again = deco.DecoderSession.from_dict(restored.to_dict())
    assert again.current.target_index == restored.current.target_index


def test_correct_guess_solves_and_stops_countdown():
    r = deco.new_round("teams", target_index=0)   # Ferrari
    assert r.remaining == 5
    res = deco.submit_guess(r, "ferrai")          # fuzzy → kanonik cevaba eşleşir
    assert res["correct"] and res["solved"]
    assert res["matched_on"] == "Ferrari"
    assert r.solved and r.over
    # çözüldükten sonra yeni tahmin sayılmaz
    res2 = deco.submit_guess(r, "başka")
    assert res2["accepted"] is False
    assert r.attempts_used == 1


def test_wrong_guesses_decrement_remaining():
    r = deco.new_round("drivers", target_index=0)  # Lewis Hamilton
    for expected_left in (4, 3, 2, 1):
        res = deco.submit_guess(r, "yanlış tahmin")
        assert res["accepted"] and not res["correct"]
        assert res["remaining"] == expected_left
    res = deco.submit_guess(r, "yine yanlış")
    assert res["remaining"] == 0
    assert res["failed"] and not res["solved"]
    assert r.failed and r.over


def test_guess_after_game_over_is_ignored():
    r = deco.new_round("tracks", target_index=2)   # Suzuka
    for _ in range(5):
        deco.submit_guess(r, "xxx")
    assert r.failed
    res = deco.submit_guess(r, "suzuka")
    assert res["accepted"] is False
    assert not r.solved                            # bitti, kurtaramaz


def test_empty_guess_not_counted():
    r = deco.new_round("teams", target_index=1)
    res = deco.submit_guess(r, "   ")
    assert res["accepted"] is False
    assert r.remaining == 5


def test_distance_and_similarity_reported():
    r = deco.new_round("teams", target_index=0)
    res = deco.submit_guess(r, "ferrai")
    assert res["distance"] == 1
    assert res["similarity"] >= 0.78


# ---- UI görünümü: blur + spoiler yok -----------------------------

def test_blur_decreases_with_wrong_guesses():
    r = deco.new_round("drivers", target_index=2)   # Senna
    full = deco.public_state(r)["blur_px"]
    assert full == deco.blur_px(5)
    deco.submit_guess(r, "prost")
    less = deco.public_state(r)["blur_px"]
    assert 0 < less < full


def test_blur_zero_when_solved():
    r = deco.new_round("teams", target_index=0)
    deco.submit_guess(r, "ferrari")
    assert deco.public_state(r)["blur_px"] == 0.0


def test_blur_zero_when_failed():
    r = deco.new_round("teams", target_index=0)
    for _ in range(5):
        deco.submit_guess(r, "nope")
    assert deco.public_state(r)["blur_px"] == 0.0


def test_public_state_hides_answer_until_over():
    r = deco.new_round("teams", target_index=2)     # McLaren
    assert deco.public_state(r)["answer"] is None
    deco.submit_guess(r, "williams")
    assert deco.public_state(r)["answer"] is None   # oyun sürüyor
    for _ in range(4):
        deco.submit_guess(r, "williams")
    view = deco.public_state(r)
    assert view["over"] and view["answer"] == "McLaren"


def test_hint_revealed_after_two_misses():
    r = deco.new_round("drivers", target_index=0)   # Hamilton
    assert deco.public_state(r)["hint"] is None
    deco.submit_guess(r, "x")
    assert deco.public_state(r)["hint"] is None
    deco.submit_guess(r, "y")
    assert deco.public_state(r)["hint"]             # 2 yanlış → ipucu açık


def test_public_state_exposes_remaining_for_ui():
    r = deco.new_round("tracks", target_index=0)
    deco.submit_guess(r, "wrong")
    view = deco.public_state(r)
    assert view["remaining"] == 4
    assert view["attempts_used"] == 1
    assert view["max_guesses"] == 5
    assert view["image"].startswith("https://") and "Monaco" in view["image"]


# ---- serileştirme (Streamlit session_state) ----------------------

def test_round_roundtrips_through_dict():
    r = deco.new_round("drivers", target_index=1)
    deco.submit_guess(r, "wrong one")
    deco.submit_guess(r, "verstappen")
    restored = deco.DecoderRound.from_dict(r.to_dict())
    assert restored.category == "drivers"
    assert restored.target_index == 1
    assert restored.guesses == ["wrong one", "verstappen"]
    assert restored.solved is True
    assert restored.remaining == r.remaining


# ---- puanlama ------------------------------------------------------

def test_score_scales_with_attempts():
    r = deco.new_round("teams", target_index=0)
    deco.submit_guess(r, "ferrari")
    assert deco.score_round(r) == 50            # 1. deneme

    r2 = deco.new_round("teams", target_index=0)
    deco.submit_guess(r2, "x"); deco.submit_guess(r2, "x"); deco.submit_guess(r2, "ferrari")
    assert deco.score_round(r2) == 30           # 3. deneme


def test_score_zero_while_playing_small_on_fail():
    r = deco.new_round("teams", target_index=0)
    assert deco.score_round(r) == 0
    for _ in range(5):
        deco.submit_guess(r, "nope")
    assert deco.score_round(r) == 2


# ---- oturum (3 kategori) -----------------------------------------

def test_session_runs_three_categories_in_order():
    s = deco.new_session()
    assert [s.order[0]] == ["teams"]
    assert s.current.category == "teams"
    deco.submit_guess(s.current, s.current.target.answer)
    s.advance()
    assert s.current.category == "drivers"
    deco.submit_guess(s.current, s.current.target.answer)
    s.advance()
    assert s.current.category == "tracks"
    deco.submit_guess(s.current, s.current.target.answer)
    assert s.done and s.swept


def test_session_advance_blocked_until_round_over():
    s = deco.new_session()
    deco.submit_guess(s.current, "wrong")
    same = s.advance()
    assert same.category == "teams"            # tur bitmedi, ilerlemedi
    assert s.index == 0


def test_session_total_score_includes_sweep_bonus():
    s = deco.new_session()
    for _ in range(3):
        deco.submit_guess(s.current, s.current.target.answer)
        s.advance()
    assert s.swept
    assert s.total_score == 50 * 3 + 25        # üç kez 1. deneme + sweep


def test_session_no_sweep_bonus_if_one_missed():
    s = deco.new_session()
    deco.submit_guess(s.current, s.current.target.answer); s.advance()
    for _ in range(5):
        deco.submit_guess(s.current, "nope")
    s.advance()
    deco.submit_guess(s.current, s.current.target.answer)
    assert s.done and not s.swept
    assert s.total_score == 50 + 2 + 50        # sweep yok


def test_session_roundtrips_and_seed_deterministic():
    s = deco.new_session(seed="2026-07-04")
    deco.submit_guess(s.current, "wrong");
    restored = deco.DecoderSession.from_dict(s.to_dict())
    assert restored.index == s.index
    assert restored.current.guesses == ["wrong"]
    s2 = deco.new_session(seed="2026-07-04")
    assert s2.current.target_index == s.current.target_index


def test_session_public_state_shape():
    s = deco.new_session()
    v = s.public_state()
    assert v["step"] == "1/3"
    assert v["round"]["category"] == "teams"
    assert v["total_score"] == 0 and not v["done"]
