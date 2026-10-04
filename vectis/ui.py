"""Shared terminal output: one Console instance for the whole app (so
output from different modules never fights over the terminal), plus a
spinner for anything that blocks for more than a moment -- brew
installs, cert generation, service restarts, network smoke-test calls.

Colorful by default (rich's "dots" spinner, cyan text). Set
VECTIS_PLAIN=1 to fall back to a plain rotating-line spinner with no
color, for terminals that don't render Unicode/ANSI well.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from rich.console import Console

console = Console()

_PLAIN = os.environ.get("VECTIS_PLAIN") == "1"


@contextmanager
def spinner(message: str):
    style = "white" if _PLAIN else "bold cyan"
    kind = "line" if _PLAIN else "dots"
    with console.status(f"[{style}]{message}[/{style}]", spinner=kind):
        yield
