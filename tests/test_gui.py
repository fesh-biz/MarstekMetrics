"""Смоук-тест вікна. Пропускається, якщо PySide6 не встановлено."""

import os
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_main_window_with_emulator(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSTEK_METRICS_HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    from marstek_metrics.config import AppConfig
    from marstek_metrics.gui import theme
    from marstek_metrics.gui.main_window import MainWindow
    from marstek_metrics.mock import MockVenus

    mock = MockVenus(seed=1).start()
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    cfg = AppConfig(host=mock.host, port=mock.port, interval=1, request_gap=0,
                    timeout=1, retries=1, bind_device_port=False)
    win = MainWindow(cfg)
    win.show()
    try:
        deadline = time.time() + 15
        while time.time() < deadline and win.last is None:
            app.processEvents()
            time.sleep(0.05)
        assert win.last is not None
        assert win.t_house.value.text() != "—"
        for i in range(3):
            win.tabs.setCurrentIndex(i)
            app.processEvents()
        win.grab().save(str(tmp_path / "window.png"))
    finally:
        win.close()
        mock.stop()
