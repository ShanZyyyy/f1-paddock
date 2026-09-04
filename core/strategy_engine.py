# -*- coding: utf-8 -*-
"""Strateji Duvarı — çekirdek olay motoru (event engine).

Saf matematiksel model: lastik aşınma eğrisi, yakıt düzeltmesi, pit kaybı,
undercut/overcut olasılığı ve Güvenlik Aracı (SC / VSC) tetikleyicileri.

Bu modül ``streamlit_app.py`` içindeki veri-güdümlü ``_strat_simulate_v67``
(gerçek yarışı yeniden oynatır) fonksiyonunu **bozmaz**; onu tamamlar. Buradaki
fonksiyonlar:

* Analiz matematiği FastF1/pandas GEREKTİRMEZ — yalnızca stdlib (``math``,
  ``random``). Böylece "farazi" strateji senaryoları (kullanıcının kendi pit
  planı) veriye bağlanmadan çalıştırılabilir ve saf birim testi yazılabilir.
* 6. bölüm ("Antrenman Labı") FastF1'den ÇIKARILMIŞ düz Python yapılarıyla
  (list/dict) çalışan saf analiz motorlarıdır. FastF1 yüklemesi yalnızca
  ``load_practice_session`` / ``analyze_practice`` içinde **tembel** (lazy)
  import edilir; modülü import etmek hâlâ stdlib-only ve ağsızdır.
* Sabitler ``streamlit_app._STRAT_COMPOUND_DEFAULT`` ile aynı ailedendir
  (offset sn, linear deg sn/tur, cliff turu).

Kullanım::

    from core.strategy_engine import (
        TYRES, tyre_pace_loss, degradation_curve,
        undercut_probability, SafetyCarModel, simulate_stint_plan,
        estimate_race_pace, estimate_degradation, analyze_straights,
        critical_corners, driving_style, analyze_practice,
    )
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "TyreModel",
    "TYRES",
    "tyre_pace_loss",
    "degradation_curve",
    "fuel_pace_gain",
    "pit_time_loss",
    "undercut_probability",
    "overcut_probability",
    "SafetyCarModel",
    "StintPlan",
    "simulate_stint_plan",
    "compare_plans",
    # 6) Antrenman Labı — yarış mühendisi analiz motorları
    "RacePaceEstimate",
    "DegradationEstimate",
    "StraightAnalysis",
    "CornerSpeed",
    "DrivingStyle",
    "estimate_race_pace",
    "estimate_degradation",
    "analyze_straights",
    "critical_corners",
    "corner_vmin_compare",
    "driving_style",
    "load_practice_session",
    "extract_practice_laps",
    "extract_lap_samples",
    "analyze_practice",
]

# =========================================================================
# 1) LASTİK MODELİ
# =========================================================================


@dataclass(frozen=True)
class TyreModel:
    """Bir lastik hamurunun pace modeli.

    ``offset``     : taze lastikte, MEDIUM referansına göre tur farkı (sn).
                     negatif = daha hızlı.
    ``deg``        : linear aşınma — her tur eklenen saniye (sn/tur).
    ``cliff``      : bu lastik yaşından SONRA "uçurum" başlar (üstel kayıp).
    ``cliff_grow`` : uçurum sonrası fazladan kayıp katsayısı.
    """

    name: str
    offset: float
    deg: float
    cliff: int
    cliff_grow: float = 0.04

    def pace_loss(self, age: int) -> float:
        """``age`` turluk lastiğin taze-referansa göre tur-zaman kaybı (sn).

        age=1 → yeni takılmış lastik (ilk tam tur).
        """
        age = max(1, int(age))
        loss = self.offset + self.deg * age
        if age > self.cliff:
            over = age - self.cliff
            loss += 0.12 * over + self.cliff_grow * over ** 1.5
        return loss

    def stint_cost(self, stint_len: int) -> float:
        """Tüm stint boyunca biriken toplam pace kaybı (sn)."""
        return sum(self.pace_loss(a) for a in range(1, max(1, int(stint_len)) + 1))

    def is_in_cliff(self, age: int) -> bool:
        return int(age) > self.cliff


# streamlit_app._STRAT_COMPOUND_DEFAULT ile aynı sayılar (offset, deg, cliff)
TYRES: Dict[str, TyreModel] = {
    "SOFT": TyreModel("SOFT", -0.55, 0.085, 15),
    "MEDIUM": TyreModel("MEDIUM", 0.0, 0.050, 26),
    "HARD": TyreModel("HARD", 0.45, 0.032, 38),
    "INTERMEDIATE": TyreModel("INTERMEDIATE", 2.5, 0.060, 24),
    "WET": TyreModel("WET", 6.0, 0.050, 30),
}

_COMPOUND_ALIAS = {
    "HYPERSOFT": "SOFT", "ULTRASOFT": "SOFT", "SUPERSOFT": "SOFT", "C1": "HARD",
    "C2": "HARD", "C3": "MEDIUM", "C4": "SOFT", "C5": "SOFT", "SUPERHARD": "HARD",
    "INTER": "INTERMEDIATE", "S": "SOFT", "M": "MEDIUM", "H": "HARD",
}


def tyre_model(compound: str) -> TyreModel:
    key = str(compound or "MEDIUM").strip().upper()
    key = _COMPOUND_ALIAS.get(key, key)
    return TYRES.get(key, TYRES["MEDIUM"])


def tyre_pace_loss(compound: str, age: int) -> float:
    """Verilen hamur + lastik yaşı için tur-zaman kaybı (sn)."""
    return tyre_model(compound).pace_loss(age)


def degradation_curve(compound: str, stint_len: int) -> List[float]:
    """1..stint_len yaşları için pace-kayıp eğrisi (grafik/HUD için hazır liste)."""
    model = tyre_model(compound)
    return [round(model.pace_loss(a), 3) for a in range(1, max(1, int(stint_len)) + 1)]


# =========================================================================
# 2) YAKIT + PİT KAYBI
# =========================================================================

# Yakıt yükü azaldıkça araç hızlanır. streamlit_app: fuel_effect = -0.033 sn/tur.
FUEL_EFFECT_PER_LAP = 0.033


def fuel_pace_gain(lap: int, total_laps: int) -> float:
    """``lap`` turunda yakıt hafiflemesinden gelen kazanç (sn, negatif = hızlı).

    Yarış başında (dolu depo) 0'a yakın maksimum ceza; sonda ~0.
    """
    laps_run = max(0, int(lap) - 1)
    return -FUEL_EFFECT_PER_LAP * laps_run + FUEL_EFFECT_PER_LAP * max(1, int(total_laps)) / 2.0


def pit_time_loss(base_pit_loss_s: float, *, under_sc: bool = False, under_vsc: bool = False) -> float:
    """Pit stop net zaman kaybı; SC/VSC altında ucuzlar.

    Katsayılar streamlit_app._strat_simulate_v67 ile aynı: SC 0.45×, VSC 0.65×.
    """
    loss = max(0.0, float(base_pit_loss_s))
    if under_sc:
        return loss * 0.45
    if under_vsc:
        return loss * 0.65
    return loss


# =========================================================================
# 3) UNDERCUT / OVERCUT OLASILIĞI
# =========================================================================


def _logistic(x: float, *, k: float = 1.0, x0: float = 0.0) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-k * (x - x0)))
    except OverflowError:
        return 0.0 if x < x0 else 1.0


def undercut_probability(
    *,
    gap_ahead_s: float,
    fresh_tyre_advantage_s: float,
    laps_until_rival_pits: int = 1,
    pit_loss_s: float = 21.0,
    in_traffic: bool = False,
    rival_tyre_age_gap: float = 0.0,
) -> float:
    """Öndeki rakibe undercut'ın tutma olasılığı (0..1).

    Parametreler
    ------------
    gap_ahead_s
        Rakip şu an kaç saniye önde (pozitif = önde).
    fresh_tyre_advantage_s
        Taze lastiğin rakibin eskimiş lastiğine karşı TUR BAŞINA kazancı (sn).
        Tipik: 0.8–2.0 (out-lap sonrası ilk birkaç tur).
    laps_until_rival_pits
        Rakip kaç tur sonra pite girecek (undercut'ın çalışması için kaç tur
        taze lastik avantajı biriktirebileceğin).
    pit_loss_s
        Pit yolundan net kayıp.
    in_traffic
        Out-lap'te trafiğe takılırsan avantaj erir.
    rival_tyre_age_gap
        Rakibin lastiği seninkinden kaç tur daha eski (pozitif = rakip dezavantajlı).

    Model
    -----
    Net kazanç = biriken taze-lastik avantajı − pit'te kaybettiğin mesafe farkı.
    Sonra lojistik ile olasılığa çevrilir.
    """
    windows = max(1, int(laps_until_rival_pits))
    # out-lap tam avantaj vermez (~%55), sonraki turlar tam
    effective_laps = 0.55 + max(0, windows - 1)
    gained = fresh_tyre_advantage_s * effective_laps
    gained += 0.05 * max(0.0, rival_tyre_age_gap)  # rakip zaten eskiyse ekstra
    if in_traffic:
        gained *= 0.45

    # undercut yapan araç pite girdiğinde rakibin ~ (pit_loss - gap) kadar önüne
    # düşmesi gerekir; gap ne kadar küçükse o kadar kolay.
    needed = max(0.0, gap_ahead_s)
    net = gained - needed

    # net = 0 → ~%35 (belirsiz), net = +2 sn → ~%80
    return round(_logistic(net, k=0.9, x0=0.6), 3)


def overcut_probability(
    *,
    gap_ahead_s: float,
    rival_deg_now_s: float,
    my_tyre_life_left: int,
    laps_extended: int = 2,
    in_clean_air: bool = True,
) -> float:
    """Rakip önce pite girdikten sonra pistte kalıp (overcut) öne geçme olasılığı.

    rival_deg_now_s
        Rakibin ŞU ANDAKİ lastiğinin tur başına aşınma cezası (out-lap dâhil
        soğuk lastik + eskimişlik). Genelde 0.5–1.5.
    my_tyre_life_left
        Kendi lastiğinde kalan sağlıklı tur sayısı; negatif/0 ise overcut riskli.
    laps_extended
        Rakipten kaç tur fazla pistte kalıyorsun.
    """
    ext = max(1, int(laps_extended))
    my_penalty = 0.25 if my_tyre_life_left >= ext else (0.9 + 0.4 * (ext - max(0, my_tyre_life_left)))
    gained = (rival_deg_now_s - my_penalty) * ext
    if not in_clean_air:
        gained *= 0.6
    net = gained - max(0.0, gap_ahead_s)
    return round(_logistic(net, k=0.85, x0=0.7), 3)


# =========================================================================
# 4) GÜVENLİK ARACI (SC / VSC) TETİKLEYİCİLERİ
# =========================================================================


@dataclass
class SafetyCarModel:
    """Olasılıksal Güvenlik Aracı üretici.

    Gerçek F1 kalibrasyonu (2014–2024 kuru yarış ortalaması):
      * Yarış başına beklenen tam SC ~ 0.55, VSC ~ 0.40.
      * İlk turlarda kaza riski belirgin yüksek (start kalabalığı).
      * Islak yarışta oran ~2×.

    Her tur bağımsız bir Bernoulli denemesi; hazard = taban + start-artışı.
    """

    total_laps: int
    sc_lap_hazard: float = 0.011           # tam SC / tur (düz)
    vsc_lap_hazard: float = 0.011          # VSC / tur (düz)
    first_lap_multiplier: float = 6.0      # 1. tur start kazası
    early_race_laps: int = 8               # ilk N tur yüksek risk penceresi
    early_race_multiplier: float = 1.8
    wet: bool = False
    sc_duration: Tuple[int, int] = (3, 5)  # tam SC kaç tur sürer (min,max)
    vsc_duration: Tuple[int, int] = (1, 3)
    min_gap_laps: int = 6                  # iki olay arası en az bu kadar tur
    late_race_cutoff_frac: float = 0.94    # son %6'da yeni SC üretme (gerçekçi değil)

    def _hazard(self, lap: int, base: float) -> float:
        h = base
        if lap == 1:
            h *= self.first_lap_multiplier
        elif lap <= self.early_race_laps:
            h *= self.early_race_multiplier
        if self.wet:
            h *= 2.0
        return min(0.9, h)

    def sample(self, *, seed: Optional[int] = None) -> Dict[str, List[Tuple[int, int]]]:
        """Bir yarış için SC ve VSC pencerelerini üret.

        Dönen: ``{"sc": [(start_lap, end_lap), ...], "vsc": [...]}`` — birbiriyle
        çakışmayan, ``min_gap_laps`` ayrık, artan sırada.
        """
        rng = random.Random(seed)
        total = max(1, int(self.total_laps))
        cutoff = int(total * self.late_race_cutoff_frac)
        blocked_until = 0
        sc_out: List[Tuple[int, int]] = []
        vsc_out: List[Tuple[int, int]] = []

        lap = 1
        while lap <= cutoff:
            if lap <= blocked_until:
                lap += 1
                continue
            h_sc = self._hazard(lap, self.sc_lap_hazard)
            h_vsc = self._hazard(lap, self.vsc_lap_hazard)
            roll = rng.random()
            if roll < h_sc:
                dur = rng.randint(*self.sc_duration)
                end = min(total, lap + dur - 1)
                sc_out.append((lap, end))
                blocked_until = end + self.min_gap_laps
                lap = end + 1
                continue
            if roll < h_sc + h_vsc:
                dur = rng.randint(*self.vsc_duration)
                end = min(total, lap + dur - 1)
                vsc_out.append((lap, end))
                blocked_until = end + self.min_gap_laps
                lap = end + 1
                continue
            lap += 1

        return {"sc": sc_out, "vsc": vsc_out}

    def probability_at_least_one(self, *, kind: str = "sc", trials: int = 4000,
                                 seed: Optional[int] = 0) -> float:
        """Monte-Carlo: en az bir (SC | VSC | herhangi) olay olasılığı."""
        rng_seed = seed
        hit = 0
        for i in range(max(1, trials)):
            out = self.sample(seed=None if rng_seed is None else rng_seed + i)
            if kind == "sc" and out["sc"]:
                hit += 1
            elif kind == "vsc" and out["vsc"]:
                hit += 1
            elif kind == "any" and (out["sc"] or out["vsc"]):
                hit += 1
        return round(hit / max(1, trials), 3)

    def expected_count(self, *, kind: str = "sc", trials: int = 3000,
                       seed: Optional[int] = 0) -> float:
        total = 0
        for i in range(max(1, trials)):
            out = self.sample(seed=None if seed is None else seed + i)
            if kind == "sc":
                total += len(out["sc"])
            elif kind == "vsc":
                total += len(out["vsc"])
            else:
                total += len(out["sc"]) + len(out["vsc"])
        return round(total / max(1, trials), 3)


def in_window(lap: int, windows: Sequence[Tuple[int, int]]) -> bool:
    return any(a <= lap <= b for a, b in (windows or ()))


# =========================================================================
# 5) FARAZİ STINT PLANI — HEPSİNİ BİRLEŞTİREN SAF ROLL-UP
# =========================================================================


@dataclass
class StintPlan:
    """Kullanıcının pit planı: başlangıç hamuru + (tur, yeni hamur) durakları."""

    start_compound: str
    stops: List[Tuple[int, str]] = field(default_factory=list)  # [(lap, compound), ...]

    def compound_at(self, lap: int) -> Tuple[str, int]:
        """``lap`` turundaki (hamur, lastik_yaşı)."""
        comp = self.start_compound
        since = 0
        for stop_lap, stop_comp in sorted(self.stops):
            if stop_lap <= lap:
                comp, since = stop_comp, stop_lap
        return comp, max(1, lap - since)


@dataclass
class RaceResult:
    total_time_s: float
    per_lap: List[float]
    pit_laps: List[int]
    sc_windows: List[Tuple[int, int]]
    vsc_windows: List[Tuple[int, int]]
    stint_lengths: List[int]


def simulate_stint_plan(
    plan: StintPlan,
    *,
    total_laps: int,
    base_lap_s: float,
    pit_loss_s: float = 21.0,
    sc_windows: Optional[Sequence[Tuple[int, int]]] = None,
    vsc_windows: Optional[Sequence[Tuple[int, int]]] = None,
    sc_model: Optional[SafetyCarModel] = None,
    seed: Optional[int] = None,
) -> RaceResult:
    """Bir stint planını tur-tur simüle et; toplam yarış süresini döndür.

    SC/VSC pencereleri ya doğrudan verilir (gerçek yarış) ya da ``sc_model``
    ile örneklenir (farazi). Hiçbiri yoksa SC'siz temiz yarış.
    """
    total_laps = max(1, int(total_laps))
    if sc_windows is None and vsc_windows is None and sc_model is not None:
        drawn = sc_model.sample(seed=seed)
        sc_windows, vsc_windows = drawn["sc"], drawn["vsc"]
    sc_windows = list(sc_windows or [])
    vsc_windows = list(vsc_windows or [])

    stop_by_lap = {int(l): str(c).upper() for l, c in plan.stops}
    per_lap: List[float] = []
    pit_laps: List[int] = []
    stint_lengths: List[int] = []
    cur_stint = 0

    for lap in range(1, total_laps + 1):
        comp, age = plan.compound_at(lap)
        model = tyre_model(comp)
        cur_stint += 1

        under_sc = in_window(lap, sc_windows)
        under_vsc = in_window(lap, vsc_windows)

        if under_sc:
            lt = base_lap_s * 1.45
        elif under_vsc:
            lt = base_lap_s * 1.28
        else:
            lt = base_lap_s + model.pace_loss(age) + fuel_pace_gain(lap, total_laps)

        if lap in stop_by_lap:
            lt += pit_time_loss(pit_loss_s, under_sc=under_sc, under_vsc=under_vsc)
            pit_laps.append(lap)
            stint_lengths.append(cur_stint)
            cur_stint = 0

        per_lap.append(lt)

    stint_lengths.append(cur_stint)

    return RaceResult(
        total_time_s=round(sum(per_lap), 3),
        per_lap=[round(x, 3) for x in per_lap],
        pit_laps=pit_laps,
        sc_windows=sc_windows,
        vsc_windows=vsc_windows,
        stint_lengths=stint_lengths,
    )


def compare_plans(
    plans: Dict[str, StintPlan],
    *,
    total_laps: int,
    base_lap_s: float,
    pit_loss_s: float = 21.0,
    sc_windows: Optional[Sequence[Tuple[int, int]]] = None,
    vsc_windows: Optional[Sequence[Tuple[int, int]]] = None,
) -> List[Tuple[str, float, float]]:
    """Birden çok planı aynı koşullarda çalıştır; en hızlıya göre sırala.

    Dönen: ``[(ad, toplam_sn, en_iyiye_fark_sn), ...]`` artan sürede.
    """
    scored = []
    for name, plan in plans.items():
        res = simulate_stint_plan(
            plan, total_laps=total_laps, base_lap_s=base_lap_s, pit_loss_s=pit_loss_s,
            sc_windows=sc_windows, vsc_windows=vsc_windows,
        )
        scored.append((name, res.total_time_s))
    scored.sort(key=lambda x: x[1])
    best = scored[0][1] if scored else 0.0
    return [(name, t, round(t - best, 3)) for name, t in scored]


# =========================================================================
# 6) ANTRENMAN LABI — YARIŞ MÜHENDİSİ ANALİZ MOTORLARI
# =========================================================================
# Girdi: FastF1'den ÇIKARILMIŞ düz Python yapıları.
#
#   Tur kaydı (session.laps'ten):
#     {"driver","team","lap_time_s","lap_number","stint","compound",
#      "tyre_life","is_accurate","is_pit_lap","session_lap_index"?}
#   Telemetri örneği (lap.get_telemetry()'den):
#     {"distance","speed","throttle","brake","drs"?,"x"?,"y"?}
#
# Bütün motorlar saf: pandas/FastF1 yok, boş/eksik girdide patlamaz (ok=False).

_DRS_ON_CODES = (10, 12, 14)          # FastF1 DRS kanalı: açık (hızlar km/s varsayılır)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _mean(xs: Sequence[float]) -> float:
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: Sequence[float]) -> float:
    xs = sorted(float(x) for x in xs if x is not None)
    if not xs:
        return 0.0
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def _trimmed_mean(xs: Sequence[float], trim: float = 0.15) -> float:
    xs = sorted(float(x) for x in xs if x is not None)
    if not xs:
        return 0.0
    k = int(len(xs) * _clamp(trim, 0.0, 0.45))
    core = xs[k:len(xs) - k] or xs
    return sum(core) / len(core)


def _linfit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Basit en-küçük-kareler doğru uydurma → (eğim, kesişim, r2)."""
    pts = [(float(a), float(b)) for a, b in zip(xs, ys) if a is not None and b is not None]
    n = len(pts)
    if n < 2:
        return (0.0, ys[0] if ys else 0.0, 0.0)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return (0.0, sy / n, 0.0)
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    mean_y = sy / n
    ss_tot = sum((p[1] - mean_y) ** 2 for p in pts)
    ss_res = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in pts)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return (slope, intercept, _clamp(r2, 0.0, 1.0))


