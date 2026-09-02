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
import random
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
    "score_round",
    "DecoderSession",
    "new_session",
]

CATEGORIES: Tuple[str, ...] = ("teams", "drivers", "tracks")
MAX_GUESSES: int = 4

# Kaç yanlıştan sonra metin ipucu açılır (yalnızca son hak kalınca)
_HINT_AFTER_MISSES = 3

# Bulanık eşleşme eşiği (0..1 benzerlik). Yazım hatası affedilir ama yakın
# tahmin yeterli değil: 'ferari'→'ferrari' = 6/7 ≈ 0.857 geçer; 'ferai' geçmez.
_MATCH_THRESHOLD = 0.82

# Blur eğrisi: başta MAX, her denemede azalır, çözülünce/bitince taban
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


# Her kategori için 3'er hedef. image = gerçek F1.com / Wikimedia görseli
# (UI bunu bulanıklaştırarak gösterir; 404 olursa UI kendi silüet-placeholder'ına düşer).
_F1_LOGO = ("https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/"
            "v1740000001/common/f1/2025/{slug}/2025{slug}logowhite.webp")
_F1_HEAD = ("https://www.formula1.com/content/dam/fom-website/drivers/"
            "2025Drivers/{sur}.jpg.transform/2col/image.jpg")
_F1_MAP = ("https://media.formula1.com/image/upload/f_auto,c_limit,w_1320,q_auto/"
           "content/dam/fom-website/2018-redesign-assets/Circuit%20maps%2016x9/{map}_Circuit")

