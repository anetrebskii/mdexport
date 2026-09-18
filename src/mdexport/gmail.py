"""Gmail exporter - mail threads a Gmail search finds, as Markdown."""

import base64
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import click
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from markdownify import markdownify as html_to_md

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _credentials(cfg: dict) -> Credentials:
    token = cfg.get("token")
    if not token:
        raise click.ClickException("No token stored for this Gmail export. Sign in again.")
    return Credentials(token=token)


def _fail(e: HttpError):
    status = e.resp.status if hasattr(e, "resp") else 0
    if status == 401:
        raise click.ClickException("Google signed you out of this account.")
    if status == 403 and "insufficient" in str(e).lower():
        raise click.ClickException("This sign-in has no access to your mail. Sign in again and allow Notula Collect to read it.")
    raise e


def _call(request):
    for attempt in range(5):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status if hasattr(e, "resp") else 0
            if status in (429, 500, 502, 503) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            _fail(e)


def _slug(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:80] or "thread"


def _header(message: dict, name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name:
            return h.get("value", "")
    return ""


def _body(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and mime == "text/plain":
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace").strip()
    if data and mime == "text/html":
        return html_to_md(base64.urlsafe_b64decode(data).decode("utf-8", "replace")).strip()
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            text = _body(part)
            if text:
                return text
    for part in parts:
        text = _body(part)
        if text:
            return text
    return ""


def _attachments(payload: dict) -> list[str]:
    names = []
    for part in payload.get("parts", []):
        if part.get("filename"):
            names.append(part["filename"])
        names.extend(_attachments(part))
    return names


def _quoted_trimmed(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        if re.match(r"^\s*(>|On .+ wrote:$|-{2,} ?Forwarded message)", line):
            break
        lines.append(line)
    return "\n".join(lines).strip() or text.strip()


def _format(message: dict) -> str:
    sent = _header(message, "date")
    when = ""
    if sent:
        try:
            when = parsedate_to_datetime(sent).astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            when = sent
    lines = [f"**{_header(message, 'from')}** - {when}", ""]
    to = _header(message, "to")
    if to:
        lines.append(f"> To: {to}")
        lines.append("")
    lines.append(_quoted_trimmed(_body(message.get("payload", {}))))
    for name in _attachments(message.get("payload", {})):
        lines.append(f"\n> File: {name}")
    return "\n".join(lines)


def _thread_date(thread: dict) -> datetime:
    ms = int(thread["messages"][0].get("internalDate", 0))
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()


def _export_thread(service, thread_id: str, out: Path) -> str:
    thread = _call(service.users().threads().get(userId="me", id=thread_id, format="full"))
    messages = thread.get("messages", [])
    if not messages:
        return ""

    subject = _header(messages[0], "subject") or "(no subject)"
    day = _thread_date(thread).strftime("%Y-%m-%d")
    path = out / f"{day}-{_slug(subject)}.md"

    parts = [f"# {subject}\n"]
    for message in messages:
        parts.append(_format(message))
        parts.append("\n---\n")
    path.write_text("\n".join(parts), encoding="utf-8")
    return subject


def _query(search: str | None, days: int, since: str | None) -> str:
    parts = [search.strip()] if search and search.strip() else []
    if since:
        stamp = datetime.fromisoformat(since.replace("Z", "+00:00"))
        parts.append(f"after:{int(stamp.timestamp())}")
    else:
        parts.append(f"newer_than:{days}d")
    return " ".join(parts)


def list_threads(cfg: dict, *, search: str | None = None, days: int = 14, limit: int = 50) -> dict:
    service = build("gmail", "v1", credentials=_credentials(cfg), cache_discovery=False)
    query = _query(search, days, None)

    ids = []
    page = None
    while True:
        reply = _call(service.users().threads().list(userId="me", q=query, maxResults=100, pageToken=page))
        ids.extend(t["id"] for t in reply.get("threads", []))
        page = reply.get("nextPageToken")
        if not page or len(ids) >= 500:
            break

    threads = []
    for thread_id in ids[:limit]:
        thread = _call(service.users().threads().get(userId="me", id=thread_id, format="metadata", metadataHeaders=["Subject", "From"]))
        messages = thread.get("messages", [])
        if not messages:
            continue
        threads.append({
            "subject": _header(messages[0], "subject") or "(no subject)",
            "from": _header(messages[-1], "from"),
            "day": _thread_date(thread).strftime("%Y-%m-%d"),
            "messages": len(messages),
        })
    return {"query": query, "total": len(ids), "more": len(ids) > limit, "threads": threads}


def export_gmail(cfg: dict, out: Path, *, search: str | None = None, days: int = 14, since: str | None = None):
    service = build("gmail", "v1", credentials=_credentials(cfg), cache_discovery=False)
    out.mkdir(parents=True, exist_ok=True)

    query = _query(search, days, since)
    click.echo(f"Searching Gmail: {query}")

    ids = []
    page = None
    while True:
        reply = _call(service.users().threads().list(userId="me", q=query, maxResults=100, pageToken=page))
        ids.extend(t["id"] for t in reply.get("threads", []))
        page = reply.get("nextPageToken")
        if not page:
            break

    click.echo(f"Exporting {len(ids)} threads...")
    count = 0
    for i, thread_id in enumerate(ids, 1):
        subject = _export_thread(service, thread_id, out)
        if subject:
            count += 1
            click.echo(f"{i}/{len(ids)} {subject}")

    click.echo(f"  Exported {count} threads total")
