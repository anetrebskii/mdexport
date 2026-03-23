# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

mdexporter is a Python CLI tool that exports data from GitHub, Slack, Linear, Google Docs, and Notion into local Markdown files. It uses Click for the CLI, and stores export configurations in `~/.mdexport/registry.json`.

## Setup & Commands

```bash
pip install -e .          # Install in development mode
mdexport --help           # CLI entry point (mdexport.cli:cli)
```

There are no tests, linter, or CI configured.

## Architecture

All source code lives in `src/mdexport/`. The codebase is small (~8 files):

- **cli.py** — Click CLI commands: `add` (github/slack/linear/google-docs/notion), `list`, `remove`, `sync`
- **registry.py** — JSON config persistence at `~/.mdexport/registry.json` (register/unregister/get/update_synced)
- **github.py** — Exports issues, PRs, and wiki using PyGithub. Wiki is cloned via git subprocess.
- **slack.py** — Exports channel messages using slack-sdk. Resolves user IDs to names, supports date-range filtering.
- **linear.py** — Exports team issues with comments and history via GraphQL (httpx).
- **googledocs.py** — Exports Google Docs as Markdown via Drive API. Supports folder filtering and incremental sync by modifiedTime.
- **notion.py** — Exports Notion pages as Markdown via REST API (httpx). Converts blocks to MD, supports page/database filtering and incremental sync by last_edited_time.

Each exporter follows the same pattern: authenticate via env var token → paginate through API → format as markdown → write files. GitHub, Linear, Google Docs, and Notion support incremental sync via `synced_at`.

## Environment Variables

Tokens are loaded from `.env` (see `.env.example`): `GITHUB_TOKEN`, `SLACK_TOKEN`, `LINEAR_TOKEN`, `GOOGLE_TOKEN`, `NOTION_TOKEN`.
