"""Stateless Attio export - one Markdown file per record of every object, with its notes and tasks, rewritten only
when its text changed."""

import re
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import httpx

from mdexport.github_export import remove_unkept
from mdexport.linear_export import _write_changed
from mdexport.linear_render import _tidy, one_line
from mdexport.slack_export import _escape_line

API_BASE = "https://api.attio.com/v2"
STAMP = "%Y-%m-%dT%H:%M:%SZ"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SLUG = re.compile(r"[a-z][a-z0-9_]*")
UNEXPECTED = "unexpected identifier"
RETRIES = 5
RECORD_PAGE = 500
NOTE_PAGE = 50
TASK_PAGE = 500
# Attributes that say nothing a reader searches for: the record's own id and the enrichment's image links.
SKIPPED_ATTRIBUTES = {"record_id", "logo_url", "avatar_url"}
NAME_ATTRIBUTES = ("name", "email_addresses", "domains")

_RECORD_FILE = re.compile(UUID.pattern + r"\.md")
_ADDRESS = ("line_1", "line_2", "line_3", "line_4", "locality", "region", "postcode", "country_code")


class AttioError(Exception):
    """A failed Attio call; `code` is Attio's error code, `HTTP <status>` or the error's type, never the token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ExportError(Exception):
    """Attio could not be listed; nothing was written or deleted."""


class AttioClient:
    """The REST API's reads, retrying 429, 5xx and network errors."""

    def __init__(self, token: str, sleep=time.sleep, transport=None):
        self._http = httpx.Client(base_url=API_BASE, headers={"Authorization": f"Bearer {token}"}, timeout=60,
                                  transport=transport)
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kwargs) -> list[dict]:
        code = "unknown"
        for attempt in range(RETRIES):
            try:
                resp = self._http.request(method, path, **kwargs)
            except httpx.HTTPError as e:
                code = type(e).__name__
            else:
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                if resp.status_code == 200 and isinstance(body, dict) and isinstance(body.get("data"), list):
                    return body["data"]
                code = f"HTTP {resp.status_code}"
                if isinstance(body, dict) and isinstance(body.get("code"), str) and body["code"]:
                    code = f"{code} {body['code']}"
                if resp.status_code == 200 or (resp.status_code != 429 and resp.status_code < 500):
                    break
            if attempt < RETRIES - 1:
                self._sleep(2 ** attempt)
        raise AttioError(code)

    def _offset_pages(self, page, size: int) -> list[dict]:
        items, offset = [], 0
        while True:
            data = page(size, offset)
            items.extend(data)
            if len(data) < size:
                return items
            offset += size

    def objects(self) -> list[dict]:
        return self._call("GET", "/objects")

    def attributes(self, object_slug: str) -> list[dict]:
        return self._call("GET", f"/objects/{object_slug}/attributes")

    def records(self, object_slug: str) -> list[dict]:
        return self._offset_pages(lambda limit, offset: self._call(
            "POST", f"/objects/{object_slug}/records/query", json={"limit": limit, "offset": offset}), RECORD_PAGE)

    def notes(self) -> list[dict]:
        return self._offset_pages(lambda limit, offset: self._call(
            "GET", "/notes", params={"limit": limit, "offset": offset}), NOTE_PAGE)

    def tasks(self) -> list[dict]:
        return self._offset_pages(lambda limit, offset: self._call(
            "GET", "/tasks", params={"limit": limit, "offset": offset}), TASK_PAGE)

    def members(self) -> list[dict]:
        return self._call("GET", "/workspace_members")


def make_client(token: str) -> AttioClient:
    return AttioClient(token)


def _member_names(members: list[dict]) -> dict[str, str]:
    names = {}
    for member in members:
        member_id = (member.get("id") or {}).get("workspace_member_id")
        full = one_line(f"{member.get('first_name') or ''} {member.get('last_name') or ''}")
        if member_id:
            names[member_id] = full or one_line(member.get("email_address")) or member_id
    return names


def _actor(actor: dict | None, members: dict[str, str]) -> str:
    """A workspace member's name; empty for an API token, an app or the system, which name nobody."""
    actor = actor or {}
    return members.get(actor.get("referenced_actor_id") or actor.get("id"), "")


def _when(stamp: str | None) -> str:
    """An ISO time as `YYYY-MM-DD HH:MM`, a date alone when it has no time, empty for None."""
    if not stamp:
        return ""
    return stamp[:16].replace("T", " ") if "T" in stamp else stamp[:10]


