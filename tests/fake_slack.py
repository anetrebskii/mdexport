"""A stand-in for slack_sdk's WebClient that records calls, pages by 2 and raises configured errors."""

import copy

from slack_sdk.errors import SlackApiError

PAGE = 2


def _page(items: list, cursor: str | None) -> dict:
    start = int(cursor or 0)
    more = str(start + PAGE) if start + PAGE < len(items) else ""
    return {"ok": True, "items": copy.deepcopy(items[start : start + PAGE]), "response_metadata": {"next_cursor": more}}


def _error(code: str) -> SlackApiError:
    return SlackApiError("x", {"ok": False, "error": code})


class FakeClient:
    """`fail[cid]` makes that channel's history raise; `list_fail_page` makes that 1-based listing page raise."""

    def __init__(self):
        self.auth = {"url": "https://formulagrowth.slack.com/", "user_id": "UBOT", "bot_id": "BBOT"}
        self.members = [{"id": "U1", "real_name": "Jay"}, {"id": "U2", "real_name": "Alex"}]
        self.channels = []
        self.history = {}
        self.replies = {}
        self.fail = {}
        self.list_fail_page = None
        self.calls = []

    def auth_test(self, **kwargs):
        self.calls.append(("auth_test", kwargs))
        return dict(self.auth)

    def users_list(self, **kwargs):
        self.calls.append(("users_list", kwargs))
        return {"ok": True, "members": copy.deepcopy(self.members), "response_metadata": {"next_cursor": ""}}

    def conversations_list(self, **kwargs):
        self.calls.append(("conversations_list", kwargs))
        if self.list_fail_page == int(kwargs.get("cursor") or 0) // PAGE + 1:
            raise _error("internal_error")
        resp = _page(self.channels, kwargs.get("cursor"))
        resp["channels"] = resp.pop("items")
        return resp

    def conversations_history(self, **kwargs):
        """Messages at or after `oldest`, newest first."""
        self.calls.append(("conversations_history", kwargs))
        cid = kwargs["channel"]
        if cid in self.fail:
            raise _error(self.fail[cid])
        found = [m for m in self.history.get(cid, []) if float(m["ts"]) >= float(kwargs["oldest"])]
        resp = _page(sorted(found, key=lambda m: float(m["ts"]), reverse=True), kwargs.get("cursor"))
        resp["messages"] = resp.pop("items")
        return resp

    def conversations_replies(self, **kwargs):
        self.calls.append(("conversations_replies", kwargs))
        resp = _page(self.replies.get((kwargs["channel"], kwargs["ts"]), []), kwargs.get("cursor"))
        resp["messages"] = resp.pop("items")
        return resp

    def conversations_join(self, **kwargs):
        self.calls.append(("conversations_join", kwargs))
        raise AssertionError("the exporter must never join a channel")

    def history_calls(self, cid: str) -> list[dict]:
        return [kw for m, kw in self.calls if m == "conversations_history" and kw["channel"] == cid]
