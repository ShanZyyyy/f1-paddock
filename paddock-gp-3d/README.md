# Paddock GP 3D — Faz 0: Teknik Sondaj

Bu klasör, [Komiserler Raporu](../game.html)'nda önerilen 3B yol haritasının
**Faz 0**'ı: tek bir araba + boş bir pist + serbest bir kamera. Amaç güzel
görünmek değil, şunu doğrulamak — Unity + C# + Claude Code iş birliği bu
proje için doğru hissettiriyor mu.

**Önemli:** Bu betikleri ben (Claude) uzak bir bulut oturumunda yazdım ama
Unity Editor'ü açıp senin için sahneyi kuramam — editör GUI tabanlı bir
masaüstü uygulaması, bu betikleri kendi bilgisayarında sen bağlayacaksın.
Aşağıdaki adımlar bunun için var. Bir yerde takılırsan hata mesajını /
ekran görüntüsünü bana ilet, birlikte çözeriz.

## 1. Proje Oluşturma

1. **Unity Hub**'ı aç → **New Project**.
2. Şablon olarak **3D (URP)** seç (Universal Render Pipeline — hafif ve bu
   tarz bir yönetim oyunu için yeterli görsel kalite verir; Faz 0'da ağır
   AAA render pipeline'ına ihtiyacımız yok).
3. Proje adı: `PaddockGP3D`. Konumu istediğin yere kaydet (bu repo'nun
   dışında olması sorun değil — Unity projeleri genelde ayrı tutulur).
4. **Create project** → Unity Editor açılsın.

## 2. Betikleri İçeri Aktar

1. Unity'de `Assets` klasörüne sağ tık → **Create → Folder** → adı `Scripts`.
2. Bu depodaki `paddock-gp-3d/Scripts/` klasörünün **içeriğini** (Track,
   Vehicles, CameraRig alt klasörleriyle birlikte) az önce oluşturduğun
   `Assets/Scripts/` klasörüne kopyala (işletim sistemi dosya gezgininden
   sürükle-bırak yeterli).
3. Unity otomatik olarak derleyecek. Alt tarafta/Console penceresinde
   kırmızı hata **olmamalı**. Hata varsa ekran görüntüsünü bana gönder.

## 3. Sahneyi Kur

### 3.1 Pist
1. Hierarchy'de sağ tık → **Create Empty** → adını `TrackManager` yap.
2. `TrackManager`'ı seç → Inspector'da **Add Component** → `Track Spline`
   yaz, script'i ekle. (Bu otomatik olarak bir `Line Renderer` bileşeni de
   ekleyecek, çünkü script'in üstünde `[RequireComponent(typeof(LineRenderer))]`
   var.)
3. `Line Renderer` bileşeninde **Materials** alanına herhangi bir materyal
   sürükle (yoksa varsayılan pembe/magenta çizgi görürsün — sorun değil,
   sadece görsel).

### 3.2 Araba (yer tutucu)
1. Hierarchy → sağ tık → **3D Object → Cube**. Adını `Car` yap.
2. Cube'un **Scale**'ini `(2, 1, 4)` yap ki araba gibi uzun görünsün.
3. `Car`'a **Add Component** → `Car Follow Path`.
4. Inspector'da beliren **Track** alanına, Hierarchy'den `TrackManager`
   nesnesini sürükle-bırak.

### 3.3 Kamera
1. Hierarchy'de zaten duran **Main Camera**'yı seç.
2. **Add Component** → `Orbit Camera`.
3. **Target** alanına Hierarchy'den `Car` nesnesini sürükle-bırak.

## 4. Çalıştır

**Play** tuşuna bas. Görmen gerekenler:

- ✅ Pist şeklinde (Bahreyn'in genel hatlarını taşıyan, kapalı bir döngü)
  mavi/pembe bir çizgi — düz bir kare veya rastgele bir zikzak **değil**.
- ✅ Küp şeklindeki "araba" bu çizgi üzerinde sabit hızla ilerliyor ve
  virajlarda yön değiştiriyor (dönüşe göre kendini çeviriyor).
- ✅ Sağ fare tuşuna basılı tutup sürükleyince kamera arabanın etrafında
  dönüyor; fare tekerleğiyle yakınlaşıp uzaklaşabiliyorsun.

Bunların hepsi çalışıyorsa **Faz 0 başarılı** — bana ekran görüntüsü/kısa
video gönder, birlikte Faz 1'e (gerçek pist geometrisi: kot farkı, kerb,
bariyer) geçelim.

## Sorun Giderme

| Belirti | Olası sebep |
|---|---|
| Console'da kırmızı hata, hiçbir şey hareket etmiyor | Bir script dosyası eksik kopyalanmış olabilir — üç klasörün de (Track, Vehicles, CameraRig) tam kopyalandığından emin ol. |
| Çizgi görünmüyor ama hata da yok | Line Renderer'ın Materials alanı boş olabilir; varsayılan bir materyal ata. |
| Araba hiç hareket etmiyor | `Car Follow Path` bileşenindeki **Track** alanı boş kalmış olabilir. |
| Kamera dönmüyor | `Orbit Camera` bileşenindeki **Target** alanı boş kalmış olabilir; ayrıca sürüklerken **sağ** fare tuşunu kullandığından emin ol (sol değil). |
| Araba pistin çok altında/üstünde duruyor | `Car Follow Path` üzerindeki **Height Offset** değerini artır/azalt. |

## Neden bu mimari?

- `TrackSpline.Evaluate(distance)` — game.html'deki JS `TRACK.pointAt(frac)`
  ile birebir aynı Catmull-Rom + kümülatif uzunluk tablosu mantığı. Faz 4'te
  gerçek yarış simülasyonunu (tempo, lastik aşınımı, pit) bu 3B sahneye
  bağlarken aynı desen kullanılacak.
- Pist verisi (`BahrainTrackData`) doğrudan `game.html`'deki zorunlu SVG
  yolunun çapa noktalarından türetildi — aynı kaynaktan geliyor, sadece
  Faz 0'da tam bezier eğriliği değil Catmull-Rom yaklaşıklığı kullanılıyor.
  Tam sadakat Faz 1'in konusu.
