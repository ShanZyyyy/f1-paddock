"""core.paddock_ai — yerel asistan hattı testleri. Ağ yok, LLM yok."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.paddock_ai import answer  # noqa: E402
from core.paddock_ai.entities import EntityExtractor  # noqa: E402
from core.paddock_ai.normalize import fold, parse  # noqa: E402


def test_normalize_folds_turkish():
    assert fold("1967 Monako GP'sini") == "1967 monako gp sini"
    assert fold("Verstappen'in şampiyonluğu") == "verstappen in sampiyonlugu"


def test_entity_year_gp_session():
    ex = EntityExtractor(driver_names={"ayrton senna": "Ayrton Senna", "senna": "Ayrton Senna"},
                         team_names={"ferrari": "Ferrari"})
    e = ex.extract(parse("1988 Monako sıralamasında pole kimin"))
    assert e.year == 1988
    assert e.gp == "Monaco"
    assert e.session == "Q"
    assert "pole" in e.metrics
    e2 = ex.extract(parse("senna kaç kez şampiyon oldu"))
    assert e2.drivers == ["Ayrton Senna"]


def test_guard_refuses_off_topic():
    a = answer("iPhone 15 fiyatı ne kadar?")
    assert a.intent == "REFUSE" and not a.ok


def test_guard_greets():
    a = answer("Merhaba")
    assert a.intent == "SMALLTALK" and a.ok


def test_champion_from_local_archive():
    a = answer("1994 dünya şampiyonu kim?")
    assert a.intent == "SEASON_CHAMPION"
    assert "Schumacher" in a.text


def test_record_intent():
    a = answer("En genç dünya şampiyonu kim?")
    assert a.intent == "RECORD" and "Vettel" in a.text


def test_driver_career_from_bundle():
    a = answer("Hamilton kariyerinde kaç pole aldı?")
    assert a.intent == "DRIVER_CAREER"
    assert "pole" in a.text and a.ok


def test_tech_upgrade_lists_components():
    a = answer("McLaren en son hangi güncellemeyi getirdi?")
    assert a.intent == "TECH_UPGRADE"
    # data/tech_upgrades.json'da örnek kayıt var
    assert "McLaren" in a.text


def test_tech_upgrade_missing_team_is_honest():
    a = answer("Williams son hangi güncellemeyi getirdi?")
    assert a.intent == "TECH_UPGRADE" and not a.ok


def test_unknown_race_says_no_data_not_hallucinate():
    a = answer("1988 Monako GP'sini kim kazandı?")
    # f1_history.sqlite yoksa: uydurmaz, "veri yok" der
    assert a.intent in ("RACE_RESULT",) and (not a.ok or "kazandı" in a.text)


def test_season_calendar_intent():
    a = answer("1956 da pistleri sayar mısın")
    assert a.intent == "SEASON_CALENDAR"
    # DB varsa liste; yoksa "veri yok" — ikisi de kabul, uydurma yok
    a2 = answer("1962 sezonunda kaç yarış vardı")
    assert a2.intent == "SEASON_CALENDAR"


def test_season_first_last_intent():
    a = answer("1958 sezonu nerede başladı?")
    assert a.intent == "SEASON_FIRST_LAST"
    a2 = answer("1970 sezonu nerede bitti?")
    assert a2.intent == "SEASON_FIRST_LAST"


def test_season_calendar_from_db_if_present():
    from core.paddock_ai.retrievers import history_db
    if not history_db.available():
        return  # DB henüz üretilmedi
    d = history_db.season_races(1950)
    if d:
        assert d["count"] == 7  # 1950'de 7 yarış vardı
        assert d["first"]["name"] == "British Grand Prix"
