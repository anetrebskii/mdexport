"""GitHub exporter - issues, PRs, wiki."""

from pathlib import Path
from github import Github, Auth
import subprocess
import click


def _format_comments(comments) -> str:
    lines = []
    for c in comments:
        date = c.created_at.strftime("%Y-%m-%d %H:%M")
        lines.append(f"### @{c.user.login} - {date}\n")
        lines.append(c.body or "*(empty)*")
        lines.append("")
    return "\n".join(lines)


def _format_reviews(pr) -> str:
    lines = []
    for r in pr.get_reviews():
        date = r.submitted_at.strftime("%Y-%m-%d %H:%M") if r.submitted_at else "unknown"
        lines.append(f"### Review by @{r.user.login} - {r.state} - {date}\n")
        lines.append(r.body or "*(no body)*")
        lines.append("")
    for c in pr.get_review_comments():
        date = c.created_at.strftime("%Y-%m-%d %H:%M")
        path = c.path or ""
        lines.append(f"### Review comment by @{c.user.login} on `{path}` - {date}\n")
        lines.append(c.body or "*(empty)*")
        lines.append("")
    return "\n".join(lines)


def _format_events(events) -> str:
    lines = []
    for e in events:
        date = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else ""
        actor = f"@{e.actor.login}" if e.actor else "system"
        detail = ""
        if e.event == "labeled":
            detail = f" `{e.label.name}`" if hasattr(e, "label") and e.label else ""
        elif e.event == "renamed":
            rename = e.rename if hasattr(e, "rename") and e.rename else {}
            detail = f" from \"{rename.get('from', '')}\" to \"{rename.get('to', '')}\""
        elif e.event == "assigned":
            detail = f" to @{e.assignee.login}" if hasattr(e, "assignee") and e.assignee else ""
        lines.append(f"- **{e.event}** by {actor} {date}{detail}")
    return "\n".join(lines)


def _export_issues(repo, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    issues = repo.get_issues(state="all", sort="created", direction="desc")
    count = 0
    for issue in issues:
        if issue.pull_request:
            continue
        labels = ", ".join(l.name for l in issue.labels)
        created = issue.created_at.strftime("%Y-%m-%d")
        body = issue.body or "*(no description)*"

        md = f"""# #{issue.number} - {issue.title}

- **State:** {issue.state}
- **Labels:** {labels or "none"}
- **Author:** @{issue.user.login}
- **Created:** {created}
- **URL:** {issue.html_url}

## Description

{body}

## Timeline

{_format_events(issue.get_timeline())}

## Comments

{_format_comments(issue.get_comments())}
"""
        fname = f"{issue.number:04d}-{_slugify(issue.title)}.md"
        (out / fname).write_text(md)
        count += 1
        if count % 25 == 0:
            click.echo(f"  Exported {count} issues...")
    click.echo(f"  Exported {count} issues total")


def _export_prs(repo, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    pulls = repo.get_pulls(state="all", sort="created", direction="desc")
    count = 0
    for pr in pulls:
        labels = ", ".join(l.name for l in pr.labels)
        created = pr.created_at.strftime("%Y-%m-%d")
        merged = pr.merged_at.strftime("%Y-%m-%d") if pr.merged_at else "not merged"
        body = pr.body or "*(no description)*"

        md = f"""# PR #{pr.number} - {pr.title}

- **State:** {pr.state} (merged: {merged})
- **Labels:** {labels or "none"}
- **Author:** @{pr.user.login}
- **Created:** {created}
- **Base:** {pr.base.ref} <- {pr.head.ref}
- **URL:** {pr.html_url}

## Description

{body}

## Reviews

{_format_reviews(pr)}

## Comments

{_format_comments(pr.get_issue_comments())}
"""
        fname = f"{pr.number:04d}-{_slugify(pr.title)}.md"
        (out / fname).write_text(md)
        count += 1
        if count % 25 == 0:
            click.echo(f"  Exported {count} PRs...")
    click.echo(f"  Exported {count} PRs total")


def _export_wiki(repo_full_name: str, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    wiki_url = f"https://github.com/{repo_full_name}.wiki.git"
    target = out / "wiki"
    if target.exists():
        click.echo("  Wiki already cloned, pulling...")
        subprocess.run(["git", "-C", str(target), "pull"], capture_output=True)
    else:
        result = subprocess.run(
            ["git", "clone", wiki_url, str(target)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            if "not found" in result.stderr.lower() or "not exist" in result.stderr.lower():
                click.echo("  No wiki found for this repo")
                return
            click.echo(f"  Wiki clone failed: {result.stderr}")
            return
    md_files = list(target.glob("*.md"))
    click.echo(f"  Wiki: {len(md_files)} pages")


def _slugify(text: str) -> str:
    slug = text.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:60]


def export_github(repo_name: str, token: str, out: Path, *, issues=True, prs=True, wiki=True):
    click.echo(f"Connecting to GitHub repo: {repo_name}")
    g = Github(auth=Auth.Token(token))
    repo = g.get_repo(repo_name)
    click.echo(f"Repo: {repo.full_name} ({repo.stargazers_count} stars)")

    if issues:
        click.echo("Exporting issues...")
        _export_issues(repo, out / "issues")

    if prs:
        click.echo("Exporting pull requests...")
        _export_prs(repo, out / "prs")

    if wiki:
        click.echo("Exporting wiki...")
        _export_wiki(repo.full_name, out)

    click.echo(f"Done! Output: {out}")
