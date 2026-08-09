# -*- coding: utf-8 -*-
"""OpenF1 historical fallback for Formula Paddock.

FastF1 remains the primary source.  This module is used only when a completed
session's lap/telemetry package is unavailable in Streamlit Community Cloud.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from functools import lru_cache

import numpy as np
import pandas as pd


API = "https://api.openf1.org/v1"
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def _json(endpoint: str, params: dict | None = None):
    query = urllib.parse.urlencode(params or {})
    url = f"{API}/{endpoint}" + (f"?{query}" if query else "")
    global _LAST_REQUEST
    for attempt in range(5):
        try:
            with _RATE_LOCK:
                wait = 0.72 - (time.monotonic() - _LAST_REQUEST)
                if wait > 0:
                    time.sleep(wait)
                request = urllib.request.Request(url, headers={"User-Agent": "FormulaPaddock/3.1"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                _LAST_REQUEST = time.monotonic()
            return payload if isinstance(payload, list) else []
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2.5 * (attempt + 1))


@lru_cache(maxsize=256)
def _cached(endpoint: str, frozen_params: tuple):
    return _json(endpoint, dict(frozen_params))


def query(endpoint: str, **params):
    return _cached(endpoint, tuple(sorted((str(key), str(value)) for key, value in params.items())))


def _normal(value):
    text = str(value or "").casefold()
    replacements = {
        "grand prix": "", "gp": "", "hungarian": "hungary", "british": "great britain",
        "italian": "italy", "spanish": "spain", "austrian": "austria", "dutch": "netherlands",
        "belgian": "belgium", "mexico city": "mexico", "são paulo": "brazil", "saudi arabian": "saudi arabia",
        "emilia romagna": "imola", "azerbaijan": "baku", "united states": "austin",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return "".join(char for char in text if char.isalnum())


def _session_name(code):
    return {
        "R": "Race", "Q": "Qualifying", "FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
        "S": "Sprint", "SQ": "Sprint Qualifying",
    }.get(str(code).upper(), str(code))


@lru_cache(maxsize=16)
def sessions_for_year(year):
    return query("sessions", year=int(year))


def find_session(year, event_name, session_code):
    wanted_event = _normal(event_name)
    wanted_session = _normal(_session_name(session_code))
    candidates = []
    for item in sessions_for_year(int(year)):
        actual_session = _normal(item.get("session_name"))
        if actual_session != wanted_session:
            continue
        event_fields = [item.get("country_name"), item.get("location"), item.get("circuit_short_name")]
        scores = []
        for field in event_fields:
            actual = _normal(field)
            scores.append(3 if actual == wanted_event else 2 if actual and (actual in wanted_event or wanted_event in actual) else 0)
        if max(scores, default=0):
            candidates.append((max(scores), item))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _seconds(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    return pd.to_datetime(value, utc=True, errors="coerce")


class OpenF1Laps(pd.DataFrame):
    @property
    def _constructor(self):
        return OpenF1Laps

    def pick_drivers(self, driver):
        names = {str(driver)} if isinstance(driver, str) else {str(item) for item in driver}
        return self[self["Driver"].astype(str).isin(names)]

    def pick_fastest(self):
        valid = self.dropna(subset=["LapTime"])
        return None if valid.empty else valid.loc[valid["LapTime"].idxmin()]


class OpenF1LapRecord(dict):
    def __init__(self, session, values, raw):
        super().__init__(values)
        self._session = session
        self._raw = raw

    def get_telemetry(self):
        return telemetry_for_lap(self._session, self._raw)


class OpenF1Session:
    def __init__(self, year, event_name, session_code, metadata, drivers, results, laps, stints):
        self.year = int(year)
        self.event_name = str(event_name)
        self.session_code = str(session_code)
        self.metadata = metadata
        self.session_key = int(metadata["session_key"])
        self.event = pd.Series({"EventName": event_name, "Location": metadata.get("location", "")})
        self._drivers = {int(item["driver_number"]): item for item in drivers if item.get("driver_number") is not None}
        self._results_raw = {int(item["driver_number"]): item for item in results if item.get("driver_number") is not None}
        self._laps_raw = laps
        self._stints = stints
        self.results = self._make_results()
        self.laps = self._make_laps()

    def _compound(self, number, lap_number):
        for stint in self._stints:
            if int(stint.get("driver_number", -1)) != number:
                continue
            start, end = int(stint.get("lap_start") or 0), int(stint.get("lap_end") or 999)
            if start <= lap_number <= end:
                return str(stint.get("compound") or "-").upper(), int(stint.get("stint_number") or 0)
        return "-", 0

    def _make_results(self):
        rows = []
        for number, driver in self._drivers.items():
            result = self._results_raw.get(number, {})
            durations = result.get("duration")
            durations = durations if isinstance(durations, list) else [durations]
            row = {
                "Position": result.get("position"), "Abbreviation": driver.get("name_acronym"),
                "DriverNumber": str(number), "TeamName": driver.get("team_name") or "Formula 1",
            }
            for index, label in enumerate(("Q1", "Q2", "Q3")):
                seconds = _seconds(durations[index]) if index < len(durations) else None
                row[label] = pd.to_timedelta(seconds, unit="s") if seconds else pd.NaT
            rows.append(row)
        return pd.DataFrame(rows)

    def _make_laps(self):
        rows = []
        for raw in self._laps_raw:
            number = int(raw.get("driver_number", -1))
            driver = self._drivers.get(number, {})
            lap_number = int(raw.get("lap_number") or 0)
            duration = _seconds(raw.get("lap_duration"))
            compound, stint = self._compound(number, lap_number)
            rows.append({
                "Driver": driver.get("name_acronym") or str(number), "DriverNumber": str(number),
                "LapNumber": lap_number, "LapTime": pd.to_timedelta(duration, unit="s") if duration else pd.NaT,
                "Sector1Time": pd.to_timedelta(_seconds(raw.get("duration_sector_1")), unit="s") if _seconds(raw.get("duration_sector_1")) else pd.NaT,
                "Sector2Time": pd.to_timedelta(_seconds(raw.get("duration_sector_2")), unit="s") if _seconds(raw.get("duration_sector_2")) else pd.NaT,
                "Sector3Time": pd.to_timedelta(_seconds(raw.get("duration_sector_3")), unit="s") if _seconds(raw.get("duration_sector_3")) else pd.NaT,
                "SpeedI1": raw.get("i1_speed"), "SpeedI2": raw.get("i2_speed"), "SpeedST": raw.get("st_speed"),
                "Compound": compound, "Stint": stint, "DateStart": raw.get("date_start"), "_raw": raw,
            })
        return OpenF1Laps(rows)

    def fastest_lap(self, driver_code, q_sub=None):
        table = self.laps.pick_drivers(driver_code).dropna(subset=["LapTime"])
        if table.empty:
            return None
        target = None
        if q_sub in {"Q1", "Q2", "Q3"} and q_sub in self.results.columns:
            result = self.results[self.results["Abbreviation"] == driver_code]
            if not result.empty and pd.notna(result.iloc[0][q_sub]):
                target = result.iloc[0][q_sub].total_seconds()
        if target:
            index = (table["LapTime"].dt.total_seconds() - target).abs().idxmin()
        else:
            index = table["LapTime"].idxmin()
        row = table.loc[index].to_dict()
        return OpenF1LapRecord(self, row, row["_raw"])

    def get_circuit_info(self):
        raise RuntimeError("OpenF1 circuit corner metadata is not exposed")


@lru_cache(maxsize=64)
def load_session(year, event_name, session_code):
    metadata = find_session(int(year), event_name, session_code)
    if not metadata:
        raise RuntimeError("OpenF1 seans anahtarı bulunamadı")
    key = int(metadata["session_key"])
    drivers = query("drivers", session_key=key)
    results = query("session_result", session_key=key)
    laps = query("laps", session_key=key)
    stints = query("stints", session_key=key)
    if not drivers or not laps:
        raise RuntimeError("OpenF1 tur paketi boş")
    return OpenF1Session(year, event_name, session_code, metadata, drivers, results, laps, stints)


@lru_cache(maxsize=512)
def _telemetry(session_key, driver_number, date_start, lap_duration):
    start = _timestamp(date_start)
    if pd.isna(start):
        raise RuntimeError("Tur başlangıç zamanı yok")
    end = start + pd.to_timedelta(float(lap_duration) + 0.6, unit="s")
    filters = {
        "session_key": int(session_key), "driver_number": int(driver_number),
        "date>": start.isoformat(), "date<": end.isoformat(),
    }
    locations = query("location", **filters)
    car_data = query("car_data", **filters)
    if len(locations) < 30:
        raise RuntimeError("OpenF1 konum örneği yetersiz")
    loc = pd.DataFrame(locations)
    # OpenF1 can mix ISO timestamps with and without fractional seconds in the
    # same response. Pandas 2.x otherwise infers one strict format from the
    # first row and rejects the other valid form.
    loc["Date"] = pd.to_datetime(loc["date"], utc=True, errors="coerce", format="mixed")
    loc = loc.dropna(subset=["Date"])
    loc = loc.sort_values("Date")
    if car_data:
        car = pd.DataFrame(car_data)
        car["Date"] = pd.to_datetime(car["date"], utc=True, errors="coerce", format="mixed")
        car = car.dropna(subset=["Date"])
        merged = pd.merge_asof(loc, car.sort_values("Date"), on="Date", direction="nearest", tolerance=pd.Timedelta("700ms"))
    else:
        merged = loc
    merged["X"] = pd.to_numeric(merged["x"], errors="coerce")
    merged["Y"] = pd.to_numeric(merged["y"], errors="coerce")
    merged = merged.dropna(subset=["X", "Y"]).drop_duplicates("Date")
    step = np.hypot(merged["X"].diff().fillna(0), merged["Y"].diff().fillna(0))
    # OpenF1 coordinates are approximate; filter single-sample jumps before accumulating distance.
    step = step.clip(upper=max(20.0, float(step.quantile(.96)) * 1.8))
    elapsed = (merged["Date"] - merged["Date"].iloc[0]).dt.total_seconds()
    merged["Time"] = pd.to_timedelta(elapsed, unit="s")
    for source, target, default in (("speed", "Speed", np.nan), ("throttle", "Throttle", 0), ("brake", "Brake", 0), ("n_gear", "nGear", 0), ("rpm", "RPM", 0)):
        merged[target] = pd.to_numeric(merged[source], errors="coerce") if source in merged else default
    if merged["Speed"].isna().all():
        delta_t = elapsed.diff().replace(0, np.nan)
        merged["Speed"] = (step / delta_t * 3.6).clip(0, 380).interpolate().fillna(0)
    delta_t = elapsed.diff().clip(lower=0, upper=1.5).fillna(0)
    # Distance is integrated from the measured car speed. OpenF1 X/Y values
    # are excellent for shape but their coordinate scale is not guaranteed to
    # be metres, so accumulating raw XY would produce false 30+ km laps.
    merged["Distance"] = (merged["Speed"].fillna(0).clip(0, 390) / 3.6 * delta_t).cumsum()
    return merged[["Date", "Time", "Distance", "X", "Y", "Speed", "Throttle", "Brake", "nGear", "RPM"]].reset_index(drop=True)


def telemetry_for_lap(session, raw):
    duration = _seconds(raw.get("lap_duration"))
    if not duration:
        raise RuntimeError("Geçerli tur süresi yok")
    return _telemetry(session.session_key, int(raw["driver_number"]), str(raw["date_start"]), duration).copy()


def _profile(driver):
    return {"name": driver.get("full_name") or driver.get("broadcast_name"), "number": str(driver.get("driver_number")),
            "photo": driver.get("headshot_url") or "", "flag": str(driver.get("country_code") or "").lower()}


def build_race_replay(year, event_name):
    try:
        session = load_session(int(year), str(event_name), "R")
        results = session._results_raw
        drivers = session._drivers
        positions_raw = query("position", session_key=session.session_key)
        positions = {}
        for item in positions_raw:
            number = int(item.get("driver_number", -1))
            when = _timestamp(item.get("date"))
            pos = item.get("position")
            if number > 0 and pd.notna(when) and pos is not None:
                positions.setdefault(number, []).append((when, int(pos)))
        for values in positions.values():
            values.sort(key=lambda pair: pair[0])
        grid = {number: values[0][1] for number, values in positions.items() if values}

        valid_laps = session.laps.dropna(subset=["LapTime", "DateStart"])
        if valid_laps.empty:
            return {"ok": False, "reason": "OpenF1 yarış tur paketi boş."}
        reference_row = valid_laps.loc[valid_laps["LapTime"].idxmin()]
        reference = session.fastest_lap(str(reference_row["Driver"]))
        telemetry = reference.get_telemetry()
        track = telemetry[["X", "Y"]].iloc[::max(1, len(telemetry) // 700)].to_numpy(dtype=float).tolist()
        if track:
            track.append(track[0])

        session_start = _timestamp(session.metadata.get("date_start"))
        cars, total_laps, total_seconds = [], 0, 0.0
        for number, driver in drivers.items():
            table = valid_laps[valid_laps["DriverNumber"].astype(str) == str(number)].sort_values("LapNumber")
            if table.empty:
                continue
            result = results.get(number, {})
            timeline = []
            previous_end = 0.0
            previous_position = grid.get(number) or int(result.get("position") or 20)
            for _, row in table.iterrows():
                start_date = _timestamp(row["DateStart"])
                duration = row["LapTime"].total_seconds()
                measured_start = max(0.0, (start_date - session_start).total_seconds())
                # A few OpenF1 events contain overlapping lap_start timestamps
                # around timing-line corrections. Keep the measured order but
                # make the replay clock monotonic so cars never jump backwards.
                start = max(previous_end, measured_start)
                end = start + duration
                actual_position = previous_position
                for when, position in positions.get(number, []):
                    if when <= start_date + pd.to_timedelta(duration, unit="s"):
                        actual_position = position
                    else:
                        break
                timeline.append({"lap": int(row["LapNumber"]), "start": round(start, 3), "end": round(end, 3),
                                 "position": actual_position, "start_position": previous_position,
                                 "compound": str(row.get("Compound") or "-"), "stint": int(row.get("Stint") or 0)})
                previous_end = end
                previous_position = actual_position
                total_laps = max(total_laps, int(row["LapNumber"]))
                total_seconds = max(total_seconds, end)
            if not timeline:
                continue
            final_position = int(result.get("position") or previous_position)
            cars.append({"code": driver.get("name_acronym") or str(number), "team": driver.get("team_name") or "Formula 1",
                         "colour": "#" + str(driver.get("team_colour") or "55c7ff").lstrip("#"), "accent": "#f4f8ff",
                         "profile": _profile(driver), "grid": grid.get(number) or timeline[0]["start_position"],
                         "final_position": final_position, "status": "Finished" if not result.get("dnf") else "DNF",
                         "retired": bool(result.get("dnf")), "laps": timeline, "pit_events": []})
        payload = {"ok": bool(cars and track), "event": str(event_name), "track": track, "cars": cars,
                   "total_laps": total_laps, "total_seconds": round(total_seconds, 2),
                   "overlay": {"sectors": [], "corners": [], "brakes": [], "straights": [],
                               "pit": [{"fraction": .985, "label": "PIT IN"}, {"fraction": .025, "label": "PIT OUT"}]},
                   "replay_source": "OpenF1 tarihî tur, konum, sıra ve stint kayıtları", "version": "openf1-3.1"}
        return payload if payload["ok"] else {"ok": False, "reason": "OpenF1 tekrar paketi tamamlanamadı."}
    except Exception as error:
        return {"ok": False, "reason": f"OpenF1 tekrar paketi hazırlanamadı: {error}"}
