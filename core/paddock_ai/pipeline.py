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
import logging

from . import guard, intents, templates
from .entities import EntityExtractor
from .normalize import parse
from .retrievers import careers, history_db, live_data, tech

_log = logging.getLogger(__name__)

try:
    from core.f1_constants import (DRIVER_DISPLAY, F1_WORLD_CHAMPIONS,
                                   TEAM_NAME_ALIASES)
except ImportError:
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


_CODE_STOPWORDS = {"ver", "alo", "law", "ric", "per", "bot", "hul", "col",
                   "sai", "alb", "lin", "gas", "ant", "oco", "str", "bor"}


def _build_extractor() -> EntityExtractor:
    drivers = dict(careers.all_driver_names())          # bundled JSON: tam adlar
    # tarihî isimler (1950+) — modern isimler çakışmada öncelikli kalsın (setdefault)
    try:
        for full in history_db.all_driver_names():
            low = full.lower()
            drivers.setdefault(low, full)
            parts = low.split()
            if len(parts) > 1 and len(parts[-1]) >= 4 and parts[-1].isalpha():
                drivers.setdefault(parts[-1], full)
    except (OSError, ValueError, TypeError) as err:   # DB/veri bozuk -> modern isimlerle devam
        _log.warning("paddock_ai: tarihî pilot adları yüklenemedi (%s)", err)
    for code, entry in DRIVER_DISPLAY.items():
        disp = _display_name(entry)                     # "L. Hamilton"
        surname = disp.split()[-1].lower()
        canonical = next((v for v in drivers.values()
                          if v.lower().split()[-1] == surname), disp)
        drivers[surname] = canonical
        # 3-harf kod ANCAK Türkçe kelimeyle çakışmıyorsa anahtar olur
        if code.lower() not in _CODE_STOPWORDS:
            drivers[code.lower()] = canonical
    teams = {t.lower(): t for t in _CANON_TEAMS_2026}
    try:
        gp_names = history_db.all_race_names()
    except (OSError, ValueError, TypeError) as err:
        _log.warning("paddock_ai: yarış adları yüklenemedi (%s)", err)
        gp_names = []
    return EntityExtractor(driver_names=drivers, team_names=teams,
                           team_aliases=TEAM_NAME_ALIASES, gp_names=gp_names)


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

def _career_for(nm: str) -> dict | None:
    """Görünen ada göre kariyer toplamları: paketli JSON (varsa API kodu ile) ->
    tarih DB'si son çare."""
    surname = nm.lower().split()[-1]
    code = next((c for c in DRIVER_DISPLAY
                 if _name_by_code(c).lower().split()[-1] == surname), None)
    api = None
    try:
        from core.f1_constants import STEWARDLE_ACTIVE_API_IDS_V24
        api = STEWARDLE_ACTIVE_API_IDS_V24.get(code)
    except ImportError:
        pass
    primary = careers.career(nm, api)
    hist = history_db.driver_career(nm)
    res = None
    if primary and hist:                       # eksik alanları tarih DB'sinden tamamla
        for k in ("wins", "podiums", "poles", "starts", "first_season", "last_season"):
            if primary.get(k) is None and hist.get(k) is not None:
                primary[k] = hist[k]
        res = primary
    else:
        res = primary or hist
    if res is not None and res.get("titles") is None:
        tc = history_db.title_count(res.get("name") or nm)
        if tc:
            res["titles"] = tc
    return res


