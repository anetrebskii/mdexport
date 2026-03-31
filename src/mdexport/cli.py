import click
from pathlib import Path


@click.group()
@click.pass_context
def cli(ctx):
    """Export GitHub, Slack, Linear, Google Docs, and Notion data to local Markdown files."""
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
@click.option("--dms/--no-dms", default=False, help="Include DMs and group DMs (needs im:history,im:read,mpim:history,mpim:read scopes)")
def add_slack(name, output, token, channel, days, dms):
    """Add a Slack workspace export.

    Exports messages from Slack channels to Markdown files.

    \b
    Token setup:
      1. Create app at https://api.slack.com/apps
      2. Go to OAuth & Permissions
      3. Under "User Token Scopes" (NOT Bot Token Scopes), add:
         - channels:history, channels:read (public channels)
         - groups:history, groups:read (private channels)
         - users:read (resolve user names)
         - im:history, im:read (DMs, if using --dms)
         - mpim:history, mpim:read (group DMs, if using --dms)
      4. Click "Install to Workspace" (or "Reinstall" if already installed)
      5. Copy the "User OAuth Token" (starts with xoxp-, NOT xoxb-)

    \b
    Important: Use the User token (xoxp-...), not the Bot token (xoxb-...).
    The user token accesses channels your account is in.
    The bot token only accesses channels the bot is added to.
    If you added new scopes, you must reinstall the app.

    \b
    Examples:
      mdexport add slack -n my-slack -o ./slack --token xoxp-...
      mdexport add slack -n my-slack -o ./slack -c general -c engineering
      mdexport add slack -n my-slack -o ./slack --dms --days 30
    """
    from mdexport.registry import register

    out = str(Path(output).resolve())

    register(name, {
        "type": "slack",
        "output": out,
        "token": token,
        "channels": list(channel) if channel else None,
        "days": days,
        "dms": dms,
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


@add.command("google-docs")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--service-account-key", type=click.Path(exists=True), help="Path to service account JSON key file")
@click.option("--token", envvar="GOOGLE_TOKEN", help="Google OAuth access token (alternative to service account)")
@click.option("--folder", "-f", multiple=True, help="Google Drive folder ID(s) to export from")
def add_google_docs(name, output, service_account_key, token, folder):
    """Add a Google Docs export.

    Exports Google Docs as Markdown files via the Drive API.

    \b
    Auth option 1 — Service Account (recommended for automation):
      1. Go to https://console.cloud.google.com/iam-admin/serviceaccounts
      2. Create a service account (or use existing)
      3. Keys tab -> Add Key -> Create new key -> JSON
      4. Download the JSON key file
      5. Enable the Google Drive API and Google Docs API:
         https://console.cloud.google.com/apis/library
      6. Share the folders/docs with the service account email
         (the email looks like name@project.iam.gserviceaccount.com)
      7. Use --service-account-key path/to/key.json

    \b
    Auth option 2 — gcloud (recommended for personal use):
      1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
      2. Run: gcloud auth application-default login --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/documents.readonly
      3. No --token needed! Credentials are stored automatically.

    \b
    Auth option 3 — OAuth token via playground (simplest, short-lived):
      1. Open https://developers.google.com/oauthplayground
      2. In the left panel, find and check:
         - Google Drive API v3 -> https://www.googleapis.com/auth/drive.readonly
         - Google Docs API v1 -> https://www.googleapis.com/auth/documents.readonly
      3. Click "Authorize APIs" -> sign in with your Google account
      4. Click "Exchange authorization code for tokens"
      5. Copy the "Access token" value
      6. Use: mdexport add google-docs -n NAME -o PATH --token ACCESS_TOKEN
      Note: Tokens expire after ~1 hour. Repeat steps 4-6 to refresh.

    \b
    Folder IDs:
      Open a folder in Google Drive, the ID is in the URL:
      https://drive.google.com/drive/folders/FOLDER_ID_HERE
      Without -f, exports all docs accessible to the account.

    \b
    Examples:
      mdexport add google-docs -n my-docs -o ./docs
      mdexport add google-docs -n my-docs -o ./docs -f FOLDER_ID
      mdexport add google-docs -n my-docs -o ./docs --service-account-key ~/sa-key.json
    """
    from mdexport.registry import register

    out = str(Path(output).resolve())

    cfg = {
        "type": "google-docs",
        "output": out,
        "folder_ids": list(folder) if folder else None,
    }
    if service_account_key:
        cfg["service_account_key"] = str(Path(service_account_key).resolve())
    if token:
        cfg["token"] = token

    register(name, cfg)
    click.echo(f"Added Google Docs export '{name}' -> {out}")


@add.command("notion")
@click.option("--name", "-n", required=True, help="Name for this export")
@click.option("--output", "-o", type=click.Path(), required=True, help="Output directory")
@click.option("--token", envvar="NOTION_TOKEN", required=True, help="Notion integration token (ntn_...)")
@click.option("--page", "-p", multiple=True, help="Specific page ID(s) to export")
@click.option("--database", "-d", multiple=True, help="Database ID(s) — export all pages in these databases")
def add_notion(name, output, token, page, database):
    """Add a Notion export."""
    from mdexport.registry import register

    out = str(Path(output).resolve())

    register(name, {
        "type": "notion",
        "output": out,
        "token": token,
        "page_ids": list(page) if page else None,
        "database_ids": list(database) if database else None,
    })
    click.echo(f"Added Notion export '{name}' -> {out}")


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
    elif t == "google-docs":
        folders = cfg.get("folder_ids")
        if folders:
            return f"{len(folders)} folder(s)"
        return "all accessible docs"
    elif t == "notion":
        pages = cfg.get("page_ids")
        dbs = cfg.get("database_ids")
        parts = []
        if pages:
            parts.append(f"{len(pages)} page(s)")
        if dbs:
            parts.append(f"{len(dbs)} database(s)")
        return ", ".join(parts) if parts else "all shared pages"
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
    elif cfg["type"] == "google-docs":
        folders = cfg.get("folder_ids")
        click.echo(f"Folders: {', '.join(folders) if folders else 'all accessible'}")
        if cfg.get("service_account_key"):
            auth = "service account"
        elif cfg.get("token"):
            auth = "token"
        else:
            auth = "application default credentials (gcloud)"
        click.echo(f"Auth:    {auth}")
    elif cfg["type"] == "notion":
        pages = cfg.get("page_ids")
        dbs = cfg.get("database_ids")
        if pages:
            click.echo(f"Pages:   {', '.join(pages)}")
        if dbs:
            click.echo(f"Databases: {', '.join(dbs)}")
        if not pages and not dbs:
            click.echo("Scope:   all shared pages")

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


# --- auth ---

@cli.command("auth")
@click.argument("name")
@click.option("--token", help="New token value")
@click.option("--service-account-key", type=click.Path(exists=True), help="Path to service account JSON key file (Google Docs)")
@click.option("--gcloud", is_flag=True, default=False, help="Re-authenticate via gcloud application-default login (Google Docs)")
def auth(name, token, service_account_key, gcloud):
    """Update authentication for an existing export.

    \b
    Examples:
      mdexport auth my-github --token ghp_NEW_TOKEN
      mdexport auth my-slack --token xoxp-NEW_TOKEN
      mdexport auth my-docs --gcloud
      mdexport auth my-docs --service-account-key ~/new-key.json
    """
    from mdexport.registry import get, update_config

    cfg = get(name)
    if not cfg:
        raise click.ClickException(f"Export '{name}' not found. Run 'mdexport list'.")

    if gcloud:
        if cfg["type"] != "google-docs":
            raise click.ClickException("--gcloud is only supported for google-docs exports.")
        from mdexport.googledocs import ADC_CMD
        click.echo(f"Running: {ADC_CMD}")
        import subprocess
        result = subprocess.run(ADC_CMD, shell=True)
        if result.returncode != 0:
            raise click.ClickException("gcloud auth failed.")
        # Remove stored token/key so ADC is used
        update_config(name, {"token": None, "service_account_key": None})
        click.echo(f"Auth updated for '{name}' (using application default credentials).")
        return

    if service_account_key:
        if cfg["type"] != "google-docs":
            raise click.ClickException("--service-account-key is only supported for google-docs exports.")
        update_config(name, {
            "service_account_key": str(Path(service_account_key).resolve()),
            "token": None,
        })
        click.echo(f"Auth updated for '{name}' (using service account).")
        return

    if token:
        updates = {"token": token}
        if cfg["type"] == "google-docs":
            updates["service_account_key"] = None
        update_config(name, updates)
        click.echo(f"Auth updated for '{name}'.")
        return

    raise click.ClickException(
        "Provide one of: --token, --service-account-key, or --gcloud.\n"
        "Run 'mdexport auth --help' for details."
    )


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
@click.option("--full", is_flag=True, default=False, help="Full sync, ignore last synced timestamp")
@click.option("--dry-run", is_flag=True, default=False, help="List files that would be synced without exporting")
def sync(name, full, dry_run):
    """Sync one or all exports. Run without NAME to sync all."""
    from mdexport.registry import get, get_all, update_synced

    if name:
        cfg = get(name)
        if not cfg:
            raise click.ClickException(f"Export '{name}' not found. Run 'mdexport list'.")
        if full:
            cfg["synced_at"] = None
        if dry_run:
            cfg["_dry_run"] = True
        try:
            _sync_one(name, cfg)
            if not dry_run:
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
            if full:
                cfg["synced_at"] = None
            if dry_run:
                cfg["_dry_run"] = True
            click.echo(f"\n{'=' * 60}")
            click.echo(f"Syncing: {n}")
            click.echo(f"{'=' * 60}")
            try:
                _sync_one(n, cfg)
                if not dry_run:
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
    since = cfg.get("synced_at")

    if t == "github":
        token = _resolve_token(cfg)
        from mdexport.github import export_github
        export_github(
            cfg["repo"], token, out,
            issues=cfg.get("issues", True),
            prs=cfg.get("prs", True),
            wiki=cfg.get("wiki", True),
            since=since,
        )

    elif t == "slack":
        token = _resolve_token(cfg)
        from mdexport.slack import export_slack
        export_slack(
            token, out,
            channels=cfg.get("channels"),
            days=cfg.get("days", 90),
            dms=cfg.get("dms", False),
        )

    elif t == "linear":
        token = _resolve_token(cfg)
        from mdexport.linear import export_linear
        export_linear(
            token, out,
            teams=cfg.get("teams"),
            since=since,
        )

    elif t == "google-docs":
        from mdexport.googledocs import export_google_docs
        export_google_docs(
            cfg, out,
            folder_ids=cfg.get("folder_ids"),
            since=since,
        )

    elif t == "notion":
        token = _resolve_token(cfg)
        from mdexport.notion import export_notion
        export_notion(
            token, out,
            page_ids=cfg.get("page_ids"),
            database_ids=cfg.get("database_ids"),
            since=since,
        )

    else:
        raise click.ClickException(f"Unknown export type: {t}")
