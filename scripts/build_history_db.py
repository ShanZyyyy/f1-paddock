"""data/f1_history.sqlite üretici — 1950'den bugüne yarış sonuçları.

Ergast/Jolpica (ücretsiz, anahtarsız) tarih arşivinden bir kez çeker, SQLite'a
yazar, repoya paketlenir (~1-2 MB). Asistan bu dosyayı salt-okunur açar ve
"1967 Monako GP'sini kim kazandı" gibi soruları saf SQL ile yanıtlar.

    .venv/Scripts/python.exe scripts/build_history_db.py            # tüm arşiv
    .venv/Scripts/python.exe scripts/build_history_db.py 1990 2010  # aralık

Jolpica yavaş (sezon başına ~1-2 sn + sayfalama). Kesintide kaldığı yerden
devam etmek için var olan tabloyu okuyup eksik sezonları tamamlar.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "f1_history.sqlite")
BASE = "https://api.jolpi.ca/ergast/f1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS races(
  season INTEGER, round INTEGER, name TEXT, circuit TEXT, country TEXT, date TEXT,
  PRIMARY KEY(season, round));
CREATE TABLE IF NOT EXISTS results(
  season INTEGER, round INTEGER, position INTEGER, driver TEXT, code TEXT,
  constructor TEXT, grid INTEGER, status TEXT, points REAL, fastest_lap_rank INTEGER);
CREATE TABLE IF NOT EXISTS qualifying(
  season INTEGER, round INTEGER, position INTEGER, driver TEXT, code TEXT,
  q1 TEXT, q2 TEXT, q3 TEXT);
CREATE TABLE IF NOT EXISTS champions(
  season INTEGER PRIMARY KEY, driver TEXT, constructor TEXT);
CREATE INDEX IF NOT EXISTS ix_res ON results(season, round);
CREATE INDEX IF NOT EXISTS ix_res_drv ON results(driver);
CREATE INDEX IF NOT EXISTS ix_race_name ON races(season, name);
"""


def _get(path: str, limit: int = 100) -> dict:
    url = f"{BASE}/{path}.json?limit={limit}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FormulaPaddock/hist"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {}


def _num(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


def _driver_name(d: dict) -> str:
    return f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()


def ingest_season(conn: sqlite3.Connection, season: int) -> int:
    data = _get(f"{season}/results", limit=1000)
    table = (data.get("MRData", {}).get("RaceTable", {}).get("Races", []))
    n = 0
    for race in table:
        rnd = _num(race.get("round"))
        circ = race.get("Circuit", {})
        conn.execute(
            "INSERT OR REPLACE INTO races VALUES (?,?,?,?,?,?)",
            (season, rnd, race.get("raceName"), circ.get("circuitName"),
             (circ.get("Location", {}) or {}).get("country"), race.get("date")))
        for res in race.get("Results", []):
            drv = res.get("Driver", {})
            fl = (res.get("FastestLap", {}) or {}).get("rank")
            conn.execute(
                "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?)",
                (season, rnd, _num(res.get("position")), _driver_name(drv),
                 (drv.get("code") or "").upper() or None,
                 (res.get("Constructor", {}) or {}).get("name"),
                 _num(res.get("grid")), res.get("status"),
                 _num(res.get("points"), 0.0), _num(fl)))
        n += 1

    # sıralama (Ergast'ta 2003+)
    qd = _get(f"{season}/qualifying", limit=1000)
    for race in qd.get("MRData", {}).get("RaceTable", {}).get("Races", []):
        rnd = _num(race.get("round"))
        for q in race.get("QualifyingResults", []):
            drv = q.get("Driver", {})
            conn.execute(
                "INSERT INTO qualifying VALUES (?,?,?,?,?,?,?,?)",
                (season, rnd, _num(q.get("position")), _driver_name(drv),
                 (drv.get("code") or "").upper() or None,
                 q.get("Q1"), q.get("Q2"), q.get("Q3")))

    # şampiyon
    cd = _get(f"{season}/driverStandings", limit=1)
    lists = cd.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
    if lists and lists[0].get("DriverStandings"):
        top = lists[0]["DriverStandings"][0]
        cons = (top.get("Constructors") or [{}])[0].get("name")
        conn.execute("INSERT OR REPLACE INTO champions VALUES (?,?,?)",
                     (season, _driver_name(top.get("Driver", {})), cons))
    return n


def main() -> int:
    lo, hi = 1950, time.gmtime().tm_year
    if len(sys.argv) == 3:
        lo, hi = int(sys.argv[1]), int(sys.argv[2])

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    done = {r[0] for r in conn.execute("SELECT DISTINCT season FROM races")}

    for season in range(lo, hi + 1):
        if season in done:
            print(f"  {season}: atlandı (zaten var)")
            continue
        t = time.time()
        try:
            races = ingest_season(conn, season)
            conn.commit()
            print(f"  {season}: {races} yarış  ({time.time() - t:.0f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {season}: HATA {exc}")
    conn.execute("VACUUM")
    conn.close()
    size = os.path.getsize(DB) / 1024
    print(f"bitti -> {DB} ({size:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
