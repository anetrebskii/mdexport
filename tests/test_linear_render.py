from mdexport.linear_render import render_issue

URL = "https://linear.app/formula/issue/FOR-7/fix-the-hero"
META = ["Status: In Progress", "Team: FOR (Formula App)", "Created: 2026-10-01", "Updated: 2026-10-20"]

EXPECTED = """\
# FOR-7 · Fix the hero
team: FOR · status: In Progress · updated 2026-10-20

## FOR-7 · Fix the hero
https://linear.app/formula/issue/FOR-7/fix-the-hero
Status: In Progress
Assignee: Jay
Team: FOR (Formula App)
Project: Seed
Milestone: Launch
Cycle: 37
Priority: High
Estimate: 3
Labels: bug, Client
Creator: Alex
Created: 2026-10-01
Updated: 2026-10-20
Started: 2026-10-02
Parent: FOR-1 Epic
Sub-tasks: FOR-9 A; FOR-10 B
Blocks: VAR-3 Variant
Duplicated by: FOR-8 Dup
Link: PR #64 — https://github.com/formula-ga/workflows/pull/64
Lead line.

## FOR-7 · Fix the hero › Steps
https://linear.app/formula/issue/FOR-7/fix-the-hero
1. Do it
\\### Detail
Code (py):
    print(1)

## FOR-7 · Fix the hero › Done
https://linear.app/formula/issue/FOR-7/fix-the-hero
- [ ] Shipped

## FOR-7 · Fix the hero › Comment by Jay, 2026-10-03 11:00 UTC
https://linear.app/formula/issue/FOR-7/fix-the-hero#comment-aaaa1111
\\## Plan
Ship it

## FOR-7 · Fix the hero › Reply by GitHub, 2026-10-03 12:30 UTC
https://linear.app/formula/issue/FOR-7/fix-the-hero#comment-bbbb2222
Done
"""


def full() -> dict:
    return {
        "identifier": "FOR-7", "title": "Fix the hero", "url": URL,
        "description": "Lead line.\n\n## Steps\n1. Do it\n### Detail\n```py\nprint(1)\n```\n## Done\n- [ ] Shipped",
        "priorityLabel": "High", "estimate": 3.0, "trashed": False, "archivedAt": None,
        "createdAt": "2026-10-01T10:00:00.000Z", "updatedAt": "2026-10-20T08:00:00.000Z",
        "startedAt": "2026-10-02T09:00:00.000Z", "completedAt": None, "canceledAt": None,
        "state": {"name": "In Progress"}, "team": {"key": "FOR", "name": "Formula App"},
        "assignee": {"name": "Jay"}, "creator": {"name": "Alex"},
        "project": {"name": "Seed"}, "projectMilestone": {"name": "Launch"}, "cycle": {"number": 37, "name": None},
        "labels": [{"name": "bug"}, {"name": "Client"}], "parent": {"identifier": "FOR-1", "title": "Epic"},
        "children": [{"identifier": "FOR-10", "title": "B"}, {"identifier": "FOR-9", "title": "A"}],
        "relations": [{"type": "blocks", "relatedIssue": {"identifier": "VAR-3", "title": "Variant"}}],
        "inverseRelations": [{"type": "duplicate", "issue": {"identifier": "FOR-8", "title": "Dup"}}],
        "attachments": [{"title": "PR #64", "url": "https://github.com/formula-ga/workflows/pull/64"}],
        "comments": [
            {"id": "c2", "url": URL + "#comment-bbbb2222", "body": "Done", "createdAt": "2026-10-03T12:30:00.000Z",
             "parent": {"id": "c1"}, "user": None, "botActor": {"name": "GitHub"}, "externalUser": None},
            {"id": "c1", "url": URL + "#comment-aaaa1111", "body": "## Plan\nShip it",
             "createdAt": "2026-10-03T11:00:00.000Z", "parent": None, "user": {"name": "Jay"}, "botActor": None,
             "externalUser": None},
        ],
    }


def bare(**fields) -> dict:
    """`full()` with every optional field null or empty, so its first passage holds only `META`."""
    empty = {"description": None, "assignee": None, "project": None, "projectMilestone": None, "cycle": None,
             "creator": None, "parent": None, "estimate": None, "startedAt": None, "completedAt": None,
             "canceledAt": None, "archivedAt": None, "priorityLabel": "No priority", "labels": [], "children": [],
             "relations": [], "inverseRelations": [], "attachments": [], "comments": []}
    return {**full(), **empty, **fields}


def comment(comment_id: str, body: str | None = "hi", minute: int = 0, **fields) -> dict:
    raw = {"id": comment_id, "url": f"{URL}#comment-{comment_id}", "body": body,
           "createdAt": f"2026-10-03T11:{minute:02d}:00.000Z", "parent": None, "user": {"name": "Jay"},
           "botActor": None, "externalUser": None}
    return {**raw, **fields}


def passages(text: str) -> list[tuple[str, list[str]]]:
    """Each passage as its heading's part after ` › ` (empty for the first) and its lines after the link."""
    out = []
    for block in text.rstrip("\n").split("\n\n## ")[1:]:
        heading, _link, *body = block.split("\n")
        out.append((heading.partition(" › ")[2], body))
    return out


def described(description: str) -> list[tuple[str, list[str]]]:
    """The passages of a bare task with `description`, the first one's metadata lines dropped."""
    (_, first), *rest = passages(render_issue(bare(description=description)))
    assert first[:4] == META
    return [("", first[4:]), *rest]


