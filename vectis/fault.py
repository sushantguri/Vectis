"""Controlled failure injection, gated to server roles.

Menu covers the standard failure scenarios for this topology -- DNS
down, a backend down, both backends down, a stale DNS record -- not a
generic "break something" list. Every action either runs locally (this
machine owns the target service) or is sent as a control command to
the peer machine that does (see control.py) -- "stop backend A" issued
from the edge machine has to actually reach the Backend A machine.

This module only ever causes a failure and logs it. Recovery is the
always-on supervisor (supervisor.py) noticing the service is down and
restarting it -- there is no retry logic here, deliberately, so a real
crash and a fault-injected one are recovered identically.
"""
from __future__ import annotations

import os
import signal
import time

import questionary

from . import control, state
from .roles.dns_role import DnsRole
from .ui import console

FAULT_LOG = state.LOG_DIR / "fault.log"

ACTIONS = [
    "Stop Backend A",
    "Stop Backend B",
    "Stop both backends",
    "Stop DNS (dnsmasq)",
    "Point app.<domain> at a wrong IP",
    "Restore DNS record",
    "Back",
]


def _log_event(message: str) -> None:
    FAULT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FAULT_LOG, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {message}\n")


def _peer_for(role_key: str) -> str | None:
    """role_key: 'backend_a' or 'backend_b'. Returns the peer IP that
    owns it, or None if it's local (this machine is the backends combo,
    or -- for a solo dev/test run -- the target simply isn't remote)."""
    data = state.load()
    if data["role"] == state.ROLE_BACKENDS:
        return None
    return data["peers"].get(role_key)


def _stop_backend(letter: str) -> None:
    role_key = f"backend_{letter.lower()}"
    peer_ip = _peer_for(role_key)
    if peer_ip is None:
        data = state.load()
        svc = data["services"].get(role_key)
        if not svc:
            console.print(f"[yellow]No local record of {role_key} running.[/yellow]")
            return
        os.kill(svc["pid"], signal.SIGKILL)
        _log_event(f"backend {letter} killed locally (fault-injected)")
        console.print(f"[bold red]Backend {letter} killed locally.[/bold red] "
                       f"Supervisor should restart it within a few seconds.")
    else:
        result = control.send(peer_ip, f"stop_backend_{letter.lower()}")
        _log_event(f"backend {letter} fault command sent to {peer_ip}: {result}")
        console.print(f"[bold red]Fault command sent to {peer_ip}.[/bold red] Result: {result}")


def _stop_dns() -> None:
    import subprocess
    subprocess.run(["brew", "services", "stop", "dnsmasq"], check=False)
    _log_event("dnsmasq stopped (fault-injected)")
    console.print("[bold red]dnsmasq stopped.[/bold red] Supervisor should restart it within a few seconds.")


def _poison_dns() -> None:
    # Reuses DnsRole.configure() itself rather than reimplementing the
    # render/write/restart sequence -- same code path as normal setup,
    # just with a deliberately wrong edge IP.
    DnsRole().configure({"edge": "203.0.113.1"})  # RFC 5737 TEST-NET-3, guaranteed unreachable
    _log_event("app.<domain> pointed at 203.0.113.1 (fault-injected)")
    console.print("[bold red]DNS record poisoned.[/bold red]")


def _restore_dns() -> None:
    data = state.load()
    edge_ip = data["peers"].get("edge")
    if not edge_ip:
        console.print("[yellow]No known-good edge IP on record -- re-run setup instead.[/yellow]")
        return
    DnsRole().configure({"edge": edge_ip})
    _log_event(f"app.<domain> restored to {edge_ip}")
    console.print("[green]DNS record restored.[/green]")


def run() -> None:
    data = state.load()
    if data["role"] not in state.SERVER_ROLES:
        console.print(
            f"[bold red]Fault injection requires a server role[/bold red] "
            f"(dns / edge / dns_edge); this machine is '{data['role']}'."
        )
        return
    while True:
        action = questionary.select("Inject which fault?", choices=ACTIONS).ask()
        if action is None or action == "Back":
            return
        try:
            if action == "Stop Backend A":
                _stop_backend("A")
            elif action == "Stop Backend B":
                _stop_backend("B")
            elif action == "Stop both backends":
                _stop_backend("A")
                _stop_backend("B")
            elif action == "Stop DNS (dnsmasq)":
                _stop_dns()
            elif action == "Point app.<domain> at a wrong IP":
                _poison_dns()
            elif action == "Restore DNS record":
                _restore_dns()
        except Exception as exc:  # noqa: BLE001 -- one fault action failing must not kill the menu
            console.print(f"[bold red]Fault action failed:[/bold red] {exc}")
        console.print()
