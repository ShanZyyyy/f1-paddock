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


# =====================================================================
# 6) ANTRENMAN LABI — yarış mühendisi analiz motorları (saf, ağsız)
# =====================================================================

def _long_run(driver, team, base, *, deg=0.05, n=10, start_lap=6, stint=2,
              compound="MEDIUM", sess_start=30):
    """Sentetik temiz uzun tur: yakıt (-0.033/tur) + aşınma (deg/tur)."""
    out = []
    for i in range(n):
        out.append({
            "driver": driver, "team": team,
            "lap_time_s": round(base - se.FUEL_EFFECT_PER_LAP * i + deg * i, 3),
            "lap_number": start_lap + i, "stint": stint, "compound": compound,
            "is_accurate": True, "is_pit_lap": False,
            "session_lap_index": sess_start + i,
        })
    return out


# ---- 6.1 FP yarış temposu ------------------------------------------

def test_race_pace_orders_teams_and_computes_gaps():
    laps = (_long_run("VER", "Red Bull", 92.0)
            + _long_run("PER", "Red Bull", 92.4)
            + _long_run("HAM", "Mercedes", 92.7)
            + _long_run("RUS", "Mercedes", 92.9))
    est = se.estimate_race_pace(laps)
    assert est.ok
    names = [d["driver"] for d in est.drivers]
    assert names[0] == "VER" and names.index("VER") < names.index("HAM")
    assert est.drivers[0]["gap_s"] == 0.0 and est.drivers[-1]["gap_s"] > 0.0
    teams = [t["team"] for t in est.teams]
    assert teams[0] == "Red Bull" and est.teams[0]["gap_s"] == 0.0
    assert est.teams[-1]["gap_s"] > 0.0


def test_race_pace_fuel_correction_removes_run_slope():
    # saf yakıt burnu (aşınma yok) → düzeltilmiş tempo ~ base
    laps = _long_run("VER", "Red Bull", 92.0, deg=0.0, n=12)
    est = se.estimate_race_pace(laps, track_evolution_s_per_lap=0.0)
    assert est.ok
    assert abs(est.drivers[0]["pace_s"] - 92.0) < 0.05


def test_race_pace_needs_min_run_length():
    laps = _long_run("VER", "Red Bull", 92.0, n=3)
    assert se.estimate_race_pace(laps, min_run_len=5).ok is False


def test_race_pace_ignores_pit_and_inaccurate_laps():
    laps = _long_run("VER", "Red Bull", 92.0, n=10)
    laps.append({"driver": "VER", "team": "Red Bull", "lap_time_s": 140.0,
                 "lap_number": 99, "stint": 2, "compound": "MEDIUM",
                 "is_accurate": True, "is_pit_lap": True})
    est = se.estimate_race_pace(laps)
    assert est.ok and est.drivers[0]["pace_s"] < 100.0


def test_race_pace_roster_fills_missing_teams():
    roster = ["Red Bull", "Mercedes", "Ferrari", "McLaren", "Alpine",
              "Racing Bulls", "Haas", "Williams", "Audi", "Aston Martin", "Cadillac"]
    laps = _long_run("VER", "Red Bull", 92.0) + _long_run("HAM", "Mercedes", 92.5)
    est = se.estimate_race_pace(laps, team_roster=roster)
    assert len(est.teams) == 11
    got = [t for t in est.teams if not t.get("no_data")]
    missing = [t for t in est.teams if t.get("no_data")]
    assert {t["team"] for t in got} == {"Red Bull", "Mercedes"}
    assert len(missing) == 9 and all(t["pace_s"] is None for t in missing)
    # kadrosuz çağrı eskisi gibi
    assert len(se.estimate_race_pace(laps).teams) == 2


def test_race_pace_roster_when_no_longruns():
    roster = ["Red Bull", "Mercedes", "Ferrari"]
    est = se.estimate_race_pace([], team_roster=roster)
    assert est.ok and len(est.teams) == 3 and all(t["no_data"] for t in est.teams)
    assert se.estimate_race_pace([]).ok is False    # kadrosuz: eski davranış


