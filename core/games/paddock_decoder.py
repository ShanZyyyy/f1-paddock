# -*- coding: utf-8 -*-
"""Paddock Dekoder — bulanık resim tahmin oyunu, çekirdek state motoru.

Oyuncuya bulanıklaştırılmış bir görsel (takım logosu / pilot / pist) gösterilir.
Her yanlış tahminde bulanıklık azalır. 5 hak. Yazım hataları Levenshtein
mesafesiyle tolere edilir ("ferari" → "Ferrari" sayılır).

Bu modül SAF: Streamlit/ağ yok. UI state sözlüğünü tutar ve şu akışı kullanır:

    from core.games import paddock_decoder as deco

    state = deco.new_round("teams", seed=day_seed)         # yeni tur
    result = deco.submit_guess(state, "ferari")            # tahmin
    view = deco.public_state(state)                        # UI için görünüm
    #   view["remaining"]  -> kalan hak (CSS blur bunun üstünden ayarlanır)
    #   view["blur_px"]    -> önerilen blur (px)
    #   view["image"], view["solved"], view["failed"], view["answer"?]
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = [
    "CATEGORIES",
    "MAX_GUESSES",
    "DecoderTarget",
    "TARGETS",
    "levenshtein",
    "similarity",
    "normalize",
    "match_score",
    "is_correct",
    "new_round",
    "submit_guess",
    "public_state",
    "blur_px",
    "reveal_hint",
]

CATEGORIES: Tuple[str, ...] = ("teams", "drivers", "tracks")
MAX_GUESSES: int = 5

# Kaç yanlıştan sonra metin ipucu açılır
_HINT_AFTER_MISSES = 2

# Bulanık eşleşme eşiği (0..1 benzerlik). 'ferari'→'ferrari' = 6/7 ≈ 0.857 geçer.
_MATCH_THRESHOLD = 0.78

# Blur eğrisi: başta MAX, her denemede azalır, çözülünce/bitince 0
_BLUR_START_PX = 22.0


# ===========================================================================
# 1) VERİ YAPISI  (şimdilik mock — gerçekte data/games_seed veya JSON'dan)
# ===========================================================================


@dataclass(frozen=True)
class DecoderTarget:
    """Tek bir tahmin hedefi."""

    category: str
    answer: str                       # kanonik doğru cevap
    image: str                        # görsel URL'i veya yerel yol
    aliases: Tuple[str, ...] = ()      # kabul edilen alternatif yazımlar
    hint: str = ""                     # _HINT_AFTER_MISSES yanlıştan sonra açılır

    @property
    def accepted(self) -> Tuple[str, ...]:
        return (self.answer, *self.aliases)


# Her kategori için 3'er mock hedef. image yolları placeholder — UI gerçek
# varlıkla değiştirir (assets/decoder/... veya CDN).
TARGETS: Dict[str, List[DecoderTarget]] = {
    "teams": [
        DecoderTarget("teams", "Ferrari",
                      "assets/decoder/teams/ferrari.png",
                      aliases=("scuderia ferrari", "scuderia", "sf"),
                      hint="Maranello merkezli, tarihin en köklü takımı."),
        DecoderTarget("teams", "Red Bull Racing",
                      "assets/decoder/teams/red_bull.png",
                      aliases=("red bull", "redbull", "rbr", "red bull racing honda"),
                      hint="Milton Keynes; 2010–2013 ve 2021–2023 hâkimiyeti."),
        DecoderTarget("teams", "McLaren",
                      "assets/decoder/teams/mclaren.png",
                      aliases=("mclaren f1 team", "maclaren", "mclaren mercedes"),
                      hint="Woking; papaya turuncusu."),
    ],
    "drivers": [
        DecoderTarget("drivers", "Lewis Hamilton",
                      "assets/decoder/drivers/hamilton.png",
                      aliases=("hamilton", "lewis", "ham", "sir lewis hamilton"),
                      hint="Yedi kez dünya şampiyonu, 44 numara."),
        DecoderTarget("drivers", "Max Verstappen",
                      "assets/decoder/drivers/verstappen.png",
                      aliases=("verstappen", "max", "ver", "verstapen"),
                      hint="Hollandalı; en genç GP kazananı."),
        DecoderTarget("drivers", "Ayrton Senna",
                      "assets/decoder/drivers/senna.png",
                      aliases=("senna", "ayrton", "sen"),
                      hint="Brezilyalı; 1988/1990/1991 şampiyonu, Monaco ustası."),
    ],
    "tracks": [
        DecoderTarget("tracks", "Circuit de Monaco",
                      "assets/decoder/tracks/monaco.png",
                      aliases=("monaco", "monako", "monte carlo", "montekarlo",
                               "circuit de monte-carlo"),
                      hint="Sokak pisti; en düşük ortalama hız, Loews virajı."),
        DecoderTarget("tracks", "Silverstone Circuit",
                      "assets/decoder/tracks/silverstone.png",
                      aliases=("silverstone", "silverston", "british gp",
                               "britanya", "ingiltere"),
                      hint="İlk F1 yarışının (1950) ev sahibi; Maggotts–Becketts."),
        DecoderTarget("tracks", "Suzuka Circuit",
                      "assets/decoder/tracks/suzuka.png",
                      aliases=("suzuka", "suzuca", "japan", "japonya", "japanese gp"),
                      hint="Tek '8' şeklindeki pist; 130R ve esler."),
    ],
}


# ===========================================================================
# 2) BULANIK EŞLEŞTİRME  (Levenshtein — dış paket yok)
# ===========================================================================


def normalize(text: str) -> str:
    """Karşılaştırma için sadeleştir: küçük harf, aksan/TR katlama, yalnız
    harf+rakam+boşluk, tek boşluk."""
    text = str(text or "").strip().lower()
    text = text.replace("ı", "i").replace("İ", "i").replace("ş", "s") \
               .replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    out = []
    for ch in text:
        out.append(ch if (ch.isalnum() or ch == " ") else " ")
    return " ".join("".join(out).split())


def levenshtein(a: str, b: str) -> int:
    """İki dizi arasındaki minimum düzenleme (ekle/sil/değiştir) sayısı."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # silme
                cur[j - 1] + 1,       # ekleme
                prev[j - 1] + (ca != cb),  # değiştirme
            ))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """0..1 — 1 tam eşleşme. Normalize edilmiş mesafeye göre."""
    a, b = normalize(a), normalize(b)
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b)) or 1
    return 1.0 - levenshtein(a, b) / longest


