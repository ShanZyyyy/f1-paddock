# -*- coding: utf-8 -*-
"""Geriye dönük uyumluluk kabuğu.

Uygulamanın giriş dosyası artık ``streamlit_app.py``. Bu dosya yalnızca
Streamlit Community Cloud'daki eski "Main file path" ayarı güncellenene kadar
uygulamayı ayakta tutar. İçe aktarma, ``streamlit_app`` modülünün tüm üst
düzey kodunu (sayfa yönlendirici dahil) çalıştırır.

Yeni ayar:  Main file path → streamlit_app.py
"""

import streamlit_app  # noqa: F401  — içe aktarma uygulamayı çalıştırır