def _norm_compound(c: str) -> str:
    key = str(c or "MEDIUM").strip().upper()
    return _COMPOUND_ALIAS.get(key, key)


# -------------------------------------------------------------------------
# 6.1  FP YARIŞ TEMPOSU TAHMİNİ  (race pace simulation)
# -------------------------------------------------------------------------
@dataclass
class RacePaceEstimate:
    ok: bool
    reference_s: float                       # en hızlı normalize edilmiş tempo
    drivers: List[dict] = field(default_factory=list)   # [{driver,team,pace_s,gap_s,laps,compound}]
    teams: List[dict] = field(default_factory=list)     # [{team,pace_s,gap_s,drivers}]
    method: str = ""


def _driver_runs(laps: Sequence[dict], *, min_run_len: int) -> Dict[str, List[List[dict]]]:
    """Pilot → stint → temiz ardışık uzun tur blokları."""
    by_drv: Dict[str, Dict[int, List[dict]]] = {}
    for lp in laps:
        d = str(lp.get("driver") or "").upper()
        if not d:
            continue
        t = lp.get("lap_time_s")
        if t is None or float(t) <= 0:
            continue
        if lp.get("is_pit_lap"):
            continue
        if lp.get("is_accurate") is False:
            continue
        st = int(lp.get("stint") or 0)
        by_drv.setdefault(d, {}).setdefault(st, []).append(lp)

    runs: Dict[str, List[List[dict]]] = {}
    for d, stints in by_drv.items():
        for st, group in stints.items():
            group = sorted(group, key=lambda x: int(x.get("lap_number") or 0))
            if len(group) < min_run_len:
                continue
            times = [float(x["lap_time_s"]) for x in group]
            cap = _median(times) + 1.6            # belirgin dışlıları at (trafik/hata)
            clean = [x for x in group if float(x["lap_time_s"]) <= cap]
            if len(clean) >= min_run_len:
                runs.setdefault(d, []).append(clean)
    return runs


