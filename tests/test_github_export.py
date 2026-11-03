import base64
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from github import GithubException

from mdexport import github_export
from mdexport.github_export import ExportError, GitHubClient, GitHubError, export, render_item, render_wiki_page

from .fake_github import FakeGitHub

NOW = datetime(2026, 11, 3, 3, 30, tzinfo=timezone.utc)
REPO = "twins-ai/Twins-AI"
URL = f"https://github.com/{REPO}"
TOKEN = "ghp_secret_token_1"


def issue(number: int, **fields) -> dict:
    return {"number": number, "title": f"Issue {number}", "state": "open", "state_reason": None,
            "user": {"login": "alice"}, "labels": [], "created_at": "2026-10-01T09:00:00Z",
            "updated_at": "2026-10-02T09:00:00Z", "closed_at": None, "html_url": f"{URL}/issues/{number}",
            "body": None, **fields}


def pull(number: int, **fields) -> dict:
    return {**issue(number, html_url=f"{URL}/pull/{number}", title=f"PR {number}"), "draft": False,
            "merged_at": None, "base": {"ref": "main"}, "head": {"ref": f"feature-{number}"}, **fields}


def comment(cid: int, number: int, body: str, created: str = "2026-10-03T11:00:00Z") -> dict:
    return {"id": cid, "issue_url": f"https://api.github.com/repos/{REPO}/issues/{number}",
            "html_url": f"{URL}/issues/{number}#issuecomment-{cid}", "user": {"login": "bob"},
            "created_at": created, "body": body}


def review_comment(cid: int, number: int, body: str, created: str = "2026-10-04T10:00:00Z") -> dict:
    return {"id": cid, "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/{number}",
            "html_url": f"{URL}/pull/{number}#discussion_r{cid}", "user": {"login": "carol"},
            "created_at": created, "body": body, "path": "src/app.py", "line": 12}


def review(rid: int, state: str, body: str = "", submitted: str = "2026-10-05T10:00:00Z") -> dict:
    return {"id": rid, "state": state, "body": body, "submitted_at": submitted, "user": {"login": "dave"},
            "html_url": f"{URL}/pull/7#pullrequestreview-{rid}"}


def run(fake: FakeGitHub, out: Path, **kwargs) -> dict:
    return export(fake, out, REPO, now=NOW, **kwargs)


def md_files(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))


def tree(out: Path) -> dict[str, bytes]:
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def put(path: Path, text: str = "old\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def full_fake() -> FakeGitHub:
    fake = FakeGitHub()
    fake.issue_list = [issue(1, body="Steps\n## Expected\nworks")]
    fake.pull_list = [pull(7, body="Fixes #1")]
    fake.comment_list = [comment(100, 1, "on the issue"), comment(101, 7, "on the PR")]
    fake.review_comment_list = [review_comment(200, 7, "rename this")]
    fake.reviews_of = {7: [review(300, "APPROVED", "ship it"), review(301, "COMMENTED")]}
    fake.wiki = {"Home": "Welcome\n# Setup\ninstall it", "Release-Process": "Tag it"}
    return fake


def test_first_run_writes_one_file_per_item(tmp_path):
    fake = full_fake()
    report = run(fake, tmp_path)
    assert md_files(tmp_path) == ["issues/1.md", "pulls/7.md", "wiki/Home.md", "wiki/Release-Process.md"]
    assert (tmp_path / "issues" / "1.md").read_text(encoding="utf-8") == render_item(
        fake.issue_list[0], [fake.comment_list[0]])
    assert (tmp_path / "wiki" / "Home.md").read_text(encoding="utf-8") == render_wiki_page(
        REPO, "Home", fake.wiki["Home"])
    assert report == {"repo": REPO, "issues": 1, "pulls": 1, "wiki_pages": 2, "written": 4, "removed": [],
                      "unreadable": {}, "removal_skipped": False, "finished_at": "2026-11-03T03:30:00Z"}


def test_issue_passages_link_back(tmp_path):
    fake = full_fake()
    run(fake, tmp_path)
    text = (tmp_path / "issues" / "1.md").read_text(encoding="utf-8")
    assert text == (
        "# #1 · Issue 1\nissue · state: open · updated 2026-10-02\n\n"
        f"## #1 · Issue 1\n{URL}/issues/1\nState: open\nAuthor: alice\nCreated: 2026-10-01\nSteps\n\\## Expected\nworks\n\n"
        f"## #1 · Issue 1 › Comment by bob, 2026-10-03 11:00 UTC\n{URL}/issues/1#issuecomment-100\non the issue\n")


def test_pull_has_comments_reviews_and_review_comments_in_time_order(tmp_path):
    run(full_fake(), tmp_path)
    text = (tmp_path / "pulls" / "7.md").read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## PR #7 · PR 7",
        "## PR #7 · PR 7 › Comment by bob, 2026-10-03 11:00 UTC",
        "## PR #7 · PR 7 › Review comment by carol on src/app.py:12, 2026-10-04 10:00 UTC",
        "## PR #7 · PR 7 › Review by dave (approved), 2026-10-05 10:00 UTC",
    ]
    assert f"{URL}/pull/7#discussion_r200\nrename this" in text
    assert f"{URL}/pull/7#pullrequestreview-300\nship it" in text
    assert "Base: main <- feature-7" in text


