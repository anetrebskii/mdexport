"""A small Linear GraphQL client - retrying rate limits, server and network errors."""

import time

import httpx

API_URL = "https://api.linear.app/graphql"
TIMEOUT = 30.0
PAGE_SIZE = 100  # the `first: 100` the queries below spell out
MAX_ATTEMPTS = 5

_COMMENT = "id url body createdAt parent { id } user { name } botActor { name } externalUser { name }"
TEAMS = """query($after: String) { teams(first: 100, after: $after, includeArchived: true) {
  nodes { key name private } pageInfo { hasNextPage endCursor } } }"""
_PAGE_INFO = "pageInfo { hasNextPage endCursor }"
# Each task connection's node fields and its page size in ISSUES; a connection with more pages is read on by ISSUE_PAGE.
_CONNECTIONS = {"labels": ("name", 50), "children": ("identifier title", 100),
                "relations": ("type relatedIssue { identifier title }", 50),
                "inverseRelations": ("type issue { identifier title }", 50),
                "attachments": ("title url", 50), "comments": (_COMMENT, 100)}
ISSUES = """query($after: String) { issues(first: 100, after: $after, includeArchived: true, orderBy: createdAt,
  filter: { team: { private: { eq: false } } }) { nodes {
  id identifier title description url priorityLabel estimate trashed archivedAt
  createdAt updatedAt startedAt completedAt canceledAt
  state { name type } team { key name } assignee { name } creator { name }
  project { name } projectMilestone { name } cycle { number name } parent { identifier title }
  %s
  } %s } }""" % ("\n  ".join("%s(first: %d) { nodes { %s } %s }" % (key, size, fields, _PAGE_INFO)
                                for key, (fields, size) in _CONNECTIONS.items()), _PAGE_INFO)
ISSUE_PAGE = """query($id: String!, $after: String) { issue(id: $id) {
  %s(first: 100, after: $after) { nodes { %s } """ + _PAGE_INFO + """ } } }"""


class LinearError(Exception):
    """A Linear call failed; `status` is 0 for a network error, and `str(e)` is the code, never the token."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _json_object(resp: httpx.Response) -> dict | None:
    """The body as a JSON object, else None."""
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error_codes(body: dict | None) -> list:
    """Every GraphQL error's `extensions.code` (None where missing), in order."""
    errors = body.get("errors") if body else None
    if not isinstance(errors, list):
        return []
    extensions = [e.get("extensions") if isinstance(e, dict) else None for e in errors]
    return [x.get("code") if isinstance(x, dict) else None for x in extensions]


def _code(body: dict | None, fallback: str) -> str:
    """The first GraphQL error's `extensions.code` when a non-empty string, else `fallback`."""
    codes = _error_codes(body)
    code = codes[0] if codes else None
    return code if isinstance(code, str) and code else fallback


def _next_cursor(info: dict) -> str | None:
    """The cursor of the next page, None on the last; a next page without a cursor raises `missing_cursor`."""
    if not info.get("hasNextPage"):
        return None
    cursor = info.get("endCursor")
    if not (isinstance(cursor, str) and cursor):
        raise LinearError(200, "missing_cursor")  # a short listing would let the exporter delete live tasks
    return cursor


class LinearClient:
    """Retrying Linear GraphQL calls; a failed call raises `LinearError`."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        headers = {"Authorization": token, "Content-Type": "application/json"}
        self._http = httpx.Client(headers=headers, timeout=TIMEOUT, transport=transport)
        self._sleep = sleep

    def _request(self, query: str, variables: dict) -> dict:
        """One call's `data`, after up to `MAX_ATTEMPTS` attempts at rate limits, 5xx and transport errors."""
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self._http.post(API_URL, json={"query": query, "variables": variables})
            except httpx.TransportError:
                status, code = 0, "network_error"
            except httpx.HTTPError:
                break  # too many redirects or an undecodable body: a retry fails the same way
            else:
                status, body = resp.status_code, _json_object(resp)
                if "RATELIMITED" in _error_codes(body):
                    code = "RATELIMITED"
                elif status == 429:
                    code = _code(body, "rate_limited")
                elif status >= 500:
                    code = _code(body, "server_error")
                elif not resp.is_success:
                    raise LinearError(status, _code(body, f"http_{status}"))
                elif body is None:
                    raise LinearError(status, "invalid_json")
                elif body.get("errors"):
                    raise LinearError(status, _code(body, "graphql_error"))
                elif not isinstance(body.get("data"), dict):
                    raise LinearError(status, "invalid_json")
                else:
                    return body["data"]
            if attempt == MAX_ATTEMPTS - 1:
                raise LinearError(status, code)
            self._sleep(2**attempt)
        # Reached only by the break above; raising here, outside the except, chains no httpx error or its headers.
        raise LinearError(0, "network_error")

    def _nodes(self, query: str, path: tuple[str, ...], variables: dict, after: str | None = None) -> list[dict]:
        """Every node of the connection at `path` in `data`, from cursor `after` on."""
        nodes = []
        while True:
            connection = self._request(query, {**variables, "after": after})
            for key in path:
                connection = connection[key]
            nodes.extend(connection["nodes"])
            after = _next_cursor(connection["pageInfo"])
            if not after:
                return nodes

    def teams(self) -> list[dict]:
        """Every team, archived ones included, as `{key, name, private}`."""
        return self._nodes(TEAMS, ("teams",), {})

    def issues(self) -> list[dict]:
        """Every task of every public team, each connection flattened to a list with all of its pages."""
        tasks = self._nodes(ISSUES, ("issues",), {})
        for task in tasks:
            for key, (fields, _) in _CONNECTIONS.items():
                after = _next_cursor(task[key]["pageInfo"])
                task[key] = task[key]["nodes"]
                if after:
                    task[key] += self._nodes(ISSUE_PAGE % (key, fields), ("issue", key), {"id": task["id"]}, after)
        return tasks

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()


def make_client(token: str) -> LinearClient:
    """A Linear client; the one factory the CLI calls and tests patch."""
    return LinearClient(token)