def estimate_race_pace(
    laps: Sequence[dict],
    *,
    fuel_correction_s_per_lap: float = FUEL_EFFECT_PER_LAP,
    track_evolution_s_per_lap: float = 0.0,
    min_run_len: int = 5,
) -> RacePaceEstimate:
    """Antrenman (özellikle FP2) uzun turlarından tahmini yarış temposu.

    Her temiz uzun tur bloğu için:
      * yakıt düzeltmesi — araç tur tur hafifleyip hızlandığından, blok içindeki
        her tura ``fuel_correction_s_per_lap * (blok_turu-1)`` eklenir (hepsi
        bloğun ilk turuyla kıyaslanabilir olur).
      * pist gelişimi — ``session_lap_index`` verildiyse seans ilerledikçe artan
        tutuş için ``track_evolution_s_per_lap * idx`` eklenir.
    Bloğun temsili temposu = düzeltilmiş turların kırpılmış ortalaması.
    Pilotun temposu = en hızlı bloğu. Takım = pilotlarının ortalaması.
    """
    runs = _driver_runs(laps, min_run_len=min_run_len)
    if not runs:
        return RacePaceEstimate(ok=False, reference_s=0.0,
                                method="yetersiz temiz uzun tur verisi")

    team_of: Dict[str, str] = {}
    for lp in laps:
        d = str(lp.get("driver") or "").upper()
        if d and lp.get("team"):
            team_of.setdefault(d, str(lp["team"]))

    idx0 = min((int(lp.get("session_lap_index") or 0) for lp in laps
                if lp.get("session_lap_index") is not None), default=0)

    drv_rows: List[dict] = []
    for d, blocks in runs.items():
        best_pace = None
        best_meta = None
        for block in blocks:
            corr = []
            for i, lp in enumerate(block):
                v = float(lp["lap_time_s"]) + fuel_correction_s_per_lap * i
                sli = lp.get("session_lap_index")
                if sli is not None and track_evolution_s_per_lap:
                    v += track_evolution_s_per_lap * (int(sli) - idx0)
                corr.append(v)
            pace = round(_trimmed_mean(corr, trim=0.18), 3)
            if best_pace is None or pace < best_pace:
                best_pace = pace
                best_meta = {
                    "laps": len(block),
                    "compound": _norm_compound(block[0].get("compound")),
                }
        drv_rows.append({
            "driver": d,
            "team": team_of.get(d, ""),
            "pace_s": best_pace,
            "laps": best_meta["laps"],
            "compound": best_meta["compound"],
        })

    drv_rows.sort(key=lambda r: r["pace_s"])
    ref = drv_rows[0]["pace_s"]
    for r in drv_rows:
        r["gap_s"] = round(r["pace_s"] - ref, 3)

    teams: Dict[str, List[float]] = {}
    for r in drv_rows:
        if r["team"]:
            teams.setdefault(r["team"], []).append(r["pace_s"])
    team_rows = [{"team": t, "pace_s": round(_mean(v), 3), "drivers": len(v)}
                 for t, v in teams.items()]
    team_rows.sort(key=lambda r: r["pace_s"])
    if team_rows:
        tref = team_rows[0]["pace_s"]
        for r in team_rows:
            r["gap_s"] = round(r["pace_s"] - tref, 3)

    return RacePaceEstimate(
        ok=True,
        reference_s=ref,
        drivers=drv_rows,
        teams=team_rows,
        method=(f"yakıt {fuel_correction_s_per_lap:.3f} sn/tur"
                + (f" · pist gelişimi {track_evolution_s_per_lap:.3f} sn/tur"
                   if track_evolution_s_per_lap else "")),
    )


