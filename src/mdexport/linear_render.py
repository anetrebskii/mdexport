"""Render one Linear task as a Markdown file of `##` passages, each with its link on the next line."""

import re

from mdexport.slack_export import _escape_line

_FENCE = re.compile(r"^ {0,3}(```|~~~)\s*([^\s`]*)")
# A closing run of `#` is stripped only after whitespace, as in CommonMark, so `C#` keeps its `#`.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_NO_PRIORITY = "No priority"
_DATES = (("Created", "createdAt"), ("Updated", "updatedAt"), ("Started", "startedAt"),
          ("Completed", "completedAt"), ("Canceled", "canceledAt"), ("Archived", "archivedAt"))
# Each relation line's label and the (connection, type) pairs that feed it; other types are ignored.
_RELATION_LINES = (
    ("Blocks", (("relations", "blocks"),)),
    ("Blocked by", (("inverseRelations", "blocks"),)),
    ("Related", (("relations", "related"), ("inverseRelations", "related"))),
    ("Duplicate of", (("relations", "duplicate"),)),
    ("Duplicated by", (("inverseRelations", "duplicate"),)),
)
_OTHER_SIDE = {"relations": "relatedIssue", "inverseRelations": "issue"}


def one_line(text: str | None) -> str:
    """`text` with every run of whitespace, line breaks included, as one space; None as empty."""
    return " ".join(text.split()) if text else ""


def _name(value: dict | None) -> str:
    """`value["name"]` as one line; empty when `value` is None."""
    return one_line((value or {}).get("name"))


def _number(value: float | None) -> str:
    """`value` without a trailing `.0`; empty when None."""
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _id_key(identifier: str) -> tuple[str, int]:
    """Sort key putting `FOR-9` before `FOR-10`: the team prefix, then the number."""
    prefix, _, number = identifier.rpartition("-")
    return (prefix, int(number)) if number.isdecimal() else (identifier, -1)


def _refs(items: list[dict | None]) -> str:
    """`ID title; ID title`, sorted by identifier and deduped by it."""
    refs: dict[str, str] = {}
    pairs = [(item["identifier"], one_line(item.get("title"))) for item in items if item and item.get("identifier")]
    for identifier, title in sorted(pairs, key=lambda pair: (_id_key(pair[0]), pair[1])):
        refs.setdefault(identifier, f"{identifier} {title}".rstrip())
    return "; ".join(refs.values())


def _metadata(issue: dict) -> list[str]:
    """The task's `Name: value` lines in their fixed order, empty values left out."""
    team, cycle = issue.get("team") or {}, issue.get("cycle") or {}
    key, team_name = one_line(team.get("key")), one_line(team.get("name"))
    number, cycle_name = _number(cycle.get("number")), one_line(cycle.get("name"))
    priority = one_line(issue.get("priorityLabel"))
    labels = sorted(filter(None, map(_name, issue.get("labels") or [])), key=lambda name: (name.casefold(), name))
    related = {label: _refs([item.get(_OTHER_SIDE[connection]) for connection, kind in sources
                             for item in issue.get(connection) or [] if item.get("type") == kind])
               for label, sources in _RELATION_LINES}
    pairs = [
        ("Status", _name(issue.get("state"))),
        ("Assignee", _name(issue.get("assignee"))),
        ("Team", f"{key} ({team_name})" if team_name else key),
        ("Project", _name(issue.get("project"))),
        ("Milestone", _name(issue.get("projectMilestone"))),
        ("Cycle", f"{number} ({cycle_name})" if number and cycle_name else number),
        ("Priority", "" if priority == _NO_PRIORITY else priority),
        ("Estimate", _number(issue.get("estimate"))),
        ("Labels", ", ".join(labels)),
        ("Creator", _name(issue.get("creator"))),
        *((label, (issue.get(field) or "")[:10]) for label, field in _DATES),
        ("Parent", _refs([issue.get("parent")])),
        ("Sub-tasks", _refs(issue.get("children") or [])),
        *related.items(),
    ]
    links = sorted((one_line(item.get("title")), one_line(item.get("url"))) for item in issue.get("attachments") or [])
    return ([f"{name}: {value}" for name, value in pairs if value]
            + [f"Link: {' — '.join(filter(None, link))}" for link in links if any(link)])


def _parse(text: str | None) -> list[tuple[int, str, str]]:
    """`text` as (heading level, cleaned heading, line) items, level 0 for text; fences are already `Code:` blocks."""
    items: list[tuple[int, str, str]] = []
    marker = None
    # splitlines also breaks at \r, \r\n and \u2028, as llmdex's chunker does.
    for line in (text or "").splitlines():
        if marker:
            if line.strip().startswith(marker):
                marker = None
            else:
                items.append((0, "", "    " + line))
        elif fence := _FENCE.match(line):
            marker, lang = fence.groups()
            items.append((0, "", f"Code ({lang}):" if lang else "Code:"))
        elif heading := _HEADING.match(line):
            items.append((len(heading.group(1)), heading.group(2).strip("*_` "), line))
        else:
            items.append((0, "", line))
    return items


def _sections(description: str | None) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The description's lead lines and its (heading, lines) sections, split at its top-level headings."""
    items = _parse(description)
    top = min((level for level, _, _ in items if level), default=0)
    lead: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current = lead
    for level, heading, line in items:
        if level and level == top:
            if heading:
                current = []
                sections.append((heading, current))
        else:
            current.append(line)
    return lead, sections


def _tidy(lines: list[str]) -> list[str]:
    """`lines` escaped, without leading or trailing blank lines, and each run of blank lines as one."""
    out: list[str] = []
    for line in lines:
        if line.strip():
            out.append(_escape_line(line))
        elif out and out[-1]:
            out.append("")
    return out[:-1] if out and not out[-1] else out


def _author(comment: dict) -> str:
    """The comment's user, bot or external user name, else `unknown`."""
    for key in ("user", "botActor", "externalUser"):
        if name := _name(comment.get(key)):
            return name
    return "unknown"


def render_issue(issue: dict) -> str:
    """The task's file text: a passage for its metadata and lead, then one per description section and comment."""
    identifier, title, url = issue["identifier"], one_line(issue.get("title")), issue["url"]
    heading = f"## {identifier} · {title}"
    team, state = one_line((issue.get("team") or {}).get("key")), _name(issue.get("state"))
    updated = (issue.get("updatedAt") or "")[:10]
    head = f"# {identifier} · {title}\nteam: {team} · status: {state} · updated {updated}"
    lead, sections = _sections(issue.get("description"))
    # Only the heading and link lines are composed here; every content line is escaped.
    passages = [[heading, url, *map(_escape_line, _metadata(issue)), *_tidy(lead)]]
    for name, lines in sections:
        if body := _tidy(lines):
            passages.append([f"{heading} › {name}", url, *body])
    for comment in sorted(issue.get("comments") or [], key=lambda c: (c["createdAt"], c["id"])):
        kind = "Reply" if comment.get("parent") is not None else "Comment"
        stamp = comment["createdAt"]
        body = _tidy([line for _, _, line in _parse(comment.get("body"))]) or ["(no text)"]
        passages.append([f"{heading} › {kind} by {_author(comment)}, {stamp[:10]} {stamp[11:16]} UTC",
                         comment["url"], *body])
    return "\n\n".join([head, *("\n".join(passage) for passage in passages)]) + "\n"
