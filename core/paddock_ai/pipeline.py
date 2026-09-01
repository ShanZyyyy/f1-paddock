"""Asistan hattı (orchestrator).

  ham metin
    -> normalize.parse            (katman 1)
    -> EntityExtractor.extract    (katman 2)
    -> guard.check                (katman 0 — F1 mi / selam mı / red mi)
    -> intents.classify           (katman 3)
    -> retrieve()                 (katman 4 — niyet -> veri kaynağı)
    -> templates.*                (katman 5 — veri -> Türkçe cümle)
    -> Answer

Tek dışa bağımlılık: güncel sezon için enjekte edilen `LiveData`. Diğer her şey
repoda paketli SQLite/JSON.
"""
from __future__ import annotations

import datetime

from . import guard, intents, templates
from .entities import EntityExtractor
from .normalize import parse
from .retrievers import careers, history_db, live_data, tech

try:
    from core.f1_constants import (DRIVER_DISPLAY, F1_WORLD_CHAMPIONS,
                                   TEAM_NAME_ALIASES)
except Exception:
    DRIVER_DISPLAY, F1_WORLD_CHAMPIONS, TEAM_NAME_ALIASES = {}, {}, {}

_CANON_TEAMS_2026 = ("Red Bull Racing", "Ferrari", "Mercedes", "McLaren",
                     "Aston Martin", "Alpine", "Williams", "Racing Bulls",
                     "Haas F1 Team", "Audi", "Cadillac F1 Team")


def _name_by_code(code: str) -> str:
    entry = DRIVER_DISPLAY.get(str(code or "").upper())
    if isinstance(entry, (list, tuple)):
        return str(entry[-1])
    return str(entry or code)


def _display_name(entry) -> str:
    """DRIVER_DISPLAY değeri ('gb', 'L. Hamilton') gibi — görünen adı çek."""
    if isinstance(entry, (list, tuple)):
        return str(entry[-1])
    return str(entry)


def _build_extractor() -> EntityExtractor:
    drivers = dict(careers.all_driver_names())          # bundled JSON: tam adlar
    for code, entry in DRIVER_DISPLAY.items():
        disp = _display_name(entry)                     # "L. Hamilton"
        # bundled adla eşleştir (soyad üzerinden), yoksa kısa adı kullan
        surname = disp.split()[-1].lower()
        canonical = next((v for v in drivers.values()
                          if v.lower().split()[-1] == surname), disp)
        drivers[code.lower()] = canonical
        drivers[surname] = canonical
    teams = {t.lower(): t for t in _CANON_TEAMS_2026}
    return EntityExtractor(driver_names=drivers, team_names=teams,
                           team_aliases=TEAM_NAME_ALIASES)


_EXTRACTOR: EntityExtractor | None = None


def _extractor() -> EntityExtractor:
    global _EXTRACTOR
    if _EXTRACTOR is None:
        _EXTRACTOR = _build_extractor()
    return _EXTRACTOR


def _has_f1_entity(ent, u) -> bool:
    if ent.drivers or ent.team or ent.gp:
        return True
    if ent.year and ent.year in F1_WORLD_CHAMPIONS:
        return True
    return False


# --------------------------------------------------------------------------

