# -*- coding: utf-8 -*-
"""Paddock Dekoder — bulanık resim tahmin oyunu, çekirdek state motoru.

Oyuncuya gri-beyaz, bulanık ve kırpılmış bir görsel (2018+ takım logosu / pilot /
pist) gösterilir; tahmin bir açılır listeden SEÇİLİR (Stewardle deseni, serbest
yazı değil). Tahmin sınırsız; ilk birkaç yanlışta kadraj ~%40 açılıp sabitlenir,
görsel asla tam netleşmez. Pist haritaları düz gösterilir (döndürme/aynalama YOK).
Havuz geniş: güncel + geçmiş sezonlardan pilotlar ve pistler.

Bu modül SAF: Streamlit/ağ yok. UI state sözlüğünü tutar ve şu akışı kullanır:

    from core.games import paddock_decoder as deco

    state = deco.new_round("teams", seed=day_seed)         # yeni tur
    options = deco.pool_options(state.category)            # açılır liste
    result = deco.submit_guess(state, options[0])          # seçilen tahmin
    view = deco.public_state(state)                        # UI için görünüm
    #   view["remaining"], view["orientation"] (deg, mirror), view["pool"], ...
"""

from __future__ import annotations

import hashlib
import random
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = [
    "CATEGORIES",
    "MAX_GUESSES",
    "DecoderTarget",
    "TARGETS",
    "levenshtein",
    "similarity",
    "normalize",
    "match_score",
    "is_correct",
    "new_round",
    "submit_guess",
    "skip_round",
    "pool_options",
    "public_state",
    "blur_px",
    "reveal_hint",
    "image_orientation",
    "score_round",
    "DecoderSession",
    "new_session",
    "ERA_START",
    "REVEAL_STEPS",
]

CATEGORIES: Tuple[str, ...] = ("teams", "drivers", "tracks")

# Tahmin SINIRSIZ. Görsel yalnızca ilk ``REVEAL_STEPS`` yanlış boyunca açılır;
# sonra %40 dolayında sabit kalır (fix) — asla tam netleşmez.
REVEAL_STEPS: int = 4
MAX_GUESSES: int = REVEAL_STEPS   # geriye dönük uyum (eski kod bunu okuyabilir)

# Kaç yanlıştan sonra metin ipucu açılır (görsel sabitlenince)
_HINT_AFTER_MISSES = REVEAL_STEPS

# Tahmin artık bir açılır listeden SEÇİLİYOR (Stewardle gibi), serbest yazı değil.
# Eşik yalnızca güvenlik amaçlı: seçim zaten kanonik ada birebir eşit gelir.
_MATCH_THRESHOLD = 0.92

# Kapsam: 2018 ve sonrası F1.
ERA_START = 2018

# Blur eğrisi: başta MAX, her denemede azalır, çözülünce/bitince taban
_BLUR_START_PX = 22.0


# ===========================================================================
# 1) VERİ YAPISI  (şimdilik mock — gerçekte data/games_seed veya JSON'dan)
# ===========================================================================


@dataclass(frozen=True)
class DecoderTarget:
    """Tek bir tahmin hedefi."""

    category: str
    answer: str                       # kanonik doğru cevap
    image: str                        # görsel URL'i veya yerel yol
    aliases: Tuple[str, ...] = ()      # kabul edilen alternatif yazımlar
    hint: str = ""                     # _HINT_AFTER_MISSES yanlıştan sonra açılır

    @property
    def accepted(self) -> Tuple[str, ...]:
        return (self.answer, *self.aliases)


# image = gerçek F1.com görseli (UI bulanıklaştırıp kırpar; 404 → silüet placeholder).
# Tümü ~1080p+ çözünürlük: logo h_1080, pilot 9col-retina (~1994²), pist w_1920.
_F1_LOGO = ("https://media.formula1.com/image/upload/c_fit%2Ch_1080/q_auto:best/"
            "v1740000001/common/f1/2025/{slug}/2025{slug}logowhite.webp")
