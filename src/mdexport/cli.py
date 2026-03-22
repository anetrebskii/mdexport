import click
import os
from pathlib import Path
from dotenv import load_dotenv


@click.group()
@click.pass_context
def cli(ctx):
    """Export GitHub, Slack, and Linear data to local Markdown files."""
    load_dotenv()
    ctx.ensure_object(dict)


# --- add ---

@cli.group()
def add():
    """Register a new export source."""


@add.command("github")
@click.argument("repo")
@click.option("--name", "-n", help="Custom name (default: derived from repo)")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="GITHUB_TOKEN", help="GitHub token (or set GITHUB_TOKEN)")
@click.option("--issues/--no-issues", default=True)
@click.option("--prs/--no-prs", default=True)
@click.option("--wiki/--no-wiki", default=True)
def add_github(repo, name, output, token, issues, prs, wiki):
    """Add a GitHub repo export. REPO is owner/name."""
    from mdexport.registry import register

    if not token:
        token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise click.ClickException("GitHub token required. Set GITHUB_TOKEN or pass --token.")

    name = name or repo.replace("/", "-")
    out = str(Path(output).resolve())

    register(name, {
        "type": "github",
        "repo": repo,
        "output": out,
        "token_env": "GITHUB_TOKEN",
        "issues": issues,
        "prs": prs,
        "wiki": wiki,
    })
    click.echo(f"Added GitHub export '{name}': {repo} -> {out}")


@add.command("slack")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="SLACK_TOKEN", help="Slack user token xoxp-... (or set SLACK_TOKEN)")
@click.option("--channel", "-c", multiple=True, help="Channels (default: all public)")
@click.option("--days", type=int, default=90, help="Days of history (default: 90)")
def add_slack(name, output, token, channel, days):
    """Add a Slack workspace export."""
    from mdexport.registry import register

    if not token:
        token = os.environ.get("SLACK_TOKEN")
    if not token:
        raise click.ClickException("Slack user token required. Set SLACK_TOKEN or pass --token.")

    out = str(Path(output).resolve())

    register(name, {
        "type": "slack",
        "output": out,
        "token_env": "SLACK_TOKEN",
        "channels": list(channel) if channel else None,
        "days": days,
    })
    click.echo(f"Added Slack export '{name}' -> {out}")


@add.command("linear")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="LINEAR_TOKEN", help="Linear API key (or set LINEAR_TOKEN)")
@click.option("--team", "-t", multiple=True, help="Team keys (default: all)")
def add_linear(name, output, token, team):
    """Add a Linear export."""
    from mdexport.registry import register

    if not token:
        token = os.environ.get("LINEAR_TOKEN")
    if not token:
        raise click.ClickException("Linear API key required. Set LINEAR_TOKEN or pass --token.")

    out = str(Path(output).resolve())

    register(name, {
        "type": "linear",
        "output": out,
        "token_env": "LINEAR_TOKEN",
        "teams": list(team) if team else None,
    })
    click.echo(f"Added Linear export '{name}' -> {out}")


# --- list ---

@cli.command("list")
def list_exports():
    """List all registered exports."""
    from mdexport.registry import get_all

    exports = get_all()
    if not exports:
        click.echo("No exports registered. Use 'mdexport add' to add one.")
        return

    for name, cfg in exports.items():
        synced = cfg.get("synced_at", "never")
        if synced and synced != "never":
            synced = synced[:19].replace("T", " ")
        source = _describe_source(cfg)
        click.echo(f"  {name:20s}  {cfg['type']:8s}  {source:30s}  synced: {synced}")


def _describe_source(cfg: dict) -> str:
    t = cfg["type"]
    if t == "github":
        return cfg["repo"]
    elif t == "slack":
        channels = cfg.get("channels")
        if channels:
            return f"{len(channels)} channels"
        return "all channels"
    elif t == "linear":
        teams = cfg.get("teams")
        if teams:
            return f"teams: {', '.join(teams)}"
        return "all teams"
    return ""


# --- remove ---

@cli.command("remove")
@click.argument("name")
def remove(name):
    """Remove an export by name."""
    from mdexport.registry import unregister

    if unregister(name):
        click.echo(f"Removed '{name}'")
    else:
        raise click.ClickException(f"Export '{name}' not found")


# --- sync ---

@cli.command("sync")
@click.argument("name", required=False)
def sync(name):
    """Sync one or all exports. Run without NAME to sync all."""
    from mdexport.registry import get, get_all, update_synced

    if name:
        cfg = get(name)
        if not cfg:
            raise click.ClickException(f"Export '{name}' not found. Run 'mdexport list'.")
        _sync_one(name, cfg)
        update_synced(name)
    else:
        exports = get_all()
        if not exports:
            raise click.ClickException("No exports registered. Use 'mdexport add' to add one.")
        for n, cfg in exports.items():
            click.echo(f"\n{'=' * 60}")
            click.echo(f"Syncing: {n}")
            click.echo(f"{'=' * 60}")
            try:
                _sync_one(n, cfg)
                update_synced(n)
            except Exception as e:
                click.echo(f"Error syncing '{n}': {e}")


def _sync_one(name: str, cfg: dict):
    t = cfg["type"]
    out = Path(cfg["output"])

    if t == "github":
        token = os.environ.get(cfg.get("token_env", "GITHUB_TOKEN"))
        if not token:
            raise click.ClickException(f"Set {cfg.get('token_env', 'GITHUB_TOKEN')} env var")
        from mdexport.github import export_github
        export_github(
            cfg["repo"], token, out,
            issues=cfg.get("issues", True),
            prs=cfg.get("prs", True),
            wiki=cfg.get("wiki", True),
        )

    elif t == "slack":
        token = os.environ.get(cfg.get("token_env", "SLACK_TOKEN"))
        if not token:
            raise click.ClickException(f"Set {cfg.get('token_env', 'SLACK_TOKEN')} env var")
        from mdexport.slack import export_slack
        export_slack(
            token, out,
            channels=cfg.get("channels"),
            days=cfg.get("days", 90),
        )

    elif t == "linear":
        token = os.environ.get(cfg.get("token_env", "LINEAR_TOKEN"))
        if not token:
            raise click.ClickException(f"Set {cfg.get('token_env', 'LINEAR_TOKEN')} env var")
        from mdexport.linear import export_linear
        export_linear(
            token, out,
            teams=cfg.get("teams"),
        )

    else:
        raise click.ClickException(f"Unknown export type: {t}")
