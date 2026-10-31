"""Емулятор станції Venus E 3.0 (прошивка 150) для перевірки без заліза.

Запуск: python -m marstek_metrics.mock --port 30000
У програмі вкажіть IP 127.0.0.1 і той самий порт.

Емулятор повторює особливості реальної прошивки: немає bat_power в
ES.GetStatus, нулі CT в ES.GetMode, PV.GetStatus → Method not found.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import threading
import time
from typing import Any

MODEL = "VenusE 3.0"
VER = 150
CAPACITY_WH = 5120


class MockVenus:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        drop_rate: float = 0.0,
        ct_connected: bool = True,
        seed: int | None = None,
    ):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.host, self.port = self.sock.getsockname()
        self.drop_rate = drop_rate
        self.ct_connected = ct_connected
        self.rng = random.Random(seed)
        self.mode = "Auto"
        self.soc = 62.0
        self.grid_in_wh = 18_430.0
        self.grid_out_wh = 15_210.0
        self.meter_import = 1_250_000.0  # 0.1 Wh
        self.meter_export = 34_000.0
        self.requests: list[dict[str, Any]] = []
        self._last = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- фізика ---------------------------------------------------------

    def _house_load(self, now: float) -> float:
        hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60
        base = 250 + 350 * max(0.0, math.sin((hour - 6) / 24 * 2 * math.pi))
        return base + self.rng.uniform(-60, 120)

    def _station_power(self, load: float) -> float:
        """+ віддає в будинок, − заряджається."""
        if self.mode == "Passive":
            return 0.0
        if self.soc > 12 and load > 0:
            return min(load * 0.9, 800.0)  # авто: покриває майже все навантаження
        return -300.0 if self.soc < 95 else 0.0

    def _step(self) -> dict[str, float]:
        now = time.time()
        dt = max(0.0, now - self._last)
        self._last = now
        load = self._house_load(now)
        station = self._station_power(load)
        grid = load - station
        self.soc = min(100.0, max(10.0, self.soc - station * dt / 3600 / CAPACITY_WH * 100))
        if station > 0:
            self.grid_out_wh += station * dt / 3600
        else:
            self.grid_in_wh += -station * dt / 3600
        if grid > 0:
            self.meter_import += grid * dt / 360
        else:
            self.meter_export += -grid * dt / 360
        return {"load": load, "station": station, "grid": grid}

    # --- відповіді ---------------------------------------------------------

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}
        p = self._step()

        def ok(result: dict[str, Any]) -> dict[str, Any]:
            return {"id": req_id, "src": "VenusE 3.0-acd929a6b1c2", "result": result}

        def err(code: int, message: str, data: int | None = None) -> dict[str, Any]:
            e: dict[str, Any] = {"code": code, "message": message}
            if data is not None:
                e["data"] = data
            return {"id": req_id, "src": "VenusE 3.0-acd929a6b1c2", "error": e}

        inst = params.get("id", 0)
        if method == "Marstek.GetDevice":
            return ok({
                "device": MODEL, "ver": VER, "ble_mac": "acd929a6b1c2",
                "wifi_mac": "acd929a6b1c3", "wifi_name": "HOME", "ip": self.host,
            })
        if method == "ES.GetStatus":
            return ok({
                "id": inst, "bat_soc": round(self.soc), "bat_cap": CAPACITY_WH,
                "pv_power": 0, "ongrid_power": round(p["station"]), "offgrid_power": 0,
                "total_pv_energy": 0, "total_grid_output_energy": round(self.grid_out_wh),
                "total_grid_input_energy": round(self.grid_in_wh), "total_load_energy": 0,
            })
        if method == "EM.GetStatus":
            if not self.ct_connected:
                return ok({"id": inst, "ct_state": 0, "a_power": 0, "b_power": 0,
                           "c_power": 0, "total_power": 0})
            return ok({
                "id": inst, "ct_state": 1, "a_power": round(p["grid"]), "b_power": 0,
                "c_power": 0, "total_power": round(p["grid"]),
                "input_energy": round(self.meter_import), "output_energy": round(self.meter_export),
            })
        if method == "ES.GetMode":
            return ok({
                "id": inst, "mode": self.mode, "ongrid_power": round(p["station"]),
                "offgrid_power": 0, "bat_soc": round(self.soc), "ct_state": 0,
                "a_power": 0, "b_power": 0, "c_power": 0, "total_power": 0,
                "input_energy": 0, "output_energy": 0,
            })
        if method == "Wifi.GetStatus":
            return ok({
                "id": inst, "wifi_mac": "acd929a6b1c3", "ssid": "HOME",
                "rssi": self.rng.randint(-72, -55), "sta_ip": self.host,
                "sta_gate": "192.168.1.1", "sta_mask": "255.255.255.0", "sta_dns": "192.168.1.1",
            })
        if method == "Bat.GetStatus":
            return ok({
                "id": inst, "soc": round(self.soc), "charg_flag": True, "dischrg_flag": True,
                "bat_temp": 24.0, "bat_capacity": round(self.soc / 100 * CAPACITY_WH),
                "rated_capacity": CAPACITY_WH,
            })
        if method == "BLE.GetStatus":
            return ok({"id": inst, "state": "disconnect", "ble_mac": "acd929a6b1c2"})
        if method == "ES.SetMode":
            cfg = params.get("config") or {}
            self.mode = cfg.get("mode", self.mode)
            return ok({"id": inst, "set_result": True})
        return err(-32601, "Method not found", 424)

    def serve_forever(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                break
            try:
                msg = json.loads(data)
            except ValueError:
                reply: dict[str, Any] | None = {
                    "id": 0, "error": {"code": -32700, "message": "Parse error"}}
            else:
                self.requests.append(msg)
                if self.rng.random() < self.drop_rate:
                    continue
                reply = self.handle(msg)
            if reply is not None:
                self.sock.sendto(json.dumps(reply).encode(), addr)

    def start(self) -> MockVenus:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.sock.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Емулятор Marstek Venus E 3.0")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=30000)
    ap.add_argument("--drop", type=float, default=0.1, help="частка втрачених відповідей (0..1)")
    ap.add_argument("--no-ct", action="store_true", help="CT-лічильник не підключено")
    args = ap.parse_args()
    mock = MockVenus(args.host, args.port, args.drop, not args.no_ct)
    print(f"Емулятор Venus E 3.0 слухає {mock.host}:{mock.port}. Ctrl+C — вихід.")
    try:
        mock.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
