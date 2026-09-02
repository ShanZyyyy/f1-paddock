"""Varlık çıkarımı (entity extraction) — asistan hattının 2. katmanı.

Cümleden yıl / Grand Prix / pilot / takım / seans / metrik çeker. Hepsi
sözlük + regex + fuzzy eşleşme (difflib) — model indirmesi yok, ağ yok.

Sözlükler `core.f1_constants` ve (varsa) tarih veritabanından beslenir; bu modül
sadece dışarıdan verilen listelerle çalışır, böylece test edilebilir kalır.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .normalize import Utterance, fold

_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")

# Kullanıcının yazabileceği yaygın kısa adlar -> resmi Grand Prix / etkinlik adı.
# Takvimden gelen adlarla BİRLEŞTİRİLİR (register_calendar).
GP_ALIASES: dict[str, str] = {
    "monako": "Monaco", "monten karlo": "Monaco", "montekarlo": "Monaco",
    "spa": "Belgian", "francorchamps": "Belgian", "belcika": "Belgian",
    "silverstone": "British", "ingiltere": "British", "britanya": "British",
    "monza": "Italian", "italya": "Italian",
    "imola": "Emilia Romagna", "san marino": "San Marino",
    "suzuka": "Japanese", "japonya": "Japanese",
    "interlagos": "São Paulo", "brezilya": "São Paulo", "sao paulo": "São Paulo",
    "hungaroring": "Hungarian", "macaristan": "Hungarian",
    "zandvoort": "Dutch", "hollanda": "Dutch",
    "cota": "United States", "austin": "United States", "abd": "United States",
    "yas marina": "Abu Dhabi", "abu dabi": "Abu Dhabi",
    "jeddah": "Saudi Arabian", "suudi": "Saudi Arabian",
    "bahreyn": "Bahrain", "sakhir": "Bahrain",
    "barselona": "Spanish", "katalunya": "Spanish", "ispanya": "Spanish",
    "melbourne": "Australian", "avustralya": "Australian",
    "montreal": "Canadian", "kanada": "Canadian",
    "singapur": "Singapore", "marina bay": "Singapore",
    "cin": "Chinese", "shanghai": "Chinese",
    "miami": "Miami", "las vegas": "Las Vegas", "vegas": "Las Vegas",
    "meksika": "Mexico City", "hermanos rodriguez": "Mexico City",
    "red bull ring": "Austrian", "avusturya": "Austrian",
    "paul ricard": "French", "fransa": "French",
    "portimao": "Portuguese", "portekiz": "Portuguese",
    "nurburgring": "Eifel", "hockenheim": "German", "almanya": "German",
    "estoril": "Portuguese", "kyalami": "South African",
    # İngilizce ülke adları ve kanonik GP sıfatları — kullanıcı "Abu Dhabi" ya da
    # "Belgian GP" yazarsa da yakala (Türkçe karşılıkları yukarıda).
    "abu dhabi": "Abu Dhabi",
    "belgium": "Belgian", "belgian": "Belgian",
    "japan": "Japanese", "japanese": "Japanese",
    "china": "Chinese", "chinese": "Chinese",
    "brazil": "São Paulo", "brazilian": "São Paulo",
    "netherlands": "Dutch", "dutch": "Dutch",
    "spain": "Spanish", "spanish": "Spanish",
    "italy": "Italian", "italian": "Italian",
    "france": "French", "french": "French",
    "britain": "British", "great britain": "British", "british": "British", "england": "British",
    "germany": "German", "german": "German",
    "hungary": "Hungarian", "hungarian": "Hungarian",
    "austria": "Austrian", "austrian": "Austrian",
    "australia": "Australian", "australian": "Australian",
    "canada": "Canadian", "canadian": "Canadian",
    "portugal": "Portuguese", "portuguese": "Portuguese",
    "mexico": "Mexico City", "mexican": "Mexico City",
    "saudi arabia": "Saudi Arabian", "saudi arabian": "Saudi Arabian", "saudi": "Saudi Arabian",
    "united states": "United States", "usa": "United States", "american": "United States",
    "emilia romagna": "Emilia Romagna", "qatar": "Qatar",
    "azerbaijan": "Azerbaijan", "baku": "Azerbaijan",
    "south africa": "South African", "south african": "South African",
}

_SESSION_HINTS = {
    "Q": ("sIralama", "pole", "qualifying", "quali", "q3"),
    "S": ("sprint",),
    "R": ("yarIs", "grand prix", "gp", "race"),
}

_METRIC_HINTS = {
    "winner": ("kim kazandi", "kazanan", "galip", "birinci", "kazandi", "zafer"),
    "pole": ("pole", "sIralamayI kim", "ilk sIrada"),
    "podium": ("podyum", "ilk uc", "podium"),
    "points": ("kac puan", "puanI", "puan durumu"),
    "grid": ("kacIncI baslad", "grid"),
    "fastest_lap": ("en hIzlI tur", "fastest lap"),
    "champion": ("sampiyon", "dunya birincisi", "wdc", "title"),
}


@dataclass
class Entities:
    year: int | None = None
    gp: str | None = None            # resmi ad parçası, ör. "Monaco"
    drivers: list[str] = None        # ["Ayrton Senna", ...] (görünen ad)
    team: str | None = None
    session: str = "R"
    metrics: list[str] = None

    def __post_init__(self):
        self.drivers = self.drivers or []
        self.metrics = self.metrics or []


class EntityExtractor:
    """Bir kez kur (sözlükleri yükle), sonra her cümle için `extract()` çağır."""

    def __init__(self, driver_names, team_names, team_aliases=None,
                 gp_names=None, gp_aliases=None):
        # driver_names: {"ayrton senna": "Ayrton Senna", "senna": "Ayrton Senna", "ham": "Lewis Hamilton", ...}
        self.drivers = {fold(k): v for k, v in (driver_names or {}).items()}
        self.teams = {fold(k): v for k, v in (team_names or {}).items()}
        for a, canon in (team_aliases or {}).items():
            self.teams[fold(a)] = canon
        self.gp = dict(GP_ALIASES)
        self.gp.update(gp_aliases or {})
        for name in (gp_names or []):
            key = fold(name).replace(" grand prix", "").strip()
            if key:
                self.gp[key] = name.replace(" Grand Prix", "").strip()
        self._driver_keys = list(self.drivers)
        self._team_keys = list(self.teams)

    # -- alt çıkarımlar ---------------------------------------------------
    def _year(self, u: Utterance):
        m = _YEAR_RE.search(u.text)
        return int(m.group(1)) if m else None

    def _gp(self, u: Utterance):
        # Sol sınır şart (kelime başı) — Türkçe ekler sağdan serbest ("Monza'daki").
        # Böylece "cin" (China) "raCINg" içinde eşleşmez. En uzun anahtar kazanır.
        best = None
        for key, canon in self.gp.items():
            if re.search(r"(?:^|\s)" + re.escape(key), u.text) and (
                    best is None or len(key) > len(best[0])):
                best = (key, canon)
        return best[1] if best else None

    def _drivers(self, u: Utterance):
        padded = f" {u.text} "
        full_hits, sur_hits = [], []
        for key in self._driver_keys:
            # tam kelime sınırı: " senna " gibi
            if f" {key} " in padded:
                (full_hits if " " in key else sur_hits).append(self.drivers[key])
        found, seen = [], set()
        for v in full_hits:                       # "michael schumacher" > "schumacher"
            if v not in seen:
                seen.add(v)
                found.append(v)
        # tam ad eşleşmesinin HERHANGİ bir kelimesi çıplak soyadı hit'ini bastırır:
        # "Lewis Hamilton" varken "Lewis" (Jackie Lewis) ikinci pilot sayılmasın.
        covered = set()
        for v in found:
            covered.update(v.lower().split())
        for v in sur_hits:
            sn = v.lower().split()[-1]
            if v not in seen and sn not in covered:
                seen.add(v)
                found.append(v)
        if not found:  # yazım hatası toleransı — sadece 5+ harfli tek token'lar için
            for tok in u.tokens:
                if len(tok) >= 5:
                    m = difflib.get_close_matches(tok, self._driver_keys, n=1, cutoff=0.86)
                    if m:
                        found.append(self.drivers[m[0]])
                        break
        return found

    def _team(self, u: Utterance):
        best = None
        for key in self._team_keys:
            if key in u.text and (best is None or len(key) > len(best)):
                best = key
        return self.teams[best] if best else None

    def _session(self, u: Utterance):
        for code, hints in _SESSION_HINTS.items():
            if u.has_any(*(fold(h) for h in hints)):
                if code != "R":
                    return code
        return "R"

    def _metrics(self, u: Utterance):
        return [m for m, hints in _METRIC_HINTS.items()
                if u.has_any(*(fold(h) for h in hints))]

    def _mask_team(self, u: Utterance, team: str) -> Utterance:
        """Takım adının kelimelerini metinden çıkar — 'Aston Martin' pilot
        aramasında 'Aston' + 'Martin' diye iki pilota bölünmesin."""
        masked = u.text
        aliases = [team.lower()] + [k for k, v in self.teams.items() if v == team]
        for a in sorted(set(aliases), key=len, reverse=True):
            if a:
                masked = masked.replace(a, " ")
        return Utterance(raw=u.raw, text=masked, tokens=masked.split())

    # -- kamuya açık -----------------------------------------------------
    def extract(self, u: Utterance) -> Entities:
        team = self._team(u)
        du = self._mask_team(u, team) if team else u   # takım adı pilot/GP'ye bölünmesin
        return Entities(
            year=self._year(u),
            gp=self._gp(du),
            drivers=self._drivers(du),
            team=team,
            session=self._session(u),
            metrics=self._metrics(u),
        )
