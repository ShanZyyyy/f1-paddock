# -*- coding: utf-8 -*-
"""core/datahub.py — SWR veri merkezi. Ağ/Streamlit gerektirmez; hızlı ve saf."""

import time

import pytest

from core.datahub import DataHub, SourceSpec


@pytest.fixture()
def hub(tmp_path):
    h = DataHub(data_dir=str(tmp_path))
    # arka plan thread yerine SENKRON çalıştır — testler deterministik olsun
    h._spawn = lambda fn: fn()
    return h


def test_cold_get_blocks_and_returns_value(hub):
    calls = []

    def loader():
        calls.append(1)
        return [{"t": "haber"}]

    hub.register(SourceSpec("news", loader, soft_ttl=10, hard_ttl=100))
    snap = hub.get("news")
    assert snap.ok and snap.value == [{"t": "haber"}]
    assert snap.stale is False
    assert len(calls) == 1


def test_fresh_get_does_not_reload(hub):
    calls = []
    hub.register(SourceSpec("x", lambda: calls.append(1) or [1], soft_ttl=10, hard_ttl=100))
    hub.get("x")
    hub.get("x")
    hub.get("x")
    assert len(calls) == 1


def test_stale_serves_old_value_then_refreshes(hub):
    box = {"n": 0}

    def loader():
        box["n"] += 1
        return [box["n"]]

    hub.register(SourceSpec("c", loader, soft_ttl=0.05, hard_ttl=100))
    first = hub.get("c")
    assert first.value == [1]
    time.sleep(0.06)
    # bayat: eski değeri döndürür ama arka planda (senkron shim) yeniler
    stale = hub.get("c")
    assert stale.stale is True
    assert stale.value == [1]          # bu çağrının döndürdüğü hâlâ eski
    # sonraki çağrı taze
    assert hub.get("c").value == [2]


def test_empty_result_not_cached(hub):
    seq = [[], [], [{"ok": 1}]]

    def loader():
        return seq.pop(0)

    hub.register(SourceSpec("e", loader, soft_ttl=10, hard_ttl=100))
    assert hub.get("e").ok is False          # 1. boş
    assert hub.get("e").ok is False          # 2. boş, hâlâ bloke edip dener
    got = hub.get("e")                        # 3. dolu
    assert got.ok and got.value == [{"ok": 1}]


def test_loader_exception_is_swallowed(hub):
    hub.register(SourceSpec("boom", lambda: (_ for _ in ()).throw(RuntimeError("net down")),
                            soft_ttl=10, hard_ttl=100))
    snap = hub.get("boom")
    assert snap.ok is False
    assert "net down" in (snap.error or "")


def test_stale_value_survives_later_failure(hub):
    state = {"fail": False}

    def loader():
        if state["fail"]:
            raise RuntimeError("kaynak koptu")
        return [42]

    hub.register(SourceSpec("s", loader, soft_ttl=0.05, hard_ttl=100))
    assert hub.get("s").value == [42]
    state["fail"] = True
    time.sleep(0.06)
    snap = hub.get("s")                       # yenileme patlar ama eski değer kalır
    assert snap.value == [42]
    assert snap.stale is True


def test_disk_persistence_across_instances(tmp_path):
    h1 = DataHub(data_dir=str(tmp_path))
    h1._spawn = lambda fn: fn()
    h1.register(SourceSpec("p", lambda: {"k": "v"}, soft_ttl=10, hard_ttl=100, empty_is_valid=False))
    h1.get("p")

    # yeni süreç simülasyonu: taze DataHub, aynı disk
    h2 = DataHub(data_dir=str(tmp_path))
    h2._spawn = lambda fn: fn()
    loaded = []
    h2.register(SourceSpec("p", lambda: loaded.append(1) or {"k": "v2"}, soft_ttl=10, hard_ttl=100))
    snap = h2.get("p")
    assert snap.value == {"k": "v"}           # diskten geldi
    assert loaded == []                        # loader hiç çağrılmadı


def test_hard_ttl_forces_sync_reload_from_disk(tmp_path):
    h1 = DataHub(data_dir=str(tmp_path))
    h1._spawn = lambda fn: fn()
    h1.register(SourceSpec("h", lambda: [1], soft_ttl=10, hard_ttl=10))
    h1.get("h")

    h2 = DataHub(data_dir=str(tmp_path))
    h2._spawn = lambda fn: fn()
    # disk snapshot'ını çok eskiye çek
    import json
    p = h2._disk_path("h")
    with open(p, encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["fetched_at"] = time.time() - 9999
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    calls = []
    h2.register(SourceSpec("h", lambda: calls.append(1) or [2], soft_ttl=10, hard_ttl=10))
    snap = h2.get("h")
    assert snap.value == [2] and len(calls) == 1   # hard_ttl aşıldı → senkron yenileme


def test_args_get_separate_slots(hub):
    hub.register(SourceSpec("q", lambda limit=10: list(range(limit)), soft_ttl=10, hard_ttl=100))
    assert hub.get("q", limit=3).value == [0, 1, 2]
    assert hub.get("q", limit=5).value == [0, 1, 2, 3, 4]


def test_peek_does_not_trigger_load(hub):
    calls = []
    hub.register(SourceSpec("k", lambda: calls.append(1) or [1], soft_ttl=10, hard_ttl=100))
    snap = hub.peek("k")
    assert snap.ok is False and calls == []


def test_prewarm_non_blocking_when_warm(hub):
    box = {"n": 0}
    hub.register(SourceSpec("w", lambda: box.__setitem__("n", box["n"] + 1) or [box["n"]],
                            soft_ttl=0.05, hard_ttl=100))
    hub.get("w")
    time.sleep(0.06)
    hub.prewarm("w")                          # senkron shim ile yeniler
    assert box["n"] == 2


def test_prewarm_cold_does_not_block(tmp_path):
    """Soğuk başlangıçta bile prewarm/block_if_cold=False çağıran'ı bekletmez."""
    import threading as _t
    h = DataHub(data_dir=str(tmp_path))
    gate = _t.Event()

    def slow_loader():
        gate.wait(timeout=2)
        return [1]

    h.register(SourceSpec("cold", slow_loader, soft_ttl=10, hard_ttl=100))
    t0 = time.time()
    snap = h.get("cold", block_if_cold=False)   # gerçek thread; beklememeli
    assert time.time() - t0 < 0.5
    assert snap.ok is False                      # henüz veri yok
    gate.set()
    # arka plan thread'i bitince veri gelir
    for _ in range(40):
        if h.peek("cold").ok:
            break
        time.sleep(0.05)
    assert h.get("cold").value == [1]


def test_unregistered_key_raises(hub):
    with pytest.raises(KeyError):
        hub.get("nope")
