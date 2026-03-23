"""Linear exporter - issues with comments and history."""

from pathlib import Path
from datetime import date
import time
import httpx
import click


def _synced_today(path: Path) -> bool:
    """Check if a file was modified today."""
    if not path.exists():
        return False
    mtime = date.fromtimestamp(path.stat().st_mtime)
    return mtime == date.today()

API_URL = "https://api.linear.app/graphql"
MAX_RETRIES = 5


def _query(token: str, query: str, variables: dict | None = None) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.post(
                API_URL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": token, "Content-Type": "application/json"},
                timeout=30,
            )
            data = resp.json()
            if resp.status_code >= 400:
                errors = data.get("errors", [])
                if errors:
                    for err in errors:
                        click.echo(f"  GraphQL error: {err.get('message', err)}", err=True)
                        if err.get("extensions"):
                            click.echo(f"    {err['extensions']}", err=True)
                resp.raise_for_status()
            if "errors" in data:
                raise click.ClickException(f"Linear API error: {data['errors']}")
            return data["data"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                click.echo(f"    Retrying in {wait}s (HTTP {e.response.status_code})...")
                time.sleep(wait)
                continue
            raise
        except httpx.TransportError:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                click.echo(f"    Retrying in {wait}s (connection error)...")
                time.sleep(wait)
                continue
            raise


def _get_teams(token: str) -> list[dict]:
    data = _query(token, """
        query { teams { nodes { id key name } } }
    """)
    return data["teams"]["nodes"]


def _get_labels(token: str) -> dict[str, str]:
    """Fetch all workspace labels, return {id: name} map."""
    labels = {}
    after = None
    while True:
        variables: dict = {"after": after}
        data = _query(token, """
            query($after: String) {
                issueLabels(first: 100, after: $after) {
                    nodes { id name }
                    pageInfo { hasNextPage endCursor }
                }
            }
        """, variables)
        for l in data["issueLabels"]["nodes"]:
            labels[l["id"]] = l["name"]
        page = data["issueLabels"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]
    return labels


def _get_issues(token: str, team_id: str, since: str | None = None) -> list[dict]:
    issues = []
    after = None
    while True:
        variables: dict = {"teamId": team_id, "after": after}
        if since:
            variables["since"] = since
            query_str = """
            query($teamId: ID!, $after: String, $since: DateTime!) {
                issues(
                    filter: { team: { id: { eq: $teamId } }, updatedAt: { gte: $since } }
                    first: 50
                    after: $after
                    orderBy: updatedAt"""
        else:
            query_str = """
            query($teamId: ID!, $after: String) {
                issues(
                    filter: { team: { id: { eq: $teamId } } }
                    first: 50
                    after: $after
                    orderBy: createdAt"""
        data = _query(token, query_str + """
                ) {
                    nodes {
                        id identifier title description state { name }
                        priority priorityLabel
                        assignee { name }
                        creator { name }
                        labels { nodes { name } }
                        createdAt updatedAt
                        url
                    }
                    pageInfo { hasNextPage endCursor }
                }
            }
        """, variables)
        issues.extend(data["issues"]["nodes"])
        page = data["issues"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]
    return issues


def _get_comments_and_history(token: str, issue_id: str) -> tuple[list[dict], list[dict]]:
    """Fetch comments and history for an issue in a single query."""
    data = _query(token, """
        query($issueId: String!) {
            issue(id: $issueId) {
                comments(orderBy: createdAt) {
                    nodes {
                        body
                        user { name }
                        createdAt
                    }
                }
                history {
                    nodes {
                        createdAt
                        actor { name }
                        fromState { name }
                        toState { name }
                        fromAssignee { name }
                        toAssignee { name }
                        fromPriority
                        toPriority
                        addedLabelIds
                        removedLabelIds
                    }
                }
            }
        }
    """, {"issueId": issue_id})
    issue = data["issue"]
    return issue["comments"]["nodes"], issue["history"]["nodes"]


def _format_history(history: list[dict], label_map: dict[str, str]) -> str:
    lines = []
    for h in history:
        date = h["createdAt"][:16].replace("T", " ")
        actor = h.get("actor", {})
        actor_name = actor.get("name", "system") if actor else "system"
        changes = []

        if h.get("fromState") and h.get("toState"):
            changes.append(f"status: {h['fromState']['name']} -> {h['toState']['name']}")
        if h.get("fromAssignee") or h.get("toAssignee"):
            fr = h.get("fromAssignee", {})
            to = h.get("toAssignee", {})
            fr_name = fr.get("name", "unassigned") if fr else "unassigned"
            to_name = to.get("name", "unassigned") if to else "unassigned"
            changes.append(f"assignee: {fr_name} -> {to_name}")
        for lid in h.get("addedLabelIds") or []:
            name = label_map.get(lid, lid[:8])
            changes.append(f"+label `{name}`")
        for lid in h.get("removedLabelIds") or []:
            name = label_map.get(lid, lid[:8])
            changes.append(f"-label `{name}`")

        if changes:
            lines.append(f"- **{date}** {actor_name}: {', '.join(changes)}")
    return "\n".join(lines) or "*(no history)*"


def _slugify(text: str) -> str:
    slug = text.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:60]


