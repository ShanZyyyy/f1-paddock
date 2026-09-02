# -*- coding: utf-8 -*-
"""Strateji Duvarı — çekirdek olay motoru (event engine).

Saf matematiksel model: lastik aşınma eğrisi, yakıt düzeltmesi, pit kaybı,
undercut/overcut olasılığı ve Güvenlik Aracı (SC / VSC) tetikleyicileri.

Bu modül ``streamlit_app.py`` içindeki veri-güdümlü ``_strat_simulate_v67``
(gerçek yarışı yeniden oynatır) fonksiyonunu **bozmaz**; onu tamamlar. Buradaki
fonksiyonlar:

* FastF1/pandas GEREKTİRMEZ — yalnızca stdlib (``math``, ``random``). Böylece
  "farazi" strateji senaryoları (kullanıcının kendi pit planı) veriye
  bağlanmadan çalıştırılabilir ve saf birim testi yazılabilir.
* Sabitler ``streamlit_app._STRAT_COMPOUND_DEFAULT`` ile aynı ailedendir
  (offset sn, linear deg sn/tur, cliff turu).

Kullanım::

    from core.strategy_engine import (
        TYRES, tyre_pace_loss, degradation_curve,
        undercut_probability, SafetyCarModel, simulate_stint_plan,
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