def test_full_task_matches_expected_file():
    assert render_issue(full()) == EXPECTED


def test_every_heading_followed_by_link():
    lines = render_issue(full()).split("\n")
    headings = [i for i, line in enumerate(lines) if line.startswith("## ")]
    assert len(headings) == 5
    assert all(lines[i + 1].startswith("https://") for i in headings)
    assert not [line for line in lines if line.strip().startswith(("```", "~~~"))]


def test_null_fields_left_out():
    assert passages(render_issue(bare())) == [("", META)]


def test_title_newlines_collapsed_and_unicode_kept():
    text = render_issue({**full(), "title": "# Fix\n  the 🚀 hero"})
    lines = text.split("\n")
    assert lines[0] == "# FOR-7 · # Fix the 🚀 hero"
    headings = [line for line in lines if line.startswith("## ")]
    assert len(headings) == 5
    assert all(line.startswith("## FOR-7 · # Fix the 🚀 hero") for line in headings)


def test_top_level_is_smallest_level():
    assert described("intro\n### A\nx\n#### B\ny\n### C\nz") == [
        ("", ["intro"]), ("A", ["x", "\\#### B", "y"]), ("C", ["z"])]


def test_heading_inside_code_not_split():
    assert described("```\n## not a heading\n```\n## Real\nbody") == [
        ("", ["Code:", "    \\## not a heading"]), ("Real", ["body"])]


def test_empty_sections_dropped():
    assert described("## Empty\n\n## Full\ntext\n##   \nmore") == [("", []), ("Full", ["text", "more"])]


def test_trailing_hash_kept_without_space():
    assert described("## Support C#\nx\n## Title ##\ny") == [("", []), ("Support C#", ["x"]), ("Title", ["y"])]


def test_no_headings_all_lead():
    assert described("one\n\n\n\ntwo") == [("", ["one", "", "two"])]


def test_unclosed_fence_runs_to_end():
    assert described("~~~\na\n## b") == [("", ["Code:", "    a", "    \\## b"])]


def test_crlf_and_indented_fence():
    description = "x\r\n   ```js\r\nlet a\r\n   ```\r\n## H\r\ny"
    assert described(description) == [("", ["x", "Code (js):", "    let a"]), ("H", ["y"])]
    assert "\r" not in render_issue(bare(description=description))


def test_line_openers_escaped():
    assert described("#eng is on it\nplain") == [("", ["\\#eng is on it", "plain"])]
    rendered = passages(render_issue(bare(comments=[comment("c1", "```")])))
    assert rendered[1] == ("Comment by Jay, 2026-10-03 11:00 UTC", ["Code:"])


def test_comment_authors_and_reply():
    comments = [
        comment("c1", minute=1),
        comment("c2", minute=2, user=None, botActor={"name": "GitHub"}),
        comment("c3", minute=3, user=None, externalUser={"name": "Ext"}),
        comment("c4", minute=4, user=None),
        comment("c5", minute=5, parent={"id": "c1"}),
    ]
    headings = [heading for heading, _ in passages(render_issue(bare(comments=comments)))[1:]]
    assert headings == [
        "Comment by Jay, 2026-10-03 11:01 UTC",
        "Comment by GitHub, 2026-10-03 11:02 UTC",
        "Comment by Ext, 2026-10-03 11:03 UTC",
        "Comment by unknown, 2026-10-03 11:04 UTC",
        "Reply by Jay, 2026-10-03 11:05 UTC",
    ]


def test_comment_empty_body():
    comments = [comment("c1", None, minute=1), comment("c2", "", minute=2)]
    bodies = [body for _, body in passages(render_issue(bare(comments=comments)))[1:]]
    assert bodies == [["(no text)"], ["(no text)"]]


def test_relations_by_type_and_direction():
    def ref(n: int) -> dict:
        return {"identifier": f"X-{n}", "title": f"T{n}"}

    relations = [{"type": "blocks", "relatedIssue": ref(1)}, {"type": "related", "relatedIssue": ref(2)},
                 {"type": "duplicate", "relatedIssue": ref(3)}, {"type": "similar", "relatedIssue": ref(9)}]
    inverse = [{"type": "blocks", "issue": ref(4)}, {"type": "related", "issue": ref(2)},
               {"type": "duplicate", "issue": ref(5)}]
    text = render_issue(bare(relations=relations, inverseRelations=inverse))
    assert passages(text) == [("", META + ["Blocks: X-1 T1", "Blocked by: X-4 T4", "Related: X-2 T2",
                                           "Duplicate of: X-3 T3", "Duplicated by: X-5 T5"])]
    assert "X-9" not in text


def test_cycle_name_and_estimate():
    text = render_issue(bare(cycle={"number": 4, "name": "Sprint"}, estimate=2.5))
    assert passages(text) == [("", META[:2] + ["Cycle: 4 (Sprint)", "Estimate: 2.5"] + META[2:])]


def test_link_without_title():
    text = render_issue(bare(attachments=[{"title": "", "url": "https://x"}]))
    assert passages(text) == [("", META + ["Link: https://x"])]


def test_render_is_deterministic():
    task = full()
    for key in ("labels", "children", "relations", "attachments", "comments"):
        task[key] = task[key][::-1]
    assert render_issue(task) == render_issue(full())
