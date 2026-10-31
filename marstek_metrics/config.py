"""Налаштування програми (JSON у теці користувача)."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)


def data_dir() -> Path:
    """Тека для налаштувань, бази та логів."""
    override = os.environ.get("MARSTEK_METRICS_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "MarstekMetrics"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "marstek-metrics"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class AppConfig:
    host: str = ""
    port: int = 30000
    interval: int = 30            # секунд між циклами опитування
    request_gap: float = 1.0      # пауза між запитами всередині циклу
    timeout: float = 10.0
    retries: int = 2
    bind_device_port: bool = True # слухати відповіді на порту станції
    read_battery: bool = False    # Bat.GetStatus: на частині прошивок перезавантажує станцію
    soc_low: int = 10             # % — попередження про низький заряд
    rssi_low: int = -80           # dBm — слабкий Wi-Fi
    temp_high: int = 45           # °C — перегрів батареї
    offline_after: int = 3        # циклів без відповіді → «станція не відповідає»
    retention_days: int = 365

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "config.json"

    @classmethod
    def load(cls) -> AppConfig:
        path = cls.path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Не вдалося прочитати %s: %s", path, exc)
            return cls()
        known = {f.name: f.type for f in fields(cls)}
        cfg = cls()
        for key, value in raw.items():
            if key in known:
                setattr(cfg, key, value)
        return cfg

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
