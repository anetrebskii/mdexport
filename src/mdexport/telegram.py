"""Telegram exporter - chats, groups and channels over MTProto."""

import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import click

API_ID = 2496
API_HASH = "8da85b0d5bfe62527e5b244c209159c3"

DC_ADDRESSES = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

SIGNED_OUT = "Telegram signed you out of this account."
SIGNED_OUT_ERRORS = {"AuthKeyUnregisteredError", "AuthKeyInvalidError", "AuthKeyPermEmptyError", "AuthKeyDuplicatedError", "SessionRevokedError", "SessionExpiredError", "UserDeactivatedError", "UserDeactivatedBanError", "UnauthorizedError"}

ID_MARKER = re.compile(r"<!-- id:(\d+) -->")
DAY_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})$", re.MULTILINE)


def _client(dc: int, auth_key: str):
    from telethon.sync import TelegramClient
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession

    key = bytes.fromhex(auth_key)
    if len(key) != 256:
        raise click.ClickException("The Telegram session is not a 256-byte auth key. Sign in again.")
    address = DC_ADDRESSES.get(dc)
    if not address:
        raise click.ClickException(f"Telegram datacenter {dc} is not known. Sign in again.")

    session = StringSession()
    session.set_dc(dc, address, 443)
    session.auth_key = AuthKey(key)
    return TelegramClient(session, API_ID, API_HASH, flood_sleep_threshold=60)


