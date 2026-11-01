"""Індикатор зв'язку зі станцією."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


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
