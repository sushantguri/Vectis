"""Always-on local supervisor.

Independent of *why* a locally-owned service went down -- crashed on
its own, or was deliberately fault-injected -- this notices and
restarts it with backoff. fault.py only ever causes a
failure; it has no retry logic of its own, because this is what makes
"auto retry to activate it" true for a real crash and an injected one
identically, rather than needing fault injection to special-case its
own cleanup.
"""
from __future__ import annotations

import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from . import state

MAX_BACKOFF_S = 30.0


@dataclass
class WatchedService:
    name: str
    is_up: Callable[[], bool]
    restart: Callable[[], None]
    check_interval_s: float = 3.0


def _log(name: str, message: str) -> None:
    state.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = state.LOG_DIR / "supervisor.log"
    with open(path, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} [{name}] {message}\n")


def _watch(svc: WatchedService, stop: threading.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            up = svc.is_up()
        except Exception as exc:  # noqa: BLE001 -- a bad health check must not kill this watcher
            up = False
            _log(svc.name, f"health check raised: {exc}")
        if up:
            backoff = 1.0
        else:
            _log(svc.name, f"down -- restarting (next backoff {backoff:.0f}s if this fails again)")
            try:
                svc.restart()
            except Exception as exc:  # noqa: BLE001
                _log(svc.name, f"restart raised: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
        time.sleep(svc.check_interval_s)


def start(services: list[WatchedService]) -> list[threading.Event]:
    """One thread per watched service -- so one flapping service can't
    delay or block the health check of another."""
    stops = []
    for svc in services:
        stop = threading.Event()
        threading.Thread(target=_watch, args=(svc, stop), daemon=True).start()
        stops.append(stop)
    return stops


def http_is_up(url: str, timeout: float = 2.0) -> Callable[[], bool]:
    def check() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status == 200
        except OSError:
            return False
    return check


def process_is_up(process_name: str) -> Callable[[], bool]:
    def check() -> bool:
        return subprocess.run(["pgrep", "-x", process_name], capture_output=True).returncode == 0
    return check


def dns_is_up(domain: str) -> Callable[[], bool]:
    def check() -> bool:
        result = subprocess.run(
            ["dig", "@127.0.0.1", f"app.{domain}", "+short"],
            capture_output=True, text=True, timeout=3,
        )
        return bool(result.stdout.strip())
    return check


def brew_service_restart(formula: str) -> Callable[[], None]:
    def restart() -> None:
        subprocess.run(["brew", "services", "restart", formula], check=False)
    return restart
