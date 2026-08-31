# -*- coding: utf-8 -*-
"""F1 Sözlüğü sayfası. streamlit_app.py monolitinden ayrıldı (views/ deseni).

Yalnızca core/ ve streamlit'e bağlı — döngüsel import yok.
"""

import streamlit as st

from core import ui as fp_ui
from core.i18n import t as T


def render():
    fp_ui.page_header(T("page.glossary.title"), T("page.glossary.sub"), eyebrow=T("section.paddock"))
    terms = [
        ('2026 Teknolojisi', 'Active Aero', 'Ön ve arka kanadın sürüş koşuluna göre aktif açı değiştirmesidir.', True),
        ('2026 Teknolojisi', 'Corner Mode', 'Virajlarda daha fazla yere basma için kullanılan aktif aero ayarıdır. 2026 ile geldi.', True),
        ('2026 Teknolojisi', 'Straight Mode', 'Düzlükte sürtünmeyi azaltan aktif aero ayarıdır. 📺 2024 ve öncesinde yayında buna **DRS** deniyordu; düzlükteki geçiş bölgelerinin karşılığıdır.', True),
        ('2026 Teknolojisi', 'Overtake Mode', 'Öndeki araca yakın pilotun geçiş için kullanabildiği ek elektrik enerjisi desteğidir. 📺 Yayın diliyle **push-to-pass / ERS hücum** — eski "overtake button" mantığı.', True),
        ('2026 Teknolojisi', 'Boost Mode', 'Pilotun savunma veya hücum için enerji dağıtımını kullandığı güç modudur.', True),
        ('2026 Teknolojisi', 'Recharge', 'Frenleme ve gaz kesme anlarında bataryanın yeniden enerji toplamasıdır.', True),
        ('2026 Teknolojisi', 'MGU-K', 'Fren enerjisini elektrik enerjisine çeviren ve güce katkı sağlayan motor-jeneratördür.', True),
        ('2026 Teknolojisi', 'ERS', 'Enerji geri kazanım ve elektrik enerjisi kullanım sistemidir.', True),
        ('2026 Teknolojisi', 'Power Unit', 'İçten yanmalı motor ve hibrit elektrik bileşenlerinin tamamına verilen isimdir.', True),
        ('2026 Teknolojisi', 'Sürdürülebilir Yakıt', '2026 güç ünitelerinde kullanılan sentetik ve sürdürülebilir kaynaklı yakıttır.', True),
        ('Yarış Hafta Sonu', 'FP1', 'Birinci antrenman seansıdır.', False),
        ('Yarış Hafta Sonu', 'FP2', 'İkinci antrenman seansıdır.', False),
        ('Yarış Hafta Sonu', 'FP3', 'Sıralama öncesindeki son antrenman seansıdır.', False),
        ('Yarış Hafta Sonu', 'Sıralama', 'Yarış başlangıç sırasını belirleyen seanstır.', False),
        ('Yarış Hafta Sonu', 'Q1', 'Sıralamanın ilk eleme bölümüdür.', False),
        ('Yarış Hafta Sonu', 'Q2', 'Sıralamanın ikinci eleme bölümüdür.', False),
        ('Yarış Hafta Sonu', 'Q3', 'Pole pozisyonunu belirleyen son eleme bölümüdür.', False),
        ('Yarış Hafta Sonu', 'Sprint Sıralaması', 'Sprint yarışının başlangıç dizilimini belirleyen kısa sıralama formatıdır.', False),
        ('Yarış Hafta Sonu', 'Sprint', 'Ana yarıştan daha kısa mesafeli ve puan veren yarıştır.', False),
        ('Yarış Hafta Sonu', 'Pole Pozisyonu', 'Ana yarışa ilk sıradan başlama hakkıdır.', False),
        ('Yarış Hafta Sonu', 'Parc Fermé', 'Araç ayarlarının büyük ölçüde kilitlendiği teknik kural dönemidir.', False),
        ('Yarış Hafta Sonu', 'Grid', 'Yarışın başlangıç dizilimidir.', False),
        ('Lastik & Strateji', 'Soft', 'En hızlı fakat genellikle en kısa ömürlü kuru zemin lastiğidir.', False),
        ('Lastik & Strateji', 'Medium', 'Hız ve dayanıklılık dengesi sunan kuru zemin lastiğidir.', False),
        ('Lastik & Strateji', 'Hard', 'Daha dayanıklı, ısınması daha zor kuru zemin lastiğidir.', False),
        ('Lastik & Strateji', 'Intermediate', 'Hafif veya değişken yağmur koşulları için lastiktir.', False),
        ('Lastik & Strateji', 'Wet', 'Yoğun yağmur ve çok ıslak pist için lastiktir.', False),
        ('Lastik & Strateji', 'Stint', 'Aynı lastik setiyle pit stop olmadan atılan tur bölümüdür.', False),
        ('Lastik & Strateji', 'Undercut', 'Rakibinden önce pite girip taze lastikle, o daha eski lastikteyken öne geçmeye çalışmaktır. Yarış tekrarında pit ayracının hemen ardından sıra değişimi görürsen genelde undercut işe yaramıştır.', False),
        ('Lastik & Strateji', 'Overcut', 'Rakip pite girdikten sonra pistte biraz daha kalıp, hızlı turlarla çıkışta öne geçmeye çalışmaktır. Undercut\'ın tersi.', False),
        ('Lastik & Strateji', 'Degradation', 'Lastiğin tur geçtikçe performans kaybetmesidir.', False),
        ('Lastik & Strateji', 'Graining', 'Lastik yüzeyinde oluşan taneciklenmenin yol tutuşunu düşürmesidir.', False),
        ('Lastik & Strateji', 'Blistering', 'Aşırı sıcaklık nedeniyle lastik yüzeyinde kabarcık oluşmasıdır.', False),
        ('Lastik & Strateji', 'Pit Stop', 'Lastik değişimi veya onarım için pit alanına girilmesidir.', False),
        ('Veri & Telemetri', 'Delta', 'İki tur veya iki pilot arasındaki zaman farkıdır — genelde "Δ" simgesiyle gösterilir. Δ +0,3 sn = öndeki 0,3 saniye daha hızlı.', False),
        ('Veri & Telemetri', 'Sektör', 'Pistin zaman ölçülen üç ana parçasından biridir.', False),
        ('Veri & Telemetri', 'Mor Sektör', 'Seansta atılmış en hızlı sektör zamanıdır.', False),
        ('Veri & Telemetri', 'Speed Trap', 'Pistin belirli bir ölçüm noktasındaki resmî hızdır.', False),
        ('Veri & Telemetri', 'Top Speed', 'Bir turdaki en yüksek telemetri hızıdır.', False),
        ('Veri & Telemetri', 'Ortalama Hız', 'Pist uzunluğu ve tur süresinden türetilen ortalama hızdır.', False),
        ('Veri & Telemetri', 'Throttle', 'Gaz pedalının kullanım oranıdır.', False),
        ('Veri & Telemetri', 'Brake', 'Fren pedalının uygulandığı anları gösteren telemetri kanalıdır.', False),
        ('Veri & Telemetri', 'RPM', 'Motorun dakikadaki devir sayısıdır.', False),
        ('Veri & Telemetri', 'Telemetri', 'Araçtan gelen hız, gaz, fren, vites ve konum verilerinin bütünüdür.', False),
        ('Yarış Olayları', 'Safety Car', 'Pistte tehlike olduğunda araçları kontrollü hızda toplayan güvenlik aracıdır.', False),
        ('Yarış Olayları', 'VSC', 'Pistte fiziksel güvenlik aracı olmadan hız sınırı uygulayan sistemdir.', False),
        ('Yarış Olayları', 'Kırmızı Bayrak', 'Seansın güvenlik nedeniyle durdurulduğunu gösterir.', False),
        ('Yarış Olayları', 'Sarı Bayrak', 'Pistte tehlike olduğunu ve geçiş yasağı bulunduğunu gösterir.', False),
        ('Yarış Olayları', 'Track Limits', 'Pist sınırlarının ihlali nedeniyle tur veya ceza riski oluşmasıdır.', False),
        ('Yarış Olayları', 'DNF', 'Pilotun yarışı tamamlayamadığını gösterir.', False),
        ('Yarış Olayları', 'DNS', 'Pilotun yarışa başlayamadığını gösterir.', False),
        ('Yarış Olayları', 'DSQ', 'Pilotun veya takımın yarıştan diskalifiye edilmesidir.', False),
        ('Yarış Olayları', 'Race Control', 'Seans güvenliği, bayraklar ve kararları yöneten yarış kontrol birimidir.', False),
        ('Yarış Olayları', 'Ceza', 'Kural ihlali karşılığında verilen zaman, grid veya yarış içi yaptırımdır.', False),
        ('Pilotluk', 'Apex', 'Virajın ideal çizgideki en iç noktasıdır.', False),
        ('Pilotluk', 'Racing Line', 'Pistte en hızlı tur için tercih edilen ideal çizgidir.', False),
        ('Pilotluk', 'Slipstream', 'Öndeki aracın hava koridorunda sürtünme azalmasıyla hız kazanmadır.', False),
        ('Pilotluk', 'Dirty Air', 'Öndeki aracın bozduğu havanın takip eden aracın yere basmasını azaltmasıdır.', False),
        ('Pilotluk', 'Lift and Coast', 'Yakıt veya enerji yönetimi için fren öncesi gazdan erken çekilmektir.', False),
        ('Pilotluk', 'Late Braking', 'Viraja rakibinden daha geç fren yaparak atak denemektir.', False),
    ]
    category_names = ['Tümü'] + sorted({term[0] for term in terms})
    filter_col, search_col = st.columns([1, 2])
    with filter_col:
        selected_category = st.selectbox('Kategori', category_names)
    with search_col:
        search_text = st.text_input('Terim ara', placeholder='Örn. Active Aero, Under cut, Delta...').strip().lower()
    visible_terms = [term for term in terms if (selected_category == 'Tümü' or term[0] == selected_category) and (not search_text or search_text in (term[1] + ' ' + term[2]).lower())]
    st.caption(f"{len(visible_terms)} terim gösteriliyor")
    for category, term, explanation, is_new in visible_terms:
        badge = "<span class='new-badge'>2026 YENİ</span>" if is_new else f"<span class='term-badge'>{category.upper()}</span>"
        with st.expander(term):
            st.markdown(badge, unsafe_allow_html=True)
            st.markdown(explanation)
