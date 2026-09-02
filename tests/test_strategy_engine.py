# -*- coding: utf-8 -*-
"""core/strategy_engine.py — saf strateji motoru. Ağ/pandas gerektirmez."""

from core import strategy_engine as se


# ---- lastik modeli ------------------------------------------------------

def test_pace_loss_monotonic_before_cliff():
    m = se.TYRES["MEDIUM"]
    losses = [m.pace_loss(a) for a in range(1, m.cliff + 1)]
    assert losses == sorted(losses)
    # linear bölgede sabit eğim
    step = losses[5] - losses[4]
    assert abs(step - m.deg) < 1e-9


def test_cliff_accelerates_loss():
    m = se.TYRES["SOFT"]
    before = m.pace_loss(m.cliff) - m.pace_loss(m.cliff - 1)
    after = m.pace_loss(m.cliff + 5) - m.pace_loss(m.cliff + 4)
    assert after > before * 1.5


def test_soft_faster_than_hard_when_fresh():
    assert se.tyre_pace_loss("SOFT", 1) < se.tyre_pace_loss("HARD", 1)


def test_hard_outlasts_soft_over_long_stint():
    # 30 turluk stint: HARD toplamda daha az kaybettirir
    assert se.TYRES["HARD"].stint_cost(30) < se.TYRES["SOFT"].stint_cost(30)


def test_compound_alias_and_fallback():
    assert se.tyre_model("C4").name == "SOFT"
    assert se.tyre_model("banana").name == "MEDIUM"


def test_degradation_curve_length_and_type():
    curve = se.degradation_curve("MEDIUM", 12)
    assert len(curve) == 12 and all(isinstance(x, float) for x in curve)


# ---- yakıt + pit ------------------------------------------------------

def test_fuel_gain_improves_through_race():
    early = se.fuel_pace_gain(2, 60)
    late = se.fuel_pace_gain(58, 60)
    assert early > late            # başta ceza, sonda kazanç


def test_pit_loss_discounted_under_sc():
    base = 22.0
    assert se.pit_time_loss(base) == 22.0
    assert se.pit_time_loss(base, under_sc=True) < se.pit_time_loss(base, under_vsc=True) < base


# ---- undercut / overcut ---------------------------------------------

def test_undercut_probability_bounds():
    p = se.undercut_probability(gap_ahead_s=1.0, fresh_tyre_advantage_s=1.5)
    assert 0.0 <= p <= 1.0


def test_undercut_more_likely_with_bigger_tyre_delta():
    lo = se.undercut_probability(gap_ahead_s=2.0, fresh_tyre_advantage_s=0.4, laps_until_rival_pits=2)
    hi = se.undercut_probability(gap_ahead_s=2.0, fresh_tyre_advantage_s=1.8, laps_until_rival_pits=2)
    assert hi > lo


def test_undercut_traffic_hurts():
    clean = se.undercut_probability(gap_ahead_s=1.5, fresh_tyre_advantage_s=1.4, in_traffic=False)
    stuck = se.undercut_probability(gap_ahead_s=1.5, fresh_tyre_advantage_s=1.4, in_traffic=True)
    assert stuck < clean


def test_undercut_harder_with_bigger_gap():
    near = se.undercut_probability(gap_ahead_s=0.5, fresh_tyre_advantage_s=1.2)
    far = se.undercut_probability(gap_ahead_s=6.0, fresh_tyre_advantage_s=1.2)
    assert near > far


def test_overcut_needs_rival_degrading():
    weak = se.overcut_probability(gap_ahead_s=1.0, rival_deg_now_s=0.2, my_tyre_life_left=6)
    strong = se.overcut_probability(gap_ahead_s=1.0, rival_deg_now_s=1.4, my_tyre_life_left=6)
    assert strong > weak


# ---- safety car -------------------------------------------------------

def test_sc_sample_deterministic_with_seed():
    m = se.SafetyCarModel(total_laps=50)
    assert m.sample(seed=42) == m.sample(seed=42)