def test_race_pace_roster_estimates_from_short_runs():
    # uzun run YOK ama Ferrari'nin birkaç temiz kısa turu var → tahmin
    roster = ["Red Bull", "Mercedes", "Ferrari"]
    laps = _long_run("VER", "Red Bull", 92.0) + _long_run("HAM", "Mercedes", 92.4)
    for i in range(4):
        laps.append({"driver": "LEC", "team": "Ferrari", "lap_time_s": 90.6 + 0.1 * i,
                     "lap_number": 5 + i, "stint": 1, "compound": "SOFT",
                     "is_accurate": True, "is_pit_lap": False})
    est = se.estimate_race_pace(laps, team_roster=roster)
    fer = next(t for t in est.teams if t["team"] == "Ferrari")
    assert fer["no_data"] and fer["estimated"] and fer["pace_s"] is not None
    assert fer["gap_s"] is not None


# ---- 6.2 lastik aşınma tahmini -------------------------------------

def test_degradation_recovers_known_rate():
    # net eğim 0.09 → +yakıt 0.033 → saf ~0.123
    stint = [92.0 + 0.09 * i for i in range(16)]
    de = se.estimate_degradation(stint, compound="MEDIUM")
    assert de.ok and de.r2 > 0.98
    assert abs(de.deg_rate_s_per_lap - (0.09 + se.FUEL_EFFECT_PER_LAP)) < 0.02
    assert de.deg_rate_raw_s_per_lap < de.deg_rate_s_per_lap    # ham < saf (yakıt)


def test_degradation_hotter_track_increases_rate():
    stint = [92.0 + 0.08 * i for i in range(14)]
    cool = se.estimate_degradation(stint, compound="MEDIUM", surface_temp_c=32.0)
    hot = se.estimate_degradation(stint, compound="MEDIUM", surface_temp_c=52.0)
    assert hot.deg_rate_temp_adjusted > cool.deg_rate_temp_adjusted
    assert hot.projected_loss_10_laps_s > cool.projected_loss_10_laps_s


def test_degradation_short_stint_falls_back_to_model_prior():
    de = se.estimate_degradation([92.0, 92.1], compound="HARD")
    assert de.ok is False
    assert abs(de.deg_rate_s_per_lap - se.TYRES["HARD"].deg) < 1e-6


def test_degradation_never_negative_after_blend():
    # araç hızlanıyormuş gibi (fiziksel değil) — model 0 tabanına çeker
    stint = [92.0 - 0.2 * i for i in range(12)]
    de = se.estimate_degradation(stint, compound="MEDIUM")
    assert de.deg_rate_s_per_lap >= -0.05


def test_degradation_detects_dropoff_lap():
    # 10 tur nazik + 6 tur uçurum (11. turdan itibaren hızlanan kayıp)
    stint = [92.0 + 0.05 * i for i in range(10)] + [93.0 + 0.30 * i for i in range(6)]
    de = se.estimate_degradation(stint, compound="SOFT")
    assert de.dropoff_lap is not None and 9 <= de.dropoff_lap <= 13
    assert de.dropoff_loss_s > 0.1


def test_degradation_no_dropoff_on_linear_stint():
    stint = [92.0 + 0.06 * i for i in range(16)]
    de = se.estimate_degradation(stint, compound="MEDIUM")
    assert de.dropoff_lap is None


# ---- akıllı aşınma: TÜM hamurlar için zorunlu drop-off -------------

def test_compound_dropoffs_all_three_present_even_if_unmeasured():
    # yalnız MEDIUM ölçülmüş — SOFT/HARD yine de bir tur numarası döner
    deg = {"VER": {"compound": "MEDIUM", "dropoff_lap": 22},
           "HAM": {"compound": "MEDIUM", "dropoff_lap": 24}}
    out = se.estimate_all_compound_dropoffs(deg)
    assert set(out) == {"SOFT", "MEDIUM", "HARD"}
    assert out["MEDIUM"]["measured"] is True and out["MEDIUM"]["dropoff_lap"] == 23
    assert out["SOFT"]["measured"] is False and out["SOFT"]["dropoff_lap"] > 0
    assert out["HARD"]["measured"] is False and out["HARD"]["dropoff_lap"] > out["SOFT"]["dropoff_lap"]


