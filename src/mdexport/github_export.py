"""Stateless GitHub export - one Markdown file per issue, pull request and wiki page, rewritten only when its text changed."""

import base64
import os
import re
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from github import GithubException
from github.PaginatedList import PaginatedList
from github.PullRequestReview import PullRequestReview

from mdexport.github import connect
from mdexport.linear_export import _write_changed
from mdexport.linear_render import _parse, _sections, _tidy, one_line

STAMP = "%Y-%m-%dT%H:%M:%SZ"
REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
UNEXPECTED = "unexpected name"

_NUMBER_FILE = re.compile(r"[1-9][0-9]*\.md")
# A wiki page name: no path separator, control character, leading dot or underscore (_Sidebar, _Footer).
_WIKI = re.compile(r"[^\W_][^/\\\x00-\x1f]*")
_WIKI_FILE = re.compile(r"[^\W_][^/\\\x00-\x1f]*\.md")
_SKIPPED_REVIEW = ("COMMENTED", "PENDING")


class GitHubError(Exception):
    """A failed GitHub read; `code` is the HTTP status or the error's type, never the token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ExportError(Exception):
    """GitHub could not be read; nothing was written or deleted."""


def _raw(items) -> list[dict]:
    # _rawData is the listing's JSON as returned; public attributes would fetch each object again.
    return [item._rawData for item in items]


class GitHubClient:
    """Reads one repository's issues, pull requests, their comments and reviews, and its wiki."""

    def __init__(self, token: str, repo: str):
        self._token = token
        self._name = repo
        self._github = connect(token, per_page=100)
        self._repo = None

    def _call(self, read):
        try:
            if self._repo is None:
                self._repo = self._github.get_repo(self._name)
            return read(self._repo)
        except GithubException as e:
            raise GitHubError(str(e.status)) from None
        except requests.RequestException as e:
            raise GitHubError(type(e).__name__) from None

    def issues(self) -> list[dict]:
        listing = self._call(lambda repo: _raw(repo.get_issues(state="all", sort="created", direction="asc")))
        return [issue for issue in listing if not issue.get("pull_request")]

    def pulls(self) -> list[dict]:
        return self._call(lambda repo: _raw(repo.get_pulls(state="all", sort="created", direction="asc")))

    def issue_comments(self) -> list[dict]:
        return self._call(lambda repo: _raw(repo.get_issues_comments(sort="created", direction="asc")))

    def review_comments(self) -> list[dict]:
        return self._call(lambda repo: _raw(repo.get_pulls_comments(sort="created", direction="asc")))

    def reviews(self, number: int) -> list[dict]:
        return self._call(lambda repo: _raw(PaginatedList(
            PullRequestReview, repo.requester, f"{repo.url}/pulls/{number}/reviews", {"per_page": 100})))

    def wiki_pages(self) -> dict[str, str]:
        """Page name -> Markdown text of every `.md` page in the wiki; empty when the repo has no wiki."""
        if not self._call(lambda repo: repo.has_wiki):
            return {}
        basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        # The token travels in the environment, never in the URL or argv, so git output and ps never show it.
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "2",
               "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
               "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
               "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": ""}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "wiki"
            result = subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", f"https://github.com/{self._name}.wiki.git", str(target)],
                capture_output=True, text=True, env=env)
            if result.returncode != 0:
                if "not found" in result.stderr.lower():
                    return {}
                raise GitHubError(f"wiki clone exit {result.returncode}")
            pages: dict[str, str] = {}
            for path in sorted(target.rglob("*.md")):
                if ".git" not in path.relative_to(target).parts and path.is_file() and not path.is_symlink():
                    pages.setdefault(path.stem, path.read_text(encoding="utf-8", errors="replace"))
            return pages

    def close(self) -> None:
        self._github.close()


def make_client(token: str, repo: str) -> GitHubClient:
    return GitHubClient(token, repo)


def _login(item: dict) -> str:
    return one_line((item.get("user") or {}).get("login")) or "ghost"