def test_pull_states():
    assert "state: merged" in render_item(pull(1, state="closed", merged_at="2026-10-09T00:00:00Z"), [], pull=True)
    assert "state: open (draft)" in render_item(pull(1, draft=True), [], pull=True)
    assert "state: closed (not_planned)" in render_item(issue(1, state="closed", state_reason="not_planned"), [])


def test_code_fence_and_heading_in_body_cannot_open_markdown():
    text = render_item(issue(1, body="```python\n# not a heading\n```\n# Heading"), [])
    assert "Code (python):\n    \\# not a heading\n\\# Heading" in text


def test_wiki_sections_and_url():
    text = render_wiki_page(REPO, "Release-Process", "Intro\n## Tag\ngit tag\n## Deploy\nhelm")
    assert text == (
        f"# Release Process\nwiki · {REPO}\n\n"
        f"## Release Process\n{URL}/wiki/Release-Process\nIntro\n\n"
        f"## Release Process › Tag\n{URL}/wiki/Release-Process\ngit tag\n\n"
        f"## Release Process › Deploy\n{URL}/wiki/Release-Process\nhelm\n")


def test_unchanged_file_not_rewritten(tmp_path):
    fake = full_fake()
    run(fake, tmp_path)
    path = tmp_path / "issues" / "1.md"
    os.utime(path, (1_000_000_000, 1_000_000_000))
    report = run(fake, tmp_path)
    assert report["written"] == 0
    assert path.stat().st_mtime == 1_000_000_000


def test_changed_item_rewritten(tmp_path):
    fake = full_fake()
    run(fake, tmp_path)
    fake.comment_list.append(comment(102, 1, "new comment", "2026-10-06T00:00:00Z"))
    report = run(fake, tmp_path)
    assert report["written"] == 1
    assert "new comment" in (tmp_path / "issues" / "1.md").read_text(encoding="utf-8")


def test_gone_and_excluded_items_deleted(tmp_path):
    fake = full_fake()
    fake.issue_list.append(issue(2))
    fake.pull_list.append(pull(8))
    run(fake, tmp_path)
    fake.issue_list.pop()
    fake.wiki.pop("Release-Process")
    report = run(fake, tmp_path, exclude=[8])
    assert md_files(tmp_path) == ["issues/1.md", "pulls/7.md", "wiki/Home.md"]
    assert report["removed"] == ["2", "8", "Release-Process"]


def test_non_item_names_untouched(tmp_path):
    others = ["notes.md", "issues/README.md", "issues/01.md", "issues/3.txt", "pulls/x.md", "wiki/_Sidebar.md",
              "wiki/.hidden.md", "other/5.md"]
    for name in others:
        put(tmp_path / name, f"keep {name}\n")
    before = {name: (tmp_path / name).read_bytes() for name in others}
    run(full_fake(), tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in others} == before


def test_symlinked_folder_untouched(tmp_path):
    outside, out = tmp_path / "outside", tmp_path / "out"
    put(outside / "9.md", "outside\n")
    out.mkdir()
    (out / "pulls").symlink_to(outside, target_is_directory=True)
    fake = full_fake()
    fake.pull_list = []
    report = run(fake, out)
    assert (outside / "9.md").read_text(encoding="utf-8") == "outside\n"
    assert report["removed"] == []


@pytest.mark.parametrize("name", ["issues", "pulls", "issue_comments", "review_comments", "wiki"])
def test_listing_failure_changes_nothing(tmp_path, name):
    put(tmp_path / "issues" / "5.md")
    before = tree(tmp_path)
    fake = full_fake()
    fake.fail[name] = GitHubError("401")
    with pytest.raises(ExportError) as raised:
        run(fake, tmp_path)
    assert str(raised.value) == "GitHub listing failed: 401"
    assert tree(tmp_path) == before


def test_listing_failure_creates_no_output(tmp_path):
    out = tmp_path / "out"
    fake = full_fake()
    fake.fail["pulls"] = GitHubError("ConnectionError")
    with pytest.raises(ExportError):
        run(fake, out)
    assert not out.exists()


def test_unreadable_reviews_keep_old_file(tmp_path):
    put(tmp_path / "pulls" / "7.md")
    put(tmp_path / "pulls" / "9.md")
    fake = full_fake()
    fake.fail[("reviews", 7)] = GitHubError("502")
    report = run(fake, tmp_path)
    assert (tmp_path / "pulls" / "7.md").read_text(encoding="utf-8") == "old\n"
    assert not (tmp_path / "pulls" / "9.md").exists()
    assert report["unreadable"] == {"pulls/7": "read failed: 502"}
    assert report["pulls"] == 0
    assert report["removed"] == ["9"]


