"""
Honeycomb-modular / Xylosome house style for the motosome bench.

Design language: scientific instrument / oscilloscope / film data strip.
Dark canvas, monospace type, thin clean lines, sharp corners, minimal colour
used functionally only (the R/G/B/C scan-channel palette). Tweak the hex values
here and the whole app follows.
"""

import os

from PySide6 import QtGui

# --- palette ---------------------------------------------------------------
BG        = "#0E0F13"   # canvas / near-black
PANEL     = "#15171D"   # group panels
PANEL_HI  = "#1B1E26"   # buttons / raised
BORDER    = "#262A33"   # hairlines
GRID      = "#1C2029"   # scope grid
ZERO      = "#3A404C"   # scope zero line
TEXT      = "#C9CDD6"   # primary text (matches the recoloured logo)
TEXT_DIM  = "#6A7180"   # labels / secondary
SIGNAL    = "#39C5BB"   # primary instrument accent (phosphor teal)

# functional scan-channel colours (Xylosome R / G / B / C)
CH_R = "#E5484D"
CH_G = "#46C26B"
CH_B = "#3B82F6"
CH_C = "#E6E6E6"

OK    = "#39C5BB"
WARN  = "#E5A23B"
FAULT = "#E5484D"
IDLE  = "#6A7180"

# preferred monospace stack (DejaVu Sans Mono ships with Ubuntu)
FONT_FAMILIES = ["JetBrains Mono", "DejaVu Sans Mono", "Menlo", "Consolas", "monospace"]

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(HERE, "logo_light.svg")


def font(size: int = 10, bold: bool = False) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSize(size)
    f.setBold(bold)
    return f


def stylesheet() -> str:
    return f"""
    QWidget {{ background: {BG}; color: {TEXT}; }}
    QMainWindow {{ background: {BG}; }}
    QGroupBox {{
        border: 1px solid {BORDER};
        margin-top: 14px;
        padding: 8px;
        background: {PANEL};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px; padding: 0 4px;
        color: {TEXT_DIM};
    }}
    QPushButton {{
        background: {PANEL_HI};
        border: 1px solid {BORDER};
        padding: 6px 10px;
        color: {TEXT};
    }}
    QPushButton:hover  {{ border-color: {SIGNAL}; }}
    QPushButton:pressed {{ background: {BORDER}; }}
    QPushButton:checked {{ border-color: {SIGNAL}; color: {SIGNAL}; }}
    QComboBox, QDoubleSpinBox {{
        background: {BG};
        border: 1px solid {BORDER};
        padding: 4px 6px;
        color: {TEXT};
        selection-background-color: {SIGNAL};
        selection-color: {BG};
    }}
    QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {SIGNAL}; }}
    QComboBox QAbstractItemView {{
        background: {PANEL}; color: {TEXT};
        border: 1px solid {BORDER}; selection-background-color: {SIGNAL};
    }}
    QLabel {{ background: transparent; }}
    QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
    """


def apply(app) -> None:
    app.setStyle("Fusion")
    app.setFont(font(10))
    app.setStyleSheet(stylesheet())
