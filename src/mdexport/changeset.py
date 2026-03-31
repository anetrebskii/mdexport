"""Changeset writer - records what changed during each sync."""

import difflib
from datetime import datetime, timezone
from pathlib import Path


class Changeset:
    """Collects changes during a sync and writes a summary file."""

    def __init__(self, out: Path):
        self._dir = out / "_changesets"
        self._entries: list[str] = []

    def add(self, heading: str, details: list[str]):
        """Add a changed item. heading = item title, details = list of change lines."""
        if not details:
            return
        lines = [f"## {heading}\n"]
        for d in details:
            lines.append(f"- {d}")
        self._entries.append("\n".join(lines))

    def add_new(self, heading: str):
        """Record a newly created item."""
        self._entries.append(f"## {heading}\n\n*New*")

    def add_diff(self, heading: str, old_path: Path, new_text: str):
        """Add a document change by diffing old file content against new text."""
        if not old_path.exists():
            self.add_new(heading)
            return
        old_text = old_path.read_text()
        if old_text.strip() == new_text.strip():
            return
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        added = 0
        removed = 0
        changed_sections: list[str] = []
        for line in difflib.unified_diff(old_lines, new_lines, n=1):
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
                changed_sections.append(line.rstrip())
            elif line.startswith("-"):
                removed += 1
                changed_sections.append(line.rstrip())
        if not added and not removed:
            return
        parts = [f"## {heading}\n"]
        parts.append(f"*{added} lines added, {removed} lines removed*\n")
        if changed_sections:
            # Show up to 30 diff lines
            shown = changed_sections[:30]
            parts.append("```diff")
            parts.extend(shown)
            if len(changed_sections) > 30:
                parts.append(f"... and {len(changed_sections) - 30} more lines")
            parts.append("```")
        self._entries.append("\n".join(parts))

    def write(self):
        """Write the changeset file. Returns the path, or None if no changes."""
        if not self._entries:
            return None
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc)
        fname = ts.strftime("%Y-%m-%dT%H-%M-%S") + ".md"
        header = f"# Changeset — {ts.strftime('%Y-%m-%d %H:%M')} UTC\n"
        content = header + "\n" + "\n\n".join(self._entries) + "\n"
        path = self._dir / fname
        path.write_text(content)
        return path
