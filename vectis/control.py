"""A small always-on control listener so a server role (edge / DNS)
can trigger a fault on a *different* physical machine -- "stop backend
A" issued from the edge machine has to actually kill a process running
on the Backend A host, not on the edge machine itself. Reuses the same
ThreadingHTTPServer pattern as dashboard.py's aggregator, since that
exact pattern is already tested and known to work.

Trust model matches dashboard.py: unauthenticated, LAN-only, by
design -- this is internal operational tooling for a private network,
not a production control plane. Flagging that explicitly rather than
leaving it implicit.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

CONTROL_PORT = 9192


class _Handler(BaseHTTPRequestHandler):
    action_handler: Callable[[str], dict] = staticmethod(
        lambda action: {"ok": False, "error": "no handler registered"}
    )

    def log_message(self, *a) -> None:  # silence default access log
        pass

    def do_POST(self) -> None:
        if self.path != "/control":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            result = self.action_handler(body["action"])
        except Exception as exc:  # noqa: BLE001 -- report to caller, never crash the listener
            result = {"ok": False, "error": str(exc)}
        payload = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_listener(bind_ip: str, action_handler: Callable[[str], dict]) -> ThreadingHTTPServer:
    """action_handler(action_name) -> {"ok": bool, ...}; called for
    every /control POST this machine receives. Every role starts one
    of these, not just server roles -- a backend machine has to be
    able to receive the "stop yourself" command from wherever fault
    injection is actually running."""
    bound = type("_BoundHandler", (_Handler,), {"action_handler": staticmethod(action_handler)})
    httpd = ThreadingHTTPServer((bind_ip, CONTROL_PORT), bound)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def send(peer_ip: str, action: str) -> dict:
    body = json.dumps({"action": action}).encode()
    req = urllib.request.Request(
        f"http://{peer_ip}:{CONTROL_PORT}/control", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
