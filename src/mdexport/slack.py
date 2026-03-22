"""Slack exporter - messages from channels using user token."""

from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler, ServerErrorRetryHandler
import click


def _synced_today(path: Path) -> bool:
    """Check if a file was modified today."""
    if not path.exists():
        return False
    mtime = date.fromtimestamp(path.stat().st_mtime)
    return mtime == date.today()


def _resolve_users(client: WebClient) -> dict:
    """Build user ID -> display name map."""
    users = {}
    cursor = None
    while True:
        resp = client.users_list(cursor=cursor, limit=200)
        for u in resp["members"]:
            name = u.get("real_name") or u.get("name") or u["id"]
            users[u["id"]] = name
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return users


def _format_message(msg: dict, users: dict) -> str:
    user = users.get(msg.get("user", ""), msg.get("user", "unknown"))
    ts = float(msg.get("ts", 0))
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    text = msg.get("text", "")

    # Replace user mentions <@U123> with names
    import re
    def replace_mention(m):
        uid = m.group(1)
        return f"@{users.get(uid, uid)}"
    text = re.sub(r"<@(U[A-Z0-9]+)>", replace_mention, text)

    lines = [f"**{user}** - {date}", "", text]

    # Thread replies
    if msg.get("reply_count"):
        lines.append(f"\n> _{msg['reply_count']} replies in thread_")

    # Attachments
    for att in msg.get("attachments", []):
        title = att.get("title", att.get("fallback", "attachment"))
        lines.append(f"\n> Attachment: {title}")

    # Files
    for f in msg.get("files", []):
        name = f.get("name", "file")
        url = f.get("url_private", "")
        lines.append(f"\n> File: [{name}]({url})")

    return "\n".join(lines)


def _export_channel(client: WebClient, channel: dict, users: dict, out: Path, oldest: float):
    cid = channel["id"]
    name = channel.get("name", cid)

    # Skip if already synced today
    channel_file = out / name / f"{name}.md"
    if _synced_today(channel_file):
        click.echo(f"  #{name} (skipped, synced today)")
        return

    click.echo(f"  #{name}...")

    messages = []
    cursor = None
    while True:
        try:
            resp = client.conversations_history(
                channel=cid, cursor=cursor, limit=200, oldest=str(oldest)
            )
        except SlackApiError as e:
            if e.response["error"] == "not_in_channel":
                # Auto-join public channels
                try:
                    client.conversations_join(channel=cid)
                    resp = client.conversations_history(
                        channel=cid, cursor=cursor, limit=200, oldest=str(oldest)
                    )
                except SlackApiError:
                    click.echo(f"    Skipped (cannot access)")
                    return
            else:
                click.echo(f"    Skipped ({e.response['error']})")
                return

        messages.extend(resp.get("messages", []))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    if not messages:
        click.echo(f"    No messages")
        return

    # Sort chronologically
    messages.sort(key=lambda m: float(m.get("ts", 0)))

    # Group by date
    days: dict[str, list] = {}
    for msg in messages:
        ts = float(msg.get("ts", 0))
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days.setdefault(day, []).append(msg)

    # Write single file per channel
    channel_dir = out / name
    channel_dir.mkdir(parents=True, exist_ok=True)

    md_parts = [f"# #{name}\n"]
    topic = channel.get("topic", {}).get("value", "")
    purpose = channel.get("purpose", {}).get("value", "")
    if topic:
        md_parts.append(f"**Topic:** {topic}\n")
    if purpose:
        md_parts.append(f"**Purpose:** {purpose}\n")

    for day, day_msgs in sorted(days.items()):
        md_parts.append(f"\n## {day}\n")
        for msg in day_msgs:
            md_parts.append(_format_message(msg, users))
            md_parts.append("\n---\n")

    (channel_dir / f"{name}.md").write_text("\n".join(md_parts))
    click.echo(f"    {len(messages)} messages")


def export_slack(token: str, out: Path, *, channels: list[str] | None = None, days: int = 90):
    client = WebClient(token=token)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=5))
    client.retry_handlers.append(ServerErrorRetryHandler(max_retry_count=5))
    out.mkdir(parents=True, exist_ok=True)

    click.echo("Resolving users...")
    users = _resolve_users(client)
    click.echo(f"Found {len(users)} users")

    oldest = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()

    click.echo("Listing channels...")
    all_channels = []
    cursor = None
    while True:
        resp = client.conversations_list(
            cursor=cursor, limit=200,
            types="public_channel,private_channel"
        )
        all_channels.extend(resp["channels"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    if channels:
        all_channels = [c for c in all_channels if c["name"] in channels]

    click.echo(f"Exporting {len(all_channels)} channels (last {days} days)...")

    for ch in all_channels:
        _export_channel(client, ch, users, out, oldest)

    click.echo(f"Done! Output: {out}")
