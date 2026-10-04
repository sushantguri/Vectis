"""Makes this machine's own browser/OS actually able to resolve the
team domain, via macOS's /etc/resolver mechanism -- the same approach
tools like Laravel Valet use for local dev domains (.test, etc).

Writing /etc/resolver/<domain> with a single `nameserver <dns-ip>`
line tells macOS: for any lookup ending in <domain>, ask this
resolver; every other lookup (google.com, anything else) is
completely unaffected. This is deliberately NOT a whole-system
resolver change via `networksetup -setdnsservers` -- that would route
*all* DNS through the vectis DNS machine, and would first need parsing
`networksetup -listnetworkserviceorder` to even find the right network
service name. /etc/resolver is more precise and is the established
pattern for exactly this case.

Without this, "all four roles are configured correctly" still isn't
enough to browse the site by name -- DNS records existing on the
vectis DNS machine doesn't make any *other* machine use them.

[VERSION-DEPENDENT] /etc/resolver is a documented, stable macOS
mechanism, but there's no macOS /etc to write to in this environment,
so only the write-and-fallback logic itself is tested here, via
VECTIS_RESOLVER_DIR pointed at a writable temp directory -- not the
real /etc/resolver path or an actual sudo prompt.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

RESOLVER_DIR = Path(os.environ.get("VECTIS_RESOLVER_DIR", "/etc/resolver"))


def resolver_path(domain: str) -> Path:
    return RESOLVER_DIR / domain


def is_enabled(domain: str) -> bool:
    return resolver_path(domain).exists()


def enable(domain: str, dns_ip: str) -> tuple[bool, str]:
    """Tries a direct write first (works when RESOLVER_DIR is already
    writable, e.g. in tests, or if vectis is already running as root);
    falls back to sudo for the real /etc/resolver case, which will
    prompt interactively the first time -- expected and normal, the
    same way Valet and similar tools require it."""
    path = resolver_path(domain)
    content = f"nameserver {dns_ip}\n"
    try:
        RESOLVER_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return True, f"Browser lookups for *.{domain} now resolve via {dns_ip}."
    except PermissionError:
        pass
    subprocess.run(["sudo", "-v"], check=True)
    script = f'mkdir -p {RESOLVER_DIR} && printf "nameserver {dns_ip}\\n" > {path}'
    result = subprocess.run(["sudo", "sh", "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or "sudo command failed"
    return True, f"Browser lookups for *.{domain} now resolve via {dns_ip}."


def disable(domain: str) -> tuple[bool, str]:
    path = resolver_path(domain)
    if not path.exists():
        return True, f"No resolver entry for {domain} to remove."
    try:
        path.unlink()
        return True, f"Removed {path}."
    except PermissionError:
        pass
    subprocess.run(["sudo", "-v"], check=True)
    result = subprocess.run(["sudo", "rm", "-f", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or "sudo command failed"
    return True, f"Removed {path}."