def format_value(value: dict, kind: str, names: dict, members: dict[str, str]) -> str:
    """One attribute value as one line of text, empty when it holds nothing readable."""
    kind = value.get("attribute_type") or kind
    if kind == "checkbox":
        return "yes" if value.get("value") else "no"
    if kind == "timestamp":
        return _when(value.get("value"))
    if kind == "currency":
        amount = value.get("currency_value")
        return "" if amount is None else one_line(f"{amount} {value.get('currency_code') or ''}")
    if kind == "status":
        return one_line((value.get("status") or {}).get("title"))
    if kind == "select":
        return one_line((value.get("option") or {}).get("title"))
    if kind == "record-reference":
        target = value.get("target_record_id")
        return names.get((value.get("target_object"), target)) or one_line(target)
    if kind == "actor-reference":
        return _actor(value, members)
    if kind == "location":
        return ", ".join(filter(None, (one_line(value.get(key)) for key in _ADDRESS)))
    if kind == "domain":
        return one_line(value.get("domain"))
    if kind == "email-address":
        return one_line(value.get("email_address"))
    if kind == "phone-number":
        return one_line(value.get("original_phone_number") or value.get("phone_number"))
    if kind == "personal-name":
        return one_line(value.get("full_name")) or one_line(
            f"{value.get('first_name') or ''} {value.get('last_name') or ''}")
    if kind == "interaction":
        return one_line(f"{value.get('interaction_type') or ''} {_when(value.get('interacted_at'))}")
    raw = value.get("value")
    if raw is None or isinstance(raw, (dict, list)):
        return ""
    return one_line(str(raw))


def _values(record: dict, slug: str, kind: str, names: dict, members: dict[str, str]) -> list[str]:
    values = ((record.get("values") or {}).get(slug)) or []
    return [text for text in (format_value(v, kind, names, members) for v in values if isinstance(v, dict)) if text]


def record_name(record: dict, kinds: dict[str, str], members: dict[str, str]) -> str:
    """The record's name, else its first email address or domain, else `Untitled`."""
    for slug in NAME_ATTRIBUTES:
        if found := _values(record, slug, kinds.get(slug, ""), {}, members):
            return found[0]
    return "Untitled"


def _record_id(record: dict) -> str | None:
    return (record.get("id") or {}).get("record_id")


def _note_passage(heading: str, url: str, note: dict, members: dict[str, str]) -> list[str]:
    title = one_line(note.get("title")) or "Untitled note"
    meta = " · ".join(filter(None, (_when(note.get("created_at"))[:10], _actor(note.get("created_by_actor"), members))))
    body = _tidy((note.get("content_markdown") or note.get("content_plaintext") or "").splitlines())
    return [f"{heading} › Note: {title}", url, *([_escape_line(meta)] if meta else []), *body]


def _task_line(task: dict, members: dict[str, str]) -> str:
    box = "[x]" if task.get("is_completed") else "[ ]"
    parts = [one_line(task.get("content_plaintext")) or "Untitled task"]
    if due := _when(task.get("deadline_at"))[:10]:
        parts.append(f"due {due}")
    if people := ", ".join(filter(None, (_actor(a, members) for a in task.get("assignees") or []))):
        parts.append(people)
    return f"- {box} " + " · ".join(parts)


def render_record(record: dict, obj: dict, attributes: list[dict], names: dict, members: dict[str, str],
                  notes: list[dict] = (), tasks: list[dict] = ()) -> str:
    """A record's file text: its header, a passage for its attributes, one per note, and one for its tasks."""
    kinds = {a.get("api_slug"): a.get("type") or "" for a in attributes}
    name, url = record_name(record, kinds, members), record["web_url"]
    noun = one_line(obj.get("singular_noun")) or one_line(obj.get("api_slug"))
    heading = f"## {name} · {noun}"
    head = f"# {name} · {noun}\n{url}\nattio {obj.get('api_slug')} · created {_when(record.get('created_at'))[:10]}"
    lines = []
    for attribute in attributes:
        slug = attribute.get("api_slug")
        if attribute.get("is_archived") or slug in SKIPPED_ATTRIBUTES:
            continue
        if found := _values(record, slug, attribute.get("type") or "", names, members):
            lines.append(_escape_line(f"{one_line(attribute.get('title')) or slug}: {'; '.join(found)}"))
    passages = [[heading, url, *lines]]
    for note in sorted(notes, key=lambda n: (n.get("created_at") or "", str((n.get("id") or {}).get("note_id")))):
        passages.append(_note_passage(heading, url, note, members))
    if tasks:
        ordered = sorted(tasks, key=lambda t: (bool(t.get("is_completed")), t.get("deadline_at") or "~",
                                               str((t.get("id") or {}).get("task_id"))))
        passages.append([f"{heading} › Tasks", url, *(_task_line(task, members) for task in ordered)])
    return "\n\n".join([head, *("\n".join(passage) for passage in passages)]) + "\n"