# -------------------------------------------------------------------------
# 6.2  LASTİK AŞINMA (DEGRADATION) TAHMİNİ
# -------------------------------------------------------------------------
@dataclass
class DegradationEstimate:
    ok: bool
    compound: str
    deg_rate_s_per_lap: float                 # yakıt düzeltmeli saf aşınma eğimi
    deg_rate_raw_s_per_lap: float             # ham eğim (yakıt + aşınma net)
    deg_rate_temp_adjusted: float             # yüzey sıcaklığına göre ayarlı
    clean_laps: int
    r2: float
    surface_temp_c: Optional[float] = None
    projected_loss_10_laps_s: float = 0.0


def estimate_degradation(
    stint_lap_times_s: Sequence[float],
    *,
    compound: str = "MEDIUM",
    fuel_correction_s_per_lap: float = FUEL_EFFECT_PER_LAP,
    surface_temp_c: Optional[float] = None,
    drop_outliers: bool = True,
) -> DegradationEstimate:
    """Bir stint'in temiz tur sürelerinden tur başına aşınma hızını (deg_rate).

    * Ham eğim = yakıt kazancı (negatif) + aşınma (pozitif) net'i.
    * Saf aşınma = ham eğim + ``fuel_correction_s_per_lap`` (yakıt kazancını geri
      ekle).
    * ``surface_temp_c`` verilirse: 40 °C referansına göre her +1 °C ~ %3 termal
      aşınma çarpanı.
    * Az veri varsa hamurun model önceliğine (``tyre_model(compound).deg``) doğru
      yumuşatılır.
    """
    comp = _norm_compound(compound)
    times = [float(t) for t in stint_lap_times_s if t is not None and float(t) > 0]
    if len(times) < 3:
        model = tyre_model(comp)
        return DegradationEstimate(
            ok=False, compound=comp,
            deg_rate_s_per_lap=round(model.deg, 4),
            deg_rate_raw_s_per_lap=round(model.deg - fuel_correction_s_per_lap, 4),
            deg_rate_temp_adjusted=round(model.deg, 4),
            clean_laps=len(times), r2=0.0, surface_temp_c=surface_temp_c,
            projected_loss_10_laps_s=round(model.deg * 10, 3),
        )

    if drop_outliers and len(times) >= 5:
        cap = _median(times) + 1.4
        times = [t for t in times if t <= cap] or times

    idx = list(range(len(times)))
    raw_slope, _b, r2 = _linfit(idx, times)
    pure = raw_slope + fuel_correction_s_per_lap

    # az/gürültülü veri → hamur önceliğiyle karıştır (r2 ne kadar düşükse o kadar prior)
    prior = tyre_model(comp).deg
    w = _clamp(r2, 0.0, 1.0) * _clamp(len(times) / 12.0, 0.0, 1.0)
    blended = w * pure + (1.0 - w) * prior
    blended = _clamp(blended, -0.05, 0.60)     # negatif aşınma fiziksel değil ~0

    temp_adj = blended
    if surface_temp_c is not None:
        factor = _clamp(1.0 + 0.03 * (float(surface_temp_c) - 40.0), 0.7, 1.8)
        temp_adj = blended * factor

    return DegradationEstimate(
        ok=True,
        compound=comp,
        deg_rate_s_per_lap=round(blended, 4),
        deg_rate_raw_s_per_lap=round(raw_slope, 4),
        deg_rate_temp_adjusted=round(temp_adj, 4),
        clean_laps=len(times),
        r2=round(r2, 3),
        surface_temp_c=surface_temp_c,
        projected_loss_10_laps_s=round(temp_adj * 10, 3),
    )