_F1_HEAD = ("https://www.formula1.com/content/dam/fom-website/drivers/"
            "{yr}Drivers/{sur}.jpg.transform/9col-retina/image.jpg")
_F1_MAP = ("https://media.formula1.com/image/upload/f_auto,c_limit,w_1920,q_auto:best/"
           "content/dam/fom-website/2018-redesign-assets/Circuit%20maps%2016x9/{map}_Circuit")


def _team(name, slug, hint, aliases=()):
    return DecoderTarget("teams", name, _F1_LOGO.format(slug=slug),
                         aliases=aliases, hint=hint)


def _driver(name, sur, yr, hint):
    return DecoderTarget("drivers", name, _F1_HEAD.format(yr=yr, sur=sur), hint=hint)


def _track(name, mp, hint, aliases=()):
    return DecoderTarget("tracks", name, _F1_MAP.format(map=mp), aliases=aliases, hint=hint)


# Tahmin bir açılır listeden seçildiği için alias'lara neredeyse hiç gerek yok;
# yalnızca yaygın kısa adlar (Stewardle'da "de olsa" mantığı) tutuldu.
# İpuçları cevabın adını İÇERMEZ (test ile doğrulanır) — yalnızca son hakta açılır.
TARGETS: Dict[str, List[DecoderTarget]] = {
    "teams": [
        _team("Ferrari", "ferrari", "Kuruluşundan bugüne kesintisiz yarışan tek takım."),
        _team("Red Bull Racing", "redbullracing",
              "Enerji içeceği markasının Milton Keynes ekibi.", ("red bull",)),
        _team("McLaren", "mclaren",
              "Woking; kurucusunun adını taşıyan Yeni Zelandalı ekip."),
        _team("Mercedes", "mercedes",
              "Brackley; hibrit çağının ilk yıllarına damga vuran marka."),
        _team("Aston Martin", "astonmartin",
              "Silverstone yanı; İngiliz spor otomobil markasının yeşil arabaları."),
        _team("Williams", "williams",
              "Grove; kurucusu Sir Frank olan, 9 yapımcı şampiyonluğu bulunan aile ekibi."),
        _team("Alpine", "alpine",
              "Enstone; bir Fransız üreticinin mavi-pembe arabaları."),
        _team("Haas F1 Team", "haas",
              "Kannapolis merkezli, tek Amerikan takımı; 2016'da katıldı."),
        _team("Kick Sauber", "kicksauber",
              "İsviçre ekibi; 2026'da bir Alman üreticinin fabrika takımına dönüşecek.",
              ("sauber", "kick sauber", "stake")),
        _team("Racing Bulls", "racingbulls",
              "Faenza merkezli; ana enerji-içeceği ekibinin kardeş/geliştirme takımı.",
              ("rb", "visa cash app rb", "alphatauri", "toro rosso")),
    ],
    "drivers": [
        _driver("Lewis Hamilton", "hamilton", 2025,
                "Rekor eşitleyen yedi kez dünya şampiyonu."),
        _driver("Max Verstappen", "verstappen", 2025,
                "En genç GP galibi; babası da F1'de yarıştı."),
        _driver("Charles Leclerc", "leclerc", 2025,
                "F2 ve GP3 şampiyonu; kırmızı arabayı sürüyor."),
        _driver("Lando Norris", "norris", 2025,
                "Woking ekibinin genç İngiliz pilotu; Twitch yayınlarıyla tanınır."),
        _driver("George Russell", "russell", 2025,
                "F2 şampiyonu; 2020 Sakhir'de bir yarış vekâleten Mercedes sürdü."),
        _driver("Carlos Sainz", "sainz", 2025,
                "Babası iki kez ralli dünya şampiyonu; 2024'te Ferrari'yle kazandı."),
        _driver("Oscar Piastri", "piastri", 2025,
                "F3 ve F2'yi üst üste kazanan Avustralyalı; 2023'te F1'e çıktı."),
        _driver("Fernando Alonso", "alonso", 2025,
                "2005–2006 çift şampiyonu; gridin en deneyimli ismi."),
        _driver("Sergio Perez", "perez", 2024,
                "Meksikalı; 2020 Sakhir'de ilk galibiyetini aldı, Red Bull'da yarıştı."),
        _driver("Valtteri Bottas", "bottas", 2024,
                "Finli; uzun yıllar bir gümüş ekipte ikinci pilot oldu, sonra İsviçre ekibine geçti."),
        _driver("Pierre Gasly", "gasly", 2025,
                "Fransız; 2020 Monza'da sürpriz galibiyet, önce ana ekipte sonra kız ekipte."),
        _driver("Esteban Ocon", "ocon", 2025,
                "Fransız; 2021 Macaristan galibi, Mercedes gençlik programından geldi."),
        _driver("Yuki Tsunoda", "tsunoda", 2025,
                "Japon; Honda desteğiyle Faenza ekibinden F1'e çıktı."),
        _driver("Nico Hulkenberg", "hulkenberg", 2025,
                "Alman; en çok yarışa çıkıp podyum görmeme rekorunu uzun süre elinde tuttu."),
        _driver("Daniel Ricciardo", "ricciardo", 2024,
                "Avustralyalı; 'shoey' kutlaması ve geç frenlemeleriyle tanınır."),
        _driver("Kevin Magnussen", "magnussen", 2024,
                "Danimarkalı; babası da F1'de yarıştı, uzun süre Amerikan ekibinde."),
        # --- güncel gridin diğer isimleri ---
        _driver("Alexander Albon", "albon", 2025,
                "Tayland bayrağıyla yarışır; ana ekipten düşüp bir yıl sonra Grove'da geri döndü."),
        _driver("Lance Stroll", "stroll", 2025,
                "Kanadalı; takım sahibinin oğlu, 2017 Bakü'de genç yaşta podyuma çıktı."),
        _driver("Liam Lawson", "lawson", 2025,
                "Yeni Zelandalı; 2023'te beş yarış vekâlet etti, 2025 başında kısa süre ana ekibe çıktı."),
        _driver("Isack Hadjar", "hadjar", 2025,
                "Fransız; 2024 F2 ikincisi, çaylak yılında beklenmedik bir podyum aldı."),
        _driver("Oliver Bearman", "bearman", 2025,
                "İngiliz; 18 yaşında Cidde'de bir gün önceden haber verilip Ferrari'yle puan aldı."),
        _driver("Kimi Antonelli", "antonelli", 2025,
                "İtalyan genç; bir Alman fabrika ekibinde yedi kez şampiyon pilottan boşalan koltuğu aldı."),
        _driver("Jack Doohan", "doohan", 2025,
                "Avustralyalı; motosiklet efsanesi bir babanın oğlu, sezon başında birkaç yarışta koştu."),
        _driver("Franco Colapinto", "colapinto", 2024,
                "Arjantinli; 2024 sonunda dokuz yarış Grove'da koştu, sonra mavi-pembe ekibe geçti."),
        _driver("Guanyu Zhou", "zhou", 2024,
                "İlk tam zamanlı Çinli F1 pilotu; 2022 Silverstone'da takla attı, halo hayat kurtardı."),
        # --- geçmiş sezonların isimleri (2018+) ---
        _driver("Sebastian Vettel", "vettel", 2022,
                "Dört kez üst üste şampiyon; parmak kaldırma kutlaması ve arabalarına isim vermesiyle anılır."),
        _driver("Kimi Räikkönen", "raikkonen", 2021,
                "'Bwoah' telsizleriyle ünlü Finli; 2007 şampiyonu, en çok GP başlangıcı rekorunu kırdı."),
        _driver("Daniil Kvyat", "kvyat", 2020,
                "Rus; genç yaşta ana ekibe çıktı, 'torpido' lakabını bir yöneticiden aldı."),
        _driver("Romain Grosjean", "grosjean", 2020,
                "İsviçre doğumlu Fransız; 2020 Bahreyn'de aracı ikiye bölünüp alevlerin içinden çıktı."),
        _driver("Antonio Giovinazzi", "giovinazzi", 2021,
                "İtalyan; uzun aradan sonra ülkesinden gelen ilk tam zamanlı pilot, sonra dayanıklılığa geçti."),
        _driver("Nikita Mazepin", "mazepin", 2021,
                "Rus; babasının gübre şirketi takıma sponsordu, 2022'de yarışması engellendi."),
        _driver("Nyck de Vries", "devries", 2023,
                "Hollandalı; Formula E ve F2 şampiyonu, 2022 Monza'da vekâleten puan aldı."),
        _driver("Mick Schumacher", "schumacher", 2022,
                "Soyadı sporun en ünlülerinden; F2 şampiyonu, sonra bir üst ekibe yedek oldu."),
        _driver("Logan Sargeant", "sargeant", 2023,
                "Grove'dan ayrılan son Amerikalı; sezon ortasında koltuğunu kaybetti."),
        _driver("Nicholas Latifi", "latifi", 2022,
                "Kanadalı; 2021 Abu Dabi'de son turdaki kazası şampiyonluğun kaderini değiştirdi."),
    ],
    "tracks": [
        _track("Circuit de Monaco", "Monaco",
               "Takvimin en yavaş ortalama hızlı, en dar sokak pisti.",
               ("monaco", "monako", "monte carlo", "montekarlo")),
        _track("Silverstone Circuit", "Great_Britain",
               "1950'de ilk F1 yarışının yapıldığı eski hava üssü.", ("silverstone",)),
        _track("Suzuka Circuit", "Japan",
               "Dünyanın tek '8' şeklindeki pisti.", ("suzuka",)),
        _track("Autodromo Nazionale Monza", "Italy",
               "'Hız tapınağı'; uzun düzlükleri ve eski banklı virajıyla ünlü.", ("monza",)),
        _track("Circuit de Spa-Francorchamps", "Belgium",
               "Ardennes ormanında; Eau Rouge–Raidillon tırmanışı burada.", ("spa",)),
        _track("Circuit Zandvoort", "Netherlands",
               "Kum tepeleri arasında; 2021'de bankinglerle takvime döndü.", ("zandvoort",)),
        _track("Hungaroring", "Hungary",
               "Budapeşte yakını; dar ve dönüşlü, 'pistte Monako' denir.", ("hungaroring",)),
        _track("Bahrain International Circuit", "Bahrain",
               "Çölde, gece yarışı; sezon açılışına sık ev sahipliği yapar.", ("sakhir", "bahrain")),
        _track("Albert Park Circuit", "Australia",
               "Melbourne'da bir göl çevresine kurulan geçici sokak-tarzı pist.",
               ("albert park", "melbourne")),
        _track("Marina Bay Street Circuit", "Singapore",
               "İlk gece yarışı; nem ve duvarlar pilotları en çok yoran pist.", ("marina bay",)),
        # --- güncel takvimin diğer pistleri ---
        _track("Circuit of the Americas", "USA",
               "Austin, Teksas; dik yokuş bir ilk viraj, ardından İngiltere'den ilhamlı hızlı esler.",
               ("cota", "austin", "amerika")),
        _track("Autódromo José Carlos Pace", "Brazil",
               "São Paulo; saat yönünün tersine, iniş-çıkışlı kısa tur, 'S' virajıyla başlar.",
               ("interlagos", "brezilya", "sao paulo")),
        _track("Red Bull Ring", "Austria",
               "Stiria dağlarında; kısa tur, birkaç sert fren ve çok sayıda irtifa değişimi.",
               ("spielberg", "avusturya")),
        _track("Baku City Circuit", "Baku",
               "Hazar kıyısında; çok uzun tam gaz bölümü ile dar kale kesimi yan yana.",
               ("baku", "azerbaycan")),
        _track("Yas Marina Circuit", "Abu_Dhabi",
               "Sezon finaline sık ev sahipliği yapar; gün batımında başlayıp gece biter.",
               ("abu dhabi", "abudabi")),
        _track("Jeddah Corniche Circuit", "Saudi_Arabia",
               "Kıyı boyunca 27 viraj; sokak pisti olmasına rağmen ortalama hız çok yüksek.",
               ("jeddah", "cidde", "suudi arabistan")),
        _track("Miami International Autodrome", "Miami",
               "Bir Amerikan futbol stadının çevresinde; sahte rıhtım dekoruyla anılır.",
               ("miami",)),
        _track("Las Vegas Strip Circuit", "Las_Vegas",
               "Gece yarısı ana bulvarda; kumarhane ışıkları altında uzun tam gaz bölümleri.",
               ("vegas", "las vegas")),
        _track("Circuit Gilles Villeneuve", "Canada",
               "Bir nehir adasında; çıkıştaki 'Şampiyon Duvarı' pek çok pilotu yakaladı.",
               ("montreal", "kanada")),
        _track("Circuit de Barcelona-Catalunya", "Spain",
               "Takımların en iyi tanıdığı pist; kış testleri yıllarca burada yapıldı.",
               ("barcelona", "catalunya", "ispanya", "montmelo")),
        _track("Autódromo Hermanos Rodríguez", "Mexico",
               "2200 m rakımda; ince hava motoru ve kanadı zorlar, stadyum bölümü ünlüdür.",
               ("meksika", "mexico city")),
        _track("Losail International Circuit", "Qatar",
               "Çölde, MotoGP için yapıldı; süpürülen kum ve gece ışıkları, çok hızlı akan virajlar.",
               ("katar", "lusail", "losail")),
        _track("Autodromo Enzo e Dino Ferrari", "Emilia_Romagna",
               "Bir İtalyan kasabasında, ters yönde akan eski bir pist; adını bir kurucu ve oğlundan alır.",
               ("imola", "emilia romagna")),
        # --- yalnızca geçmiş sezonlarda (2018–2023) yer alan pistler ---
        _track("Istanbul Park", "Turkey",
               "Saat yönünün tersine akar; dört apeksli sola dönen ünlü viraj burada, 2020–2021'de döndü.",
               ("istanbul", "turkiye")),
        _track("Autódromo Internacional do Algarve", "Portugal",
               "Ülkenin güneyinde; hız trenini andıran sürekli iniş-çıkışlar, 2020–2021 takvimindeydi.",
               ("portimao", "portekiz")),
        _track("Circuit Paul Ricard", "France",
               "Mavi-kırmızı boyalı geniş asfalt kaçış alanlarıyla tanınır; 2018–2022 arası takvimdeydi.",
               ("le castellet", "fransa")),
        _track("Sochi Autodrom", "Russia",
               "Bir kış olimpiyat parkının çevresinde; upuzun 90° üçüncü viraj, 2014–2021 arası takvimde.",
               ("soci", "rusya")),
        _track("Hockenheimring", "Germany",
               "Eskiden ormanda upuzun düzlükleri vardı; kısaltılmış hâliyle 2019'a kadar takvimde, stadyum virajları kaldı.",
               ("hockenheim", "almanya")),
        _track("Shanghai International Circuit", "China",
               "Kalkışta uzayıp daralan sarmal ilk viraj; ara verdikten sonra 2024'te döndü.",
               ("shanghai", "cin", "sangay")),
    ],
}


