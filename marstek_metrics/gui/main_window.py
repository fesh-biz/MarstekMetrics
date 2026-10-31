"""Головне вікно."""

from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, fmt
from ..alerts import LEVEL_UK, Event
from ..config import AppConfig, data_dir
from ..metrics import daily_summaries, local_day_bounds, summarize
from ..models import MODE_TITLES_UK, DeviceInfo, Telemetry
from ..poller import Poller
from ..storage import Storage
from . import theme
from .charts import DailyBars, PowerChart
from .settings_dialog import SettingsDialog
from .widgets import StatusPill, Tile

RANGES = (("1 год", 3600), ("6 год", 6 * 3600), ("24 год", 86400), ("7 дн", 7 * 86400))


class Bridge(QObject):
    """Передає події фонового опитування в головний потік."""

    telemetry = Signal(object)
    events = Signal(object)
    device = Signal(object)
    status = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.db_path = data_dir() / "metrics.db"
        self.storage = Storage(self.db_path)
        self.poller: Poller | None = None
        self.info: DeviceInfo | None = None
        self.last: Telemetry | None = self.storage.latest()
        self.active: dict[str, Event] = {}
        self.range_s = RANGES[1][1]

        self.bridge = Bridge()
        self.bridge.telemetry.connect(self._on_telemetry)
        self.bridge.events.connect(self._on_events)
        self.bridge.device.connect(self._on_device)
        self.bridge.status.connect(lambda s: self.pill.set_status(s))

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 780)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)
        root.addLayout(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_now_tab(), "Зараз")
        self.tabs.addTab(self._build_usage_tab(), "Споживання")
        self.tabs.addTab(self._build_events_tab(), "Помилки")
        self.tabs.currentChanged.connect(self._on_tab)
        root.addWidget(self.tabs, 1)

        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.clock.start(1000)
        self.usage_timer = QTimer(self)
        self.usage_timer.timeout.connect(lambda: self._refresh_usage())
        self.usage_timer.start(5 * 60 * 1000)

        self._refresh_all()
        if cfg.host:
            self._start_poller()
        else:
            QTimer.singleShot(200, self._open_settings)

    # --- побудова -----------------------------------------------------------

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
        self.refresh_btn = QPushButton("Оновити")
        self.refresh_btn.clicked.connect(lambda _c=False: self._poll_now())
        row.addWidget(self.refresh_btn)
        settings = QPushButton("Налаштування")
        settings.clicked.connect(lambda _c=False: self._open_settings())
        row.addWidget(settings)
        return row

    def _build_now_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.t_house = Tile("Споживання будинку", theme.HOUSE)
        self.t_grid = Tile("Зовнішня мережа", theme.GRID)
        self.t_station = Tile("Станція", theme.STATION)
        self.t_soc = Tile("Батарея", theme.BATTERY, progress=True)
        self.t_today = Tile("Споживання будинку сьогодні")
        self.t_mode = Tile("Режим роботи")
        self.t_ct = Tile("CT-лічильник")
        self.t_wifi = Tile("Wi-Fi")
        tiles = [self.t_house, self.t_grid, self.t_station, self.t_soc,
                 self.t_today, self.t_mode, self.t_ct, self.t_wifi]
        for i, t in enumerate(tiles):
            grid.addWidget(t, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)

        bar = QHBoxLayout()
        title = QLabel("Потужності")
        title.setObjectName("cardTitle")
        bar.addWidget(title)
        bar.addStretch(1)
        group = QButtonGroup(self)
        for i, (label, seconds) in enumerate(RANGES):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(seconds == self.range_s)
            group.addButton(b, i)
            bar.addWidget(b)
        group.idClicked.connect(self._set_range)
        lay.addLayout(bar)
        self.chart = PowerChart()
        lay.addWidget(self.chart, 1)
        return w

    def _build_usage_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.u_today = Tile("Сьогодні", theme.HOUSE)
        self.u_yesterday = Tile("Учора", theme.HOUSE)
        self.u_week = Tile("7 днів", theme.HOUSE)
        self.u_month = Tile("30 днів", theme.HOUSE)
        for i, t in enumerate((self.u_today, self.u_yesterday, self.u_week, self.u_month)):
            grid.addWidget(t, 0, i)
            grid.setColumnStretch(i, 1)
        lay.addLayout(grid)

        bar = QHBoxLayout()
        title = QLabel("Споживання за днями")
        title.setObjectName("cardTitle")
        bar.addWidget(title)
        bar.addStretch(1)
        self.days_combo = QComboBox()
        for n in (7, 14, 30):
            self.days_combo.addItem(f"{n} днів", n)
        self.days_combo.setCurrentIndex(1)
        self.days_combo.currentIndexChanged.connect(lambda _i: self._refresh_usage())
        bar.addWidget(self.days_combo)
        lay.addLayout(bar)
        self.bars = DailyBars()
        self.bars.setMinimumHeight(220)
        lay.addWidget(self.bars, 1)

        self.days_table = QTableWidget(0, 8)
        self.days_table.setHorizontalHeaderLabels([
            "Дата", "Будинок спожив", "З мережі", "У мережу", "Станція віддала",
            "Станція зарядилася", "Пік будинку", "Даних за день",
        ])
        self._setup_table(self.days_table)
        lay.addWidget(self.days_table, 1)
        return w

    def _build_events_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        self.active_label = QLabel()
        self.active_label.setWordWrap(True)
        self.active_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.active_label)
        bar = QHBoxLayout()
        title = QLabel("Журнал")
        title.setObjectName("cardTitle")
        bar.addWidget(title)
        bar.addStretch(1)
        self.events_filter = QComboBox()
        self.events_filter.addItem("Лише проблеми", ("error", "warning"))
        self.events_filter.addItem("Усі записи", None)
        self.events_filter.currentIndexChanged.connect(lambda _i: self._refresh_events())
        bar.addWidget(self.events_filter)
        lay.addLayout(bar)
        self.events_table = QTableWidget(0, 3)
        self.events_table.setHorizontalHeaderLabels(["Час", "Рівень", "Повідомлення"])
        self._setup_table(self.events_table)
        self.events_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.events_table, 1)
        hint = QLabel(f"База та лог: {data_dir()}")
        hint.setObjectName("muted")
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(hint)
        return w

    @staticmethod
    def _setup_table(t: QTableWidget) -> None:
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)

    # --- опитування ------------------------------------------------------------------

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
        self.device_sub.setText(f"{self.cfg.host}:{self.cfg.port} · підключення…")

    def _poll_now(self) -> None:
        if self.poller is not None:
            self.poller.poll_now()

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

    # --- обробка даних ----------------------------------------------------

    def _on_device(self, info: DeviceInfo) -> None:
        self.info = info
        self.device_title.setText(f"Marstek {info.title}")

    def _on_telemetry(self, t: Telemetry) -> None:
        self.last = t
        self._refresh_now()
        if self.tabs.currentIndex() == 1:
            self._refresh_usage()

    def _on_events(self, events: list[Event]) -> None:
        for e in events:
            if e.resolved:
                self.active.pop(e.code, None)
            elif e.level in ("error", "warning") and not e.code.startswith("rpc:") \
                    and e.code != "counter_reset":
                self.active[e.code] = e
        self._refresh_events()

    def _on_tab(self, index: int) -> None:
        if index == 1:
            self._refresh_usage()
        elif index == 2:
            self._refresh_events()

    def _set_range(self, idx: int) -> None:
        self.range_s = RANGES[idx][1]
        self._refresh_chart()

    def _tick(self) -> None:
        if self.info is not None:
            when = fmt.ago(self.last.ts if self.last else None, time.time())
            self.device_sub.setText(
                f"{self.info.host}:{self.cfg.port} · прошивка {self.info.ver} · оновлено {when}"
            )

    # --- оновлення екранів -----------------------------------------------------

    def _refresh_all(self) -> None:
        self._refresh_now()
        self._refresh_events()

    def _refresh_now(self) -> None:
        t = self.last
        if t is None:
            for tile in (self.t_house, self.t_grid, self.t_station, self.t_soc,
                         self.t_today, self.t_mode, self.t_ct, self.t_wifi):
                tile.set("—", "немає даних")
            self._refresh_chart()
            return

        if t.house_power is not None:
            self.t_house.set(fmt.power(t.house_power), "станція + мережа + off-grid")
        elif t.ct_connected is False:
            self.t_house.set("—", "потрібен підключений CT-лічильник")
        else:
            self.t_house.set("—", "немає даних CT")

        g = t.grid_power
        if g is None:
            self.t_grid.set("—", "немає даних CT")
        elif g >= 0:
            self.t_grid.set(fmt.power(g), "беремо з мережі")
        else:
            self.t_grid.set(fmt.power(-g), "віддаємо в мережу")

        s = t.station_power
        if s is None:
            self.t_station.set("—")
        elif s > 5:
            self.t_station.set(fmt.power(s), "віддає в будинок")
        elif s < -5:
            self.t_station.set(fmt.power(-s), "заряджається від мережі")
        else:
            self.t_station.set("0 Вт", "простоює")

        cap = f" з {fmt.energy(t.bat_capacity_wh)}" if t.bat_capacity_wh else ""
        bat = "" if t.battery_power is None else f", {fmt.power(abs(t.battery_power))}"
        sub = f"{t.battery_state}{bat}{cap}"
        if t.bat_temp is not None:
            sub += f" · {t.bat_temp:.0f} °C"
        self.t_soc.set(fmt.percent(t.soc), sub, t.soc)

        start, _ = local_day_bounds(dt.date.today())
        today = summarize(self.storage.samples(start), "сьогодні", start, time.time())
        share = today.self_supply_share
        self.t_today.set(
            fmt.energy(today.house_wh) if today.samples else "—",
            f"з мережі {fmt.energy(today.grid_import_wh)}"
            + (f" · станція покрила {fmt.percent(share * 100)}" if share is not None else ""),
        )

        self.t_mode.set(MODE_TITLES_UK.get(t.mode or "", t.mode or "—"),
                        "станція сама керує потужністю" if t.mode in ("Auto", "AI") else "")
        if t.ct_connected is None:
            self.t_ct.set("—")
        elif t.ct_connected:
            phases = [p for p in (t.phase_a, t.phase_b, t.phase_c) if p]
            self.t_ct.set("підключено", " / ".join(fmt.power(p) for p in phases) if len(phases) > 1 else "")
        else:
            self.t_ct.set("немає зв'язку", "перевірте CT002/CT003")
        self.t_wifi.set(fmt.rssi_quality(t.rssi).split(",")[0],
                        fmt.rssi_quality(t.rssi).split(", ")[-1] if t.rssi is not None else "")
        self._refresh_chart()

    def _refresh_chart(self) -> None:
        end = time.time()
        start = end - self.range_s
        self.chart.set_samples(self.storage.samples(start), start, end)

    def _refresh_usage(self) -> None:
        n = self.days_combo.currentData() or 14
        days = daily_summaries(self.storage.samples, max(n, 30))
        shown = days[-n:]
        self.bars.set_days(shown)

        def total(items):
            return sum(d.house_wh for d in items), sum(d.grid_import_wh for d in items)

        for tile, items in ((self.u_today, days[-1:]), (self.u_yesterday, days[-2:-1]),
                            (self.u_week, days[-7:]), (self.u_month, days[-30:])):
            house, grid = total(items)
            has = any(d.samples for d in items)
            tile.set(fmt.energy(house) if has else "—",
                     f"з мережі {fmt.energy(grid)}" if has else "немає даних")

        self.days_table.setRowCount(len(shown))
        for row, d in enumerate(reversed(shown)):
            values = [
                d.label, fmt.energy(d.house_wh), fmt.energy(d.grid_import_wh),
                fmt.energy(d.grid_export_wh), fmt.energy(d.station_out_wh),
                fmt.energy(d.station_in_wh), fmt.power(d.peak_house_w),
                fmt.percent(d.coverage * 100) if d.samples else "—",
            ]
            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.days_table.setItem(row, col, item)

    def _refresh_events(self) -> None:
        levels = self.events_filter.currentData()
        events = self.storage.events(limit=500, levels=levels)
        self.events_table.setRowCount(len(events))
        for row, e in enumerate(events):
            color = QColor(theme.LEVEL_COLORS["info" if e.resolved else e.level])
            level = "Вирішено" if e.resolved else LEVEL_UK.get(e.level, e.level)
            for col, text in enumerate((fmt.ts_full(e.ts), level, e.message)):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(color)
                self.events_table.setItem(row, col, item)

        if self.active:
            items = "".join(
                f'<li><span style="color:{theme.LEVEL_COLORS[e.level]}">●</span> {e.message}</li>'
                for e in sorted(self.active.values(), key=lambda e: e.level != "error")
            )
            self.active_label.setText(f"<b>Активні проблеми</b><ul>{items}</ul>")
        else:
            self.active_label.setText(
                f'<span style="color:{theme.STATION}">●</span> Активних проблем немає')
        n = len(self.active)
        self.tabs.setTabText(2, f"Помилки ({n})" if n else "Помилки")

    # --- закриття ------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_poller()
        self.storage.close()
        super().closeEvent(event)


def window_title() -> str:
    return f"{APP_NAME} {__version__}"
