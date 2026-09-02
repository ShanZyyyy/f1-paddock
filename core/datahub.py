# -*- coding: utf-8 -*-
"""Veri merkezi — çok kaynaklı ağır veriyi tek kapıdan, taze-iken-yenile (SWR)
mantığıyla servis eder.

Neden
-----
Haber Merkezi, yarış tekrarı ve tarih veritabanı gibi kaynaklar ağır: RSS +
çeviri servisi + FastF1 + SQLite. Bunları her Streamlit yeniden-çalıştırmasında
(veya 15 dk'lık TTL her dolduğunda) SENKRON çekmek arayüzü kilitliyor
("Haber akışı hazırlanıyor..." spinner'ı).

Bu modül **stale-while-revalidate** verir:

* Elde bir anlık görüntü (snapshot) varsa — bayat bile olsa — ANINDA döner.
* Snapshot yumuşak TTL'i geçmişse arka planda bir daemon thread yeniden çeker;
  sonuç diske + belleğe yazılır, bir sonraki rerun tazesini alır.
* Hiç snapshot yoksa (soğuk başlangıç) ya da snapshot sert TTL'den eskiyse
  tek sefer senkron çekilir.
* Anahtar başına tek-uçuş (single-flight) kilidi: eşzamanlı rerun'lar aynı
  kaynağı üst üste çekmez.

Streamlit Community Cloud'da kalıcı servis (Redis) yok; kalıcılık diskteki
JSON snapshot'larıyla sağlanır. Modül framework'ten bağımsızdır (yalnızca
stdlib + threading); Streamlit'e hiç bağlı değildir, bu yüzden saf test edilebilir.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = [
    "Snapshot",
    "SourceSpec",
    "DataHub",
    "hub",
]

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "datahub")


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class Snapshot:
    """Bir kaynağın tek bir okunuşu.

    ``value``      : yükleyicinin döndürdüğü veri (asla exception).
    ``fetched_at`` : verinin üretildiği epoch saniyesi (0 = hiç yok).
    ``stale``      : yumuşak TTL geçti mi (arka planda yenileniyor olabilir).
    ``refreshing`` : şu an bir arka plan yenilemesi uçuşta mı.
    ``age``        : saniye cinsinden yaş.
    ``ok``         : elde gösterilebilir bir değer var mı.
    """

    value: Any
    fetched_at: float = 0.0
    stale: bool = False
    refreshing: bool = False
    error: Optional[str] = None

    @property
    def age(self) -> float:
        if not self.fetched_at:
            return float("inf")
        return max(0.0, _now() - self.fetched_at)

    @property
    def ok(self) -> bool:
        return self.fetched_at > 0 and self.value is not None


@dataclass
class SourceSpec:
    """Bir veri kaynağının tanımı."""

    key: str
    loader: Callable[..., Any]
    soft_ttl: float = 900.0        # bu yaştan sonra arka planda yenile
    hard_ttl: float = 86_400.0     # bu yaştan sonra senkron bloke ederek yenile
    disk: bool = True              # snapshot'ı diske de yaz
    empty_is_valid: bool = False   # loader boş liste/dict dönerse geçerli say

    def is_empty(self, value: Any) -> bool:
        if self.empty_is_valid:
            return value is None
        return value is None or (hasattr(value, "__len__") and len(value) == 0)


@dataclass
class _Entry:
    spec: SourceSpec
    value: Any = None
    fetched_at: float = 0.0
    error: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    refreshing: bool = False
    loaded_from_disk: bool = False


class DataHub:
    """Kaynak kayıtları + SWR okuma mantığı. İş parçacığı güvenli."""

    def __init__(self, data_dir: str = _DATA_DIR) -> None:
        self._data_dir = data_dir
        self._entries: Dict[str, _Entry] = {}
        self._registry_lock = threading.Lock()
        self._spawn = _spawn_daemon  # test'te değiştirilebilir

    # ---- kayıt ---------------------------------------------------------------

    def register(self, spec: SourceSpec) -> None:
        with self._registry_lock:
            existing = self._entries.get(spec.key)
            if existing is not None:
                existing.spec = spec
            else:
                self._entries[spec.key] = _Entry(spec=spec)

    def source(self, key: str, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Dekoratör kısayolu::

            @hub.source("news", soft_ttl=900, hard_ttl=86400, on_error=list)
            def _load_news(limit=30): ...
        """

        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(SourceSpec(key=key, loader=fn, **kwargs))
            return fn

        return _decorator

    # ---- okuma ------------------------------------------------------------

    def get(
        self,
        key: str,
        *args: Any,
        block_if_cold: bool = True,
        **kwargs: Any,
    ) -> Snapshot:
        """Kaynağın son anlık görüntüsünü döndür; gerekiyorsa yenilemeyi tetikle.

        Argümanlar ``loader``'a geçer. Farklı argümanlar farklı snapshot
        dosyalarına yazılır (argüman parmak izi anahtara eklenir).
        """

        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"datahub: '{key}' kayıtlı bir kaynak değil (önce register() çağır)")

        fp = _fingerprint(args, kwargs)
        slot_key = key if not fp else f"{key}::{fp}"
        entry = self._slot(slot_key, entry.spec)

        # bellekte yoksa diskten yükle (süreç yeni başlamış olabilir)
        if entry.fetched_at == 0.0 and entry.spec.disk and not entry.loaded_from_disk:
            self._hydrate_from_disk(slot_key, entry)

        spec = entry.spec
        age = (_now() - entry.fetched_at) if entry.fetched_at else float("inf")
        has_value = entry.fetched_at > 0 and not spec.is_empty(entry.value)

        # 1) taze veri elde → dön
        if has_value and age < spec.soft_ttl:
            return self._snapshot(entry, stale=False)

        # 2) bayat ama gösterilebilir veri elde → önce eldekini dön, sonra yenile.
        #    Snapshot yenilemeden ÖNCE alınır: çağıran her hâlükârda bayat değeri
        #    görür (yenileme senkron bitse bile), tazesi sonraki tur'a kalır.
        if has_value and age < spec.hard_ttl:
            snap = self._snapshot(entry, stale=True)
            self._kick_refresh(slot_key, entry, args, kwargs)
            return snap

        # 3) hiç veri yok ya da çok eski
        if not block_if_cold and has_value:
            snap = self._snapshot(entry, stale=True)
            self._kick_refresh(slot_key, entry, args, kwargs)
            return snap

        self._refresh_sync(slot_key, entry, args, kwargs)
        return self._snapshot(entry, stale=False)

    def peek(self, key: str, *args: Any, **kwargs: Any) -> Snapshot:
        """Yenileme TETİKLEMEDEN eldeki snapshot'ı döndür (hata ayıklama/HUD rozeti)."""
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(key)
        fp = _fingerprint(args, kwargs)
        slot_key = key if not fp else f"{key}::{fp}"
        slot = self._slot(slot_key, entry.spec)
        if slot.fetched_at == 0.0 and slot.spec.disk and not slot.loaded_from_disk:
            self._hydrate_from_disk(slot_key, slot)
        return self._snapshot(slot, stale=bool(slot.fetched_at) and (_now() - slot.fetched_at) >= slot.spec.soft_ttl)

    def invalidate(self, key: str, *args: Any, **kwargs: Any) -> None:
        """Snapshot'ı düşür — bir sonraki get() yeniden çeker."""
        fp = _fingerprint(args, kwargs)
        slot_key = key if not fp else f"{key}::{fp}"
        with self._registry_lock:
            self._entries.pop(slot_key, None)
        path = self._disk_path(slot_key)
        try:
            os.remove(path)
        except OSError:
            pass

    def prewarm(self, key: str, *args: Any, **kwargs: Any) -> None:
        """Oturum başında çağır: veri bayatsa arka planda sessizce tazele,
        bloke etme. Soğuksa bir kez arka plan çekimi başlatır."""
        try:
            self.get(key, *args, block_if_cold=False, **kwargs)
        except KeyError:
            raise
        except Exception:
            pass

    # ---- iç ----------------------------------------------------------------

    def _slot(self, slot_key: str, spec: SourceSpec) -> _Entry:
        with self._registry_lock:
            entry = self._entries.get(slot_key)
            if entry is None:
                entry = _Entry(spec=spec)
                self._entries[slot_key] = entry
            else:
                entry.spec = spec
            return entry

    def _snapshot(self, entry: _Entry, *, stale: bool) -> Snapshot:
        return Snapshot(
            value=entry.value,
            fetched_at=entry.fetched_at,
            stale=stale,
            refreshing=entry.refreshing,
            error=entry.error,
        )

    def _kick_refresh(self, slot_key: str, entry: _Entry, args: Tuple, kwargs: Dict) -> None:
        if entry.refreshing:
            return
        if not entry.lock.acquire(blocking=False):
            return
        entry.refreshing = True

        def _work() -> None:
            try:
                self._do_load(entry, args, kwargs, slot_key)
            finally:
                entry.refreshing = False
                entry.lock.release()

        self._spawn(_work)

    def _refresh_sync(self, slot_key: str, entry: _Entry, args: Tuple, kwargs: Dict) -> None:
        with entry.lock:
            # kilit beklerken başka biri tazelemiş olabilir
            age = (_now() - entry.fetched_at) if entry.fetched_at else float("inf")
            if entry.fetched_at and age < entry.spec.soft_ttl and not entry.spec.is_empty(entry.value):
                return
            entry.refreshing = True
            try:
                self._do_load(entry, args, kwargs, slot_key)
            finally:
                entry.refreshing = False

    def _do_load(self, entry: _Entry, args: Tuple, kwargs: Dict, slot_key: str) -> None:
        spec = entry.spec
        try:
            value = spec.loader(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — bilinçli: kaynak hatası kilit yaratmasın
            entry.error = f"{type(exc).__name__}: {exc}"
            return
        if spec.is_empty(value):
            # boş sonucu SNAPSHOT'A YAZMA — eski veri (varsa) elde kalsın,
            # sonraki tur yeniden denesin. cache_data_safe ile aynı ilke.
            entry.error = "kaynak boş sonuç döndürdü"
            return
        entry.value = value
        entry.fetched_at = _now()
        entry.error = None
        if spec.disk:
            self._write_disk(slot_key, value, entry.fetched_at)

    # ---- disk -------------------------------------------------------------

    def _disk_path(self, slot_key: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in slot_key)
        return os.path.join(self._data_dir, f"{safe}.json")

    def _write_disk(self, slot_key: str, value: Any, fetched_at: float) -> None:
        path = self._disk_path(slot_key)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {"fetched_at": fetched_at, "value": value}
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            # diske yazamamak ölümcül değil — bellek snapshot'ı yine geçerli
            pass

    def _hydrate_from_disk(self, slot_key: str, entry: _Entry) -> None:
        entry.loaded_from_disk = True
        path = self._disk_path(slot_key)
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        value = payload.get("value")
        fetched_at = payload.get("fetched_at") or 0.0
        if value is None or not isinstance(fetched_at, (int, float)):
            return
        if entry.spec.is_empty(value):
            return
        entry.value = value
        entry.fetched_at = float(fetched_at)


def _fingerprint(args: Tuple, kwargs: Dict) -> str:
    if not args and not kwargs:
        return ""
    try:
        raw = json.dumps([args, sorted(kwargs.items())], default=str, sort_keys=True)
    except (TypeError, ValueError):
        raw = repr((args, sorted(kwargs.items())))
    digest = 0
    for ch in raw:
        digest = (digest * 131 + ord(ch)) & 0xFFFFFFFFFFFF
    return format(digest, "x")


def _spawn_daemon(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="datahub-refresh", daemon=True).start()


# Süreç ömrü boyunca tek örnek — monolit ``from core.datahub import hub`` ile alır.
hub = DataHub()
