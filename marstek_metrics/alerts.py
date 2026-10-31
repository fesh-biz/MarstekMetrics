"""Журнал ошибок и предупреждений.

Open API не отдаёт коды аварий станции, поэтому «ошибки» складываются из:
- сбоев связи (таймауты, ошибки JSON-RPC, станция пропала из сети);
- признаков неисправности в телеметрии (CT отключён, запрет заряда/разряда,
  перегрев, слабый Wi-Fi, сброс счётчиков, невозможные значения).

Каждое условие пишется в журнал дважды: когда возникло и когда прошло, —
чтобы журнал не засорялся повторами каждые 30 секунд.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import AppConfig
from .models import MODE_TITLES_RU, DeviceInfo, Telemetry

INFO, WARNING, ERROR = "info", "warning", "error"
LEVEL_RU = {INFO: "Инфо", WARNING: "Внимание", ERROR: "Ошибка"}


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
    resolved: bool = False  # True — запись о том, что проблема прошла


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

    # --- подъём / снятие условий ----------------------------------------

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

    # --- связь ---------------------------------------------------------

    def on_cycle(self, ok_methods: list[str], errors: list[CommError]) -> list[Event]:
        out: list[Event] = []
        for m in ok_methods:
            self.fail_streak[m] = 0
            out += self._clear(f"comm:{m}", f"{m}: связь восстановлена")
        for e in errors:
            if e.kind == "rpc" and e.code == -32601:
                if e.method not in self.unsupported:
                    self.unsupported.add(e.method)
                    out.append(Event(INFO, f"unsupported:{e.method}",
                                     f"{e.method} не поддерживается этой прошивкой"))
                continue
            n = self.fail_streak.get(e.method, 0) + 1
            self.fail_streak[e.method] = n
            if e.kind == "rpc":
                out.append(Event(WARNING, f"rpc:{e.method}", f"Станция вернула ошибку: {e.message}"))
            elif n >= 2:
                out += self._raise(f"comm:{e.method}", WARNING,
                                   f"{e.method}: нет ответа {n} раза подряд")

        if ok_methods:
            self.dead_cycles = 0
            if self.offline:
                self.offline = False
                out += self._clear("offline", "Станция снова на связи")
        elif errors:
            self.dead_cycles += 1
            if self.dead_cycles >= self.cfg.offline_after and not self.offline:
                self.offline = True
                out += self._raise(
                    "offline", ERROR,
                    f"Станция не отвечает {self.dead_cycles} циклов подряд — "
                    "проверьте питание, Wi-Fi и включён ли Open API",
                )
        return out

    def on_device(self, info: DeviceInfo) -> list[Event]:
        out: list[Event] = []
        prev = self.info
        self.info = info
        if prev is None:
            out.append(Event(INFO, "device", f"Подключено: {info.title} ({info.model}), прошивка {info.ver}"))
            if not info.profile.supported:
                out.append(Event(WARNING, "device:unsupported",
                                 f"Модель {info.model} не поддерживается или работает с Open API нестабильно"))
        elif prev.ver != info.ver:
            out.append(Event(INFO, "firmware", f"Прошивка изменилась: {prev.ver} → {info.ver}. "
                             "Проверьте, что Open API всё ещё включён"))
        return out

    # --- телеметрия ----------------------------------------------------

    def on_telemetry(self, t: Telemetry) -> list[Event]:
        cfg = self.cfg
        out: list[Event] = []
        prev, self.last = self.last, t

        if t.ct_connected is not None:
            out += self._cond(not t.ct_connected, "ct", WARNING,
                              "CT-счётчик не подключён — расход дома посчитать нельзя",
                              "CT-счётчик снова на связи")
        if t.soc is not None:
            out += self._cond(t.soc <= cfg.soc_low, "soc_low", WARNING,
                              f"Низкий заряд батареи: {t.soc:.0f}%",
                              f"Заряд восстановился: {t.soc:.0f}%")
        if t.rssi is not None:
            out += self._cond(t.rssi <= cfg.rssi_low, "wifi", WARNING,
                              f"Слабый сигнал Wi-Fi: {t.rssi:.0f} dBm — возможны потери связи",
                              f"Сигнал Wi-Fi в норме: {t.rssi:.0f} dBm")
        if t.bat_temp is not None:
            out += self._cond(t.bat_temp >= cfg.temp_high, "temp_high", ERROR,
                              f"Перегрев батареи: {t.bat_temp:.1f} °C",
                              f"Температура батареи в норме: {t.bat_temp:.1f} °C")
            out += self._cond(t.bat_temp <= 0, "temp_low", WARNING,
                              f"Батарея переохлаждена: {t.bat_temp:.1f} °C — заряд может быть ограничен",
                              f"Температура батареи в норме: {t.bat_temp:.1f} °C")
        if t.charge_allowed is not None:
            out += self._cond(not t.charge_allowed, "charge_blocked", WARNING,
                              "BMS запретила заряд батареи", "Заряд батареи снова разрешён")
        if t.discharge_allowed is not None:
            out += self._cond(not t.discharge_allowed, "discharge_blocked", WARNING,
                              "BMS запретила разряд батареи", "Разряд батареи снова разрешён")

        bad = [name for name, v in (("станция", t.station_power), ("сеть", t.grid_power),
                                     ("батарея", t.battery_power)) if v is not None and abs(v) > 10_000]
        if t.soc is not None and not 0 <= t.soc <= 100:
            bad.append("заряд")
        out += self._cond(bool(bad), "implausible", WARNING,
                          "Подозрительные значения: " + ", ".join(bad),
                          "Значения снова правдоподобны")

        if prev is not None:
            if prev.mode and t.mode and prev.mode != t.mode:
                out.append(Event(INFO, "mode", "Режим: {} → {}".format(
                    MODE_TITLES_RU.get(prev.mode, prev.mode), MODE_TITLES_RU.get(t.mode, t.mode))))
            for name, a, b in (("взято из сети", prev.grid_input_wh, t.grid_input_wh),
                               ("отдано станцией", prev.grid_output_wh, t.grid_output_wh)):
                if a is not None and b is not None and b < a - 1:
                    out.append(Event(WARNING, "counter_reset",
                                     f"Счётчик «{name}» уменьшился ({a:.0f} → {b:.0f} Wh): сброс или перезагрузка станции"))
        return out
