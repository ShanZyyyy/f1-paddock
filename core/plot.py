# -*- coding: utf-8 -*-
"""Matplotlib — Formula Paddock stili (F1 TV yayin grafigi).

Sayfalar hardcoded renk yerine bu sabitleri ve style() yardimcisini kullanir.
"""

from core import theme

BG = theme.TOKENS["bg-0"]        # #07090d  figure zemini
PANEL = theme.TOKENS["bg-2"]     # #11161f  eksen zemini
GRID = theme.TOKENS["line"]      # #26313f  izgara + spine
TEXT = theme.TOKENS["text-dim"]  # #9fb0c0  etiket + tick
MUTE = theme.TOKENS["text-mute"] # #63748a

# 1. / 2. surucu (duello) — kirmizi vs cyan
A1 = theme.TOKENS["red"]         # #e10600
A2 = theme.TOKENS["cyan"]        # #38e1d0

# Lastik hamuru
COMPOUND = {
    "SOFT": "#ff5b5b", "MEDIUM": "#ffe14d", "HARD": "#e7edf3",
    "INTERMEDIATE": "#4ade80", "WET": "#5db4ff",
}


def style(fig, *axes):
    """Bir figure + eksenlerini paddock temasina cevir."""
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=TEXT, labelsize=8)
        ax.xaxis.label.set_color(TEXT)
        ax.yaxis.label.set_color(TEXT)
        ax.title.set_color(theme.TOKENS["text"])
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.grid(True, color=GRID, linestyle="--", alpha=0.5, linewidth=0.7)
    return fig


def figure(figsize=(10, 5)):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=figsize)
    style(fig, ax)
    return fig, ax
