# -*- coding: utf-8 -*-
"""Haftalık skor tablosu — Supabase (PostgREST) üzerinden.

Streamlit Community Cloud'un kalıcı deposu yok; haftalık XP'yi paylaşmak için
dışarıda bir yer gerekiyor. Supabase'in ücretsiz katmanı + `anon` anahtarıyla
tek tablo (`weekly_scores`) yeter.

Kimlik yok: her tarayıcıya rastgele bir `player_id` + kullanıcının seçtiği bir
takma ad. `anon` anahtarı zaten herkese açıktır (ön yüz anahtarı); yazma/okuma
neyi kapsayacağı Supabase RLS politikalarıyla sınırlanır (bkz. `docs/`).

Ağ hatası = exception (sentinel DÖNMEZ) — çağıran taraf `cache_data_safe` ile
sarar, geçici blip TTL boyunca donmaz.
"""
from __future__ import annotations

import datetime as _dt

import requests

_TABLE = "weekly_scores"
_TIMEOUT = 6


def _creds():
    """(base_url, anon_key) ya da yapılandırılmamışsa None. Streamlit Secrets
    (`SUPABASE_URL` / `SUPABASE_ANON_KEY`) ya da ortam değişkeni."""
    url = key = ""
    try:
        import streamlit as st
        url = str(st.secrets.get("SUPABASE_URL", "") or "").strip()
        key = str(st.secrets.get("SUPABASE_ANON_KEY", "") or "").strip()
    except Exception:
        pass
    if not url or not key:
        import os
        url = url or os.getenv("SUPABASE_URL", "").strip()
        key = key or os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        return None
    return url.rstrip("/"), key


def configured() -> bool:
    return _creds() is not None


def _headers(key, extra=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def submit_weekly(player_id: str, week: str, name: str, xp: int) -> bool:
    """(player_id, week) satırını ekle/güncelle (upsert). Başarısızlıkta RAISE."""
    creds = _creds()
    if creds is None:
        raise RuntimeError("Supabase yapılandırılmadı")
    base, key = creds
    row = {
        "player_id": str(player_id)[:64],
        "week": str(week)[:16],
        "name": (str(name).strip() or "Anonim")[:32],
        "xp": max(0, int(xp)),
        "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    r = requests.post(
        f"{base}/rest/v1/{_TABLE}",
        headers=_headers(key, {"Prefer": "resolution=merge-duplicates,return=minimal"}),
        json=[row], timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return True


def fetch_weekly(week: str, limit: int = 25) -> list[dict]:
    """O haftanın en yüksek `limit` skoru — xp azalan, eşitlikte önce gönderen.
    Başarısızlıkta RAISE (çağıran `cache_data_safe` ile sarar)."""
    creds = _creds()
    if creds is None:
        raise RuntimeError("Supabase yapılandırılmadı")
    base, key = creds
    r = requests.get(
        f"{base}/rest/v1/{_TABLE}",
        headers=_headers(key),
        params={
            "week": f"eq.{week}",
            "select": "player_id,name,xp,updated_at",
            "order": "xp.desc,updated_at.asc",
            "limit": int(limit),
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def rank_in(rows: list[dict], player_id: str):
    """`fetch_weekly` çıktısında player_id'nin 1-tabanlı sırası, yoksa None."""
    for i, row in enumerate(rows, 1):
        if row.get("player_id") == player_id:
            return i
    return None