# -------------------------------------------------------------------------
# 6.3  VMAX & DRS VERİMLİLİK ANALİZİ
# -------------------------------------------------------------------------
@dataclass
class StraightAnalysis:
    ok: bool
    v_max_kmh: float
    straights: List[dict] = field(default_factory=list)   # [{start_m,end_m,length_m,v_max,v_mean,drs_open_frac}]
    drs_available: bool = False
    drs_gain_kmh: Optional[float] = None                  # açık vs kapalı tepe hız farkı


def _detect_straights(samples: Sequence[dict], *, speed_frac: float, min_len_m: float) -> List[Tuple[int, int]]:
    speeds = [float(s.get("speed") or 0) for s in samples]
    if not speeds:
        return []
    vmax = max(speeds) or 1.0
    thr = speed_frac * vmax
    have_thr = any(s.get("throttle") is not None for s in samples)
    runs: List[Tuple[int, int]] = []
    i = 0
    n = len(samples)
    while i < n:
        if speeds[i] >= thr and (not have_thr or float(samples[i].get("throttle") or 0) >= 92):
            j = i
            while j + 1 < n and speeds[j + 1] >= thr * 0.97 and \
                    (not have_thr or float(samples[j + 1].get("throttle") or 0) >= 85):
                j += 1
            d0 = float(samples[i].get("distance") or 0)
            d1 = float(samples[j].get("distance") or 0)
            if d1 - d0 >= min_len_m:
                runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def _drs_on(v) -> bool:
    if v is None:
        return False
    iv = int(round(float(v)))
    return iv in _DRS_ON_CODES or iv == 1


