"""UDP-транспорт Marstek Device Open API.

Станція приймає JSON-запити по UDP і відповідає JSON-RPC-подібними
повідомленнями. Протокол описано в docs/open-api.md.

Особливості, які враховує клієнт:
- відповіді через Wi-Fi губляться, тому кожен запит має тайм-аут і повтори;
- запити йдуть суворо послідовно (станція погано переносить паралельні);
- частина прошивок відповідає на порт, який слухає сама станція, а не на
  порт відправника, тому локальний сокет типово прив'язується до того самого
  порту (30000). Якщо порт зайнятий, використовується випадковий.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PORT = 30000
DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 2  # повторів після першої спроби
RETRY_DELAY = 1.5
MAX_DATAGRAM = 65535


class MarstekError(Exception):
    """Базова помилка зв'язку зі станцією."""


class MarstekTimeout(MarstekError):
    """Станція не відповіла за відведений час (з урахуванням повторів)."""

    def __init__(self, method: str, host: str, attempts: int):
        super().__init__(f"{method}: немає відповіді від {host} після {attempts} спроб")
        self.method = method
        self.host = host
        self.attempts = attempts


class TransportClosed(MarstekError):
    """Клієнт закрито — очікування відповіді перервано."""


class MarstekRpcError(MarstekError):
    """Станція повернула об'єкт error (JSON-RPC)."""

    METHOD_NOT_FOUND = -32601

    def __init__(self, method: str, code: int | None, message: str, data: Any = None):
        text = f"{method}: помилка {code} {message}"
        if data is not None:
            text += f" (data={data})"
        super().__init__(text)
        self.method = method
        self.code = code
        self.message = message
        self.data = data

    @property
    def method_not_found(self) -> bool:
        return self.code == self.METHOD_NOT_FOUND


@dataclass(frozen=True)
class Reply:
    """Успішна відповідь станції."""

    method: str
    host: str
    src: str | None
    result: dict[str, Any]
    raw: dict[str, Any]
    elapsed: float


