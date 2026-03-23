"""Notion exporter - exports pages and databases as Markdown."""

from pathlib import Path
from datetime import datetime
import re

import click
import httpx


API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _slugify(text: str) -> str:
    slug = text.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:60]


# --- Block to Markdown conversion ---

def _rich_text_to_md(rich_texts: list[dict]) -> str:
    """Convert Notion rich_text array to markdown string."""
    parts = []
    for rt in rich_texts:
        text = rt.get("plain_text", "")
        annot = rt.get("annotations", {})
        href = rt.get("href")

        if annot.get("code"):
            text = f"`{text}`"
        if annot.get("bold"):
            text = f"**{text}**"
        if annot.get("italic"):
            text = f"*{text}*"
        if annot.get("strikethrough"):
            text = f"~~{text}~~"
        if href:
            text = f"[{text}]({href})"

        parts.append(text)
    return "".join(parts)


def _block_to_md(block: dict, indent: int = 0) -> str:
    """Convert a single Notion block to markdown."""
    btype = block.get("type", "")
    data = block.get(btype, {})
    prefix = "  " * indent

    if btype == "paragraph":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{prefix}{text}\n" if text else "\n"

    elif btype in ("heading_1", "heading_2", "heading_3"):
        level = int(btype[-1])
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{'#' * level} {text}\n"

    elif btype == "bulleted_list_item":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{prefix}- {text}\n"

    elif btype == "numbered_list_item":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{prefix}1. {text}\n"

    elif btype == "to_do":
        text = _rich_text_to_md(data.get("rich_text", []))
        checked = "x" if data.get("checked") else " "
        return f"{prefix}- [{checked}] {text}\n"

    elif btype == "toggle":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{prefix}<details><summary>{text}</summary>\n\n"

    elif btype == "code":
        text = _rich_text_to_md(data.get("rich_text", []))
        lang = data.get("language", "")
        return f"```{lang}\n{text}\n```\n"

    elif btype == "quote":
        text = _rich_text_to_md(data.get("rich_text", []))
        lines = text.split("\n")
        return "\n".join(f"{prefix}> {line}" for line in lines) + "\n"

    elif btype == "callout":
        icon = data.get("icon", {})
        emoji = icon.get("emoji", "") if icon.get("type") == "emoji" else ""
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{prefix}> {emoji} {text}\n"

    elif btype == "divider":
        return "---\n"

    elif btype == "image":
        img = data.get("file", data.get("external", {}))
        url = img.get("url", "")
        caption = _rich_text_to_md(data.get("caption", []))
        return f"![{caption}]({url})\n"

    elif btype == "bookmark":
        url = data.get("url", "")
        caption = _rich_text_to_md(data.get("caption", []))
        return f"[{caption or url}]({url})\n"

    elif btype == "table_row":
        cells = data.get("cells", [])
        row = " | ".join(_rich_text_to_md(cell) for cell in cells)
        return f"| {row} |\n"

    elif btype == "child_page":
        title = data.get("title", "Untitled")
        return f"**[Subpage: {title}]**\n"

    elif btype == "child_database":
        title = data.get("title", "Untitled")
        return f"**[Database: {title}]**\n"

    elif btype == "equation":
        expr = data.get("expression", "")
        return f"$${expr}$$\n"

    elif btype == "embed":
        url = data.get("url", "")
        return f"[Embed: {url}]({url})\n"

    elif btype == "link_preview":
        url = data.get("url", "")
        return f"[{url}]({url})\n"

    return ""


# --- API calls ---

def _search_pages(client: httpx.Client, token: str, since: datetime | None = None) -> list[dict]:
    """Search for all pages accessible to the integration."""
    pages = []
    payload: dict = {
        "filter": {"value": "page", "property": "object"},
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        "page_size": 100,
    }
    while True:
        resp = client.post(f"{API_BASE}/search", headers=_headers(token), json=payload)
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            edited = page.get("last_edited_time", "")
            if since and edited:
                edited_dt = datetime.fromisoformat(edited.replace("Z", "+00:00"))
                if edited_dt < since:
                    return pages
            pages.append(page)
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return pages


