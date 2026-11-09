# mdexport

Export data from GitHub, Slack, Telegram, Gmail, Linear, Google Docs, Notion, and Granola into local Markdown files.

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

#### Telegram

```bash
mdexport add telegram -n my-telegram -o ./output/telegram

# Specific chats, longer history, private chats too
mdexport add telegram -n my-telegram -o ./output/telegram -c @durov -c "Client group" --days 30 --dms
```

Options: `-c` chats by @username, t.me link or title (default: all), `--days` history on the first run (default: 14), `--dms/--no-dms`.

Reads with your own Telegram account over MTProto. The session is not stored here: `mdexport sync` expects `TELEGRAM_DC` and `TELEGRAM_AUTH_KEY`, which Notula Collect sets from its Telegram sign-in window. Later runs export only messages newer than the last one in each file.

#### Gmail

```bash
mdexport add gmail -n my-mail -o ./output/mail

# A search, and more history on the first run
mdexport add gmail -n my-mail -o ./output/mail -q "label:clients OR from:boss@acme.com" --days 90
```

Options: `-q` Gmail search (default: the mailbox without spam and trash), `--days` history on the first run (default: 14).

One Markdown file per thread. Needs a Google access token with `https://www.googleapis.com/auth/gmail.readonly`, in `GOOGLE_TOKEN` or `--token`; Notula Collect refreshes it before every run.

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

#### Granola

```bash
mdexport add granola -n meetings --token grn_... -p TwinsAI=~/work/twinsai/meetings -p Formula=~/work/formula/meetings

# Only some Granola folders, and send one folder's meetings straight to a place
mdexport add granola -n meetings --token grn_... -p Formula=~/work/formula/meetings -f fol_ID -l fol_ID=Formula

mdexport granola pending meetings               # meetings waiting for a place, with a guess, as JSON
mdexport granola file meetings not_ID TwinsAI   # save one; the rest of its series follows
mdexport granola file meetings not_ID Formula --series     # move a saved meeting and its whole series
mdexport granola file meetings not_ID Formula --only-this  # move just this one
mdexport granola places meetings -p Name=folder ...        # change the places
```

