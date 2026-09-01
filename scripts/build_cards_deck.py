"""Rebuild data/cards_deck_v9.json — the Sıralama Kartları (Top Trumps) deck.

Every driver who raced in 2018 or later, with full verified career totals
(wins / podiums / poles / starts / points) aggregated from Jolpica. Titles and
the display team come from the bundled Stewardle database.

    .venv/Scripts/python.exe scripts/build_cards_deck.py

Slow (Jolpica is ~5-8 s per driver). Drivers with no verified rows are skipped
and simply won't appear in the deck. The old deck file stays untouched until a
successful run replaces it, so a failed run is safe.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit_app as app  # noqa: E402


def short_code(api, name):
    inv = {v: k for k, v in app.STEWARDLE_ACTIVE_API_IDS_V24.items()}
    if api in inv:
        return inv[api]
    parts = [p for p in str(name).replace('.', ' ').split() if p]
    surname = parts[-1] if parts else api
    return surname[:3].upper()


def main():
    rows = app._load_stewarlde_database_v29()
    roster = [r for r in rows
              if int(app._career_number_v27(r.get('latest_season')) or 0) >= 2018]
    print(f"{len(roster)} sürücü (2018+) — kariyer verisi çekiliyor…", flush=True)

    cards, failed = [], []
    for r in roster:
        api = str(r.get('api_code', '')).strip()
        if not api:
            continue
        started = time.time()
        try:
            prof = app._driver_full_profile_raw_v33.__wrapped__(api)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{api} ({exc})")
            print(f"  SKIP {api}", flush=True)
            continue
        if not prof.get('ok'):
            failed.append(api)
            print(f"  SKIP {api} (no rows)", flush=True)
            continue
        starts = int(prof.get('starts') or 0)
        if starts < 5:
            failed.append(f"{api} (<5 starts)")
            continue
        cards.append({
            'code': short_code(api, r.get('name', api)),
            'name': str(r.get('name') or api.title()),
            'team': str(r.get('team') or (prof.get('teams') or [''])[-1] or ''),
            'last': int(app._career_number_v27(prof.get('last_season'))
                        or r.get('latest_season') or 0),
            'wins': int(prof.get('wins') or 0),
            'podiums': int(prof.get('podiums') or 0),
            'poles': int(prof.get('poles') or 0),
            'starts': starts,
            'titles': int(app._career_number_v27(r.get('titles')) or 0),
            'ppr': round(float(prof.get('points') or 0) / max(1, starts), 1),
        })
        print(f"  ok {api:16s} W{cards[-1]['wins']:>3} Pod{cards[-1]['podiums']:>3} "
              f"Pole{cards[-1]['poles']:>3} St{starts:>3}  ({time.time() - started:.0f}s)",
              flush=True)

    if len(cards) < 12:
        print(f"YETERSİZ: yalnız {len(cards)} kart — dosya güncellenmedi.")
        return 1
    cards.sort(key=lambda c: -c['starts'])
    os.makedirs(os.path.dirname(app._CARDS_DECK_FILE), exist_ok=True)
    with open(app._CARDS_DECK_FILE, 'w', encoding='utf-8') as handle:
        json.dump({'built': time.time(), 'count': len(cards), 'cards': cards},
                  handle, ensure_ascii=False, separators=(',', ':'))
    size = os.path.getsize(app._CARDS_DECK_FILE) / 1024
    print(f"\nbitti: {len(cards)} kart, {size:.0f} KB -> {app._CARDS_DECK_FILE}")
    if failed:
        print(f"atlandı ({len(failed)}): {', '.join(map(str, failed))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
