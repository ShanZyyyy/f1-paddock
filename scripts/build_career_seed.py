"""Rebuild data/driver_careers_seed.json — the offline fallback for driver
career profiles (Pilotlar / Karşılaştır / "Bu Pistte").

Run before a deploy, or whenever the Jolpica/Ergast API has recovered after
an outage:

    .venv/Scripts/python.exe scripts/build_career_seed.py

It fetches the full career record for the current 2026 grid plus a handful
of title-winning legends, aggregates each into the same shape the app uses
at runtime, and writes the seed file. Takes a few minutes (the API is slow).
Drivers with no verified career rows yet (rookies) are reported as failed
and simply fall through to the live path at runtime.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit_app as app  # noqa: E402


def main():
    started = time.time()
    result = app._build_career_seed_v45()
    print(f"seed rebuilt in {time.time() - started:.0f}s")
    print(f"  written: {result['written']} drivers -> {app._CAREER_SEED_FILE}")
    if result['failed']:
        print(f"  no career rows (left to live path): {', '.join(result['failed'])}")
    return 0 if result['written'] else 1


if __name__ == "__main__":
    raise SystemExit(main())
