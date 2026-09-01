"""Canlı/güncel sezon verisi köprüsü — bağımlılık enjeksiyonu (dependency injection).

`core.paddock_ai` paketi `streamlit_app`'i İMPORT ETMEZ (döngüsel olurdu ve test
edilemez olurdu). Bunun yerine güncel sezon verisi gereken niyetler için
`streamlit_app` bir `LiveData` uygulaması geçirir.

`streamlit_app` tarafında adaptör (örnek):

    class _AppLive(live_data.LiveData):
        def championship(self, year):
            ds, *_ = get_championship_data_stable(year)
            return [{"code": r["Pilot"], "points": float(r["Puan"]),
                     "position": int(r["Sıra"])} for _, r in ds.iterrows()]
        def calendar(self, year):
            return [{"name": e["EventName"], "date": str(e["Session5DateUtc"]),
                     "location": e.get("Location", "")} for e in get_calendar_details(year)]
        def session_top(self, year, event, code):
            table, _ = get_session_results_table(year, event, code)
            return table.fillna("—").to_dict("records")
        def user_prediction(self):
            return {"points": int(fp_ui.get_pref("ps") or 0),
                    "scored": int(fp_ui.get_pref("pn") or 0)}
"""
from __future__ import annotations


class LiveData:
    """Pipeline'ın çağırdığı arayüz. Hiçbiri zorunlu değil — desteklenmeyen
    yöntemler None döndürür ve o niyet 'veri yok' cevabına düşer."""

    def championship(self, year: int) -> list[dict] | None:
        return None

    def calendar(self, year: int) -> list[dict] | None:
        return None

    def session_top(self, year: int, event: str, code: str) -> list[dict] | None:
        return None

    def last_edition(self, event_fragment: str, from_year: int) -> dict | None:
        return None

    def user_prediction(self) -> dict | None:
        return None


NULL = LiveData()
