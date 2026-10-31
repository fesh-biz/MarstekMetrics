"""Форматирование чисел для интерфейса."""

from __future__ import annotations

import datetime as dt

DASH = "—"


def power(w: float | None, signed: bool = False) -> str:
    if w is None:
        return DASH
    sign = "+" if signed and w > 0 else ""
    if abs(w) >= 1000:
        return f"{sign}{w / 1000:.2f} кВт".replace(".", ",")
    return f"{sign}{w:.0f} Вт"


def energy(wh: float | None) -> str:
    if wh is None:
        return DASH
    if abs(wh) >= 100:
        return f"{wh / 1000:.2f} кВт·ч".replace(".", ",")
    return f"{wh:.0f} Вт·ч"


def percent(v: float | None, digits: int = 0) -> str:
    return DASH if v is None else f"{v:.{digits}f}%".replace(".", ",")


def ts_time(ts: float | None) -> str:
    if ts is None:
        return DASH
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def ts_full(ts: float | None) -> str:
    if ts is None:
        return DASH
    d = dt.datetime.fromtimestamp(ts)
    if d.date() == dt.date.today():
        return "сегодня " + d.strftime("%H:%M:%S")
    return d.strftime("%d.%m.%Y %H:%M:%S")


def ago(ts: float | None, now: float) -> str:
    if ts is None:
        return "нет данных"
    s = max(0, int(now - ts))
    if s < 60:
        return f"{s} с назад"
    if s < 3600:
        return f"{s // 60} мин назад"
    return f"{s // 3600} ч назад"


def rssi_quality(rssi: float | None) -> str:
    if rssi is None:
        return DASH
    if rssi >= -60:
        q = "отличный"
    elif rssi >= -70:
        q = "хороший"
    elif rssi >= -80:
        q = "слабый"
    else:
        q = "очень слабый"
    return f"{rssi:.0f} dBm, {q}"


def yes_no(v: bool | None) -> str:
    return DASH if v is None else ("да" if v else "нет")