def analyze_straights(
    samples: Sequence[dict],
    *,
    speed_frac: float = 0.85,
    min_straight_len_m: float = 260.0,
) -> StraightAnalysis:
    """Telemetri örneklerinden ana düzlükleri, Vmax'ı ve DRS hız kazancını çıkar.

    ``samples`` en az ``distance`` + ``speed`` içermeli; ``throttle`` varsa düzlük
    tespiti keskinleşir, ``drs`` varsa açık/kapalı hız farkı hesaplanır.
    2026'da DRS yok → ``drs`` kanalı hep kapalı/eksik ise ``drs_available=False``.
    """
    samples = [s for s in samples if s.get("distance") is not None and s.get("speed") is not None]
    if len(samples) < 8:
        return StraightAnalysis(ok=False, v_max_kmh=0.0)

    vmax_all = max(float(s["speed"]) for s in samples)
    seg = _detect_straights(samples, speed_frac=speed_frac, min_len_m=min_straight_len_m)

    straights = []
    for a, b in seg:
        chunk = samples[a:b + 1]
        sp = [float(s["speed"]) for s in chunk]
        drs_frac = (_mean([1.0 if _drs_on(s.get("drs")) else 0.0 for s in chunk])
                    if any(s.get("drs") is not None for s in chunk) else 0.0)
        straights.append({
            "start_m": round(float(chunk[0]["distance"]), 1),
            "end_m": round(float(chunk[-1]["distance"]), 1),
            "length_m": round(float(chunk[-1]["distance"]) - float(chunk[0]["distance"]), 1),
            "v_max_kmh": round(max(sp), 1),
            "v_mean_kmh": round(_mean(sp), 1),
            "drs_open_frac": round(drs_frac, 2),
        })
    straights.sort(key=lambda s: s["length_m"], reverse=True)

    drs_present = any(s.get("drs") is not None for s in samples)
    drs_open_speeds, drs_closed_speeds = [], []
    if drs_present and seg:
        for a, b in seg:
            for s in samples[a:b + 1]:
                (drs_open_speeds if _drs_on(s.get("drs")) else drs_closed_speeds).append(float(s["speed"]))
    drs_available = bool(drs_open_speeds and drs_closed_speeds)
    drs_gain = (round(max(drs_open_speeds) - max(drs_closed_speeds), 1)
                if drs_available else None)

    return StraightAnalysis(
        ok=True,
        v_max_kmh=round(vmax_all, 1),
        straights=straights,
        drs_available=drs_available,
        drs_gain_kmh=drs_gain,
    )


# -------------------------------------------------------------------------
# 6.4  VMIN — KRİTİK VİRAJ HIZLARI
# -------------------------------------------------------------------------
@dataclass
class CornerSpeed:
    corner_id: int
    distance_m: float
    v_min_kmh: float
    entry_speed_kmh: float
    severity: float                          # 0..1 — ne kadar yavaş + ne kadar fren


def critical_corners(
    samples: Sequence[dict],
    *,
    n_corners: int = 3,
    min_prominence_kmh: float = 28.0,
    min_corner_gap_m: float = 180.0,
    window: int = 5,
) -> List[CornerSpeed]:
    """Hız izindeki yerel minimumlardan en kritik ``n_corners`` virajı seç.

    Kriterlik = apeks yavaşlığı + öncesindeki hız düşüşü (fren yükü). Birbirine
    ``min_corner_gap_m``'den yakın minimumlar tek viraja indirgenir (en yavaş
    nokta tutulur). Sıralama kritiklikten yüksekten düşüğe.
    """
    pts = [(float(s["distance"]), float(s["speed"]))
           for s in samples if s.get("distance") is not None and s.get("speed") is not None]
    n = len(pts)
    if n < 2 * window + 3:
        return []
    speeds = [p[1] for p in pts]
    vmax = max(speeds) or 1.0

    raw: List[CornerSpeed] = []
    i = window
    while i < n - window:
        v = speeds[i]
        lo = min(speeds[i - window:i + window + 1])
        if v <= lo + 1e-6:
            entry = max(speeds[max(0, i - 4 * window):i + 1])
            prominence = entry - v
            if prominence >= min_prominence_kmh:
                severity = _clamp(0.6 * (1.0 - v / vmax) + 0.4 * (prominence / vmax), 0.0, 1.0)
                raw.append(CornerSpeed(
                    corner_id=0,
                    distance_m=round(pts[i][0], 1),
                    v_min_kmh=round(v, 1),
                    entry_speed_kmh=round(entry, 1),
                    severity=round(severity, 3),
                ))
            i += window
        i += 1

    # yakın minimumları birleştir — en yavaş (en düşük Vmin) noktayı tut
    raw.sort(key=lambda c: c.distance_m)
    merged: List[CornerSpeed] = []
    for c in raw:
        if merged and c.distance_m - merged[-1].distance_m < min_corner_gap_m:
            if c.v_min_kmh < merged[-1].v_min_kmh:
                merged[-1] = c
        else:
            merged.append(c)

    merged.sort(key=lambda c: c.severity, reverse=True)
    top = merged[:max(1, int(n_corners))]
    top.sort(key=lambda c: c.distance_m)          # pist sırasına geri koy
    for k, c in enumerate(top, 1):
        c.corner_id = k
    return top


