"""Paddock Asistan — yerel, F1'e kısıtlı, veritabanı odaklı asistan.

Dış LLM / ücretli API YOK. Kural tabanlı NLP (normalize + entity + intent) +
paketli SQLite/JSON üzerinden RAG.

Kullanım:

    from core.paddock_ai import answer
    a = answer("1994 dünya şampiyonu kim?")
    print(a.text, "—", a.source)

Güncel sezon soruları için `streamlit_app` bir LiveData adaptörü geçirir:

    from core.paddock_ai import answer, LiveData
    a = answer("kim lider?", live=my_live_adapter)
"""
from .pipeline import answer
from .retrievers.live_data import NULL as NULL_LIVE
from .retrievers.live_data import LiveData
from .templates import Answer

__all__ = ["answer", "Answer", "LiveData", "NULL_LIVE"]