def test_compound_dropoffs_scale_by_measured_ratio():
    # ölçülen MEDIUM modelden (26) belirgin ERKEN aşınıyor (18) → oran ~0.69
    # SOFT/HARD tahminleri de aynı oranda erkene çekilmeli (ham model değil)
    deg = {"VER": {"compound": "MEDIUM", "dropoff_lap": 18}}
    out = se.estimate_all_compound_dropoffs(deg)
    assert out["SOFT"]["dropoff_lap"] < se.TYRES["SOFT"].cliff
    assert out["HARD"]["dropoff_lap"] < se.TYRES["HARD"].cliff


def test_compound_dropoffs_pure_model_when_nothing_measured():
    out = se.estimate_all_compound_dropoffs({})
    assert all(not v["measured"] for v in out.values())
    for c in ("SOFT", "MEDIUM", "HARD"):
        assert out[c]["dropoff_lap"] == se.TYRES[c].cliff


# ---- 6.3 Vmax & DRS ------------------------------------------------

def _straight_telemetry(drs_gain=12.0):
    """İki uzun düzlük + aralarında yavaş virajlar. 2. düzlükte DRS açık."""
    s = []
    for i in range(600):
        d = i * 8.0
        on_straight_1 = 400 <= d <= 1600
        on_straight_2 = 2600 <= d <= 3800
        if on_straight_1:
            s.append({"distance": d, "speed": 300.0, "throttle": 100.0, "drs": 0})
        elif on_straight_2:
            s.append({"distance": d, "speed": 300.0 + drs_gain, "throttle": 100.0, "drs": 12})
        else:
            s.append({"distance": d, "speed": 120.0, "throttle": 15.0, "drs": 0})
    return s


def test_straights_detected_and_vmax():
    an = se.analyze_straights(_straight_telemetry())
    assert an.ok and len(an.straights) >= 2
    assert an.v_max_kmh >= 310
    assert an.straights[0]["length_m"] >= 260


def test_drs_gain_positive_when_open_faster():
    an = se.analyze_straights(_straight_telemetry(drs_gain=14.0))
    assert an.drs_available is True
    assert 10.0 <= an.drs_gain_kmh <= 18.0


def test_drs_unavailable_when_channel_missing():
    tel = [{"distance": i * 8.0,
            "speed": 300.0 if 400 <= i * 8.0 <= 1600 else 120.0,
            "throttle": 100.0 if 400 <= i * 8.0 <= 1600 else 15.0}
           for i in range(400)]
    an = se.analyze_straights(tel)
    assert an.ok and an.drs_available is False and an.drs_gain_kmh is None


def test_straights_empty_input_falls_back_to_typical_estimate():
    # boş/hata yerine açıkça işaretli tipik değer — arayüz asla çıplak "veri yok" görmez
    an = se.analyze_straights([])
    assert an.ok is True and an.estimated is True
    assert an.drs_gain_kmh == se._TYPICAL_STRAIGHT_GAIN_KMH
    assert an.source and "ortalama" in an.source


def test_straights_fallback_can_be_disabled():
    an = se.analyze_straights([], allow_typical_fallback=False)
    assert an.ok is False and an.estimated is False


def test_straights_real_data_is_not_marked_estimated():
    an = se.analyze_straights(_straight_telemetry())
    assert an.ok and an.estimated is False


# ---- 6.4 Vmin kritik virajlar ------------------------------------

def _corner_telemetry(vmins=(90.0, 70.0, 120.0), offset=0.0):
    """3 V-şekilli viraj, aralarda 300 km/s düzlük."""
    centers = [1500, 3000, 4500]
    s = []
    for i in range(700):
        d = i * 8.0
        sp = 300.0
        for c, vm in zip(centers, vmins):
            if abs(d - c) < 200:
                sp = min(sp, vm + (abs(d - c) / 200.0) * (300.0 - vm))
        s.append({"distance": d, "speed": round(sp + offset, 1)})
    return s


def test_critical_corners_finds_slowest_and_orders_by_distance():
    corners = se.critical_corners(_corner_telemetry(vmins=(90, 70, 130)), n_corners=3)
    assert len(corners) == 3
    assert [c.distance_m for c in corners] == sorted(c.distance_m for c in corners)
    slowest = min(corners, key=lambda c: c.v_min_kmh)
    assert abs(slowest.v_min_kmh - 70.0) < 8.0
    # en yavaş viraj en yüksek severity
    assert slowest.severity == max(c.severity for c in corners)


