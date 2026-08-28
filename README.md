# Formula Paddock — Race Intelligence

Bağımsız bir Formula 1 veri, telemetri ve oyun merkezi. Streamlit ile çalışır.

Canlı: <https://appmanagertrackfixedv28verifiedcareerandduelpy-fteph2y8up6ft3g.streamlit.app/>

## Özellikler

- **Ana Sayfa** — son yarışın kazananı + fark, canlı şerit, geri sayım, Türkçe haber akışı
- **Telemetri Merkezi** — 2D tur düellosu, pist dominasyonu, fren analizi, lastik stratejisi, top speed tablosu (FastF1; OpenF1 tarihî yedeği)
- **Şampiyona Merkezi** — pilot/takım puan tabloları, sezon matrisi
- **Takvim & Pistler**, **Takımlar & Pilotlar (2026)**, **F2 & F3**
- **Hafta Sonu Merkezi**, **Yarış Hikayesi**, **Pilot Karşılaştırma**
- **Paddock Asistanı** — doğrulanmış F1 verisinden yanıt (isteğe bağlı OpenAI katmanı)
- **Oyunlar** — Stewardle, GridMaster, Takım Patronu, Paddock Draft, Tahmin, Sürüş prototipi

Veri kaynakları: FastF1, OpenF1, Jolpica/Ergast, resmî RSS akışları. Kaynak
erişilemezse uygulama **sahte veri üretmez**; boş/bekleme durumu gösterir.

## Yerel çalıştırma

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

İsteğe bağlı sırlar (`.streamlit/secrets.toml` veya ortam değişkeni):

```
OPENAI_API_KEY = "..."        # Asistanda genel F1 sohbeti
OPENF1_TOKEN   = "..."        # Canlı OpenF1 paketi (yoksa anonim denenir)
```

## Proje yapısı

```
streamlit_app.py     # ana uygulama + router (tek dosya, kademeli modülerleştiriliyor)
openf1_fallback.py   # OpenF1 tarihî seans/telemetri yedeği
core/
  theme.py           # tasarım sistemi — renk/tipografi/CSS (F1 TV yönü)
  ui.py              # yeniden kullanılabilir bileşenler (page_header, hud_card, ...)
  plot.py            # matplotlib paddock teması
  nav.py             # kenar menü yapısı
design/preview.html  # tasarım sistemi görsel referansı
tests/smoke_test.py  # her sayfayı yükleyip hata var mı kontrol eder
```

## Test

```bash
.venv/Scripts/python tests/smoke_test.py
```

Her router sayfasını Streamlit `AppTest` ile yükler ve kod seviyesinde
exception olmadığını doğrular (ağ hataları uygulamanın kendi try/except'i
tarafından ele alınır, regresyon sayılmaz).

## Dağıtım (Streamlit Community Cloud)

Ana dosya yolu: **`streamlit_app.py`**. (Eski ad
`app_manager_track_fixed_v2_8_verified_career_and_duel.py` idi — Cloud
ayarında güncellenmeli.)