class UdpTransport:
    """Послідовний UDP-клієнт для однієї або кількох станцій.

    Один екземпляр володіє одним сокетом. Методи потокобезпечні: запити
    з різних потоків шикуються в чергу через блокування.
    """

    def __init__(
        self,
        bind_port: int | None = DEFAULT_PORT,
        bind_host: str = "0.0.0.0",
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self.timeout = timeout
        self.retries = retries
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._next_id = int(time.time()) % 10000
        self._sock = self._open_socket(bind_host, bind_port)

    # --- сокет ---------------------------------------------------------

    @staticmethod
    def _open_socket(bind_host: str, bind_port: int | None) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind((bind_host, bind_port or 0))
        except OSError as exc:
            if not bind_port:
                sock.close()
                raise
            log.warning(
                "Порт %s зайнятий (%s), використовую випадковий порт. "
                "Якщо станція не відповідає, закрийте інші програми, "
                "що працюють з Marstek.", bind_port, exc,
            )
            sock.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((bind_host, 0))
        return sock

    @property
    def local_port(self) -> int:
        return self._sock.getsockname()[1]

    def close(self) -> None:
        """Закрити клієнт. Можна викликати з іншого потоку: поточний запит перерветься
        протягом ~0,25 с, а сокет закриє потік, який зараз ним користується."""
        self._closing.set()
        if self._lock.acquire(blocking=False):
            try:
                self._sock.close()
            finally:
                self._lock.release()

    @property
    def closed(self) -> bool:
        return self._closing.is_set()

    def _check_open(self) -> None:
        if self._closing.is_set():
            self._sock.close()
            raise TransportClosed("клієнт закрито")

    def __enter__(self) -> UdpTransport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- запити ---------------------------------------------------------

    def _take_id(self) -> int:
        self._next_id = (self._next_id + 1) % 1_000_000
        return self._next_id

    def _drain(self) -> None:
        """Відкинути запізнілі відповіді на попередні запити."""
        self._sock.setblocking(False)
        try:
            while True:
                self._sock.recvfrom(MAX_DATAGRAM)
        except (BlockingIOError, OSError):
            pass
        finally:
            self._sock.setblocking(True)

    def request(
        self,
        host: str,
        method: str,
        params: dict[str, Any] | None = None,
        port: int = DEFAULT_PORT,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Reply:
        """Надіслати запит і дочекатися відповіді з тим самим id від цього хоста."""
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        params = {"id": 0} if params is None else params
        attempts = retries + 1

        with self._lock:
            for attempt in range(1, attempts + 1):
                self._check_open()
                req_id = self._take_id()
                payload = json.dumps(
                    {"id": req_id, "method": method, "params": params},
                    separators=(",", ":"),
                ).encode()
                self._drain()
                started = time.monotonic()
                self._sock.sendto(payload, (host, port))
                log.debug("→ %s:%s %s", host, port, payload.decode())
                try:
                    raw = self._wait_reply(host, req_id, started + timeout)
                except TimeoutError:
                    log.debug("%s: тайм-аут, спроба %d/%d", method, attempt, attempts)
                    if attempt < attempts:
                        self._closing.wait(RETRY_DELAY)
                    continue
                elapsed = time.monotonic() - started
                if "error" in raw:
                    err = raw.get("error") or {}
                    raise MarstekRpcError(
                        method, err.get("code"), str(err.get("message", "")), err.get("data")
                    )
                result = raw.get("result")
                if not isinstance(result, dict):
                    raise MarstekError(f"{method}: у відповіді немає result: {raw}")
                return Reply(method, host, raw.get("src"), result, raw, elapsed)
        raise MarstekTimeout(method, host, attempts)

    def _wait_reply(self, host: str, req_id: int, deadline: float) -> dict[str, Any]:
        while True:
            self._check_open()
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            self._sock.settimeout(min(left, 0.25))
            try:
                data, (src_host, _src_port) = self._sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows: ICMP «порт недоступний» від попереднього надсилання
                continue
            if src_host != host:
                continue
            msg = _parse(data)
            if msg is None:
                continue
            log.debug("← %s %s", src_host, data[:500])
            msg_id = msg.get("id")
            if msg_id == req_id or str(msg_id) == str(req_id):
                return msg
            # Помилка розбору приходить з id=0 — вона стосується поточного запиту
            if msg_id == 0 and "error" in msg:
                return msg

    def broadcast(
        self,
        method: str,
        params: dict[str, Any],
        port: int = DEFAULT_PORT,
        listen: float = 3.0,
        repeats: int = 2,
        addresses: list[str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Розіслати запит широкомовно і зібрати відповіді (host, повідомлення)."""
        addresses = addresses or ["255.255.255.255"]
        found: dict[str, dict[str, Any]] = {}
        with self._lock:
            self._drain()
            for _ in range(repeats):
                req_id = self._take_id()
                payload = json.dumps(
                    {"id": req_id, "method": method, "params": params},
                    separators=(",", ":"),
                ).encode()
                for addr in addresses:
                    try:
                        self._sock.sendto(payload, (addr, port))
                    except OSError as exc:
                        log.debug("broadcast %s: %s", addr, exc)
                deadline = time.monotonic() + listen
                while (left := deadline - time.monotonic()) > 0:
                    self._check_open()
                    self._sock.settimeout(min(left, 0.25))
                    try:
                        data, (src_host, _p) = self._sock.recvfrom(MAX_DATAGRAM)
                    except (socket.timeout, ConnectionResetError):
                        continue
                    msg = _parse(data)
                    if msg and isinstance(msg.get("result"), dict):
                        found[src_host] = msg
        return list(found.items())


def local_bind_port(host: str, port: int, bind_device_port: bool = True) -> int | None:
    """Який локальний порт займати для роботи зі станцією host:port.

    Для станції в мережі — той самий порт, що в станції (частина прошивок
    відповідає саме на нього). Для 127.0.0.1 (емулятор на цьому ж комп'ютері)
    — випадковий: на Windows обидва сокети могли б зайняти один порт, і
    відповіді емулятора поверталися б йому самому.
    """
    if not bind_device_port:
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    return port


def _parse(data: bytes) -> dict[str, Any] | None:
    try:
        msg = json.loads(data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        log.debug("Не JSON: %r", data[:200])
        return None
    return msg if isinstance(msg, dict) else None


def local_broadcast_addresses() -> list[str]:
    """Широкомовні адреси: загальна + /24 основного інтерфейсу."""
    addrs = ["255.255.255.255"]
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        ip = probe.getsockname()[0]
        probe.close()
        if not ip.startswith("127."):
            addrs.append(".".join(ip.split(".")[:3] + ["255"]))
    except OSError:
        pass
    return addrs
