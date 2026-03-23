"""Google Docs exporter - exports docs from a Drive folder as Markdown."""

from pathlib import Path
from datetime import date, datetime, timezone
import re
import subprocess

import click
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from markdownify import markdownify as html_to_md


ADC_CMD = 'gcloud auth application-default login --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/documents.readonly'


def _check_auth_error(e: HttpError):
    """If the error is auth-related, print refresh instructions and re-raise."""
    status = e.resp.status if hasattr(e, 'resp') else 0
    if status in (401, 403):
        click.echo("\nToken expired or insufficient permissions.", err=True)
        click.echo("\nTo fix, run one of:", err=True)
        click.echo(f"\n  Option 1 — Re-login with gcloud (recommended):", err=True)
        click.echo(f"    {ADC_CMD}", err=True)
        click.echo(f"\n  Option 2 — Update token manually:", err=True)
        click.echo("    Edit ~/.mdexport/registry.json and replace the token value", err=True)
    raise e


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]


def _get_credentials(cfg: dict) -> Credentials:
    """Build credentials from service account key, token, or application default credentials."""
    sa_key = cfg.get("service_account_key")
    if sa_key:
        return service_account.Credentials.from_service_account_file(
            sa_key, scopes=SCOPES
        )
    token = cfg.get("token")
    if token:
        return Credentials(token=token)
    # Try application default credentials (from gcloud auth application-default login)
    try:
        import google.auth
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    except Exception:
        pass
    raise click.ClickException(
        "Google Docs export requires --service-account-key, --token, "
        f"or application default credentials.\n"
        f"Run: {ADC_CMD}"
    )


def _synced_today(path: Path) -> bool:
    """Check if a file was modified today."""
    if not path.exists():
        return False
    mtime = date.fromtimestamp(path.stat().st_mtime)
    return mtime == date.today()


def _slugify(text: str) -> str:
    slug = text.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:60]


def _list_subfolders(drive, folder_id: str) -> list[str]:
    """Recursively list all subfolder IDs under a folder."""
    q = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    subfolders = []
    page_token = None
    while True:
        resp = drive.files().list(
            q=q, fields="nextPageToken, files(id)",
            pageSize=100, pageToken=page_token,
            corpora="allDrives",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        for f in resp.get("files", []):
            subfolders.append(f["id"])
            subfolders.extend(_list_subfolders(drive, f["id"]))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return subfolders


def _list_docs(drive, folder_ids: list[str] | None, since: datetime | None = None) -> list[dict]:
    """List Google Docs in the given folders (recursively) or all accessible docs."""
    docs = []
    query_parts = ["mimeType = 'application/vnd.google-apps.document'", "trashed = false"]
    if since:
        ts = since.strftime("%Y-%m-%dT%H:%M:%S")
        query_parts.append(f"modifiedTime > '{ts}'")

    if folder_ids:
        # Expand folder_ids to include all subfolders
        all_folder_ids = list(folder_ids)
        for fid in folder_ids:
            click.echo(f"  Scanning subfolders of {fid}...")
            all_folder_ids.extend(_list_subfolders(drive, fid))
        click.echo(f"  Found {len(all_folder_ids)} folders total")
        for folder_id in all_folder_ids:
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
            fields="nextPageToken, files(id, name, modifiedTime, createdTime, owners, webViewLink, parents)",
            pageSize=100,
            pageToken=page_token,
            corpora="allDrives",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def _resolve_folder_path(drive, folder_id: str, cache: dict) -> str:
    """Resolve a folder ID to its full path, caching results."""
    if folder_id in cache:
        return cache[folder_id]
    try:
        folder = drive.files().get(
            fileId=folder_id,
            fields="id, name, parents, mimeType",
            supportsAllDrives=True,
        ).execute()
    except Exception:
        cache[folder_id] = ""
        return ""
    name = _slugify(folder.get("name", ""))
    # Stop at root folders (My Drive, shared drive roots)
    parents = folder.get("parents", [])
    if not parents:
        # This is a root — don't include its name in the path
        cache[folder_id] = ""
        return ""
    parent_path = _resolve_folder_path(drive, parents[0], cache)
    path = f"{parent_path}/{name}" if parent_path else name
    cache[folder_id] = path
    return path


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
    try:
        docs = _list_docs(drive, folder_ids, since=since_dt)
    except HttpError as e:
        _check_auth_error(e)
    click.echo(f"Found {len(docs)} document(s) to export")

    if cfg.get("_dry_run"):
        folder_cache: dict[str, str] = {}
        for doc in docs:
            title = doc.get("name", "Untitled")
            modified = doc.get("modifiedTime", "")[:10]
            doc_id = doc["id"]
            fname = f"{_slugify(title)}-{doc_id[:8]}.md"
            doc_parents = doc.get("parents", [])
            if doc_parents:
                folder_path = _resolve_folder_path(drive, doc_parents[0], folder_cache)
            else:
                folder_path = ""
            if not folder_path:
                folder_path = "_unsorted"
            rel = f"{folder_path}/{fname}"
            click.echo(f"  {modified}  {rel}")
        click.echo(f"\n{len(docs)} docs would be exported")
        return

    out.mkdir(parents=True, exist_ok=True)
    folder_cache: dict[str, str] = {}
    count = 0
    skipped = 0
    errors = 0
    for doc in docs:
        doc_id = doc["id"]
        title = doc.get("name", "Untitled")
        fname = f"{_slugify(title)}-{doc_id[:8]}.md"

        # Resolve folder path for subdirectory structure
        doc_parents = doc.get("parents", [])
        if doc_parents:
            folder_path = _resolve_folder_path(drive, doc_parents[0], folder_cache)
        else:
            folder_path = ""
        if not folder_path:
            folder_path = "_unsorted"
        doc_dir = out / folder_path
        doc_dir.mkdir(parents=True, exist_ok=True)
        fpath = doc_dir / fname

        if _synced_today(fpath):
            skipped += 1
            continue

        try:
            body_md = _export_doc_as_md(drive, doc_id)
        except HttpError as e:
            status = e.resp.status if hasattr(e, 'resp') else 0
            if status in (401, 403):
                _check_auth_error(e)
            click.echo(f"  Skipping '{title}': HTTP {status}")
            errors += 1
            continue
        except Exception as e:
            click.echo(f"  Skipping '{title}': {e}")
            errors += 1
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

    extra = []
    if skipped:
        extra.append(f"skipped {skipped} existing")
    if errors:
        extra.append(f"{errors} errors")
    suffix = f" ({', '.join(extra)})" if extra else ""
    click.echo(f"  Exported {count} docs total{suffix}")
    click.echo(f"Done! Output: {out}")
