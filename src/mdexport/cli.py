import click
from pathlib import Path


@click.group()
@click.pass_context
def cli(ctx):
    """Export GitHub, Slack, and Linear data to local Markdown files."""
    ctx.ensure_object(dict)


# --- add ---

@cli.group()
def add():
    """Register a new export source."""


@add.command("github")
@click.argument("repo")
@click.option("--name", "-n", help="Custom name (default: derived from repo)")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="GITHUB_TOKEN", required=True, help="GitHub token")
@click.option("--issues/--no-issues", default=True)
@click.option("--prs/--no-prs", default=True)
@click.option("--wiki/--no-wiki", default=True)
def add_github(repo, name, output, token, issues, prs, wiki):
    """Add a GitHub repo export. REPO is owner/name."""
    from mdexport.registry import register

    name = name or repo.replace("/", "-")
    out = str(Path(output).resolve())

    register(name, {
        "type": "github",
        "repo": repo,
        "output": out,
        "token": token,
        "issues": issues,
        "prs": prs,
        "wiki": wiki,
    })
    click.echo(f"Added GitHub export '{name}': {repo} -> {out}")


@add.command("slack")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="SLACK_TOKEN", required=True, help="Slack user token (xoxp-...)")
@click.option("--channel", "-c", multiple=True, help="Channels (default: all public)")
@click.option("--days", type=int, default=90, help="Days of history (default: 90)")
def add_slack(name, output, token, channel, days):
    """Add a Slack workspace export."""
    from mdexport.registry import register

    out = str(Path(output).resolve())

    register(name, {
        "type": "slack",
        "output": out,
        "token": token,
        "channels": list(channel) if channel else None,
        "days": days,
    })
    click.echo(f"Added Slack export '{name}' -> {out}")


@add.command("linear")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="LINEAR_TOKEN", required=True, help="Linear API key")
@click.option("--team", "-t", multiple=True, help="Team keys (default: all)")
def add_linear(name, output, token, team):
    """Add a Linear export."""
    from mdexport.registry import register

    out = str(Path(output).resolve())

    register(name, {
        "type": "linear",
        "output": out,
        "token": token,
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


# --- info ---

@cli.command("info")
@click.argument("name")
def info(name):
    """Show detailed info about a registered export."""
    from mdexport.registry import get

    cfg = get(name)
    if not cfg:
        raise click.ClickException(f"Export '{name}' not found. Run 'mdexport list'.")

    click.echo(f"Name:    {name}")
    click.echo(f"Type:    {cfg['type']}")
    click.echo(f"Output:  {cfg['output']}")

    if cfg["type"] == "github":
        click.echo(f"Repo:    {cfg['repo']}")
        click.echo(f"Issues:  {'yes' if cfg.get('issues', True) else 'no'}")
        click.echo(f"PRs:     {'yes' if cfg.get('prs', True) else 'no'}")
        click.echo(f"Wiki:    {'yes' if cfg.get('wiki', True) else 'no'}")
    elif cfg["type"] == "slack":
        channels = cfg.get("channels")
        click.echo(f"Channels: {', '.join(channels) if channels else 'all public'}")
        click.echo(f"Days:    {cfg.get('days', 90)}")
    elif cfg["type"] == "linear":
        teams = cfg.get("teams")
        click.echo(f"Teams:   {', '.join(teams) if teams else 'all'}")

    added = cfg.get("added_at", "unknown")
    if added and added != "unknown":
        added = added[:19].replace("T", " ")
    synced = cfg.get("synced_at")
    if synced:
        synced = synced[:19].replace("T", " ")
    else:
        synced = "never"

    click.echo(f"Added:   {added}")
    click.echo(f"Synced:  {synced}")
    click.echo(f"Token:   {'configured' if cfg.get('token') else 'missing'}")


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
        try:
            _sync_one(name, cfg)
            update_synced(name)
        except Exception as e:
            click.echo(f"\nError: {e}", err=True)
            click.echo(f"\nTo resume, run:  mdexport sync {name}", err=True)
            click.echo("Already exported items will be skipped.", err=True)
            raise SystemExit(1)
    else:
        exports = get_all()
        if not exports:
            raise click.ClickException("No exports registered. Use 'mdexport add' to add one.")
        failed = []
        for n, cfg in exports.items():
            click.echo(f"\n{'=' * 60}")
            click.echo(f"Syncing: {n}")
            click.echo(f"{'=' * 60}")
            try:
                _sync_one(n, cfg)
                update_synced(n)
            except Exception as e:
                click.echo(f"Error syncing '{n}': {e}")
                failed.append(n)
        if failed:
            click.echo(f"\nFailed exports: {', '.join(failed)}", err=True)
            click.echo("To resume, run:", err=True)
            for n in failed:
                click.echo(f"  mdexport sync {n}", err=True)
            click.echo("Already exported items will be skipped.", err=True)
            raise SystemExit(1)


def _resolve_token(cfg: dict) -> str:
    token = cfg.get("token")
    if not token:
        raise click.ClickException(f"No token stored for '{cfg['type']}' export. Re-add with --token.")
    return token


def _sync_one(name: str, cfg: dict):
    t = cfg["type"]
    out = Path(cfg["output"])
    token = _resolve_token(cfg)
    since = cfg.get("synced_at")

    if t == "github":
        from mdexport.github import export_github
        export_github(
            cfg["repo"], token, out,
            issues=cfg.get("issues", True),
            prs=cfg.get("prs", True),
            wiki=cfg.get("wiki", True),
            since=since,
        )

    elif t == "slack":
        from mdexport.slack import export_slack
        export_slack(
            token, out,
            channels=cfg.get("channels"),
            days=cfg.get("days", 90),
        )

    elif t == "linear":
        from mdexport.linear import export_linear
        export_linear(
            token, out,
            teams=cfg.get("teams"),
            since=since,
        )

    else:
        raise click.ClickException(f"Unknown export type: {t}")
