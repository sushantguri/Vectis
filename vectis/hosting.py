"""Content hosting on the DNS machine.

Available on the DNS role, whether it's running standalone or combined
with edge (dns_edge). Picking a port that doesn't collide with dnsmasq
or nginx is the operator's job when the combo is in use.

Binds 0.0.0.0, same as every other service in this project -- so it's
reachable from Backend A and Backend B (and everyone else) directly
over the LAN, no routing or proxying through anything else required.
"""
from __future__ import annotations

import http.server
import os
import shlex
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from . import state, ui

STATIC_PORT = 8080
SERVICE_LOG = state.LOG_DIR / "hosted_service.log"


def host_static(folder: str, port: int = STATIC_PORT) -> http.server.ThreadingHTTPServer:
    """Always binds 0.0.0.0, same as every other service in this
    project -- so both 127.0.0.1 (what check_reachable checks) and
    this machine's real LAN IP (what other team machines use) reach
    the same server. No bind_ip parameter: there's nothing meaningful
    left for a caller to choose once the bind is always 0.0.0.0."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")

    def handler_factory(*a, **kw):
        return http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **kw)

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler_factory)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state.update(services={**state.load()["services"], "hosted_static": {"port": port, "root": str(root)}})
    return httpd


def host_monorepo_service(cwd: str, command: str, port: int) -> int:
    """Spawns `command` (split and exec'd directly, not through a
    shell) with cwd set to the given directory, and PORT set in its
    environment -- the standard convention most Node/Python dev
    servers already respect (`process.env.PORT`, `os.environ["PORT"]`)
    -- so the port vectis tracks for the reachability check is the
    same port the service actually binds, not two independent numbers
    that happen to be typed into two different prompts. If the
    monorepo's app doesn't read $PORT, its own start script/config
    still decides the real port -- vectis can't force an arbitrary
    command to honor an env var it doesn't check, so the reachability
    check is only as good as that assumption holding. Same
    subprocess + logged-stdout + state-tracked-PID pattern as
    backend_role.py, reused rather than reinvented."""
    workdir = Path(cwd).expanduser().resolve()
    if not workdir.is_dir():
        raise ValueError(f"{workdir} is not a directory")
    SERVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(SERVICE_LOG, "a")
    proc = subprocess.Popen(
        shlex.split(command), cwd=str(workdir),
        env={**os.environ, "PORT": str(port)},
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    data = state.load()
    data["services"]["hosted_service"] = {
        "pid": proc.pid, "started": time.time(), "port": port, "cwd": str(workdir), "command": command,
    }
    state.save(data)
    return proc.pid


def check_reachable(port: int, path: str = "/", timeout: float = 3.0) -> bool:
    with ui.spinner(f"Checking http://127.0.0.1:{port}{path} ..."):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
                return r.status < 500
        except OSError:
            return False