def test_critical_corners_dedupes_close_minima():
    # düz plato bir virajda birden çok minimum üretmemeli
    flat = [{"distance": i * 8.0, "speed": 300.0 if i < 100 or i > 140 else 95.0}
            for i in range(240)]
    corners = se.critical_corners(flat, n_corners=3)
    assert len(corners) == 1


def test_top_speed_compare_ranks_and_deltas():
    fast = _straight_telemetry(drs_gain=0.0)
    slow = [dict(s, speed=s["speed"] - 8.0) for s in fast]
    out = se.top_speed_compare({"VER": fast, "HAM": slow})
    assert out["v_max_by_driver"]["VER"] > out["v_max_by_driver"]["HAM"]
    assert out["delta_to_best"]["VER"] == 0.0
    assert abs(out["delta_to_best"]["HAM"] - 8.0) < 0.2
    assert se.top_speed_compare({})["v_max_by_driver"] == {}


# ---- Pist Hakimiyeti (Track Dominance) -----------------------------

def _zone_track(low, medium, high, *, offset=0.0):
    """Düşük/orta/yüksek üç ayrı hız bölümünden oluşan basit bir tur."""
    s, d = [], 0.0
    for v in (low, medium, high):
        for _ in range(25):
            s.append({"distance": d, "speed": v + offset})
            d += 10.0
    return s


def test_track_dominance_segments_into_three_zones():
    samples = {"VER": _zone_track(90, 180, 320), "HAM": _zone_track(88, 178, 318)}
    td = se.track_dominance(samples, {"VER": "Red Bull", "HAM": "Mercedes"})
    assert td.ok
    zone_types = {z["zone"] for z in td.zones}
    assert zone_types == {"low", "medium", "high"}
    assert set(td.by_team) == {"Red Bull", "Mercedes"}


def test_track_dominance_finds_zone_specific_advantage():
    # Red Bull SADECE düşük hızda hızlı, orta/yüksekte aynı — avantaj yalnız "low"da çıkmalı
    ver = _zone_track(100, 180, 320)                 # düşük hızda hızlı
    ham = _zone_track(85, 180, 320)                  # düşük hızda 15 km/s yavaş
    td = se.track_dominance({"VER": ver, "HAM": ham}, {"VER": "Red Bull", "HAM": "Mercedes"})
    assert td.ok and td.insights
    top = td.insights[0]
    assert top["team"] == "Red Bull" and top["zone"] == "low" and top["advantage_s"] > 0
    assert top["zone_tr"] == "Düşük Hız"


def test_track_dominance_needs_at_least_two_drivers():
    assert se.track_dominance({"VER": _zone_track(90, 180, 320)}, {}).ok is False
    assert se.track_dominance({}, {}).ok is False


def test_corner_vmin_compare_gives_delta_to_best():
    corners = se.critical_corners(_corner_telemetry(), n_corners=3)
    fast = _corner_telemetry()
    slow = _corner_telemetry(offset=-6.0)          # her yerde 6 km/s daha yavaş
    rows = se.corner_vmin_compare({"VER": fast, "HAM": slow}, corners)
    assert rows and all("VER" in r["v_min_by_driver"] for r in rows)
    for r in rows:
        assert r["delta_to_best"]["VER"] == 0.0
        assert r["delta_to_best"]["HAM"] >= 4.0


# ---- 6.5 gaz/fren karakteristiği --------------------------------

def _lap_trace(*, ramp_m=40.0, brake_len_m=90.0, coast_m=0.0):
    """Bir tur: düzlük tam gaz → fren → viraj → kademeli gaz açılışı."""
    s = []
    d = 0.0
    # tam gaz düzlük
    while d < 1200:
        s.append({"distance": d, "speed": 300.0, "throttle": 100.0, "brake": 0}); d += 10
    # fren bölgesi
    v = 300.0
    for _ in range(int(brake_len_m / 10)):
        v -= (300.0 - 90.0) / (brake_len_m / 10)
        s.append({"distance": d, "speed": max(90.0, v), "throttle": 0.0, "brake": 1}); d += 10
    # serbest (coast) — istenirse
    for _ in range(int(coast_m / 10)):
        s.append({"distance": d, "speed": 90.0, "throttle": 0.0, "brake": 0}); d += 10
    # viraj
    for _ in range(6):
        s.append({"distance": d, "speed": 90.0, "throttle": 5.0, "brake": 0}); d += 10
    # kademeli gaz açılışı
    t = 0.0
    while t < 100.0:
        t += 100.0 / (ramp_m / 10)
        s.append({"distance": d, "speed": 90.0 + t, "throttle": min(100.0, t), "brake": 0}); d += 10
    # tekrar düzlük
    for _ in range(40):
        s.append({"distance": d, "speed": 290.0, "throttle": 100.0, "brake": 0}); d += 10
    return s