def corner_vmin_compare(
    driver_samples: Dict[str, Sequence[dict]],
    corners: Sequence[CornerSpeed],
    *,
    tol_m: float = 60.0,
) -> List[dict]:
    """Her kritik virajda pilotların Vmin'i + en hızlıya fark (km/s).

    ``corners`` ``critical_corners`` çıktısıdır (referans viraj konumları).
    """
    rows: List[dict] = []
    for c in corners:
        per: Dict[str, float] = {}
        for drv, samples in driver_samples.items():
            near = [float(s["speed"]) for s in samples
                    if s.get("distance") is not None and s.get("speed") is not None
                    and abs(float(s["distance"]) - c.distance_m) <= tol_m]
            if near:
                per[str(drv).upper()] = round(min(near), 1)
        if not per:
            continue
        best = max(per.values())
        rows.append({
            "corner_id": c.corner_id,
            "distance_m": c.distance_m,
            "v_min_by_driver": per,
            "delta_to_best": {d: round(best - v, 1) for d, v in per.items()},
        })
    return rows


# -------------------------------------------------------------------------
# 6.5  GAZ / FREN KARAKTERİSTİĞİ — SÜRÜŞ TARZI PROFİLİ
# -------------------------------------------------------------------------
@dataclass
class DrivingStyle:
    ok: bool
    full_throttle_frac: float                 # turun ne kadarı tam gaz
    coast_frac: float                         # gaz da fren de yokken (serbest)
    throttle_aggression: float                # 0..1 — gaza ne kadar sert basıyor
    brake_aggression: float                   # 0..1 — frene ne kadar sert giriyor
    trail_brake_index: float                  # 0..1 — apekse fren taşıma eğilimi
    peak_decel_kmh_s: float
    label: str = ""


def driving_style(samples: Sequence[dict]) -> DrivingStyle:
    """Bir turun telemetrisinden sürüş tarzı profili.

    ``samples``: ``distance`` + ``speed`` + ``throttle`` (0-100) gerekir; ``brake``
    (bool/0-100) varsa fren metrikleri keskinleşir. Etiket: "agresif" / "yumuşak"
    / "dengeli".
    """
    rows = [s for s in samples
            if s.get("distance") is not None and s.get("speed") is not None
            and s.get("throttle") is not None]
    if len(rows) < 12:
        return DrivingStyle(False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "veri yok")

    dist = [float(s["distance"]) for s in rows]
    spd = [float(s["speed"]) for s in rows]
    thr = [_clamp(float(s["throttle"]), 0.0, 100.0) for s in rows]
    brk = [1.0 if bool(s.get("brake")) and float(s.get("brake") or 0) > 0 else 0.0 for s in rows]
    total_len = max(1e-6, dist[-1] - dist[0])

    def _frac(mask):
        seg = 0.0
        for i in range(1, len(rows)):
            if mask(i):
                seg += dist[i] - dist[i - 1]
        return _clamp(seg / total_len, 0.0, 1.0)

    full_throttle = _frac(lambda i: thr[i] >= 96)
    coast = _frac(lambda i: thr[i] < 5 and brk[i] < 0.5)
    braking = _frac(lambda i: brk[i] >= 0.5)

    # gaz agresifliği: gaz uygulama bölgelerinde d(throttle)/d(distance) (%/m).
    # tanh ile 0..1'e ezilir — ~2 %/m "sert", <1 %/m "yumuşak".
    applies = []
    for i in range(1, len(rows)):
        dd = dist[i] - dist[i - 1]
        if dd <= 0:
            continue
        dt = thr[i] - thr[i - 1]
        if dt > 0 and thr[i - 1] < 90:
            applies.append(dt / dd)
    throttle_aggr = math.tanh(_mean(applies) / 2.0) if applies else 0.0

    # fren agresifliği: fren girişindeki yavaşlama oranı (km/s düşüşü / m).
    decels = []
    for i in range(1, len(rows)):
        dd = dist[i] - dist[i - 1]
        if dd <= 0:
            continue
        dv = spd[i - 1] - spd[i]
        if dv > 0 and (brk[i] >= 0.5 or dv / dd > 0.05):
            decels.append(dv / dd)
    peak_decel = round(max(decels) * _mean(spd) if decels else 0.0, 1)   # kaba km/s·s
    brake_aggr = math.tanh((max(decels) if decels else 0.0) / 1.6)

    # trail-brake: fren + kısmi gaz örtüşmesi veya frenden çıkarken hâlâ yavaşlama
    overlap = _frac(lambda i: brk[i] >= 0.5 and thr[i] > 8)
    trail = _clamp(overlap / max(braking, 1e-6) if braking else 0.0, 0.0, 1.0)

    score = 0.45 * throttle_aggr + 0.4 * brake_aggr + 0.15 * (1.0 - coast)
    if score >= 0.62:
        label = "agresif"
    elif score <= 0.4:
        label = "yumuşak"
    else:
        label = "dengeli"

    return DrivingStyle(
        ok=True,
        full_throttle_frac=round(full_throttle, 3),
        coast_frac=round(coast, 3),
        throttle_aggression=round(throttle_aggr, 3),
        brake_aggression=round(brake_aggr, 3),
        trail_brake_index=round(trail, 3),
        peak_decel_kmh_s=peak_decel,
        label=label,
    )


# -------------------------------------------------------------------------
# 6.6  FASTF1 ÇIKARMA KATMANI  (tembel import — modül seviyesi stdlib kalır)
# -------------------------------------------------------------------------
def _has_fastf1() -> bool:
    try:
        import fastf1  # noqa: F401
        return True
    except Exception:
        return False


