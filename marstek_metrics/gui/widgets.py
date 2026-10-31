"""Невеликі віджети: плитка показника та індикатор зв'язку."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout


class Tile(QFrame):
    """Картка: заголовок, велике значення, пояснення."""

    def __init__(self, title: str, color: str | None = None, progress: bool = False):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        if color:
            self.title.setText(f'<span style="color:{color}">●</span>&nbsp;{title}')
            self.title.setTextFormat(Qt.TextFormat.RichText)
        self.value = QLabel("—")
        self.value.setObjectName("cardValue")
        self.sub = QLabel("")
        self.sub.setObjectName("cardSub")
        self.sub.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        self.bar: QProgressBar | None = None
        if progress:
            self.bar = QProgressBar()
            self.bar.setRange(0, 100)
            self.bar.setTextVisible(False)
            lay.addWidget(self.bar)
        lay.addWidget(self.sub)
        lay.addStretch(1)

    def set(self, value: str, sub: str = "", progress: float | None = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        if self.bar is not None:
            self.bar.setValue(int(progress or 0))


class StatusPill(QLabel):
    COLORS = {
        "На зв'язку": "#16a34a",
        "Опитування…": "#2563eb",
        "Підключення…": "#2563eb",
        "Немає відповіді": "#d97706",
        "Немає зв'язку": "#dc2626",
        "Помилка мережі": "#dc2626",
        "Зупинено": "#64748b",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("pill")
        self.set_status("Зупинено")

    def set_status(self, text: str) -> None:
        # «Опитування…» блимає кожні 30 с — не перефарбовуємо ним зелений статус
        if text == "Опитування…" and self.text() == "На зв'язку":
            return
        self.setText(text)
        color = self.COLORS.get(text, "#64748b")
        self.setStyleSheet(f"background: {color};")