def test_render_error_reported_and_file_kept(tmp_path, monkeypatch):
    put(tmp_path / "issues" / "1.md")

    def render(item, *args, **kwargs):
        if item["number"] == 1:
            raise KeyError("html_url")
        return render_item(item, *args, **kwargs)

    monkeypatch.setattr(github_export, "render_item", render)
    report = run(full_fake(), tmp_path)
    assert (tmp_path / "issues" / "1.md").read_text(encoding="utf-8") == "old\n"
    assert report["unreadable"] == {"issues/1": "render failed: KeyError"}


def test_unsafe_wiki_name_never_written(tmp_path):
    fake = full_fake()
    fake.wiki = {"../evil": "x", "_Sidebar": "x", "Home": "ok"}
    report = run(fake, tmp_path)
    assert sorted(p.name for p in (tmp_path / "wiki").iterdir()) == ["Home.md"]
    assert report["unreadable"] == {"wiki/../evil": "unexpected name", "wiki/_Sidebar": "unexpected name"}


def test_no_item_kept_skips_removal(tmp_path):
    put(tmp_path / "issues" / "1.md")
    put(tmp_path / "wiki" / "Home.md")
    before = tree(tmp_path)
    report = run(FakeGitHub(), tmp_path)
    assert tree(tmp_path) == before
    assert report["removal_skipped"] is True
    assert report["removed"] == []


def test_kind_that_keeps_nothing_is_not_cleaned(tmp_path):
    # An empty wiki listing, as a clone that looks like "not found" gives, must not delete the earlier pages.
    put(tmp_path / "wiki" / "Home.md")
    fake = full_fake()
    fake.wiki = {}
    report = run(fake, tmp_path)
    assert (tmp_path / "wiki" / "Home.md").read_text(encoding="utf-8") == "old\n"
    assert report["removal_skipped"] is False
    assert report["wiki_pages"] == 0


def test_disabled_kinds_not_read_or_cleaned(tmp_path):
    put(tmp_path / "pulls" / "9.md")
    put(tmp_path / "wiki" / "Old.md")
    fake = full_fake()
    report = run(fake, tmp_path, pulls=False, wiki=False)
    assert fake.calls == ["issues", "issue_comments"]
    assert md_files(tmp_path) == ["issues/1.md", "pulls/9.md", "wiki/Old.md"]
    assert (report["issues"], report["pulls"], report["wiki_pages"]) == (1, 0, 0)


def test_utf8_written(tmp_path):
    fake = full_fake()
    fake.issue_list = [issue(1, title="Hero 🚀")]
    run(fake, tmp_path)
    assert "🚀" in (tmp_path / "issues" / "1.md").read_text(encoding="utf-8")


class Item:
    def __init__(self, data: dict):
        self._rawData = data


def client_with(repo) -> GitHubClient:
    client = GitHubClient(TOKEN, REPO)
    client._repo = repo
    return client


def test_client_issues_leave_out_pull_requests():
    repo = SimpleNamespace(get_issues=lambda **kw: [Item(issue(1)), Item(issue(2, pull_request={"url": "x"}))])
    assert [i["number"] for i in client_with(repo).issues()] == [1]


def test_client_wraps_github_errors():
    def fail(**kw):
        raise GithubException(403, {"message": "rate limited"}, None)

    with pytest.raises(GitHubError) as raised:
        client_with(SimpleNamespace(get_pulls=fail)).pulls()
    assert raised.value.code == "403"


def test_wiki_skipped_when_repo_has_none(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("git must not run"))
    assert client_with(SimpleNamespace(has_wiki=False)).wiki_pages() == {}


def test_wiki_clone_keeps_token_out_of_argv(monkeypatch):
    seen = []

    def fake_run(args, **kwargs):
        seen.append((args, kwargs["env"]))
        target = Path(args[-1])
        put(target / "Home.md", "Welcome")
        put(target / "guides" / "Setup.md", "Install")
        put(target / "image.png", "png")
        put(target / ".git" / "HEAD.md", "ref")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    pages = client_with(SimpleNamespace(has_wiki=True)).wiki_pages()
    assert pages == {"Home": "Welcome", "Setup": "Install"}
    args, env = seen[0]
    assert args[:2] == ["git", "clone"]
    assert f"https://github.com/{REPO}.wiki.git" in args
    assert all(TOKEN not in arg for arg in args)
    header = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {header}"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_wiki_not_found_gives_no_pages(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 128, "", "remote: Repository not found.\nfatal: repository 'x' not found\n"))
    assert client_with(SimpleNamespace(has_wiki=True)).wiki_pages() == {}


def test_wiki_clone_failure_raises_without_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 128, "", "fatal: unable to access: Could not resolve host\n"))
    with pytest.raises(GitHubError) as raised:
        client_with(SimpleNamespace(has_wiki=True)).wiki_pages()
    assert raised.value.code == "wiki clone exit 128"
