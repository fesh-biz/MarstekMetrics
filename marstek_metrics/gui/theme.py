"""Кольори та стилі інтерфейсу."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Кольори серій (однакові на всіх графіках і плитках)
HOUSE = "#2563eb"    # споживання будинку
GRID = "#64748b"     # зовнішня мережа
STATION = "#16a34a"  # станція
BATTERY = "#d97706"  # батарея

LEVEL_COLORS = {"error": "#dc2626", "warning": "#d97706", "info": "#64748b"}

LIGHT = {
    "bg": "#f4f5f7", "card": "#ffffff", "border": "#e2e5ea",
    "text": "#111827", "muted": "#6b7280", "accent": "#2563eb",
}
DARK = {
    "bg": "#16181d", "card": "#1f2228", "border": "#2e323a",
    "text": "#e6e8eb", "muted": "#9aa1ac", "accent": "#60a5fa",
}


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def colors(app: QApplication | None = None) -> dict[str, str]:
    app = app or QApplication.instance()
    return DARK if app is not None and is_dark(app) else LIGHT


def apply(app: QApplication) -> dict[str, str]:
    app.setStyle("Fusion")
    c = colors(app)
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["card"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QWidget {{ font-size: 10pt; }}
        QFrame#card {{
            background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px;
        }}
        QLabel#cardTitle {{ color: {c['muted']}; font-size: 9pt; }}
        QLabel#bigValue {{ font-size: 40pt; font-weight: 600; }}
        QLabel#stateValue {{ font-size: 13pt; }}
        QLabel#deviceTitle {{ font-size: 13pt; font-weight: 600; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QListWidget#journal {{ border: none; background: transparent; }}
        QListWidget#journal::item {{ padding: 4px 0; border-bottom: 1px solid {c['border']}; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 24px; }}
        QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        QProgressBar {{
            border: none; border-radius: 5px; background: {c['border']}; max-height: 10px;
        }}
        QProgressBar::chunk {{ border-radius: 5px; background: {STATION}; }}
    """)
    return c
