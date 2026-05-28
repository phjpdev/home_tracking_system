#!/usr/bin/env python3
"""Headless hallway-lighting helper for the calibration session.

Floods the DMX spotlights full-on by setting every channel in the spot
universes (0 and 1 by default) to 255 on the ODE MK3, holds them on, then
guarantees a blackout on exit. Use this to light a dark room (e.g. the hallway
camera) while calibrating.

The Art-Net packet format matches ``dmx_test/dmx_test_gui.py`` (ArtDMX opcode
0x5000, ODE MK3 at 192.168.178.11:6454). No third-party packages required.

Because the Maro server (192.168.178.25:8420) also drives these controllers
("last packet on the wire wins"), this tool can pause Maro's Art-Net output
while the lights are held and re-enable it on exit.

Examples
--------
Check the controllers are reachable::

    python3 spots.py check

Light the spots (universes 0+1) and hold until Ctrl+C, pausing Maro::

    python3 spots.py on

Light for a fixed 10 minutes then auto-blackout::

    python3 spots.py on --seconds 600

Blackout the spot universes (and re-enable Maro)::

    python3 spots.py off
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

ODE_IP = "192.168.178.11"
PIXELATOR_IP = "192.168.178.10"
ARTNET_PORT = 6454
CHANNELS_PER_UNIVERSE = 512

# Every spot in maro-light-tools/meter_test/config/artnet_mapping.json lives on
# ODE universe 0 or 1, so those two universes cover all spotlights/dimmers.
SPOT_UNIVERSES = (0, 1)

MARO_BASE = "http://192.168.178.25:8420"
MARO_ARTNET_TOGGLE = "/api/artnet/enabled"

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _make_artdmx(universe: int, data: bytes) -> bytes:
    header = b"Art-Net\x00"
    opcode = struct.pack("<H", 0x5000)
    proto = struct.pack(">H", 14)
    seq = b"\x00"
    physical = b"\x00"
    univ = struct.pack("<H", universe)
    length = struct.pack(">H", len(data))
    return header + opcode + proto + seq + physical + univ + length + data


def send_dmx(channels: list[int], universe: int, ip: str = ODE_IP) -> None:
    data = bytes(channels).ljust(CHANNELS_PER_UNIVERSE, b"\x00")
    _sock.sendto(_make_artdmx(universe, data), (ip, ARTNET_PORT))


def set_universes(value: int, universes: tuple[int, ...], ip: str = ODE_IP) -> None:
    payload = [value] * CHANNELS_PER_UNIVERSE
    for u in universes:
        send_dmx(payload, u, ip)


def maro_set_artnet(enabled: bool, base: str = MARO_BASE, timeout: float = 3.0) -> bool:
    """Toggle Maro's Art-Net output. Returns True on success, False otherwise."""
    url = base.rstrip("/") + MARO_ARTNET_TOGGLE
    body = json.dumps({"enabled": enabled}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        print(f"  Maro Art-Net -> {'enabled' if enabled else 'paused'} ({url})")
        return True
    except (urllib.error.URLError, OSError) as exc:
        print(f"  Maro not reachable ({url}): {exc} -- continuing without toggling")
        return False


def _ping(ip: str) -> bool:
    """Best-effort reachability probe via a UDP Art-Net 'all off' send.

    Art-Net is connectionless so this only fails on routing/socket errors, not
    on a silent controller. For a real ICMP check use the OS 'ping' command.
    """
    try:
        send_dmx([0] * CHANNELS_PER_UNIVERSE, 0, ip)
        print(f"  sent test packet to {ip}:{ARTNET_PORT} (no socket error)")
        return True
    except OSError as exc:
        print(f"  FAILED to send to {ip}: {exc}")
        return False


def cmd_check(_args: argparse.Namespace) -> int:
    print("[spots] reachability (Art-Net is UDP; use OS ping for ICMP):")
    ode_ok = _ping(ODE_IP)
    pix_ok = _ping(PIXELATOR_IP)
    print()
    print("  Tip: for a definitive check run:")
    print(f"    ping {ODE_IP}    # ODE MK3 (spots)")
    print(f"    ping {PIXELATOR_IP}    # Pixelator MK2 (strips)")
    return 0 if (ode_ok and pix_ok) else 1


def cmd_off(args: argparse.Namespace) -> int:
    print(f"[spots] blackout universes {list(SPOT_UNIVERSES)} on ODE {ODE_IP}")
    set_universes(0, SPOT_UNIVERSES)
    if not args.no_maro:
        maro_set_artnet(True, base=args.maro_base)
    print("[spots] off.")
    return 0


def cmd_on(args: argparse.Namespace) -> int:
    paused = False
    if not args.no_maro:
        paused = maro_set_artnet(False, base=args.maro_base)

    print(
        f"[spots] ON: all channels = {args.value} on universes "
        f"{list(SPOT_UNIVERSES)} (ODE {ODE_IP})"
    )
    if args.seconds:
        print(f"[spots] holding for {args.seconds:.0f}s, then auto-blackout")
    else:
        print("[spots] holding until Ctrl+C, then auto-blackout")

    deadline = time.time() + args.seconds if args.seconds else None
    try:
        while True:
            set_universes(args.value, SPOT_UNIVERSES)
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print("\n[spots] interrupted")
    finally:
        set_universes(0, SPOT_UNIVERSES)
        print(f"[spots] blackout universes {list(SPOT_UNIVERSES)}")
        if not args.no_maro and paused:
            maro_set_artnet(True, base=args.maro_base)
    print("[spots] done.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    # Shared options live on each subcommand so they go AFTER it,
    # e.g. ``spots.py off --no-maro`` / ``spots.py on --maro-base ...``.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--maro-base", default=MARO_BASE, help="Maro API base URL")
    common.add_argument(
        "--no-maro",
        action="store_true",
        help="do not pause/re-enable Maro Art-Net (e.g. Maro is down)",
    )

    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", parents=[common], help="probe ODE/Pixelator reachability")
    p_check.set_defaults(func=cmd_check)

    p_on = sub.add_parser("on", parents=[common], help="flood spot universes full-on and hold")
    p_on.add_argument("--value", type=int, default=255, help="DMX value per channel (0-255)")
    p_on.add_argument("--seconds", type=float, default=0.0, help="auto-blackout after N seconds (0 = until Ctrl+C)")
    p_on.add_argument("--refresh", type=float, default=2.0, help="re-send interval while holding (seconds)")
    p_on.set_defaults(func=cmd_on)

    p_off = sub.add_parser("off", parents=[common], help="blackout spot universes")
    p_off.set_defaults(func=cmd_off)

    args = ap.parse_args()
    try:
        return int(args.func(args))
    finally:
        _sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
