"""Rebuild data/games_seed_v68.json — the pre-computed data pack that lets the
mini games (Strateji Duvarı, Podyum Tahmini, Kızgın Tur) open instantly instead
of blocking on a cold FastF1 session load at runtime.

Run before a deploy, or whenever the game pools change:

    .venv/Scripts/python.exe scripts/build_games_seed.py

It walks the curated race pools, computes each entry with the exact same
functions the app uses at runtime (via `.__wrapped__`, bypassing the Streamlit
cache), and writes the ones that pass their quality guards. Entries that fail
are reported and simply fall through to the live FastF1 path at runtime.

The seed file is re-written after every pool, so a partial run still leaves a
usable seed (the missing games just fall back to live). Uses the shared FastF1
cache under ./cache, so a warm cache makes this quick.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit_app as app  # noqa: E402

PAYLOAD = {"built": time.time(), "strat": {}, "podium": {}, "hotlap": {}}


def _flush():
    os.makedirs(os.path.dirname(app._GAMES_SEED_FILE), exist_ok=True)
    with open(app._GAMES_SEED_FILE, "w", encoding="utf-8") as handle:
        json.dump(PAYLOAD, handle, ensure_ascii=False, separators=(",", ":"))


def _run(kind, pool, fn):
    out, failed = {}, []
    for year, gp in pool:
        key = f"{int(year)}|{gp}"
        if key in out:
            continue
        started = time.time()
        try:
            data = fn(year, gp)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{key} ({exc})")
            continue
        if isinstance(data, dict) and data.get("ok"):
            out[key] = data
            print(f"  [{kind}] ok  {key}  ({time.time() - started:.0f}s)", flush=True)
        else:
            failed.append(key)
            print(f"  [{kind}] SKIP {key}", flush=True)
    PAYLOAD[kind] = out
    PAYLOAD["built"] = time.time()
    _flush()
    print(f"  -> {kind}: {len(out)} ok, {len(failed)} atlandı; seed güncellendi", flush=True)
    return out


def main():
    started = time.time()
    _run("strat", app._STRAT_RACES_V67, app._strat_race_model_v67.__wrapped__)
    _run("podium", app._PODIUM_POOL_V67, app._podium_of_race_v67.__wrapped__)
    _run("hotlap", app._HOTLAP_POOL_V68, app._hotlap_quali_v66.__wrapped__)
    total = sum(len(PAYLOAD[k]) for k in ("strat", "podium", "hotlap"))
    size_kb = os.path.getsize(app._GAMES_SEED_FILE) / 1024
    print(f"bitti: {total} giriş, {size_kb:.0f} KB, {time.time() - started:.0f}s -> {app._GAMES_SEED_FILE}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
