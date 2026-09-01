"""Paketli kariyer verisi (retriever) — ağ yok.

`data/driver_careers_seed.json`  : güncel grid + şampiyonlar, tam profil
`data/cards_deck_v9.json`        : 2018+ herkes (Sıralama Kartları destesi)
`data/stewardle_drivers.json`    : 2010+ isim/takım/ülke/ilk-son sezon
"""
from __future__ import annotations

import json
import os

# core/paddock_ai/retrievers/careers.py -> repo kökü 4 seviye yukarı
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_DIR = os.path.join(_ROOT, "data")


def _load(name: str):
    try:
        with open(os.path.join(_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return None


_careers = (_load("driver_careers_seed.json") or {}).get("drivers", {})
_deck = {c["name"].lower(): c for c in ((_load("cards_deck_v9.json") or {}).get("cards", []))
         if isinstance(c, dict) and c.get("name")}
_stewardle = {r["name"].lower(): r for r in (_load("stewardle_drivers.json") or [])
              if isinstance(r, dict) and r.get("name")}


def career(name: str, api_code: str | None = None) -> dict | None:
    """Görünen ada göre kariyer toplamları. Önce deste (2018+), sonra kariyer
    seed (api_code ile), sonra Stewardle temel bilgisi."""
    key = name.lower()
    if key in _deck:
        c = _deck[key]
        return {"name": c["name"], "team": c.get("team"), "wins": c["wins"],
                "podiums": c["podiums"], "poles": c["poles"], "starts": c["starts"],
                "titles": c["titles"], "ppr": c.get("ppr"),
                "last_season": c.get("last"), "source": "cards_deck_v9.json"}
    if api_code and api_code in _careers:
        p = _careers[api_code]
        return {"name": name, "wins": p.get("wins"), "podiums": p.get("podiums"),
                "poles": p.get("poles"), "starts": p.get("starts"),
                "points": p.get("points"), "first_season": p.get("first_season"),
                "last_season": p.get("last_season"), "teams": p.get("teams"),
                "source": "driver_careers_seed.json"}
    if key in _stewardle:
        r = _stewardle[key]
        return {"name": r["name"], "team": r.get("team"), "nation": r.get("nation"),
                "wins": r.get("wins"), "titles": r.get("titles"),
                "starts": r.get("starts"),
                "first_season": str(r.get("first_gp_date", ""))[:4] or None,
                "last_season": r.get("latest_season"),
                "source": "stewardle_drivers.json"}
    return None


def all_driver_names() -> dict[str, str]:
    """Entity çıkarımı için: {fold-anahtar: görünen ad}. Tam ad + soyad."""
    out: dict[str, str] = {}
    for src in (_deck, _stewardle):
        for key, row in src.items():
            disp = row["name"]
            out[key] = disp
            parts = key.split()
            if len(parts) > 1:
                out[parts[-1]] = disp
    return out
