# -*- coding: utf-8 -*-
"""F1 statik veri sabitleri — takim/pilot dizinleri, renkler, tarihi kayitlar.

streamlit_app.py monolitinden ayrildi. Saf veri: st/fastf1/mantik yok.
streamlit_app `from core.f1_constants import *` ile alir (geriye donuk uyum).
"""

DRIVER_TEAMS = {
    "NOR": {"color": "#FF8000"}, "PIA": {"color": "#FF8000"},
    "HAM": {"color": "#E8002D"}, "LEC": {"color": "#E8002D"},
    "ANT": {"color": "#27F4D2"}, "RUS": {"color": "#27F4D2"},
    "VER": {"color": "#3671C6"}, "HAD": {"color": "#3671C6"},
    "GAS": {"color": "#FF87BC"}, "COL": {"color": "#FF87BC"},
    "LAW": {"color": "#6692FF"}, "LIN": {"color": "#6692FF"},
    "OCO": {"color": "#B6BABD"}, "BEA": {"color": "#B6BABD"},
    "ALB": {"color": "#64C4FF"}, "SAI": {"color": "#64C4FF"},
    "HUL": {"color": "#F50537"}, "BOR": {"color": "#F50537"},
    "ALO": {"color": "#229971"}, "STR": {"color": "#229971"},
    "PER": {"color": "#C0C0C0"}, "BOT": {"color": "#C0C0C0"},
}


MEDIA_DRIVER = "https://media.formula1.com/d_driver_fallback_image.png/content/dam/fom-website/drivers/"


TEAM_DIRECTORY_2026 = {
    "Mercedes": {"slug": "mercedes", "color": "#27F4D2", "drivers": [("George Russell", "RUS", "#63", "G/GEORUS01_George_Russell/georus01.png"), ("Kimi Antonelli", "ANT", "#12", "A/ANDANT01_Andrea_Kimi_Antonelli/andant01.png")]},
    "Ferrari": {"slug": "ferrari", "color": "#E8002D", "drivers": [("Charles Leclerc", "LEC", "#16", "C/CHALEC01_Charles_Leclerc/chalec01.png"), ("Lewis Hamilton", "HAM", "#44", "L/LEWHAM01_Lewis_Hamilton/lewham01.png")]},
    "McLaren": {"slug": "mclaren", "color": "#FF8000", "drivers": [("Lando Norris", "NOR", "#1", "L/LANNOR01_Lando_Norris/lannor01.png"), ("Oscar Piastri", "PIA", "#81", "O/OSCPIA01_Oscar_Piastri/oscpia01.png")]},
    "Red Bull Racing": {"slug": "red-bull-racing", "color": "#3671C6", "drivers": [("Max Verstappen", "VER", "#3", "M/MAXVER01_Max_Verstappen/maxver01.png"), ("Isack Hadjar", "HAD", "#6", "I/ISAHAD01_Isack_Hadjar/isahad01.png")]},
    "Alpine": {"slug": "alpine", "color": "#FF87BC", "drivers": [("Pierre Gasly", "GAS", "#10", "P/PIEGAS01_Pierre_Gasly/piegas01.png"), ("Franco Colapinto", "COL", "#43", "F/FRACOL01_Franco_Colapinto/fracol01.png")]},
    "Racing Bulls": {"slug": "racing-bulls", "color": "#6692FF", "drivers": [("Liam Lawson", "LAW", "#30", "L/LIALAW01_Liam_Lawson/lialaw01.png"), ("Arvid Lindblad", "LIN", "#41", "A/ARVLIND01_Arvid_Lindblad/arvlind01.png")]},
    "Haas F1 Team": {"slug": "haas", "color": "#B6BABD", "drivers": [("Esteban Ocon", "OCO", "#31", "E/ESTOCO01_Esteban_Ocon/estoco01.png"), ("Oliver Bearman", "BEA", "#87", "O/OLIBEA01_Oliver_Bearman/olibea01.png")]},
    "Williams": {"slug": "williams", "color": "#64C4FF", "drivers": [("Carlos Sainz", "SAI", "#55", "C/CARSAI01_Carlos_Sainz/carsai01.png"), ("Alexander Albon", "ALB", "#23", "A/ALEALB01_Alexander_Albon/alealb01.png")]},
    "Audi": {"slug": "audi", "color": "#F50537", "drivers": [("Nico Hulkenberg", "HUL", "#27", "N/NICHUL01_Nico_Hulkenberg/nichul01.png"), ("Gabriel Bortoleto", "BOR", "#5", "G/GABBOR01_Gabriel_Bortoleto/gabbor01.png")]},
    "Aston Martin": {"slug": "aston-martin", "color": "#229971", "drivers": [("Fernando Alonso", "ALO", "#14", "F/FERALO01_Fernando_Alonso/feralo01.png"), ("Lance Stroll", "STR", "#18", "L/LANSTR01_Lance_Stroll/lanstr01.png")]},
    "Cadillac": {"slug": "cadillac", "color": "#C0C0C0", "drivers": [("Sergio Perez", "PER", "#11", "S/SERPER01_Sergio_Perez/serper01.png"), ("Valtteri Bottas", "BOT", "#77", "V/VALBOT01_Valtteri_Bottas/valbot01.png")]},
}


