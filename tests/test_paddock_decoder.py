# -*- coding: utf-8 -*-
"""core/games/paddock_decoder.py — bulanık tahmin oyunu çekirdeği. Ağsız, saf."""

import pytest

from core.games import paddock_decoder as deco

MG = deco.MAX_GUESSES  # 4


# ---- veri yapısı ----------------------------------------------------

def test_three_categories_three_targets_each():
    assert set(deco.CATEGORIES) == {"teams", "drivers", "tracks"}
    for cat in deco.CATEGORIES:
        assert len(deco.TARGETS[cat]) == 3
        for t in deco.TARGETS[cat]:
            assert t.answer and t.image and t.hint
            assert t.category == cat


def test_max_guesses_is_four():
    assert deco.MAX_GUESSES == 4


def test_hints_do_not_name_the_answer():
    # ipucu, cevabın kendisini içermemeli (spoiler değil, ima)
    for cat in deco.CATEGORIES:
        for t in deco.TARGETS[cat]:
            words = {w for w in deco.normalize(t.answer).split() if len(w) > 3}
            hint_words = set(deco.normalize(t.hint).split())
            assert not (words & hint_words), (t.answer, t.hint)


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


def test_small_typo_accepted_but_loose_guess_rejected():
    ferrari = deco.TARGETS["teams"][0]
    assert deco.is_correct("ferrari", ferrari)
    assert deco.is_correct("ferari", ferrari)        # 1 harf düşük → geçer
    assert deco.is_correct("scuderia ferrari", ferrari)
    assert not deco.is_correct("ferai", ferrari)     # 2 harf → 0.82 eşiğin altı
    assert not deco.is_correct("mercedes", ferrari)
    assert not deco.is_correct("red bull", ferrari)


def test_track_language_variants_and_keyword():
    monaco = deco.TARGETS["tracks"][0]               # Circuit de Monaco
    assert deco.is_correct("monaco", monaco)         # ayırt edici kelime
    assert deco.is_correct("monako", monaco)         # TR yazım
    assert deco.is_correct("monte carlo", monaco)
    assert not deco.is_correct("de", monaco)         # kısa jenerik kelime saymaz


def test_driver_typo_tolerated_no_nickname():
    verst = deco.TARGETS["drivers"][1]               # Max Verstappen
    assert deco.is_correct("verstappen", verst)
    assert deco.is_correct("verstapen", verst)       # yazım hatası
    assert not deco.is_correct("max", verst)         # tek isim / lakap YOK
    assert not deco.is_correct("hamilton", verst)


# ---- tur state'i ---------------------------------------------------

def test_new_round_validates_category():
    with pytest.raises(ValueError):
        deco.new_round("engines")


def test_seed_is_deterministic():
    a = deco.new_round("teams", seed="2026-05-01")
    b = deco.new_round("teams", seed="2026-05-01")
    assert a.target_index == b.target_index
    assert isinstance(deco.new_round("teams", seed="x").target_index, int)


def test_seedless_sessions_vary():
    seen = {deco.new_session().current.target_index for _ in range(20)}
    assert len(seen) >= 2


def test_seedless_session_stable_across_roundtrip():
    s = deco.new_session()
    idx = s.current.target_index
    restored = deco.DecoderSession.from_dict(s.to_dict())
    assert restored.current.target_index == idx
    deco.submit_guess(restored.current, restored.current.target.answer)
    restored.advance()
    again = deco.DecoderSession.from_dict(restored.to_dict())
    assert again.current.target_index == restored.current.target_index


def test_correct_guess_solves_and_stops_countdown():
    r = deco.new_round("teams", target_index=0)   # Ferrari
    assert r.remaining == MG
    res = deco.submit_guess(r, "ferrai")
    assert res["correct"] and res["solved"]
    assert res["matched_on"] == "Ferrari"
    assert r.solved and r.over
    res2 = deco.submit_guess(r, "başka")
    assert res2["accepted"] is False
    assert r.attempts_used == 1


def test_wrong_guesses_burn_down_to_fail():
    r = deco.new_round("drivers", target_index=0)
    lefts = []
    for _ in range(MG):
        lefts.append(deco.submit_guess(r, "yanlış tahmin")["remaining"])
    assert lefts == list(range(MG - 1, -1, -1))       # [3,2,1,0]
    assert r.failed and r.over and not r.solved


def test_guess_after_game_over_is_ignored():
    r = deco.new_round("tracks", target_index=2)   # Suzuka
    for _ in range(MG):
        deco.submit_guess(r, "xxx")
    assert r.failed
    res = deco.submit_guess(r, "suzuka")
    assert res["accepted"] is False and not r.solved


def test_empty_guess_not_counted():
    r = deco.new_round("teams", target_index=1)
    assert deco.submit_guess(r, "   ")["accepted"] is False
    assert r.remaining == MG


