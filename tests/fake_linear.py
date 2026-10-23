"""A stand-in for LinearClient that records calls, returns deep copies and raises configured errors."""

import copy

from mdexport.linear_client import LinearError


class FakeLinear:
    """The `teams_list` and `issue_list` tables; `fail["teams"]` or `fail["issues"]` raises from that listing."""

    def __init__(self, teams_list: list[dict] | None = None, issue_list: list[dict] | None = None):
        self.teams_list: list[dict] = teams_list or []
        self.issue_list: list[dict] = issue_list or []
        self.fail: dict[str, LinearError] = {}
        self.calls: list[str] = []
        self.closed = False

    def _list(self, name: str, table: list[dict]) -> list[dict]:
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]
        return copy.deepcopy(table)

    def teams(self) -> list[dict]:
        return self._list("teams", self.teams_list)

    def issues(self) -> list[dict]:
        return self._list("issues", self.issue_list)

    def close(self) -> None:
        self.closed = True
