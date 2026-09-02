#!/usr/bin/env python
"""Dev-server launcher for the Browser pane / preview_start.

The preview harness assigns a free port via the ``PORT`` env var (autoPort).
Streamlit does not read ``PORT`` itself, so this shim forwards it as
``--server.port``. Falls back to 8720 when run outside the harness.
"""
import os
import subprocess
import sys

port = os.environ.get("PORT") or os.environ.get("STREAMLIT_SERVER_PORT") or "8720"
cmd = [
    sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
    "--server.port", str(port), "--server.headless", "true",
]
sys.exit(subprocess.call(cmd))
