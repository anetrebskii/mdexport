from mdexport.slack_export import author_of, clean_text, day_of, permalink, render_day

BASE = "https://formulagrowth.slack.com/"


def test_permalink_top_level():
    assert (
        permalink(BASE, "C0123ABCD", "1791381900.123456")
        == "https://formulagrowth.slack.com/archives/C0123ABCD/p1791381900123456"
    )


def test_permalink_reply():
    assert permalink(BASE, "C0123ABCD", "1791382800.654321", thread_ts="1791381900.123456") == (
        "https://formulagrowth.slack.com/archives/C0123ABCD/p1791382800654321"
        "?thread_ts=1791381900.123456&cid=C0123ABCD"
    )


def test_permalink_adds_slash():
    assert permalink("https://x.slack.com", "C1", "1.2").startswith("https://x.slack.com/archives/")


def test_clean_text_tokens():
    text = "<@U1> see <#C2|eng> and <https://x.io|docs> <https://y.io> <!here> &lt;b&gt; &amp;"
    assert (
        clean_text(text, {"U1": "Jay"}, {})
        == "@Jay see #eng and [docs](https://x.io) https://y.io @here <b> &"
    )


def test_clean_text_fallbacks():
    text = "<@U9> <@U8|al> <#C3> <!subteam^S1|@devs>"
    assert clean_text(text, {}, {"C3": "seed"}) == "@U9 @al #seed @devs"


def test_escapes_heading_lines():
    assert clean_text("# urgent\n  ## also\nnot # this", {}, {}) == "\\# urgent\n  \\## also\nnot # this"


def test_escapes_fence_lines():
    assert clean_text("```js\nx = 1\n  ~~~\nmid ``` stays", {}, {}) == "\\```js\nx = 1\n  \\~~~\nmid ``` stays"


def test_escapes_lines_as_llmdex_splits_them():
    # llmdex's chunker also breaks lines at \r and \u2028 and strips any leading whitespace.
    text = "a\u2028```js\n\u00a0~~~\nb\r# c\r\n```pnpm lint```"
    assert clean_text(text, {}, {}) == "a\u2028\\```js\n\u00a0\\~~~\nb\r\\# c\r\n\\```pnpm lint```"


def _headings_outside_fences(text: str) -> list[str]:
    """The heading lines llmdex's chunker sees: a line starting with ``` toggles a code fence."""
    headings, fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and line.startswith("#"):
            headings.append(line)
    return headings


def test_render_day_unclosed_fence_keeps_the_next_message():
    channel = {"id": "C1", "name": "eng", "shared": False}
    first = {"ts": "1791381900.123456", "user": "U1", "text": "Run:\n```pnpm lint```\n```js\nconst hero = 'B';"}
    second = {"ts": "1791382800.654321", "user": "U2", "text": "next"}
    out = render_day(channel, "2026-10-07", [first, second], {"U1": "Jay", "U2": "Alex"}, {}, BASE)
    lines = out.splitlines()
    assert "## 14:20 UTC · Alex" in lines
    assert not any(line.lstrip().startswith("```") for line in lines)
    assert _headings_outside_fences(out) == ["# #eng, 2026-10-07", "## 14:05 UTC · Jay", "## 14:20 UTC · Alex"]


def test_day_boundary_is_utc():
    assert day_of("1791417599.000000") == "2026-10-07"
    assert day_of("1791417601.000000") == "2026-10-08"


def test_author_fallbacks():
    assert author_of({"user": "U1"}, {"U1": "Jay"}) == "Jay"
    assert author_of({"username": "Linear"}, {}) == "Linear"
    assert author_of({"bot_profile": {"name": "GitHub"}}, {}) == "GitHub"
    assert author_of({"user": "U7"}, {}) == "U7"
    assert author_of({}, {}) == "unknown"


def test_render_day_example():
    channel = {"id": "C0123ABCD", "name": "seed-formula", "shared": True}
    message = {
        "ts": "1791381900.123456",
        "user": "U1",
        "text": "Seed approved the hero test for Monday.",
        "_replies": [
            {
                "ts": "1791382800.654321",
                "thread_ts": "1791381900.123456",
                "user": "U2",
                "text": "I'll set up the variants today.",
            }
        ],
    }
    out = render_day(channel, "2026-10-07", [message], {"U1": "Jay", "U2": "Alex"}, {}, BASE)
    assert out == (
        "# #seed-formula, 2026-10-07\n"
        "channel: C0123ABCD · shared with another organisation: yes\n"
        "\n"
        "## 14:05 UTC · Jay\n"
        "https://formulagrowth.slack.com/archives/C0123ABCD/p1791381900123456\n"
        "Seed approved the hero test for Monday.\n"
        "\n"
        "> 14:20 UTC · Alex: I'll set up the variants today.\n"
        "> https://formulagrowth.slack.com/archives/C0123ABCD/p1791382800654321"
        "?thread_ts=1791381900.123456&cid=C0123ABCD\n"
    )


def test_render_day_files_and_multiline_reply():
    channel = {"id": "C1", "name": "eng", "shared": False}
    reply_link = permalink(BASE, "C1", "1791382800.654321", thread_ts="1791381900.123456")
    message = {
        "ts": "1791381900.123456",
        "user": "U1",
        "text": "see attached",
        "files": [{"name": "brief.pdf", "permalink": "https://f/1"}],
        "_replies": [
            {"ts": "1791382800.654321", "thread_ts": "1791381900.123456", "user": "U2", "text": "a\nb"}
        ],
    }
    lines = render_day(channel, "2026-10-07", [message], {"U1": "Jay", "U2": "X"}, {}, BASE).split("\n")
    assert "channel: C1 · shared with another organisation: no" in lines
    assert lines[lines.index("see attached") + 1] == "File: [brief.pdf](https://f/1)"
    start = lines.index("> 14:20 UTC · X: a")
    assert lines[start + 1 : start + 3] == ["> b", f"> {reply_link}"]


def test_render_day_file_only_message_has_no_blank_text_line():
    channel = {"id": "C1", "name": "eng", "shared": False}
    message = {"ts": "1791381900.123456", "user": "U1", "text": "", "files": [{"name": "a.png", "url_private": "https://f/2"}]}
    out = render_day(channel, "2026-10-07", [message], {}, {}, BASE)
    assert out.endswith("p1791381900123456\nFile: [a.png](https://f/2)\n")


def test_render_day_reply_files_and_empty_reply_text():
    channel = {"id": "C1", "name": "eng", "shared": False}
    parent = "1791381900.123456"
    message = {
        "ts": parent,
        "user": "U1",
        "text": "question",
        "_replies": [
            {"ts": "1791382800.654321", "thread_ts": parent, "user": "U2", "text": "",
             "files": [{"name": "shot.png", "permalink": "https://f/3"}]},
            {"ts": "1791382900.000001", "thread_ts": parent, "user": "U2", "text": "see\nthis",
             "files": [{"name": "a.pdf", "url_private": "https://f/4"}]},
        ],
    }
    out = render_day(channel, "2026-10-07", [message], {"U2": "X"}, {}, BASE)
    first = permalink(BASE, "C1", "1791382800.654321", thread_ts=parent)
    second = permalink(BASE, "C1", "1791382900.000001", thread_ts=parent)
    assert out.endswith(
        "question\n"
        "\n"
        "> 14:20 UTC · X:\n"
        "> File: [shot.png](https://f/3)\n"
        f"> {first}\n"
        "\n"
        "> 14:21 UTC · X: see\n"
        "> this\n"
        "> File: [a.pdf](https://f/4)\n"
        f"> {second}\n"
    )