def test_sc_windows_non_overlapping_and_ordered():
    m = se.SafetyCarModel(total_laps=70, sc_lap_hazard=0.05, vsc_lap_hazard=0.05)
    out = m.sample(seed=7)
    allw = sorted(out["sc"] + out["vsc"])
    for (a1, b1), (a2, b2) in zip(allw, allw[1:]):
        assert b1 < a2                       # ayrık
        assert a2 - b1 >= m.min_gap_laps


def test_sc_respects_late_race_cutoff():
    m = se.SafetyCarModel(total_laps=50, sc_lap_hazard=0.4)
    for s in range(30):
        out = m.sample(seed=s)
        for a, _b in out["sc"] + out["vsc"]:
            assert a <= 50 * m.late_race_cutoff_frac


def test_wet_race_has_more_safety_cars():
    dry = se.SafetyCarModel(total_laps=60).probability_at_least_one(kind="any", trials=1200, seed=1)
    wet = se.SafetyCarModel(total_laps=60, wet=True).probability_at_least_one(kind="any", trials=1200, seed=1)
    assert wet > dry


def test_sc_probability_in_realistic_range():
    # gerçek F1: yarışların ~%40-65'inde en az bir SC/VSC
    p = se.SafetyCarModel(total_laps=57).probability_at_least_one(kind="any", trials=2500, seed=3)
    assert 0.30 <= p <= 0.80


# ---- stint planı roll-up -------------------------------------------

def test_simulate_stint_plan_counts_stops_and_stints():
    plan = se.StintPlan("SOFT", [(15, "MEDIUM"), (35, "HARD")])
    r = se.simulate_stint_plan(plan, total_laps=52, base_lap_s=88.0, pit_loss_s=20.0)
    assert r.pit_laps == [15, 35]
    assert r.stint_lengths == [15, 20, 17]
    assert sum(r.stint_lengths) == 52


def test_one_stop_vs_two_stop_tradeoff():
    one = se.StintPlan("MEDIUM", [(28, "HARD")])
    two = se.StintPlan("SOFT", [(18, "MEDIUM"), (36, "MEDIUM")])
    table = se.compare_plans({"1-stop": one, "2-stop": two},
                             total_laps=55, base_lap_s=90.0, pit_loss_s=22.0)
    names = [row[0] for row in table]
    assert set(names) == {"1-stop", "2-stop"}
    assert table[0][2] == 0.0 and table[1][2] > 0.0   # fark sıralı


def test_sc_makes_pit_cheaper_in_sim():
    plan = se.StintPlan("MEDIUM", [(20, "HARD")])
    clean = se.simulate_stint_plan(plan, total_laps=45, base_lap_s=90.0, pit_loss_s=24.0)
    lucky = se.simulate_stint_plan(plan, total_laps=45, base_lap_s=90.0, pit_loss_s=24.0,
                                   sc_windows=[(19, 22)])
    # SC turu daha yavaş tur zamanı ama pit çok daha ucuz — burada pit avantajı test edilmez;
    # sadece SC penceresinin sonuca yansıdığını doğrula
    assert lucky.sc_windows == [(19, 22)]
    assert lucky.per_lap[19] > clean.per_lap[19]      # 20. tur (index 19) SC ile yavaş


def test_sc_model_feeds_simulation():
    plan = se.StintPlan("MEDIUM", [(22, "HARD")])
    m = se.SafetyCarModel(total_laps=50, sc_lap_hazard=0.06, vsc_lap_hazard=0.0)
    r = se.simulate_stint_plan(plan, total_laps=50, base_lap_s=91.0, sc_model=m, seed=11)
    # seed sabit → tekrar üretilebilir
    r2 = se.simulate_stint_plan(plan, total_laps=50, base_lap_s=91.0, sc_model=m, seed=11)
    assert r.total_time_s == r2.total_time_s