def export_linear(token: str, out: Path, *, teams: list[str] | None = None, since: str | None = None):
    click.echo("Fetching Linear teams...")
    all_teams = _get_teams(token)

    if teams:
        all_teams = [t for t in all_teams if t["key"] in teams]

    if not all_teams:
        raise click.ClickException("No matching teams found")

    if since:
        click.echo(f"Incremental sync since {since[:19].replace('T', ' ')}")

    click.echo(f"Exporting {len(all_teams)} team(s): {', '.join(t['key'] for t in all_teams)}")

    label_map = _get_labels(token)

    for team in all_teams:
        team_dir = out / team["key"]
        team_dir.mkdir(parents=True, exist_ok=True)

        click.echo(f"\nTeam: {team['name']} ({team['key']})")
        issues = _get_issues(token, team["id"], since=since)
        click.echo(f"  Found {len(issues)} issues")

        skipped = 0
        for i, issue in enumerate(issues):
            fname = f"{issue['identifier']}-{_slugify(issue['title'])}.md"
            if _synced_today(team_dir / fname):
                skipped += 1
                continue
            comments, history = _get_comments_and_history(token, issue["id"])

            labels = ", ".join(l["name"] for l in issue.get("labels", {}).get("nodes", []))
            assignee = issue.get("assignee", {})
            assignee_name = assignee.get("name", "unassigned") if assignee else "unassigned"
            creator = issue.get("creator", {})
            creator_name = creator.get("name", "unknown") if creator else "unknown"
            state = issue.get("state", {})
            state_name = state.get("name", "unknown") if state else "unknown"
            created = issue["createdAt"][:10]
            description = issue.get("description") or "*(no description)*"

            comments_md = ""
            for c in comments:
                c_date = c["createdAt"][:16].replace("T", " ")
                c_user = c.get("user", {})
                c_name = c_user.get("name", "unknown") if c_user else "unknown"
                comments_md += f"### {c_name} - {c_date}\n\n{c.get('body', '')}\n\n"

            md = f"""# {issue['identifier']} - {issue['title']}

- **State:** {state_name}
- **Priority:** {issue.get('priorityLabel', 'none')}
- **Labels:** {labels or 'none'}
- **Assignee:** {assignee_name}
- **Creator:** {creator_name}
- **Created:** {created}
- **URL:** {issue.get('url', '')}

## Description

{description}

## History

{_format_history(history, label_map)}

## Comments

{comments_md or '*(no comments)*'}
"""
            (team_dir / fname).write_text(md)

            if (i + 1) % 25 == 0:
                click.echo(f"  Exported {i + 1} issues...")

        click.echo(f"  Exported {len(issues) - skipped} issues total" + (f" (skipped {skipped} existing)" if skipped else ""))

    click.echo(f"\nDone! Output: {out}")
