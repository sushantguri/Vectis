"""Preflight dependency check: verifies (and installs, if missing) the
Homebrew formulae each role needs before touching any config.

This is deliberately kept separate from each role's own smoke_test().
"brew formula is installed" and "the resulting DNS record actually
resolves" are different claims -- collapsing them into one "all tests
passed" message hides exactly the failure class that matters most
when something actually breaks.

[VERSION-DEPENDENT] Assumes Homebrew is already installed and `brew`
is on PATH -- vectis does not bootstrap Homebrew itself. Formula names
are current as of early-2026 homebrew-core; if one comes back not
found, check `brew search <name>` for a rename.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from . import ui

REQUIRED_BY_ROLE: dict[str, list[str]] = {
    "dns": ["dnsmasq"],
    "edge": ["nginx", "mkcert"],
    "backend_a": [],   # backend app runs under vectis's own venv Python
    "backend_b": [],
    "dns_edge": ["dnsmasq", "nginx", "mkcert"],
    "backends": [],
}


@dataclass
class CheckResult:
    formula: str
    installed: bool
    action: str  # "already-installed" | "installed-now" | "install-failed"


def _brew_present() -> bool:
    return shutil.which("brew") is not None


def _formula_installed(formula: str) -> bool:
    result = subprocess.run(["brew", "list", "--formula", formula], capture_output=True, text=True)
    return result.returncode == 0


def _brew_install(formula: str) -> bool:
    result = subprocess.run(["brew", "install", formula], capture_output=True, text=True)
    return result.returncode == 0


def run(role: str) -> list[CheckResult]:
    formulae = REQUIRED_BY_ROLE.get(role, [])
    if not formulae:
        return []
    if not _brew_present():
        raise RuntimeError(
            "Homebrew not found on PATH. Install it first: https://brew.sh -- "
            "vectis does not bootstrap Homebrew itself."
        )
    results: list[CheckResult] = []
    for formula in formulae:
        if _formula_installed(formula):
            results.append(CheckResult(formula, True, "already-installed"))
            continue
        with ui.spinner(f"Installing {formula}..."):
            ok = _brew_install(formula)
        results.append(CheckResult(formula, ok, "installed-now" if ok else "install-failed"))
    return results


def all_ok(results: list[CheckResult]) -> bool:
    return all(r.installed for r in results)
