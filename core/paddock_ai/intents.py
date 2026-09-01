"""Niyet sınıflandırma (intent classification) — asistan hattının 3. katmanı.

LLM yerine puanlı kural motoru: her niyetin anahtar kelime kümeleri ve gerekli
varlıkları var. Skor = kelime isabetleri + varlık bonusu. En yüksek skor kazanır;
eşik altındaysa FALLBACK.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .entities import Entities
from .normalize import Utterance, fold


@dataclass
class IntentDef:
    name: str
    keywords: tuple[str, ...] = ()        # herhangi biri geçerse +1
    strong: tuple[str, ...] = ()          # geçerse +2 (ayırt edici ifadeler)
    needs: tuple[str, ...] = ()           # bu varlıklar yoksa niyet elenir
    boost_if: tuple[str, ...] = ()        # bu varlık varsa +1


# Sıra önemsiz — hepsi puanlanır. `needs` sert filtre.
CATALOG: list[IntentDef] = [
    IntentDef("RACE_RESULT",
              keywords=("kazandi", "kazanan", "galip", "pole", "podyum", "birinci"),
              strong=("kim kazandi", "yarisi kim", "pole kimin"),
              boost_if=("gp",)),
    IntentDef("SEASON_CHAMPION",
              keywords=("sampiyon", "dunya birincisi", "wdc", "title"),
              strong=("sampiyonu kim", "hangi yil sampiyon"),
              needs=("year",)),
    IntentDef("STANDINGS",
              keywords=("klasman", "puan durumu", "lider", "kim onde", "sirala"),
              strong=("kim lider", "sampiyonada lider", "puan durumu ne")),
    IntentDef("DRIVER_SEASON",
              keywords=("bu sezon", "sezonu", "formda", "nasil gidiyor", "gidisat"),
              strong=("bu sezon nasil",),
              needs=("driver",)),
    IntentDef("DRIVER_CAREER",
              keywords=("kariyer", "toplam", "kac galibiyet", "kac pole", "kac podyum",
                        "kac yaris", "ne zaman basladi", "ilk yaris"),
              strong=("kariyerinde kac", "toplam kac"),
              needs=("driver",)),
    IntentDef("HEAD_TO_HEAD",
              keywords=("karsilastir", "vs", "kime karsi", "kim daha", "hangisi daha")),
    IntentDef("RECORD",
              keywords=("rekor", "en cok", "en fazla", "en genc", "en yasli", "tarihte kim"),
              strong=("rekoru kimin", "en cok kim", "en genc sampiyon", "en genc dunya",
                      "en cok galibiyet", "en cok pole", "en fazla sampiyon")),
    IntentDef("TECH_UPGRADE",
              keywords=("guncelleme", "upgrade", "yeni parca", "gelistirme", "paket",
                        "aero", "kanat", "zemin", "difuzor", "getirdi"),
              strong=("hangi guncelleme", "son guncelleme", "ne getirdi"),
              needs=("team",)),
    IntentDef("NEXT_RACE",
              keywords=("siradaki yaris", "gelecek yaris", "ne zaman", "takvim",
                        "bir sonraki", "hangi pist")),
    IntentDef("USER_STATS",
              keywords=("tahmin puanim", "benim puanim", "kac isabet", "tahmin skorum"),
              strong=("tahmin puanim",)),
]

_MIN_SCORE = 2


@dataclass
class Classification:
    name: str
    score: int
    scores: dict = field(default_factory=dict)


def classify(u: Utterance, ent: Entities) -> Classification:
    have = {
        "year": ent.year is not None,
        "gp": ent.gp is not None,
        "driver": bool(ent.drivers),
        "team": ent.team is not None,
    }
    scores: dict[str, int] = {}
    for d in CATALOG:
        if any(not have.get(n, False) for n in d.needs):
            continue
        s = 0
        s += sum(1 for k in d.keywords if fold(k) in u.text)
        s += sum(2 for k in d.strong if fold(k) in u.text)
        s += sum(1 for b in d.boost_if if have.get(b, False))
        # zorunlu varlığı olan niyetler, o varlık geldiyse bir taban puan alır
        if d.needs and s:
            s += 1
        # HEAD_TO_HEAD yalnız iki pilot varsa
        if d.name == "HEAD_TO_HEAD" and len(ent.drivers) < 2:
            s = 0
        if s:
            scores[d.name] = s

    if not scores:
        # niyet kelimesi yok — varlıklardan makul varsayılan
        if ent.gp or (ent.year and not ent.drivers):
            return Classification("RACE_RESULT", 1, scores)
        if ent.year and ent.metrics == ["champion"]:
            return Classification("SEASON_CHAMPION", 1, scores)
        if ent.team:
            return Classification("TECH_UPGRADE", 1, scores)
        if ent.drivers:
            return Classification("DRIVER_CAREER", 1, scores)
        return Classification("FALLBACK", 0, scores)

    best = max(scores, key=scores.get)
    # zayıf skor + hiç somut varlık yoksa cevap verme
    if scores[best] < _MIN_SCORE and not any(
            (have["gp"], have["year"], have["driver"], have["team"])):
        return Classification("FALLBACK", scores[best], scores)
    return Classification(best, scores[best], scores)
