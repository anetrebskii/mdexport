import json

import httpx
import pytest

from mdexport.linear_client import ISSUES, LinearClient, LinearError


class Script:
    """A MockTransport handler answering with `replies` in turn (the last one repeats) and recording each request."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def make(script: Script, token: str = "lin_test") -> tuple[LinearClient, list[float]]:
    slept: list[float] = []
    client = LinearClient(token, transport=httpx.MockTransport(script), sleep=slept.append)
    return client, slept


def ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def page(key: str, nodes: list, cursor: str | None = None) -> httpx.Response:
    """One page of a top-level connection; `cursor` set means another page follows."""
    info = {"hasNextPage": cursor is not None, "endCursor": cursor}
    return ok({"data": {key: {"nodes": nodes, "pageInfo": info}}})


LAST = {"hasNextPage": False, "endCursor": None}
CONNECTIONS = ("labels", "children", "relations", "inverseRelations", "attachments", "comments")


def node(task_id: str, **fields) -> dict:
    """A raw task node as GraphQL returns it, with every connection an empty last page unless given."""
    raw = {"id": task_id, "identifier": f"FOR-{task_id}", "assignee": None,
           **{key: {"nodes": [], "pageInfo": LAST} for key in CONNECTIONS}}
    return {**raw, **fields}


def comment(comment_id: str) -> dict:
    return {"id": comment_id, "url": f"https://linear.app/c/{comment_id}", "body": comment_id,
            "createdAt": "2026-10-22T09:00:00.000Z", "parent": None, "user": {"name": "Alex"},
            "botActor": None, "externalUser": None}


AUTH_ERROR = {"errors": [{"message": "Authentication required, not authenticated",
                          "extensions": {"code": "AUTHENTICATION_ERROR"}}]}


def test_headers_and_body():
    script = Script(ok({"data": {"teams": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}))
    client, _ = make(script)
    assert client.teams() == []
    (request,) = script.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.linear.app/graphql"
    assert request.headers["Authorization"] == "lin_test"
    assert request.headers["Content-Type"] == "application/json"
    assert request.extensions["timeout"] == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}
    sent = body(request)
    assert "teams(" in sent["query"]
    assert sent["variables"] == {"after": None}


def test_teams_follows_cursors():
    a = {"key": "FOR", "name": "Formula", "private": False}
    b = {"key": "SEC", "name": "Secret", "private": True}
    script = Script(page("teams", [a], "c1"), page("teams", [b]))
    client, _ = make(script)
    assert client.teams() == [a, b]
    first, second = (body(r) for r in script.requests)
    assert first["variables"] == {"after": None}
    assert second["variables"]["after"] == "c1"


def test_issues_follows_cursors():
    script = Script(page("issues", [node("1"), node("2")], "c1"), page("issues", [node("3")]))
    client, _ = make(script)
    assert [task["id"] for task in client.issues()] == ["1", "2", "3"]
    first, second = (body(r) for r in script.requests)
    assert "issues(" in first["query"]
    assert first["variables"] == {"after": None}
    assert second["variables"]["after"] == "c1"


def test_connections_flattened():
    labels = [{"name": "bug"}]
    children = [{"identifier": "FOR-2", "title": "Child"}]
    relations = [{"type": "related", "relatedIssue": {"identifier": "FOR-3", "title": "Other"}}]
    inverse = [{"type": "blocks", "issue": {"identifier": "FOR-4", "title": "Blocker"}}]
    attachments = [{"title": "PR", "url": "https://github.com/x/y/pull/1"}]
    raw = node("1", labels={"nodes": labels, "pageInfo": LAST}, children={"nodes": children, "pageInfo": LAST},
               relations={"nodes": relations, "pageInfo": LAST}, inverseRelations={"nodes": inverse, "pageInfo": LAST},
               attachments={"nodes": attachments, "pageInfo": LAST},
               comments={"nodes": [comment("k")], "pageInfo": {"hasNextPage": False, "endCursor": "z"}})
    client, _ = make(Script(page("issues", [raw])))
    (task,) = client.issues()
    assert task["labels"] == labels
    assert task["children"] == children
    assert task["relations"] == relations
    assert task["inverseRelations"] == inverse
    assert task["attachments"] == attachments
    assert task["comments"] == [comment("k")]
    assert task["assignee"] is None


def test_issue_comments_paged():
    c1, c2, c3 = comment("c1"), comment("c2"), comment("c3")
    raw = node("issue-uuid", comments={"nodes": [c1, c2], "pageInfo": {"hasNextPage": True, "endCursor": "k1"}})
    more = {"comments": {"nodes": [c3], "pageInfo": {"hasNextPage": False, "endCursor": "k2"}}}
    script = Script(page("issues", [raw]), ok({"data": {"issue": more}}))
    client, _ = make(script)
    (task,) = client.issues()
    assert task["comments"] == [c1, c2, c3]
    follow_up = body(script.requests[1])
    assert "issue(id: $id)" in follow_up["query"]
    assert follow_up["variables"] == {"id": "issue-uuid", "after": "k1"}


METADATA = {"labels": {"name": "bug"}, "children": {"identifier": "FOR-2", "title": "Child"},
            "relations": {"type": "related", "relatedIssue": {"identifier": "FOR-3", "title": "Other"}},
            "inverseRelations": {"type": "blocks", "issue": {"identifier": "FOR-4", "title": "Blocker"}},
            "attachments": {"title": "PR", "url": "https://github.com/x/y/pull/1"}}


@pytest.mark.parametrize("key", list(METADATA))
def test_metadata_connection_paged(key):
    first, rest = dict(METADATA[key]), [dict(METADATA[key]), dict(METADATA[key])]
    raw = node("issue-uuid", **{key: {"nodes": [first], "pageInfo": {"hasNextPage": True, "endCursor": "m1"}}})
    pages = [{key: {"nodes": [rest[0]], "pageInfo": {"hasNextPage": True, "endCursor": "m2"}}},
             {key: {"nodes": [rest[1]], "pageInfo": LAST}}]
    script = Script(page("issues", [raw]), *(ok({"data": {"issue": p}}) for p in pages))
    client, _ = make(script)
    (task,) = client.issues()
    assert task[key] == [first, *rest]
    follow_ups = [body(r) for r in script.requests[1:]]
    assert [f["variables"] for f in follow_ups] == [{"id": "issue-uuid", "after": "m1"},
                                                    {"id": "issue-uuid", "after": "m2"}]
    assert all("issue(id: $id)" in f["query"] and f"{key}(first: 100, after: $after)" in f["query"]
               for f in follow_ups)


def test_issues_query_asks_every_connection_for_its_page_info():
    for key in CONNECTIONS:
        start = ISSUES.index(f"{key}(first: ")
        assert "pageInfo { hasNextPage endCursor }" in ISSUES[start:ISSUES.index("\n", start)]


@pytest.mark.parametrize("cursor", [None, ""])
def test_metadata_missing_cursor_raises(cursor):
    raw = node("1", attachments={"nodes": [METADATA["attachments"]],
                                 "pageInfo": {"hasNextPage": True, "endCursor": cursor}})
    script = Script(page("issues", [raw]), ok({"data": {"issue": {"attachments": {"nodes": [], "pageInfo": LAST}}}}))
    client, _ = make(script)
    with pytest.raises(LinearError) as caught:
        client.issues()
    assert (caught.value.status, caught.value.code) == (200, "missing_cursor")
    assert len(script.requests) == 1


def no_cursor(key: str, nodes: list, cursor: str | None) -> httpx.Response:
    """A page that claims another follows but gives no usable cursor."""
    return ok({"data": {key: {"nodes": nodes, "pageInfo": {"hasNextPage": True, "endCursor": cursor}}}})


@pytest.mark.parametrize("cursor", [None, ""])
def test_teams_missing_cursor_raises(cursor):
    script = Script(no_cursor("teams", [{"key": "FOR", "name": "Formula", "private": False}], cursor),
                    page("teams", []))
    client, _ = make(script)
    with pytest.raises(LinearError) as caught:
        client.teams()
    assert (caught.value.status, caught.value.code) == (200, "missing_cursor")
    assert len(script.requests) == 1


@pytest.mark.parametrize("cursor", [None, ""])
def test_issues_missing_cursor_raises(cursor):
    script = Script(no_cursor("issues", [node("1")], cursor), page("issues", []))
    client, _ = make(script)
    with pytest.raises(LinearError) as caught:
        client.issues()
    assert (caught.value.status, caught.value.code) == (200, "missing_cursor")
    assert len(script.requests) == 1


@pytest.mark.parametrize("cursor", [None, ""])
def test_comments_missing_cursor_raises(cursor):
    raw = node("1", comments={"nodes": [comment("c1")], "pageInfo": {"hasNextPage": True, "endCursor": cursor}})
    more = {"comments": {"nodes": [comment("c2")], "pageInfo": {"hasNextPage": False, "endCursor": "k2"}}}
    script = Script(page("issues", [raw]), ok({"data": {"issue": more}}))
    client, _ = make(script)
    with pytest.raises(LinearError) as caught:
        client.issues()
    assert (caught.value.status, caught.value.code) == (200, "missing_cursor")
    assert len(script.requests) == 1


def test_server_error_backs_off():
    script = Script(httpx.Response(502), httpx.Response(502), page("teams", []))
    client, slept = make(script)
    assert client.teams() == []
    assert slept == [1, 2]


def test_429_retried():
    script = Script(httpx.Response(429), page("teams", []))
    client, slept = make(script)
    assert client.teams() == []
    assert slept == [1]
    assert len(script.requests) == 2


@pytest.mark.parametrize("status", [400, 200])
def test_ratelimited_in_body_retried(status):
    script = Script(httpx.Response(status, json={"errors": [{"extensions": {"code": "RATELIMITED"}}]}),
                    page("teams", []))
    client, slept = make(script)
    assert client.teams() == []
    assert slept == [1]
    assert len(script.requests) == 2


def test_gives_up_after_five_attempts():
    script = Script(httpx.Response(503))
    client, slept = make(script)
    with pytest.raises(LinearError) as caught:
        client.teams()
    assert caught.value.status == 503
    assert caught.value.code == "server_error"
    assert len(script.requests) == 5
    assert slept == [1, 2, 4, 8]


def test_network_error_retried_then_raised():
    script = Script(httpx.ConnectError("x"))
    client, slept = make(script)
    with pytest.raises(LinearError) as caught:
        client.teams()
    err = caught.value
    assert err.status == 0
    assert err.code == "network_error"
    assert str(err) == "network_error"
    assert len(script.requests) == 5
    assert slept == [1, 2, 4, 8]
    assert err.__context__ is None and err.__cause__ is None


@pytest.mark.parametrize("error", [httpx.TooManyRedirects("Exceeded maximum allowed redirects."),
                                   httpx.DecodingError("Malformed gzip body.")])
def test_other_http_error_not_retried(error):
    script = Script(error)
    client, slept = make(script)
    with pytest.raises(LinearError) as caught:
        client.teams()
    err = caught.value
    assert err.status == 0
    assert err.code == "network_error"
    assert len(script.requests) == 1
    assert slept == []
    # The httpx error carries the request and its Authorization header, so it must not be chained.
    assert err.__context__ is None and err.__cause__ is None


def test_auth_error_not_retried():
    script = Script(httpx.Response(401, json=AUTH_ERROR))
    client, slept = make(script)
    with pytest.raises(LinearError) as caught:
        client.teams()
    assert caught.value.status == 401
    assert caught.value.code == "AUTHENTICATION_ERROR"
    assert len(script.requests) == 1
    assert slept == []


def test_graphql_error_on_200_not_retried():
    script = Script(ok({"errors": [{"extensions": {"code": "INVALID_INPUT"}}]}))
    client, slept = make(script)
    with pytest.raises(LinearError) as caught:
        client.issues()
    assert caught.value.status == 200
    assert caught.value.code == "INVALID_INPUT"
    assert len(script.requests) == 1
    assert slept == []


@pytest.mark.parametrize("reply", [httpx.Response(200, content=b"not json"), httpx.Response(200, json=[])])
def test_invalid_json_not_retried(reply):
    script = Script(reply)
    client, slept = make(script)
    with pytest.raises(LinearError) as caught:
        client.teams()
    err = caught.value
    assert err.status == 200
    assert err.code == "invalid_json"
    assert len(script.requests) == 1
    assert slept == []
    assert err.__context__ is None and err.__cause__ is None


def test_token_not_in_error():
    script = Script(httpx.Response(401, json=AUTH_ERROR))
    client, _ = make(script, token="lin_secret_123")
    with pytest.raises(LinearError) as caught:
        client.teams()
    err = caught.value
    assert script.requests[0].headers["Authorization"] == "lin_secret_123"
    assert str(err) == "AUTHENTICATION_ERROR"
    assert "lin_secret_123" not in str(err)
    assert "lin_secret_123" not in repr(err)
    assert err.__cause__ is None
