"""Stateless Granola export - one Markdown file per meeting note the key can read, read again only when it changed."""

import re
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import httpx

from mdexport.github_export import remove_unkept
from mdexport.linear_export import _write_changed
from mdexport.linear_render import _sections, _tidy, one_line
from mdexport.slack_export import _escape_line

API_BASE = "https://public-api.granola.ai/v1"
STAMP = "%Y-%m-%dT%H:%M:%SZ"
NOTE = re.compile(r"not_[A-Za-z0-9]{14}")
FOLDER = re.compile(r"fol_[A-Za-z0-9]{14}")
UNEXPECTED = "unexpected identifier"
# Bumped when the file text changes shape, so every meeting is read and written again once.
FORMAT = 1
TRANSCRIPT_CHARS = 4000
RETRIES = 5

_NOTE_FILE = re.compile(r"not_[A-Za-z0-9]{14}\.md")


class GranolaError(Exception):
    """A failed Granola read; `code` is `HTTP <status>` or the error's type, never the key."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ExportError(Exception):
    """Granola could not be listed; nothing was written or deleted."""


class GranolaClient:
    """The public API's note reads, retrying 429, 5xx and network errors."""

    def __init__(self, token: str, sleep=time.sleep, transport=None):
        self._http = httpx.Client(base_url=API_BASE, headers={"Authorization": f"Bearer {token}"}, timeout=60,
                                  transport=transport)
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(RETRIES):
            try:
                resp = self._http.get(path, params=params)
            except httpx.HTTPError as e:
                code = type(e).__name__
            else:
                if resp.status_code == 200:
                    return resp.json()
                code = f"HTTP {resp.status_code}"
                if resp.status_code != 429 and resp.status_code < 500:
                    break
            if attempt < RETRIES - 1:
                self._sleep(2 ** attempt)
        raise GranolaError(code)

    def _pages(self, path: str, key: str, params: dict, size: int) -> list[dict]:
        items, cursor = [], None
        while True:
            data = self._get(path, {**params, "page_size": size, **({"cursor": cursor} if cursor else {})})
            items.extend(data.get(key) or [])
            if not data.get("hasMore") or not data.get("cursor"):
                return items
            cursor = data["cursor"]

    def notes(self, folder_id: str | None = None) -> list[dict]:
        """Every note the key reads, or every note in one folder: id, title, owner, created and updated times."""
        return self._pages("/notes", "notes", {"folder_id": folder_id} if folder_id else {}, 30)

    def note(self, note_id: str) -> dict:
        """One note with its transcript; a note too large to send whole is read with its transcript paged."""
        try:
            return self._get(f"/notes/{note_id}", {"include": "transcript"})
        except GranolaError as e:
            if e.code != "HTTP 413":
                raise
        note = self._get(f"/notes/{note_id}")
        note["transcript"] = self._pages(f"/notes/{note_id}/transcript", "transcript", {}, 100)
        return note


def make_client(token: str) -> GranolaClient:
    return GranolaClient(token)


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def _title(note: dict) -> str:
    event = note.get("calendar_event") or {}
    return one_line(note.get("title")) or one_line(event.get("event_title")) or "Untitled meeting"


def _person(person: dict | None) -> str:
    person = person or {}
    return one_line(person.get("name")) or one_line(person.get("email"))


def _people(note: dict) -> list[str]:
    """Attendees, then calendar invitees not already named, by name or email, the owner left out."""
    owner = ((note.get("owner") or {}).get("email") or "").lower()
    seen, people = set(), []
    invitees = (note.get("calendar_event") or {}).get("invitees") or []
    for person in [*(note.get("attendees") or []), *invitees]:
        email = (person.get("email") or "").lower()
        label = _person(person)
        if not label or (email and email == owner) or (email or label) in seen:
            continue
        seen.add(email or label)
        people.append(label)
    return people


def _speaker(segment: dict, owner: str) -> str:
    speaker = segment.get("speaker") or {}
    if name := one_line(speaker.get("name")):
        return name
    if speaker.get("attribution") == "me" or (not speaker.get("attribution") and speaker.get("source") == "microphone"):
        return owner or "Note owner"
    return one_line(speaker.get("diarization_label")) or "Other speaker"


def _turns(note: dict) -> list[tuple[str, str]]:
    """The transcript as (start HH:MM, line) turns, consecutive segments of one speaker joined."""
    owner = _person(note.get("owner"))
    turns: list[list[str]] = []
    last = None
    for segment in note.get("transcript") or []:
        text = one_line(segment.get("text"))
        if not text:
            continue
        who = _speaker(segment, owner)
        if who == last:
            turns[-1][2] += " " + text
            continue
        start = segment.get("start_time")
        at = _utc(start).strftime("%H:%M:%S") if start else ""
        turns.append([at[:5], f"{who} ({at}):" if at else f"{who}:", text])
        last = who
    return [(at, f"{who} {text}") for at, who, text in turns]


