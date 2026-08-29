# -*- coding: utf-8 -*-
"""Ortak test kurulumu: Streamlit'in 'missing ScriptRunContext' uyarilarini sustur."""

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for name in ("streamlit", "streamlit.runtime", "streamlit.runtime.scriptrunner_utils"):
    logging.getLogger(name).setLevel(logging.CRITICAL)