def _retrieve(name, ent, u, live, this_year):
    if name == "SEASON_CHAMPION" and ent.year:
        d = history_db.champion(ent.year)
        return templates.champion(d) if d else None

    if name == "RACE_RESULT":
        year = ent.year or this_year
        if ent.gp and year < this_year:
            d = history_db.race_result(year, ent.gp, ent.metrics)
            if d:
                return templates.race_result(d)
        # güncel/son yarış -> canlı veri
        frag = ent.gp or ""
        ed = live.last_edition(frag, this_year) if frag else None
        if ed:
            return templates.race_result({
                "season": ed.get("year", year), "race": ed.get("event", frag),
                "circuit": ed.get("circuit", ""), "date": "",
                "winner": ed.get("winner"), "pole": ed.get("pole"),
                "podium": ed.get("podium", []), "metrics": ent.metrics,
                "source": ed.get("source", "Hafta Sonu Merkezi · FastF1")})
        # geçmiş DB'yi son çare dene
        if ent.gp:
            d = history_db.race_result(year, ent.gp, ent.metrics)
            if d:
                return templates.race_result(d)
        return None

    if name == "STANDINGS":
        rows = live.championship(ent.year or this_year)
        if rows:
            named = [{"name": _name_by_code(r["code"]),
                      "points": r["points"], "position": r.get("position")} for r in rows]
            return templates.standings(named, ent.year or this_year)
        return None

    if name in ("DRIVER_SEASON", "DRIVER_CAREER") and ent.drivers:
        nm = ent.drivers[0]
        surname = nm.lower().split()[-1]
        code = next((c for c in DRIVER_DISPLAY
                     if _name_by_code(c).lower().split()[-1] == surname), None)
        api = None
        try:
            from core.f1_constants import STEWARDLE_ACTIVE_API_IDS_V24
            api = STEWARDLE_ACTIVE_API_IDS_V24.get(code)
        except Exception:
            pass
        d = careers.career(nm, api) or history_db.driver_career(nm)
        return templates.driver_career(d) if d else None

    if name == "TECH_UPGRADE" and ent.team:
        d = tech.latest_for_team(ent.team)
        return templates.tech_upgrade(d) if d else templates.tech_missing(ent.team)

    if name == "NEXT_RACE":
        cal = live.calendar(this_year) or []
        now = datetime.datetime.now(datetime.timezone.utc)
        for ev in cal:
            try:
                dt = datetime.datetime.fromisoformat(str(ev["date"]).replace("Z", "+00:00"))
            except (ValueError, KeyError):
                continue
            if dt > now:
                return templates.next_race(ev)
        return None

    if name == "RECORD":
        try:
            from core.f1_constants import F1_RECORD_FACTS_V19
        except Exception:
            F1_RECORD_FACTS_V19 = {}
        t = u.text
        if "genc" in t and "sampiyon" in t:
            return templates.record(F1_RECORD_FACTS_V19.get("youngest_champion", ""))
        if "pole" in t:
            return templates.record(F1_RECORD_FACTS_V19.get("most_poles", ""))
        if "sezonda" in t and ("galibiyet" in t or "kazan" in t):
            return templates.record(F1_RECORD_FACTS_V19.get("most_wins_single_season", ""))
        if "sampiyon" in t:
            return templates.record(F1_RECORD_FACTS_V19.get("most_titles", ""))
        if "galibiyet" in t or "kazan" in t:
            return templates.record(F1_RECORD_FACTS_V19.get("most_wins", ""))
        return None

    if name == "USER_STATS":
        return templates.user_stats(live.user_prediction())

    return None


def answer(question: str, *, live: live_data.LiveData = live_data.NULL,
           this_year: int | None = None) -> templates.Answer:
    this_year = this_year or datetime.datetime.now(datetime.timezone.utc).year
    u = parse(question)
    ent = _extractor().extract(u)

    verdict = guard.check(u, has_f1_entity=_has_f1_entity(ent, u))
    if verdict.action == "SMALLTALK":
        return templates.Answer(verdict.reply, "Paddock Asistan", "SMALLTALK")
    if verdict.action == "REFUSE":
        return templates.Answer(verdict.reply, "Paddock Asistan", "REFUSE", ok=False)

    cls = intents.classify(u, ent)
    try:
        out = _retrieve(cls.name, ent, u, live, this_year)
    except Exception:
        out = None
    if out is not None:
        out.intent = out.intent or cls.name
        return out

    if cls.name != "FALLBACK":
        return templates.Answer(templates.NO_DATA, "Paddock Asistan", cls.name, ok=False)
    return templates.fallback()
