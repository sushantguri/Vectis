"""Common interface every role implements. Roles never import or call
each other's Python objects except by explicit composition (see
combo_dns_edge.py, combo_backends.py) -- coordination between
independently-running machines goes through state.py, not this class.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SetupResult:
    ok: bool
    checks: dict[str, bool]
    messages: list[str]


class Role(ABC):
    name: str

    @abstractmethod
    def collect_peers(self) -> dict[str, str]:
        """Prompt for whatever peer IPs this role needs. Returns a dict
        merged into state['peers']. Return {} if none are needed."""

    @abstractmethod
    def configure(self, peers: dict[str, str]) -> None:
        """Write config files / start the subprocess and (re)start
        services. Must be safe to call again on re-run."""

    @abstractmethod
    def smoke_test(self) -> SetupResult:
        """Run post-configure assertions. Must never mutate config."""
