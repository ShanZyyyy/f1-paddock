# -*- coding: utf-8 -*-
"""Persona (yanıt modu) — asistan cevabını kitleye göre çerçeveler.

Kural tabanlı, LLM yok. Cevabın ANLAMINI ya da verisini değiştirmez; yalnızca
sunumunu uyarlar.

  - ``"engineer"`` (Mühendis Modu): mevcut davranış. Kısa, teknik, kaynak künyeli.
    ``adapt()`` bu modda hiçbir şey yapmaz.
  - ``"guide"`` (Kılavuz Modu): cevabın (ya da sorunun) içinde geçen F1 jargonunu
    sade Türkçe ile açan kısa bir "🎓 Kısaca …" eki koyar.

``context_hint(mode)`` — ileride bir LLM arka ucu bağlanırsa system-prompt'a
verilebilecek insan-okunur bağlam dizesi (şimdilik yalnızca belge amaçlı).
"""
from __future__ import annotations

from dataclasses import replace

MODES = ("engineer", "guide")
DEFAULT_MODE = "engineer"          # geriye dönük uyum: eski çağrılar aynı kalır

# Kılavuz modunda açıklanan jargon — yalnızca gerçekten kafa karıştıran terimler.
# ("grid", "pit stop" gibi bağlamdan anlaşılan sık terimler bilerek dışarıda.)
_JARGON = {
    "undercut": "rakipten önce pite girip taze lastikle, o hâlâ eskiyken öne geçme taktiği",
    "overcut": "rakip pite girince pistte kalıp hızlı turlarla çıkışta öne geçme",
    "stint": "iki pit stop arasında aynı lastik setiyle atılan tur bloğu",
    "graining": "lastik yüzeyinin taneciklenip geçici olarak tutuş kaybetmesi",
    "blistering": "aşırı ısıdan lastik yüzeyinde kabarcık oluşması",
    "degradasyon": "lastiğin tur geçtikçe yavaşlaması (aşınma)",
    "degradation": "lastiğin tur geçtikçe yavaşlaması (aşınma)",
    "ers": "frende toplanan elektrik enerjisini ekstra güce çeviren sistem",
    "drs": "2024'e kadar düzlükte arka kanadı açıp hız kazandıran sistem — 2026'da kaldırıldı",
    "vsc": "sanal güvenlik aracı: pistte fiziksel araç yok ama herkese hız sınırı var",
    "safety car": "pistte tehlike varken sahayı toplayıp yavaşlatan güvenlik aracı",
    "delta": "iki tur ya da iki pilot arasındaki zaman farkı",
    "dirty air": "öndeki aracın bozduğu, arkadakinin yere basmasını düşüren türbülanslı hava",
    "apeks": "virajın ideal çizgideki en iç noktası",
    "parc ferme": "sıralama sonrası araç ayarlarının büyük ölçüde kilitlendiği kural dönemi",
    "lift and coast": "yakıt/enerji yönetimi için fren öncesi gazdan erken çekme",
    "box": "telsizde 'pite gir' komutu ('box box')",
}

_MAX_HITS = 3


def _fold(text: str) -> str:
    return (str(text or "").lower()
            .replace("ı", "i").replace("ş", "s").replace("ğ", "g")
            .replace("ü", "u").replace("ö", "o").replace("ç", "c"))


def context_hint(mode: str) -> str:
    """LLM arka ucu bağlanırsa system-prompt'a eklenebilecek bağlam."""
    if mode == "guide":
        return ("Kılavuz Modu: kullanıcı Formula 1'e yeni. Jargonu sadeleştir, "
                "kısa cümleler kur, teknik terimleri parantez içinde açıkla.")
    return ("Mühendis Modu: kullanıcı deneyimli. Sayıyı, farkı ve kaynağı net ver; "
            "gereksiz açıklama yapma.")


def adapt(ans, mode: str = DEFAULT_MODE, question: str = ""):
    """Answer'ı moda göre çerçevele. Yeni bir Answer döndürür (mutasyon yok).

    Yalnızca ``guide`` modunda ve yalnızca gerçek bir veri cevabında iş yapar.
    """
    if mode != "guide" or not getattr(ans, "text", ""):
        return ans
    if getattr(ans, "intent", "") in ("SMALLTALK", "REFUSE"):
        return ans

    haystack = _fold(ans.text) + "  " + _fold(question)
    hits: list[tuple[str, str]] = []
    for term, explanation in _JARGON.items():
        if _fold(term) in haystack:
            hits.append((term, explanation))
        if len(hits) >= _MAX_HITS:
            break
    if not hits:
        return ans

    tail = "🎓 **Kısaca:** " + "; ".join(f"*{t}* — {e}" for t, e in hits) + "."
    return replace(ans, text=f"{ans.text}\n\n{tail}")
