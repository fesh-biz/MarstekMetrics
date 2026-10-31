"""Розрахунок споживання енергії за історією телеметрії."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import pairwise

from .models import Telemetry

# Якщо між вимірами минуло більше, відрізок не інтегруємо —
# програма була вимкнена або станція не відповідала.
MAX_GAP_S = 300


def integrate_wh(
    samples: Iterable[Telemetry],
    value: Callable[[Telemetry], float | None],
    max_gap: float = MAX_GAP_S,
) -> float:
    """Енергія (Wh) методом трапецій; відрізки з розривом > max_gap пропускаються."""
    total = 0.0
    for a, b in pairwise(samples):
        dt_s = b.ts - a.ts
        if dt_s <= 0 or dt_s > max_gap:
            continue
        va, vb = value(a), value(b)
        if va is None or vb is None:
            continue
        total += (va + vb) / 2 * dt_s / 3600
    return total


def _pos(attr: str) -> Callable[[Telemetry], float | None]:
    def f(t: Telemetry) -> float | None:
        v = getattr(t, attr)
        return None if v is None else max(v, 0.0)
    return f


def _neg(attr: str) -> Callable[[Telemetry], float | None]:
    def f(t: Telemetry) -> float | None:
        v = getattr(t, attr)
        return None if v is None else max(-v, 0.0)
    return f


def _counter_delta(samples: list[Telemetry], attr: str) -> float | None:
    """Приріст накопичувального лічильника (з урахуванням скидань)."""
    values = [getattr(s, attr) for s in samples if getattr(s, attr) is not None]
    if len(values) < 2:
        return None
    total = 0.0
    for a, b in pairwise(values):
        if b >= a:
            total += b - a
    return total


@dataclass
class EnergySummary:
    label: str
    start: float
    end: float
    house_wh: float = 0.0          # споживання будинку
    grid_import_wh: float = 0.0    # взято із зовнішньої мережі
    grid_export_wh: float = 0.0    # віддано у зовнішню мережу
    station_out_wh: float = 0.0    # станція віддала в будинок
    station_in_wh: float = 0.0     # станція взяла на заряджання
    peak_house_w: float | None = None
    avg_house_w: float | None = None
    samples: int = 0
    coverage: float = 0.0          # частка періоду, покрита даними

    @property
    def self_supply_share(self) -> float | None:
        """Яку частку споживання будинку покрила станція."""
        if self.house_wh <= 0:
            return None
        return min(1.0, self.station_out_wh / self.house_wh)


def summarize(samples: list[Telemetry], label: str, start: float, end: float) -> EnergySummary:
    s = EnergySummary(label, start, end, samples=len(samples))
    if not samples:
        return s
    s.house_wh = integrate_wh(samples, _pos("house_power"))
    s.grid_import_wh = integrate_wh(samples, _pos("grid_power"))
    s.grid_export_wh = integrate_wh(samples, _neg("grid_power"))
    out_counter = _counter_delta(samples, "grid_output_wh")
    in_counter = _counter_delta(samples, "grid_input_wh")
    s.station_out_wh = out_counter if out_counter is not None else integrate_wh(samples, _pos("station_power"))
    s.station_in_wh = in_counter if in_counter is not None else integrate_wh(samples, _neg("station_power"))
    house = [t.house_power for t in samples if t.house_power is not None]
    if house:
        s.peak_house_w = max(house)
        s.avg_house_w = sum(house) / len(house)
    covered = sum(
        b.ts - a.ts for a, b in pairwise(samples) if 0 < b.ts - a.ts <= MAX_GAP_S
    )
    span = max(1.0, min(end, samples[-1].ts) - start)
    s.coverage = min(1.0, covered / span)
    return s


def local_day_bounds(day: dt.date) -> tuple[float, float]:
    start = dt.datetime.combine(day, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def daily_summaries(
    load: Callable[[float, float], list[Telemetry]], days: int, today: dt.date | None = None
) -> list[EnergySummary]:
    """Зведення за останні `days` днів (старі → нові). load(since, until) → виміри."""
    today = today or dt.date.today()
    out = []
    for i in range(days - 1, -1, -1):
        day = today - dt.timedelta(days=i)
        start, end = local_day_bounds(day)
        out.append(summarize(load(start, end), day.strftime("%d.%m"), start, end))
    return out
