"""Вікно налаштувань із пошуком станцій у мережі."""

from __future__ import annotations

import ipaddress
import threading
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..config import AppConfig
from ..device import discover
from ..models import DeviceInfo
from ..protocol import UdpTransport


class _DiscoverSignals(QObject):
    done = Signal(object, str)  # list[DeviceInfo], помилка


def _spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(value))
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Налаштування")
        self.setMinimumWidth(520)
        self.cfg = cfg
        self._signals = _DiscoverSignals()
        self._signals.done.connect(self._on_discovered)

        root = QVBoxLayout(self)

        # --- станція
        box = QGroupBox("Станція")
        form = QFormLayout(box)
        host_row = QHBoxLayout()
        self.host = QLineEdit(cfg.host)
        self.host.setPlaceholderText("наприклад, 192.168.1.50")
        self.find_btn = QPushButton("Знайти в мережі")
        self.find_btn.clicked.connect(lambda _c=False: self._discover())
        host_row.addWidget(self.host, 1)
        host_row.addWidget(self.find_btn)
        form.addRow("IP-адреса", host_row)
        self.found = QListWidget()
        self.found.setMaximumHeight(90)
        self.found.hide()
        self.found.itemClicked.connect(lambda it: self.host.setText(it.data(Qt.ItemDataRole.UserRole)))
        form.addRow("", self.found)
        self.port = _spin(1, 65535, cfg.port)
        form.addRow("UDP-порт Open API", self.port)
        hint = QLabel("Open API вмикається в застосунку Marstek. Закріпіть за станцією "
                      "постійну IP-адресу в налаштуваннях роутера.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        root.addWidget(box)

        # --- опитування
        box = QGroupBox("Опитування")
        form = QFormLayout(box)
        self.interval = _spin(10, 3600, cfg.interval, " с")
        self.interval.setToolTip("Кожен цикл — один запит до станції. Рекомендовано 15–30 с; частіше ніж раз на 10 с не варто")
        form.addRow("Інтервал", self.interval)
        self.timeout = _spin(2, 60, int(cfg.timeout), " с")
        form.addRow("Очікування відповіді", self.timeout)
        self.retries = _spin(0, 5, cfg.retries)
        form.addRow("Повторів при втраті", self.retries)
        self.bind = QCheckBox("Приймати відповіді на порту станції (рекомендовано)")
        self.bind.setChecked(cfg.bind_device_port)
        form.addRow("", self.bind)
        self.battery = QCheckBox("Читати дані BMS (температура, заборони заряджання/розряджання)")
        self.battery.setChecked(cfg.read_battery)
        self.battery.toggled.connect(self._warn_battery)
        form.addRow("", self.battery)
        root.addWidget(box)

        # --- пороги
        box = QGroupBox("Попередження")
        form = QFormLayout(box)
        self.soc_low = _spin(0, 100, cfg.soc_low, " %")
        form.addRow("Низький заряд, нижче", self.soc_low)
        self.rssi_low = _spin(-100, -30, cfg.rssi_low, " dBm")
        form.addRow("Слабкий Wi-Fi, нижче", self.rssi_low)
        self.temp_high = _spin(30, 80, cfg.temp_high, " °C")
        form.addRow("Перегрів батареї, від", self.temp_high)
        self.offline_after = _spin(1, 20, cfg.offline_after)
        form.addRow("«Немає зв'язку» після циклів", self.offline_after)
        self.retention = _spin(7, 3650, cfg.retention_days, " дн")
        form.addRow("Зберігати історію", self.retention)
        root.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Зберегти")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Скасувати")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _warn_battery(self, on: bool) -> None:
        if on:
            QMessageBox.warning(
                self, "Дані BMS",
                "На деяких прошивках запит Bat.GetStatus перезавантажує станцію.\n"
                "Увімкніть і поспостерігайте: якщо станція почне перезавантажуватися — вимкніть.",
            )

    # --- пошук ------------------------------------------------------------

    def _discover(self) -> None:
        self.find_btn.setEnabled(False)
        self.find_btn.setText("Шукаю…")
        port = self.port.value()
        bind = port if self.bind.isChecked() else None

        def work() -> None:
            try:
                with UdpTransport(bind_port=bind, timeout=3) as tr:
                    found = discover(tr, port, listen=3.0)
                self._signals.done.emit(found, "")
            except OSError as exc:
                self._signals.done.emit([], str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_discovered(self, found: list[DeviceInfo], error: str) -> None:
        self.find_btn.setEnabled(True)
        self.find_btn.setText("Знайти в мережі")
        self.found.clear()
        if error or not found:
            QMessageBox.information(
                self, "Пошук станцій",
                (f"Помилка: {error}\n\n" if error else "Станції не знайдено.\n\n")
                + "Перевірте, що Open API ввімкнено в застосунку Marstek, комп'ютер у тій самій "
                "мережі, а брандмауер Windows дозволяє Python вхідні з'єднання в приватній мережі. "
                "Можна ввести IP вручну — його видно в застосунку або в роутері.",
            )
            return
        for d in found:
            item = QListWidgetItem(f"{d.host}   {d.title}   прошивка {d.ver}")
            item.setData(Qt.ItemDataRole.UserRole, d.host)
            self.found.addItem(item)
        self.found.show()
        if len(found) == 1:
            self.host.setText(found[0].host)

    # --- збереження ---------------------------------------------------------

    def _save(self) -> None:
        host = self.host.text().strip()
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            QMessageBox.warning(self, "Налаштування", "Вкажіть IPv4-адресу станції, наприклад 192.168.1.50")
            return
        self.cfg = replace(
            self.cfg,
            host=host,
            port=self.port.value(),
            interval=self.interval.value(),
            timeout=float(self.timeout.value()),
            retries=self.retries.value(),
            bind_device_port=self.bind.isChecked(),
            read_battery=self.battery.isChecked(),
            soc_low=self.soc_low.value(),
            rssi_low=self.rssi_low.value(),
            temp_high=self.temp_high.value(),
            offline_after=self.offline_after.value(),
            retention_days=self.retention.value(),
        )
        self.accept()
