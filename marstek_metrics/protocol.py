"""UDP-транспорт Marstek Device Open API.

Станция принимает JSON-запросы по UDP и отвечает JSON-RPC-подобными
сообщениями. Протокол описан в docs/open-api.md.

Особенности, которые учитывает клиент:
- ответы по Wi-Fi теряются, поэтому у каждого запроса таймаут и повторы;
- запросы идут строго последовательно (станция плохо переносит параллельные);
- часть прошивок отвечает на порт, который слушает сама станция, а не на
  порт отправителя, поэтому локальный сокет по умолчанию биндится на тот же
  порт (30000). Если порт занят, используется случайный.
"""

from __future__ import annotations

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
DEFAULT_RETRIES = 2  # повторов после первой попытки
RETRY_DELAY = 1.5
MAX_DATAGRAM = 65535


class MarstekError(Exception):
    """Базовая ошибка общения со станцией."""


class MarstekTimeout(MarstekError):
    """Станция не ответила за отведённое время (с учётом повторов)."""

    def __init__(self, method: str, host: str, attempts: int):
        super().__init__(f"{method}: нет ответа от {host} после {attempts} попыток")
        self.method = method
        self.host = host
        self.attempts = attempts


class TransportClosed(MarstekError):
    """Клиент закрыт — ожидание ответа прервано."""


class MarstekRpcError(MarstekError):
    """Станция вернула объект error (JSON-RPC)."""

    METHOD_NOT_FOUND = -32601

    def __init__(self, method: str, code: int | None, message: str, data: Any = None):
        text = f"{method}: ошибка {code} {message}"
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
    """Успешный ответ станции."""

    method: str
    host: str
    src: str | None
    result: dict[str, Any]
    raw: dict[str, Any]
    elapsed: float


class UdpTransport:
    """Последовательный UDP-клиент для одной или нескольких станций.

    Один экземпляр владеет одним сокетом. Методы потокобезопасны: запросы
    из разных потоков выстраиваются в очередь через блокировку.
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
                "Порт %s занят (%s), использую случайный порт. "
                "Если станция не отвечает, закройте другие программы, "
                "работающие с Marstek.", bind_port, exc,
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
        """Закрыть клиент. Можно звать из другого потока: идущий запрос прервётся
        в течение ~0,25 с, а сокет закроет поток, который им сейчас пользуется."""
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
            raise TransportClosed("клиент закрыт")

    def __enter__(self) -> UdpTransport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- запросы ---------------------------------------------------------

    def _take_id(self) -> int:
        self._next_id = (self._next_id + 1) % 1_000_000
        return self._next_id

    def _drain(self) -> None:
        """Выбросить опоздавшие ответы на прошлые запросы."""
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
        """Отправить запрос и дождаться ответа с тем же id от этого хоста."""
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
                    log.debug("%s: таймаут, попытка %d/%d", method, attempt, attempts)
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
                    raise MarstekError(f"{method}: в ответе нет result: {raw}")
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
                # Windows: ICMP «порт недоступен» от прошлой отправки
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
            # Ошибка разбора приходит с id=0 — она относится к текущему запросу
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
        """Разослать запрос широковещательно и собрать ответы (host, сообщение)."""
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


def _parse(data: bytes) -> dict[str, Any] | None:
    try:
        msg = json.loads(data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        log.debug("Не JSON: %r", data[:200])
        return None
    return msg if isinstance(msg, dict) else None


def local_broadcast_addresses() -> list[str]:
    """Широковещательные адреса: общий + /24 основного интерфейса."""
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
