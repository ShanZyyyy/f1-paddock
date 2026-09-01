# -*- coding: utf-8 -*-
"""Regresyon guvenlik agi.

Her sayfayi Streamlit AppTest ile yukler ve KOD SEVIYESINDE hata (exception)
olmadigini dogrular. Ag hatalari uygulamanin kendi try/except'i tarafindan
st.error/st.info'ya cevrildigi icin burada exception saymaz — amac "bir sayfayi
duzeltince baska sayfa patladi mi?" sorusunu yakalamak.

Kullanim:
    .venv/Scripts/python -m pytest tests/smoke_test.py -q
veya tek dosya:
    .venv/Scripts/python tests/smoke_test.py
"""

import os
import sys

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "streamlit_app.py")

# Router'daki tum sayfa anahtarlari (st.session_state['page'] degerleri)
PAGES = [
    "home", "news", "telemetry", "live", "calendar", "weekend", "story",
    "compare", "drivers", "learn", "favourites", "teams", "standings", "f2f3",
    "assistant", "glossary", "games", "stewarlde", "predict",
]

TIMEOUT = 90


def run_page(page):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.session_state["page"] = page
    # Ag cagrilarini sakinlestir: veri "istenmedi" durumuyla basla.
    at.session_state["home_data_requested"] = False
    at.session_state["telemetry_schedule_requested"] = False
    at.session_state["news_requested"] = False
    # AppTest'te JS yok: prefs bootstrap st.stop()'unu atla (saklı tercih yok gibi davran).
    at.session_state["_fp_no_prefs"] = True
    at.run()
    return at


def check(page):
    at = run_page(page)
    if at.exception:
        exc = at.exception[0]
        return False, f"{type(exc).__name__ if hasattr(exc,'__class__') else exc}: {str(exc)[:300]}"
    return True, "ok"


def main():
    only = sys.argv[1:] or PAGES
    failures = []
    for page in only:
        ok, msg = check(page)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {page:<16} {msg if not ok else ''}".rstrip())
        if not ok:
            failures.append(page)
    print("-" * 50)
    print(f"{len(only) - len(failures)}/{len(only)} sayfa temiz")
    sys.exit(1 if failures else 0)


# pytest icin
def test_pages():
    bad = {p: check(p)[1] for p in PAGES if not check(p)[0]}
    assert not bad, f"Exception atan sayfalar: {bad}"


if __name__ == "__main__":
    main()
