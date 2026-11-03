import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mdexport import googledocs_export
from mdexport.googledocs_export import DriveClient, ExportError, GoogleError, export, render_doc

from .fake_google_docs import FakeDrive

NOW = datetime(2026, 11, 3, 3, 30, tzinfo=timezone.utc)
A = "1AaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
B = "1BbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
C = "1CcccccccccccccccccccccccccccccccccccccccccC"


def doc(doc_id: str, name: str = "Plan", **fields) -> dict:
    return {"id": doc_id, "name": name, "modifiedTime": "2026-10-20T08:00:00.000Z",
            "webViewLink": f"https://docs.google.com/document/d/{doc_id}/edit?usp=drivesdk", **fields}


def run(fake: FakeDrive, out: Path, **kwargs) -> dict:
    return export(fake, out, now=NOW, **kwargs)


def md_files(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))


def tree(out: Path) -> dict[str, bytes]:
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def put(path: Path, text: str = "old\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_first_run_writes_one_file_per_doc(tmp_path):
    fake = FakeDrive([doc(A), doc(B, "Notes")], {A: "Intro\n# Goals\nship\n# Risks\nnone", B: "Just text"})
    report = run(fake, tmp_path)
    assert md_files(tmp_path) == [f"{A}.md", f"{B}.md"]
    assert (tmp_path / f"{A}.md").read_text(encoding="utf-8") == render_doc(doc(A), fake.bodies[A])
    assert report == {"docs": 2, "sections": 4, "written": 2, "removed": [], "unreadable": {},
                      "removal_skipped": False, "finished_at": "2026-11-03T03:30:00Z"}


def test_render_header_and_sections():
    url = f"https://docs.google.com/document/d/{A}/edit?usp=drivesdk"
    assert render_doc(doc(A), "Intro\n\n# **Goals**\nship it\n## Detail\nmore\n# Risks\nnone") == (
        f"# Plan\n{url}\ngoogle doc · updated 2026-10-20\n\n"
        f"## Plan\n{url}\nIntro\n\n"
        f"## Plan › Goals\n{url}\nship it\n\\## Detail\nmore\n\n"
        f"## Plan › Risks\n{url}\nnone\n")


def test_url_falls_back_to_doc_id():
    assert f"https://docs.google.com/document/d/{A}/edit\n" in render_doc({"id": A, "name": "X"}, "")


def test_folders_reach_the_listing(tmp_path):
    fake = FakeDrive([doc(A)])
    run(fake, tmp_path, folders=("f1", "f2"))
    assert fake.calls[0] == ("docs", ("f1", "f2"))


def test_unchanged_file_not_rewritten(tmp_path):
    fake = FakeDrive([doc(A)], {A: "same"})
    run(fake, tmp_path)
    path = tmp_path / f"{A}.md"
    os.utime(path, (1_000_000_000, 1_000_000_000))
    report = run(fake, tmp_path)
    assert report["written"] == 0
    assert path.stat().st_mtime == 1_000_000_000


def test_changed_doc_rewritten(tmp_path):
    fake = FakeDrive([doc(A)], {A: "old text"})
    run(fake, tmp_path)
    fake.bodies[A] = "new text"
    assert run(fake, tmp_path)["written"] == 1
    assert "new text" in (tmp_path / f"{A}.md").read_text(encoding="utf-8")


def test_gone_and_excluded_docs_deleted(tmp_path):
    run(FakeDrive([doc(A), doc(B), doc(C)]), tmp_path)
    report = run(FakeDrive([doc(A), doc(B)]), tmp_path, exclude=[B])
    assert md_files(tmp_path) == [f"{A}.md"]
    assert report["removed"] == sorted([B, C])


def test_non_id_names_untouched(tmp_path):
    others = ["notes.md", "README.md", f"{B}.txt", f"sub/{B}.md", ".state/x.md"]
    for name in others:
        put(tmp_path / name, f"keep {name}\n")
    before = {name: (tmp_path / name).read_bytes() for name in others}
    run(FakeDrive([doc(A)]), tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in others} == before


def test_listing_failure_changes_nothing(tmp_path):
    put(tmp_path / f"{A}.md")
    before = tree(tmp_path)
    fake = FakeDrive([doc(B)])
    fake.fail["docs"] = GoogleError("HTTP 403")
    with pytest.raises(ExportError) as raised:
        run(fake, tmp_path)
    assert str(raised.value) == "Google Drive listing failed: HTTP 403"
    assert tree(tmp_path) == before


def test_listing_failure_creates_no_output(tmp_path):
    fake = FakeDrive()
    fake.fail["docs"] = GoogleError("RefreshError")
    with pytest.raises(ExportError):
        run(fake, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_unreadable_doc_keeps_old_file(tmp_path):
    put(tmp_path / f"{A}.md")
    fake = FakeDrive([doc(A), doc(B)], {B: "fine"})
    fake.fail[A] = GoogleError("HTTP 500")
    report = run(fake, tmp_path)
    assert (tmp_path / f"{A}.md").read_text(encoding="utf-8") == "old\n"
    assert report["unreadable"] == {A: "read failed: HTTP 500"}
    assert report["docs"] == 1


def test_render_error_reported(tmp_path):
    report = run(FakeDrive([doc(A, name="Bad \ud800"), doc(B)]), tmp_path)
    assert report["unreadable"] == {A: "render failed: UnicodeEncodeError"}
    assert md_files(tmp_path) == [f"{B}.md"]


def test_unsafe_id_never_written(tmp_path):
    out = tmp_path / "out"
    report = run(FakeDrive([doc("../../" + A), doc("short"), doc(B)]), out)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == ["out", f"out/{B}.md"]
    assert report["unreadable"] == {"../../" + A: "unexpected identifier", "short": "unexpected identifier"}


def test_no_doc_kept_skips_removal(tmp_path):
    put(tmp_path / f"{A}.md")
    report = run(FakeDrive([doc(A)]), tmp_path, exclude=[A])
    assert (tmp_path / f"{A}.md").read_text(encoding="utf-8") == "old\n"
    assert report["removal_skipped"] is True
    assert report["removed"] == []


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeFiles:
    """Drive `files()`: folders map to their subfolders and docs; `list` reads the folder id out of the query."""

    def __init__(self):
        self.queries = []

    def list(self, q, **kwargs):
        self.queries.append(q)
        if "folder" in q:
            return FakeRequest({"files": [{"id": "sub"}] if q.startswith("'top'") else []})
        if "'top' in parents" in q:
            return FakeRequest({"files": [doc(A)]})
        if "'sub' in parents" in q:
            return FakeRequest({"files": [doc(A), doc(B)]})
        return FakeRequest({"files": [doc(C)]})


def drive_client(files: FakeFiles) -> DriveClient:
    client = DriveClient.__new__(DriveClient)
    client._drive = type("Drive", (), {"files": lambda self: files})()
    return client


def test_client_lists_everything_without_folders():
    files = FakeFiles()
    assert [d["id"] for d in drive_client(files).docs([])] == [C]
    assert files.queries == ["mimeType = 'application/vnd.google-apps.document' and trashed = false"]


def test_client_lists_folders_with_subfolders_once_each():
    assert [d["id"] for d in drive_client(FakeFiles()).docs(["top"])] == [A, B]


def test_make_client_uses_read_only_scopes(monkeypatch, tmp_path):
    seen = {}

    def from_file(path, scopes):
        seen.update(path=path, scopes=scopes)
        return object()

    monkeypatch.setattr(googledocs_export.service_account.Credentials, "from_service_account_file", from_file)
    monkeypatch.setattr(googledocs_export, "build", lambda *a, **kw: "drive")
    googledocs_export.make_client("key.json")
    assert seen["path"] == "key.json"
    assert all(scope.endswith(".readonly") for scope in seen["scopes"])
