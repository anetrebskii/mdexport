"""Google Docs exporter - exports docs from a Drive folder as Markdown."""

from pathlib import Path
from datetime import datetime, timezone
import re

import click
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from markdownify import markdownify as html_to_md


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]


def _get_credentials(cfg: dict) -> Credentials:
    """Build credentials from either a service account key file or an access token."""
    sa_key = cfg.get("service_account_key")
    if sa_key:
        return service_account.Credentials.from_service_account_file(
            sa_key, scopes=SCOPES
        )
    token = cfg.get("token")
    if token:
        return Credentials(token=token)
    raise click.ClickException(
        "Google Docs export requires --service-account-key or --token"
    )


def _slugify(text: str) -> str:
    slug = text.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:60]


def _list_docs(drive, folder_ids: list[str] | None, since: datetime | None = None) -> list[dict]:
    """List Google Docs in the given folders (or all accessible docs)."""
    docs = []
    query_parts = ["mimeType = 'application/vnd.google-apps.document'", "trashed = false"]
    if since:
        ts = since.strftime("%Y-%m-%dT%H:%M:%S")
        query_parts.append(f"modifiedTime > '{ts}'")

    if folder_ids:
        for folder_id in folder_ids:
            q = " and ".join(query_parts + [f"'{folder_id}' in parents"])
            docs.extend(_paginate_files(drive, q))
    else:
        q = " and ".join(query_parts)
        docs.extend(_paginate_files(drive, q))

    return docs


def _paginate_files(drive, query: str) -> list[dict]:
    """Paginate through Drive file listing."""
    files = []
    page_token = None
    while True:
        resp = drive.files().list(
            q=query,
            fields="nextPageToken, files(id, name, modifiedTime, createdTime, owners, webViewLink)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def _export_doc_as_md(drive, doc_id: str) -> str:
    """Export a Google Doc as HTML, then convert to Markdown."""
    html = drive.files().export(
        fileId=doc_id,
        mimeType="text/html",
    ).execute()
    if isinstance(html, bytes):
        html = html.decode("utf-8")
    md = html_to_md(html, heading_style="ATX", strip=["img"])
    # Clean up excessive blank lines
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def export_google_docs(
    cfg: dict,
    out: Path,
    *,
    folder_ids: list[str] | None = None,
    since: str | None = None,
):
    """Export Google Docs to Markdown files."""
    creds = _get_credentials(cfg)
    drive = build("drive", "v3", credentials=creds)

    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
        click.echo(f"Incremental sync since {since_dt.strftime('%Y-%m-%d %H:%M')}")

    click.echo("Listing documents...")
    docs = _list_docs(drive, folder_ids, since=since_dt)
    click.echo(f"Found {len(docs)} document(s) to export")

    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for doc in docs:
        doc_id = doc["id"]
        title = doc.get("name", "Untitled")
        fname = f"{_slugify(title)}-{doc_id[:8]}.md"
        fpath = out / fname

        try:
            body_md = _export_doc_as_md(drive, doc_id)
        except Exception as e:
            click.echo(f"  Skipping '{title}': {e}")
            continue

        modified = doc.get("modifiedTime", "")[:10]
        created = doc.get("createdTime", "")[:10]
        owners = ", ".join(o.get("displayName", "?") for o in doc.get("owners", []))
        link = doc.get("webViewLink", "")

        md = f"""# {title}

- **Modified:** {modified}
- **Created:** {created}
- **Owner:** {owners}
- **Link:** {link}

---

{body_md}
"""
        fpath.write_text(md)
        count += 1
        if count % 10 == 0:
            click.echo(f"  Exported {count} docs...")

    click.echo(f"  Exported {count} docs total")
    click.echo(f"Done! Output: {out}")
