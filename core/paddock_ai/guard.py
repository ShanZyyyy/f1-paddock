"""Kapsam kapısı (domain guard) — asistan hattının 0. katmanı.

Kural 1: bot yalnız F1 sorularına + temel selamlaşmaya cevap verir. Bunun
dışındaki her şeyi kibarca reddeder.

Sıralama:
  1. Selamlaşma/hal hatır  -> hazır dostça yanıt (SMALLTALK)
  2. Net bir F1 sinyali var mı? (pilot/takım/pist adı, "f1", yıl+yarış sözcüğü…)
     -> hatta devam (PASS)
  3. Hiçbiri -> reddet (REFUSE)
"""
from __future__ import annotations

from dataclasses import dataclass

from .normalize import Utterance, fold

_GREETINGS = (
    "merhaba", "selam", "gunaydin", "iyi aksamlar", "iyi geceler", "naber",
    "nasilsin", "nasil gidiyor", "hey", "hello", "hi ", "kim sin", "kimsin",
    "adin ne", "ne yapabilirsin", "yardim",
)

# Cümlede bunlardan biri geçiyorsa "kesin F1" say.
_F1_KEYWORDS = (
    "f1", "formula 1", "formula1", "grand prix", " gp ", "gp ", " gp",
    "pole", "podyum", "sampiyon", "siralama", "pit", "lastik", "pilot",
    "takim", "yaris", "sezon", "puan durumu", "klasman", "kokpit",
    "sprint", "quali", "fia", "paddock", "circuit", "pist", "tur ",
    "undercut", "drs", "ers", "safety car", "guvenlik araci",
)


@dataclass
class Verdict:
    action: str          # "SMALLTALK" | "PASS" | "REFUSE"
    reply: str = ""       # SMALLTALK/REFUSE'da doğrudan kullanıcıya


REFUSAL = ("Ben Paddock Asistan — yalnızca Formula 1 üzerine konuşabiliyorum. "
           "Yarış sonuçları, şampiyona durumu, pilot/takım istatistikleri, tarihî "
           "rekorlar veya teknik güncellemeler hakkında sorabilirsin.")

_SMALLTALK = {
    "nasilsin": "İyiyim, teşekkürler! Sıradaki yarışı bekliyorum. Sana nasıl yardımcı olabilirim?",
    "kimsin": "Paddock Asistan'ım — bu sitenin doğrulanmış F1 verisiyle çalışan yardımcısı. "
              "LLM değilim; sorunu ayrıştırıp veritabanımızdan cevap veririm.",
    "ne yapabilirsin": "Yarış kazananı/pole'u, herhangi bir yılın şampiyonunu, güncel "
                       "klasmanı, bir pilotun sezonunu/kariyerini, tarihî rekorları ve "
                       "takımların teknik güncellemelerini söyleyebilirim.",
    "_default": "Merhaba! F1 hakkında ne öğrenmek istersin?",
}


def check(u: Utterance, *, has_f1_entity: bool) -> Verdict:
    """`has_f1_entity`: entity çıkarımı bir pilot/takım/GP/şampiyon-yılı buldu mu.
    Bu, anahtar-kelime listesine güvenmeden 'F1'lik' için en güçlü sinyal."""
    t = u.text

    # 1) selamlaşma — ama F1 sinyali de varsa selamı geç, soruyu işle
    if not has_f1_entity and any(g in f" {t} " for g in _GREETINGS):
        for key, msg in _SMALLTALK.items():
            if key != "_default" and key in t:
                return Verdict("SMALLTALK", msg)
        return Verdict("SMALLTALK", _SMALLTALK["_default"])

    # 2) F1 sinyali
    if has_f1_entity or any(k in t for k in _F1_KEYWORDS):
        return Verdict("PASS")

    # 3) kapsam dışı
    return Verdict("REFUSE", REFUSAL)