def test_driving_style_profile_shapes():
    ds = se.driving_style(_lap_trace())
    assert ds.ok
    assert 0.0 <= ds.full_throttle_frac <= 1.0
    assert 0.0 <= ds.throttle_aggression <= 1.0 and 0.0 <= ds.brake_aggression <= 1.0
    assert ds.label in ("agresif", "yumuşak", "dengeli")


def test_driving_style_aggressive_vs_smooth():
    sharp = se.driving_style(_lap_trace(ramp_m=20.0, brake_len_m=60.0))
    smooth = se.driving_style(_lap_trace(ramp_m=140.0, brake_len_m=200.0))
    assert sharp.throttle_aggression > smooth.throttle_aggression
    assert sharp.brake_aggression >= smooth.brake_aggression


def test_driving_style_coasting_detected():
    none = se.driving_style(_lap_trace(coast_m=0.0))
    lots = se.driving_style(_lap_trace(coast_m=300.0))
    assert lots.coast_frac > none.coast_frac


def test_driving_style_empty_is_safe():
    assert se.driving_style([]).ok is False


def test_braking_zones_profile():
    lap = _lap_trace(brake_len_m=90.0)
    zones = se.braking_zones(lap)
    assert zones and all(z["delta_kmh"] > 20 for z in zones)
    z = zones[0]
    for k in ("start_m", "end_m", "length_m", "entry_speed_kmh", "apex_speed_kmh",
              "delta_kmh", "peak_decel", "trail_brake", "brake_point_m"):
        assert k in z
    assert 0.0 <= z["trail_brake"] <= 1.0


def test_driving_style_per_corner_deep_metrics():
    sam = _corner_telemetry(vmins=(90, 70, 130))
    # köşe telemetrisine gaz/fren ekle
    for s in sam:
        fast = s["speed"] > 250
        s["throttle"] = 100.0 if fast else 0.0
        s["brake"] = 0 if fast else 1
    corners = se.critical_corners(sam, n_corners=3)
    ds = se.driving_style(sam, corners=corners)
    assert ds.ok
    assert ds.brake_zones >= 1 and ds.mean_brake_len_m > 0
    assert 0.0 <= ds.brake_point_consistency <= 1.0
    assert 0.0 <= ds.lift_and_coast_frac <= 1.0
    matched = [c for c in ds.per_corner if c.get("matched")]
    assert matched, "en az bir viraj fren bölgesiyle eşleşmeli"
    for c in matched:
        assert "apex_speed_kmh" in c and "trail_brake" in c and "brake_aggression" in c
    # corners verilmezse per_corner boş, geriye dönük uyum
    assert se.driving_style(sam).per_corner == []


# ---- 6.6 FastF1 katmanı — ortam yoksa nazikçe döner --------------

def test_analyze_practice_without_fastf1_returns_reason():
    if se._has_fastf1():
        return                       # ortamda FastF1 varsa bu test atlanır
    out = se.analyze_practice(2024, "Bahrain", session_name="FP2")
    assert out["ok"] is False and "reason" in out


def test_engine_module_import_is_stdlib_only():
    # strategy_engine tek başına import edilince fastf1/pandas YÜKLENMEZ.
    # (Tam suitte başka dosyalar fastf1'i çekmiş olabilir → ayrı süreçte doğrula.)
    import subprocess
    import sys
    code = ("import sys, core.strategy_engine;"
            "assert 'fastf1' not in sys.modules, 'fastf1 leaked';"
            "assert 'pandas' not in sys.modules, 'pandas leaked';"
            "print('ok')")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "ok" in res.stdout
