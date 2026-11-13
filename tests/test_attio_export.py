import copy
import json
from datetime import datetime, timezone

import httpx
import pytest
from click.testing import CliRunner

from mdexport import attio_export
from mdexport.attio_export import AttioClient, AttioError, ExportError, export, render_record
from mdexport.cli import cli

NOW = datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)
PEOPLE, COMPANIES = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
ADA, BOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ACME = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
JAY = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def obj(object_id, slug, noun):
    return {"id": {"object_id": object_id}, "api_slug": slug, "singular_noun": noun, "plural_noun": noun + "s"}


def attr(slug, title, kind, archived=False):
    return {"api_slug": slug, "title": title, "type": kind, "is_archived": archived}


def record(oid, rid, noun, /, **values):
    return {"id": {"object_id": oid, "record_id": rid}, "created_at": "2026-10-01T10:00:00.000Z",
            "web_url": f"https://app.attio.com/formula/{noun}/{rid}/overview", "values": values}


PEOPLE_ATTRS = [attr("record_id", "Record ID", "text"), attr("name", "Name", "personal-name"),
                attr("email_addresses", "Email addresses", "email-address"), attr("company", "Company", "record-reference"),
                attr("old", "Old", "text", archived=True), attr("owner", "Owner", "actor-reference")]
COMPANY_ATTRS = [attr("name", "Name", "text"), attr("domains", "Domains", "domain"), attr("stage", "Stage", "status"),
                 attr("arr", "ARR", "currency"), attr("hq", "HQ", "location"), attr("vip", "VIP", "checkbox")]


def ada(**extra):
    return record(PEOPLE, ADA, "person",
                  record_id=[{"value": ADA}],
                  name=[{"attribute_type": "personal-name", "full_name": "Ada Lovelace"}],
                  email_addresses=[{"attribute_type": "email-address", "email_address": "ada@acme.com"},
                                   {"attribute_type": "email-address", "email_address": "ada@home.org"}],
                  company=[{"attribute_type": "record-reference", "target_object": "companies",
                            "target_record_id": ACME}],
                  old=[{"value": "gone"}],
                  owner=[{"attribute_type": "actor-reference", "referenced_actor_type": "workspace-member",
                          "referenced_actor_id": JAY}], **extra)


def acme():
    return record(COMPANIES, ACME, "company",
                  name=[{"attribute_type": "text", "value": "# Acme"}],
                  domains=[{"attribute_type": "domain", "domain": "acme.com"}],
                  stage=[{"attribute_type": "status", "status": {"title": "Proposal"}}],
                  arr=[{"attribute_type": "currency", "currency_value": 12000, "currency_code": "USD"}],
                  hq=[{"attribute_type": "location", "locality": "Austin", "region": "TX", "country_code": "US"}],
                  vip=[{"attribute_type": "checkbox", "value": True}])


def note(note_id, parent_object, parent_record, title, markdown, created="2026-11-02T09:00:00.000Z"):
    return {"id": {"note_id": note_id}, "parent_object": parent_object, "parent_record_id": parent_record,
            "title": title, "content_markdown": markdown, "content_plaintext": markdown,
            "created_by_actor": {"type": "workspace-member", "id": JAY}, "created_at": created}


def task(task_id, content, links, done=False, due=None):
    return {"id": {"task_id": task_id}, "content_plaintext": content, "is_completed": done, "deadline_at": due,
            "linked_records": [{"target_object_id": o, "target_record_id": r} for o, r in links],
            "assignees": [{"referenced_actor_type": "workspace-member", "referenced_actor_id": JAY}]}


