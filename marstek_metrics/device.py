"""Высокоуровневые вызовы Open API для одной станции."""

from __future__ import annotations

from typing import Any

from .models import DeviceInfo
from .protocol import DEFAULT_PORT, Reply, UdpTransport, local_broadcast_addresses

READ_METHODS = (
    "Marstek.GetDevice",
    "ES.GetStatus",
    "ES.GetMode",
    "EM.GetStatus",
    "Wifi.GetStatus",
    "BLE.GetStatus",
    "Bat.GetStatus",
    "PV.GetStatus",
)


class MarstekDevice:
    def __init__(self, transport: UdpTransport, host: str, port: int = DEFAULT_PORT):
        self.transport = transport
        self.host = host
        self.port = port

    def call(self, method: str, params: dict[str, Any] | None = None, **kw: Any) -> Reply:
        return self.transport.request(self.host, method, params, port=self.port, **kw)

    def get_device(self) -> DeviceInfo:
        reply = self.call("Marstek.GetDevice", {"ble_mac": "0"})
        return DeviceInfo.from_result(self.host, reply.result, reply.src)

    def es_status(self) -> dict[str, Any]:
        return self.call("ES.GetStatus").result

    def es_mode(self) -> dict[str, Any]:
        return self.call("ES.GetMode").result

    def em_status(self) -> dict[str, Any]:
        return self.call("EM.GetStatus").result

    def wifi_status(self) -> dict[str, Any]:
        return self.call("Wifi.GetStatus").result

    def bat_status(self) -> dict[str, Any]:
        return self.call("Bat.GetStatus").result


def discover(
    transport: UdpTransport, port: int = DEFAULT_PORT, listen: float = 3.0
) -> list[DeviceInfo]:
    """Найти станции в локальной сети широковещательным Marstek.GetDevice."""
    replies = transport.broadcast(
        "Marstek.GetDevice",
        {"ble_mac": "0"},
        port=port,
        listen=listen,
        addresses=local_broadcast_addresses(),
    )
    return [DeviceInfo.from_result(host, msg["result"], msg.get("src")) for host, msg in replies]
