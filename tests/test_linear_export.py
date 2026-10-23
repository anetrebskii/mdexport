import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mdexport import linear_client, linear_export
from mdexport.linear_client import LinearError
from mdexport.linear_export import ExportError, export
from mdexport.linear_render import render_issue

from .fake_linear import FakeLinear

NOW = datetime(2026, 10, 22, 3, 30, tzinfo=timezone.utc)


def team(key: str, private: bool = False) -> dict:
    return {"key": key, "name": f"{key} team", "private": private}


def task(identifier: str, team: str = "FOR", **fields) -> dict:
    """A task carrying every field `render_issue` reads, None or empty unless given."""
    return {
        "id": f"id-{identifier}", "identifier": identifier, "title": None, "description": None,
        "url": f"https://linear.app/formula/issue/{identifier}", "priorityLabel": None, "estimate": None,
        "trashed": False, "archivedAt": None, "createdAt": None, "updatedAt": None, "startedAt": None,
        "completedAt": None, "canceledAt": None, "state": None, "team": {"key": team, "name": None},
        "assignee": None, "creator": None, "project": None, "projectMilestone": None, "cycle": None,
        "labels": [], "parent": None, "children": [], "relations": [], "inverseRelations": [],
        "attachments": [], "comments": [], **fields,
    }


def comment(cid: str, body: str, created: str) -> dict:
    return {"id": cid, "url": f"https://linear.app/formula/issue/FOR-1#comment-{cid}", "body": body,
            "createdAt": created, "parent": None, "user": {"name": "Jay"}, "botActor": None, "externalUser": None}


def fake_with(*tasks: dict, teams: list[dict] | None = None) -> FakeLinear:
    keys = sorted({t["team"]["key"] for t in tasks})
    return FakeLinear(teams_list=[team(k) for k in keys] if teams is None else teams, issue_list=list(tasks))


def run(fake: FakeLinear, out: Path, **kwargs) -> dict:
    return export(fake, out, now=NOW, **kwargs)


def md_files(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))