def _body(text: str | None) -> list[str]:
    return _tidy([line for _, _, line in _parse(text)])


def _stamp(value: str | None) -> str:
    value = value or ""
    return f"{value[:10]} {value[11:16]} UTC" if value else "unknown date"


def _number_of(comment: dict, field: str) -> int | None:
    tail = (comment.get(field) or "").rstrip("/").rpartition("/")[2]
    return int(tail) if tail.isdecimal() else None


def _state(item: dict, pull: bool) -> str:
    if pull and item.get("merged_at"):
        return "merged"
    state = one_line(item.get("state"))
    if pull and item.get("draft") and state == "open":
        return "open (draft)"
    reason = one_line(item.get("state_reason"))
    return f"{state} ({reason})" if reason and state == "closed" else state


def _metadata(item: dict, pull: bool) -> list[str]:
    labels = sorted(filter(None, (one_line(label.get("name")) for label in item.get("labels") or [])),
                    key=lambda name: (name.casefold(), name))
    pairs = [
        ("State", _state(item, pull)),
        ("Author", _login(item)),
        ("Labels", ", ".join(labels)),
        ("Base", f"{one_line((item.get('base') or {}).get('ref'))} <- {one_line((item.get('head') or {}).get('ref'))}"
         if pull else ""),
        ("Created", (item.get("created_at") or "")[:10]),
        ("Merged", (item.get("merged_at") or "")[:10]),
        ("Closed", (item.get("closed_at") or "")[:10]),
    ]
    return [f"{name}: {value}" for name, value in pairs if value]


def render_item(item: dict, comments: list[dict], reviews: list[dict] = (), review_comments: list[dict] = (),
                *, pull: bool = False) -> str:
    """An issue's or pull request's file text: a passage for its metadata and body, then one per comment and review."""
    number, title = item["number"], one_line(item.get("title"))
    label = f"PR #{number}" if pull else f"#{number}"
    heading = f"## {label} · {title}"
    head = (f"# {label} · {title}\n{'pull request' if pull else 'issue'} · state: {_state(item, pull)}"
            f" · updated {(item.get('updated_at') or '')[:10]}")
    passages = [[heading, item["html_url"], *_metadata(item, pull), *_body(item.get("body"))]]
    entries = []
    for c in comments:
        entries.append((c.get("created_at") or "", c["id"], f"Comment by {_login(c)}", c))
    for r in reviews:
        if r.get("body") or r.get("state") not in _SKIPPED_REVIEW:
            state = one_line(r.get("state")).lower().replace("_", " ")
            entries.append((r.get("submitted_at") or "", r["id"], f"Review by {_login(r)} ({state})", r))
    for c in review_comments:
        where = one_line(c.get("path"))
        line = c.get("line") or c.get("original_line")
        if where and line:
            where = f"{where}:{line}"
        entries.append((c.get("created_at") or "", c["id"],
                        f"Review comment by {_login(c)}" + (f" on {where}" if where else ""), c))
    for stamp, _, what, entry in sorted(entries, key=lambda e: (e[0], e[1])):
        passages.append([f"{heading} › {what}, {_stamp(stamp)}", entry.get("html_url") or item["html_url"],
                         *(_body(entry.get("body")) or ["(no text)"])])
    return "\n\n".join([head, *("\n".join(passage) for passage in passages)]) + "\n"


def render_wiki_page(repo: str, name: str, text: str) -> str:
    """A wiki page's file text: a passage for its lead, then one per top-level heading section."""
    title = one_line(name.replace("-", " "))
    url = f"https://github.com/{repo}/wiki/{quote(name)}"
    heading = f"## {title}"
    lead, sections = _sections(text)
    passages = [[heading, url, *_tidy(lead)]]
    for section, lines in sections:
        if body := _tidy(lines):
            passages.append([f"{heading} › {section}", url, *body])
    return "\n\n".join([f"# {title}\nwiki · {repo}", *("\n".join(passage) for passage in passages)]) + "\n"