def load_practice_session(year: int, gp, session_name: str = "FP2"):
    """FastF1 antrenman seansını yükle (telemetri + turlar + hava). Tembel import.

    Ağ/paket yoksa ``RuntimeError``. Analiz motorları bu nesneye bağlı değildir —
    yalnızca ``extract_*`` yardımcıları kullanır.
    """
    try:
        import fastf1
    except Exception as err:  # pragma: no cover - ortam bağımlı
        raise RuntimeError(f"FastF1 kullanılamıyor: {err}") from err
    sess = fastf1.get_session(int(year), gp, session_name)
    sess.load(laps=True, telemetry=True, weather=True, messages=False)
    return sess


def extract_practice_laps(session) -> List[dict]:
    """``session.laps`` → motorların beklediği düz tur kayıtları."""
    laps = getattr(session, "laps", None)
    if laps is None or getattr(laps, "empty", True):
        return []
    out: List[dict] = []
    for idx, (_, row) in enumerate(laps.iterrows()):
        lt = row.get("LapTime")
        secs = lt.total_seconds() if lt is not None and lt == lt else None
        pit_in = row.get("PitInTime")
        pit_out = row.get("PitOutTime")
        is_pit = bool((pit_in is not None and pit_in == pit_in)
                      or (pit_out is not None and pit_out == pit_out))
        out.append({
            "driver": str(row.get("Driver") or ""),
            "team": str(row.get("Team") or ""),
            "lap_time_s": round(secs, 3) if secs else None,
            "lap_number": int(row.get("LapNumber") or 0),
            "stint": int(row.get("Stint") or 0) if row.get("Stint") == row.get("Stint") else 0,
            "compound": str(row.get("Compound") or "") or None,
            "tyre_life": (int(row.get("TyreLife")) if row.get("TyreLife") == row.get("TyreLife") else None),
            "is_accurate": bool(row.get("IsAccurate")) if row.get("IsAccurate") == row.get("IsAccurate") else None,
            "is_pit_lap": is_pit,
            "session_lap_index": idx,
        })
    return out


def extract_lap_samples(session, driver: str, lap_selector="fastest") -> List[dict]:
    """Bir pilotun bir turunun telemetri örnekleri (mesafe eksenli).

    ``lap_selector``: "fastest" | tur numarası (int).
    """
    laps = getattr(session, "laps", None)
    if laps is None or getattr(laps, "empty", True):
        return []
    try:
        dl = laps.pick_drivers(driver) if hasattr(laps, "pick_drivers") else laps.pick_driver(driver)
        lap = dl.pick_fastest() if lap_selector == "fastest" else \
            dl[dl["LapNumber"] == int(lap_selector)].iloc[0]
        tel = lap.get_telemetry()
    except Exception:
        return []
    if tel is None or getattr(tel, "empty", True):
        return []
    cols = tel.columns
    out: List[dict] = []
    for _, r in tel.iterrows():
        out.append({
            "distance": float(r["Distance"]) if "Distance" in cols and r["Distance"] == r["Distance"] else None,
            "speed": float(r["Speed"]) if "Speed" in cols and r["Speed"] == r["Speed"] else None,
            "throttle": float(r["Throttle"]) if "Throttle" in cols and r["Throttle"] == r["Throttle"] else None,
            "brake": (float(r["Brake"]) if "Brake" in cols and str(r["Brake"]) not in ("nan", "None")
                      and not isinstance(r["Brake"], bool) else (100.0 if bool(r.get("Brake")) else 0.0)),
            "drs": (int(r["DRS"]) if "DRS" in cols and r["DRS"] == r["DRS"] else None),
            "x": float(r["X"]) if "X" in cols and r["X"] == r["X"] else None,
            "y": float(r["Y"]) if "Y" in cols and r["Y"] == r["Y"] else None,
        })
    return out


def analyze_practice(year: int, gp, *, session_name: str = "FP2",
                     drivers: Optional[Sequence[str]] = None) -> dict:
    """Uçtan uca: FP seansını yükle → 5 motoru çalıştır → tek rapor sözlüğü.

    Ağ/FastF1 yoksa ``{"ok": False, "reason": ...}``. Saf motorlar ayrıca
    doğrudan (sentetik veriyle) çağrılabilir ve test edilir.
    """
    if not _has_fastf1():
        return {"ok": False, "reason": "FastF1 ortamda yok"}
    try:
        sess = load_practice_session(year, gp, session_name)
    except Exception as err:
        return {"ok": False, "reason": str(err)}

    laps = extract_practice_laps(sess)
    if not laps:
        return {"ok": False, "reason": "tur verisi yok"}

    surface_t = None
    wx = getattr(sess, "weather_data", None)
    if wx is not None and not getattr(wx, "empty", True) and "TrackTemp" in wx.columns:
        try:
            surface_t = round(float(wx["TrackTemp"].mean()), 1)
        except Exception:
            surface_t = None

    pace = estimate_race_pace(laps)

    drv_list = list(drivers) if drivers else [r["driver"] for r in pace.drivers[:6]]
    samples_by_drv = {d: extract_lap_samples(sess, d) for d in drv_list}
    samples_by_drv = {d: s for d, s in samples_by_drv.items() if s}

    ref_samples = next(iter(samples_by_drv.values()), [])
    straights = analyze_straights(ref_samples)
    corners = critical_corners(ref_samples)
    style = {d: driving_style(s).__dict__ for d, s in samples_by_drv.items()}

    # aşınma: her pilotun en uzun stint'i
    deg_by_drv = {}
    runs = _driver_runs(laps, min_run_len=5)
    for d, blocks in runs.items():
        longest = max(blocks, key=len)
        comp = _norm_compound(longest[0].get("compound"))
        deg_by_drv[d] = estimate_degradation(
            [lp["lap_time_s"] for lp in longest],
            compound=comp, surface_temp_c=surface_t,
        ).__dict__

    return {
        "ok": True,
        "year": int(year), "gp": str(gp), "session": session_name,
        "surface_temp_c": surface_t,
        "race_pace": pace.__dict__,
        "degradation": deg_by_drv,
        "straights": straights.__dict__,
        "critical_corners": [c.__dict__ for c in corners],
        "corner_compare": corner_vmin_compare(samples_by_drv, corners),
        "driving_style": style,
    }
