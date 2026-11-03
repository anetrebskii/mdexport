"""Stateless Google Docs export - one Markdown file per doc the service account can see, rewritten only when its text changed."""

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import httplib2
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mdexport.github_export import remove_unkept
from mdexport.googledocs import SCOPES, _export_doc_as_md, _list_subfolders, _paginate_files
from mdexport.linear_export import _write_changed
from mdexport.linear_render import _sections, _tidy, one_line

STAMP = "%Y-%m-%dT%H:%M:%SZ"
ID = re.compile(r"[A-Za-z0-9_-]{25,}")
FOLDER = re.compile(r"[A-Za-z0-9_-]+")
UNEXPECTED = "unexpected identifier"

_DOC_FILE = re.compile(r"[A-Za-z0-9_-]{25,}\.md")
_DOCS = "mimeType = 'application/vnd.google-apps.document' and trashed = false"


class GoogleError(Exception):
    """A failed Drive read; `code` is `HTTP <status>` or the error's type, never a credential."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ExportError(Exception):
    """Google Drive could not be listed; nothing was written or deleted."""


def _call(read):
    try:
        return read()
    except HttpError as e:
        raise GoogleError(f"HTTP {e.resp.status}") from None
    except (GoogleAuthError, httplib2.HttpLib2Error, OSError) as e:
        raise GoogleError(type(e).__name__) from None


class DriveClient:
    """Lists Google Docs through the Drive API and exports each one as Markdown."""

    def __init__(self, credentials):
        self._drive = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def docs(self, folders: Iterable[str] = ()) -> list[dict]:
        """Every doc under `folders` and their subfolders, or every doc visible when none is given, once each."""
        def listing():
            if not folders:
                return _paginate_files(self._drive, _DOCS)
            ids = [sub for folder in folders for sub in (folder, *_list_subfolders(self._drive, folder))]
            return [doc for folder in dict.fromkeys(ids)
                    for doc in _paginate_files(self._drive, f"{_DOCS} and '{folder}' in parents")]
        return list({doc["id"]: doc for doc in _call(listing)}.values())

    def markdown(self, doc_id: str, title: str) -> str:
        return _call(lambda: _export_doc_as_md(self._drive, doc_id, title, echo=False))


def make_client(key_file: str) -> DriveClient:
    """A Drive client authenticated as the service account in `key_file`, with read-only scopes."""
    return DriveClient(service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES))


def doc_url(doc: dict) -> str:
    return doc.get("webViewLink") or f"https://docs.google.com/document/d/{doc['id']}/edit"


def render_doc(doc: dict, body: str) -> str:
    """A doc's file text: a header with its title and URL, a passage for its lead, then one per heading section."""
    title, url = one_line(doc.get("name")) or "Untitled", doc_url(doc)
    heading = f"## {title}"
    head = f"# {title}\n{url}\ngoogle doc · updated {(doc.get('modifiedTime') or '')[:10]}"
    lead, sections = _sections(body)
    passages = [[heading, url, *_tidy(lead)]]
    for section, lines in sections:
        if text := _tidy(lines):
            passages.append([f"{heading} › {section}", url, *text])
    return "\n\n".join([head, *("\n".join(passage) for passage in passages)]) + "\n"


def export(client, out: Path, *, folders: Iterable[str] = (), exclude: Iterable[str] = (),
           now: datetime | None = None) -> dict:
    """Export every visible doc, or every doc under `folders`, into `out/<doc id>.md`; return the run's report."""
    try:
        docs = client.docs(list(folders))
    except GoogleError as e:
        raise ExportError(f"Google Drive listing failed: {e.code}") from e
    excluded = set(exclude)
    unreadable: dict = {}
    rendered: list[tuple] = []
    failed: set[str] = set()
    for doc in sorted(docs, key=lambda d: str(d.get("id"))):
        doc_id = doc.get("id")
        if doc_id in excluded:
            continue
        if not (isinstance(doc_id, str) and ID.fullmatch(doc_id)):
            unreadable[str(doc_id)] = UNEXPECTED
            continue
        name = f"{doc_id}.md"
        try:
            text = render_doc(doc, client.markdown(doc_id, one_line(doc.get("name"))))
            rendered.append((name, text, text.encode("utf-8")))
        except GoogleError as e:
            unreadable[doc_id] = f"read failed: {e.code}"
            failed.add(name)
        except Exception as e:
            unreadable[doc_id] = f"render failed: {type(e).__name__}"
            failed.add(name)
    out.mkdir(parents=True, exist_ok=True)
    written = sum(_write_changed(out / name, text, data) for name, text, data in rendered)
    # A doc that failed to read or render keeps its old file, so its name counts as kept for removal.
    keep = {name for name, *_ in rendered} | failed
    report = {
        "docs": len(rendered),
        "sections": sum(line.startswith("## ") for _, text, _ in rendered for line in text.splitlines()),
        "written": written,
        "removed": remove_unkept(out, _DOC_FILE, keep) if keep else [],
        "unreadable": unreadable,
        "removal_skipped": not keep,  # a run that keeps no doc never deletes anything
        "finished_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP),
    }
    return report
