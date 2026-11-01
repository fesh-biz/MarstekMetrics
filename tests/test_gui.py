"""Смоук-тест вікна. Пропускається, якщо PySide6 не встановлено."""

import os
import time

import pytest

pytest.importorskip("PySide6")
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
        assert win.soc_label.text() != "—"
        assert "Розряджається" in win.state_label.text() or "Заряджається" in win.state_label.text()
        assert win.journal.count() >= 1
        app.processEvents()
        out = os.environ.get("SCREENSHOT_DIR")
        if out:
            os.makedirs(out, exist_ok=True)
            win.grab().save(os.path.join(out, "window.png"))
    finally:
        win.close()
        mock.stop()
