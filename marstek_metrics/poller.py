"""Фонове опитування станції."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .alerts import AlertEngine, CommError, Event
from .config import AppConfig
from .device import MarstekDevice
from .models import (
    DeviceInfo,
    Profile,
    Telemetry,
    apply_battery,
    apply_em_status,
    apply_es_mode,
    apply_es_status,
    apply_wifi,
    finalize,
)
from .protocol import MarstekError, MarstekRpcError, MarstekTimeout, UdpTransport, local_bind_port
from .storage import Storage

log = logging.getLogger(__name__)

SLOW_EVERY = 10          # режим, CT, Wi-Fi, батарея — раз на N циклів
DEVICE_EVERY_S = 3600    # Marstek.GetDevice — раз на годину
PRUNE_EVERY_S = 86400


def _comm_error(method: str, exc: Exception) -> CommError:
    if isinstance(exc, MarstekTimeout):
        return CommError(method, "timeout", str(exc))
    if isinstance(exc, MarstekRpcError):
        return CommError(method, "rpc", str(exc), exc.code)
    return CommError(method, "other", str(exc))


def collect_once(
    dev: MarstekDevice,
    profile: Profile,
    slow: bool = False,
    battery: bool = False,
    gap: float = 1.0,
    skip: set[str] | None = None,
    ok: list[str] | None = None,
    stop: threading.Event | None = None,
) -> tuple[Telemetry, list[CommError]]:
    """Один цикл опитування. Повертає зріз телеметрії та збої зв'язку."""
    t = Telemetry()
    errors: list[CommError] = []
    ok = [] if ok is None else ok
    skip = skip or set()
    # Кожен цикл — лише один запит (заряд і потужність батареї), решта рідше:
    # так станція отримує мінімум запитів навіть за частого опитування.
    steps: list[tuple[str, Callable[[dict], None]]] = [
        ("ES.GetStatus", lambda r: apply_es_status(t, r, profile)),
    ]
    if slow:
        steps.append(("ES.GetMode", lambda r: apply_es_mode(t, r)))
        steps.append(("EM.GetStatus", lambda r: apply_em_status(t, r, profile)))
        steps.append(("Wifi.GetStatus", lambda r: apply_wifi(t, r)))
        if battery:
            steps.append(("Bat.GetStatus", lambda r: apply_battery(t, r)))
    first = True
    for method, apply in steps:
        if method in skip:
            continue
        if stop is not None and stop.is_set():
            break
        if not first and gap > 0:
            time.sleep(gap)
        first = False
        try:
            apply(dev.call(method).result)
            ok.append(method)
        except MarstekError as exc:
            log.info("%s", exc)
            errors.append(_comm_error(method, exc))
    t.ts = time.time()
    finalize(t)
    return t, errors


def carry_slow_fields(t: Telemetry, prev: Telemetry | None) -> None:
    """Режим, CT і Wi-Fi читаються рідше — між читаннями тримаємо останні значення."""
    if prev is None:
        return
    for name in ("mode", "ct_connected", "rssi"):
        if getattr(t, name) is None:
            setattr(t, name, getattr(prev, name))


class Poller(threading.Thread):
    """Опитує станцію за розкладом, пише історію та журнал.

    Колбеки викликаються з фонового потоку — GUI має передавати їх у
    головний потік (сигнали Qt).
    """

    def __init__(
        self,
        cfg: AppConfig,
        db_path: Path | str,
        on_telemetry: Callable[[Telemetry], None] | None = None,
        on_events: Callable[[list[Event]], None] | None = None,
        on_device: Callable[[DeviceInfo], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ):
        super().__init__(name="marstek-poller", daemon=True)
        self.cfg = cfg
        self.db_path = db_path
        self.on_telemetry = on_telemetry or (lambda t: None)
        self.on_events = on_events or (lambda e: None)
        self.on_device = on_device or (lambda d: None)
        self.on_status = on_status or (lambda s: None)
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self.info: DeviceInfo | None = None
        self._transport: UdpTransport | None = None

    def stop(self) -> None:
        """Зупинити опитування. Закриття сокета перериває очікування відповіді."""
        self._stop_event.set()
        self._wake.set()
        if self._transport is not None:
            try:
                self._transport.close()
            except OSError:
                pass

    def poll_now(self) -> None:
        self._wake.set()

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(timeout=seconds)
        self._wake.clear()

    def run(self) -> None:
        cfg = self.cfg
        storage = Storage(self.db_path)
        alerts = AlertEngine(cfg)
        try:
            transport = UdpTransport(
                bind_port=local_bind_port(cfg.host, cfg.port, cfg.bind_device_port),
                timeout=cfg.timeout,
                retries=cfg.retries,
            )
        except OSError as exc:
            ev = Event("error", "socket", f"Не вдалося відкрити UDP-сокет: {exc}")
            storage.add_events([ev])
            self.on_events([ev])
            self.on_status("Помилка мережі")
            storage.close()
            return

        self._transport = transport
        try:
            self._loop(transport, storage, alerts)
        except OSError:
            if not self._stop_event.is_set():
                log.exception("Збій опитування")
                self.on_status("Помилка мережі")
        finally:
            transport.close()
            storage.close()

    def _loop(self, transport: UdpTransport, storage: Storage, alerts: AlertEngine) -> None:
        cfg = self.cfg
        dev = MarstekDevice(transport, cfg.host, cfg.port)
        cycle = 0
        last_device_check = 0.0
        last_prune = 0.0
        while not self._stop_event.is_set():
            started = time.monotonic()
            events: list[Event] = []

            if self.info is None or time.time() - last_device_check > DEVICE_EVERY_S:
                self.on_status("Підключення…" if self.info is None else "Опитування…")
                try:
                    info = dev.get_device()
                    last_device_check = time.time()
                    self.info = info
                    events += alerts.on_device(info)
                    self.on_device(info)
                except MarstekError as exc:
                    events += alerts.on_cycle([], [_comm_error("Marstek.GetDevice", exc)])
                    if self.info is None:
                        storage.add_events(events)
                        self.on_events(events)
                        self.on_status("Немає зв'язку")
                        self._sleep(cfg.interval)
                        continue

            self.on_status("Опитування…")
            ok: list[str] = []
            tel, errors = collect_once(
                dev, self.info.profile,
                slow=cycle % SLOW_EVERY == 0,
                battery=cfg.read_battery,
                gap=cfg.request_gap,
                skip=alerts.unsupported,
                ok=ok,
                stop=self._stop_event,
            )
            if self._stop_event.is_set():
                break
            events += alerts.on_cycle(ok, errors)
            if ok:
                carry_slow_fields(tel, alerts.last)
                events += alerts.on_telemetry(tel)
                storage.add_sample(tel)
                self.on_telemetry(tel)
            storage.add_events(events)
            if events:
                self.on_events(events)
            self.on_status("Немає зв'язку" if alerts.offline else
                           "На зв'язку" if ok else "Немає відповіді")

            if time.time() - last_prune > PRUNE_EVERY_S:
                storage.prune(cfg.retention_days)
                last_prune = time.time()

            cycle += 1
            self._sleep(max(1.0, cfg.interval - (time.monotonic() - started)))
