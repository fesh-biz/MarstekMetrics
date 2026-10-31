"""Модели данных и нормализация ответов станции.

Соглашение о знаках внутри программы:
- station_power  (ongrid_power): + станция отдаёт в домашнюю сеть, − берёт из неё (заряд);
- battery_power: + батарея заряжается, − разряжается (знак Open API);
- grid_power     (CT total_power): + дом берёт из внешней сети (импорт), − отдаёт (экспорт);
- house_power: потребление дома = grid_power + station_power + offgrid_power.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any

MODE_NAMES = {0: "Auto", 1: "AI", 2: "Manual", 3: "Passive", 4: "UPS"}
MODE_TITLES_RU = {
    "Auto": "Авто",
    "AI": "AI",
    "Manual": "Расписание",
    "Passive": "Пассивный",
    "UPS": "ИБП",
}


# --- модель и прошивка ---------------------------------------------------


def device_family(model: str | None) -> str:
    """Семейство по строке device из Marstek.GetDevice.

    Возвращает: venus_a, venus_c, venus_d, venus_e, venus_e_mini, venus_e2, unknown.
    """
    if not model:
        return "unknown"
    m = re.sub(r"[\s_-]+", "", model).lower()
    if m in {"vnsem0"} or "emini" in m:
        return "venus_e_mini"
    if m in {"venuse", "hmg50", "vnse2"} or m.startswith("venuse2"):
        return "venus_e2"
    if m.startswith("venuse") or m.startswith("vnse3"):
        return "venus_e"
    if m.startswith("venusa") or m.startswith("vnsa"):
        return "venus_a"
    if m.startswith("venusd") or m.startswith("vnsd"):
        return "venus_d"
    if m.startswith("venusc"):
        return "venus_c"
    return "unknown"


FAMILY_TITLES = {
    "venus_a": "Venus A",
    "venus_c": "Venus C",
    "venus_d": "Venus D",
    "venus_e": "Venus E 3.0",
    "venus_e_mini": "Venus E mini",
    "venus_e2": "Venus E 2.0",
    "unknown": "Неизвестная модель",
}


@dataclass(frozen=True)
class Profile:
    """Что умеет станция и в каких единицах отдаёт счётчики."""

    family: str
    ver: int | None

    @property
    def has_pv(self) -> bool:
        return self.family in {"venus_a", "venus_d"}

    @property
    def em_energy_scale(self) -> float | None:
        """Множитель EM input/output_energy → Wh (None — не доверять полям)."""
        if self.ver is None:
            return None
        if self.family == "venus_c" and self.ver >= 155:
            return 0.1
        if self.family in {"venus_a", "venus_d", "venus_e", "venus_e_mini"} and self.ver >= 150:
            return 0.1
        return None

    @property
    def pv_energy_scale(self) -> float:
        """Множитель total_pv_energy → Wh."""
        if self.ver is None or not self.has_pv:
            return 1.0
        if self.family == "venus_a" and self.ver >= 149:
            return 10.0
        if self.ver >= 150:
            return 10.0
        return 1.0

    @property
    def supported(self) -> bool:
        return self.family not in {"venus_e2", "unknown"}


def parse_ver(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass
class DeviceInfo:
    host: str
    model: str | None = None
    ver: int | None = None
    ble_mac: str | None = None
    wifi_mac: str | None = None
    wifi_name: str | None = None
    reported_ip: str | None = None
    src: str | None = None

    @property
    def profile(self) -> Profile:
        return Profile(device_family(self.model), self.ver)

    @property
    def title(self) -> str:
        return FAMILY_TITLES[self.profile.family]

    @classmethod
    def from_result(cls, host: str, result: dict[str, Any], src: str | None = None) -> DeviceInfo:
        return cls(
            host=host,
            model=_str(result.get("device")),
            ver=parse_ver(result.get("ver")),
            ble_mac=_str(result.get("ble_mac")),
            wifi_mac=_str(result.get("wifi_mac")),
            wifi_name=_str(result.get("wifi_name")),
            reported_ip=_str(result.get("ip")),
            src=src,
        )


# --- телеметрия ------------------------------------------------------------


@dataclass
class Telemetry:
    """Один срез состояния станции (уже в единицах W / Wh / % / °C)."""

    ts: float = field(default_factory=time.time)
    soc: float | None = None
    bat_capacity_wh: float | None = None
    station_power: float | None = None
    offgrid_power: float | None = None
    pv_power: float | None = None
    battery_power: float | None = None
    battery_power_derived: bool = False
    grid_power: float | None = None
    phase_a: float | None = None
    phase_b: float | None = None
    phase_c: float | None = None
    ct_connected: bool | None = None
    house_power: float | None = None
    mode: str | None = None
    grid_input_wh: float | None = None
    grid_output_wh: float | None = None
    load_wh: float | None = None
    pv_wh: float | None = None
    meter_import_wh: float | None = None
    meter_export_wh: float | None = None
    rssi: float | None = None
    bat_temp: float | None = None
    charge_allowed: bool | None = None
    discharge_allowed: bool | None = None

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("battery_power_derived", "ct_connected", "charge_allowed", "discharge_allowed"):
            if row[key] is not None:
                row[key] = int(row[key])
        return row

    @classmethod
    def columns(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @property
    def battery_state(self) -> str:
        if self.battery_power is None:
            return "—"
        if self.battery_power > 5:
            return "заряд"
        if self.battery_power < -5:
            return "разряд"
        return "ожидание"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN → None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return None


def normalize_mode(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return MODE_NAMES.get(int(value))
    text = str(value).strip()
    for name in MODE_NAMES.values():
        if text.lower() == name.lower():
            return name
    return text or None


def apply_es_status(t: Telemetry, r: dict[str, Any], profile: Profile) -> None:
    t.soc = _num(r.get("bat_soc"))
    t.bat_capacity_wh = _num(r.get("bat_cap"))
    t.station_power = _num(r.get("ongrid_power"))
    t.offgrid_power = _num(r.get("offgrid_power"))
    pv = _num(r.get("pv_power")) if profile.has_pv else None
    t.pv_power = pv
    bat = _num(r.get("bat_power"))
    if bat is None and t.station_power is not None:
        # Venus E 3.0 часто не присылает bat_power: считаем по балансу мощностей
        bat = (pv or 0.0) - t.station_power - (t.offgrid_power or 0.0)
        t.battery_power_derived = True
    t.battery_power = bat
    t.grid_input_wh = _num(r.get("total_grid_input_energy"))
    t.grid_output_wh = _num(r.get("total_grid_output_energy"))
    t.load_wh = _num(r.get("total_load_energy"))
    pv_wh = _num(r.get("total_pv_energy"))
    t.pv_wh = pv_wh * profile.pv_energy_scale if (pv_wh is not None and profile.has_pv) else None


def apply_em_status(t: Telemetry, r: dict[str, Any], profile: Profile) -> None:
    state = r.get("ct_state")
    t.ct_connected = None if state is None else bool(_num(state))
    t.phase_a = _num(r.get("a_power"))
    t.phase_b = _num(r.get("b_power"))
    t.phase_c = _num(r.get("c_power"))
    t.grid_power = _num(r.get("total_power"))
    if t.grid_power is None and any(v is not None for v in (t.phase_a, t.phase_b, t.phase_c)):
        t.grid_power = sum(v or 0.0 for v in (t.phase_a, t.phase_b, t.phase_c))
    scale = profile.em_energy_scale
    if scale is not None:
        imp, exp = _num(r.get("input_energy")), _num(r.get("output_energy"))
        t.meter_import_wh = imp * scale if imp is not None else None
        t.meter_export_wh = exp * scale if exp is not None else None


def apply_es_mode(t: Telemetry, r: dict[str, Any]) -> None:
    t.mode = normalize_mode(r.get("mode"))
    if t.soc is None:
        t.soc = _num(r.get("bat_soc"))
    if t.station_power is None:
        t.station_power = _num(r.get("ongrid_power"))
    if t.offgrid_power is None:
        t.offgrid_power = _num(r.get("offgrid_power"))
    # CT-поля в ES.GetMode на fw 150 бывают нулями при живом счётчике —
    # берём их, только если EM.GetStatus ничего не дал и CT отмечен подключённым.
    if t.grid_power is None and _num(r.get("ct_state")) == 1:
        t.ct_connected = True
        t.grid_power = _num(r.get("total_power"))
        t.phase_a = _num(r.get("a_power"))
        t.phase_b = _num(r.get("b_power"))
        t.phase_c = _num(r.get("c_power"))


def apply_wifi(t: Telemetry, r: dict[str, Any]) -> None:
    t.rssi = _num(r.get("rssi"))


def apply_battery(t: Telemetry, r: dict[str, Any]) -> None:
    temp = _num(r.get("bat_temp"))
    if temp is not None and temp > 100:
        temp = temp / 10.0  # старые BMS отдают температуру ×10
    t.bat_temp = temp
    t.charge_allowed = _bool(r.get("charg_flag"))
    t.discharge_allowed = _bool(r.get("dischrg_flag"))
    if t.soc is None:
        t.soc = _num(r.get("soc"))


def finalize(t: Telemetry) -> None:
    """Посчитать производные величины."""
    if t.ct_connected and t.grid_power is not None and t.station_power is not None:
        t.house_power = t.grid_power + t.station_power + (t.offgrid_power or 0.0)