def _retrieve(name, ent, u, live, this_year):
    if name == "SEASON_CHAMPION" and ent.year:
        d = history_db.champion(ent.year)
        return templates.champion(d) if d else None

    if name == "SEASON_CALENDAR" and ent.year:
        d = history_db.season_races(ent.year)
        if not d:
            return None
        if u.has_any("kac yaris", "kac tane", "kac pist"):
            return templates.Answer(
                f"{ent.year} sezonunda {d['count']} Grand Prix düzenlendi.",
                d["source"], "SEASON_CALENDAR")
        return templates.season_calendar(d)

    if name == "SEASON_FIRST_LAST" and ent.year:
        d = history_db.season_races(ent.year)
        if not d:
            return None
        which = "last" if u.has_any("son yaris", "nerede bitti", "kapanis") else "first"
        return templates.season_first_last(d, which)

    if name == "RACE_RESULT":
        # Belirli bir geçmiş yıl istendi -> yalnız tarih DB'si. Başka bir yılın
        # yarışını ASLA yerine koyma.
        if ent.year and ent.year < this_year:
            if not ent.gp:
                return None
            d = history_db.race_result(ent.year, ent.gp, ent.metrics)
            return templates.race_result(d) if d else None
        # yıl yok / bu sezon -> canlı "en son yarışılan" verisi
        frag = ent.gp or ""
        ed = live.last_edition(frag, this_year) if frag else None
        if ed:
            return templates.race_result({
                "season": ed.get("year", this_year), "race": ed.get("event", frag),
                "circuit": ed.get("circuit", ""), "date": "",
                "winner": ed.get("winner"), "pole": ed.get("pole"),
                "podium": ed.get("podium", []), "metrics": ent.metrics,
                "source": ed.get("source", "Hafta Sonu Merkezi · FastF1")})
        if ent.gp:  # tarih DB'si son çare
            d = history_db.race_result(ent.year or this_year, ent.gp, ent.metrics)
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

    if name == "DRIVER_SEASON" and ent.drivers and ent.year and ent.year < this_year:
        d = history_db.driver_season(ent.year, ent.drivers[0])
        if d:
            return templates.driver_season(d)
        # o sezona ait satır yok -> kariyer toplamına düş (uydurma yok)
        c = _career_for(ent.drivers[0])
        return templates.driver_career(c) if c else None

    if name in ("DRIVER_SEASON", "DRIVER_CAREER") and ent.drivers:
        d = _career_for(ent.drivers[0])
        return templates.driver_career(d) if d else None

    if name == "TEAM_TITLES" and ent.team:
        d = history_db.constructor_titles(ent.team)
        return templates.team_titles(d) if d else None

    if name == "HEAD_TO_HEAD" and len(ent.drivers) >= 2:
        da, db = _career_for(ent.drivers[0]), _career_for(ent.drivers[1])
        if da and db:
            return templates.head_to_head(da, db)
        return None

    if name == "TECH_UPGRADE" and ent.team:
        d = tech.latest_for_team(ent.team)
        return templates.tech_upgrade(d) if d else templates.tech_missing(ent.team)

    if name == "NEXT_RACE":
        cal = live.calendar(this_year) or []
        today = datetime.date.today().isoformat()
        upcoming = [ev for ev in cal if str(ev.get("date", ""))[:10] >= today]
        if upcoming:
            return templates.next_race(upcoming[0])
        if cal:  # sezon bitti -> son yarış
            return templates.next_race(cal[-1])
        return None

    if name == "RECORD":
        try:
            from core.f1_constants import F1_RECORD_FACTS_V19
        except ImportError:
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

    cls = intents.classify(u, ent)

    verdict = guard.check(u, has_f1_entity=_has_f1_entity(ent, u), intent=cls.name)
    if verdict.action == "SMALLTALK":
        return templates.Answer(verdict.reply, "Paddock Asistan", "SMALLTALK")
    if verdict.action == "REFUSE":
        return templates.Answer(verdict.reply, "Paddock Asistan", "REFUSE", ok=False)

    try:
        out = _retrieve(cls.name, ent, u, live, this_year)
    except Exception:   # retriever hatası kullanıcıya çökme değil "veri yok" olarak yansır
        _log.exception("paddock_ai retrieve başarısız: intent=%s soru=%r", cls.name, question)
        out = None
    if out is not None:
        out.intent = out.intent or cls.name
        return out

    if cls.name != "FALLBACK":
        return templates.Answer(templates.NO_DATA, "Paddock Asistan", cls.name, ok=False)
    return templates.fallback()
