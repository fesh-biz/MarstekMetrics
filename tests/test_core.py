import datetime as dt
import socket
import threading
import time

import pytest

from marstek_metrics.alerts import AlertEngine, CommError
from marstek_metrics.config import AppConfig
from marstek_metrics.device import MarstekDevice, discover
from marstek_metrics.metrics import daily_summaries, integrate_wh, summarize
from marstek_metrics.mock import MockVenus
from marstek_metrics.models import (
    DeviceInfo,
    Profile,
    Telemetry,
    apply_em_status,
    apply_es_mode,
    apply_es_status,
    device_family,
    finalize,
    normalize_mode,
)
from marstek_metrics.poller import Poller, collect_once
from marstek_metrics.protocol import MarstekRpcError, MarstekTimeout, UdpTransport, local_bind_port
from marstek_metrics.storage import Storage


@pytest.fixture
def mock():
    m = MockVenus(seed=1).start()
    yield m
    m.stop()


@pytest.fixture
def transport():
    tr = UdpTransport(bind_port=None, timeout=0.5, retries=2)
    yield tr
    tr.close()


# --- протокол -------------------------------------------------------------


def test_get_device_and_status(mock, transport):
    dev = MarstekDevice(transport, mock.host, mock.port)
    info = dev.get_device()
    assert info.model == "VenusE 3.0" and info.ver == 150
    assert info.profile.family == "venus_e"
    tel, errors = collect_once(dev, info.profile, slow=True, gap=0)
    assert errors == []
    assert tel.soc is not None and tel.mode == "Auto"
    assert tel.battery_power_derived  # bat_power у відповіді немає
    assert tel.ct_connected is True and tel.house_power is not None
    assert tel.rssi is not None


def test_method_not_found(mock, transport):
    dev = MarstekDevice(transport, mock.host, mock.port)
    with pytest.raises(MarstekRpcError) as exc:
        dev.call("PV.GetStatus")
    assert exc.value.method_not_found


def test_timeout_and_retry(transport):
    silent = MockVenus(seed=2)
    silent.drop_rate = 1.0
    silent.start()
    try:
        dev = MarstekDevice(transport, silent.host, silent.port)
        t0 = time.monotonic()
        with pytest.raises(MarstekTimeout):
            dev.call("ES.GetStatus", timeout=0.2, retries=1)
        assert len(silent.requests) == 2
        assert time.monotonic() - t0 < 5
    finally:
        silent.stop()


def test_retry_recovers_after_drop(transport):
    flaky = MockVenus(seed=3)
    flaky.drop_rate = 0.5
    flaky.start()
    try:
        dev = MarstekDevice(transport, flaky.host, flaky.port)
        for _ in range(5):
            dev.call("ES.GetMode", timeout=0.2, retries=8)
    finally:
        flaky.stop()


def test_bind_fallback_when_port_busy(mock):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("0.0.0.0", 0))
    busy = blocker.getsockname()[1]
    tr = UdpTransport(bind_port=busy, timeout=0.5)
    try:
        assert tr.local_port != busy
        MarstekDevice(tr, mock.host, mock.port).get_device()
    finally:
        tr.close()
        blocker.close()


def test_local_bind_port():
    assert local_bind_port("192.168.1.50", 30000) == 30000
    assert local_bind_port("127.0.0.1", 30000) is None
    assert local_bind_port("192.168.1.50", 30000, bind_device_port=False) is None


def test_discover_broadcast_addresses(mock, transport):
    replies = transport.broadcast(
        "Marstek.GetDevice", {"ble_mac": "0"}, port=mock.port, listen=0.3,
        repeats=1, addresses=[mock.host],
    )
    assert replies and replies[0][1]["result"]["device"] == "VenusE 3.0"


# --- нормалізація ---------------------------------------------------------


@pytest.mark.parametrize("model,family", [
    ("VenusE 3.0", "venus_e"), ("VNSE3-0", "venus_e"), ("VenusE", "venus_e2"),
    ("HMG-50", "venus_e2"), ("Venus E mini", "venus_e_mini"), ("VNSEM-0", "venus_e_mini"),
    ("VenusC", "venus_c"), ("Venus D", "venus_d"), ("VNSA-0", "venus_a"), (None, "unknown"),
])
def test_family(model, family):
    assert device_family(model) == family


def test_es_status_derives_battery_and_house():
    p = Profile("venus_e", 150)
    t = Telemetry()
    apply_es_status(t, {"bat_soc": 50, "ongrid_power": 600, "offgrid_power": 0, "pv_power": 0}, p)
    assert t.battery_power == -600 and t.battery_power_derived  # розряджання
    apply_em_status(t, {"ct_state": 1, "total_power": 150, "input_energy": 1000, "output_energy": 20}, p)
    assert t.meter_import_wh == 100 and t.meter_export_wh == 2
    finalize(t)
    assert t.house_power == 750


def test_get_mode_ct_zeros_ignored_when_em_present():
    p = Profile("venus_e", 150)
    t = Telemetry()
    apply_em_status(t, {"ct_state": 1, "total_power": 300}, p)
    apply_es_mode(t, {"mode": 0, "ct_state": 0, "total_power": 0})
    assert t.mode == "Auto" and t.grid_power == 300


