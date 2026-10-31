"""Небольшие виджеты: плитка показателя и индикатор связи."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout


class Tile(QFrame):
    """Карточка: заголовок, крупное значение, пояснение."""

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
        "На связи": "#16a34a",
        "Опрос…": "#2563eb",
        "Подключение…": "#2563eb",
        "Нет ответа": "#d97706",
        "Нет связи": "#dc2626",
        "Ошибка сети": "#dc2626",
        "Остановлено": "#64748b",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("pill")
        self.set_status("Остановлено")

    def set_status(self, text: str) -> None:
        # «Опрос…» мелькает каждые 30 с — не перекрашиваем им зелёный статус
        if text == "Опрос…" and self.text() == "На связи":
            return
        self.setText(text)
        color = self.COLORS.get(text, "#64748b")
        self.setStyleSheet(f"background: {color};")
