"""Teknik güncelleme verisi (retriever).

FastF1'de araç geliştirme / upgrade bilgisi YOKTUR ve ücretsiz bir API de yoktur
(FIA her hafta sonu PDF yayımlar). Bu yüzden `data/tech_upgrades.json` **elle
tutulan** bir veri kümesidir — her yarış sonrası birkaç satır eklenir.

Şema (liste):
  {
    "team": "Aston Martin",          # core.f1_constants kanonik takım adı
    "season": 2026,
    "event": "Spanish Grand Prix",
    "date": "2026-05-31",
    "type": "aero",                  # aero | floor | suspension | power_unit | reliability | cooling
    "components": ["yeni zemin kenarı", "revize edilmiş difüzör"],
    "goal": "yüksek hızlı virajlarda yere basma",
    "note": "FIA teknik dokümanından"
  }
"""
from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "data", "tech_upgrades.json")

_cache: list[dict] | None = None


def _load() -> list[dict]:
    global _cache
    if _cache is None:
        try:
            with open(_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            _cache = [r for r in raw if isinstance(r, dict) and r.get("team")]
        except (OSError, ValueError, TypeError):
            _cache = []
    return _cache


def latest_for_team(team: str, limit: int = 3) -> dict | None:
    rows = [r for r in _load() if str(r.get("team", "")).lower() == team.lower()]
    if not rows:
        return None
    rows.sort(key=lambda r: (str(r.get("date", "")), str(r.get("event", ""))), reverse=True)
    return {
        "team": team,
        "updates": rows[:limit],
        "source": "tech_upgrades.json (elle tutulan FIA teknik dokümanı özeti)",
    }


def has_data() -> bool:
    return bool(_load())