# Alias'lar dar tutulur: yazım hatası toleransı Levenshtein'de zaten var; burada
# yalnızca tam ad + SOYAD + dil varyantı (Türkçe/İngilizce) kabul edilir.
# Tek isim ("Max"), 3-harf kod ("VER"), gevşek kısaltma ("RB", "SF") YOK.
TARGETS: Dict[str, List[DecoderTarget]] = {
    "teams": [
        DecoderTarget("teams", "Ferrari",
                      _F1_LOGO.format(slug="ferrari"),
                      aliases=("scuderia ferrari",),
                      hint="Kuruluşundan bugüne kesintisiz yarışan tek takım."),
        DecoderTarget("teams", "Red Bull Racing",
                      _F1_LOGO.format(slug="redbullracing"),
                      aliases=("red bull racing", "red bull"),
                      hint="Enerji içeceği markasının Milton Keynes ekibi."),
        DecoderTarget("teams", "McLaren",
                      _F1_LOGO.format(slug="mclaren"),
                      aliases=("mclaren f1 team", "mclaren racing"),
                      hint="Woking; kurucusunun adını taşıyan Yeni Zelandalı ekip."),
    ],
    "drivers": [
        DecoderTarget("drivers", "Lewis Hamilton",
                      _F1_HEAD.format(sur="hamilton"),
                      aliases=("hamilton", "sir lewis hamilton"),
                      hint="Rekor eşitleyen yedi kez dünya şampiyonu."),
        DecoderTarget("drivers", "Max Verstappen",
                      _F1_HEAD.format(sur="verstappen"),
                      aliases=("verstappen",),
                      hint="En genç GP galibi; babası da F1'de yarıştı."),
        DecoderTarget("drivers", "Charles Leclerc",
                      _F1_HEAD.format(sur="leclerc"),
                      aliases=("leclerc",),
                      hint="F2 ve GP3 şampiyonu; kırmızı arabayı sürüyor."),
    ],
    "tracks": [
        DecoderTarget("tracks", "Circuit de Monaco",
                      _F1_MAP.format(map="Monaco"),
                      aliases=("monaco", "monako", "monte carlo", "montekarlo"),
                      hint="Takvimin en yavaş ortalama hızlı, en dar pisti."),
        DecoderTarget("tracks", "Silverstone Circuit",
                      _F1_MAP.format(map="Great_Britain"),
                      aliases=("silverstone", "silverston"),
                      hint="1950'de ilk F1 yarışının yapıldığı eski hava üssü."),
        DecoderTarget("tracks", "Suzuka Circuit",
                      _F1_MAP.format(map="Japan"),
                      aliases=("suzuka", "suzuca"),
                      hint="Dünyanın tek '8' şeklindeki pisti."),
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
        # tahmin, çok kelimeli cevabın AYIRT EDİCİ (4+ harf) bir kelimesini tam
        # tutuyorsa (ör. "monaco" -> "circuit de monaco") kısmi eşleşme say —
        # "de", "f1", "gp" gibi kısa/jenerik kelimeler saymaz
        if len(g) >= 4 and g in c.split():
            score = max(score, 0.86)
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
        return random.randrange(len(pool))
    digest = hashlib.sha256(f"{category}:{seed}".encode("utf-8")).hexdigest()
    return int(digest, 16) % len(pool)


def new_round(category: str, *, seed=None, target_index: Optional[int] = None) -> DecoderRound:
    """Yeni tur başlat.

    category      : "teams" | "drivers" | "tracks"
    seed          : verilirse deterministik hedef; verilmezse rastgele
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

    remaining == MAX_GUESSES → tam blur; her yanlışta eşit azalır; 0/çözüldü → 0.
    (UI kendi eğrisini kullanabilir; bu yalnızca basit bir yardımcı.)
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
        "score": score_round(state) if state.over else 0,
    }


# ===========================================================================
# 5) PUANLAMA
# ===========================================================================

_SOLVE_BASE = 50        # 1. denemede bilme
_SOLVE_STEP = 13        # her fazladan deneme -kaybı
_SOLVE_FLOOR = 10       # son denemede bile en az
_FAIL_XP = 2            # teselli
_SWEEP_BONUS = 30       # 3 kategoriyi de bilme (artık daha zor)


def score_round(state: DecoderRound) -> int:
    """Bir turun XP değeri. Oyun bitmediyse 0.

    1. deneme 50 · 2. 37 · 3. 24 · 4. 11 · bilemedi 2.
    (stewardle/predict oyunlarıyla aynı ~5–50 bandı.)
    """
    if not state.over:
        return 0
    if not state.solved:
        return _FAIL_XP
    return max(_SOLVE_FLOOR, _SOLVE_BASE - _SOLVE_STEP * (state.attempts_used - 1))


# ===========================================================================
# 6) OTURUM — 3 KATEGORİLİK OYNANIŞ
# ===========================================================================


@dataclass
class DecoderSession:
    """teams → drivers → tracks sırasıyla üç turluk tam oyun."""

    order: List[str] = field(default_factory=lambda: list(CATEGORIES))
    rounds: List[DecoderRound] = field(default_factory=list)
    index: int = 0
    _seed: Optional[str] = None

    # -- erişim ------------------------------------------------------
    @property
    def current(self) -> Optional[DecoderRound]:
        if 0 <= self.index < len(self.rounds):
            return self.rounds[self.index]
        return None

    @property
    def done(self) -> bool:
        return len(self.rounds) == len(self.order) and all(r.over for r in self.rounds)

    @property
    def solved_count(self) -> int:
        return sum(1 for r in self.rounds if r.solved)

    @property
    def swept(self) -> bool:
        return self.done and self.solved_count == len(self.order)

    @property
    def total_score(self) -> int:
        base = sum(score_round(r) for r in self.rounds if r.over)
        return base + (_SWEEP_BONUS if self.swept else 0)

    # -- ilerleme --------------------------------------------------
    def advance(self) -> Optional[DecoderRound]:
        """Sıradaki kategoriye geç. Aktif tur bitmemişse dokunmaz."""
        if self.current is not None and not self.current.over:
            return self.current
        if self.index + 1 < len(self.order):
            self.index += 1
            if self.index >= len(self.rounds):
                self.rounds.append(new_round(self.order[self.index], seed=self._seed))
        return self.current

    # -- serileştirme --------------------------------------------
    def to_dict(self) -> dict:
        return {
            "order": list(self.order),
            "index": self.index,
            "seed": self._seed,
            "rounds": [r.to_dict() for r in self.rounds],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecoderSession":
        s = cls(
            order=list(data.get("order", CATEGORIES)),
            index=int(data.get("index", 0)),
            rounds=[DecoderRound.from_dict(d) for d in data.get("rounds", [])],
        )
        s._seed = data.get("seed")
        return s

    def public_state(self) -> dict:
        cur = self.current
        return {
            "category_order": list(self.order),
            "index": self.index,
            "step": f"{self.index + 1}/{len(self.order)}",
            "round": public_state(cur) if cur is not None else None,
            "done": self.done,
            "solved_count": self.solved_count,
            "swept": self.swept,
            "total_score": self.total_score,
        }


def new_session(*, seed=None, order: Optional[List[str]] = None) -> DecoderSession:
    """Yeni tam oyun.

    `seed` verilirse (günlük mod) üç hedef de o seed'e göre deterministik.
    Verilmezse rastgele bir oturum seed'i üretilir ve saklanır — böylece oturum
    yeniden-çalıştırmalar (Streamlit rerun) arasında tutarlı kalır ama her yeni
    oyun farklı hedefler verir.
    """
    order = list(order or CATEGORIES)
    for cat in order:
        if cat not in TARGETS:
            raise ValueError(f"bilinmeyen kategori: {cat!r}")
    resolved = str(seed) if seed is not None else format(random.randrange(1 << 40), "x")
    s = DecoderSession(order=order, rounds=[new_round(order[0], seed=resolved)], index=0)
    s._seed = resolved
    return s
