"""Combo role: both backend instances on one machine, for 2-machine
teams (paired with DnsEdgeRole on the other machine)."""
from __future__ import annotations

from .backend_role import BackendRole
from .base import Role, SetupResult


class BackendsRole(Role):
    name = "backends"

    def __init__(self) -> None:
        self._a = BackendRole("A")
        self._b = BackendRole("B")

    def collect_peers(self) -> dict[str, str]:
        return {}

    def configure(self, peers: dict[str, str]) -> None:
        self._a.configure(peers)
        self._b.configure(peers)

    def smoke_test(self) -> SetupResult:
        # BackendRole always uses the same check names regardless of
        # which backend it is ("api_status_reachable", ...), so a
        # plain {**a.checks, **b.checks} merge silently collides --
        # b's values overwrite a's, and a failing backend A can read
        # as all-green. Namespace by backend to keep both visible.
        a = self._a.smoke_test()
        b = self._b.smoke_test()
        checks = {f"a_{k}": v for k, v in a.checks.items()}
        checks.update({f"b_{k}": v for k, v in b.checks.items()})
        return SetupResult(ok=a.ok and b.ok, checks=checks, messages=[])
