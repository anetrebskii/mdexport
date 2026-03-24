# Adding New Export Channels

This guide walks through adding a new data source (channel) to mdexporter. Every channel follows the same pattern: authenticate, paginate, format as Markdown, write files. The tool currently has five channels: GitHub, Slack, Linear, Google Docs, and Notion.

## Architecture Overview

A channel consists of three touch points:

| File | What to add |
|---|---|
| `src/mdexport/<channel>.py` | Exporter module — API calls, formatting, file writing |
| `src/mdexport/cli.py` | `add` subcommand (registers config) + wiring in `_sync_one()` |
| `src/mdexport/registry.py` | No changes needed — generic JSON persistence |

Supporting module:

| File | Purpose |
|---|---|
| `src/mdexport/changeset.py` | Records what changed during incremental syncs |

## Step 1: Create the Exporter Module

Create `src/mdexport/<channel>.py`. The module exposes a single public function:

```python
def export_<channel>(token: str, out: Path, *, since: str | None = None):
    ...
```

### Skeleton

```python
"""<Channel> exporter - <what it exports>."""

from pathlib import Path
from datetime import date
import click

from mdexport.changeset import Changeset


def _synced_today(path: Path) -> bool:
    """Check if a file was modified today (dedup within same day)."""
    if not path.exists():
        return False
    mtime = date.fromtimestamp(path.stat().st_mtime)
    return mtime == date.today()


def _slugify(text: str) -> str:
    slug = text.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:60]


def export_example(token: str, out: Path, *, since: str | None = None):
    """Main entry point. Called by cli._sync_one()."""
    click.echo("Connecting to Example API...")

    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)
        click.echo(f"Incremental sync since {since_dt.strftime('%Y-%m-%d %H:%M')}")

    changeset = Changeset(out) if since else None

    # 1. Authenticate (token from env var or registry config)
    # 2. Paginate through API
    items = _fetch_items(token, since=since)
    click.echo(f"Found {len(items)} items")

    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in items:
        fname = f"{item['id']}-{_slugify(item['title'])}.md"
        fpath = out / fname

        if _synced_today(fpath):
            continue

        # 3. Format as Markdown
        md = _format_item(item)

        # 4. Record changeset (before writing, so diff sees old file)
        if changeset:
            # For structured data (issues, tickets): use changeset.add()
            # For documents (pages, docs): use changeset.add_diff()
            changeset.add_diff(item["title"], fpath, md)

        # 5. Write file
        fpath.write_text(md)
        count += 1

    if changeset:
        cs_path = changeset.write()
        if cs_path:
            click.echo(f"\nChangeset: {cs_path}")

    click.echo(f"Exported {count} items")
    click.echo(f"Done! Output: {out}")
```

### Key Conventions

**Authentication.** Token comes from an environment variable, passed through `cli.py`. All existing channels use a single token/API key. Google Docs is the exception — it supports service account keys and application default credentials via a `cfg` dict instead of a plain token.

**Incremental sync.** The `since` parameter is an ISO 8601 timestamp string (e.g., `2026-03-24T10:00:00+00:00`) stored in the registry. Filter API results to only items updated after this time. If `since` is `None`, do a full export.

**Dedup with `_synced_today()`.** Every channel checks file mtime before re-exporting. This lets users safely re-run sync without duplicating API calls for items already exported today.

**Slugify.** File names use `_slugify()` — lowercase, hyphens, max 60 chars. Prefix with an identifier when available (issue number, doc ID prefix) to avoid collisions.

**Retries.** Handle rate limits and transient errors. See `linear.py` for a manual retry loop pattern, or `github.py` which uses PyGithub's built-in `GithubRetry`.

**Progress output.** Use `click.echo()` for progress. Print a count every 10-25 items for large exports.

## Step 2: Changeset Integration

Changesets record what changed during each incremental sync. The file is written to `<output>/_changesets/YYYY-MM-DDTHH-MM-SS.md`.

The `Changeset` class provides three methods:

### `changeset.add(heading, details)` — Structured data

Use for sources with discrete change events (issues, tickets, PRs). Pass a list of human-readable change descriptions.

```python
# Linear example: filter history/comments by since timestamp
changes = []
for h in history:
    if h["createdAt"] <= since:
        continue
    if h.get("fromState") and h.get("toState"):
        changes.append(f"**Status:** {h['fromState']['name']} → {h['toState']['name']}")
changeset.add(f"ISSUE-123 - Fix bug", changes)
```

