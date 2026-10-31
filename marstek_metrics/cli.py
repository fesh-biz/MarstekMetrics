"""Консольна утиліта для перевірки зв'язку зі станцією.

Приклади:
  marstek-cli discover
  marstek-cli status 192.168.1.50
  marstek-cli raw 192.168.1.50 ES.GetStatus
  marstek-cli raw 192.168.1.50 all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .device import READ_METHODS, MarstekDevice, discover
from .poller import collect_once
from .protocol import DEFAULT_PORT, MarstekError, UdpTransport, local_bind_port


def _transport(args: argparse.Namespace) -> UdpTransport:
    bind = local_bind_port(getattr(args, "host", ""), args.port, not args.random_port)
    return UdpTransport(bind_port=bind, timeout=args.timeout, retries=args.retries)


def cmd_discover(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        print(f"Шукаю станції (UDP {args.port}, локальний порт {tr.local_port})…")
        found = discover(tr, args.port, listen=args.listen)
    if not found:
        print("Нічого не знайдено. Перевірте, що Open API ввімкнено в застосунку Marstek, "
              "комп'ютер у тій самій мережі, а брандмауер Windows пропускає Python.")
        return 1
    for d in found:
        print(f"{d.host:15}  {d.title:13}  model={d.model}  ver={d.ver}  ble_mac={d.ble_mac}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        dev = MarstekDevice(tr, args.host, args.port)
        info = dev.get_device()
        print(f"{info.title} ({info.model}), прошивка {info.ver}, {args.host}")
        tel, errors = collect_once(dev, info.profile, slow=True, battery=args.battery)
    rows = [
        ("Заряд", tel.soc, "%"),
        ("Споживання будинку", tel.house_power, "W"),
        ("Мережа (CT, + імпорт)", tel.grid_power, "W"),
        ("Станція → будинок (+) / заряджання (−)", tel.station_power, "W"),
        ("Батарея (+ заряджання)", tel.battery_power, "W"),
        ("Off-grid вихід", tel.offgrid_power, "W"),
        ("CT підключено", tel.ct_connected, ""),
        ("Режим", tel.mode, ""),
        ("Усього взято з мережі", tel.grid_input_wh, "Wh"),
        ("Усього віддано", tel.grid_output_wh, "Wh"),
        ("Wi-Fi", tel.rssi, "dBm"),
    ]
    if args.battery:
        rows.append(("Температура батареї", tel.bat_temp, "°C"))
    for name, value, unit in rows:
        if value is None:
            shown = "—"
        elif isinstance(value, bool):
            shown = "так" if value else "ні"
        elif isinstance(value, float):
            shown = f"{value:g} {unit}"
        else:
            shown = f"{value} {unit}".strip()
        print(f"  {name:32} {shown}")
    for e in errors:
        print(f"  ! {e}")
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    methods = READ_METHODS if args.method == "all" else (args.method,)
    if args.method == "all":
        methods = tuple(m for m in methods if m != "Bat.GetStatus" or args.battery)
    params = json.loads(args.params) if args.params else None
    code = 0
    with _transport(args) as tr:
        dev = MarstekDevice(tr, args.host, args.port)
        for method in methods:
            p = params
            if p is None:
                p = {"ble_mac": "0"} if method == "Marstek.GetDevice" else {"id": 0}
            try:
                reply = dev.call(method, p)
                print(f"# {method} ({reply.elapsed:.2f} c)")
                print(json.dumps(reply.raw, ensure_ascii=False, indent=2))
            except MarstekError as exc:
                print(f"# {method}: {exc}")
                code = 1
            time.sleep(args.gap)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="marstek-cli", description="Перевірка Marstek Open API")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP-порт станції")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--random-port", action="store_true",
                    help="не прив'язувати локальний сокет до порту станції")
    ap.add_argument("-v", "--verbose", action="store_true", help="показувати пакети")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="знайти станції в мережі")
    d.add_argument("--listen", type=float, default=3.0)
    d.set_defaults(func=cmd_discover)

    s = sub.add_parser("status", help="поточний стан")
    s.add_argument("host")
    s.add_argument("--battery", action="store_true",
                   help="запитати Bat.GetStatus (на частині прошивок перезавантажує станцію)")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("raw", help="сира відповідь методу (або all)")
    r.add_argument("host")
    r.add_argument("method")
    r.add_argument("--params", help='JSON, наприклад {"id":0}')
    r.add_argument("--gap", type=float, default=1.0, help="пауза між запитами, с")
    r.add_argument("--battery", action="store_true", help="додати Bat.GetStatus до all")
    r.set_defaults(func=cmd_raw)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except MarstekError as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