class FakeAttio:
    def __init__(self):
        self.objects_list = [obj(PEOPLE, "people", "Person"), obj(COMPANIES, "companies", "Company")]
        self.attrs = {"people": PEOPLE_ATTRS, "companies": COMPANY_ATTRS}
        self.recs = {"people": [ada()], "companies": [acme()]}
        self.notes_list = [note("n1", "people", ADA, "Intro call", "## Agenda\n\n- pricing\n```\nraw\n")]
        self.tasks_list = [task("t1", "Send proposal", [(PEOPLE, ADA), (COMPANIES, ACME)], due="2026-11-20T00:00:00Z"),
                           task("t2", "Book call", [(PEOPLE, ADA)], done=True)]
        self.fail: dict[str, AttioError] = {}

    def _maybe(self, name):
        if name in self.fail:
            raise self.fail[name]

    def members(self):
        self._maybe("members")
        return [{"id": {"workspace_member_id": JAY}, "first_name": "Jay", "last_name": "Shek",
                 "email_address": "jay@tryformula.co"}]

    def objects(self):
        self._maybe("objects")
        return copy.deepcopy(self.objects_list)

    def attributes(self, slug):
        return copy.deepcopy(self.attrs[slug])

    def records(self, slug):
        self._maybe(f"records:{slug}")
        return copy.deepcopy(self.recs[slug])

    def notes(self):
        self._maybe("notes")
        return copy.deepcopy(self.notes_list)

    def tasks(self):
        return copy.deepcopy(self.tasks_list)


def files(out):
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))


def test_render_person_with_notes_and_tasks():
    fake = FakeAttio()
    names = {("companies", ACME): "Acme"}
    members = {JAY: "Jay Shek"}
    text = render_record(ada(), fake.objects_list[0], PEOPLE_ATTRS, names, members, fake.notes_list,
                         fake.tasks_list)
    url = f"https://app.attio.com/formula/person/{ADA}/overview"
    assert text == (
        f"# Ada Lovelace · Person\n{url}\nattio people · created 2026-10-01\n\n"
        f"## Ada Lovelace · Person\n{url}\nName: Ada Lovelace\nEmail addresses: ada@acme.com; ada@home.org\n"
        "Company: Acme\nOwner: Jay Shek\n\n"
        f"## Ada Lovelace · Person › Note: Intro call\n{url}\n2026-11-02 · Jay Shek\n\\## Agenda\n\n- pricing\n"
        "\\```\nraw\n\n"
        f"## Ada Lovelace · Person › Tasks\n{url}\n- [ ] Send proposal · due 2026-11-20 · Jay Shek\n"
        "- [x] Book call · Jay Shek\n")


def test_api_token_actors_and_image_links_are_left_out():
    rec = record(PEOPLE, ADA, "person",
                 name=[{"attribute_type": "personal-name", "full_name": "Ada"}],
                 created_by=[{"attribute_type": "actor-reference", "referenced_actor_type": "api-token",
                              "referenced_actor_id": "tok"}],
                 avatar_url=[{"attribute_type": "text", "value": "https://img/ada.png"}])
    attrs = [attr("name", "Name", "personal-name"), attr("created_by", "Created by", "actor-reference"),
             attr("avatar_url", "Avatar URL", "text")]
    bot_note = note("n2", "people", ADA, "Audit", "body")
    bot_note["created_by_actor"] = {"type": "api-token", "id": "tok"}
    text = render_record(rec, obj(PEOPLE, "people", "Person"), attrs, {}, {JAY: "Jay Shek"}, [bot_note])
    assert "Created by" not in text and "Avatar URL" not in text and "api-token" not in text
    assert "› Note: Audit\nhttps://app.attio.com/formula/person/" in text and "\n2026-11-02\nbody\n" in text


def test_render_company_value_types_and_escaped_name_line():
    text = render_record(acme(), obj(COMPANIES, "companies", "Company"), COMPANY_ATTRS, {}, {})
    assert "Name: # Acme\nDomains: acme.com\nStage: Proposal\nARR: 12000 USD\nHQ: Austin, TX, US\nVIP: yes\n" in text
    assert text.startswith("# # Acme · Company\n")


def test_export_writes_one_file_per_record_and_reports(tmp_path):
    report = export(FakeAttio(), tmp_path, now=NOW)
    assert files(tmp_path) == [f"{PEOPLE}/{ADA}.md", f"{COMPANIES}/{ACME}.md"]
    assert report == {"objects": 2, "records": 2, "notes": 1, "tasks": 3, "sections": 5, "written": 2,
                      "removed": [], "unreadable": {}, "removal_skipped": False, "excluded": [],
                      "finished_at": "2026-11-13T03:00:00Z"}
    person = (tmp_path / PEOPLE / f"{ADA}.md").read_text()
    assert "Company: # Acme" in person


def test_second_run_writes_nothing_unchanged(tmp_path):
    export(FakeAttio(), tmp_path, now=NOW)
    assert export(FakeAttio(), tmp_path, now=NOW)["written"] == 0


