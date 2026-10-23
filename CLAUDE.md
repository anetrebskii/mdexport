# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

mdexporter is a Python CLI tool that exports data from GitHub, Slack, Telegram, Gmail, Linear, Google Docs, and Notion into local Markdown files. It uses Click for the CLI, and stores export configurations in `~/.mdexport/registry.json`.

## Setup & Commands

```bash
pip install -e .          # Install in development mode
mdexport --help           # CLI entry point (mdexport.cli:cli)
```

Tests: `pip install -e '.[dev]'` then `pytest` (`tests/` covers `slack_export.py`, `notion_client.py`, `notion_render.py`, `notion_export.py`, `linear_client.py`, `linear_render.py`, `linear_export.py`, `mdexport export slack`, `mdexport export notion` and `mdexport export linear`; `tests/fake_slack.py`, `tests/fake_notion.py` and `tests/fake_linear.py` stand in for the APIs). No linter or CI.

## Architecture

All source code lives in `src/mdexport/`. The codebase is small (~15 files):

- **cli.py** — Click CLI commands: `add` (github/slack/telegram/gmail/linear/google-docs/notion), `list`, `info`, `remove`, `sync`, `auth`, `chats`, `threads`, and `export` (slack/notion/linear, no registry)
- **registry.py** — JSON config persistence at `~/.mdexport/registry.json` (register/unregister/get/update_synced)
- **github.py** — Exports issues, PRs, and wiki using PyGithub. Wiki is cloned via git subprocess.
- **slack.py** — Exports channel messages using slack-sdk. Resolves user IDs to names, supports date-range filtering.
- **slack_export.py** — Stateless `mdexport export slack`: one Markdown file per channel per UTC day with permalinks, a cursor per channel in `.state/`, a JSON report; token only from `SLACK_TOKEN`, no registry.
- **telegram.py** — Exports chats, groups and channels with Telethon, using an auth key and DC number from the environment (`TELEGRAM_DC`, `TELEGRAM_AUTH_KEY`). One file per chat, incremental by the `<!-- id:N -->` markers it writes.
- **gmail.py** — Exports mail threads a Gmail search finds, via the Gmail API. One file per thread, incremental by `after:` on the last sync.
- **linear.py** — Exports team issues with comments and history via GraphQL (httpx).
- **googledocs.py** — Exports Google Docs as Markdown via Drive API. Supports folder filtering and incremental sync by modifiedTime.
- **notion.py** — Exports Notion pages as Markdown via REST API (httpx). Converts blocks to MD, supports page/database filtering and incremental sync by last_edited_time.
- **notion_client.py** — Small Notion REST client over httpx (`Notion-Version: 2022-06-28`), at most 3 calls per second, retrying 429, 5xx and network errors; failures raise `NotionError` with Notion's code, never the token.
- **notion_render.py** — Renders a Notion page and its blocks as Markdown: a `##` item per heading with its link on the next line, database-row properties, the Slack line escape, no code fences.
- **notion_export.py** — Stateless `mdexport export notion`: every page shared with the integration as `<root id>/<page id>.md`, re-reading only changed pages, a state file per page in `.state/`, deletion of pages no longer shared or excluded and of root folders left empty, a JSON report; token only from `NOTION_TOKEN`, no registry.
- **linear_client.py** — Small Linear GraphQL client over httpx, retrying 429, `RATELIMITED`, 5xx and network errors with backoff; it pages every team and every task of the public teams with all their comments, and failures raise `LinearError` with Linear's code, never the token.
- **linear_render.py** — Renders a Linear task as Markdown: a `##` passage for its metadata and lead text, then one per top-level description heading and one per comment, each with its link on the next line, the Slack line escape, no code fences.
- **linear_export.py** — Stateless `mdexport export linear`: every task of every public team as `<team key>/<task id>.md`, archived kept and trashed left out, rewriting only files whose text changed, deletion of tasks no longer listed or excluded and of team folders left empty, a JSON report; token only from `LINEAR_TOKEN`, no registry.
- **granola.py** - Exports Granola meetings (summary, own notes, transcript) via the public API with a `grn_` key. A meeting goes to one of several named places: one chosen for it, else its calendar series' place, else a linked Granola folder's place; otherwise it waits in `pending` until `mdexport granola file` gives it one. `files`, `series`, `notes` and `pending` live in the registry entry, and `output` is the first place.

Each exporter follows the same pattern: authenticate via env var token → paginate through API → format as markdown → write files. GitHub, Linear, Google Docs, and Notion support incremental sync via `synced_at`.

## Environment Variables

Tokens are loaded from `.env` (see `.env.example`): `GITHUB_TOKEN`, `SLACK_TOKEN`, `LINEAR_TOKEN`, `GOOGLE_TOKEN` (Google Docs and Gmail), `NOTION_TOKEN`, `GRANOLA_TOKEN`. Telegram takes `TELEGRAM_DC` and `TELEGRAM_AUTH_KEY` instead, set by Notula Collect for the length of one run.
