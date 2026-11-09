import copy
import json
from datetime import datetime, timezone

import httpx
import pytest
from click.testing import CliRunner

from mdexport import granola_export
from mdexport.cli import cli
from mdexport.granola_export import ExportError, GranolaClient, GranolaError, export, render_note

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
A, B, C = "not_aaaaaaaaaaaaaa", "not_bbbbbbbbbbbbbb", "not_cccccccccccccc"
FOLDER = "fol_ffffffffffffff"


def listed(note_id, updated="2026-11-03T16:40:53.658Z", **fields):
    return {"id": note_id, "object": "note", "title": "Kick off", "created_at": "2026-11-03T15:59:23.384Z",
            "updated_at": updated, "deleted_at": None, **fields}


def full(note_id, updated="2026-11-03T16:40:53.658Z", **fields):
    note = {**listed(note_id, updated), "web_url": f"https://notes.granola.ai/d/{note_id}",
            "owner": {"name": "Diona Doli", "email": "diona@tryformula.co"},
            "attendees": [{"name": "Diona Doli", "email": "diona@tryformula.co"},
                          {"name": "Jacob W", "email": "jacob@maximus.com"}],
            "calendar_event": {"event_title": "Formula x Maximus", "scheduled_start_time": "2026-11-02T21:00:00Z",
                               "scheduled_end_time": "2026-11-02T22:00:00Z",
                               "invitees": [{"email": "jacob@maximus.com"}, {"email": "nick@maximus.com"}]},
            "folder_membership": [{"id": FOLDER, "name": "Client calls"}],
            "summary_markdown": "### Kick off\n\n- Focus: testosterone flows\n\n### Next Steps\n\n- Send access\n",
            "private_notes_markdown": "SECRET own notes", "private_notes_text": "SECRET own notes",
            "transcript": [
                {"speaker": {"source": "microphone", "attribution": "me"}, "text": "Hello.",
                 "start_time": "2026-11-02T20:59:10Z"},
                {"speaker": {"source": "microphone", "attribution": "me"}, "text": "Ready?",
                 "start_time": "2026-11-02T20:59:12Z"},
                {"speaker": {"source": "speaker", "name": "Jacob W"}, "text": "# yes",
                 "start_time": "2026-11-02T20:59:20Z"},
            ]}
    note.update(fields)
    return note


class FakeGranola:
    def __init__(self, notes, folders=None):
        self.full = {n["id"]: n for n in notes}
        self.folders = folders or {}
        self.fail: dict[str, GranolaError] = {}
        self.calls: list = []

    def notes(self, folder_id=None):
        self.calls.append(("notes", folder_id))
        if "notes" in self.fail:
            raise self.fail["notes"]
        ids = self.folders.get(folder_id, []) if folder_id else list(self.full)
        return [{k: self.full[i][k] for k in ("id", "title", "created_at", "updated_at", "deleted_at")} for i in ids]

    def note(self, note_id):
        self.calls.append(("note", note_id))
        if note_id in self.fail:
            raise self.fail[note_id]
        return copy.deepcopy(self.full[note_id])


def run(fake, out, **kw):
    return export(fake, out, now=NOW, **kw)


def test_render_keeps_summary_and_transcript_and_drops_private_notes():
    text = render_note(full(A))
    url = f"https://notes.granola.ai/d/{A}"
    assert text == (
        f"# Kick off · 2026-11-02\n{url}\ngranola meeting · updated 2026-11-03T16:40:53.658Z · v1\n\n"
        f"## Kick off · 2026-11-02\n{url}\nWhen: 2026-11-02 21:00-22:00 UTC\nOwner: Diona Doli\n"
        "People: Jacob W, nick@maximus.com\nFolders: Client calls\n- Focus: testosterone flows\n\n"
        f"## Kick off · 2026-11-02 › Next Steps\n{url}\n- Send access\n\n"
        f"## Kick off · 2026-11-02 › Transcript from 20:59 UTC\n{url}\n"
        "Diona Doli (20:59:10): Hello. Ready?\nJacob W (20:59:20): # yes\n")
    assert "SECRET" not in text


def test_long_transcript_splits_into_passages(monkeypatch):
    monkeypatch.setattr(granola_export, "TRANSCRIPT_CHARS", 30)
    segs = [{"speaker": {"name": n}, "text": "x" * 20, "start_time": f"2026-11-02T21:0{i}:00Z"}
            for i, n in enumerate(["A", "B", "C"])]
    text = render_note(full(A, transcript=segs))
    assert [line for line in text.splitlines() if "Transcript" in line] == [
        "## Kick off · 2026-11-02 › Transcript from 21:00 UTC", "## Kick off · 2026-11-02 › Transcript from 21:01 UTC",
        "## Kick off · 2026-11-02 › Transcript from 21:02 UTC"]


def test_first_run_writes_then_an_unchanged_meeting_is_not_read(tmp_path):
    fake = FakeGranola([full(A), full(B)])
    assert run(fake, tmp_path) == {"meetings": 2, "read": 2, "written": 2, "removed": [], "unreadable": {},
                                   "removal_skipped": False, "excluded": [], "finished_at": "2026-11-09T18:00:00Z"}
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{A}.md", f"{B}.md"]
    fake.calls.clear()
    assert run(fake, tmp_path)["read"] == 0
    assert fake.calls == [("notes", None)]


