"""Metin normalleştirme — asistan hattının 1. katmanı.

Kural tabanlı NLP'nin temeli: her şeyi küçük harfe indir, Türkçe karakterleri
ASCII'ye çevir (kullanıcı 'Verstappen' de yazsa 'verştappen' de yazsa aynı),
noktalama işaretlerini ayır, token listesi üret. LLM yok, sözlük yok.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def fold(text: str) -> str:
    """'1967 Monako GP'sini' -> '1967 monako gp sini' (aksan/işaret yok)."""
    text = str(text or "").translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _PUNCT.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


@dataclass
class Utterance:
    """Hattın geri kalanının taşıdığı normalize edilmiş girdi."""
    raw: str
    text: str                       # fold(raw)
    tokens: list[str] = field(default_factory=list)

    @property
    def token_set(self) -> set[str]:
        return set(self.tokens)

    def has_any(self, *phrases: str) -> bool:
        return any(p in self.text for p in phrases)

    def has_all(self, *phrases: str) -> bool:
        return all(p in self.text for p in phrases)


def parse(raw: str) -> Utterance:
    folded = fold(raw)
    return Utterance(raw=str(raw or ""), text=folded, tokens=folded.split())