# ===========================================================================
# 2) BULANIK EŞLEŞTİRME  (Levenshtein — dış paket yok)
# ===========================================================================


def normalize(text: str) -> str:
    """Karşılaştırma için sadeleştir: küçük harf, aksan/TR katlama, yalnız
    harf+rakam+boşluk, tek boşluk."""
    text = str(text or "").strip().lower()
    text = text.replace("ı", "i").replace("İ", "i").replace("ş", "s") \
               .replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    out = []
    for ch in text:
        out.append(ch if (ch.isalnum() or ch == " ") else " ")
    return " ".join("".join(out).split())


def levenshtein(a: str, b: str) -> int:
    """İki dizi arasındaki minimum düzenleme (ekle/sil/değiştir) sayısı."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # silme
                cur[j - 1] + 1,       # ekleme
                prev[j - 1] + (ca != cb),  # değiştirme
            ))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """0..1 — 1 tam eşleşme. Normalize edilmiş mesafeye göre."""
    a, b = normalize(a), normalize(b)
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b)) or 1
    return 1.0 - levenshtein(a, b) / longest


def match_score(guess: str, target: DecoderTarget) -> Tuple[float, str]:
    """Tahminin hedefe en iyi benzerliği + hangi kabul-varyantına eşleştiği."""
    g = normalize(guess)
    best, best_on = 0.0, target.answer
    for candidate in target.accepted:
        c = normalize(candidate)
        score = similarity(g, c)
        # tahmin, çok kelimeli cevabın AYIRT EDİCİ (4+ harf) bir kelimesini tam
        # tutuyorsa (ör. "monaco" -> "circuit de monaco") kısmi eşleşme say —
        # "de", "f1", "gp" gibi kısa/jenerik kelimeler saymaz
        if len(g) >= 4 and g in c.split():
            score = max(score, 0.86)
        if score > best:
            best, best_on = score, candidate
    return best, best_on


def is_correct(guess: str, target: DecoderTarget, *, threshold: float = _MATCH_THRESHOLD) -> bool:
    """Oransal hata toleransı ile doğru mu.

    'ferari' (6 harf) → 'ferrari' mesafe 1 → benzerlik 6/7≈0.857 ≥ 0.78 ✓
    'mercedes' → 'ferrari' benzerlik düşük ✗
    """
    score, _ = match_score(guess, target)
    return score >= threshold


# ===========================================================================
# 3) TUR STATE'İ
# ===========================================================================


@dataclass
class DecoderRound:
    category: str
    target_index: int
    guesses: List[str] = field(default_factory=list)   # ham tahminler
    solved: bool = False
    skipped: bool = False
    matched_on: Optional[str] = None

    # -- türetilenler --------------------------------------------------
    @property
    def target(self) -> DecoderTarget:
        return TARGETS[self.category][self.target_index]

    @property
    def attempts_used(self) -> int:
        return len(self.guesses)

    @property
    def revealed(self) -> float:
        """0 (kapalı) → 1 (sabit noktaya ulaştı). Sınırsız tahmin; yalnızca ilk
        REVEAL_STEPS yanlış boyunca açılır, sonra sabit."""
        return min(1.0, self.attempts_used / max(1, REVEAL_STEPS))

    @property
    def failed(self) -> bool:
        return self.skipped and not self.solved

    @property
    def over(self) -> bool:
        return self.solved or self.failed

    # -- serileştirme (Streamlit session_state için) --------------------
    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "target_index": self.target_index,
            "guesses": list(self.guesses),
            "solved": self.solved,
            "skipped": self.skipped,
            "matched_on": self.matched_on,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecoderRound":
        return cls(
            category=data["category"],
            target_index=int(data["target_index"]),
            guesses=list(data.get("guesses", [])),
            solved=bool(data.get("solved", False)),
            skipped=bool(data.get("skipped", False)),
            matched_on=data.get("matched_on"),
        )


def _seed_index(category: str, seed) -> int:
    pool = TARGETS[category]
    if seed is None:
        return random.randrange(len(pool))
    digest = hashlib.sha256(f"{category}:{seed}".encode("utf-8")).hexdigest()
    return int(digest, 16) % len(pool)


def new_round(category: str, *, seed=None, target_index: Optional[int] = None) -> DecoderRound:
    """Yeni tur başlat.

    category      : "teams" | "drivers" | "tracks"
    seed          : verilirse deterministik hedef; verilmezse rastgele
    target_index  : elle hedef seç (test / "sınırsız" mod rotasyonu)
    """
    if category not in TARGETS:
        raise ValueError(f"bilinmeyen kategori: {category!r} (geçerli: {CATEGORIES})")
    pool = TARGETS[category]
    if target_index is None:
        idx = _seed_index(category, seed)
    else:
        idx = int(target_index) % len(pool)
    return DecoderRound(category=category, target_index=idx)


def pool_options(category: str) -> List[str]:
    """Bu kategorinin açılır listesi — kanonik hedef adları (Stewardle deseni)."""
    return [t.answer for t in TARGETS[category]]


def submit_guess(state: DecoderRound, guess: str) -> dict:
    """Seçilen bir tahmini işle, state'i günceller, sonucu döndürür.

    Tahmin SINIRSIZ — yanlış tahmin turu bitirmez, yalnızca sayacı artırır.
    ``guess`` açılır listeden gelen kanonik ad olmalı; güvenlik için çok yakın
    yazımlar da kabul edilir.

    Dönen: {accepted, correct, solved, failed, over, attempts_used}
    """
    guess = str(guess or "").strip()
    if state.over or not guess:
        return {
            "accepted": False, "correct": False, "solved": state.solved,
            "failed": state.failed, "over": state.over,
            "attempts_used": state.attempts_used,
        }

    state.guesses.append(guess)
    accepted_norm = {normalize(a) for a in state.target.accepted}
    correct = normalize(guess) in accepted_norm
    if not correct:
        correct = match_score(guess, state.target)[0] >= _MATCH_THRESHOLD
    if correct:
        state.solved = True
        state.matched_on = state.target.answer

    return {
        "accepted": True, "correct": correct, "solved": state.solved,
        "failed": state.failed, "over": state.over,
        "attempts_used": state.attempts_used,
    }


def skip_round(state: DecoderRound) -> dict:
    """Oyuncu turu geçer — bilinemedi sayılır, düşük XP, sıradaki kategoriye."""
    if not state.over:
        state.skipped = True
    return {"skipped": state.skipped, "over": state.over}


# ===========================================================================
# 4) UI GÖRÜNÜMÜ  (blur + hangi bilgi açık)
# ===========================================================================


def blur_px(attempts_used: int, *, start: float = _BLUR_START_PX,
            solved: bool = False) -> float:
    """Basit blur yardımcı: ``attempts_used`` arttıkça azalır, REVEAL_STEPS'te
    tabana oturur (0'a inmez). UI kendi (kategori bazlı) eğrisini kullanır."""
    if solved:
        return 0.0
    frac = min(1.0, attempts_used / max(1, REVEAL_STEPS))
    return round(start * (1.0 - 0.75 * frac), 1)     # en fazla %75 açılır


def reveal_hint(state: DecoderRound) -> Optional[str]:
    """Görsel sabitlendikten (REVEAL_STEPS yanlış) sonra metin ipucu açılır."""
    if state.over:
        return state.target.hint or None
    if state.attempts_used >= _HINT_AFTER_MISSES and not state.solved:
        return state.target.hint or None
    return None


def image_orientation(state: DecoderRound) -> Tuple[int, bool]:
    """Görsel HER ZAMAN düz gösterilir — döndürme ve aynalama YOK.

    (Eski sürümde pist haritaları ilk tahminlerde 180° çevriliyordu; kullanıcı
    geri bildirimiyle kaldırıldı — pisti tanımayı imkânsız hâle getiriyordu.)
    ``(derece, aynala)`` imzası UI geriye dönük uyumu için korunuyor.
    """
    return (0, False)


def public_state(state: DecoderRound) -> dict:
    """UI'nın ihtiyacı olan her şey — hiç 'spoiler' sızdırmadan.

    Cevap yalnızca oyun bittiğinde (`over`) döner.
    """
    return {
        "category": state.category,
        "image": state.target.image,
        "guesses": list(state.guesses),
        "attempts_used": state.attempts_used,
        "reveal_steps": REVEAL_STEPS,
        "revealed": round(state.revealed, 3),
        "solved": state.solved,
        "skipped": state.skipped,
        "failed": state.failed,
        "over": state.over,
        "blur_px": blur_px(state.attempts_used, solved=state.solved),
        "hint": reveal_hint(state),
        "orientation": image_orientation(state),
        "pool": pool_options(state.category),
        "answer": state.target.answer if state.over else None,
        "matched_on": state.matched_on,
        "score": score_round(state) if state.over else 0,
    }


# ===========================================================================
# 5) PUANLAMA
# ===========================================================================

_SOLVE_BASE = 50        # 1. denemede bilme
_SOLVE_STEP = 9         # her fazladan yanlış -kaybı
_SOLVE_FLOOR = 8        # ne kadar geç bilirsen bil en az
_FAIL_XP = 2            # turu geçme tesellisi
_SWEEP_BONUS = 40       # 3 kategoriyi de bilme


def score_round(state: DecoderRound) -> int:
    """Bir turun XP değeri. Oyun bitmediyse 0. Sınırsız tahmin — puan yanlış
    sayısıyla azalır: 1. deneme 50 · 2. 41 · 3. 32 · … taban 8. Geçilirse 2.
    """
    if not state.over:
        return 0
    if not state.solved:
        return _FAIL_XP
    return max(_SOLVE_FLOOR, _SOLVE_BASE - _SOLVE_STEP * (state.attempts_used - 1))


# ===========================================================================
# 6) OTURUM — 3 KATEGORİLİK OYNANIŞ
# ===========================================================================


@dataclass
class DecoderSession:
    """teams → drivers → tracks sırasıyla üç turluk tam oyun."""

    order: List[str] = field(default_factory=lambda: list(CATEGORIES))
    rounds: List[DecoderRound] = field(default_factory=list)
    index: int = 0
    _seed: Optional[str] = None

    # -- erişim ------------------------------------------------------
    @property
    def current(self) -> Optional[DecoderRound]:
        if 0 <= self.index < len(self.rounds):
            return self.rounds[self.index]
        return None

    @property
    def done(self) -> bool:
        return len(self.rounds) == len(self.order) and all(r.over for r in self.rounds)

    @property
    def solved_count(self) -> int:
        return sum(1 for r in self.rounds if r.solved)

    @property
    def swept(self) -> bool:
        return self.done and self.solved_count == len(self.order)

    @property
    def total_score(self) -> int:
        base = sum(score_round(r) for r in self.rounds if r.over)
        return base + (_SWEEP_BONUS if self.swept else 0)

    # -- ilerleme --------------------------------------------------
    def advance(self) -> Optional[DecoderRound]:
        """Sıradaki kategoriye geç. Aktif tur bitmemişse dokunmaz."""
        if self.current is not None and not self.current.over:
            return self.current
        if self.index + 1 < len(self.order):
            self.index += 1
            if self.index >= len(self.rounds):
                self.rounds.append(new_round(self.order[self.index], seed=self._seed))
        return self.current

    # -- serileştirme --------------------------------------------
    def to_dict(self) -> dict:
        return {
            "order": list(self.order),
            "index": self.index,
            "seed": self._seed,
            "rounds": [r.to_dict() for r in self.rounds],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecoderSession":
        s = cls(
            order=list(data.get("order", CATEGORIES)),
            index=int(data.get("index", 0)),
            rounds=[DecoderRound.from_dict(d) for d in data.get("rounds", [])],
        )
        s._seed = data.get("seed")
        return s

    def public_state(self) -> dict:
        cur = self.current
        return {
            "category_order": list(self.order),
            "index": self.index,
            "step": f"{self.index + 1}/{len(self.order)}",
            "round": public_state(cur) if cur is not None else None,
            "done": self.done,
            "solved_count": self.solved_count,
            "swept": self.swept,
            "total_score": self.total_score,
        }


def new_session(*, seed=None, order: Optional[List[str]] = None) -> DecoderSession:
    """Yeni tam oyun.

    `seed` verilirse (günlük mod) üç hedef de o seed'e göre deterministik.
    Verilmezse rastgele bir oturum seed'i üretilir ve saklanır — böylece oturum
    yeniden-çalıştırmalar (Streamlit rerun) arasında tutarlı kalır ama her yeni
    oyun farklı hedefler verir.
    """
    order = list(order or CATEGORIES)
    for cat in order:
        if cat not in TARGETS:
            raise ValueError(f"bilinmeyen kategori: {cat!r}")
    resolved = str(seed) if seed is not None else format(random.randrange(1 << 40), "x")
    s = DecoderSession(order=order, rounds=[new_round(order[0], seed=resolved)], index=0)
    s._seed = resolved
    return s
