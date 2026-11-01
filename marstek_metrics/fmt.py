"""Форматування чисел для інтерфейсу."""

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
        return f"{wh / 1000:.2f} кВт·год".replace(".", ",")
    return f"{wh:.0f} Вт·год"


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
        return "сьогодні " + d.strftime("%H:%M:%S")
    return d.strftime("%d.%m.%Y %H:%M:%S")


def ago(ts: float | None, now: float) -> str:
    if ts is None:
        return "немає даних"
    s = max(0, int(now - ts))
    if s < 60:
        return f"{s} с тому"
    if s < 3600:
        return f"{s // 60} хв тому"
    if s < 86400:
        return f"{s // 3600} год тому"
    return f"{s // 86400} дн тому"


def rssi_quality(rssi: float | None) -> str:
    if rssi is None:
        return DASH
    if rssi >= -60:
        q = "відмінний"
    elif rssi >= -70:
        q = "добрий"
    elif rssi >= -80:
        q = "слабкий"
    else:
        q = "дуже слабкий"
    return f"{rssi:.0f} dBm, {q}"


def yes_no(v: bool | None) -> str:
    return DASH if v is None else ("так" if v else "ні")