def _remove_unkept(out: Path, keep: set[tuple[str, str]]) -> list[str]:
    """Delete record files not kept from object folders, then those folders left empty; return the record ids."""
    removed = []
    groups = {group for group, _ in keep}
    for folder in sorted(out.iterdir()):
        if not (UUID.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink()):
            continue
        names = {name for group, name in keep if group == folder.name} if folder.name in groups else set()
        removed += remove_unkept(folder, _RECORD_FILE, names)
        if not any(folder.iterdir()):
            folder.rmdir()
    return sorted(removed)


def _object_id(obj: dict) -> str | None:
    return (obj.get("id") or {}).get("object_id")


def export(client, out: Path, *, exclude: Iterable[str] = (), now: datetime | None = None) -> dict:
    """Export every record of every object into `out/<object id>/<record id>.md` and return the run's report."""
    excluded = set(exclude)
    unreadable: dict = {}
    try:
        members = _member_names(client.members())
        objects = []
        for obj in sorted(client.objects(), key=lambda o: str(o.get("api_slug"))):
            object_id, slug = _object_id(obj), obj.get("api_slug")
            if object_id in excluded or slug in excluded:
                continue
            if not (isinstance(object_id, str) and UUID.fullmatch(object_id) and isinstance(slug, str)
                    and SLUG.fullmatch(slug)):
                unreadable[str(object_id or slug)] = UNEXPECTED
                continue
            objects.append((obj, client.attributes(slug), client.records(slug)))
        notes, tasks = client.notes(), client.tasks()
    except AttioError as e:
        raise ExportError(f"Attio listing failed: {e.code}") from e

    names: dict = {}
    kept = []
    for obj, attributes, records in objects:
        kinds = {a.get("api_slug"): a.get("type") or "" for a in attributes}
        for record in records:
            record_id = _record_id(record)
            if record_id in excluded:
                continue
            if not (isinstance(record_id, str) and UUID.fullmatch(record_id) and record.get("web_url")):
                unreadable[str(record_id)] = UNEXPECTED
                continue
            names[(obj["api_slug"], record_id)] = record_name(record, kinds, members)
            kept.append((obj, attributes, record))

    notes_by, tasks_by = {}, {}
    for note in notes:
        notes_by.setdefault((note.get("parent_object"), note.get("parent_record_id")), []).append(note)
    for task in tasks:
        for link in task.get("linked_records") or []:
            tasks_by.setdefault((link.get("target_object_id"), link.get("target_record_id")), []).append(task)

    rendered, keep = [], set()
    note_count = task_count = 0
    for obj, attributes, record in kept:
        object_id, record_id = _object_id(obj), _record_id(record)
        file_key = (object_id, f"{record_id}.md")
        # A record that failed to render keeps its old file, so its path counts as kept either way.
        keep.add(file_key)
        record_notes = notes_by.get((obj["api_slug"], record_id), [])
        record_tasks = tasks_by.get((object_id, record_id), [])
        try:
            text = render_record(record, obj, attributes, names, members, record_notes, record_tasks)
        except Exception as e:
            unreadable[record_id] = f"render failed: {type(e).__name__}"
            continue
        rendered.append((file_key, text, text.encode("utf-8")))
        note_count += len(record_notes)
        task_count += len(record_tasks)

    out.mkdir(parents=True, exist_ok=True)
    written = sum(_write_changed(out / group / name, text, data) for (group, name), text, data in rendered)
    return {
        "objects": len({group for (group, _), _, _ in rendered}),
        "records": len(rendered),
        "notes": note_count,
        "tasks": task_count,
        "sections": sum(line.startswith("## ") for _, text, _ in rendered for line in text.splitlines()),
        "written": written,
        "removed": _remove_unkept(out, keep) if keep else [],
        "unreadable": unreadable,
        "removal_skipped": not keep,  # a run that keeps no record never deletes anything
        "excluded": sorted(excluded),
        "finished_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP),
    }