def match_score(guess: str, target: DecoderTarget) -> Tuple[float, str]:
    """Tahminin hedefe en iyi benzerliği + hangi kabul-varyantına eşleştiği."""
    g = normalize(guess)
    best, best_on = 0.0, target.answer
    for candidate in target.accepted:
        c = normalize(candidate)
        score = similarity(g, c)
        # tahmin, çok kelimeli cevabın bir kelimesini tam tutuyorsa (ör. "monaco"
        # -> "circuit de monaco") bunu güçlü kısmi eşleşme say
        if g and g in c.split():
            score = max(score, 0.9)
        if score > best:
            best, best_on = score, candidate
    return best, best_on


def is_correct(guess: str, target: DecoderTarget, *, threshold: float = _MATCH_THRESHOLD) -> bool:
    """Oransal hata toleransı ile doğru mu.

    'ferari' (6 harf) → 'ferrari' mesafe 1 → benzerlik 6/7≈0.857 ≥ 0.78 ✓
    'mercedes' → 'ferrari' benzerlik düşük ✗
    """
    score, _ = match_score(guess, target)
    return score >= threshold


# ===========================================================================
# 3) TUR STATE'İ
# ===========================================================================


@dataclass
class DecoderRound:
    category: str
    target_index: int
    guesses: List[str] = field(default_factory=list)   # ham tahminler
    solved: bool = False
    matched_on: Optional[str] = None

    # -- türetilenler --------------------------------------------------
    @property
    def target(self) -> DecoderTarget:
        return TARGETS[self.category][self.target_index]

    @property
    def attempts_used(self) -> int:
        return len(self.guesses)

    @property
    def remaining(self) -> int:
        return max(0, MAX_GUESSES - self.attempts_used)

    @property
    def failed(self) -> bool:
        return not self.solved and self.remaining == 0

    @property
    def over(self) -> bool:
        return self.solved or self.failed

    # -- serileştirme (Streamlit session_state için) --------------------
    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "target_index": self.target_index,
            "guesses": list(self.guesses),
            "solved": self.solved,
            "matched_on": self.matched_on,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecoderRound":
        return cls(
            category=data["category"],
            target_index=int(data["target_index"]),
            guesses=list(data.get("guesses", [])),
            solved=bool(data.get("solved", False)),
            matched_on=data.get("matched_on"),
        )


