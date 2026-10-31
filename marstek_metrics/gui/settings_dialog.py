"""Окно настроек с поиском станций в сети."""

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
    done = Signal(object, str)  # list[DeviceInfo], ошибка


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
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        self.cfg = cfg
        self._signals = _DiscoverSignals()
        self._signals.done.connect(self._on_discovered)

        root = QVBoxLayout(self)

        # --- станция
        box = QGroupBox("Станция")
        form = QFormLayout(box)
        host_row = QHBoxLayout()
        self.host = QLineEdit(cfg.host)
        self.host.setPlaceholderText("например, 192.168.1.50")
        self.find_btn = QPushButton("Найти в сети")
        self.find_btn.clicked.connect(lambda _c=False: self._discover())
        host_row.addWidget(self.host, 1)
        host_row.addWidget(self.find_btn)
        form.addRow("IP-адрес", host_row)
        self.found = QListWidget()
        self.found.setMaximumHeight(90)
        self.found.hide()
        self.found.itemClicked.connect(lambda it: self.host.setText(it.data(Qt.ItemDataRole.UserRole)))
        form.addRow("", self.found)
        self.port = _spin(1, 65535, cfg.port)
        form.addRow("UDP-порт Open API", self.port)
        hint = QLabel("Open API включается в приложении Marstek. Закрепите за станцией "
                      "постоянный IP в настройках роутера.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        root.addWidget(box)

        # --- опрос
        box = QGroupBox("Опрос")
        form = QFormLayout(box)
        self.interval = _spin(10, 3600, cfg.interval, " с")
        self.interval.setToolTip("Опрос чаще 30 с может делать станцию нестабильной")
        form.addRow("Интервал", self.interval)
        self.timeout = _spin(2, 60, int(cfg.timeout), " с")
        form.addRow("Ожидание ответа", self.timeout)
        self.retries = _spin(0, 5, cfg.retries)
        form.addRow("Повторов при потере", self.retries)
        self.bind = QCheckBox("Принимать ответы на порту станции (рекомендуется)")
        self.bind.setChecked(cfg.bind_device_port)
        form.addRow("", self.bind)
        self.battery = QCheckBox("Читать данные BMS (температура, запреты заряда/разряда)")
        self.battery.setChecked(cfg.read_battery)
        self.battery.toggled.connect(self._warn_battery)
        form.addRow("", self.battery)
        root.addWidget(box)

        # --- пороги
        box = QGroupBox("Предупреждения")
        form = QFormLayout(box)
        self.soc_low = _spin(0, 100, cfg.soc_low, " %")
        form.addRow("Низкий заряд, ниже", self.soc_low)
        self.rssi_low = _spin(-100, -30, cfg.rssi_low, " dBm")
        form.addRow("Слабый Wi-Fi, ниже", self.rssi_low)
        self.temp_high = _spin(30, 80, cfg.temp_high, " °C")
        form.addRow("Перегрев батареи, от", self.temp_high)
        self.offline_after = _spin(1, 20, cfg.offline_after)
        form.addRow("«Нет связи» после циклов", self.offline_after)
        self.retention = _spin(7, 3650, cfg.retention_days, " дн")
        form.addRow("Хранить историю", self.retention)
        root.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _warn_battery(self, on: bool) -> None:
        if on:
            QMessageBox.warning(
                self, "Данные BMS",
                "На некоторых прошивках запрос Bat.GetStatus перезагружает станцию.\n"
                "Включите и понаблюдайте: если станция начнёт перезагружаться — выключите.",
            )

    # --- поиск ------------------------------------------------------------

    def _discover(self) -> None:
        self.find_btn.setEnabled(False)
        self.find_btn.setText("Ищу…")
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
        self.find_btn.setText("Найти в сети")
        self.found.clear()
        if error or not found:
            QMessageBox.information(
                self, "Поиск станций",
                (f"Ошибка: {error}\n\n" if error else "Станции не найдены.\n\n")
                + "Проверьте, что Open API включён в приложении Marstek, компьютер в той же "
                "сети и брандмауэр Windows разрешает Python входящие соединения в частной сети. "
                "Можно ввести IP вручную — его видно в приложении или в роутере.",
            )
            return
        for d in found:
            item = QListWidgetItem(f"{d.host}   {d.title}   прошивка {d.ver}")
            item.setData(Qt.ItemDataRole.UserRole, d.host)
            self.found.addItem(item)
        self.found.show()
        if len(found) == 1:
            self.host.setText(found[0].host)

    # --- сохранение ---------------------------------------------------------

    def _save(self) -> None:
        host = self.host.text().strip()
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            QMessageBox.warning(self, "Настройки", "Укажите IPv4-адрес станции, например 192.168.1.50")
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
