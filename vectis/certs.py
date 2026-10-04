"""Certificate generation and LAN distribution.

Generation: mkcert if available (issues a cert trusted by mkcert's own
local CA, once that CA is installed on a client machine), falling back
to a plain OpenSSL self-signed cert if mkcert isn't present.

Distribution: the private key never leaves the edge machine. The
*public* cert is not sensitive, so it's served over plain HTTP on the
LAN every team machine is already required to share -- not Bluetooth,
not QUIC. QUIC is built on TLS 1.3, so using it to bootstrap trust for
this TLS cert is circular: you'd need an already-trusted channel to do
the QUIC handshake itself. macOS Bluetooth file transfer has no stable
CLI/automation surface -- doing it reliably from a script means
depending on undocumented OBEX/AirDrop internals for a ~2KB file that
plain HTTP already moves correctly.

Trusting the cert once it's downloaded is still a local, per-machine
step (trust_locally() below) -- that part cannot be done remotely
regardless of transport, since it needs the target machine's own
keychain access.
"""
from __future__ import annotations

import http.server
import shutil
import subprocess
import threading
from pathlib import Path

from . import state, ui

CERT_DIR = state.STATE_DIR / "certs"

# Canonical local landing spot for a cert *pulled* from another
# machine (as opposed to CERT_DIR, which is where the edge machine
# generates its own). Every other vectis command that needs to know
# "does this machine already have the cert" checks here -- fixed path,
# not wherever curl happened to be run from -- see is_pulled()/
# is_trusted() below and pull_command in CertServer.
PULLED_DIR = state.STATE_DIR / "certs" / "pulled"


def pulled_path(domain: str) -> Path:
    return PULLED_DIR / f"{domain}.crt"


def is_pulled(domain: str) -> bool:
    return pulled_path(domain).exists()


def is_trusted(domain: str) -> bool:
    """Best-effort: asks the keychain directly whether this exact cert
    is already trusted, rather than just checking the file exists.
    [VERSION-DEPENDENT] `security verify-cert` output format is
    unverified in this environment -- if this misreports on real
    hardware, `is_pulled()` is the fallback signal."""
    path = pulled_path(domain)
    if not path.exists():
        return False
    result = subprocess.run(
        ["security", "verify-cert", "-c", str(path)], capture_output=True, text=True
    )
    return result.returncode == 0


def generate(domain: str) -> tuple[Path, Path]:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    crt = CERT_DIR / f"{domain}.crt"
    key = CERT_DIR / f"{domain}.key"
    with ui.spinner(f"Generating certificate for {domain}..."):
        if shutil.which("mkcert"):
            subprocess.run(
                ["mkcert", "-cert-file", str(crt), "-key-file", str(key), domain, f"api.{domain}"],
                check=True,
            )
        else:
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "825",
                    "-keyout", str(key), "-out", str(crt),
                    "-subj", f"/CN={domain}",
                    "-addext", f"subjectAltName=DNS:{domain},DNS:api.{domain}",
                ],
                check=True,
            )
    return crt, key


class CertServer:
    def __init__(self, httpd: http.server.ThreadingHTTPServer, thread: threading.Thread,
                 ip: str, port: int, filename: str):
        self.httpd = httpd
        self.thread = thread
        # Printed as a literal $HOME reference, NOT this machine's own
        # resolved Path.home() -- pulled_path() would bake in the edge
        # user's own username (e.g. /Users/alice/.config/vectis/...)
        # into a command meant for a *different* person to run
        # verbatim, which then fails on their machine (wrong user,
        # likely no such path, no permission). $HOME is resolved by
        # each recipient's own shell when they actually run it, so it's
        # always correct for whoever that is -- and still lines up with
        # what pulled_path()/is_pulled() compute locally on their
        # machine, since both resolve to the same default whenever
        # VECTIS_HOME isn't overridden.
        self.pull_command = (
            f'curl --create-dirs -o "$HOME/.config/vectis/certs/pulled/{filename}" '
            f"http://{ip}:{port}/{filename}"
        )

    def stop(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)


def publish(crt_path: Path, bind_ip: str, port: int = 9190) -> CertServer:
    """Serve only crt_path's directory over plain HTTP so other team
    machines can pull the .crt with curl. The .key never lives in this
    directory's sibling path that's served -- callers must keep the
    key elsewhere or this would happily serve it too; see generate(),
    which writes both into CERT_DIR, so this only ever runs against a
    scratch copy containing solely the .crt."""
    serve_dir = state.STATE_DIR / "cert_publish"
    serve_dir.mkdir(parents=True, exist_ok=True)
    published = serve_dir / crt_path.name
    published.write_bytes(crt_path.read_bytes())

    def handler_factory(*a, **kw):
        return http.server.SimpleHTTPRequestHandler(*a, directory=str(serve_dir), **kw)

    httpd = http.server.ThreadingHTTPServer((bind_ip, port), handler_factory)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return CertServer(httpd, thread, bind_ip, port, crt_path.name)


def trust_locally(crt_path: Path) -> bool:
    """Install crt_path into this machine's login keychain. Must run on
    each client Mac individually -- see module docstring.
    [VERSION-DEPENDENT] `security add-trusted-cert` syntax is stable
    across recent macOS releases but untested in this environment."""
    with ui.spinner("Adding certificate to login keychain..."):
        result = subprocess.run(
            [
                "security", "add-trusted-cert", "-d", "-r", "trustRoot",
                "-k", str(Path.home() / "Library" / "Keychains" / "login.keychain-db"),
                str(crt_path),
            ],
            capture_output=True, text=True,
        )
    return result.returncode == 0


def trust(domain: str) -> tuple[bool, str]:
    """Convenience wrapper for the `vectis trust-cert` command: looks
    up the cert at its canonical pulled path (not a path the caller
    has to know or supply) and trusts it. Returns (ok, message)."""
    path = pulled_path(domain)
    if not path.exists():
        return False, f"No cert found at {path}. Pull it first (the edge machine prints the exact command)."
    if is_trusted(domain):
        return True, f"{domain} is already trusted."
    ok = trust_locally(path)
    return ok, (f"{domain} is now trusted." if ok else "security add-trusted-cert failed -- see stderr above.")
