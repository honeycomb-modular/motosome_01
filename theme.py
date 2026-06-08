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
# Exact tokens from xylosome-hmi  pi/hmi/qml/Theme.qml
BG         = "#050505"   # Theme.bg     — canvas
PANEL      = "#0E0E0E"   # Theme.panel
PANEL_HI   = "#141414"   # raised (buttons)
BORDER     = "#262626"   # Theme.border — hairlines
BORDER_DIM = "#1A1A1A"   # Theme.borderDim
GRID       = "#1A1A1A"   # scope / curve grid
ZERO       = "#2E2E2E"   # zero line
TEXT       = "#ECECEC"   # Theme.colorText      — primary
TEXT_DIM   = "#888888"   # Theme.colorTextDim   — secondary
TEXT_FAINT = "#4A4A4A"   # Theme.colorTextFaint — tertiary
SIGNAL     = "#4ADE80"   # Theme.accent     — active / live / OK · curves, nodes, traces
ACCENT_DIM = "#1F6E3A"   # Theme.accentDim
ACTION     = "#F87171"   # Theme.danger     — offline / error / play-stop

# functional scan-channel colours (R / G / B / C) for later channel UI
CH_R = "#F87171"
CH_G = "#4ADE80"
CH_B = "#3B82F6"
CH_C = "#ECECEC"

OK    = "#4ADE80"
WARN  = "#F87171"
FAULT = "#F87171"
IDLE  = "#4A4A4A"

# Matches Theme.fontFamily in the HMI (Courier New → DejaVu Sans Mono → monospace)
FONT_FAMILIES = ["Courier New", "Courier", "DejaVu Sans Mono", "Liberation Mono", "monospace"]

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
        border: 1px solid {SIGNAL};
        padding: 6px 10px;
        color: {TEXT};
    }}
    QPushButton:hover  {{ border-color: {SIGNAL}; color: {SIGNAL}; }}
    QPushButton:pressed {{ background: {BORDER}; }}
    QPushButton:checked {{ border-color: {SIGNAL}; color: {SIGNAL}; }}
    QPushButton[role="action"] {{ border-color: {ACTION}; color: {ACTION}; }}
    QPushButton[role="action"]:hover {{ border-color: {ACTION}; color: {ACTION}; }}
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