def test_an_updated_meeting_is_read_again(tmp_path):
    fake = FakeGranola([full(A)])
    run(fake, tmp_path)
    fake.full[A] = full(A, updated="2026-11-04T10:00:00Z", title="Renamed")
    report = run(fake, tmp_path)
    assert (report["read"], report["written"]) == (1, 1)
    assert (tmp_path / f"{A}.md").read_text().startswith("# Renamed")


def test_deleted_excluded_and_unlisted_meetings_are_removed(tmp_path):
    fake = FakeGranola([full(A), full(B), full(C)], folders={FOLDER: [C]})
    run(fake, tmp_path)
    (tmp_path / "notes.md").write_text("mine")
    fake.full[A]["deleted_at"] = "2026-11-05T00:00:00Z"
    report = run(fake, tmp_path, exclude=[FOLDER])
    assert report["removed"] == [A, C] and report["excluded"] == [FOLDER]
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{B}.md", "notes.md"]


def test_a_failed_read_keeps_the_old_file_and_nothing_is_removed_when_nothing_is_kept(tmp_path):
    fake = FakeGranola([full(A)])
    run(fake, tmp_path)
    fake.full[A] = full(A, updated="2026-11-04T10:00:00Z")
    fake.fail[A] = GranolaError("HTTP 500")
    report = run(fake, tmp_path)
    assert report["unreadable"] == {A: "read failed: HTTP 500"} and (tmp_path / f"{A}.md").exists()
    report = run(FakeGranola([]), tmp_path)
    assert report["removal_skipped"] and (tmp_path / f"{A}.md").exists()


def test_listing_failure_changes_nothing(tmp_path):
    fake = FakeGranola([full(A)])
    fake.fail["notes"] = GranolaError("HTTP 401")
    with pytest.raises(ExportError, match="Granola listing failed: HTTP 401"):
        run(fake, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unexpected_id_is_unreadable(tmp_path):
    fake = FakeGranola([full("../escape")])
    assert run(fake, tmp_path)["unreadable"] == {"../escape": "unexpected identifier"}


def client(handler, sleeps=None):
    return GranolaClient("grn_key", sleep=(sleeps.append if sleeps is not None else lambda s: None),
                         transport=httpx.MockTransport(handler))


def test_client_pages_and_retries():
    seen, sleeps = [], []

    def handler(request):
        seen.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer grn_key"
        if len(seen) == 1:
            return httpx.Response(429)
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"notes": [{"id": A}], "hasMore": True, "cursor": "c2"})
        return httpx.Response(200, json={"notes": [{"id": B}], "hasMore": False, "cursor": None})

    assert [n["id"] for n in client(handler, sleeps).notes()] == [A, B]
    assert sleeps == [1] and seen[-1].endswith("page_size=30&cursor=c2")


def test_client_reads_a_large_note_with_its_transcript_paged():
    def handler(request):
        if request.url.path.endswith("/transcript"):
            if "cursor" in request.url.params:
                return httpx.Response(200, json={"transcript": [{"text": "b"}], "hasMore": False})
            return httpx.Response(200, json={"transcript": [{"text": "a"}], "hasMore": True, "cursor": "t2"})
        if request.url.params.get("include") == "transcript":
            return httpx.Response(413)
        return httpx.Response(200, json={"id": A})

    assert client(handler).note(A) == {"id": A, "transcript": [{"text": "a"}, {"text": "b"}]}


def test_client_error_never_carries_the_key():
    with pytest.raises(GranolaError) as e:
        client(lambda r: httpx.Response(401, text="bad grn_key")).notes()
    assert e.value.code == "HTTP 401" and "grn_key" not in str(e.value)


def test_cli_needs_the_token_and_checks_excludes(tmp_path, monkeypatch):
    monkeypatch.delenv("GRANOLA_TOKEN", raising=False)
    args = ["export", "granola", "-o", str(tmp_path / "out"), "--report", str(tmp_path / "r.json")]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1 and "GRANOLA_TOKEN is not set" in result.output
    monkeypatch.setenv("GRANOLA_TOKEN", "grn_key")
    result = CliRunner().invoke(cli, [*args, "--exclude", "spa_x"])
    assert result.exit_code == 1 and "--exclude must be a Granola note or folder id: spa_x" in result.output


def test_cli_writes_the_report(tmp_path, monkeypatch):
    fake = FakeGranola([full(A)])
    fake.close = lambda: None
    tokens = []
    monkeypatch.setattr(granola_export, "make_client", lambda token: tokens.append(token) or fake)
    monkeypatch.setenv("GRANOLA_TOKEN", "grn_key")
    result = CliRunner().invoke(cli, ["export", "granola", "-o", str(tmp_path / "out"),
                                      "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 0, result.output
    assert result.output == "Exported 1 meetings (1 read, 1 written), 0 unreadable\n"
    assert json.loads((tmp_path / "r.json").read_text())["meetings"] == 1 and tokens == ["grn_key"]
