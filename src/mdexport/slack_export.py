"""Stateless Slack export - one Markdown file per channel per UTC day, with message permalinks."""

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler, ServerErrorRetryHandler

from mdexport.slack import _resolve_users

SKIP_SUBTYPES = ("channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name")

_TOKEN = re.compile(r"<([^<>]*)>")
_LINE_OPENERS = ("#", "```", "~~~")
_BROADCASTS = ("here", "channel", "everyone")
_CHANNEL_ID = re.compile(r"[CG][A-Z0-9]{2,}")


def permalink(base_url: str, channel_id: str, ts: str, thread_ts: str | None = None) -> str:
    base = base_url if base_url.endswith("/") else base_url + "/"
    link = f"{base}archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts:
        link += f"?thread_ts={thread_ts}&cid={channel_id}"
    return link


def _replace_token(token: str, users: dict[str, str], channels: dict[str, str]) -> str:
    target, _, label = token.partition("|")
    if target.startswith("@"):
        uid = target[1:]
        return "@" + (users.get(uid) or label or uid)
    if target.startswith("#"):
        return "#" + (label or channels.get(target[1:], target[1:]))
    if target.startswith("!"):
        command = target[1:]
        if command in _BROADCASTS:
            return "@" + command
        return label or command
    return f"[{label}]({target})" if label else target


def _escape_line(line: str) -> str:
    """`line` with a backslash before a leading `#`, ``` or ~~~, which would open a heading or a code fence."""
    body = line.lstrip()
    if not body.startswith(_LINE_OPENERS):
        return line
    return line[: len(line) - len(body)] + "\\" + body


def clean_text(text: str, users: dict[str, str], channels: dict[str, str]) -> str:
    """Turn Slack mrkdwn tokens into readable Markdown that cannot open a heading or a code fence."""
    text = _TOKEN.sub(lambda m: _replace_token(m.group(1), users, channels), text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    # Lines as llmdex's chunker splits them: it also breaks at \r, \u2028 and the like.
    return "".join(_escape_line(line) for line in text.splitlines(keepends=True))


def _utc(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def day_of(ts: str) -> str:
    return _utc(ts).strftime("%Y-%m-%d")


def time_of(ts: str) -> str:
    return _utc(ts).strftime("%H:%M")


def author_of(msg: dict, users: dict[str, str]) -> str:
    uid = msg.get("user")
    if uid in users:
        return users[uid]
    bot_name = (msg.get("bot_profile") or {}).get("name")
    return msg.get("username") or bot_name or uid or "unknown"


def _file_lines(msg: dict) -> list[str]:
    return [
        f"File: [{f.get('name', 'file')}]({f.get('permalink') or f.get('url_private', '')})" for f in msg.get("files", [])
    ]


def _message_block(msg: dict, cid: str, users: dict, channels: dict, base_url: str) -> str:
    lines = [f"## {time_of(msg['ts'])} UTC · {author_of(msg, users)}", permalink(base_url, cid, msg["ts"])]
    text = clean_text(msg.get("text", ""), users, channels)
    if text:
        lines.append(text)
    lines += _file_lines(msg)
    return "\n".join(lines)


def _reply_block(reply: dict, parent_ts: str, cid: str, users: dict, channels: dict, base_url: str) -> str:
    first, *rest = clean_text(reply.get("text", ""), users, channels).split("\n")
    header = f"> {time_of(reply['ts'])} UTC · {author_of(reply, users)}:"
    lines = [f"{header} {first}" if first else header]
    lines += [f"> {line}" for line in rest + _file_lines(reply)]
    lines.append(f"> {permalink(base_url, cid, reply['ts'], reply.get('thread_ts') or parent_ts)}")
    return "\n".join(lines)


def render_day(channel: dict, day: str, messages: list[dict], users: dict, channels: dict, base_url: str) -> str:
    """Render one channel's day file; each message's `_replies` follow it as quoted blocks."""
    cid = channel["id"]
    shared = "yes" if channel["shared"] else "no"
    blocks = [f"# #{channel['name']}, {day}\nchannel: {cid} · shared with another organisation: {shared}"]
    for msg in messages:
        blocks.append(_message_block(msg, cid, users, channels, base_url))
        for reply in msg.get("_replies", []):
            blocks.append(_reply_block(reply, msg["ts"], cid, users, channels, base_url))
    return "\n\n".join(blocks) + "\n"


class ExportError(Exception):
    """The workspace, its users or its channels could not be listed; nothing was written or deleted."""


def make_client(token: str) -> WebClient:
    """A Slack client that retries rate limits and server errors up to five times."""
    client = WebClient(token=token)
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=5))
    client.retry_handlers.append(ServerErrorRetryHandler(max_retry_count=5))
    return client


