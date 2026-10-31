import threading
import time

from marstek_metrics import fmt
from marstek_metrics.config import AppConfig
from marstek_metrics.mock import MockVenus
from marstek_metrics.poller import Poller


def test_fmt():
    assert fmt.power(None) == "—"
    assert fmt.power(560.4) == "560 Вт"
    assert fmt.power(1234) == "1,23 кВт"
    assert fmt.power(50, signed=True) == "+50 Вт"
    assert fmt.energy(80) == "80 Вт·год"
    assert fmt.energy(12_345) == "12,35 кВт·год"
    assert fmt.percent(62.0) == "62%"
    assert fmt.rssi_quality(-65) == "-65 dBm, добрий"
    assert fmt.ago(100, 130) == "30 с тому"


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MARSTEK_METRICS_HOME", str(tmp_path))
    cfg = AppConfig(host="192.168.1.50", interval=45)
    cfg.save()
    loaded = AppConfig.load()
    assert loaded.host == "192.168.1.50" and loaded.interval == 45


def test_poller_stops_fast_while_waiting(tmp_path):
    silent = MockVenus()
    silent.drop_rate = 1.0
    silent.start()
    try:
        cfg = AppConfig(host=silent.host, port=silent.port, timeout=10, retries=3,
                        bind_device_port=False)
        statuses = []
        p = Poller(cfg, tmp_path / "m.db", on_status=statuses.append)
        p.start()
        time.sleep(0.5)  # потік чекає на відповідь на Marstek.GetDevice
        t0 = time.monotonic()
        p.stop()
        p.join(3)
        assert not p.is_alive()
        assert time.monotonic() - t0 < 2
        assert "Помилка мережі" not in statuses
    finally:
        silent.stop()
