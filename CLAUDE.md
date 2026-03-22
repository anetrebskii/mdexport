# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

mdexporter is a Python CLI tool that exports data from GitHub, Slack, and Linear into local Markdown files. It uses Click for the CLI, and stores export configurations in `~/.mdexport/registry.json`.

## Setup & Commands

```bash
pip install -e .          # Install in development mode
mdexport --help           # CLI entry point (mdexport.cli:cli)
```

There are no tests, linter, or CI configured.

## Architecture

All source code lives in `src/mdexport/`. The codebase is small (~6 files):

- **cli.py** — Click CLI commands: `add` (github/slack/linear), `list`, `remove`, `sync`
- **registry.py** — JSON config persistence at `~/.mdexport/registry.json` (register/unregister/get/update_synced)
- **github.py** — Exports issues, PRs, and wiki using PyGithub. Wiki is cloned via git subprocess.
- **slack.py** — Exports channel messages using slack-sdk. Resolves user IDs to names, supports date-range filtering.
- **linear.py** — Exports team issues with comments and history via GraphQL (httpx).

Each exporter follows the same pattern: authenticate via env var token → paginate through API → format as markdown → write files. Syncs are full exports (no incremental/delta).

## Environment Variables

Tokens are loaded from `.env` (see `.env.example`): `GITHUB_TOKEN`, `SLACK_TOKEN`, `LINEAR_TOKEN`.