def tree(out: Path) -> dict[str, bytes]:
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def put(path: Path, text: str = "old\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_make_client_is_the_clients():
    assert linear_export.make_client is linear_client.make_client


def test_first_run_writes_one_file_per_task(tmp_path):
    for_1, var_2 = task("FOR-1", title="Fix the hero"), task("VAR-2", team="VAR", title="Variant")
    report = run(fake_with(for_1, var_2), tmp_path)
    assert md_files(tmp_path) == ["FOR/FOR-1.md", "VAR/VAR-2.md"]
    assert (tmp_path / "FOR" / "FOR-1.md").read_text(encoding="utf-8") == render_issue(for_1)
    assert (tmp_path / "VAR" / "VAR-2.md").read_text(encoding="utf-8") == render_issue(var_2)
    assert (report["teams"], report["issues"], report["written"]) == (2, 2, 2)
    assert report["removed"] == []
    assert report["removal_skipped"] is False
    assert report["finished_at"] == "2026-10-22T03:30:00Z"


def test_trashed_dropped_archived_kept(tmp_path):
    fake = fake_with(task("FOR-1", trashed=True), task("FOR-2", archivedAt="2026-10-01T09:00:00.000Z"))
    report = run(fake, tmp_path)
    assert md_files(tmp_path) == ["FOR/FOR-2.md"]
    assert report["issues"] == 1


def test_private_team_skipped_and_reported(tmp_path):
    fake = fake_with(task("FOR-1"), task("SEC-1", team="SEC"), teams=[team("FOR"), team("SEC", private=True)])
    report = run(fake, tmp_path)
    assert not (tmp_path / "SEC").exists()
    assert md_files(tmp_path) == ["FOR/FOR-1.md"]
    assert report["skipped_private_teams"] == ["SEC"]


def test_exclude_team_and_task(tmp_path):
    put(tmp_path / "WF" / "WF-1.md")
    put(tmp_path / "FOR" / "FOR-2.md")
    fake = fake_with(task("WF-1", team="WF"), task("FOR-1"), task("FOR-2"))
    report = run(fake, tmp_path, exclude=["WF", "FOR-2"])
    assert md_files(tmp_path) == ["FOR/FOR-1.md"]
    assert not (tmp_path / "WF").exists()
    assert report["excluded"] == ["FOR-2", "WF"]
    assert report["removed"] == ["FOR-2", "WF-1"]


def test_unchanged_file_not_rewritten(tmp_path):
    fake = fake_with(task("FOR-1", title="Same"))
    run(fake, tmp_path)
    path = tmp_path / "FOR" / "FOR-1.md"
    os.utime(path, (1_000_000_000, 1_000_000_000))
    report = run(fake, tmp_path)
    assert report["written"] == 0
    assert path.stat().st_mtime == 1_000_000_000


def test_changed_task_rewritten(tmp_path):
    run(fake_with(task("FOR-1", description="old text")), tmp_path)
    changed = task("FOR-1", description="new text")
    report = run(fake_with(changed), tmp_path)
    assert report["written"] == 1
    text = (tmp_path / "FOR" / "FOR-1.md").read_text(encoding="utf-8")
    assert text == render_issue(changed)
    assert "new text" in text


def test_moved_task_leaves_one_file(tmp_path):
    run(fake_with(task("FOR-5", id="u5")), tmp_path)
    report = run(fake_with(task("VAR-9", team="VAR", id="u5"), task("FOR-1")), tmp_path)
    assert not (tmp_path / "FOR" / "FOR-5.md").exists()
    assert (tmp_path / "VAR" / "VAR-9.md").exists()
    assert md_files(tmp_path) == ["FOR/FOR-1.md", "VAR/VAR-9.md"]
    assert report["removed"] == ["FOR-5"]


def test_gone_task_deleted_and_empty_team_folder_removed(tmp_path):
    run(fake_with(task("FOR-1"), task("VAR-2", team="VAR")), tmp_path)
    report = run(fake_with(task("FOR-1")), tmp_path)
    assert not (tmp_path / "VAR").exists()
    assert md_files(tmp_path) == ["FOR/FOR-1.md"]
    assert report["removed"] == ["VAR-2"]


def test_non_id_names_untouched(tmp_path):
    others = ["notes.md", ".claude/x.md", "FOR/README.md", "FOR/FOR-9.txt", "lower/FOR-3.md"]
    for name in others:
        put(tmp_path / name, f"keep {name}\n")
    before = {name: (tmp_path / name).read_bytes() for name in others}
    run(fake_with(task("FOR-1")), tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in others} == before


def test_symlinked_team_folder_untouched(tmp_path):
    outside, out = tmp_path / "outside", tmp_path / "out"
    put(outside / "FOR-7.md", "outside\n")
    out.mkdir()
    (out / "FOR").symlink_to(outside, target_is_directory=True)
    report = run(fake_with(task("VAR-1", team="VAR")), out)
    assert (outside / "FOR-7.md").read_text(encoding="utf-8") == "outside\n"
    assert (out / "FOR").is_symlink()
    assert report["removed"] == []


@pytest.mark.parametrize("name", ["teams", "issues"])
def test_listing_failure_changes_nothing(tmp_path, name):
    put(tmp_path / "FOR" / "FOR-1.md")
    before = tree(tmp_path)
    fake = fake_with(task("FOR-2"))
    fake.fail[name] = LinearError(401, "AUTHENTICATION_ERROR")
    with pytest.raises(ExportError) as raised:
        run(fake, tmp_path)
    assert str(raised.value) == "Linear listing failed: AUTHENTICATION_ERROR"
    assert tree(tmp_path) == before
    assert fake.calls == (["teams"] if name == "teams" else ["teams", "issues"])


def test_listing_failure_creates_no_output(tmp_path):
    out = tmp_path / "out"
    fake = fake_with(task("FOR-1"))
    fake.fail["issues"] = LinearError(500, "server_error")
    with pytest.raises(ExportError):
        run(fake, out)
    assert not out.exists()


def test_no_task_kept_skips_removal(tmp_path):
    put(tmp_path / "FOR" / "FOR-1.md")
    report = run(FakeLinear(teams_list=[team("FOR")], issue_list=[]), tmp_path)
    assert (tmp_path / "FOR" / "FOR-1.md").read_text(encoding="utf-8") == "old\n"
    assert report["removal_skipped"] is True
    assert report["removed"] == []
    assert report["teams"] == 0


def test_listed_but_none_kept_skips_removal(tmp_path):
    # The listing is not empty, but trashing and exclusion keep nothing, so nothing may be deleted.
    put(tmp_path / "FOR" / "FOR-1.md")
    put(tmp_path / "WF" / "WF-1.md")
    before = tree(tmp_path)
    report = run(fake_with(task("FOR-1", trashed=True), task("WF-1", team="WF")), tmp_path, exclude=["WF"])
    assert tree(tmp_path) == before
    assert report["removal_skipped"] is True
    assert report["removed"] == []
    assert report["issues"] == 0


def test_unsafe_identifier_never_written(tmp_path):
    out = tmp_path / "out"
    fake = fake_with(task("X-1", team="../X"), task("../../evil"), task("for-1"), task("FOR-2"),
                     teams=[team("FOR")])
    report = run(fake, out)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [
        "out", "out/FOR", "out/FOR/FOR-2.md"]
    assert report["unreadable"] == {"X-1": "unexpected identifier", "../../evil": "unexpected identifier",
                                    "for-1": "unexpected identifier"}
    assert report["issues"] == 1


def test_unrenderable_task_reported_and_file_kept(tmp_path):
    put(tmp_path / "FOR" / "FOR-2.md")
    before = (tmp_path / "FOR" / "FOR-2.md").read_bytes()
    report = run(fake_with(task("FOR-1"), task("FOR-2", title="Bad \ud800 title")), tmp_path)
    assert (tmp_path / "FOR" / "FOR-1.md").exists()
    assert (tmp_path / "FOR" / "FOR-2.md").read_bytes() == before
    assert report["unreadable"] == {"FOR-2": "render failed: UnicodeEncodeError"}
    assert report["issues"] == 1


def test_render_error_reported(tmp_path, monkeypatch):
    def render(issue: dict) -> str:
        if issue["identifier"] == "FOR-2":
            raise KeyError("url")
        return render_issue(issue)

    monkeypatch.setattr(linear_export, "render_issue", render)
    report = run(fake_with(task("FOR-1"), task("FOR-2")), tmp_path)
    assert report["unreadable"] == {"FOR-2": "render failed: KeyError"}
    assert md_files(tmp_path) == ["FOR/FOR-1.md"]
    assert report["issues"] == 1


def test_utf8_written(tmp_path):
    run(fake_with(task("FOR-1", title="Hero 🚀 test")), tmp_path)
    assert "🚀" in (tmp_path / "FOR" / "FOR-1.md").read_text(encoding="utf-8")


def test_report_keys(tmp_path):
    comments = [comment("c1", "First", "2026-10-03T11:00:00.000Z"), comment("c2", "Second", "2026-10-03T12:00:00.000Z")]
    report = run(fake_with(task("FOR-1", description="intro\n## Plan\nship it", comments=comments)), tmp_path)
    assert set(report) == {"teams", "issues", "comments", "sections", "written", "removed", "unreadable",
                           "removal_skipped", "skipped_private_teams", "excluded", "finished_at"}
    assert report["comments"] == 2
    assert report["sections"] == 4
