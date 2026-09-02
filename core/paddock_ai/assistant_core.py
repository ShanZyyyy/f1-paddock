# -*- coding: utf-8 -*-
"""Paddock Asistan — bağımsız çekirdek iskelet (RAG mimarisi taslağı).

Dış API YOK (ChatGPT vb.). Üç katmanlı, kendi içinde çalışan, test edilebilir
akış:

    1) parse_query(text)          -> niyet + varlık çıkarımı (Regex/Keyword)
    2) route_to_data(intent, ent) -> veritabanı yönlendiricisi (şimdilik mock)
    3) generate_response(data)    -> şablonlu doğal dil üretimi

    ask(text) = 1 -> guard -> 2 -> 3   (tek giriş noktası)

Bu modül, paketteki gelişmiş `pipeline.answer()` hattından BAĞIMSIZDIR ve onu
değiştirmez. Amaç: yeni niyetlerin/kuralların hızlı prototiplenebileceği,
gerçek SQLite/JSON sorgularının yerine kolayca geçirilebileceği sade bir
çekirdek. `route_to_data` içindeki mock sözlükler, gerçek sorgu fonksiyonlarıyla
(``retrievers/history_db.py`` vb.) birebir aynı sözlük şeklini döndürür.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .normalize import fold  # Türkçe→ASCII katlama; saf, bağımsız yardımcı

__all__ = [
    "ParsedQuery",
    "AssistantReply",
    "parse_query",
    "route_to_data",
    "generate_response",
    "ask",
    "OUT_OF_SCOPE_REPLY",
]

# ===========================================================================
# 0) SÖZLÜKLER  (gerçek sistemde f1_constants / tarih DB'sinden beslenir)
# ===========================================================================

_YEAR_RE = re.compile(r"\b(18[5-9]\d|19\d\d|20[0-4]\d)\b")

# kullanıcı yazımı  ->  kanonik Grand Prix adı
_TRACK_ALIASES: Dict[str, str] = {
    "monako": "Monaco", "monte karlo": "Monaco", "montekarlo": "Monaco",
    "spa": "Belgian", "francorchamps": "Belgian", "belcika": "Belgian",
    "monza": "Italian", "italya": "Italian",
    "silverstone": "British", "ingiltere": "British", "britanya": "British",
    "imola": "Emilia Romagna", "nurburgring": "German", "hockenheim": "German",
    "suzuka": "Japanese", "japonya": "Japanese",
    "interlagos": "Brazilian", "brezilya": "Brazilian", "sao paulo": "Brazilian",
    "hungaroring": "Hungarian", "macaristan": "Hungarian",
    "zandvoort": "Dutch", "hollanda": "Dutch",
    "cota": "United States", "austin": "United States",
    "bahreyn": "Bahrain", "sakhir": "Bahrain",
    "cebeli tarik": "Gibraltar",  # (bilinçli imkânsız kayıt — not_found testi için)
}

# kullanıcı yazımı  ->  kanonik takım adı
_TEAM_ALIASES: Dict[str, str] = {
    "aston martin": "Aston Martin", "aston": "Aston Martin",
    "ferrari": "Ferrari", "scuderia": "Ferrari",
    "red bull": "Red Bull Racing", "redbull": "Red Bull Racing", "rb": "Red Bull Racing",
    "mercedes": "Mercedes", "merc": "Mercedes",
    "mclaren": "McLaren",
    "williams": "Williams",
    "alpine": "Alpine", "renault": "Alpine",
    "haas": "Haas F1 Team",
    "sauber": "Kick Sauber", "kick sauber": "Kick Sauber", "audi": "Kick Sauber",
    "racing bulls": "Racing Bulls", "rb f1": "Racing Bulls", "alphatauri": "Racing Bulls",
}

# --- niyet kuralları: (intent, güçlü ifadeler, anahtar kelimeler, zorunlu varlık)
_INTENT_RULES = [
    ("race_winner",
     ("kim kazandi", "yarisi kim kazandi", "yarisini kim", "who won"),
     ("kazandi", "kazanan", "galip", "birinci", "winner", "won", "zaferi"),
     None),
    ("pole_position",
     ("pole kimin", "pole pozisyonu kimin", "poleu kim aldi"),
     ("pole", "pole pozisyonu", "en hizli siralama"),
     None),
    ("season_champion",
     ("dunya sampiyonu kim", "sampiyonu kim", "kim sampiyon oldu"),
     ("sampiyon", "dunya birincisi", "wdc", "title", "sampiyonluk"),
     "year"),
    ("car_upgrades",
     ("son guncellemeler", "hangi guncellemeyi getirdi", "yeni parca getirdi"),
     ("guncelleme", "guncellemeler", "upgrade", "yeni parca", "gelistirme",
      "aero paket", "paket", "kanat", "difuzor", "zemin", "getirdi"),
     "team"),
]

# net "bu F1" sinyalleri (varlık bulunamasa bile)
_F1_HINTS = ("formula 1", "f1", "grand prix", " gp", "gp ", "pilot", "yaris",
             "sezon", "sampiyon", "pole", "podyum", "lastik", "pit", "takim",
             "siralama", "klasman", "kokpit", "paddock", "pist")

OUT_OF_SCOPE_REPLY = ("Ben bir yarış mühendisiyim, sadece pit duvarındaki "
                      "verilere hakimim.")


# ===========================================================================
# 1) NİYET + VARLIK ÇIKARIMI
# ===========================================================================


@dataclass
class ParsedQuery:
    """`parse_query` çıktısı."""

    raw: str
    text: str                                   # fold(raw)
    intent: str = "unknown"
    entities: Dict[str, Any] = field(default_factory=dict)
    score: int = 0

    @property
    def has_f1_signal(self) -> bool:
        if any(self.entities.get(k) for k in ("year", "track", "team", "driver")):
            return True
        return any(h in f" {self.text} " for h in _F1_HINTS)



def _match_alias(text: str, aliases: Dict[str, str]) -> Optional[str]:
    """En uzun eşleşen anahtar kazanır ('sao paulo' > 'paulo')."""
    best_key = None
    for key in aliases:
        if key in text and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return aliases[best_key] if best_key else None


def _extract_entities(text: str) -> Dict[str, Any]:
    ent: Dict[str, Any] = {}
    m = _YEAR_RE.search(text)
    if m:
        ent["year"] = int(m.group(1))
    track = _match_alias(text, _TRACK_ALIASES)
    if track:
        ent["track"] = track
    team = _match_alias(text, _TEAM_ALIASES)
    if team:
        ent["team"] = team
    return ent


def _classify(text: str, ent: Dict[str, Any]) -> tuple[str, int]:
    padded = f" {text} "
    best_intent, best_score = "unknown", 0
    for intent, strong, keywords, needs in _INTENT_RULES:
        if needs and not ent.get(needs):
            continue
        score = 0
        score += sum(3 for s in strong if s in text)
        score += sum(1 for k in keywords if k in padded or f" {k} " in padded or k in text)
        if needs and score:
            score += 1                          # zorunlu varlık geldiyse taban puan
        if score > best_score:
            best_intent, best_score = intent, score

    if best_score == 0:
        # niyet kelimesi yok — varlıklardan makul varsayım
        if ent.get("year") and ent.get("track"):
            return "race_winner", 1
        if ent.get("team"):
            return "car_upgrades", 1
        if ent.get("year"):
            return "season_champion", 1
    return best_intent, best_score


def parse_query(text: str) -> ParsedQuery:
    """Ham cümleden niyet + varlık çıkar.

    >>> q = parse_query("1967 Monako GP'sini kim kazandı?")
    >>> q.intent, q.entities
    ('race_winner', {'year': 1967, 'track': 'Monaco'})

    >>> parse_query("Aston Martin son güncellemeler").intent
    'car_upgrades'
    """
    folded = fold(text)
    ent = _extract_entities(folded)
    intent, score = _classify(folded, ent)
    return ParsedQuery(raw=str(text or ""), text=folded, intent=intent,
                       entities=ent, score=score)


# ===========================================================================
# 2) VERİTABANI YÖNLENDİRİCİSİ  (MOCK — gerçek SQL/JSON sorgusu buraya girer)
# ===========================================================================

# Gerçek sistemde: history_db.race_result(year, gp) → SQLite
_MOCK_RACES: Dict[tuple, Dict[str, str]] = {
    (1967, "Monaco"): {"winner": "Denny Hulme", "constructor": "Brabham-Repco",
                       "pole": "Jack Brabham"},
    (1988, "Monaco"): {"winner": "Alain Prost", "constructor": "McLaren-Honda",
                       "pole": "Ayrton Senna"},
    (2023, "Italian"): {"winner": "Max Verstappen", "constructor": "Red Bull Racing",
                        "pole": "Carlos Sainz"},
    (1950, "British"): {"winner": "Nino Farina", "constructor": "Alfa Romeo",
                        "pole": "Nino Farina"},
}

# Gerçek sistemde: yerel şampiyon arşivi (f1_constants.F1_WORLD_CHAMPIONS)
_MOCK_CHAMPIONS: Dict[int, Dict[str, str]] = {
    1967: {"driver": "Denny Hulme", "constructor": "Brabham-Repco"},
    1988: {"driver": "Ayrton Senna", "constructor": "McLaren-Honda"},
    1994: {"driver": "Michael Schumacher", "constructor": "Benetton-Ford"},
    2021: {"driver": "Max Verstappen", "constructor": "Red Bull Racing"},
}

# Gerçek sistemde: tech.latest_for_team(team) → tech_upgrades.json
_MOCK_UPGRADES: Dict[str, list] = {
    "Aston Martin": [
        {"event": "Imola", "date": "2024-05-17",
         "components": ["yeni zemin", "yan gövde girişi"],
         "goal": "düşük hızlı virajda yere basma"},
        {"event": "Barcelona", "date": "2024-06-21",
         "components": ["arka kanat", "beam kanat"],
         "goal": "düşük sürüklenme konfigürasyonu"},
    ],
    "Ferrari": [
        {"event": "Bahrain", "date": "2024-02-21",
         "components": ["yeni difüzör"], "goal": "arka aks stabilitesi"},
    ],
}


def route_to_data(intent: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    """Niyet + varlıkları uygun (mock) veri sorgusuna yönlendir.

    Dönen sözlük her zaman ``intent`` ve ``status`` ('ok' | 'not_found' |
    'need_more' | 'unsupported') taşır — ``generate_response`` bunu okur.
    """
    ent = entities or {}

    if intent == "race_winner":
        year, track = ent.get("year"), ent.get("track")
        if not year or not track:
            return {"intent": intent, "status": "need_more",
                    "missing": [k for k in ("year", "track") if not ent.get(k)]}
        row = _MOCK_RACES.get((year, track))
        if not row:
            return {"intent": intent, "status": "not_found", "year": year, "track": track}
        return {"intent": intent, "status": "ok", "year": year, "track": track,
                "winner": row["winner"], "constructor": row.get("constructor")}

    if intent == "pole_position":
        year, track = ent.get("year"), ent.get("track")
        if not year or not track:
            return {"intent": intent, "status": "need_more",
                    "missing": [k for k in ("year", "track") if not ent.get(k)]}
        row = _MOCK_RACES.get((year, track))
        if not row or not row.get("pole"):
            return {"intent": intent, "status": "not_found", "year": year, "track": track}
        return {"intent": intent, "status": "ok", "year": year, "track": track,
                "pole": row["pole"]}

    if intent == "season_champion":
        year = ent.get("year")
        if not year:
            return {"intent": intent, "status": "need_more", "missing": ["year"]}
        row = _MOCK_CHAMPIONS.get(year)
        if not row:
            return {"intent": intent, "status": "not_found", "year": year}
        return {"intent": intent, "status": "ok", "year": year,
                "driver": row["driver"], "constructor": row.get("constructor")}

    if intent == "car_upgrades":
        team = ent.get("team")
        if not team:
            return {"intent": intent, "status": "need_more", "missing": ["team"]}
        updates = _MOCK_UPGRADES.get(team)
        if not updates:
            return {"intent": intent, "status": "not_found", "team": team}
        return {"intent": intent, "status": "ok", "team": team, "updates": updates}

    return {"intent": intent, "status": "unsupported"}


# ===========================================================================
# 3) DİNAMİK YANIT ÜRETİCİ
# ===========================================================================

_NOT_FOUND = ("{what} için doğrulanmış bir kayıt bulamadım. Farklı bir "
              "yıl/yarış/takım ile dener misin?")

_NEED_MORE = {
    "year": "hangi yılı sorduğunu",
    "track": "hangi yarışı (pist) sorduğunu",
    "team": "hangi takımı sorduğunu",
}


def generate_response(data: Dict[str, Any]) -> str:
    """Ham (mock) veriyi şablonlu bir F1 cümlesine çevir."""
    if not data:
        return OUT_OF_SCOPE_REPLY

    intent = data.get("intent", "unknown")
    status = data.get("status", "unsupported")

    if status == "out_of_scope":
        return OUT_OF_SCOPE_REPLY

    if status == "need_more":
        miss = data.get("missing") or []
        want = " ve ".join(_NEED_MORE.get(m, m) for m in miss) or "biraz daha ayrıntı"
        return f"Bunu yanıtlamak için {want} belirtmen gerekiyor."

    if status == "not_found":
        bits = []
        if data.get("year"):
            bits.append(str(data["year"]))
        if data.get("track"):
            bits.append(f"{data['track']} GP")
        if data.get("team"):
            bits.append(str(data["team"]))
        what = " ".join(bits) or "Bu soru"
        return _NOT_FOUND.format(what=what)

    if status != "ok":
        return ("Bu tür bir soruyu henüz yanıtlayamıyorum — yarış kazananı, "
                "pole, sezon şampiyonu veya takım güncellemeleri sorabilirsin.")

    if intent == "race_winner":
        team = f" ({data['constructor']})" if data.get("constructor") else ""
        return f"{data['year']} {data['track']} GP'sini {data['winner']}{team} kazanmıştır."

    if intent == "pole_position":
        return f"{data['year']} {data['track']} GP'sinde pole pozisyonu {data['pole']}'de idi."

    if intent == "season_champion":
        team = f" ({data['constructor']})" if data.get("constructor") else ""
        return f"{data['year']} Formula 1 Dünya Şampiyonu {data['driver']}{team} olmuştur."

    if intent == "car_upgrades":
        lines = [f"{data['team']} için kayıtlı son teknik güncellemeler:"]
        for u in data["updates"]:
            comps = ", ".join(u.get("components", [])) or "güncelleme"
            goal = f" — {u['goal']}" if u.get("goal") else ""
            lines.append(f"• {u['event']} ({u.get('date', '')}): {comps}{goal}")
        return "\n".join(lines)

    return OUT_OF_SCOPE_REPLY


# ===========================================================================
# TEK GİRİŞ NOKTASI:  parse -> guard -> route -> generate
# ===========================================================================


@dataclass
class AssistantReply:
    text: str
    intent: str
    ok: bool = True
    source: str = "Paddock Asistan · mock çekirdek"
    entities: Dict[str, Any] = field(default_factory=dict)


def ask(text: str) -> AssistantReply:
    """Uçtan uca: bir kullanıcı cümlesi → asistan yanıtı."""
    q = parse_query(text)

    # --- kapsam kapısı: F1 dışı her şeyi kibarca reddet ----------------
    # Selamlaşma, hal-hatır ("Nasılsın?"), hava durumu vb. dâhil — net bir F1
    # sinyali (yıl/pist/takım/pilot ya da F1 anahtar kelimesi) yoksa asistan
    # rolünü hatırlatan tek bir yanıt döner.
    if q.intent == "unknown" and not q.has_f1_signal:
        return AssistantReply(OUT_OF_SCOPE_REPLY, "out_of_scope", ok=False)

    data = route_to_data(q.intent, q.entities)
    reply = generate_response(data)
    ok = data.get("status") == "ok"
    return AssistantReply(reply, q.intent, ok=ok, entities=q.entities)