def _transcript_passages(heading: str, url: str, note: dict) -> list[list[str]]:
    """The transcript in passages of about TRANSCRIPT_CHARS, each headed by the time its first turn starts."""
    passages: list[list[str]] = []
    size = TRANSCRIPT_CHARS
    for at, line in _turns(note):
        if size + len(line) > TRANSCRIPT_CHARS:
            label = f"Transcript from {at} UTC" if at else "Transcript"
            passages.append([f"{heading} › {label}", url])
            size = 0
        passages[-1].append(_escape_line(line))
        size += len(line)
    return passages


def _start(note: dict) -> datetime:
    event = note.get("calendar_event") or {}
    return _utc(event.get("scheduled_start_time") or note["created_at"])


def stamp_line(note: dict) -> str:
    """The file's third line, which tells a later run whether the meeting changed since it was written; it uses
    only what the listing returns."""
    return f"granola meeting · updated {note.get('updated_at') or ''} · v{FORMAT}"


def render_note(note: dict) -> str:
    """A meeting's file text: its header, a passage for its details and summary lead, one per summary section,
    then the transcript in passages. The owner's private notes are left out."""
    title, url, start = _title(note), note["web_url"], _start(note)
    heading = f"## {title} · {start.strftime('%Y-%m-%d')}"
    head = f"# {title} · {start.strftime('%Y-%m-%d')}\n{url}\n{stamp_line(note)}"
    when = start.strftime("%Y-%m-%d %H:%M")
    if end := (note.get("calendar_event") or {}).get("scheduled_end_time"):
        when += "-" + _utc(end).strftime("%H:%M")
    details = [f"When: {when} UTC"]
    if owner := _person(note.get("owner")):
        details.append(f"Owner: {owner}")
    if people := _people(note):
        details.append(f"People: {', '.join(people)}")
    if folders := sorted(filter(None, (one_line(f.get("name")) for f in note.get("folder_membership") or []))):
        details.append(f"Folders: {', '.join(folders)}")
    lead, sections = _sections(note.get("summary_markdown") or note.get("summary_text"))
    # Granola's summary often opens with a heading of its own; its lines join the first passage.
    if not _tidy(lead) and sections:
        lead = sections.pop(0)[1]
    passages = [[heading, url, *map(_escape_line, details), *_tidy(lead)]]
    for name, lines in sections:
        if body := _tidy(lines):
            passages.append([f"{heading} › {one_line(name)}", url, *body])
    passages += _transcript_passages(heading, url, note)
    return "\n\n".join([head, *("\n".join(passage) for passage in passages)]) + "\n"


def _current(path: Path, note: dict) -> bool:
    """Whether `path` was written from this very version of the note, by its third line."""
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open(encoding="utf-8") as f:
            lines = [f.readline() for _ in range(3)]
    except (OSError, UnicodeDecodeError):
        return False
    return bool(note.get("updated_at")) and lines[2].rstrip("\n") == stamp_line(note)


def export(client, out: Path, *, exclude: Iterable[str] = (), now: datetime | None = None) -> dict:
    """Export every meeting note the key reads into `out/<note id>.md` and return the run's report."""
    exclude = list(exclude)
    try:
        listed = client.notes()
        excluded = {value for value in exclude if NOTE.fullmatch(value)}
        for folder in (value for value in exclude if FOLDER.fullmatch(value)):
            excluded |= {note.get("id") for note in client.notes(folder)}
    except GranolaError as e:
        raise ExportError(f"Granola listing failed: {e.code}") from e
    unreadable: dict = {}
    rendered: list[tuple] = []
    keep: set[str] = set()
    current = 0
    for note in sorted(listed, key=lambda n: str(n.get("id"))):
        note_id = note.get("id")
        if note.get("deleted_at") or note_id in excluded:
            continue
        if not (isinstance(note_id, str) and NOTE.fullmatch(note_id)):
            unreadable[str(note_id)] = UNEXPECTED
            continue
        name = f"{note_id}.md"
        # A meeting that failed to read or render keeps its old file, so its name counts as kept either way.
        keep.add(name)
        if _current(out / name, note):
            current += 1
            continue
        try:
            text = render_note(client.note(note_id))
            rendered.append((name, text, text.encode("utf-8")))
        except GranolaError as e:
            unreadable[note_id] = f"read failed: {e.code}"
        except Exception as e:
            unreadable[note_id] = f"render failed: {type(e).__name__}"
    out.mkdir(parents=True, exist_ok=True)
    written = sum(_write_changed(out / name, text, data) for name, text, data in rendered)
    return {
        "meetings": current + len(rendered),
        "read": len(rendered),
        "written": written,
        "removed": remove_unkept(out, _NOTE_FILE, keep) if keep else [],
        "unreadable": unreadable,
        "removal_skipped": not keep,  # a run that keeps no meeting never deletes anything
        "excluded": sorted(set(exclude)),
        "finished_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP),
    }
