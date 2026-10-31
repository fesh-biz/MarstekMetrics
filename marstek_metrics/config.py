"""Настройки программы (JSON в папке пользователя)."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)


def data_dir() -> Path:
    """Папка для настроек, базы и логов."""
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
    interval: int = 30            # секунд между циклами опроса
    request_gap: float = 1.0      # пауза между запросами внутри цикла
    timeout: float = 10.0
    retries: int = 2
    bind_device_port: bool = True # слушать ответы на порту станции
    read_battery: bool = False    # Bat.GetStatus: на части прошивок перезагружает станцию
    soc_low: int = 10             # % — предупреждение о низком заряде
    rssi_low: int = -80           # dBm — слабый Wi-Fi
    temp_high: int = 45           # °C — перегрев батареи
    offline_after: int = 3        # циклов без ответа → «станция не отвечает»
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
            log.warning("Не удалось прочитать %s: %s", path, exc)
            return cls()
        known = {f.name: f.type for f in fields(cls)}
        cfg = cls()
        for key, value in raw.items():
            if key in known:
                setattr(cfg, key, value)
        return cfg

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