def test_old_firmware_no_em_energy_scale():
    t = Telemetry()
    apply_em_status(t, {"ct_state": 1, "total_power": 5, "input_energy": 10}, Profile("venus_e", 145))
    assert t.meter_import_wh is None


def test_normalize_mode():
    assert normalize_mode("passive") == "Passive"
    assert normalize_mode(4) == "UPS"
    assert normalize_mode(None) is None


# --- журнал ---------------------------------------------------------------


def test_alerts_raise_and_clear_once():
    eng = AlertEngine(AppConfig(offline_after=2))
    err = [CommError("ES.GetStatus", "timeout", "немає відповіді")]
    assert [e.code for e in eng.on_cycle([], err)] == []
    ev = eng.on_cycle([], err)
    assert {e.code for e in ev} == {"comm:ES.GetStatus", "offline"}
    assert eng.on_cycle([], err) == []  # без повторів
    ev = eng.on_cycle(["ES.GetStatus"], [])
    assert {e.code for e in ev} == {"comm:ES.GetStatus", "offline"}
    assert all(e.resolved for e in ev)


def test_alerts_telemetry_conditions():
    eng = AlertEngine(AppConfig())
    ev = eng.on_telemetry(Telemetry(ct_connected=False, soc=5, rssi=-85, mode="Auto"))
    assert {e.code for e in ev} == {"ct", "soc_low", "wifi"}
    ev = eng.on_telemetry(Telemetry(ct_connected=True, soc=50, rssi=-60, mode="Manual"))
    assert {e.code for e in ev} == {"ct", "soc_low", "wifi", "mode"}


def test_alerts_counter_reset_and_unsupported():
    eng = AlertEngine(AppConfig())
    eng.on_telemetry(Telemetry(grid_input_wh=1000))
    assert [e.code for e in eng.on_telemetry(Telemetry(grid_input_wh=10))] == ["counter_reset"]
    err = [CommError("PV.GetStatus", "rpc", "x", -32601)]
    assert [e.code for e in eng.on_cycle(["ES.GetStatus"], err)] == ["unsupported:PV.GetStatus"]
    assert eng.on_cycle(["ES.GetStatus"], err) == []
    assert "PV.GetStatus" in eng.unsupported


def test_alerts_device_and_firmware():
    eng = AlertEngine(AppConfig())
    assert [e.code for e in eng.on_device(DeviceInfo("h", "VenusE", 153))] == ["device", "device:unsupported"]
    assert [e.code for e in eng.on_device(DeviceInfo("h", "VenusE", 154))] == ["firmware"]


# --- споживання та зберігання ------------------------------------------------------


def test_integrate_and_gaps():
    s = [Telemetry(ts=0, house_power=1000), Telemetry(ts=1800, house_power=1000),
         Telemetry(ts=3600, house_power=1000)]
    assert integrate_wh(s, lambda t: t.house_power, max_gap=4000) == pytest.approx(1000)
    assert integrate_wh(s, lambda t: t.house_power, max_gap=100) == 0


def test_summary_uses_counters():
    s = [Telemetry(ts=i * 60, house_power=500, grid_power=-100, station_power=600,
                   grid_output_wh=1000 + i * 10) for i in range(61)]
    sm = summarize(s, "x", 0, 3600)
    assert sm.house_wh == pytest.approx(500)
    assert sm.grid_export_wh == pytest.approx(100)
    assert sm.station_out_wh == pytest.approx(600)
    assert sm.self_supply_share == 1.0
    assert sm.coverage == pytest.approx(1.0)


def test_storage_roundtrip(tmp_path):
    st = Storage(tmp_path / "m.db")
    now = time.time()
    st.add_sample(Telemetry(ts=now, soc=40, ct_connected=True, mode="AI"))
    from marstek_metrics.alerts import Event
    st.add_events([Event("warning", "ct", "x", now)])
    t = st.latest()
    assert t.soc == 40 and t.ct_connected is True and t.mode == "AI"
    assert st.events()[0].code == "ct"
    assert st.count_events_since(now - 1, ("warning",)) == 1
    days = daily_summaries(st.samples, 3, dt.date.today())
    assert len(days) == 3 and days[-1].samples == 1
    st.close()


def test_poller_end_to_end(tmp_path, mock):
    cfg = AppConfig(host=mock.host, port=mock.port, interval=1, request_gap=0,
                    timeout=0.5, retries=1, bind_device_port=False)
    got, events = [], []
    done = threading.Event()

    def on_tel(t):
        got.append(t)
        if len(got) >= 2:
            done.set()

    p = Poller(cfg, tmp_path / "m.db", on_telemetry=on_tel, on_events=events.extend)
    p.start()
    assert done.wait(10)
    p.stop()
    p.join(5)
    assert got[0].house_power is not None
    assert any(e.code == "device" for e in events)
    st = Storage(tmp_path / "m.db")
    assert len(st.samples(0)) >= 2
    st.close()
