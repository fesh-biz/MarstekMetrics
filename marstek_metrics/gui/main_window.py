"""Головне вікно: заряд батареї, заряджання/розряджання і помилки."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, fmt
from ..alerts import Event
from ..config import AppConfig, data_dir
from ..models import DeviceInfo, Telemetry
from ..poller import Poller
from ..storage import Storage
from . import theme
from .settings_dialog import SettingsDialog
from .widgets import StatusPill

# Нижче цієї потужності вважаємо, що батарея простоює
IDLE_W = 5
JOURNAL_LIMIT = 50

CHARGE_COLOR = "#16a34a"
DISCHARGE_COLOR = "#d97706"
IDLE_COLOR = "#64748b"


class Bridge(QObject):
    """Передає події фонового опитування в головний потік."""

    telemetry = Signal(object)
    events = Signal(object)
    device = Signal(object)
    status = Signal(str)


def battery_text(t: Telemetry | None) -> tuple[str, str, str]:
    """(стан, потужність, колір) для блоку батареї."""
    if t is None or t.battery_power is None:
        return "Немає даних", "", IDLE_COLOR
    p = t.battery_power
    if p > IDLE_W:
        return "Заряджається", fmt.power(p), CHARGE_COLOR
    if p < -IDLE_W:
        return "Розряджається", fmt.power(-p), DISCHARGE_COLOR
    return "Очікування", "", IDLE_COLOR


def is_problem(e: Event) -> bool:
    """Чи показувати запис у журналі помилок."""
    return e.resolved or e.level in ("error", "warning")


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.db_path = data_dir() / "metrics.db"
        self.storage = Storage(self.db_path)
        self.poller: Poller | None = None
        self.info: DeviceInfo | None = None
        self.last: Telemetry | None = None
        self.active: dict[str, Event] = {}

        self.bridge = Bridge()
        self.bridge.telemetry.connect(self._on_telemetry)
        self.bridge.events.connect(self._on_events)
        self.bridge.device.connect(self._on_device)
        self.bridge.status.connect(lambda s: self.pill.set_status(s))

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(420, 560)
        self.setMinimumSize(360, 440)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        root.addLayout(self._build_header())
        root.addWidget(self._build_battery())
        root.addWidget(self._build_errors(), 1)

        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.clock.start(1000)

        self._show_battery()
        self._refresh_errors()
        if cfg.host:
            self._start_poller()
        else:
            QTimer.singleShot(200, self._open_settings)

    # --- побудова ---------------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(0)
        self.device_title = QLabel("Станцію не налаштовано")
        self.device_title.setObjectName("deviceTitle")
        self.device_sub = QLabel("Відкрийте налаштування і вкажіть IP станції")
        self.device_sub.setObjectName("muted")
        col.addWidget(self.device_title)
        col.addWidget(self.device_sub)
        row.addLayout(col, 1)
        self.pill = StatusPill()
        row.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        settings = QPushButton("⚙")
        settings.setToolTip("Налаштування")
        settings.setFixedWidth(36)
        settings.clicked.connect(lambda _c=False: self._open_settings())
        row.addWidget(settings, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _build_battery(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(6)

        self.soc_label = QLabel("—")
        self.soc_label.setObjectName("bigValue")
        self.soc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.soc_label)

        self.soc_bar = QProgressBar()
        self.soc_bar.setRange(0, 100)
        self.soc_bar.setTextVisible(False)
        lay.addWidget(self.soc_bar)

        self.state_label = QLabel()
        self.state_label.setObjectName("stateValue")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addSpacing(4)
        lay.addWidget(self.state_label)

        self.capacity_label = QLabel()
        self.capacity_label.setObjectName("muted")
        self.capacity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.capacity_label)
        return card

    def _build_errors(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        title = QLabel("Помилки")
        title.setObjectName("cardTitle")
        lay.addWidget(title)

        self.active_label = QLabel()
        self.active_label.setWordWrap(True)
        self.active_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.active_label)

        journal = QLabel("Журнал")
        journal.setObjectName("cardTitle")
        lay.addWidget(journal)

        self.journal = QListWidget()
        self.journal.setObjectName("journal")
        self.journal.setWordWrap(True)
        self.journal.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.journal.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.journal, 1)
        return card

    # --- опитування -----------------------------------------------------------

    def _start_poller(self) -> None:
        self.poller = Poller(
            self.cfg, self.db_path,
            on_telemetry=self.bridge.telemetry.emit,
            on_events=self.bridge.events.emit,
            on_device=self.bridge.device.emit,
            on_status=self.bridge.status.emit,
        )
        self.active.clear()
        self.poller.start()
        self.device_sub.setText(f"{self.cfg.host} · підключення…")

    def _stop_poller(self) -> None:
        if self.poller is not None:
            self.poller.stop()
            self.poller.join(timeout=3)
            self.poller = None
        self.pill.set_status("Зупинено")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if self.poller is not None:
            self._stop_poller()  # звільняємо UDP-порт для пошуку станцій
        if dlg.exec():
            self.cfg = dlg.cfg
            self.cfg.save()
        if self.cfg.host:
            self._start_poller()

    # --- події ------------------------------------------------------------------

    def _on_device(self, info: DeviceInfo) -> None:
        self.info = info
        self.device_title.setText(f"Marstek {info.title}")

    def _on_telemetry(self, t: Telemetry) -> None:
        self.last = t
        self._show_battery()

    def _on_events(self, events: list[Event]) -> None:
        for e in events:
            if e.resolved:
                self.active.pop(e.code, None)
            elif e.level in ("error", "warning") and not e.code.startswith("rpc:") \
                    and e.code != "counter_reset":
                self.active[e.code] = e
        self._refresh_errors()
        if "offline" in self.active:
            self._show_battery()

    def _tick(self) -> None:
        if self.info is not None:
            when = fmt.ago(self.last.ts if self.last else None, time.time())
            self.device_sub.setText(f"{self.info.host} · оновлено {when}")

    # --- відображення ---------------------------------------------------------

    def _show_battery(self) -> None:
        t = self.last
        offline = "offline" in self.active
        soc = None if t is None else t.soc
        self.soc_label.setText(fmt.percent(soc))
        self.soc_bar.setValue(int(soc or 0))

        state, power, color = battery_text(None if offline else t)
        if offline:
            state = "Немає зв'язку"
        text = f'<span style="color:{color}">●</span>&nbsp;{state}'
        if power:
            text += f"&nbsp;&nbsp;<b>{power}</b>"
        self.state_label.setText(text)

        if t is not None and t.soc is not None and t.bat_capacity_wh:
            left = t.bat_capacity_wh * t.soc / 100
            self.capacity_label.setText(
                f"у батареї {fmt.energy(left)} з {fmt.energy(t.bat_capacity_wh)}")
        else:
            self.capacity_label.setText("")

    def _refresh_errors(self) -> None:
        if self.active:
            items = "".join(
                f'<p style="margin:2px 0"><span style="color:{theme.LEVEL_COLORS[e.level]}">●</span>'
                f"&nbsp;{e.message}</p>"
                for e in sorted(self.active.values(), key=lambda e: e.level != "error")
            )
            self.active_label.setText(items)
        else:
            self.active_label.setText(
                f'<span style="color:{CHARGE_COLOR}">●</span>&nbsp;Зараз помилок немає')

        events = [e for e in self.storage.events(limit=JOURNAL_LIMIT * 3) if is_problem(e)]
        self.journal.clear()
        for e in events[:JOURNAL_LIMIT]:
            color = theme.LEVEL_COLORS["info" if e.resolved else e.level]
            prefix = "✓ " if e.resolved else ""
            item = QListWidgetItem(f"{fmt.ts_full(e.ts)}\n{prefix}{e.message}")
            item.setForeground(QColor(color) if not e.resolved else QColor(IDLE_COLOR))
            self.journal.addItem(item)
        if not events:
            self.journal.addItem(QListWidgetItem("Записів поки немає"))

    # --- закриття ------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_poller()
        self.storage.close()
        super().closeEvent(event)