One file per meeting, `YYYY-MM-DD Title.md` named after the calendar event (Granola's note title when there is none), with ` (2)` for a second one that day, with the summary, your own notes and the transcript. A meeting goes to the place chosen for it, else to where its calendar series went before, else to the place linked to its Granola folder; otherwise it waits in `pending` and is not written anywhere. The key is made in Granola under Settings > API (Business plan).

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

GitHub, Telegram, Gmail, Linear, Google Docs, and Notion support incremental sync — only new/updated items are fetched after the first full sync.

### `mdexport export slack` — One-off Slack export

```bash
SLACK_TOKEN=xoxb-... mdexport export slack --output ./slack --report ./slack-report.json

# Less history, skip some channels and users
SLACK_TOKEN=xoxb-... mdexport export slack -o ./slack --report ./slack-report.json \
  --history-days 90 --refetch-days 7 --exclude random --exclude C0123ABCD --exclude-user U0456EFGH
```

Options: `--history-days` days read on a channel's first run (default: 365), `--refetch-days` recent days read again on every run to pick up edits, deletions and late thread replies (default: 14, at most `--history-days`), `--exclude` channel id or name without `#` (repeatable), `--exclude-user` user id (repeatable), `--keep-own` keeps the bot's own messages, `--dms` exports the bot's direct messages instead of channels, `--report` file for the JSON report (required).

Exports every channel the token's bot is a member of; the bot never joins a channel by itself, so invite it where it should read. The token comes only from `SLACK_TOKEN` and is never printed or stored. A bot token (`xoxb-...`) needs `channels:read`, `groups:read`, `channels:history`, `groups:history` and `users:read`. The bot's own messages are left out unless `--keep-own` is given. Nothing is read from or written to `~/.mdexport/registry.json`.

Writes one file per channel per UTC day, `OUTPUT/<channel id>/<YYYY-MM-DD>.md`, with a link to every message, and a cursor per channel in `OUTPUT/.state/`. **The output directory is dedicated to this export:** each run deletes the folders named like a channel id (`C…`/`G…`) that the bot is no longer in or that are excluded. When a run keeps no channel at all (wrong workspace, bot removed everywhere), nothing is deleted.

On success the report is written to `--report` and one line is printed:

```json
{"channels": 2, "messages": 120, "removed": ["C0OLD1234"], "unreadable": {"C0789IJKL": "ratelimited"}}
```

```
Exported 2 channels, 120 messages, 1 unreadable
```

A channel that cannot be read is listed under `unreadable` and keeps its earlier files. A run that keeps no channel adds `"removal_skipped": true` to the report and `; removal skipped: no channels kept` to the line. If the workspace, its users or its channels cannot be listed, the command exits 1, writes no report and changes nothing.

With `--dms` the run exports the bot's one-to-one direct messages instead of channels: every DM it has, deactivated users' included, except Slackbot's and those whose DM id or user id is given to `--exclude`. It needs `im:read`, `im:history` and `users:read`. Day files go to `OUTPUT/<DM id>/<YYYY-MM-DD>.md`, titled `# DM with <name>, <YYYY-MM-DD>` (the user id when no name is known). A DM run deletes only unkept folders named like a DM id (`D…`), and a channel run never deletes one. The report has `dms` in place of `channels`, and the line reads `Exported 3 DMs, 40 messages, 0 unreadable`, ending `; removal skipped: no DMs kept` when no DM is kept. A failed listing exits 1 with `Slack DM listing failed: <error>`, such as `missing_scope` without `im:read`.

### `mdexport export notion` — One-off Notion export

```bash
NOTION_TOKEN=ntn_... mdexport export notion --output ./notion --report ./notion-report.json

# Skip a page or database and everything under it
NOTION_TOKEN=ntn_... mdexport export notion -o ./notion --report ./notion-report.json \
  --exclude 1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d
```

Options: `--exclude` page or database id, with or without dashes, any case (repeatable), `--report` file for the JSON report (required).

Exports every page and database row shared with the integration (see [Notion token setup](#notion-token-setup)); sharing a page shares the pages under it, and unsharing removes them at the next run. The token comes only from `NOTION_TOKEN` and is never printed or stored. Nothing is read from or written to `~/.mdexport/registry.json`.

Writes one file per page, `OUTPUT/<root id>/<page id>.md`, in a folder per root (the topmost shared page or database above it), with a `##` item per heading and a link under each, and a state file per page in `OUTPUT/.state/`. Ids are 32 lowercase hex digits without dashes. A page is read again only when its edit time, path or the file format changed, or when it was edited within a minute of its last read. **The output directory is dedicated to this export:** a page that is no longer shared, is trashed or is excluded has its file and state deleted; a page missing from Notion's listing is first checked directly and kept while it can still be read. A root folder is removed once it holds nothing else. Only names shaped like an id are ever deleted.

On success the report is written to `--report` and one line is printed:

```json
{"roots": 12, "pages": 340, "sections": 1290, "read": 18, "removed": ["<root id>"], "pages_removed": 2,
 "unreadable": {"<page id>": "<error>"}, "root_titles": {"<root id>": "Clients"}}
```

```
Exported 12 roots, 340 pages (18 read), 1 unreadable
```

`roots` and `pages` count what is kept, `sections` the `##` items in kept files, `read` the pages read this run, `removed` the roots whose pages were all removed this run (a folder still holding other files stays on disk), `pages_removed` the page files deleted. A page that cannot be read is listed under `unreadable` and keeps its earlier file. A run that keeps no page deletes nothing and adds `"removal_skipped": true` to the report and `; removal skipped: no roots kept` to the line. If the shared pages or databases cannot be listed, the command exits 1, writes no report and changes nothing.

### `mdexport export linear` — One-off Linear export

```bash
LINEAR_TOKEN=lin_api_... mdexport export linear --output ./linear --report ./linear-report.json

# Skip a whole team and a single task
LINEAR_TOKEN=lin_api_... mdexport export linear -o ./linear --report ./linear-report.json \
  --exclude WF --exclude FOR-123
```

Options: `--exclude` team key (`WF`) or task id (`FOR-123`), any case (repeatable), `--report` file for the JSON report (required).

Exports every task of every public team the key can see, archived tasks included and trashed ones left out, with all its comments and replies. Private teams are never collected; the report names the ones skipped. The token comes only from `LINEAR_TOKEN` (a personal API key, see [Authentication](#authentication)) and is never printed or stored. Nothing is read from or written to `~/.mdexport/registry.json`.

Writes one file per task, `OUTPUT/<team key>/<task id>.md`, in a folder per team. The file opens with the task's title line, then a `##` passage holding its metadata (status, assignee, team, project, cycle, labels, dates, parent, sub-tasks, related tasks, attached links) and the description up to its first heading, then one passage per top-level description heading and one per comment, each with its link on the next line. Every run reads every task and rewrites only the files whose text changed; no state is kept. **The output directory is dedicated to this export:** a task that is no longer listed, is trashed, is in a private team or is excluded has its file deleted, and a team folder is removed once it is empty. Only names shaped like a team key or task id are ever deleted, and nothing is deleted in a run that keeps no task.

On success the report is written to `--report` and one line is printed:

```json
{"teams": 3, "issues": 1790, "comments": 4210, "sections": 7400, "written": 12, "removed": ["FOR-88"],
 "unreadable": {}, "removal_skipped": false, "skipped_private_teams": ["OPS"], "excluded": ["WF"],
 "finished_at": "2026-10-23T03:30:12Z"}
```

```
Exported 3 teams, 1790 tasks (12 written), 4210 comments
```

`teams`, `issues` and `comments` count what is kept, `sections` the `##` passages in kept files, `written` the files written this run, `removed` the task ids whose files were deleted, `skipped_private_teams` the private team keys left out, `excluded` the `--exclude` values that matched a listed team or task, and `finished_at` the run's end in UTC. A task that cannot be rendered, or whose team key or id is not shaped as expected, is listed under `unreadable`; one that fails to render keeps its earlier file. A run that keeps no task adds `"removal_skipped": true` to the report and `; removal skipped: no tasks kept` to the line. If the teams or tasks cannot be listed, the command exits 1 with `Linear listing failed: <code>`, writes no report and changes nothing.

### `mdexport export github` — One-off GitHub export

```bash
GITHUB_TOKEN=ghp_... mdexport export github --repo owner/name --output ./github --report ./github-report.json

# Issues only, skipping two numbers
GITHUB_TOKEN=ghp_... mdexport export github --repo owner/name -o ./github --report ./github-report.json \
  --no-prs --no-wiki --exclude 12 --exclude 345
```

Options: `--repo owner/name` (required), `--issues/--no-issues`, `--prs/--no-prs`, `--wiki/--no-wiki` (all on by default), `--exclude` issue or pull request number (repeatable), `--report` file for the JSON report (required).

Exports every issue and pull request of the repository, open and closed, with their comments; pull requests also carry their reviews and review comments. The wiki is cloned over HTTPS with the token sent in an HTTP header through git's environment, never in a URL or on the command line; a repository with no wiki gives 0 pages. The token comes only from `GITHUB_TOKEN` and is never printed or stored. Nothing is read from or written to `~/.mdexport/registry.json`.

Writes `OUTPUT/issues/<number>.md`, `OUTPUT/pulls/<number>.md` and `OUTPUT/wiki/<page>.md`. An issue or pull request file has a `##` passage for its metadata (state, author, labels, branches, dates) and body, then one passage per comment, review and review comment in time order, each heading followed by its GitHub link. A wiki page has one passage per top-level heading section, each followed by the page's link. Every run reads everything and rewrites only the files whose text changed; no state is kept. **The output directory is dedicated to this export:** an item no longer listed or excluded has its file deleted. Only names shaped like the export's own files are deleted, only in a kind (issues, pulls, wiki) that was read this run and keeps at least one file, and nothing is deleted in a run that keeps no item. Each run reads the reviews of every pull request with one call per pull request, so a large repository takes a while under GitHub's rate limit.

On success the report is written to `--report` and one line is printed:

```json
{"repo": "owner/name", "issues": 1800, "pulls": 2300, "wiki_pages": 40, "written": 25, "removed": ["1234"],
 "unreadable": {}, "removal_skipped": false, "finished_at": "2026-11-03T03:30:12Z"}
```

```
Exported 1800 issues, 2300 pull requests, 40 wiki pages (25 written), 0 unreadable
```

`issues`, `pulls` and `wiki_pages` count what is kept, `written` the files written this run, `removed` the stems of the deleted files, and `finished_at` the run's end in UTC. A pull request whose reviews cannot be read, an item that cannot be rendered, or a wiki page whose name is not a safe file name is listed under `unreadable` as `issues/<n>`, `pulls/<n>` or `wiki/<page>`; the first two keep their earlier file. A run that keeps no item adds `"removal_skipped": true` to the report and `; removal skipped: no items kept` to the line. If the repository, its issues, pull requests or comments, or its wiki cannot be read, the command exits 1 with `GitHub listing failed: <code>`, writes no report and changes nothing.

### `mdexport export google-docs` — One-off Google Docs export

```bash
GOOGLE_APPLICATION_CREDENTIALS=./key.json mdexport export google-docs --output ./gdocs --report ./gdocs-report.json

# Two folders with their subfolders, skipping one doc
GOOGLE_APPLICATION_CREDENTIALS=./key.json mdexport export google-docs -o ./gdocs --report ./gdocs-report.json \
  --folder 1AbC... --folder 0XyZ... --exclude 1DoC...
```

Options: `--folder` Drive folder id, exported with its subfolders (repeatable; none means every doc the account can see, shared drives included), `--exclude` doc id (repeatable), `--report` file for the JSON report (required).

Authenticates as the service account whose key file path is in `GOOGLE_APPLICATION_CREDENTIALS`, with read-only Drive and Docs scopes. A doc is visible once it, its folder or its shared drive is shared with the service account's email. Nothing is read from or written to `~/.mdexport/registry.json`.

Writes `OUTPUT/<doc id>.md`: a header with the doc's title, URL and last change, then one `##` passage for the text before the first heading and one per top-level heading section, each followed by the doc's URL. Every run exports every doc and rewrites only the files whose text changed; no state is kept. **The output directory is dedicated to this export:** a doc no longer listed or excluded has its file deleted. Only names shaped like a doc id are deleted, and nothing is deleted in a run that keeps no doc.

```json
{"docs": 120, "sections": 900, "written": 4, "removed": ["1AbC..."], "unreadable": {},
 "removal_skipped": false, "finished_at": "2026-11-03T03:30:12Z"}
```

```
Exported 120 docs (4 written), 900 sections, 0 unreadable
```

`docs` counts what is kept, `sections` the `##` passages in kept files, `written` the files written this run, and `removed` the ids of the deleted files. A doc that cannot be exported or rendered is listed under `unreadable` and keeps its earlier file. A run that keeps no doc adds `"removal_skipped": true` and `; removal skipped: no docs kept`. If Drive cannot be listed, the command exits 1 with `Google Drive listing failed: <code>`, writes no report and changes nothing.

### `mdexport export granola` - One-off Granola export

```bash
GRANOLA_TOKEN=grn_... mdexport export granola --output ./granola --report ./granola-report.json

# Skip one folder and one meeting
GRANOLA_TOKEN=grn_... mdexport export granola -o ./granola --report ./granola-report.json --exclude fol_... --exclude not_...
```

Options: `--exclude` note id (`not_...`) or folder id (`fol_...`), repeatable, `--report` file for the JSON report (required).

The key is read only from `GRANOLA_TOKEN`. A workspace key, made by a Granola admin, reads the workspace's public notes and the spaces whose "Allow access with a workspace API key" is on; a personal key reads its user's notes. Nothing is read from or written to `~/.mdexport/registry.json`.

Writes `OUTPUT/<note id>.md`: a header with the meeting's title, date, Granola link and updated time, a `##` passage with when it was, its owner, people and folders and the start of its summary, one per summary section, then the transcript in passages of about 4,000 characters headed by the time they start, all in UTC. Each passage is followed by the Granola link. The owner's private notes are never written. A meeting is read again only when Granola's updated time differs from the one in its file; no other state is kept. **The output directory is dedicated to this export:** a meeting no longer listed, deleted or excluded has its file deleted. Only names shaped like a note id are deleted, and nothing is deleted in a run that keeps no meeting.

```json
{"meetings": 6, "read": 1, "written": 1, "removed": [], "unreadable": {},
 "removal_skipped": false, "excluded": [], "finished_at": "2026-11-09T18:32:56Z"}
```

```
Exported 6 meetings (1 read, 1 written), 0 unreadable
```

`meetings` counts what is kept, `read` the meetings read from Granola this run, `written` the files written, and `removed` the ids of the deleted files. A meeting that cannot be read or rendered is listed under `unreadable` and keeps its earlier file. A run that keeps no meeting adds `"removal_skipped": true` and `; removal skipped: no meetings kept`. If Granola cannot be listed, the command exits 1 with `Granola listing failed: <code>`, writes no report and changes nothing.

## Authentication

Tokens are loaded from environment variables or a `.env` file. See `.env.example` for all options.

| Source | Env var | Where to get it |
|---|---|---|
| GitHub | `GITHUB_TOKEN` | [Settings → Tokens](https://github.com/settings/tokens) (classic, `repo` scope) |
| Slack | `SLACK_TOKEN` | [Create app](https://api.slack.com/apps) → OAuth & Permissions → User Token (`xoxp-...`) |
| Linear | `LINEAR_TOKEN` | [Settings → API](https://linear.app/settings/api) → Personal API keys |
| Telegram | `TELEGRAM_DC`, `TELEGRAM_AUTH_KEY` | Notula Collect's Telegram sign-in window |
| Gmail | `GOOGLE_TOKEN` | A Google OAuth token with the `gmail.readonly` scope |
| Google Docs | `GOOGLE_TOKEN` | Service account, gcloud CLI, or [OAuth playground](https://developers.google.com/oauthplayground) |
| Notion | `NOTION_TOKEN` | [My integrations](https://www.notion.so/my-integrations) → Create integration (`ntn_...`) |
| Granola | `GRANOLA_TOKEN` | Granola app → Settings → API → Create key (`grn_...`) |

You can also pass tokens directly via `--token` when adding a source.

### Slack token setup

1. Create an app at https://api.slack.com/apps
2. Go to **OAuth & Permissions**
3. Under **User Token Scopes** (not Bot), add: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`
4. For DMs, also add: `im:history`, `im:read`, `mpim:history`, `mpim:read`
5. Install to workspace and copy the **User OAuth Token** (`xoxp-...`)

### Notion token setup

1. Go to https://www.notion.so/my-integrations
2. Create a new internal integration with the **Read content** and **Read user information without email addresses** capabilities (user information resolves people properties to names)
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