def _seed_index(category: str, seed) -> int:
    pool = TARGETS[category]
    if seed is None:
        return 0
    digest = hashlib.sha256(f"{category}:{seed}".encode("utf-8")).hexdigest()
    return int(digest, 16) % len(pool)


def new_round(category: str, *, seed=None, target_index: Optional[int] = None) -> DecoderRound:
    """Yeni tur başlat.

    category      : "teams" | "drivers" | "tracks"
    seed          : verilirse deterministik hedef (günlük mod için gün dizesi)
    target_index  : elle hedef seç (test / "sınırsız" mod rotasyonu)
    """
    if category not in TARGETS:
        raise ValueError(f"bilinmeyen kategori: {category!r} (geçerli: {CATEGORIES})")
    pool = TARGETS[category]
    if target_index is None:
        idx = _seed_index(category, seed)
    else:
        idx = int(target_index) % len(pool)
    return DecoderRound(category=category, target_index=idx)


def submit_guess(state: DecoderRound, guess: str) -> dict:
    """Bir tahmini işle, state'i günceller, sonucu döndürür.

    Dönen: {
        "accepted": bool,          # tahmin sayıldı mı (boş/oyun bitti değil)
        "correct": bool,
        "solved": bool,
        "failed": bool,
        "remaining": int,
        "distance": int,           # normalize Levenshtein mesafesi (en yakın varyanta)
        "similarity": float,       # 0..1
        "matched_on": str | None,  # doğruysa hangi kabul-varyantı
    }
    """
    guess = str(guess or "").strip()
    if state.over or not guess:
        return {
            "accepted": False, "correct": False,
            "solved": state.solved, "failed": state.failed,
            "remaining": state.remaining, "distance": None, "similarity": 0.0,
            "matched_on": state.matched_on,
        }

    state.guesses.append(guess)
    score, on = match_score(guess, state.target)
    correct = score >= _MATCH_THRESHOLD
    if correct:
        state.solved = True
        state.matched_on = on

    ng, nt = normalize(guess), normalize(on)
    return {
        "accepted": True,
        "correct": correct,
        "solved": state.solved,
        "failed": state.failed,
        "remaining": state.remaining,
        "distance": levenshtein(ng, nt),
        "similarity": round(score, 3),
        "matched_on": state.matched_on if correct else None,
    }


# ===========================================================================
# 4) UI GÖRÜNÜMÜ  (blur + hangi bilgi açık)
# ===========================================================================


def blur_px(remaining: int, *, start: float = _BLUR_START_PX,
            solved: bool = False) -> float:
    """Kalan hakka göre önerilen CSS blur (px).

    remaining=5 → tam blur; her yanlışta ~1/5 azalır; 0 veya çözüldü → 0.
    """
    if solved or remaining <= 0:
        return 0.0
    used = MAX_GUESSES - max(0, min(MAX_GUESSES, remaining))
    frac = 1.0 - used / MAX_GUESSES
    return round(start * frac, 1)


def reveal_hint(state: DecoderRound) -> Optional[str]:
    """Yeterince yanlış yapıldıysa (veya oyun bittiyse) metin ipucunu ver."""
    if state.over:
        return state.target.hint or None
    if state.attempts_used >= _HINT_AFTER_MISSES and not state.solved:
        return state.target.hint or None
    return None


def public_state(state: DecoderRound) -> dict:
    """UI'nın ihtiyacı olan her şey — hiç 'spoiler' sızdırmadan.

    Cevap yalnızca oyun bittiğinde (`over`) döner.
    """
    return {
        "category": state.category,
        "image": state.target.image,
        "guesses": list(state.guesses),
        "attempts_used": state.attempts_used,
        "remaining": state.remaining,
        "max_guesses": MAX_GUESSES,
        "solved": state.solved,
        "failed": state.failed,
        "over": state.over,
        "blur_px": blur_px(state.remaining, solved=state.solved),
        "hint": reveal_hint(state),
        "answer": state.target.answer if state.over else None,
        "matched_on": state.matched_on,
    }
