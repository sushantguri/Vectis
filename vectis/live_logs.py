"""Live multi-service log tailing.

Each configured log source gets its own follower thread, feeding a
single thread-safe queue the main thread renders from -- so output
from different services never interleaves mid-line. A source whose log
file doesn't exist yet is reported as "waiting", not a crash, and is
picked up automatically once the file appears. A source that dies
mid-stream (bad permissions, binary garbage) reports once and stops --
it does not take any other source's thread down with it, same
isolation principle as everywhere else in this codebase.
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from . import state
from .ui import console

SOURCE_COLORS = {
    "dnsmasq": "cyan",
    "nginx-access": "magenta",
    "nginx-error": "red",
    "backend_a": "green",
    "backend_b": "yellow",
    "fault-events": "bold red",
    "supervisor": "blue",
    "hosted-service": "bright_green",
}

# [VERSION-DEPENDENT] Apple Silicon Homebrew nginx log directory;
# Intel Macs use /usr/local/var/log/nginx/. Unverified in this
# environment -- there is no macOS/nginx install to test against here.
NGINX_LOG_DIR = Path("/opt/homebrew/var/log/nginx")

_ALWAYS_ON = {
    "fault-events": state.LOG_DIR / "fault.log",
    "supervisor": state.LOG_DIR / "supervisor.log",
}

SOURCES_BY_ROLE: dict[str, dict[str, Path]] = {
    state.ROLE_DNS: {
        "dnsmasq": state.LOG_DIR / "dnsmasq.log",
        "hosted-service": state.LOG_DIR / "hosted_service.log",
        **_ALWAYS_ON,
    },
    state.ROLE_EDGE: {
        "nginx-access": NGINX_LOG_DIR / "access.log",
        "nginx-error": NGINX_LOG_DIR / "error.log",
        **_ALWAYS_ON,
    },
    state.ROLE_BACKEND_A: {"backend_a": state.LOG_DIR / "backend_a.log", **_ALWAYS_ON},
    state.ROLE_BACKEND_B: {"backend_b": state.LOG_DIR / "backend_b.log", **_ALWAYS_ON},
    state.ROLE_DNS_EDGE: {
        "dnsmasq": state.LOG_DIR / "dnsmasq.log",
        "nginx-access": NGINX_LOG_DIR / "access.log",
        "nginx-error": NGINX_LOG_DIR / "error.log",
        **_ALWAYS_ON,
    },
    state.ROLE_BACKENDS: {
        "backend_a": state.LOG_DIR / "backend_a.log",
        "backend_b": state.LOG_DIR / "backend_b.log",
        **_ALWAYS_ON,
    },
}


def _follow(name: str, path: Path, out: "queue.Queue[tuple[str, str]]", stop: threading.Event) -> None:
    """tail -f semantics: seeks to end-of-file on first open, so only
    lines written *after* this follower first sees the file are shown
    -- pre-existing content is never replayed. For a file that doesn't
    exist yet, this means the very first line written at the instant
    the file is created can race the next poll tick and be missed;
    every subsequent line is not. That's an acceptable trade for a
    live operational view (you want what's happening now, not a
    replay), and is called out here since it's easy to mistake for a bug."""
    reported_waiting = False
    fh = None
    try:
        while not stop.is_set():
            if fh is None:
                if not path.exists():
                    if not reported_waiting:
                        out.put((name, f"[waiting for {path.name} to appear]"))
                        reported_waiting = True
                    time.sleep(1)
                    continue
                fh = open(path, "r", errors="replace")
                fh.seek(0, 2)  # start at end -- only new lines from here on
            line = fh.readline()
            if line:
                out.put((name, line.rstrip("\n")))
            else:
                time.sleep(0.3)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see module docstring
        out.put((name, f"[log follower stopped: {exc}]"))


def _render(name: str, line: str, ip: str | None) -> None:
    """Vercel/Render-style trailing line: dim timestamp, this
    machine's IP (so a screenshot or a shared terminal is
    unambiguous about whose log it is), colored source badge, then
    the line itself. Team-aggregated lines already carry their own
    IP column (see team_logs.py), so `ip` is None there -- adding a
    second one would be redundant."""
    ts = time.strftime("%H:%M:%S")
    color = SOURCE_COLORS.get(name, "white")
    ip_part = f"[bold]{ip:>15}[/bold]  " if ip else ""
    console.print(f"[dim]{ts}[/dim]  {ip_part}[{color}]{name:<14}[/{color}]| {line}")


def _tail(sources: dict[str, Path], ip: str | None) -> None:
    if not sources:
        console.print("No known log sources.")
        return
    out: "queue.Queue[tuple[str, str]]" = queue.Queue()
    stop = threading.Event()
    for name, path in sources.items():
        threading.Thread(target=_follow, args=(name, path, out, stop), daemon=True).start()

    console.print(f"[bold]Tailing:[/bold] {', '.join(sources)}  [dim](Ctrl+C to stop)[/dim]\n")
    try:
        while True:
            name, line = out.get()
            _render(name, line, ip)
    except KeyboardInterrupt:
        stop.set()
        console.print("\n[dim]Stopped.[/dim]")


def run(role: str) -> None:
    sources = SOURCES_BY_ROLE.get(role, {})
    if not sources:
        console.print(f"No known log sources for role '{role}'.")
        return
    self_ip = state.load()["self"]["ip"]
    _tail(sources, ip=self_ip)


def run_team() -> None:
    """Tails the shared cross-machine log every configured machine
    forwards into (see team_logs.py) -- one trailing view across the
    whole deployment instead of going machine to machine. Each line
    already carries its own source IP and timestamp, written by the
    aggregator, so this is the same follower pointed at one file."""
    from . import team_logs
    _tail({"team": team_logs.TEAM_LOG}, ip=None)