OFFICIAL_TEAM_LOGOS = {
    "Mercedes": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/mercedes/2025mercedeslogowhite.webp",
    "Ferrari": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/ferrari/2025ferrarilogolight.webp",
    "McLaren": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/mclaren/2025mclarenlogowhite.webp",
    "Red Bull Racing": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/redbullracing/2025redbullracinglogowhite.webp",
    "Alpine": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/alpine/2025alpinelogowhite.webp",
    "Racing Bulls": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/racingbulls/2025racingbullslogowhite.webp",
    "Haas F1 Team": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/haas/2025haaslogowhite.webp",
    "Williams": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/williams/2025williamslogowhite.webp",
    "Audi": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2026/audi/2026audilogowhite.webp",
    "Aston Martin": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2025/astonmartin/2025astonmartinlogowhite.webp",
    "Cadillac": "https://media.formula1.com/image/upload/c_fit%2Ch_256/q_auto/v1740000001/common/f1/2026/cadillac/2026cadillaclogowhite.webp",
}


TEAM_MEDIA_NAMES = {
    "Mercedes": "mercedes", "Ferrari": "ferrari", "McLaren": "mclaren",
    "Red Bull Racing": "redbullracing", "Alpine": "alpine",
    "Racing Bulls": "racingbulls", "Haas F1 Team": "haas",
    "Williams": "williams", "Audi": "audi", "Aston Martin": "astonmartin",
    "Cadillac": "cadillac",
}


TEAM_HISTORY = {
    "Mercedes": "Mercedes, modern hibrit çağın belirleyici takımıdır. Brackley merkezli ekip, 2014 sonrası dönemde üst üste şampiyonluklarla F1 tarihine geçti.",
    "Ferrari": "Ferrari, 1950'den beri Formula 1'de yarışan tek takımdır. Maranello ekibi, serinin en köklü yarış miraslarından birine sahiptir.",
    "McLaren": "McLaren, Bruce McLaren tarafından kuruldu. Takım; Senna, Prost, Hakkinen ve Hamilton gibi isimlerle F1 tarihinin en önemli ekipleri arasına girdi.",
    "Red Bull Racing": "Red Bull Racing 2005'te F1'e katıldı. Takım, önce Vettel dönemi ardından Verstappen dönemiyle şampiyonluklar kazandı.",
    "Alpine": "Alpine markası, Renault'nun Formula 1 mirasını temsil eder. Enstone merkezli takım, geçmişte Renault adıyla dünya şampiyonlukları yaşadı.",
    "Racing Bulls": "Faenza merkezli ekip, Red Bull'un genç yetenek programıyla bağlantılıdır. Takım geçmişte Toro Rosso ve AlphaTauri isimleriyle yarıştı.",
    "Haas F1 Team": "Haas, 2016'da Formula 1'e girdi. Amerikan lisanslı ekip, modern F1'in en genç takımlarından biridir.",
    "Williams": "Williams, Formula 1'in en başarılı bağımsız takımlarındandır. Frank Williams'ın kurduğu ekip, birçok sürücü ve takımlar şampiyonluğu elde etti.",
    "Audi": "Audi 2026'da Formula 1'e fabrika takımı olarak katıldı. Proje, markanın uzun motorsporları geçmişini F1'e taşıyor.",
    "Aston Martin": "Aston Martin adı F1'de ilk kez 1959'da göründü; modern fabrika takımı ise Silverstone merkezli yapının devamıdır.",
    "Cadillac": "Cadillac, 2026'da Formula 1 gridine katılan yeni Amerikan fabrikacı markadır. Takım, serinin 11. ekibi olarak yarışıyor.",
}


