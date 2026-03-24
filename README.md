# mdexport

Export data from GitHub, Slack, Linear, Google Docs, and Notion into local Markdown files.

mdexport is a CLI tool that pulls content from your tools and saves it as plain Markdown — readable, searchable, version-controllable. Register your sources once, then sync whenever you need fresh data.

## Install

```bash
# Clone and install in development mode
git clone https://github.com/anetrebskii/mdexporter.git
cd mdexporter
pip install -e .
```

Requires Python 3.11+.

## Quick start

```bash
# 1. Set up tokens (see "Authentication" below)
cp .env.example .env
# edit .env with your tokens

# 2. Register an export source
mdexport add github owner/repo -o ./output/github

# 3. Sync
mdexport sync
```

## Commands

### `mdexport add` — Register an export source

#### GitHub

```bash
mdexport add github owner/repo -o ./output/github

# Custom name, selective export
mdexport add github owner/repo -n my-repo -o ./output/github --no-wiki
```

Options: `--issues/--no-issues`, `--prs/--no-prs`, `--wiki/--no-wiki` (all enabled by default).

Exports issues, pull requests, and wiki pages as Markdown files.

#### Slack

```bash
mdexport add slack -n my-slack -o ./output/slack

# Specific channels, shorter history
mdexport add slack -n my-slack -o ./output/slack -c general -c engineering --days 30

# Include DMs
mdexport add slack -n my-slack -o ./output/slack --dms
```

Options: `-c` channels (default: all public), `--days` history depth (default: 90), `--dms/--no-dms`.

#### Linear

```bash
mdexport add linear -n my-linear -o ./output/linear

# Specific teams
mdexport add linear -n my-linear -o ./output/linear -t ENG -t INFRA
```

Exports issues with comments and history.

#### Google Docs

```bash
# Using gcloud credentials (recommended for personal use)
mdexport add google-docs -n my-docs -o ./output/docs

# Specific folders
mdexport add google-docs -n my-docs -o ./output/docs -f FOLDER_ID

# Service account (recommended for automation)
mdexport add google-docs -n my-docs -o ./output/docs --service-account-key ~/sa-key.json
```

Three auth methods: gcloud application-default credentials, service account key file, or OAuth token. See `mdexport add google-docs --help` for detailed setup instructions.

#### Notion

```bash
mdexport add notion -n my-notion -o ./output/notion

# Specific pages or databases
mdexport add notion -n my-notion -o ./output/notion -p PAGE_ID
mdexport add notion -n my-notion -o ./output/notion -d DATABASE_ID
```

Exports pages as Markdown. Share pages/databases with your integration to make them accessible.

### `mdexport list` — Show registered exports

```bash
mdexport list
```

### `mdexport info <name>` — Show details for an export

```bash
mdexport info my-repo
```

### `mdexport remove <name>` — Remove an export

```bash
mdexport remove my-repo
```

### `mdexport sync` — Sync exports

```bash
# Sync all registered exports
mdexport sync

# Sync a specific export
mdexport sync my-repo

# Full sync (ignore last synced timestamp)
mdexport sync --full

# Preview what would be synced
mdexport sync --dry-run
```

GitHub, Linear, Google Docs, and Notion support incremental sync — only new/updated items are fetched after the first full sync.

## Authentication

Tokens are loaded from environment variables or a `.env` file. See `.env.example` for all options.

| Source | Env var | Where to get it |
|---|---|---|
| GitHub | `GITHUB_TOKEN` | [Settings → Tokens](https://github.com/settings/tokens) (classic, `repo` scope) |
| Slack | `SLACK_TOKEN` | [Create app](https://api.slack.com/apps) → OAuth & Permissions → User Token (`xoxp-...`) |
| Linear | `LINEAR_TOKEN` | [Settings → API](https://linear.app/settings/api) → Personal API keys |
| Google Docs | `GOOGLE_TOKEN` | Service account, gcloud CLI, or [OAuth playground](https://developers.google.com/oauthplayground) |
| Notion | `NOTION_TOKEN` | [My integrations](https://www.notion.so/my-integrations) → Create integration (`ntn_...`) |

You can also pass tokens directly via `--token` when adding a source.

### Slack token setup

1. Create an app at https://api.slack.com/apps
2. Go to **OAuth & Permissions**
3. Under **User Token Scopes** (not Bot), add: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`
4. For DMs, also add: `im:history`, `im:read`, `mpim:history`, `mpim:read`
5. Install to workspace and copy the **User OAuth Token** (`xoxp-...`)

### Notion token setup

1. Go to https://www.notion.so/my-integrations
2. Create a new integration with **Read content** capability
3. Copy the token (`ntn_...`)
4. **Share** each page or database with your integration (open the page → ··· → Connections → Add your integration)

## How it works

1. **Register** — `mdexport add` saves the source config (type, token, output dir, filters) to `~/.mdexport/registry.json`
2. **Sync** — `mdexport sync` calls the appropriate exporter, which authenticates via token, paginates through the API, converts content to Markdown, and writes files to the output directory
3. **Incremental** — After the first sync, subsequent runs only fetch items modified since the last sync (where supported)

## Configuration

All export configs are stored in `~/.mdexport/registry.json`. You can edit this file directly if needed, but using the CLI commands is recommended.

## License

MIT
