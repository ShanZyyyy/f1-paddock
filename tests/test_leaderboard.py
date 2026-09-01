# -*- coding: utf-8 -*-
"""core.leaderboard — ağsız birim testleri (saf fonksiyonlar + yapılandırma kapısı)."""
import pytest

from core import leaderboard as lb


def test_rank_in_finds_and_misses():
    rows = [
        {"player_id": "p_a", "xp": 90},
        {"player_id": "p_b", "xp": 40},
        {"player_id": "p_c", "xp": 10},
    ]
    assert lb.rank_in(rows, "p_a") == 1
    assert lb.rank_in(rows, "p_c") == 3
    assert lb.rank_in(rows, "p_zzz") is None
    assert lb.rank_in([], "p_a") is None


def test_unconfigured_is_honest(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    # st.secrets erişimi test ortamında patlar -> _creds() None döner
    assert lb.configured() is False
    with pytest.raises(RuntimeError):
        lb.submit_weekly("p_x", "2026-H36", "Test", 10)
    with pytest.raises(RuntimeError):
        lb.fetch_weekly("2026-H36")


def test_env_creds_pick_up(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://demo.supabase.co/")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key-123")
    assert lb.configured() is True
    base, key = lb._creds()
    assert base == "https://demo.supabase.co"   # sondaki / kırpılır
    assert key == "anon-key-123"