def _midnight(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def window_start(now: datetime, cursor_ts: str | None, history_days: int, refetch_days: int) -> datetime:
    """UTC midnight a channel's fetch starts from; a cursor older than the refetch window widens it."""
    today = _midnight(now)
    if not cursor_ts:
        return today - timedelta(days=history_days)
    return min(today - timedelta(days=refetch_days), _midnight(_utc(cursor_ts)))


def _pages(method, key: str, **kwargs) -> list[dict]:
    items, cursor = [], None
    while True:
        resp = method(cursor=cursor, limit=200, **kwargs)
        items.extend(resp.get(key) or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return items


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_cursor(out: Path, cid: str) -> dict:
    path = out / ".state" / f"{cid}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_cursor(out: Path, cid: str, state: dict) -> None:
    _write_atomic(out / ".state" / f"{cid}.json", json.dumps(state))


def _skipped(msg: dict, skip_users: set[str], bot_id: str | None) -> bool:
    if msg.get("subtype") in SKIP_SUBTYPES or msg.get("user") in skip_users:
        return True
    return bool(bot_id) and msg.get("bot_id") == bot_id


def _fetch(client, cid: str, start: datetime, skip_users: set[str], bot_id: str | None) -> list[dict]:
    """The channel's top-level messages since `start`, each with its kept replies under `_replies`."""
    messages = [
        m for m in _pages(client.conversations_history, "messages", channel=cid,
                          oldest=str(start.timestamp()), inclusive=True)
        if not _skipped(m, skip_users, bot_id) and m.get("thread_ts", m["ts"]) == m["ts"]
    ]
    for msg in messages:
        if msg.get("reply_count"):
            replies = _pages(client.conversations_replies, "messages", channel=cid, ts=msg["ts"])
            kept = [r for r in replies if r["ts"] != msg["ts"] and not _skipped(r, skip_users, bot_id)]
            msg["_replies"] = sorted(kept, key=lambda r: float(r["ts"]))
    return messages


def _write_days(out: Path, channel: dict, messages: list[dict], start: datetime, now: datetime,
                users: dict, channels: dict, base_url: str) -> int:
    """Rewrite every day file from `start` to `now` from `messages`; return the messages and replies written."""
    by_day: dict[str, list[dict]] = {}
    for msg in messages:
        by_day.setdefault(day_of(msg["ts"]), []).append(msg)
    written, day, last = 0, start.date(), _midnight(now).date()
    while day <= last:
        name = day.isoformat()
        path = out / channel["id"] / f"{name}.md"
        if name in by_day:
            day_messages = sorted(by_day[name], key=lambda m: float(m["ts"]))
            _write_atomic(path, render_day(channel, name, day_messages, users, channels, base_url))
            written += sum(1 + len(m.get("_replies", [])) for m in day_messages)
        else:
            path.unlink(missing_ok=True)
        day += timedelta(days=1)
    return written


def _remove_unkept(out: Path, kept: set[str]) -> list[str]:
    """Delete every folder named like a channel id that was not kept, with its cursor; return their ids."""
    folders = (p.name for p in out.iterdir() if p.is_dir() and _CHANNEL_ID.fullmatch(p.name))
    removed = sorted(name for name in folders if name not in kept)
    for cid in removed:
        shutil.rmtree(out / cid)
        (out / ".state" / f"{cid}.json").unlink(missing_ok=True)
    return removed


def export(client, out: Path, *, history_days: int, refetch_days: int, exclude: Iterable[str] = (),
           exclude_users: Iterable[str] = (), now: datetime | None = None) -> dict:
    """Export every member channel's window into `out/<id>/<day>.md` and return the run's report."""
    now = now or datetime.now(timezone.utc)
    try:
        auth = client.auth_test()
        users = _resolve_users(client)
        listed = _pages(client.conversations_list, "channels",
                        types="public_channel,private_channel", exclude_archived=False)
    except SlackApiError as e:
        raise ExportError(f"Slack channel listing failed: {e.response['error']}") from e
    base_url, bot_id = auth["url"], auth.get("bot_id")
    skip_users = {auth["user_id"], *exclude_users}
    names = {c["id"]: c.get("name") or c["id"] for c in listed}
    excluded = set(exclude)
    kept = [c for c in listed if c.get("is_member") and c["id"] not in excluded and c.get("name") not in excluded]
    out.mkdir(parents=True, exist_ok=True)
    report = {"channels": len(kept), "messages": 0, "removed": [], "unreadable": {}}
    for listing in kept:
        cid = listing["id"]
        channel = {"id": cid, "name": names[cid],
                   "shared": bool(listing.get("is_ext_shared") or listing.get("is_pending_ext_shared"))}
        old_ts = _read_cursor(out, cid).get("latest_ts") or None
        start = window_start(now, old_ts, history_days, refetch_days)
        try:
            messages = _fetch(client, cid, start, skip_users, bot_id)
        except SlackApiError as e:
            # Nothing of this channel has been written yet, so its files and cursor stay as they were.
            report["unreadable"][cid] = e.response["error"]
            continue
        report["messages"] += _write_days(out, channel, messages, start, now, users, names, base_url)
        stamps = [m["ts"] for m in messages] + [r["ts"] for m in messages for r in m.get("_replies", [])]
        latest = max(stamps, key=float) if stamps else old_ts or ""
        _write_cursor(out, cid, {"latest_ts": latest, "name": channel["name"], "shared": channel["shared"]})
    if kept:
        report["removed"] = _remove_unkept(out, {c["id"] for c in kept})
    else:
        # A listing that kept nothing (wrong workspace, bot removed everywhere) never wipes the export.
        report["removal_skipped"] = True
    return report
