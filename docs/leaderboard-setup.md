# Haftalık Skor Tablosu — Supabase Kurulumu

Oyun Merkezi'ndeki **Haftalık Sıralama** bloğu, haftalık XP'yi Supabase'de tek
bir tabloda toplar. Yapılandırılmazsa blok kendini gizler; oyunlar etkilenmez.

Kimlik doğrulama yok: her tarayıcı rastgele bir `player_id` üretir, kullanıcı bir
takma ad seçer. `anon` anahtarı herkese açıktır (ön yüz anahtarıdır); ne
yapılabileceği aşağıdaki RLS politikalarıyla sınırlanır.

## 1. Proje

1. <https://supabase.com> → **New project** (ücretsiz katman yeter).
2. Bir bölge seç, proje şifresini kaydet, oluştur.

## 2. Tablo + politikalar

Supabase panelinde **SQL Editor** → yeni sorgu → şunu çalıştır:

```sql
create table if not exists public.weekly_scores (
  player_id  text        not null,
  week       text        not null,          -- "2026-H36"
  name       text        not null default 'Anonim',
  xp         integer     not null default 0 check (xp >= 0 and xp <= 100000),
  updated_at timestamptz not null default now(),
  primary key (player_id, week)
);

alter table public.weekly_scores enable row level security;

-- Herkes (anon) o haftanın tablosunu okuyabilir
create policy "read scores" on public.weekly_scores
  for select using (true);

-- Herkes kendi satırını ekley/güncelleyebilir (upsert). Kimlik olmadığı için
-- player_id sahteciliği teknik olarak mümkün — hobi skor tablosu için kabul.
create policy "insert own" on public.weekly_scores
  for insert with check (char_length(player_id) between 3 and 64);
create policy "update own" on public.weekly_scores
  for update using (true) with check (char_length(player_id) between 3 and 64);
```

`xp` sütunundaki `check` üst sınırı kaba bir kötüye-kullanım freni; uygulama zaten
`_game_week_v8` kutusundaki değeri gönderir.

## 3. Anahtarlar

Panel → **Project Settings → API**:

| Alan | Nereye |
|---|---|
| **Project URL** | `SUPABASE_URL` |
| **Project API keys → `anon` `public`** | `SUPABASE_ANON_KEY` |

> `service_role` anahtarını **kullanma** — o gizli ve tam yetkili.

### Yerel

`.streamlit/secrets.toml` (git'e girmez):

```toml
SUPABASE_URL = "https://xxxxxxxxxxxx.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOi..."
```

### Streamlit Community Cloud

Uygulama → **Settings → Secrets** → aynı iki satırı yapıştır → Save.

## 4. Doğrulama

Oyun Merkezi'ni aç → bir oyun oyna (XP kazan) → **Haftalık Sıralama** bloğu
görünür, takma ad ister, sonra skorun tabloda çıkar. Supabase panelinde
**Table Editor → weekly_scores** satırı görürsün.

## Sınırlar

- Kimlik yok → takma ad ve `player_id` sahteciliği önlenmez. Hobi F1 sitesi için kabul.
- Sunucu tarafı XP doğrulaması yok (yalnız sütun `check`'i). İleride bir Postgres
  fonksiyonu / Edge Function ile sıkılaştırılabilir.
- Haftalık satırlar birikir; istersen eski haftaları periyodik silen bir cron ekle.
