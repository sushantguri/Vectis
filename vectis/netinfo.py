"""Detect this machine's active LAN interface, IP, and MAC address.

Primary path shells out to macOS tools (route/ifconfig), since that's
the deployment target. A socket-based fallback covers machines where
those calls fail or return nothing usable -- and is what actually gets
exercised in non-macOS dev/test environments, including the one this
file was tested in.

[VERSION-DEPENDENT] The `route get default` / `ifconfig` parsing below
is written against macOS 13-15's known output format. There's no macOS
box available in this environment to verify it against -- treat the
macOS path as unverified until it's run on real hardware. The
detected value is always shown to the user for confirmation before
vectis acts on it (see cli.py), specifically because a laptop with
Wi-Fi + VPN + hotspot interfaces up at once can make "the active
interface" ambiguous.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import uuid
from dataclasses import dataclass


@dataclass
class NetInfo:
    interface: str
    ip: str
    mac: str


def is_valid_ipv4(value: str) -> bool | str:
    """questionary validator: return True, or an error string to display."""
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return "Enter a valid IPv4 address"


def _mac_from_uuid_getnode() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> ele) & 0xff:02x}" for ele in range(40, -1, -8))


def _fallback_socket() -> NetInfo:
    """Cross-platform: open a UDP socket 'connected' to a public IP (no
    packet is actually sent for a connectionless socket -- this only
    asks the kernel which local interface it would route through) to
    learn this machine's outbound IP, then resolve the MAC via
    uuid.getnode(). Correct when there's a single active NIC, which is
    the common case (one machine, one LAN link)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return NetInfo(interface="default", ip=ip, mac=_mac_from_uuid_getnode())


def _macos_route_default_iface() -> str | None:
    try:
        out = subprocess.run(
            ["route", "-n", "get", "default"], capture_output=True, text=True, timeout=3
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if m else None


def _macos_ifconfig(iface: str) -> NetInfo | None:
    try:
        out = subprocess.run(
            ["ifconfig", iface], capture_output=True, text=True, timeout=3
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    ip_m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    mac_m = re.search(r"ether ([0-9a-f:]{17})", out)
    if not ip_m or not mac_m:
        return None
    return NetInfo(interface=iface, ip=ip_m.group(1), mac=mac_m.group(1))


def detect() -> NetInfo:
    iface = _macos_route_default_iface()
    if iface:
        info = _macos_ifconfig(iface)
        if info:
            return info
    return _fallback_socket()