def test_records_no_longer_listed_or_excluded_are_removed(tmp_path):
    export(FakeAttio(), tmp_path, now=NOW)
    stray = tmp_path / PEOPLE / "notes.md"
    stray.write_text("keep")
    fake = FakeAttio()
    fake.recs["people"] = []
    report = export(fake, tmp_path, exclude=["companies"], now=NOW)
    assert report["removal_skipped"] is True and report["removed"] == []
    fake.recs["people"] = [record(PEOPLE, BOB, "person", name=[{"full_name": "Bob", "attribute_type": "personal-name"}])]
    report = export(fake, tmp_path, exclude=["companies"], now=NOW)
    assert report["removed"] == [ADA, ACME]
    assert files(tmp_path) == [f"{PEOPLE}/{BOB}.md", f"{PEOPLE}/notes.md"]
    assert not (tmp_path / COMPANIES).exists()


def test_excluded_record_id_is_left_out(tmp_path):
    report = export(FakeAttio(), tmp_path, exclude=[ADA], now=NOW)
    assert files(tmp_path) == [f"{COMPANIES}/{ACME}.md"]
    assert report["excluded"] == [ADA]


def test_record_with_unexpected_id_is_unreadable(tmp_path):
    fake = FakeAttio()
    fake.recs["people"].append(record(PEOPLE, "../escape", "person"))
    report = export(fake, tmp_path, now=NOW)
    assert report["unreadable"] == {"../escape": "unexpected identifier"}
    assert len(files(tmp_path)) == 2


@pytest.mark.parametrize("failing", ["members", "objects", "records:people", "notes"])
def test_a_failed_listing_writes_and_deletes_nothing(tmp_path, failing):
    export(FakeAttio(), tmp_path, now=NOW)
    before = files(tmp_path)
    fake = FakeAttio()
    fake.fail[failing] = AttioError("HTTP 403 forbidden")
    with pytest.raises(ExportError, match="Attio listing failed: HTTP 403 forbidden"):
        export(fake, tmp_path, now=NOW)
    assert files(tmp_path) == before


def test_client_pages_records_and_retries_rate_limits():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        if len(calls) == 1:
            return httpx.Response(429, json={"code": "rate_limit_exceeded"})
        offset = json.loads(request.content)["offset"]
        size = attio_export.RECORD_PAGE if offset == 0 else 1
        return httpx.Response(200, json={"data": [{"n": offset + i} for i in range(size)]})

    sleeps = []
    client = AttioClient("secret", sleep=sleeps.append, transport=httpx.MockTransport(handler))
    records = client.records("people")
    assert len(records) == attio_export.RECORD_PAGE + 1
    assert sleeps == [1]
    assert all(path == "/v2/objects/people/records/query" for _, path, _ in calls)


def test_client_error_names_attio_code_and_never_the_token():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(403, json={"code": "missing_scopes", "message": "secret"})

    client = AttioClient("secret", sleep=lambda _: None, transport=httpx.MockTransport(handler))
    with pytest.raises(AttioError) as info:
        client.objects()
    assert info.value.code == "HTTP 403 missing_scopes"


def test_cli_export_attio(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTIO_TOKEN", "secret")
    fake = FakeAttio()
    fake.close = lambda: None
    monkeypatch.setattr(attio_export, "make_client", lambda token: fake)
    report = tmp_path / "report.json"
    result = CliRunner().invoke(cli, ["export", "attio", "-o", str(tmp_path / "out"), "--report", str(report),
                                      "--exclude", "Users"])
    assert result.exit_code == 0, result.output
    assert result.output == "Exported 2 objects, 2 records (2 written), 1 notes, 3 tasks, 0 unreadable\n"
    assert json.loads(report.read_text())["excluded"] == ["users"]


def test_cli_rejects_bad_exclude_and_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("ATTIO_TOKEN", raising=False)
    args = ["export", "attio", "-o", str(tmp_path / "out"), "--report", str(tmp_path / "r.json")]
    assert "ATTIO_TOKEN is not set" in CliRunner().invoke(cli, args).output
    bad = CliRunner().invoke(cli, [*args, "--exclude", "../x"])
    assert "--exclude must be an Attio object slug, object id or record id: ../x" in bad.output
