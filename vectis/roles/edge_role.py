"""Edge role -- reverse proxy, TLS termination, load balancing.

Needs both backends' IPs to build the nginx upstream block. Also owns
certificate generation and LAN publishing (see certs.py for why cert
distribution is a plain-HTTP pull rather than Bluetooth or QUIC).
"""
from __future__ import annotations

import re
import subprocess
import urllib.request
from pathlib import Path

import questionary
from jinja2 import Environment, FileSystemLoader

from .. import certs, state, ui
from ..netinfo import is_valid_ipv4
from .base import Role, SetupResult

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# [VERSION-DEPENDENT] Apple Silicon Homebrew nginx server-includes path.
NGINX_CONF = Path("/opt/homebrew/etc/nginx/servers/vectis.conf")


class EdgeRole(Role):
    name = "edge"

    def collect_peers(self) -> dict[str, str]:
        a = questionary.text("Backend A machine's IP:", validate=is_valid_ipv4).ask()
        b = questionary.text("Backend B machine's IP:", validate=is_valid_ipv4).ask()
        return {"backend_a": a, "backend_b": b}

    def configure(self, peers: dict[str, str]) -> None:
        data = state.load()
        domain = data["domain"]
        slug = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")

        # Always generates a fresh cert+key pair here, even if this
        # machine already has a *pulled* copy of some earlier cert at
        # certs.pulled_path(domain) -- that pulled copy is only ever
        # the public .crt (see certs.py: the private key never leaves
        # the machine that generated it). A pulled .crt has no matching
        # key, so it cannot be used to configure this machine as the
        # TLS-terminating edge; only the machine that generates its own
        # key pair can serve as edge for that pair. Moving the edge
        # role to a different machine means everyone re-pulls and
        # re-trusts the new cert -- there's no way around that.
        crt, key = certs.generate(domain)
        server = certs.publish(crt, bind_ip=data["self"]["ip"])
        print(f"\nCert published. On every OTHER machine, run:\n  {server.pull_command}\n"
              f"(the private key stays on this machine -- only the .crt is served)")
        questionary.text("Press enter once every machine has pulled it...").ask()
        server.stop()

        env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        rendered = env.get_template("nginx.conf.j2").render(
            domain=domain, domain_slug=slug,
            backend_a_ip=peers["backend_a"], backend_b_ip=peers["backend_b"],
            https_port=443, cert_path=str(crt), key_path=str(key),
        )
        NGINX_CONF.parent.mkdir(parents=True, exist_ok=True)
        NGINX_CONF.write_text(rendered)
        with ui.spinner("Testing and restarting nginx (restart needs sudo -- port 443 is privileged)..."):
            # Config test doesn't bind anything, so it doesn't need
            # root. The restart does -- same reasoning as dnsmasq's
            # restart in dns_role.py: port 443 is privileged, and a
            # non-root `brew services restart` can report success
            # without nginx actually being able to bind it. Same
            # stop-non-root-first cleanup too, for the same reason.
            subprocess.run(["nginx", "-t"], check=True)
            subprocess.run(["brew", "services", "stop", "nginx"], check=False)
            subprocess.run(["sudo", "brew", "services", "restart", "nginx"], check=True)

    def smoke_test(self) -> SetupResult:
        data = state.load()
        checks: dict[str, bool] = {}
        with ui.spinner(f"Checking https://{data['domain']}/api/status ..."):
            try:
                with urllib.request.urlopen(f"https://{data['domain']}/api/status", timeout=5) as r:
                    checks["https_reachable"] = r.status == 200
            except OSError:
                checks["https_reachable"] = False
        return SetupResult(ok=all(checks.values()), checks=checks, messages=[])