def test_distance_and_similarity_reported():
    r = deco.new_round("teams", target_index=0)
    res = deco.submit_guess(r, "ferrai")
    assert res["distance"] == 1
    assert res["similarity"] >= deco._MATCH_THRESHOLD


# ---- UI görünümü: blur + spoiler yok -----------------------------

def test_blur_decreases_with_wrong_guesses():
    r = deco.new_round("drivers", target_index=2)
    full = deco.public_state(r)["blur_px"]
    assert full == deco.blur_px(MG)
    deco.submit_guess(r, "prost")
    assert 0 < deco.public_state(r)["blur_px"] < full


def test_blur_zero_when_solved_or_failed():
    r = deco.new_round("teams", target_index=0)
    deco.submit_guess(r, "ferrari")
    assert deco.public_state(r)["blur_px"] == 0.0
    r2 = deco.new_round("teams", target_index=0)
    for _ in range(MG):
        deco.submit_guess(r2, "nope")
    assert deco.public_state(r2)["blur_px"] == 0.0


def test_public_state_hides_answer_until_over():
    r = deco.new_round("teams", target_index=2)     # McLaren
    assert deco.public_state(r)["answer"] is None
    deco.submit_guess(r, "williams")
    assert deco.public_state(r)["answer"] is None
    for _ in range(MG - 1):
        deco.submit_guess(r, "williams")
    view = deco.public_state(r)
    assert view["over"] and view["answer"] == "McLaren"


def test_hint_only_on_last_guess():
    r = deco.new_round("drivers", target_index=0)   # Hamilton
    for _ in range(deco._HINT_AFTER_MISSES - 1):
        deco.submit_guess(r, "x")
        assert deco.public_state(r)["hint"] is None
    deco.submit_guess(r, "x")                        # 3. yanlış → 1 hak kaldı
    assert deco.public_state(r)["hint"]
    assert r.remaining == MG - deco._HINT_AFTER_MISSES


def test_public_state_exposes_remaining_for_ui():
    r = deco.new_round("tracks", target_index=0)
    deco.submit_guess(r, "wrong")
    view = deco.public_state(r)
    assert view["remaining"] == MG - 1
    assert view["attempts_used"] == 1
    assert view["max_guesses"] == MG
    assert view["image"].startswith("https://") and "Monaco" in view["image"]


# ---- serileştirme ------------------------------------------------

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
    assert deco.score_round(r) == 50                # 1. deneme

    r2 = deco.new_round("teams", target_index=0)
    deco.submit_guess(r2, "x"); deco.submit_guess(r2, "x"); deco.submit_guess(r2, "ferrari")
    assert deco.score_round(r2) == 24               # 3. deneme (50 - 13*2)


def test_score_zero_while_playing_small_on_fail():
    r = deco.new_round("teams", target_index=0)
    assert deco.score_round(r) == 0
    for _ in range(MG):
        deco.submit_guess(r, "nope")
    assert deco.score_round(r) == 2


# ---- oturum (3 kategori) -----------------------------------------

def test_session_runs_three_categories_in_order():
    s = deco.new_session()
    assert s.order[0] == "teams" and s.current.category == "teams"
    deco.submit_guess(s.current, s.current.target.answer); s.advance()
    assert s.current.category == "drivers"
    deco.submit_guess(s.current, s.current.target.answer); s.advance()
    assert s.current.category == "tracks"
    deco.submit_guess(s.current, s.current.target.answer)
    assert s.done and s.swept


def test_session_advance_blocked_until_round_over():
    s = deco.new_session()
    deco.submit_guess(s.current, "wrong")
    assert s.advance().category == "teams" and s.index == 0


def test_session_total_score_includes_sweep_bonus():
    s = deco.new_session()
    for _ in range(3):
        deco.submit_guess(s.current, s.current.target.answer)
        s.advance()
    assert s.swept
    assert s.total_score == 50 * 3 + 30            # üç kez 1. deneme + sweep


def test_session_no_sweep_bonus_if_one_missed():
    s = deco.new_session()
    deco.submit_guess(s.current, s.current.target.answer); s.advance()
    for _ in range(MG):
        deco.submit_guess(s.current, "nope")
    s.advance()
    deco.submit_guess(s.current, s.current.target.answer)
    assert s.done and not s.swept
    assert s.total_score == 50 + 2 + 50


def test_session_roundtrips_and_seed_deterministic():
    s = deco.new_session(seed="2026-07-04")
    deco.submit_guess(s.current, "wrong")
    restored = deco.DecoderSession.from_dict(s.to_dict())
    assert restored.index == s.index
    assert restored.current.guesses == ["wrong"]
    assert deco.new_session(seed="2026-07-04").current.target_index == s.current.target_index


def test_session_public_state_shape():
    v = deco.new_session().public_state()
    assert v["step"] == "1/3"
    assert v["round"]["category"] == "teams"
    assert v["total_score"] == 0 and not v["done"]
