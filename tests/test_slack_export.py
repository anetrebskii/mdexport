import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from slack_sdk.errors import SlackApiError

from mdexport import slack_export
from mdexport.slack_export import ExportError, export, permalink

from .fake_slack import FakeClient

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)
BASE = "https://formulagrowth.slack.com/"


def ts(day: str, hhmm: str) -> str:
    return f"{int(datetime.fromisoformat(f'{day}T{hhmm}:00+00:00').timestamp())}.000001"


def midnight(day: str) -> str:
    return str(datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp())


def msg(day: str, hhmm: str, text: str, user: str = "U1", **extra) -> dict:
    return {"type": "message", "ts": ts(day, hhmm), "user": user, "text": text, **extra}


def bot_msg(day: str, hhmm: str, text: str, bot_id: str, **extra) -> dict:
    return {"type": "message", "subtype": "bot_message", "ts": ts(day, hhmm), "bot_id": bot_id, "text": text, **extra}


def channel(cid: str, name: str, member: bool = True, **extra) -> dict:
    return {"id": cid, "name": name, "is_member": member, **extra}


def run(fake: FakeClient, out, **kwargs) -> dict:
    return export(fake, out, history_days=365, refetch_days=14, now=NOW, **kwargs)


def count(text: str, m: dict) -> int:
    """How often the message's permalink (top-level or reply form) appears."""
    return text.count("/p" + m["ts"].replace(".", ""))


def folders(out) -> list[str]:
    return sorted(p.name for p in out.iterdir() if p.is_dir() and not p.name.startswith("."))


def files(folder) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.iterdir()}


def text_of(out, cid: str) -> str:
    return "".join(p.read_text() for p in sorted((out / cid).glob("*.md")))


def cursor(out, cid: str) -> dict:
    return json.loads((out / ".state" / f"{cid}.json").read_text())


