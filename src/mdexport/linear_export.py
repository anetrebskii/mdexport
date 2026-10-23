"""Stateless Linear export - one Markdown file per task of every public team, rewritten only when its text changed."""

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

# make_client is re-exported, so the CLI and tests patch this one name.
from mdexport.linear_client import LinearError, make_client
from mdexport.linear_render import render_issue
from mdexport.slack_export import _write_atomic

STAMP = "%Y-%m-%dT%H:%M:%SZ"
EXCLUDE = re.compile(r"[A-Z][A-Z0-9]*(-[0-9]+)?")
UNEXPECTED = "unexpected identifier"

_TEAM = re.compile(r"[A-Z][A-Z0-9]*")
_TASK = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")
_TASK_FILE = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+\.md")


class ExportError(Exception):
    """Linear could not be read; nothing was written or deleted."""


def _shaped(pattern: re.Pattern, value) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _team_key(task: dict):
    return (task.get("team") or {}).get("key")


def _kept(tasks: list[dict], private: set[str], excluded: set[str], unreadable: dict) -> list[dict]:
    """The tasks to write: not trashed, private or excluded; path-unsafe ones go to `unreadable` instead."""
    kept = []
    for task in tasks:
        key, identifier = _team_key(task), task.get("identifier")
        if task.get("trashed") or key in private or key in excluded or identifier in excluded:
            continue
        if not (_shaped(_TEAM, key) and _shaped(_TASK, identifier)):
            unreadable[identifier or task.get("id")] = UNEXPECTED
            continue
        kept.append(task)
    return kept


def _render(kept: list[dict], unreadable: dict) -> tuple[list[tuple], set[tuple[str, str]]]:
    """Each kept task as (file key, task, text, UTF-8 bytes), and the file keys of tasks that failed to render."""
    rendered, failed = [], set()
    for task in kept:
        file_key = (_team_key(task), f"{task['identifier']}.md")
        try:
            text = render_issue(task)
            rendered.append((file_key, task, text, text.encode("utf-8")))
        except Exception as e:
            unreadable[task["identifier"]] = f"render failed: {type(e).__name__}"
            failed.add(file_key)
    return rendered, failed


def _write_changed(path: Path, text: str, data: bytes) -> bool:
    """Write `text` to `path` unless the file already holds exactly `data`, its UTF-8; return whether it wrote."""
    if path.is_file() and path.read_bytes() == data:
        return False
    _write_atomic(path, text)
    return True


def _remove_unkept(out: Path, kept: set[tuple[str, str]]) -> list[str]:
    """Delete task-named files not kept from team-named folders, then those folders left empty; return the ids."""
    removed = []
    for folder in sorted(out.iterdir()):
        if not (_TEAM.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink()):
            continue
        for path in sorted(folder.iterdir()):
            if (_TASK_FILE.fullmatch(path.name) and (folder.name, path.name) not in kept
                    and path.is_file() and not path.is_symlink()):
                path.unlink()
                removed.append(path.stem)
        if not any(folder.iterdir()):
            folder.rmdir()
    return sorted(removed)


def export(client, out: Path, *, exclude: Iterable[str] = (), now: datetime | None = None) -> dict:
    """Export every task of every public team into `out/<team key>/<task id>.md` and return the run's report."""
    try:
        teams = client.teams()
        tasks = client.issues()
    except LinearError as e:
        raise ExportError(f"Linear listing failed: {e.code}") from e
    excluded = set(exclude)
    private = {team.get("key") for team in teams if team.get("private")}
    listed = {team.get("key") for team in teams} | {_team_key(t) for t in tasks} | {t.get("identifier") for t in tasks}
    unreadable: dict = {}
    # Every task is rendered and encoded before anything is written, so task content cannot fail a run partway.
    rendered, failed = _render(_kept(tasks, private, excluded, unreadable), unreadable)
    out.mkdir(parents=True, exist_ok=True)
    written = sum(_write_changed(out / key / name, text, data) for (key, name), _, text, data in rendered)
    # A task that failed to render keeps its old file, so its path counts as kept for removal.
    keep = {file_key for file_key, *_ in rendered} | failed
    report = {
        "teams": len({key for (key, _), *_ in rendered}),
        "issues": len(rendered),
        "comments": sum(len(task.get("comments") or []) for _, task, _, _ in rendered),
        "sections": sum(line.startswith("## ") for *_, text, _ in rendered for line in text.splitlines()),
        "written": written,
        "removed": [],
        "unreadable": unreadable,
        "removal_skipped": not keep,  # a run that keeps no task never deletes anything
        "skipped_private_teams": sorted(private),
        "excluded": sorted(excluded & listed),
    }
    if keep:
        report["removed"] = _remove_unkept(out, keep)
    report["finished_at"] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(STAMP)
    return report
