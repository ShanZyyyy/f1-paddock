"""Tarihî F1 veritabanı erişimi (retriever).

`data/f1_history.sqlite` — 1950'den bugüne yarışlar, sonuçlar, şampiyonlar.
`scripts/build_history_db.py` Ergast/Jolpica'dan bir kez üretir; repoda paketlenir
(~1-2 MB). Dosya yoksa şampiyon sorusu `F1_WORLD_CHAMPIONS` sözlüğüne düşer.

Şema:
  races(season, round, name, circuit, country, date)                PRIMARY KEY(season, round)
  results(season, round, position, driver, code, constructor,
          grid, status, points, fastest_lap_rank)
  qualifying(season, round, position, driver, code, q1, q2, q3)
  champions(season, driver, constructor)                            PRIMARY KEY(season)
"""
from __future__ import annotations

import os
import sqlite3

try:
    from core.f1_constants import F1_WORLD_CHAMPIONS
except Exception:  # bağımsız kullanımda
    F1_WORLD_CHAMPIONS = {}

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "data", "f1_history.sqlite")

_conn: sqlite3.Connection | None = None
_checked = False


def _db() -> sqlite3.Connection | None:
    global _conn, _checked
    if _checked:
        return _conn
    _checked = True
    if os.path.exists(_DB_PATH):
        try:
            _conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True,
                                    check_same_thread=False)
            _conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            _conn = None
    return _conn


def available() -> bool:
    return _db() is not None


# -- sorgular -----------------------------------------------------------------

def champion(season: int) -> dict | None:
    conn = _db()
    if conn is not None:
        row = conn.execute(
            "SELECT driver, constructor FROM champions WHERE season = ?", (season,)
        ).fetchone()
        if row:
            return {"season": season, "driver": row["driver"],
                    "constructor": row["constructor"], "source": "f1_history.sqlite"}
    name = F1_WORLD_CHAMPIONS.get(season)
    if name:
        return {"season": season, "driver": name, "constructor": None,
                "source": "yerel şampiyon arşivi"}
    return None


def _find_round(conn, season: int, gp_fragment: str) -> sqlite3.Row | None:
    like = f"%{gp_fragment}%"
    return conn.execute(
        "SELECT * FROM races WHERE season = ? AND "
        "(name LIKE ? OR circuit LIKE ? OR country LIKE ?) ORDER BY round LIMIT 1",
        (season, like, like, like),
    ).fetchone()


def race_result(season: int, gp_fragment: str, metrics: list[str]) -> dict | None:
    """Belirli bir yarışın kazananı / pole'u / podyumu.
    `metrics` boşsa kazanan + pole + podyum hepsi döner."""
    conn = _db()
    if conn is None:
        return None
    race = _find_round(conn, season, gp_fragment)
    if race is None:
        return None
    rnd = race["round"]
    res = conn.execute(
        "SELECT position, driver, code, constructor, grid, status, fastest_lap_rank "
        "FROM results WHERE season = ? AND round = ? ORDER BY position", (season, rnd)
    ).fetchall()
    if not res:
        return None
    by_pos = {r["position"]: r for r in res if r["position"]}
    winner = by_pos.get(1)
    pole_row = next((r for r in res if r["grid"] == 1), None)
    # Sıralama tablosu varsa pole'u oradan doğrula (2006 sonrası daha güvenilir)
    q = conn.execute(
        "SELECT driver, code FROM qualifying WHERE season = ? AND round = ? AND position = 1",
        (season, rnd),
    ).fetchone()
    pole = {"driver": q["driver"], "code": q["code"]} if q else (
        {"driver": pole_row["driver"], "code": pole_row["code"]} if pole_row else None)
    podium = [{"position": by_pos[p]["driver"], "code": by_pos[p]["code"]}
              for p in (1, 2, 3) if p in by_pos]
    return {
        "season": season, "race": race["name"], "circuit": race["circuit"],
        "date": race["date"],
        "winner": {"driver": winner["driver"], "code": winner["code"],
                   "constructor": winner["constructor"]} if winner else None,
        "pole": pole,
        "podium": podium,
        "metrics": metrics,
        "source": "f1_history.sqlite",
    }


def season_races(season: int) -> dict | None:
    """Bir sezonun tüm yarışları (sıralı). 'takvim', 'kaç yarış', 'ilk/son yarış'."""
    conn = _db()
    if conn is None:
        return None
    rows = conn.execute(
        "SELECT round, name, circuit, country, date FROM races "
        "WHERE season = ? ORDER BY round", (season,),
    ).fetchall()
    if not rows:
        return None
    races = [{"round": r["round"], "name": r["name"], "circuit": r["circuit"],
              "country": r["country"], "date": r["date"]} for r in rows]
    return {"season": season, "count": len(races), "races": races,
            "first": races[0], "last": races[-1], "source": "f1_history.sqlite"}


def driver_career(name_or_code: str) -> dict | None:
    """Tarihî toplamlar — kariyer seed'inde olmayan eski pilotlar için."""
    conn = _db()
    if conn is None:
        return None
    like = f"%{name_or_code}%"
    row = conn.execute(
        "SELECT driver, "
        "  SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END)  AS wins, "
        "  SUM(CASE WHEN position <= 3 THEN 1 ELSE 0 END) AS podiums, "
        "  SUM(CASE WHEN grid = 1 THEN 1 ELSE 0 END)      AS poles, "
        "  COUNT(*) AS starts, "
        "  MIN(season) AS first_season, MAX(season) AS last_season "
        "FROM results WHERE driver LIKE ? OR code = ? GROUP BY driver "
        "ORDER BY starts DESC LIMIT 1",
        (like, name_or_code.upper()),
    ).fetchone()
    if not row or not row["starts"]:
        return None
    return {k: row[k] for k in row.keys()} | {"source": "f1_history.sqlite"}
