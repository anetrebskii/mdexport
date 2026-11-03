"""A stand-in for DriveClient that records calls, returns deep copies and raises configured errors."""

import copy

from mdexport.googledocs_export import GoogleError


class FakeDrive:
    """`doc_list` is the listing, `bodies[doc id]` each doc's Markdown; `fail["docs"]` or `fail[doc id]` raises."""

    def __init__(self, doc_list: list[dict] | None = None, bodies: dict[str, str] | None = None):
        self.doc_list: list[dict] = doc_list or []
        self.bodies: dict[str, str] = bodies or {}
        self.fail: dict[str, GoogleError] = {}
        self.calls: list = []

    def docs(self, folders: list[str]) -> list[dict]:
        self.calls.append(("docs", tuple(folders)))
        if "docs" in self.fail:
            raise self.fail["docs"]
        return copy.deepcopy(self.doc_list)

    def markdown(self, doc_id: str, title: str) -> str:
        self.calls.append(("markdown", doc_id))
        if doc_id in self.fail:
            raise self.fail[doc_id]
        return self.bodies.get(doc_id, "")
