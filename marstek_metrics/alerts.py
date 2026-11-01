"""Журнал помилок і попереджень.

Open API не віддає коди аварій станції, тому «помилки» складаються з:
- збоїв зв'язку (тайм-аути, помилки JSON-RPC, станція зникла з мережі);
- ознак несправності в телеметрії (CT відключено, заборона заряджання/розряджання,
  перегрів, слабкий Wi-Fi, скидання лічильників, неможливі значення).

Кожна умова пишеться в журнал двічі: коли виникла і коли минула, —
щоб журнал не засмічувався повторами кожні 30 секунд.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import AppConfig
from .models import MODE_TITLES_UK, DeviceInfo, Telemetry

INFO, WARNING, ERROR = "info", "warning", "error"
LEVEL_UK = {INFO: "Інфо", WARNING: "Увага", ERROR: "Помилка"}


@dataclass
class CommError:
    method: str
    kind: str  # timeout | rpc | other
    message: str
    code: int | None = None


@dataclass
class Event:
    level: str
    code: str
    message: str
    ts: float = field(default_factory=time.time)
    resolved: bool = False  # True — запис про те, що проблема минула


class AlertEngine:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.active: dict[str, Event] = {}
        self.fail_streak: dict[str, int] = {}
        self.dead_cycles = 0
        self.offline = False
        self.last: Telemetry | None = None
        self.info: DeviceInfo | None = None
        self.unsupported: set[str] = set()

    # --- підняття / зняття умов ----------------------------------------

    def _raise(self, code: str, level: str, message: str) -> list[Event]:
        if code in self.active:
            return []
        ev = Event(level, code, message)
        self.active[code] = ev
        return [ev]

    def _clear(self, code: str, message: str) -> list[Event]:
        ev = self.active.pop(code, None)
        if ev is None:
            return []
        return [Event(INFO, code, message, resolved=True)]

    def _cond(self, on: bool, code: str, level: str, msg_on: str, msg_off: str) -> list[Event]:
        return self._raise(code, level, msg_on) if on else self._clear(code, msg_off)

    # --- зв'язок ---------------------------------------------------------

    def on_cycle(self, ok_methods: list[str], errors: list[CommError]) -> list[Event]:
        out: list[Event] = []
        for m in ok_methods:
            self.fail_streak[m] = 0
            out += self._clear(f"comm:{m}", f"{m}: зв'язок відновлено")
        for e in errors:
            if e.kind == "rpc" and e.code == -32601:
                if e.method not in self.unsupported:
                    self.unsupported.add(e.method)
                    out.append(Event(INFO, f"unsupported:{e.method}",
                                     f"{e.method} не підтримується цією прошивкою"))
                continue
            n = self.fail_streak.get(e.method, 0) + 1
            self.fail_streak[e.method] = n
            if e.kind == "rpc":
                out.append(Event(WARNING, f"rpc:{e.method}", f"Станція повернула помилку: {e.message}"))
            elif n >= 2:
                out += self._raise(f"comm:{e.method}", WARNING,
                                   f"{e.method}: немає відповіді {n} рази поспіль")

        if ok_methods:
            self.dead_cycles = 0
            if self.offline:
                self.offline = False
                out += self._clear("offline", "Станція знову на зв'язку")
        elif errors:
            self.dead_cycles += 1
            if self.dead_cycles >= self.cfg.offline_after and not self.offline:
                self.offline = True
                out += self._raise(
                    "offline", ERROR,
                    f"Станція не відповідає {self.dead_cycles} циклів поспіль — "
                    "перевірте живлення, Wi-Fi і чи ввімкнено Open API",
                )
        return out

    def on_device(self, info: DeviceInfo) -> list[Event]:
        out: list[Event] = []
        prev = self.info
        self.info = info
        if prev is None:
            out.append(Event(INFO, "device", f"Підключено: {info.title} ({info.model}), прошивка {info.ver}"))
            if not info.profile.supported:
                out.append(Event(WARNING, "device:unsupported",
                                 f"Модель {info.model} не підтримується або працює з Open API нестабільно"))
        elif prev.ver != info.ver:
            out.append(Event(INFO, "firmware", f"Прошивка змінилася: {prev.ver} → {info.ver}. "
                             "Перевірте, що Open API досі ввімкнено"))
        return out

    # --- телеметрія ----------------------------------------------------

    def on_telemetry(self, t: Telemetry) -> list[Event]:
        cfg = self.cfg
        out: list[Event] = []
        prev, self.last = self.last, t

        if t.soc is not None:
            out += self._cond(t.soc <= cfg.soc_low, "soc_low", WARNING,
                              f"Низький заряд батареї: {t.soc:.0f}%",
                              f"Заряд відновився: {t.soc:.0f}%")
        if t.rssi is not None:
            out += self._cond(t.rssi <= cfg.rssi_low, "wifi", WARNING,
                              f"Слабкий сигнал Wi-Fi: {t.rssi:.0f} dBm — можливі втрати зв'язку",
                              f"Сигнал Wi-Fi у нормі: {t.rssi:.0f} dBm")
        if t.bat_temp is not None:
            out += self._cond(t.bat_temp >= cfg.temp_high, "temp_high", ERROR,
                              f"Перегрів батареї: {t.bat_temp:.1f} °C",
                              f"Температура батареї в нормі: {t.bat_temp:.1f} °C")
            out += self._cond(t.bat_temp <= 0, "temp_low", WARNING,
                              f"Батарея переохолоджена: {t.bat_temp:.1f} °C — заряджання може бути обмежене",
                              f"Температура батареї в нормі: {t.bat_temp:.1f} °C")
        if t.charge_allowed is not None:
            out += self._cond(not t.charge_allowed, "charge_blocked", WARNING,
                              "BMS заборонила заряджання батареї", "Заряджання батареї знову дозволено")
        if t.discharge_allowed is not None:
            out += self._cond(not t.discharge_allowed, "discharge_blocked", WARNING,
                              "BMS заборонила розряджання батареї", "Розряджання батареї знову дозволено")

        bad = [name for name, v in (("станція", t.station_power), ("мережа", t.grid_power),
                                     ("батарея", t.battery_power)) if v is not None and abs(v) > 10_000]
        if t.soc is not None and not 0 <= t.soc <= 100:
            bad.append("заряд")
        out += self._cond(bool(bad), "implausible", WARNING,
                          "Підозрілі значення: " + ", ".join(bad),
                          "Значення знову правдоподібні")

        if prev is not None:
            if prev.mode and t.mode and prev.mode != t.mode:
                out.append(Event(INFO, "mode", "Режим: {} → {}".format(
                    MODE_TITLES_UK.get(prev.mode, prev.mode), MODE_TITLES_UK.get(t.mode, t.mode))))
            for name, a, b in (("взято з мережі", prev.grid_input_wh, t.grid_input_wh),
                               ("віддано станцією", prev.grid_output_wh, t.grid_output_wh)):
                if a is not None and b is not None and b < a - 1:
                    out.append(Event(WARNING, "counter_reset",
                                     f"Лічильник «{name}» зменшився ({a:.0f} → {b:.0f} Wh): скидання або перезавантаження станції"))
        return out
