"""История телеметрии и журнал событий в SQLite."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .alerts import Event
from .models import Telemetry

_TYPES = {
    "ts": "REAL NOT NULL",
    "mode": "TEXT",
    "battery_power_derived": "INTEGER",
    "ct_connected": "INTEGER",
    "charge_allowed": "INTEGER",
    "discharge_allowed": "INTEGER",
}


class Storage:
    """Одно соединение на поток: создавайте свой экземпляр в каждом потоке."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        cols = Telemetry.columns()
        defs = ", ".join(f"{c} {_TYPES.get(c, 'REAL')}" for c in cols)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS samples ({defs})")
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(samples)")}
        for c in cols:
            if c not in existing:
                self.db.execute(f"ALTER TABLE samples ADD COLUMN {c} {_TYPES.get(c, 'REAL')}")
        self.db.execute("CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts)")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL,
                message TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0)"""
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS events_ts ON events(ts)")
        self.db.commit()

    # --- запись ---------------------------------------------------------

    def add_sample(self, t: Telemetry) -> None:
        row = t.to_row()
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        self.db.execute(f"INSERT INTO samples ({cols}) VALUES ({marks})", row)
        self.db.commit()

    def add_events(self, events: list[Event]) -> None:
        if not events:
            return
        self.db.executemany(
            "INSERT INTO events (ts, level, code, message, resolved) VALUES (?, ?, ?, ?, ?)",
            [(e.ts, e.level, e.code, e.message, int(e.resolved)) for e in events],
        )
        self.db.commit()

    def prune(self, retention_days: int) -> None:
        cutoff = time.time() - retention_days * 86400
        self.db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.db.commit()

    # --- чтение ---------------------------------------------------------

    def samples(self, since: float, until: float | None = None) -> list[Telemetry]:
        until = time.time() + 60 if until is None else until
        rows = self.db.execute(
            "SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (since, until)
        ).fetchall()
        return [_row_to_telemetry(r) for r in rows]

    def latest(self) -> Telemetry | None:
        row = self.db.execute("SELECT * FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
        return _row_to_telemetry(row) if row else None

    def events(self, limit: int = 500, levels: tuple[str, ...] | None = None) -> list[Event]:
        sql = "SELECT * FROM events"
        args: list[Any] = []
        if levels:
            sql += f" WHERE level IN ({', '.join('?' for _ in levels)})"
            args += levels
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        return [
            Event(r["level"], r["code"], r["message"], r["ts"], bool(r["resolved"]))
            for r in self.db.execute(sql, args)
        ]

    def count_events_since(self, since: float, levels: tuple[str, ...]) -> int:
        q = f"SELECT COUNT(*) FROM events WHERE ts >= ? AND resolved = 0 AND level IN ({', '.join('?' for _ in levels)})"
        return self.db.execute(q, (since, *levels)).fetchone()[0]


_BOOL_COLS = ("battery_power_derived", "ct_connected", "charge_allowed", "discharge_allowed")


def _row_to_telemetry(row: sqlite3.Row) -> Telemetry:
    data = {k: row[k] for k in row.keys() if k in Telemetry.columns()}
    for k in _BOOL_COLS:
        if data.get(k) is not None:
            data[k] = bool(data[k])
    if data.get("battery_power_derived") is None:
        data["battery_power_derived"] = False
    return Telemetry(**data)
