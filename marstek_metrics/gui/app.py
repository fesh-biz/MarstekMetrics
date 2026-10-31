"""Точка входу десктопного застосунку."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import theme  # noqa: F401  (до імпорту pyqtgraph — обирає PySide6)
from ..config import AppConfig, data_dir


def setup_logging() -> None:
    handler = RotatingFileHandler(
        data_dir() / "marstek-metrics.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    setup_logging()
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow, window_title

    app = QApplication(sys.argv)
    app.setApplicationName("Marstek Metrics")
    theme.apply(app)
    win = MainWindow(AppConfig.load())
    win.setWindowTitle(window_title())
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