def _slug(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "chat"


def _matches(dialog, wanted: list[str]) -> bool:
    entity = dialog.entity
    names = {str(dialog.id), str(getattr(entity, "id", ""))}
    for attr in ("username", "phone"):
        value = getattr(entity, attr, None)
        if value:
            names.add(value.lower())
    for username in getattr(entity, "usernames", None) or []:
        names.add(username.username.lower())
    if dialog.name:
        names.add(dialog.name.lower())
    for want in wanted:
        want = want.strip().lower().lstrip("@")
        want = re.sub(r"^https?://t\.me/", "", want).strip("/")
        if want and want in names:
            return True
    return False


def _media_line(message) -> str:
    if not message.media:
        return ""
    name = getattr(message.file, "name", None) if message.file else None
    if name:
        return f"\n> File: {name}"
    if message.photo:
        return "\n> Photo"
    if message.voice:
        return "\n> Voice message"
    if message.video:
        return "\n> Video"
    if message.sticker:
        return f"\n> Sticker: {message.file.emoji or ''}".rstrip()
    return "\n> Attachment"


def _sender(message, chat_title: str) -> str:
    from telethon import utils

    sender = message.sender
    if sender is None:
        return chat_title
    return utils.get_display_name(sender) or chat_title


def _format(message, chat_title: str) -> str:
    when = message.date.astimezone().strftime("%Y-%m-%d %H:%M")
    text = message.text or ""
    return f"**{_sender(message, chat_title)}** - {when} <!-- id:{message.id} -->\n\n{text}{_media_line(message)}"


def _last_id(path: Path) -> int:
    if not path.exists():
        return 0
    ids = [int(m) for m in ID_MARKER.findall(path.read_text(encoding="utf-8"))]
    return max(ids) if ids else 0


def _last_day(path: Path) -> str:
    if not path.exists():
        return ""
    days = DAY_HEADING.findall(path.read_text(encoding="utf-8"))
    return days[-1] if days else ""


def _export_chat(client, dialog, out: Path, days: int) -> int:
    title = dialog.name or str(dialog.id)
    folder = out / "dms" if dialog.is_user else out
    path = folder / f"{_slug(title)}.md"

    last = _last_id(path)
    kwargs = {"reverse": True}
    if last:
        kwargs["min_id"] = last
    else:
        kwargs["offset_date"] = datetime.now(timezone.utc) - timedelta(days=days)

    messages = [m for m in client.iter_messages(dialog.entity, **kwargs) if m.text or m.media]
    if not messages:
        return 0

    folder.mkdir(parents=True, exist_ok=True)
    day = _last_day(path)
    parts = [] if path.exists() else [f"# {title}\n"]
    for message in messages:
        this_day = message.date.astimezone().strftime("%Y-%m-%d")
        if this_day != day:
            parts.append(f"\n## {this_day}\n")
            day = this_day
        parts.append(_format(message, title))
        parts.append("\n---\n")

    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return len(messages)


def _me(client):
    """Ask who the session belongs to, letting Telegram's own error through.

    is_user_authorized() swallows every RPCError, so an api_id problem and a
    dead session both read as signed out.
    """
    from telethon.errors import RPCError

    try:
        me = client.get_me()
    except RPCError as e:
        if _is_signed_out(e):
            raise click.ClickException(f"{SIGNED_OUT} ({type(e).__name__})")
        raise click.ClickException(f"Telegram refused the session: {e}")
    if me is None:
        raise click.ClickException(f"{SIGNED_OUT} (the session belongs to no account)")
    return me


def _is_signed_out(e) -> bool:
    name = type(e).__name__
    message = str(getattr(e, "message", "") or e).upper()
    return name in SIGNED_OUT_ERRORS or any(word in message for word in ("AUTH_KEY", "SESSION_REVOKED", "SESSION_EXPIRED", "USER_DEACTIVATED", "UNAUTHORIZED"))


def list_chats(dc: int, auth_key: str) -> list[dict]:
    from telethon.errors import AuthKeyNotFound, UnauthorizedError

    client = _client(dc, auth_key)
    try:
        client.connect()
        _me(client)
        chats = []
        for dialog in client.iter_dialogs():
            if not (dialog.is_user or dialog.is_group or dialog.is_channel):
                continue
            username = getattr(dialog.entity, "username", None)
            chats.append({
                "title": dialog.name or str(dialog.id),
                "username": f"@{username}" if username else "",
                "kind": "dm" if dialog.is_user else "group" if dialog.is_group else "channel",
            })
        return chats
    except (AuthKeyNotFound, UnauthorizedError) as e:
        raise click.ClickException(f"{SIGNED_OUT} ({type(e).__name__})")
    finally:
        client.disconnect()


def export_telegram(dc: int, auth_key: str, out: Path, *, name: str | None = None, chats: list[str] | None = None, days: int = 14, dms: bool = False):
    from telethon.errors import AuthKeyNotFound, FloodWaitError, UnauthorizedError

    gone = (AuthKeyNotFound, UnauthorizedError)

    client = _client(dc, auth_key)
    out.mkdir(parents=True, exist_ok=True)

    try:
        client.connect()
        me = _me(client)
        account = " ".join(filter(None, [me.first_name, me.last_name])) or me.username or ""
        if me.phone:
            account = f"{account}, +{me.phone}" if account else f"+{me.phone}"
        click.echo(f"Signed in as {account}")
        if name:
            from mdexport.registry import update_config

            update_config(name, {"account": account})

        dialogs = [d for d in client.iter_dialogs() if d.is_user or d.is_group or d.is_channel]
        if not dms:
            dialogs = [d for d in dialogs if not d.is_user]
        if chats:
            dialogs = [d for d in dialogs if _matches(d, chats)]

        click.echo(f"Exporting {len(dialogs)} chats (last {days} days)...")
        exported = 0
        for i, dialog in enumerate(dialogs, 1):
            click.echo(f"{i}/{len(dialogs)} {dialog.name or dialog.id}")
            try:
                if _export_chat(client, dialog, out, days):
                    exported += 1
            except FloodWaitError as e:
                raise click.ClickException(f"Telegram asked to wait {max(1, round(e.seconds / 60))} minutes before reading more.")
            except gone:
                raise click.ClickException(SIGNED_OUT)
            except Exception as e:
                click.echo(f"    Skipped ({e})")

        click.echo(f"  Exported {exported} chats total")
    except gone as e:
        raise click.ClickException(f"{SIGNED_OUT} ({type(e).__name__})")
    finally:
        client.disconnect()