def _by_number(comments: list[dict], field: str) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for comment in comments:
        number = _number_of(comment, field)
        if number is not None:
            grouped.setdefault(number, []).append(comment)
    return grouped


def remove_unkept(folder: Path, pattern: re.Pattern, keep: set[str]) -> list[str]:
    """Delete regular files in `folder` named like `pattern` and not in `keep`; return their stems."""
    if not folder.is_dir() or folder.is_symlink():
        return []
    removed = []
    for path in sorted(folder.iterdir()):
        if pattern.fullmatch(path.name) and path.name not in keep and path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(path.stem)
    return removed


def _try_render(rendered: list, failed: set, unreadable: dict, folder: str, name: str, render) -> None:
    try:
        text = render()
        rendered.append((folder, name, text, text.encode("utf-8")))
    except Exception as e:
        unreadable[f"{folder}/{name[:-3]}"] = f"render failed: {type(e).__name__}"
        failed.add((folder, name))


def export(client, out: Path, repo: str, *, issues: bool = True, pulls: bool = True, wiki: bool = True,
           exclude: Iterable[int] = (), now: datetime | None = None) -> dict:
    """Export `repo` into `out/issues/<n>.md`, `out/pulls/<n>.md` and `out/wiki/<page>.md`; return the run's report."""
    try:
        issue_list = client.issues() if issues else []
        pull_list = client.pulls() if pulls else []
        comments = _by_number(client.issue_comments(), "issue_url") if issues or pulls else {}
        review_comments = _by_number(client.review_comments(), "pull_request_url") if pulls else {}
        pages = client.wiki_pages() if wiki else {}
    except GitHubError as e:
        raise ExportError(f"GitHub listing failed: {e.code}") from e
    excluded = set(exclude)
    unreadable: dict = {}
    rendered: list[tuple] = []
    failed: set[tuple[str, str]] = set()
    for item in issue_list:
        if item["number"] not in excluded:
            _try_render(rendered, failed, unreadable, "issues", f"{item['number']}.md",
                        lambda: render_item(item, comments.get(item["number"], [])))
    for item in pull_list:
        number = item["number"]
        if number in excluded:
            continue
        try:
            reviews = client.reviews(number)
        except GitHubError as e:
            unreadable[f"pulls/{number}"] = f"read failed: {e.code}"
            failed.add(("pulls", f"{number}.md"))
            continue
        _try_render(rendered, failed, unreadable, "pulls", f"{number}.md",
                    lambda: render_item(item, comments.get(number, []), reviews, review_comments.get(number, []),
                                        pull=True))
    for name, text in pages.items():
        if not _WIKI.fullmatch(name):
            unreadable[f"wiki/{name}"] = UNEXPECTED
            continue
        _try_render(rendered, failed, unreadable, "wiki", f"{name}.md", lambda: render_wiki_page(repo, name, text))
    out.mkdir(parents=True, exist_ok=True)
    written = sum(_write_changed(out / folder / name, text, data) for folder, name, text, data in rendered)
    # An item that failed to read or render keeps its old file, so its path counts as kept for removal.
    keep = {(folder, name) for folder, name, *_ in rendered} | failed
    count = {folder: sum(f == folder for f, *_ in rendered) for folder in ("issues", "pulls", "wiki")}
    report = {
        "repo": repo,
        "issues": count["issues"],
        "pulls": count["pulls"],
        "wiki_pages": count["wiki"],
        "written": written,
        "removed": [],
        "unreadable": unreadable,
        "removal_skipped": not keep,  # a run that keeps no item never deletes anything
    }
    # Removal is per kind and only for a kind that was read and keeps at least one file.
    for folder, enabled, pattern in (("issues", issues, _NUMBER_FILE), ("pulls", pulls, _NUMBER_FILE),
                                     ("wiki", wiki, _WIKI_FILE)):
        names = {name for f, name in keep if f == folder}
        if enabled and names:
            report["removed"] += remove_unkept(out / folder, pattern, names)
    report["removed"].sort()
    report["finished_at"] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP)
    return report
