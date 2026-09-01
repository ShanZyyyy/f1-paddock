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


def driver_career(d: dict) -> Answer:
    bits = []
    for label, key in (("galibiyet", "wins"), ("podyum", "podiums"),
                       ("pole", "poles"), ("yarış", "starts"), ("şampiyonluk", "titles")):
        if d.get(key) is not None:
            bits.append(f"{d[key]} {label}")
    span = ""
    if d.get("first_season"):
        last = "aktif" if str(d.get("last_season")) in ("2026", "2027") else d.get("last_season")
        span = f" ({d['first_season']}–{last})"
    return Answer(f"{d['name']}{span}: " + ", ".join(bits) + "." if bits else NO_DATA,
                  d.get("source", "kariyer arşivi"), "DRIVER_CAREER", bool(bits))


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