def test_first_run_writes_day_files_and_cursor(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed", is_ext_shared=True), channel("C02", "eng", is_pending_ext_shared=True)]
    today, older = msg("2026-10-07", "10:00", "hello"), msg("2026-10-05", "09:00", "older")
    fake.history = {"C01": [today, older], "C02": [msg("2026-10-07", "11:00", "eng news")]}
    run(fake, tmp_path)
    assert permalink(BASE, "C01", today["ts"]) in (tmp_path / "C01" / "2026-10-07.md").read_text()
    assert permalink(BASE, "C01", older["ts"]) in (tmp_path / "C01" / "2026-10-05.md").read_text()
    assert (tmp_path / "C02" / "2026-10-07.md").exists()
    assert cursor(tmp_path, "C01") == {"latest_ts": today["ts"], "name": "seed", "shared": True}
    assert cursor(tmp_path, "C02")["shared"] is True
    assert fake.history_calls("C01")[0]["oldest"] == midnight("2025-10-07")
    assert fake.history_calls("C01")[0]["inclusive"] is True


def test_only_member_channels_exclusions_by_id_and_name_no_dms(tmp_path):
    fake = FakeClient()
    fake.channels = [
        channel("C01", "seed"),
        channel("C02", "random", member=False),
        channel("C03", "eng"),
        channel("C04", "secret"),
    ]
    fake.history = {cid: [msg("2026-10-07", "10:00", f"in {cid}")] for cid in ("C01", "C02", "C03", "C04")}
    report = run(fake, tmp_path, exclude=("C03", "secret"))
    assert folders(tmp_path) == ["C01"]
    assert report["channels"] == 1
    assert {kw["channel"] for m, kw in fake.calls if m == "conversations_history"} == {"C01"}
    lists = [kw for m, kw in fake.calls if m == "conversations_list"]
    assert len(lists) == 2
    assert all(kw["types"] == "public_channel,private_channel" and kw["exclude_archived"] is False for kw in lists)


def test_same_day_second_run_picks_up_a_new_message_without_duplicates(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    first = msg("2026-10-07", "09:00", "first")
    fake.history = {"C01": [first]}
    run(fake, tmp_path)
    second = msg("2026-10-07", "14:00", "second")
    fake.history["C01"].append(second)
    run(fake, tmp_path)
    text = (tmp_path / "C01" / "2026-10-07.md").read_text()
    assert count(text, first) == 1
    assert count(text, second) == 1


def test_edit_and_deletion_inside_the_window(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    edited, deleted = msg("2026-10-04", "09:00", "first draft"), msg("2026-10-04", "10:00", "to be deleted")
    alone = msg("2026-10-03", "09:00", "alone on its day")
    fake.history = {"C01": [edited, deleted, alone]}
    run(fake, tmp_path)
    assert (tmp_path / "C01" / "2026-10-03.md").exists()
    edited["text"] = "final text"
    fake.history["C01"] = [edited]
    run(fake, tmp_path)
    text = (tmp_path / "C01" / "2026-10-04.md").read_text()
    assert "final text" in text
    assert "first draft" not in text
    assert count(text, deleted) == 0
    assert not (tmp_path / "C01" / "2026-10-03.md").exists()


def test_days_before_the_window_untouched(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    old = msg("2026-09-07", "09:00", "old text")
    fake.history = {"C01": [old, msg("2026-10-07", "09:00", "today")]}
    run(fake, tmp_path)
    old_file = tmp_path / "C01" / "2026-09-07.md"
    before = old_file.read_bytes()
    old["text"] = "edited long after"
    run(fake, tmp_path)
    assert old_file.read_bytes() == before
    assert fake.history_calls("C01")[-1]["oldest"] == midnight("2026-09-23")


def test_stale_cursor_widens_the_window(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    stale = ts("2026-08-28", "17:30")
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "C01.json").write_text(json.dumps({"latest_ts": stale, "name": "seed", "shared": False}))
    run(fake, tmp_path)
    assert fake.history_calls("C01")[0]["oldest"] == midnight("2026-08-28")
    assert cursor(tmp_path, "C01")["latest_ts"] == stale


def test_replies_sit_under_their_parents_day(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    parent = msg("2026-10-06", "23:00", "late question", reply_count=1)
    reply = msg("2026-10-07", "01:00", "early answer", user="U2", thread_ts=parent["ts"])
    fake.history = {"C01": [parent]}
    fake.replies = {("C01", parent["ts"]): [parent, reply]}
    run(fake, tmp_path)
    assert permalink(BASE, "C01", reply["ts"], parent["ts"]) in (tmp_path / "C01" / "2026-10-06.md").read_text()
    assert not (tmp_path / "C01" / "2026-10-07.md").exists()


def test_broadcast_reply_not_duplicated(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    parent = msg("2026-10-07", "09:00", "question", reply_count=1)
    reply = msg("2026-10-07", "09:30", "answer", user="U2", thread_ts=parent["ts"], subtype="thread_broadcast")
    fake.history = {"C01": [parent, reply]}
    fake.replies = {("C01", parent["ts"]): [parent, reply]}
    run(fake, tmp_path)
    text = text_of(tmp_path, "C01")
    assert count(text, reply) == 1
    assert permalink(BASE, "C01", reply["ts"], parent["ts"]) in text


def test_skips_own_bot_excluded_users_and_join_noise_keeps_other_bots(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    human = msg("2026-10-07", "09:00", "human question", reply_count=2)
    fake.history = {"C01": [
        human,
        msg("2026-10-07", "09:01", "own user answer", user="UBOT"),
        bot_msg("2026-10-07", "09:02", "own bot post", "BBOT"),
        msg("2026-10-07", "09:03", "excluded user", user="U9"),
        msg("2026-10-07", "09:04", "<@U2> has joined the channel", user="U2", subtype="channel_join"),
        bot_msg("2026-10-07", "09:05", "FOR-1 moved", "B2", username="Linear"),
    ]}
    fake.replies = {("C01", human["ts"]): [
        human,
        msg("2026-10-07", "09:10", "own thread answer", user="UBOT", thread_ts=human["ts"]),
        msg("2026-10-07", "09:11", "thanks", user="U2", thread_ts=human["ts"]),
        msg("2026-10-07", "09:12", "excluded reply", user="U9", thread_ts=human["ts"]),
        msg("2026-10-07", "09:13", "renamed the channel", user="U2", thread_ts=human["ts"], subtype="channel_name"),
    ]}
    run(fake, tmp_path, exclude_users=("U9",))
    text = text_of(tmp_path, "C01")
    gone_texts = ("own user answer", "own bot post", "excluded user", "has joined", "own thread answer",
                  "excluded reply", "renamed the channel")
    for gone in gone_texts:
        assert gone not in text
    for kept in ("human question", "thanks", "## 09:05 UTC · Linear", "FOR-1 moved"):
        assert kept in text


def test_token_without_a_bot_keeps_human_messages(tmp_path):
    fake = FakeClient()
    del fake.auth["bot_id"]
    fake.channels = [channel("C01", "seed")]
    fake.history = {"C01": [msg("2026-10-07", "09:00", "human"), bot_msg("2026-10-07", "09:05", "notify", "B2")]}
    run(fake, tmp_path)
    text = text_of(tmp_path, "C01")
    assert "human" in text
    assert "notify" in text


def test_left_or_excluded_channel_is_removed(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed"), channel("C02", "eng"), channel("C03", "ops")]
    fake.history = {cid: [msg("2026-10-07", "09:00", "hi")] for cid in ("C01", "C02", "C03")}
    run(fake, tmp_path)
    fake.channels[1]["is_member"] = False
    report = run(fake, tmp_path)
    assert report["removed"] == ["C02"]
    assert "removal_skipped" not in report
    assert folders(tmp_path) == ["C01", "C03"]
    assert not (tmp_path / ".state" / "C02.json").exists()
    assert (tmp_path / ".state" / "C01.json").exists()
    report = run(fake, tmp_path, exclude=("seed",))
    assert report["removed"] == ["C01"]
    assert folders(tmp_path) == ["C03"]
    assert not (tmp_path / ".state" / "C01.json").exists()


def test_folder_not_named_like_a_channel_survives(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    fake.history = {"C01": [msg("2026-10-07", "09:00", "hi")]}
    for name in ("notes", "C1", "c02abc"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "keep.md").write_text("mine")
    report = run(fake, tmp_path)
    assert report["removed"] == []
    for name in ("notes", "C1", "c02abc"):
        assert (tmp_path / name / "keep.md").read_text() == "mine"


def test_zero_kept_channels_skips_removal(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed", member=False)]
    (tmp_path / "C01").mkdir()
    (tmp_path / "C01" / "2026-10-01.md").write_text("kept")
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "C01.json").write_text("{}")
    report = run(fake, tmp_path)
    assert report == {"channels": 0, "messages": 0, "removed": [], "unreadable": {}, "removal_skipped": True}
    assert (tmp_path / "C01" / "2026-10-01.md").read_text() == "kept"
    assert (tmp_path / ".state" / "C01.json").exists()


def test_listing_failure_deletes_nothing(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed"), channel("C02", "eng"), channel("C03", "ops")]
    fake.history = {"C01": [msg("2026-10-07", "09:00", "hi")]}
    (tmp_path / "C09").mkdir()
    (tmp_path / "C09" / "2026-10-01.md").write_text("kept")
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "C09.json").write_text("{}")
    fake.list_fail_page = 2
    with pytest.raises(ExportError, match="Slack channel listing failed: internal_error"):
        run(fake, tmp_path)
    assert (tmp_path / "C09" / "2026-10-01.md").read_text() == "kept"
    assert (tmp_path / ".state" / "C09.json").exists()
    assert folders(tmp_path) == ["C09"]
    assert fake.history_calls("C01") == []


@pytest.mark.parametrize("method", ["auth_test", "users_list"])
def test_auth_or_user_listing_failure_deletes_nothing(tmp_path, method):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]

    def revoked(**kwargs):
        raise SlackApiError("x", {"ok": False, "error": "invalid_auth"})

    setattr(fake, method, revoked)
    (tmp_path / "C09").mkdir()
    with pytest.raises(ExportError, match="Slack channel listing failed: invalid_auth"):
        run(fake, tmp_path)
    assert folders(tmp_path) == ["C09"]


def test_one_channel_failing_is_unreadable_others_written(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed"), channel("C02", "eng")]
    fake.history = {"C01": [msg("2026-10-06", "09:00", "c1 old")], "C02": [msg("2026-10-06", "09:00", "c2 old")]}
    run(fake, tmp_path)
    before = files(tmp_path / "C02")
    state_before = (tmp_path / ".state" / "C02.json").read_bytes()
    new = msg("2026-10-07", "09:00", "c1 new")
    fake.history["C01"].append(new)
    fake.history["C02"] = [msg("2026-10-07", "10:00", "c2 new")]
    fake.fail = {"C02": "ratelimited"}
    report = run(fake, tmp_path)
    assert report["unreadable"] == {"C02": "ratelimited"}
    assert report["removed"] == []
    assert permalink(BASE, "C01", new["ts"]) in (tmp_path / "C01" / "2026-10-07.md").read_text()
    assert files(tmp_path / "C02") == before
    assert (tmp_path / ".state" / "C02.json").read_bytes() == state_before


def test_replies_failure_keeps_the_channels_files(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    fake.history = {"C01": [msg("2026-10-06", "09:00", "old")]}
    run(fake, tmp_path)
    before = files(tmp_path / "C01")
    parent = msg("2026-10-07", "09:00", "thread", reply_count=1)
    fake.history["C01"] = [parent]

    def broken(**kwargs):
        raise SlackApiError("x", {"ok": False, "error": "fatal_error"})

    fake.conversations_replies = broken
    report = run(fake, tmp_path)
    assert report["unreadable"] == {"C01": "fatal_error"}
    assert files(tmp_path / "C01") == before


def threaded(fake: FakeClient) -> list[dict]:
    parent = msg("2026-10-05", "09:00", "parent", reply_count=1)
    reply = msg("2026-10-05", "09:30", "reply", user="U2", thread_ts=parent["ts"])
    today = msg("2026-10-07", "09:00", "today")
    fake.channels = [channel("C01", "seed"), channel("C02", "eng")]
    fake.history = {"C01": [parent, today], "C02": [msg("2026-10-07", "10:00", "eng")]}
    fake.replies = {("C01", parent["ts"]): [parent, reply]}
    return [parent, reply, today]


def test_crash_before_cursor_write_repeats_cleanly(tmp_path, monkeypatch):
    clean, out = tmp_path / "clean", tmp_path / "out"
    fake = FakeClient()
    messages = threaded(fake)
    run(fake, clean)
    real = slack_export._write_cursor
    crashed = []

    def crash_once(out_dir, cid, state):
        if cid == "C01" and not crashed:
            crashed.append(cid)
            raise RuntimeError("killed")
        real(out_dir, cid, state)

    monkeypatch.setattr(slack_export, "_write_cursor", crash_once)
    with pytest.raises(RuntimeError):
        run(fake, out)
    assert (out / "C01").is_dir()
    assert not (out / ".state" / "C01.json").exists()
    run(fake, out)
    assert files(out / "C01") == files(clean / "C01")
    text = text_of(out, "C01")
    assert all(count(text, m) == 1 for m in messages)


def test_rename_keeps_one_folder(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    fake.history = {"C01": [msg("2026-10-06", "09:00", "before rename")]}
    run(fake, tmp_path)
    fake.channels[0]["name"] = "seed-formula"
    fake.history["C01"].append(msg("2026-10-07", "09:00", "after rename"))
    run(fake, tmp_path)
    assert folders(tmp_path) == ["C01"]
    assert (tmp_path / "C01" / "2026-10-07.md").read_text().startswith("# #seed-formula, 2026-10-07\n")
    assert cursor(tmp_path, "C01")["name"] == "seed-formula"
    assert cursor(tmp_path, "C01")["shared"] is False


def test_history_pages_are_all_read(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    day = [msg("2026-10-07", f"0{h}:00", f"message {h}") for h in range(1, 6)]
    fake.history = {"C01": list(day)}
    run(fake, tmp_path)
    text = (tmp_path / "C01" / "2026-10-07.md").read_text()
    assert all(count(text, m) == 1 for m in day)
    assert [kw["cursor"] for kw in fake.history_calls("C01")] == [None, "2", "4"]
    positions = [text.index(permalink(BASE, "C01", m["ts"])) for m in day]
    assert positions == sorted(positions)


def test_report_counts(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed"), channel("C02", "eng")]
    parent = msg("2026-10-07", "09:00", "parent", reply_count=1)
    fake.history = {"C01": [parent, msg("2026-10-06", "09:00", "other")], "C02": [msg("2026-10-07", "10:00", "eng")]}
    reply = msg("2026-10-07", "09:30", "reply", user="U2", thread_ts=parent["ts"])
    fake.replies = {("C01", parent["ts"]): [parent, reply]}
    assert run(fake, tmp_path) == {"channels": 2, "messages": 4, "removed": [], "unreadable": {}}


def test_temp_files_end_in_tmp(tmp_path, monkeypatch):
    # llmdex indexes .md and .json even in a dot folder, so a temp a crash leaves behind must not be either.
    fake = FakeClient()
    fake.channels = [channel("C01", "seed")]
    fake.history = {"C01": [msg("2026-10-07", "09:00", "hi")]}
    real, seen = os.replace, []

    def spy(src, dst):
        seen.append((Path(src), Path(dst), sorted(p.name for p in Path(dst).parent.iterdir())))
        real(src, dst)

    monkeypatch.setattr(slack_export.os, "replace", spy)
    run(fake, tmp_path)
    assert [dst.relative_to(tmp_path).as_posix() for _, dst, _ in seen] == ["C01/2026-10-07.md", ".state/C01.json"]
    for src, dst, listing in seen:
        assert src.parent == dst.parent
        assert src.name.endswith(".tmp")
        assert listing == [src.name]


def test_failed_write_removes_its_temp(tmp_path, monkeypatch):
    def crash(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(slack_export.os, "replace", crash)
    with pytest.raises(OSError):
        slack_export._write_atomic(tmp_path / "C01" / "2026-10-07.md", "x")
    assert list((tmp_path / "C01").iterdir()) == []


def test_never_joins(tmp_path):
    fake = FakeClient()
    fake.channels = [channel("C01", "seed"), channel("C02", "general", member=False)]
    fake.history = {"C01": [msg("2026-10-07", "09:00", "hi")], "C02": [msg("2026-10-07", "09:00", "hi")]}
    run(fake, tmp_path)
    assert [m for m, _ in fake.calls if m == "conversations_join"] == []
