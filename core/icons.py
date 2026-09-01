# -*- coding: utf-8 -*-
"""Tek renkli çizgi ikon seti — TEK KAYNAK (Yayın Arayüzü, Faz 14).

Emoji YOK. 24×24 viewBox, 1.5 stroke, yuvarlak uç, `currentColor`.
`icon(name)` satır-içi `<svg>` döndürür — st.markdown(unsafe_allow_html=True).

Bu modül Streamlit'e / hiçbir şeye bağımlı değildir; saf string üretir.
"""

# name -> iç SVG gövdesi (path/circle/rect …). viewBox 0 0 24 24.
_P = {
    "flag": "<path d='M6 21V4M6 5h11l-1.6 4L17 13H6'/>",
    "trophy": "<path d='M8 4h8v5a4 4 0 0 1-8 0zM8 6H5v2a3 3 0 0 0 3 3M16 6h3v2a3 3 0 0 1-3 3M10 15h4M9 20h6M12 15v5'/>",
    "calendar": "<rect x='4' y='5' width='16' height='16' rx='2'/><path d='M4 10h16M9 3v4M15 3v4'/>",
    "pin": "<path d='M12 21s7-6.3 7-11a7 7 0 0 0-14 0c0 4.7 7 11 7 11z'/><circle cx='12' cy='10' r='2.5'/>",
    "chart": "<path d='M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-7'/>",
    "user": "<circle cx='12' cy='8' r='4'/><path d='M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6'/>",
    "wrench": "<path d='M15 6a4 4 0 0 0-5.2 5.2L4 17l3 3 5.8-5.8A4 4 0 0 0 18 9l-3 3-2-2 3-3z'/>",
    "message": "<path d='M4 5h16v11H9l-5 4z'/>",
    "target": "<circle cx='12' cy='12' r='8'/><circle cx='12' cy='12' r='3.5'/>",
    "flame": "<path d='M12 3c1 3-1.5 4-1.5 7A3.5 3.5 0 0 0 14 13c0-1 .5-2 1-2.5.6 1 2 2.5 2 5a5.5 5.5 0 0 1-11 0C6 11 10 8 12 3z'/>",
    "gem": "<path d='M6 4h12l3 5-9 12L3 9z'/><path d='M3 9h18M9 4 7 9l5 12M15 4l2 5-5 12'/>",
    "crown": "<path d='M4 8l3.5 3L12 5l4.5 6L20 8v9H4zM4 21h16'/>",
    "play": "<path d='M8 5v14l11-7z'/>",
    "pause": "<path d='M9 5v14M15 5v14'/>",
    "arrow-right": "<path d='M5 12h14M13 6l6 6-6 6'/>",
    "arrow-left": "<path d='M19 12H5M11 6l-6 6 6 6'/>",
    "chevron-right": "<path d='M9 6l6 6-6 6'/>",
    "chevron-left": "<path d='M15 6l-6 6 6 6'/>",
    "chevron-down": "<path d='M6 9l6 6 6-6'/>",
    "chevron-up": "<path d='M6 15l6-6 6 6'/>",
    "star": "<path d='M12 4l2.4 5 5.6.8-4 4 1 5.6L12 16l-5 2.4 1-5.6-4-4 5.6-.8z'/>",
    "pulse": "<path d='M3 12h4l2.5-7 4 14 2.5-7H21'/>",
    "alert": "<path d='M12 4 2.5 20h19zM12 10v5M12 18h.01'/>",
    "safety-car": "<path d='M4 15l2-6a3 3 0 0 1 3-2h6a3 3 0 0 1 3 2l2 6M4 15v3h3M20 15v3h-3M4 15h16M8 18h8'/><circle cx='8' cy='15' r='1'/><circle cx='16' cy='15' r='1'/>",
    "stopwatch": "<circle cx='12' cy='13' r='7'/><path d='M12 13V9M10 2h4M18 6l1.5-1.5'/>",
    "layers": "<path d='M12 4 3 9l9 5 9-5zM3 14l9 5 9-5'/>",
    "gauge": "<path d='M4 17a8 8 0 1 1 16 0M12 13l4-4'/>",
    "book": "<path d='M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 0-2 2zM7 20a2 2 0 0 1-2-2V4'/>",
    "medal": "<circle cx='12' cy='15' r='6'/><path d='M9 3 12 9 15 3M8.5 4.5 12 11M15.5 4.5 12 11'/>",
    "check": "<path d='M5 13l4 4L19 7'/>",
    "x": "<path d='M6 6l12 12M18 6L6 18'/>",
    "plus": "<path d='M12 5v14M5 12h14'/>",
    "minus": "<path d='M5 12h14'/>",
    "refresh": "<path d='M20 11a8 8 0 0 0-14-4M4 5v3h3M4 13a8 8 0 0 0 14 4m2 2v-3h-3'/>",
    "share": "<circle cx='6' cy='12' r='2.5'/><circle cx='18' cy='6' r='2.5'/><circle cx='18' cy='18' r='2.5'/><path d='M8.3 10.8 15.7 7.2M8.3 13.2l7.4 3.6'/>",
    "dot": "<circle cx='12' cy='12' r='4'/>",
    "record": "<path d='M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 0-2 2zM7 20a2 2 0 0 1-2-2V4'/>",
    "tyre": "<circle cx='12' cy='12' r='8'/><circle cx='12' cy='12' r='3.5'/><path d='M12 4v3M12 17v3M4 12h3M17 12h3'/>",
    "steering": "<circle cx='12' cy='12' r='8'/><circle cx='12' cy='12' r='2'/><path d='M4.5 10h5M14.5 10h5M12 14v5.5'/>",
    "route": "<circle cx='6' cy='19' r='2'/><circle cx='18' cy='5' r='2'/><path d='M8 19h6a4 4 0 0 0 0-8H10a4 4 0 0 1 0-8h6'/>",
    "history": "<path d='M4 12a8 8 0 1 0 3-6.2M4 4v4h4M12 8v4l3 2'/>",
    "clock": "<circle cx='12' cy='12' r='8'/><path d='M12 7v5l3 2'/>",
    "grid": "<rect x='4' y='4' width='7' height='7' rx='1'/><rect x='13' y='4' width='7' height='7' rx='1'/><rect x='4' y='13' width='7' height='7' rx='1'/><rect x='13' y='13' width='7' height='7' rx='1'/>",
    "cards": "<rect x='4' y='6' width='11' height='14' rx='2'/><path d='M8 3h9a2 2 0 0 1 2 2v11'/>",
    "help": "<circle cx='12' cy='12' r='8'/><path d='M9.5 9.5A2.5 2.5 0 0 1 14 11c0 2-2.5 2-2.5 4M12 18h.01'/>",
    "sliders": "<path d='M4 8h10M18 8h2M4 16h4M12 16h8'/><circle cx='16' cy='8' r='2'/><circle cx='10' cy='16' r='2'/>",
    "cog": "<circle cx='12' cy='12' r='3'/><path d='M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1'/>",
}

# Yaygın eşanlamlar
_ALIAS = {
    "championship": "trophy", "calendar-check": "calendar", "map-pin": "pin",
    "bar-chart": "chart", "spanner": "wrench", "chat": "message",
    "next": "chevron-right", "prev": "chevron-left",
    "podium": "trophy", "wheel": "steering",
}


def icon(name, size=16, *, cls="", stroke_width=1.5, style=""):
    """Satır-içi <svg> — `currentColor` çizgi. `size` px (kare)."""
    body = _P.get(name) or _P.get(_ALIAS.get(name, ""), "") or _P["dot"]
    c = f" class='{cls}'" if cls else ""
    s = f" style='{style}'" if style else ""
    return (
        f"<svg{c}{s} width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' "
        f"stroke='currentColor' stroke-width='{stroke_width}' stroke-linecap='round' "
        f"stroke-linejoin='round' aria-hidden='true'>{body}</svg>"
    )


def has(name):
    return name in _P or name in _ALIAS
