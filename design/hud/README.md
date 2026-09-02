# Formula Paddock — Yayın HUD

Yayın seviyesinde iki veri HUD'u: **Yarış Tekrarı** ve **Strateji Duvarı**.
F1 TV yayın grafiği / pit duvarı mühendislik ekranı dili. Emoji yok, jenerik
kart ızgarası yok — asimetrik, piksel hassasiyetinde yerleşim.

## Siteye entegrasyon durumu

Bu klasör **tasarım referansıdır** (React/Tailwind çıktısı, tam vizyon).
Siteye (Streamlit) taşınan parçalar `core/hud_kit.py` üzerinden:

| Site parçası | Dosya | Not |
|---|---|---|
| Ortak jeton kiti | `core/hud_kit.py` | `kit_css()` — `theme.TOKENS`'tan türer, tüm iframe HUD'ları paylaşır |
| Telemetri enstrüman rayı | `streamlit_app.py :: telemetry_trace_html` | Sol küme: HIZ / GAZ-FREN / VİTES + Δ, imleçle canlı — yalnız gerçek payload |
| Strateji Duvarı (Gantt) | `streamlit_app.py :: strategy_wall_html` | Tur eksenli, aşınma koyulaşması, pit süreleri, undercut etiketi, en hızlı tur |

Streamlit iframe'i çevrimdışı srcdoc olduğu için site tarafı **saf vanilla-JS/
canvas** — React yok. Bu klasördeki `.jsx` yalnız tam-vizyon referansı.
Testler: `tests/test_hud_kit.py`.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `index.html` | Tek dosya, bağımsız demo (React UMD + Babel CDN). Tarayıcıda aç. |
| `FormulaPaddockHUD.jsx` | React bileşenleri — `RaceReplayHUD`, `StrategyWallHUD`, `FormulaPaddockHUD` (ikisini anahtarlayan kabuk), `MotorsportIcons`. |
| `hud.css` | Tasarım jetonları (`:root`) + tüm bileşen stilleri. Tek tema (koyu), bilinçli. |

## Kullanım (Vite / Next / CRA)

```jsx
import { RaceReplayHUD, StrategyWallHUD } from "./design/hud/FormulaPaddockHUD";

// hud.css bileşen dosyasının içinden import edilir; ayrıca fontları yükle:
// <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">

<RaceReplayHUD />
<StrategyWallHUD />
```

Demodaki veriler (`GRID`, `STINTS`, `SCENARIOS`, `CARS`, `CIRCUIT`) bileşen
dosyasının başında sabit olarak duruyor — canlı telemetriye bağlarken bunları
prop'a çıkar.

## Tasarım kararları

- **Palet** `core/theme.py` "Yayın Arayüzü" jetonlarını yansıtır: mavi çalan
  koyu zemin (`#0a0e14`), 1px çizgiler, marka kırmızısı (`#e10600`) yalnız ince
  detay, cyan (`#35d6c8`) telemetri aksanı. Semantik renkler (amber/green/pink/
  violet) aksandan ayrı: sektör derecesi, kazanç/kayıp, uçurum riski.
- **Tipografi** iki rol: `Inter` (arayüz, 400–800, sıkı negatif tracking) ve
  `JetBrains Mono` (tüm sayısal veri — tur zamanı, delta, sektör, lastik yaşı,
  hız — `tabular-nums`). Etiketler mono 600 / `.15em` / uppercase / `--mute`.
- **Yerleşim** simetrik kart dizisi değil:
  - *Yarış Tekrarı*: solda zamanlama kulesi (332px) · ortada baskın pist
    haritası + hız izi (akışkan) · sağda dikey telemetri enstrüman sütunu
    (248px) · altta tüm sütunları geçen transport çubuğu.
  - *Strateji Duvarı*: ortada stint Gantt'ı kahraman (çapraz tarama pit
    pencereleri + `ŞU AN` çizgisi) · solda strateji fişleri · sağda "ne olurdu"
    senaryo delta paneli + lastik ömrü barı.
- **Mikro etkileşim** `transition: all .25–.3s cubic-bezier`; stint barları
  hover'da 1px yükselir + takım/hamur renginde gölge; kule/fiş satırları hover
  ve odakta ince cyan sol bayrak çizgisi kazanır. `prefers-reduced-motion`
  animasyonları kapatır.
- **İkonlar** hepsi inline `<svg>`, 1.6 stroke, `currentColor` — lastik, hız
  göstergesi, vites, DRS kanadı, ERS şimşeği, pit tabelası. Kütüphane yok.

## Tailwind notu

Proje tasarım sistemi (`core/theme.py`, `design/preview.html`) saf CSS custom
property + sınıf sözlüğü üzerine kurulu; Tailwind kullanılmıyor. Uyumlu kalmak
için bu HUD da aynı dili konuşuyor. Tailwind'e taşımak istersen jetonları
`tailwind.config` içine köprüle:

```js
theme: { extend: {
  colors: {
    night:"#0a0e14", panel:"#121826", raised:"#1a2130",
    line:"#222c3a", ink:"#eef2f7", dim:"#9aa7b8", mute:"#63728a",
    red:"#e10600", cyan:"#35d6c8", amber:"#f5b843", green:"#3ecf8e",
    pink:"#ff5a4d", violet:"#9a8cff",
  },
  fontFamily: { ui:["Inter","system-ui","sans-serif"],
                data:["JetBrains Mono","ui-monospace","monospace"] },
  borderRadius: { s:"6px", m:"9px", l:"13px" },
}}
```

`hud.css` içindeki `.pane`, `.tt-row`, `.stint`, `.nowline` gibi sınıflar
`@apply` ile birebir çevrilebilir; Gantt konumlandırması (yüzde `left`/`width`)
zaten inline stil, dokunmana gerek yok.
