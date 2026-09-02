"""Doğal dil üretimi (NLG) — asistan hattının 5. katmanı.

Model yok: her niyet için Türkçe cümle şablonu + `data` sözlüğünden alan doldurma.
Veri gelmezse dürüst "bulamadım" cümlesi. Her cevaba kaynak eklenir.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Answer:
    text: str
    source: str
    intent: str = ""
    ok: bool = True
    items: list | None = None   # [(sol, sağ), …] — liste tipi cevaplarda HUD satır satır render eder


def _podium(rows) -> str:
    return " · ".join(f"{i + 1}. {p['driver']}" for i, p in enumerate(rows)) if rows else "—"


NO_DATA = ("Bu soru için veritabanımızda doğrulanmış bir kayıt bulamadım. "
           "Farklı bir yıl/yarış veya pilot adıyla dener misin?")


def race_result(d: dict) -> Answer:
    parts = []
    m = d.get("metrics") or []
    if not m or "winner" in m or "podium" in m:
        w = d.get("winner")
        if w:
            team = f" ({w['constructor']})" if w.get("constructor") else ""
            parts.append(f"{d['season']} {d['race']} yarışını {w['driver']}{team} kazandı.")
    if not m or "pole" in m:
        if d.get("pole"):
            parts.append(f"Pole pozisyonu {d['pole']['driver']}'de idi.")
    if "podium" in m and d.get("podium"):
        parts.append(f"Podyum: {_podium(d['podium'])}.")
    if not parts and d.get("podium"):
        parts.append(f"Podyum: {_podium(d['podium'])}.")
    return Answer(" ".join(parts) or NO_DATA, d.get("source", "F1 tarih arşivi"),
                  "RACE_RESULT", bool(parts))


_MONTHS_TR = ("", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
              "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _date_tr(iso: str) -> str:
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{int(d)} {_MONTHS_TR[int(m)]}"
    except (ValueError, IndexError):
        return str(iso or "")


def _race_label(r: dict) -> str:
    where = r.get("circuit") or r.get("country") or ""
    return f"{r['name']}" + (f" — {where}" if where else "")


def season_calendar(d: dict) -> Answer:
    lines = [f"**{d['season']} sezonu — {d['count']} yarış:**"]
    items = []
    for r in d["races"]:
        lines.append(f"{r['round']}. {_race_label(r)}"
                     + (f"  ({_date_tr(r['date'])})" if r.get("date") else ""))
        items.append((f"{r['round']}. {_race_label(r)}", _date_tr(r.get("date")) or "—"))
    return Answer("\n".join(lines), d["source"], "SEASON_CALENDAR", items=items)


def season_first_last(d: dict, which: str) -> Answer:
    r = d["last"] if which == "last" else d["first"]
    tail = "kapanış" if which == "last" else "açılış"
    where = r.get("circuit") or r.get("country") or "—"
    return Answer(
        f"{d['season']} sezonunun {tail} yarışı {r['name']} idi — {where}"
        + (f", {_date_tr(r['date'])}" if r.get("date") else "") + ".",
        d["source"], "SEASON_FIRST_LAST")


def champion(d: dict) -> Answer:
    c = f" ({d['constructor']})" if d.get("constructor") else ""
    return Answer(f"{d['season']} Formula 1 Dünya Şampiyonu {d['driver']}{c} oldu.",
                  d.get("source", "yerel şampiyon arşivi"), "SEASON_CHAMPION")


def standings(rows, year) -> Answer:
    if not rows or len(rows) < 2:
        return Answer(NO_DATA, "Şampiyona Merkezi", "STANDINGS", False)
    a, b = rows[0], rows[1]
    gap = round(a["points"] - b["points"])
    return Answer(
        f"{year} şampiyonasında {a['name']} {round(a['points'])} puanla lider; "
        f"ikinci {b['name']} ({round(b['points'])} puan, {gap} puan geride). "
        "Bu bir ara tablodur — sezon sürüyor.",
        "Şampiyona Merkezi · FastF1 (tamamlanan yarışlar)", "STANDINGS")


# Uyruk: İngilizce sıfat (Stewardle JSON) VE ISO 2-harf kod (DRIVER_DISPLAY) ->
# Türkçe sıfat. Anahtarlar küçük harf.
_NATION_TR = {
    "american": "Amerikalı", "us": "Amerikalı",
    "argentine": "Arjantinli", "argentinian": "Arjantinli", "ar": "Arjantinli",
    "australian": "Avustralyalı", "au": "Avustralyalı",
    "austrian": "Avusturyalı", "at": "Avusturyalı",
    "belgian": "Belçikalı", "be": "Belçikalı",
    "brazilian": "Brezilyalı", "br": "Brezilyalı",
    "british": "İngiliz", "english": "İngiliz", "gb": "İngiliz",
    "canadian": "Kanadalı", "ca": "Kanadalı",
    "chinese": "Çinli", "cn": "Çinli",
    "colombian": "Kolombiyalı", "co": "Kolombiyalı",
    "czech": "Çek", "cz": "Çek",
    "danish": "Danimarkalı", "dk": "Danimarkalı",
    "dutch": "Hollandalı", "nl": "Hollandalı",
    "finnish": "Finlandiyalı", "fi": "Finlandiyalı",
    "french": "Fransız", "fr": "Fransız",
    "german": "Alman", "de": "Alman",
    "hungarian": "Macar", "hu": "Macar",
    "indian": "Hintli", "in": "Hintli",
    "indonesian": "Endonezyalı", "id": "Endonezyalı",
    "irish": "İrlandalı", "ie": "İrlandalı",
    "italian": "İtalyan", "it": "İtalyan",
    "japanese": "Japon", "jp": "Japon",
    "mexican": "Meksikalı", "mx": "Meksikalı",
    "monegasque": "Monakolu", "mc": "Monakolu",
    "new zealander": "Yeni Zelandalı", "nz": "Yeni Zelandalı",
    "polish": "Polonyalı", "pl": "Polonyalı",
    "portuguese": "Portekizli", "pt": "Portekizli",
    "russian": "Rus", "ru": "Rus",
    "south african": "Güney Afrikalı", "za": "Güney Afrikalı",
    "spanish": "İspanyol", "es": "İspanyol",
    "swedish": "İsveçli", "se": "İsveçli",
    "swiss": "İsviçreli", "ch": "İsviçreli",
    "thai": "Taylandlı", "th": "Taylandlı",
    "venezuelan": "Venezuelalı", "ve": "Venezuelalı",
}


def _nation_tr(value) -> str | None:
    if not value:
        return None
    return _NATION_TR.get(str(value).strip().lower())


def _join_tr(items: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b ve c'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " ve " + items[-1]


def driver_career(d: dict) -> Answer:
    stats = [f"{d[key]} {label}" for label, key in (
        ("galibiyet", "wins"), ("podyum", "podiums"),
        ("pole", "poles"), ("şampiyonluk", "titles")) if d.get(key)]
    starts = d.get("starts")
    if not stats and starts is None:
        return Answer(NO_DATA, d.get("source", "kariyer arşivi"), "DRIVER_CAREER", ok=False)

    name = d.get("name", "Bu pilot")
    nat = _nation_tr(d.get("nation"))
    active = bool(d.get("active"))
    team = d.get("team")
    fs, ls = d.get("first_season"), d.get("last_season")

    lines = [f"{name}, {nat} bir Formula 1 pilotudur." if nat
             else f"{name} bir Formula 1 pilotudur."]
    if team:
        lines.append(f"{team} takımı adına yarışmaktadır." if active
                     else f"Son olarak {team} takımında yarıştı.")
    if stats:
        lines.append("Kariyeri boyunca " + _join_tr(stats) + " elde etti.")
    elif starts:
        lines.append("Henüz bir Grand Prix galibiyeti bulunmuyor.")
    if fs and starts:
        if active:
            lines.append(f"{fs} yılından bu yana {starts} Grand Prix'de yer aldı.")
        elif ls:
            lines.append(f"{fs}–{ls} yılları arasında {starts} yarışa çıktı.")
        else:
            lines.append(f"Toplam {starts} yarışa çıktı.")
    elif starts:
        lines.append(f"Toplam {starts} yarışa çıktı.")

    return Answer(" ".join(lines), d.get("source", "kariyer arşivi"),
                  "DRIVER_CAREER", ok=bool(stats or starts))


def _hh_num(v):
    return v if isinstance(v, (int, float)) else None


def head_to_head(a: dict, b: dict) -> Answer:
    """İki pilotun paketli kariyer toplamlarını yan yana koyar. Yorum yok, uydurma
    yok — yalnızca kayıttaki alanlar."""
    na, nb = a.get("name", "?"), b.get("name", "?")
    rows = []
    for label, key in (("Şampiyonluk", "titles"), ("Galibiyet", "wins"),
                       ("Podyum", "podiums"), ("Pole", "poles"), ("Yarış", "starts")):
        av, bv = a.get(key), b.get(key)
        if av is None and bv is None:
            continue
        rows.append((label, f"{na}: {av if av is not None else '—'}  ·  "
                            f"{nb}: {bv if bv is not None else '—'}"))
    if not rows:
        return Answer(NO_DATA, "kariyer arşivi", "HEAD_TO_HEAD", False)

    lead = ""
    for key, word in (("titles", "şampiyonluk"), ("wins", "galibiyet")):
        av, bv = _hh_num(a.get(key)), _hh_num(b.get(key))
        if av is not None and bv is not None and av != bv:
            hi = na if av > bv else nb
            lead = f" {hi}, {word} sayısında önde ({max(av, bv)}–{min(av, bv)})."
            break

    body = f"**{na} — {nb}**\n" + "\n".join(f"• {l}: {v}" for l, v in rows) + \
           (f"\n{lead.strip()}" if lead else "")
    return Answer(body, a.get("source", "kariyer arşivi"), "HEAD_TO_HEAD", items=rows)


def team_titles(d: dict) -> Answer:
    team = d.get("team", "Bu takım")
    if not d.get("count"):
        return Answer(
            f"{team} renkleriyle kazanılmış bir Dünya Pilotlar Şampiyonluğu kaydım yok.",
            d.get("source", "f1_history.sqlite"), "TEAM_TITLES", ok=False)
    yrs = ", ".join(str(s["season"]) for s in d["seasons"])
    return Answer(
        f"{team} renkleriyle {d['count']} kez Dünya Pilotlar Şampiyonu çıktı ({yrs}). "
        "Not: bu, Takımlar Şampiyonası sayısından farklıdır.",
        d.get("source", "f1_history.sqlite"), "TEAM_TITLES")


def driver_season(d: dict) -> Answer:
    bits = []
    for label, key in (("galibiyet", "wins"), ("podyum", "podiums"), ("pole", "poles")):
        if d.get(key) is not None:
            bits.append(f"{d[key]} {label}")
    tail = ""
    if d.get("best_finish") and not d.get("wins"):
        tail = f", en iyi bitiş {d['best_finish']}."
    elif d.get("points") is not None:
        tail = f", {d['points']:g} puan."
    champ = " O sezon Dünya Şampiyonu oldu." if d.get("champion") else ""
    return Answer(
        f"{d['name']} {d['season']} sezonu: {d['starts']} yarışta "
        + ", ".join(bits) + (tail or ".") + champ,
        d.get("source", "f1_history.sqlite"), "DRIVER_SEASON",
        ok=bool(d.get("starts")))


def tech_upgrade(d: dict) -> Answer:
    lines = []
    for u in d["updates"]:
        comps = ", ".join(u.get("components", [])) or u.get("type", "güncelleme")
        goal = f" — {u['goal']}" if u.get("goal") else ""
        lines.append(f"• {u['event']} ({u.get('date', '')}): {comps}{goal}")
    body = f"{d['team']} son teknik güncellemeleri:\n" + "\n".join(lines)
    return Answer(body, d["source"], "TECH_UPGRADE")


def tech_missing(team: str) -> Answer:
    return Answer(
        f"{team} için teknik güncelleme kaydım yok. Bu veri FIA hafta sonu "
        "dokümanlarından elle işleniyor; henüz bu takım için giriş eklenmemiş.",
        "tech_upgrades.json", "TECH_UPGRADE", ok=False)


def record(text: str) -> Answer:
    return Answer(text, "F1 rekor arşivi", "RECORD")


def next_race(d: dict) -> Answer:
    return Answer(f"Sıradaki yarış: {d['name']} ({d.get('date', '')}).",
                  "Resmî F1 takvimi", "NEXT_RACE")


def user_stats(d: dict) -> Answer:
    if not d or not d.get("scored"):
        return Answer("Henüz puanlanmış bir hafta sonu tahminin yok.",
                      "Tarayıcı tercihlerin", "USER_STATS", False)
    acc = round(d["points"] / (d["scored"] * 20) * 100)
    return Answer(f"Bu sezon {d['scored']} tahmin puanlandı: toplam {d['points']} puan, "
                  f"%{acc} isabet.", "Tarayıcı tercihlerin · uygulama verisi", "USER_STATS")


def fallback() -> Answer:
    return Answer(
        "Sorunu tam çözemedim. Şöyle sorabilirsin: “1988 Monako GP'sini kim "
        "kazandı?”, “Leclerc'in kariyer istatistikleri”, “Kim lider?”, "
        "“Aston Martin son hangi güncellemeyi getirdi?”",
        "Paddock Asistan", "FALLBACK", ok=False)