def _get_page_blocks(client: httpx.Client, token: str, page_id: str) -> list[dict]:
    """Retrieve all blocks for a page, including children."""
    blocks = []
    url = f"{API_BASE}/blocks/{page_id}/children"
    params: dict = {"page_size": 100}
    while True:
        resp = client.get(url, headers=_headers(token), params=params)
        resp.raise_for_status()
        data = resp.json()
        for block in data.get("results", []):
            blocks.append(block)
            if block.get("has_children") and block["type"] not in ("child_page", "child_database"):
                children = _get_page_blocks(client, token, block["id"])
                blocks.extend(children)
        if not data.get("has_more"):
            break
        params["start_cursor"] = data["next_cursor"]
    return blocks


def _get_page_title(page: dict) -> str:
    """Extract title from a page object."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            return _rich_text_to_md(prop.get("title", []))
    return "Untitled"


def _blocks_to_md(blocks: list[dict]) -> str:
    """Convert a list of blocks to markdown, handling table rows."""
    lines = []
    in_table = False
    table_row_idx = 0
    for block in blocks:
        btype = block.get("type", "")
        if btype == "table_row":
            line = _block_to_md(block)
            lines.append(line)
            table_row_idx += 1
            if table_row_idx == 1:
                # Add header separator after first row
                cells = block.get("table_row", {}).get("cells", [])
                sep = "| " + " | ".join("---" for _ in cells) + " |\n"
                lines.append(sep)
            in_table = True
        else:
            if in_table:
                lines.append("\n")
                in_table = False
                table_row_idx = 0
            lines.append(_block_to_md(block))
    md = "".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def _filter_pages(pages: list[dict], page_ids: list[str] | None, database_ids: list[str] | None) -> list[dict]:
    """Filter pages by explicit page IDs or parent database IDs."""
    if not page_ids and not database_ids:
        return pages
    filtered = []
    page_id_set = set(page_ids or [])
    db_id_set = set(database_ids or [])
    for page in pages:
        pid = page["id"].replace("-", "")
        if page_id_set and pid in {p.replace("-", "") for p in page_id_set}:
            filtered.append(page)
            continue
        parent = page.get("parent", {})
        if parent.get("type") == "database_id":
            parent_db = parent["database_id"].replace("-", "")
            if db_id_set and parent_db in {d.replace("-", "") for d in db_id_set}:
                filtered.append(page)
    return filtered


def export_notion(
    token: str,
    out: Path,
    *,
    page_ids: list[str] | None = None,
    database_ids: list[str] | None = None,
    since: str | None = None,
):
    """Export Notion pages to Markdown files."""
    click.echo("Connecting to Notion...")

    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
        click.echo(f"Incremental sync since {since_dt.strftime('%Y-%m-%d %H:%M')}")

    with httpx.Client(timeout=30) as client:
        click.echo("Searching for pages...")
        pages = _search_pages(client, token, since=since_dt)
        pages = _filter_pages(pages, page_ids, database_ids)
        click.echo(f"Found {len(pages)} page(s) to export")

        out.mkdir(parents=True, exist_ok=True)
        count = 0
        for page in pages:
            page_id = page["id"]
            title = _get_page_title(page)
            fname = f"{_slugify(title)}-{page_id[:8]}.md"
            fpath = out / fname

            try:
                blocks = _get_page_blocks(client, token, page_id)
                body_md = _blocks_to_md(blocks)
            except Exception as e:
                click.echo(f"  Skipping '{title}': {e}")
                continue

            edited = page.get("last_edited_time", "")[:10]
            created = page.get("created_time", "")[:10]
            url = page.get("url", "")

            md = f"""# {title}

- **Last edited:** {edited}
- **Created:** {created}
- **URL:** {url}

---

{body_md}
"""
            fpath.write_text(md)
            count += 1
            if count % 10 == 0:
                click.echo(f"  Exported {count} pages...")

    click.echo(f"  Exported {count} pages total")
    click.echo(f"Done! Output: {out}")