TEAM_LEADERSHIP_2026 = {
    "Mercedes": {"name": "Toto Wolff", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/fom-website/2025/Qatar/GettyImages-2249023480.webp", "bio": "Mercedes takım patronu. Oyunda strateji ve liderlik bonusu sağlar.", "strategy": 4, "reliability": 3},
    "Ferrari": {"name": "Fred Vasseur", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Brazil___Previews/2245297636.webp", "bio": "Ferrari takım patronu. Oyunda pit duvarı ve yarış temposu bonusu sağlar.", "strategy": 4, "reliability": 2},
    "McLaren": {"name": "Andrea Stella", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Brazil/2245811681.webp", "bio": "McLaren takım patronu. Oyunda lastik ve strateji bonusu sağlar.", "strategy": 5, "reliability": 2},
    "Red Bull Racing": {"name": "Laurent Mekies", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Abu_Dhabi___Practice/2250117585.webp", "bio": "Red Bull Racing takım patronu. Oyunda performans ve karar hızı bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Alpine": {"name": "Steve Nielsen", "role": "Yönetim Ekibi", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2024/F1_Grand_Prix_of_Austria___Sprint__Qualifying/2159773387.webp", "bio": "Alpine yönetim ekibi. Oyunda denge ve geliştirme bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Racing Bulls": {"name": "Alan Permane", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Abu_Dhabi___Previews/2249891571.webp", "bio": "Racing Bulls takım patronu. Oyunda geliştirme ve pit kararı bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Haas F1 Team": {"name": "Ayao Komatsu", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Las_Vegas___Previews/2247510876.webp", "bio": "Haas takım patronu. Oyunda ayar ve güvenilirlik bonusu sağlar.", "strategy": 2, "reliability": 4},
    "Williams": {"name": "James Vowles", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Las_Vegas___Previews/2247516844.webp", "bio": "Williams takım patronu. Oyunda uzun vadeli gelişim bonusu sağlar.", "strategy": 4, "reliability": 3},
    "Audi": {"name": "Jonathan Wheatley", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/fom-website/2025/Austria/GettyImages-2222409477.webp", "bio": "Audi takım patronu. Oyunda operasyon ve güvenilirlik bonusu sağlar.", "strategy": 3, "reliability": 4},
    "Aston Martin": {"name": "Adrian Newey", "role": "Takım Patronu / Teknik Ortak", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/trackside-images/2025/F1_Grand_Prix_of_Monaco___Previews/2216517332.webp", "bio": "Aston Martin teknik yönetimi. Oyunda aerodinami ve sıralama bonusu sağlar.", "strategy": 3, "reliability": 3},
    "Cadillac": {"name": "Graeme Lowdon", "role": "Takım Patronu", "photo": "https://media.formula1.com/image/upload/t_16by9North/c_lfill%2Cw_3392/q_auto/v1740000001/fom-website/2025/Cadillac%20%28GM%29/GettyImages-2233817654.webp", "bio": "Cadillac takım patronu. Oyunda yeni takım uyumu bonusu sağlar.", "strategy": 3, "reliability": 3},
}


GAME_ENGINEERING_PACKAGES = {
    "Strategist": {"title": "Pit Duvarı Stratejisti", "description": "Lastik ömrü, undercut ve pit penceresi odaklı oyun paketi.", "strategy": 5, "pace": 1, "reliability": 0},
    "Performance": {"title": "Araç Performans Lideri", "description": "Ayar, sıralama hızı ve yarış temposu odaklı oyun paketi.", "strategy": 1, "pace": 5, "reliability": 1},
    "Reliability": {"title": "Güvenilirlik Şefi", "description": "Motor, soğutma ve uzun stint riski odaklı oyun paketi.", "strategy": 1, "pace": 1, "reliability": 5},
}


DRIVER_BIRTHDAYS = {
    'RUS': '1998-02-15', 'ANT': '2006-08-25', 'LEC': '1997-10-16', 'HAM': '1985-01-07',
    'NOR': '1999-11-13', 'PIA': '2001-04-06', 'VER': '1997-09-30', 'HAD': '2004-09-28',
    'GAS': '1996-02-07', 'COL': '2003-05-27', 'LAW': '2002-02-11', 'LIN': '2007-08-08',
    'OCO': '1996-09-17', 'BEA': '2005-05-08', 'SAI': '1994-09-01', 'ALB': '1996-03-23',
    'HUL': '1987-08-19', 'BOR': '2004-10-14', 'ALO': '1981-07-29', 'STR': '1998-10-29',
    'PER': '1990-01-26', 'BOT': '1989-08-28',
}


DRIVER_CAREER_PROFILE = {
    'RUS': {'wins': 7, 'podiums': 29, 'bio': 'Williams ile başlayan kariyerini Mercedes liderliğine taşıyan İngiliz pilot; GP3 ve F2 şampiyonluklarından sonra F1’de istikrarlı hızını öne çıkardı.', 'moment': 'İlk Grand Prix galibiyetini 2022 Sao Paulo’da aldı.'},
    'ANT': {'wins': 5, 'podiums': 8, 'bio': 'Mercedes genç sürücü programından gelen İtalyan pilot, tek koltuklu serilerdeki hızlı yükselişiyle F1’e adım attı.', 'moment': '2026’da ilk galibiyetini alarak Mercedes’in genç kuşağının öne çıkan ismi oldu.'},
    'LEC': {'wins': 8, 'podiums': 46, 'bio': 'Monakolu sürücü Ferrari Akademisi üzerinden F1’e geldi. Tek tur hızı ve lastik yönetimi onu gridin en güçlü isimlerinden biri yaptı.', 'moment': '2019 Belçika GP, ilk F1 galibiyeti ve Ferrari ile dönüm noktasıydı.'},
    'HAM': {'wins': 106, 'podiums': 204, 'bio': 'Yedi kez dünya şampiyonu olan Hamilton, kartingden F1’e uzanan kariyerinde rekorları ve uzun soluklu yarış yönetimiyle tanındı.', 'moment': '2008’de ilk dünya şampiyonluğunu son virajlarda gelen dramatik Brezilya finalinde kazandı.'},
    'NOR': {'wins': 10, 'podiums': 31, 'bio': 'McLaren’in genç programından yetişen Norris, güçlü yağmur sürüşleri ve agresif tek tur temposuyla modern gridin lider isimlerinden biri oldu.', 'moment': '2024 Miami GP ilk F1 galibiyetiydi; ardından şampiyonluk mücadelesine yerleşti.'},
    'PIA': {'wins': 7, 'podiums': 18, 'bio': 'Avustralyalı pilot, Formula Renault, F3 ve F2 şampiyonluklarını art arda kazanarak F1’e yükseldi.', 'moment': '2024 Macaristan GP’de ilk F1 galibiyetini alarak McLaren tarihinde yerini aldı.'},
    'VER': {'wins': 67, 'podiums': 129, 'bio': 'Çok genç yaşta F1’e çıkan Hollandalı, Red Bull ile çok sayıda şampiyonluk ve galibiyet mücadelesi verdi.', 'moment': '2016 İspanya GP’de ilk Red Bull yarışında zafere ulaşarak en genç yarış galibi oldu.'},
    'HAD': {'wins': 0, 'podiums': 1, 'bio': 'Fransız sürücü, Red Bull genç programının F1’e taşıdığı hızlı tek tur yeteneklerinden biri olarak öne çıktı.', 'moment': 'İlk F1 podyumu, genç kariyerinin önemli kilometre taşlarından biri oldu.'},
    'GAS': {'wins': 1, 'podiums': 5, 'bio': 'Normandiya kökenli Gasly, Formula Renault Eurocup şampiyonluğundan sonra F1’e yükseldi ve dayanıklılığıyla tanındı.', 'moment': '2020 İtalya GP’de kazandığı galibiyet, AlphaTauri için unutulmaz bir zaferdi.'},
    'COL': {'wins': 0, 'podiums': 0, 'bio': 'Arjantinli pilot, Williams ile F1’e giriş yaptıktan sonra tek tur temposu ve cesur geçişleriyle dikkat çekti.', 'moment': 'F1’e ilk puanlarını 2024 Azerbaycan GP hafta sonunda getirdi.'},
    'LAW': {'wins': 0, 'podiums': 0, 'bio': 'Yeni Zelandalı sürücü, Super Formula’daki güçlü performansının ardından Red Bull yapısında F1 fırsatı buldu.', 'moment': 'İlk F1 yarışlarında puan alarak programdaki yerini sağlamlaştırdı.'},
    'LIN': {'wins': 0, 'podiums': 0, 'bio': 'İngiliz genç sürücü, karting ve tek koltuklu serilerden F1 gridine yükselen yeni neslin temsilcisi.', 'moment': '2026 F1 başlangıcı, kariyerinin en büyük basamağıdır.'},
    'OCO': {'wins': 1, 'podiums': 3, 'bio': 'Fransız pilot, kartingden F3 ve GP3 şampiyonluğuna uzanan yolculuğun ardından F1’de yerini aldı.', 'moment': '2021 Macaristan GP’de kazandığı yarış, hem kendi hem Alpine/Renault mirası için özel bir zaferdi.'},
    'BEA': {'wins': 0, 'podiums': 1, 'bio': 'Ferrari Akademisi kökenli İngiliz sürücü, F2’deki yükselişinin ardından F1’de hızla dikkat çekti.', 'moment': '2024 Azerbaycan GP’deki podyum, çaylak sezonunun en büyük anıydı.'},
    'SAI': {'wins': 4, 'podiums': 27, 'bio': 'İspanyol sürücü, Red Bull genç programından Toro Rosso üzerinden F1’e yükseldi; temiz yarış yönetimiyle bilinir.', 'moment': '2022 Britanya GP’de ilk F1 galibiyetini aldı.'},
    'ALB': {'wins': 0, 'podiums': 2, 'bio': 'Tayland bayrağı altında yarışan Albon, zorlu bir ilk F1 döneminden sonra Williams ile kariyerini yeniden kurdu.', 'moment': '2020’de aldığı iki podyum, ilk F1 sezonlarının güçlü notlarıydı.'},
    'HUL': {'wins': 0, 'podiums': 0, 'bio': 'Alman sürücü, GP2 şampiyonluğunun ardından uzun F1 deneyimini teknik geri bildirim gücüyle birleştirdi.', 'moment': '2015 Le Mans 24 Saat zaferi, F1 dışındaki en önemli başarısıdır.'},
    'BOR': {'wins': 0, 'podiums': 0, 'bio': 'Brezilyalı sürücü, Formula 3 ve Formula 2 başarılarından sonra F1’e çıkan yeni nesil yeteneklerden biri.', 'moment': 'F1’deki ilk puanları, Audi projesi için önemli bir kilometre taşıdır.'},
    'ALO': {'wins': 32, 'podiums': 106, 'bio': 'İki kez dünya şampiyonu Alonso, uzun kariyerini farklı takımlarda rekabetçi kalmayı başararak sürdürdü.', 'moment': '2005’te aldığı ilk dünya şampiyonluğu, Schumacher dönemini sona erdirdi.'},
    'STR': {'wins': 0, 'podiums': 3, 'bio': 'Kanadalı sürücü, tek turdaki doğal hızı ve yağmur koşullarındaki performansıyla bilinir.', 'moment': '2017 Azerbaycan GP podyumu, çaylak sezonunun unutulmaz anıydı.'},
    'PER': {'wins': 6, 'podiums': 39, 'bio': 'Meksikalı sürücü, uzun stintlerde lastik yönetimi ve savunma becerisiyle öne çıktı.', 'moment': '2020 Sakhir GP’de ilk F1 galibiyetini aldı.'},
    'BOT': {'wins': 10, 'podiums': 67, 'bio': 'Fin sürücü, Williams’tan Mercedes’e geçerek galibiyetler ve takımlar şampiyonlukları mücadelesinde rol aldı.', 'moment': '2017 Rusya GP, ilk F1 galibiyetiydi.'},
}


JUNIOR_TEAM_SLUGS = {
    'Invicta Racing': 'invictaracing', 'Hitech': 'hitech', 'Campos Racing': 'camposracing',
    'DAMS Lucas Oil': 'damslucasoil', 'MP Motorsport': 'mpmotorsport', 'PREMA Racing': 'premaracing',
    'Rodin Motorsport': 'rodinmotorsport', 'ART Grand Prix': 'artgrandprix', 'AIX Racing': 'aixracing',
    'Van Amersfoort Racing': 'vanamersfoortracing', 'TRIDENT': 'trident',
}


COUNTRY_FLAGS = {
    'Australia': '🇦🇺', 'China': '🇨🇳', 'Japan': '🇯🇵', 'Bahrain': '🇧🇭',
    'Saudi Arabia': '🇸🇦', 'United States': '🇺🇸', 'Italy': '🇮🇹',
    'Monaco': '🇲🇨', 'Spain': '🇪🇸', 'Canada': '🇨🇦', 'Austria': '🇦🇹',
    'Great Britain': '🇬🇧', 'Belgium': '🇧🇪', 'Hungary': '🇭🇺',
    'Netherlands': '🇳🇱', 'Azerbaijan': '🇦🇿', 'Singapore': '🇸🇬',
    'Mexico': '🇲🇽', 'Brazil': '🇧🇷', 'Qatar': '🇶🇦', 'United Arab Emirates': '🇦🇪',
}


COUNTRY_CODES = {
    'Australia': 'au', 'China': 'cn', 'Japan': 'jp', 'Bahrain': 'bh', 'Saudi Arabia': 'sa',
    'United States': 'us', 'Italy': 'it', 'Monaco': 'mc', 'Spain': 'es', 'Canada': 'ca',
    'Austria': 'at', 'Great Britain': 'gb', 'Belgium': 'be', 'Hungary': 'hu', 'Netherlands': 'nl',
    'Azerbaijan': 'az', 'Singapore': 'sg', 'Mexico': 'mx', 'Brazil': 'br', 'Qatar': 'qa',
    'United Arab Emirates': 'ae',
}


DRIVER_DISPLAY = {
    'ANT': ('it', 'A. Antonelli'), 'HAM': ('gb', 'L. Hamilton'), 'RUS': ('gb', 'G. Russell'),
    'LEC': ('mc', 'C. Leclerc'), 'NOR': ('gb', 'L. Norris'), 'PIA': ('au', 'O. Piastri'),
    'VER': ('nl', 'M. Verstappen'), 'HAD': ('fr', 'I. Hadjar'), 'GAS': ('fr', 'P. Gasly'),
    'COL': ('ar', 'F. Colapinto'), 'LAW': ('nz', 'L. Lawson'), 'LIN': ('gb', 'A. Lindblad'),
    'OCO': ('fr', 'E. Ocon'), 'BEA': ('gb', 'O. Bearman'), 'SAI': ('es', 'C. Sainz'),
    'ALB': ('th', 'A. Albon'), 'HUL': ('de', 'N. Hülkenberg'), 'BOR': ('br', 'G. Bortoleto'),
    'ALO': ('es', 'F. Alonso'), 'STR': ('ca', 'L. Stroll'), 'PER': ('mx', 'S. Pérez'),
    'BOT': ('fi', 'V. Bottas'),
}


TEAM_NAME_ALIASES = {
    'Red Bull': 'Red Bull Racing',
    'Oracle Red Bull Racing': 'Red Bull Racing',
    'Visa Cash App RB': 'Racing Bulls',
    'RB': 'Racing Bulls',
    'Haas': 'Haas F1 Team',
    'MoneyGram Haas F1 Team': 'Haas F1 Team',
    'Kick Sauber': 'Audi',
    'Stake F1 Team Kick Sauber': 'Audi',
    'Sauber': 'Audi',
    'BWT Alpine F1 Team': 'Alpine',
    'Alpine F1 Team': 'Alpine',
    'Mercedes-AMG PETRONAS F1 Team': 'Mercedes',
    'Scuderia Ferrari': 'Ferrari',
    'Scuderia Ferrari HP': 'Ferrari',
    'McLaren Formula 1 Team': 'McLaren',
}


STEWARDLE_META = {
    # ülke kodu, gerçek F1 ilk sezonu, dünya şampiyonluğu
    'RUS': ('GB', 2019, 0), 'ANT': ('IT', 2025, 0),
    'LEC': ('MC', 2018, 0), 'HAM': ('GB', 2007, 7),
    'NOR': ('GB', 2019, 1), 'PIA': ('AU', 2023, 0),
    'VER': ('NL', 2015, 4), 'HAD': ('FR', 2025, 0),
    'GAS': ('FR', 2017, 0), 'COL': ('AR', 2024, 0),
    'LAW': ('NZ', 2023, 0), 'LIN': ('GB', 2026, 0),
    'OCO': ('FR', 2016, 0), 'BEA': ('GB', 2024, 0),
    'SAI': ('ES', 2015, 0), 'ALB': ('TH', 2019, 0),
    'HUL': ('DE', 2010, 0), 'BOR': ('BR', 2025, 0),
    'ALO': ('ES', 2001, 2), 'STR': ('CA', 2017, 0),
    'PER': ('MX', 2011, 0), 'BOT': ('FI', 2013, 0),
}


TEAM_LIVERY_ACCENTS = {
    'Mercedes': '#071f22', 'Ferrari': '#ffe7df', 'McLaren': '#17191d',
    'Red Bull Racing': '#ffcf32', 'Alpine': '#1540a0', 'Racing Bulls': '#ffffff',
    'Haas F1 Team': '#d92431', 'Williams': '#163e8c', 'Audi': '#111111',
    'Aston Martin': '#d7f6ee', 'Cadillac': '#1e1e24',
}


DRIVER_GAME_STATS = {
    'VER': (96, 97, 94, 92), 'NOR': (95, 95, 94, 92), 'PIA': (93, 94, 89, 93), 'LEC': (95, 93, 90, 88),
    'HAM': (91, 92, 93, 91), 'RUS': (92, 93, 91, 91), 'ANT': (88, 89, 86, 88), 'HAD': (83, 84, 82, 83),
    'GAS': (85, 86, 87, 88), 'COL': (80, 82, 84, 80), 'LAW': (82, 83, 83, 82), 'LIN': (78, 79, 80, 77),
    'OCO': (84, 85, 86, 86), 'BEA': (85, 84, 82, 81), 'SAI': (88, 89, 88, 90), 'ALB': (87, 88, 90, 88),
    'HUL': (83, 84, 86, 89), 'BOR': (80, 81, 82, 80), 'ALO': (91, 91, 94, 93), 'STR': (78, 80, 81, 79),
    'PER': (84, 85, 86, 90), 'BOT': (83, 84, 86, 88),
}


TEAM_GAME_PACE = {
    'McLaren': 9, 'Mercedes': 8, 'Ferrari': 8, 'Red Bull Racing': 7, 'Aston Martin': 5,
    'Williams': 4, 'Racing Bulls': 3, 'Alpine': 2, 'Haas F1 Team': 2, 'Audi': 2, 'Cadillac': 1,
}


CAREER_ROUNDS = [
    ('Bahrain', 'technical'), ('Monaco', 'qualifying'), ('Silverstone', 'high_speed'), ('Hungaroring', 'tyres'),
    ('Spa', 'mixed'), ('Monza', 'power'), ('Singapore', 'street'), ('Austin', 'mixed'), ('Interlagos', 'wet'), ('Abu Dhabi', 'technical'),
]


F1_GAME_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


F1_WORLD_CHAMPIONS = {
    1950:'Giuseppe Farina',1951:'Juan Manuel Fangio',1952:'Alberto Ascari',1953:'Alberto Ascari',1954:'Juan Manuel Fangio',1955:'Juan Manuel Fangio',1956:'Jack Brabham',1957:'Juan Manuel Fangio',1958:'Mike Hawthorn',1959:'Jack Brabham',
    1960:'Jack Brabham',1961:'Phil Hill',1962:'Graham Hill',1963:'Jim Clark',1964:'John Surtees',1965:'Jim Clark',1966:'Jack Brabham',1967:'Denny Hulme',1968:'Graham Hill',1969:'Jackie Stewart',
    1970:'Jochen Rindt',1971:'Jackie Stewart',1972:'Emerson Fittipaldi',1973:'Jackie Stewart',1974:'Emerson Fittipaldi',1975:'Niki Lauda',1976:'James Hunt',1977:'Niki Lauda',1978:'Mario Andretti',1979:'Jody Scheckter',
    1980:'Alan Jones',1981:'Nelson Piquet',1982:'Keke Rosberg',1983:'Nelson Piquet',1984:'Niki Lauda',1985:'Alain Prost',1986:'Alain Prost',1987:'Nelson Piquet',1988:'Ayrton Senna',1989:'Alain Prost',
    1990:'Ayrton Senna',1991:'Ayrton Senna',1992:'Nigel Mansell',1993:'Alain Prost',1994:'Michael Schumacher',1995:'Michael Schumacher',1996:'Damon Hill',1997:'Jacques Villeneuve',1998:'Mika Hakkinen',1999:'Mika Hakkinen',
    2000:'Michael Schumacher',2001:'Michael Schumacher',2002:'Michael Schumacher',2003:'Michael Schumacher',2004:'Michael Schumacher',2005:'Fernando Alonso',2006:'Fernando Alonso',2007:'Kimi Raikkonen',2008:'Lewis Hamilton',2009:'Jenson Button',
    2010:'Sebastian Vettel',2011:'Sebastian Vettel',2012:'Sebastian Vettel',2013:'Sebastian Vettel',2014:'Lewis Hamilton',2015:'Lewis Hamilton',2016:'Nico Rosberg',2017:'Lewis Hamilton',2018:'Lewis Hamilton',2019:'Lewis Hamilton',
    2020:'Lewis Hamilton',2021:'Max Verstappen',2022:'Max Verstappen',2023:'Max Verstappen',2024:'Max Verstappen',
}


F1_RECORD_FACTS_V19 = {
    'most_wins_single_season': 'Bir sezonda en cok Grand Prix galibiyeti rekoru, 2023 sezonunda 19 galibiyet alan Max Verstappen\'e aittir.',
    'most_titles': 'Dunya sampiyonlugu rekoru yedi ile Lewis Hamilton ve Michael Schumacher tarafindan paylasilir.',
    'most_wins': 'Grand Prix galibiyeti rekoru Lewis Hamilton\'a aittir.',
    'most_poles': 'Pole pozisyonu rekoru Lewis Hamilton\'a aittir.',
    'youngest_champion': 'En genc Formula 1 dunya sampiyonu Sebastian Vettel\'dir; 2010 sezonunda 23 yasindayken sampiyon oldu.',
}


STEWARDLE_ACTIVE_API_IDS_V24 = {
    'RUS': 'russell', 'ANT': 'antonelli', 'HAM': 'hamilton', 'LEC': 'leclerc',
    'NOR': 'norris', 'PIA': 'piastri', 'VER': 'max_verstappen', 'HAD': 'hadjar',
    'LAW': 'lawson', 'LIN': 'lindblad', 'GAS': 'gasly', 'COL': 'colapinto',
    'OCO': 'ocon', 'BEA': 'bearman', 'SAI': 'sainz', 'ALB': 'albon',
    'HUL': 'hulkenberg', 'BOR': 'bortoleto', 'ALO': 'alonso', 'STR': 'stroll',
    'PER': 'perez', 'BOT': 'bottas',
}


CAREER_TITLES_V27 = {
    'HAM': 7,
    'VER': 4,
    'ALO': 2,
}


PIT_WALL_PERSONNEL_2026 = {
    "Mercedes": {
        "principal": "Toto Wolff",
        "strategy": "Rosie Wait",
        "chief": "James Allison",
        "engineers": [("George Russell", "Marcus Dudley"), ("Kimi Antonelli", "Peter Bonnington")],
        "source": "https://www.mercedesamgf1.com/team",
    },
    "Ferrari": {
        "principal": "Fred Vasseur",
        "strategy": "Ravin Jain",
        "chief": "Loïc Serra",
        "engineers": [("Charles Leclerc", "Bryan Bozzi"), ("Lewis Hamilton", "Riccardo Adami")],
        "source": "https://www.ferrari.com/en-EN/formula1/team",
    },
    "McLaren": {
        "principal": "Andrea Stella",
        "strategy": "Randeep Singh",
        "chief": "Rob Marshall",
        "engineers": [("Lando Norris", "Will Joseph"), ("Oscar Piastri", "Tom Stallard")],
        "source": "https://www.mclaren.com/racing/formula-1/2026/who-sits-on-mclarens-pit-wall/",
    },
    "Red Bull Racing": {
        "principal": "Laurent Mekies",
        "strategy": "Hannah Schmitz",
        "chief": "Pierre Waché",
        "engineers": [("Max Verstappen", "Gianpiero Lambiase"), ("Isack Hadjar", "Richard Wood")],
        "source": "https://www.redbullracing.com/int-en/projects/bulls-guide-to-the-pit-wall/bulls-guide-to-the-pit-wall-hot-seats",
    },
    "Alpine": {
        "principal": "Steve Nielsen",
        "strategy": "Kamuya açık değil",
        "chief": "David Sanchez",
        "engineers": [("Pierre Gasly", "John Howard"), ("Franco Colapinto", "Stuart Barlow")],
        "source": "https://www.alpinef1.com/team",
    },
    "Racing Bulls": {
        "principal": "Alan Permane",
        "strategy": "Kamuya açık değil",
        "chief": "Guillaume Cattelani",
        "engineers": [("Liam Lawson", "Mattia Spini"), ("Arvid Lindblad", "Pierre Hamelin")],
        "source": "https://www.visacashapprb.com/en/team",
    },
    "Haas F1 Team": {
        "principal": "Ayao Komatsu",
        "strategy": "Mike Caulfield",
        "chief": "Andrea De Zordo",
        "engineers": [("Esteban Ocon", "Francesco Nenci"), ("Oliver Bearman", "Ronan O'Hare")],
        "source": "https://www.haasf1team.com/our-team",
    },
    "Williams": {
        "principal": "James Vowles",
        "strategy": "Kamuya açık değil",
        "chief": "Pat Fry",
        "engineers": [("Carlos Sainz", "Gaëtan Jego"), ("Alexander Albon", "James Urwin")],
        "source": "https://www.williamsf1.com/team",
    },
    "Audi": {
        "principal": "Jonathan Wheatley",
        "strategy": "Kamuya açık değil",
        "chief": "Mattia Binotto",
        "engineers": [("Nico Hülkenberg", "Steven Petrik"), ("Gabriel Bortoleto", "José Manuel López")],
        "source": "https://www.audi.com/en/sport/motorsport/formula-1/",
    },
    "Aston Martin": {
        "principal": "Adrian Newey",
        "strategy": "Andy Cowell",
        "chief": "Enrico Cardile",
        "engineers": [("Fernando Alonso", "Chris Cronin"), ("Lance Stroll", "Andrew Vizard")],
        "source": "https://www.astonmartinf1.com/en-GB/news/announcement/aston-martin-aramco-announces-changes-to-leadership-structure",
    },
    "Cadillac": {
        "principal": "Graeme Lowdon",
        "strategy": "Kamuya açık değil",
        "chief": "Pat Symonds",
        "engineers": [("Sergio Perez", "Kamuya açık değil"), ("Valtteri Bottas", "Kamuya açık değil")],
        "source": "https://www.cadillacf1team.com/",
    },
}


__all__ = [
    'DRIVER_TEAMS',
    'MEDIA_DRIVER',
    'TEAM_DIRECTORY_2026',
    'OFFICIAL_TEAM_LOGOS',
    'TEAM_MEDIA_NAMES',
    'TEAM_HISTORY',
    'TEAM_LEADERSHIP_2026',
    'GAME_ENGINEERING_PACKAGES',
    'DRIVER_BIRTHDAYS',
    'DRIVER_CAREER_PROFILE',
    'JUNIOR_TEAM_SLUGS',
    'COUNTRY_FLAGS',
    'COUNTRY_CODES',
    'DRIVER_DISPLAY',
    'TEAM_NAME_ALIASES',
    'STEWARDLE_META',
    'TEAM_LIVERY_ACCENTS',
    'DRIVER_GAME_STATS',
    'TEAM_GAME_PACE',
    'CAREER_ROUNDS',
    'F1_GAME_POINTS',
    'F1_WORLD_CHAMPIONS',
    'F1_RECORD_FACTS_V19',
    'STEWARDLE_ACTIVE_API_IDS_V24',
    'CAREER_TITLES_V27',
    'PIT_WALL_PERSONNEL_2026',
]