If `details` is empty, nothing is recorded (item was updated but no meaningful changes detected).

### `changeset.add_new(heading)` — New items

Use when an item was created after `since`. Records it simply as "*New*".

```python
if item["created_at"] > since:
    changeset.add_new(f"ISSUE-123 - Fix bug")
```

### `changeset.add_diff(heading, old_path, new_text)` — Document content

Use for document-type sources (Google Docs, Notion, wiki pages) where changes are in the content body. Diffs the old file on disk against the new text and records added/removed lines.

```python
md = format_document(doc)
changeset.add_diff(doc["title"], output_path, md)
# Important: call add_diff BEFORE writing the file
output_path.write_text(md)
```

If the file doesn't exist yet, it's recorded as "*New*". If content is identical, nothing is recorded.

### Which method to use

| Source type | Method | Example channels |
|---|---|---|
| Issue trackers, tickets | `add()` / `add_new()` | Linear, GitHub |
| Documents, pages | `add_diff()` | Google Docs, Notion |
| Append-only (messages) | Usually skip | Slack |

## Step 3: Add the CLI Command

In `cli.py`, add two things:

### 3a. The `add` subcommand

Register the channel config in `~/.mdexport/registry.json`:

```python
@add.command("example")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="EXAMPLE_TOKEN", required=True, help="API token")
# Add channel-specific options here (filters, scopes, etc.)
def add_example(name, output, token):
    """Add an Example export."""
    from mdexport.registry import register

    out = str(Path(output).resolve())

    register(name, {
        "type": "example",
        "output": out,
        "token": token,
        # Channel-specific config fields
    })
    click.echo(f"Added Example export '{name}' -> {out}")
```

The `register()` call automatically adds `added_at` and `synced_at` fields.

### 3b. Wire into `_sync_one()`

Add an `elif` branch in `_sync_one()`:

```python
elif t == "example":
    token = _resolve_token(cfg)
    from mdexport.example import export_example
    export_example(
        token, out,
        since=since,
    )
```

The `since` value comes from `cfg.get("synced_at")` — it's `None` on first sync and an ISO timestamp on subsequent syncs. After `_sync_one()` returns successfully, `cli.py` calls `update_synced(name)` to record the current time.

### 3c. Update `_describe_source()` (optional)

Add a branch in `_describe_source()` so `mdexport list` shows a useful summary:

```python
elif t == "example":
    return "some summary of scope"
```

### 3d. Update `info` command (optional)

Add a branch in the `info` command to show channel-specific config details.

## Step 4: Add Dependencies

If the channel requires new packages, add them to `pyproject.toml` under `[project] dependencies`.

## Step 5: Add Environment Variable

Document the token env var in `.env.example` (e.g., `EXAMPLE_TOKEN=`).

## Checklist

- [ ] `src/mdexport/<channel>.py` — exporter module with `export_<channel>()` function
- [ ] Handles `since` parameter for incremental sync
- [ ] Uses `_synced_today()` for same-day dedup
- [ ] Integrates `Changeset` (call before writing files)
- [ ] `cli.py` — `add` subcommand with `--token` envvar, channel-specific options
- [ ] `cli.py` — `_sync_one()` branch
- [ ] `cli.py` — `_describe_source()` branch
- [ ] `pyproject.toml` — new dependencies (if any)
- [ ] `.env.example` — new env var

## Reference: Existing Channel Patterns

| Channel | Auth | API client | Incremental filter | Changeset style |
|---|---|---|---|---|
| GitHub | `GITHUB_TOKEN` | PyGithub | `since` param on issues; `updated_at` cutoff on PRs | `add()` — timeline events, comments, reviews |
| Slack | `SLACK_TOKEN` | slack-sdk | `oldest` timestamp param | None (append-only messages) |
| Linear | `LINEAR_TOKEN` | httpx + GraphQL | `updatedAt >= $since` filter | `add()` — history entries, comments |
| Google Docs | Service account / gcloud / token | google-api-python-client | `modifiedTime > since` Drive query | `add_diff()` — content diff |
| Notion | `NOTION_TOKEN` | httpx + REST | `last_edited_time` sort + cutoff | `add_diff()` — content diff |
