"""Paketli kariyer verisi (retriever) — ağ yok.

`data/driver_careers_seed.json`  : güncel grid + şampiyonlar, tam profil
`data/cards_deck_v9.json`        : 2018+ herkes (Sıralama Kartları destesi)
`data/stewardle_drivers.json`    : 2010+ isim/takım/ülke/ilk-son sezon
"""
from __future__ import annotations

import datetime
import json
import os

_THIS_YEAR = datetime.date.today().year

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


def _nation_for(name_key: str) -> str | None:
    """Uyruğu (İngilizce sıfat, ör. 'Spanish') Stewardle kaydından çek."""
    r = _stewardle.get(name_key)
    return r.get("nation") if r else None


def _is_active(last_season) -> bool:
    try:
        return int(last_season) >= _THIS_YEAR - 1
    except (TypeError, ValueError):
        return False


def career(name: str, api_code: str | None = None) -> dict | None:
    """Görünen ada göre kariyer profili — istatistik + biyografik alanlar
    (uyruk, mevcut/son takım, aktiflik). Önce deste (2018+), sonra kariyer seed
    (api_code ile), sonra Stewardle temel bilgisi."""
    key = name.lower()
    if key in _deck:
        c = _deck[key]
        last = c.get("last")
        return {"name": c["name"], "team": c.get("team"),
                "nation": _nation_for(key), "active": _is_active(last),
                "wins": c["wins"], "podiums": c["podiums"], "poles": c["poles"],
                "starts": c["starts"], "titles": c["titles"], "ppr": c.get("ppr"),
                "last_season": last, "source": "cards_deck_v9.json"}
    if api_code and api_code in _careers:
        p = _careers[api_code]
        teams = p.get("teams") or []
        last_team = teams[-1][0] if teams and isinstance(teams[-1], (list, tuple)) else None
        return {"name": name, "wins": p.get("wins"), "podiums": p.get("podiums"),
                "poles": p.get("poles"), "starts": p.get("starts"),
                "points": p.get("points"), "first_season": p.get("first_season"),
                "last_season": p.get("last_season"), "teams": teams,
                "team": last_team, "nation": _nation_for(key),
                "active": _is_active(p.get("last_season")),
                "source": "driver_careers_seed.json"}
    if key in _stewardle:
        r = _stewardle[key]
        return {"name": r["name"], "team": r.get("team"), "nation": r.get("nation"),
                "wins": r.get("wins"), "titles": r.get("titles"),
                "starts": r.get("starts"),
                "first_season": str(r.get("first_gp_date", ""))[:4] or None,
                "last_season": r.get("latest_season"),
                "active": _is_active(r.get("latest_season")),
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
