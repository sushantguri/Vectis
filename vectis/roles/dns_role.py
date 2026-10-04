"""DNS role -- also used as the DNS half of the dns_edge combo.

Needs to know the edge machine's IP to point app.<domain> and
api.<domain> at it. Needs nothing else -- dnsmasq is authoritative for
this LAN, it doesn't chain to another resolver.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import questionary
from jinja2 import Environment, FileSystemLoader

from .. import state, ui
from ..netinfo import is_valid_ipv4
from .base import Role, SetupResult

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# [VERSION-DEPENDENT] Apple Silicon Homebrew prefix. Intel Macs use
# /usr/local/etc/dnsmasq.conf -- detect via `brew --prefix` at
# configure time rather than hardcoding, if this gets tested on Intel.
DNSMASQ_CONF = Path("/opt/homebrew/etc/dnsmasq.conf")


class DnsRole(Role):
    name = "dns"

    def collect_peers(self) -> dict[str, str]:
        edge_ip = questionary.text(
            "Load Balancer / Edge machine's IP address (app.<domain> will point here):",
            validate=is_valid_ipv4,
        ).ask()
        return {"edge": edge_ip}

    def configure(self, peers: dict[str, str]) -> None:
        data = state.load()
        domain = data["domain"]
        env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        rendered = env.get_template("dnsmasq.conf.j2").render(
            domain=domain,
            edge_ip=peers.get("edge"),
            # Set when `vectis host` has a static site or monorepo
            # service running on this machine -- gives it a proper
            # name (hosted.<domain>) instead of leaving it reachable
            # only by raw IP. Re-rendering this way always re-includes
            # whatever edge_ip is already current, so hosting a service
            # later never clobbers the app.<domain> record.
            hosted_ip=peers.get("hosted"),
            log_path=str(state.LOG_DIR / "dnsmasq.log"),
        )
        DNSMASQ_CONF.parent.mkdir(parents=True, exist_ok=True)
        DNSMASQ_CONF.write_text(rendered)
        subprocess.run(["sudo", "-v"], check=True)
        with ui.spinner("Restarting dnsmasq (needs sudo -- port 53 is privileged)..."):
            # Plain `brew services restart dnsmasq` reports "Successfully
            # started" even when it can't actually bind port 53 as a
            # non-root LaunchAgent -- Homebrew's own dnsmasq caveats say
            # so directly ("dnsmasq must be run as root to start at
            # system startup"). The command succeeding is not evidence
            # the service is actually listening; sudo is what makes
            # that true rather than just documented as true.
            #
            # Stop the non-root version first (plain `brew services
            # stop`, no sudo): if dnsmasq was ever started without sudo
            # before, that registered a per-user LaunchAgent that can't
            # bind port 53 but stays loaded, retrying -- restarting only
            # the root-owned LaunchDaemon on top of that can leave both
            # fighting over the same port instead of cleanly replacing
            # one with the other.
            # Bypass `brew services restart` completely as it hangs indefinitely via sudo.
            # Instead, copy the plist and use launchctl directly.
            plist_src = "/opt/homebrew/opt/dnsmasq/homebrew.mxcl.dnsmasq.plist"
            plist_dest = "/Library/LaunchDaemons/homebrew.mxcl.dnsmasq.plist"
            if Path(plist_src).exists():
                subprocess.run(["sudo", "cp", plist_src, plist_dest], check=True)
                subprocess.run(["sudo", "launchctl", "unload", "-w", plist_dest], check=False)
                subprocess.run(["sudo", "launchctl", "load", "-w", plist_dest], check=True)
            else:
                ui.console.print(f"[bold red]Cannot find dnsmasq plist at {plist_src}[/bold red]")

    def smoke_test(self) -> SetupResult:
        data = state.load()
        domain = data["domain"]
        checks: dict[str, bool] = {}
        with ui.spinner(f"Checking DNS resolution for app.{domain}..."):
            try:
                result = subprocess.run(
                    ["dig", "@127.0.0.1", f"app.{domain}", "+short"],
                    capture_output=True, text=True, timeout=5,
                )
                checks["dns_resolves_app_domain"] = bool(result.stdout.strip())
            except subprocess.TimeoutExpired:
                # A timeout here (as opposed to a fast empty/refused
                # response) almost always means nothing is actually
                # listening on port 53 -- usually because dnsmasq was
                # started without root and silently failed to bind the
                # privileged port despite brew reporting success.
                checks["dns_resolves_app_domain"] = False
        return SetupResult(ok=all(checks.values()), checks=checks, messages=[])
