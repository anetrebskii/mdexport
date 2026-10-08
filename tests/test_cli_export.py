import json
import time

import pytest
from click.testing import CliRunner
from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler, ServerErrorRetryHandler

from mdexport import slack_export
from mdexport.cli import cli

from .fake_slack import FakeClient

TOKEN = "xoxb-test"


def recent_msg(text: str, hours_ago: int = 1) -> dict:
    return {"type": "message", "ts": f"{int(time.time()) - hours_ago * 3600}.000001", "user": "U1", "text": text}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SLACK_TOKEN", TOKEN)
    fake, seen = FakeClient(), []
    monkeypatch.setattr(slack_export, "make_client", lambda token: seen.append(token) or fake)
    return fake, seen


def invoke(tmp_path, *extra: str):
    args = ["export", "slack", "--output", str(tmp_path / "out"), "--report", str(tmp_path / "report.json"), *extra]
    return CliRunner().invoke(cli, args)


def test_missing_token(tmp_path, env, monkeypatch):
    monkeypatch.delenv("SLACK_TOKEN")
    result = invoke(tmp_path)
    assert result.exit_code == 1
    assert "SLACK_TOKEN is not set" in result.output
    assert env[1] == []
    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize(
    "days, message",
    [
        (("--history-days", "7", "--refetch-days", "14"), "--refetch-days must be at most --history-days"),
        (("--history-days", "0", "--refetch-days", "0"), "--history-days and --refetch-days must be at least 1"),
        (("--refetch-days", "0"), "--history-days and --refetch-days must be at least 1"),
    ],
)
def test_refetch_above_history(tmp_path, env, days, message):
    result = invoke(tmp_path, *days)
    assert result.exit_code == 1
    assert message in result.output
    assert env[1] == []
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "report.json").exists()


def test_success_writes_report(tmp_path, env):
    fake, seen = env
    fake.channels = [
        {"id": "C01", "name": "general", "is_member": True},
        {"id": "C09", "name": "client-acme", "is_member": True},
    ]
    fake.history = {"C01": [recent_msg("hello", 2), recent_msg("again", 1)]}
    fake.fail = {"C09": "ratelimited"}
    result = invoke(tmp_path)
    assert result.exit_code == 0, result.output
    assert result.output == "Exported 2 channels, 2 messages, 1 unreadable\n"
    report = json.loads((tmp_path / "report.json").read_text())
    assert report == {"channels": 2, "messages": 2, "removed": [], "unreadable": {"C09": "ratelimited"}}
    assert seen == [TOKEN]
    assert list((tmp_path / "out" / "C01").glob("*.md"))
    assert TOKEN not in result.output
    leaked = [p for p in tmp_path.rglob("*") if p.is_file() and TOKEN.encode() in p.read_bytes()]
    assert leaked == []
    assert not (tmp_path / ".mdexport").exists()
    assert not list(tmp_path.glob(".report.json.*"))


def test_options_reach_export(tmp_path, env):
    fake, _ = env
    fake.channels = [
        {"id": "C01", "name": "general", "is_member": True},
        {"id": "C02", "name": "random", "is_member": True},
        {"id": "C03", "name": "ops", "is_member": True},
    ]
    fake.history = {"C01": [recent_msg("kept"), {**recent_msg("skipped", 2), "user": "U2"}]}
    result = invoke(tmp_path, "--history-days", "30", "--refetch-days", "3",
                    "--exclude", "C02", "--exclude", "ops", "--exclude-user", "U2")
    assert result.exit_code == 0, result.output
    assert result.output == "Exported 1 channels, 1 messages, 0 unreadable\n"
    assert {kw["channel"] for m, kw in fake.calls if m == "conversations_history"} == {"C01"}
    oldest = float(fake.history_calls("C01")[0]["oldest"])
    assert 29 * 86400 < time.time() - oldest < 31 * 86400


def test_removal_skipped_is_echoed(tmp_path, env):
    fake, _ = env
    fake.channels = [{"id": "C01", "name": "general", "is_member": False}]
    result = invoke(tmp_path)
    assert result.exit_code == 0, result.output
    assert result.output == "Exported 0 channels, 0 messages, 0 unreadable; removal skipped: no channels kept\n"
    assert json.loads((tmp_path / "report.json").read_text())["removal_skipped"] is True


def test_listing_failure_exits_1(tmp_path, env):
    fake, _ = env
    fake.channels = [{"id": "C01", "name": "general", "is_member": True}]
    fake.list_fail_page = 1
    result = invoke(tmp_path)
    assert result.exit_code == 1
    assert "Slack channel listing failed" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "report.json").exists()


def test_make_client_retries_rate_limits_and_server_errors():
    client = slack_export.make_client(TOKEN)
    assert isinstance(client, WebClient)
    assert client.token == TOKEN
    retries = {type(h): h.max_retry_count for h in client.retry_handlers}
    assert retries[RateLimitErrorRetryHandler] == 5
    assert retries[ServerErrorRetryHandler] == 5


def test_help_lists_every_option():
    result = CliRunner().invoke(cli, ["export", "slack", "--help"])
    assert result.exit_code == 0
    for option in ("--output", "--history-days", "--refetch-days", "--exclude", "--exclude-user", "--report"):
        assert option in result.output
    assert "dedicated" in result.output
    assert "--token" not in result.output
