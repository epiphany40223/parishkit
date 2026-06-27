# ParishKit

![ParishKit logo](parishkit-logo.png)

ParishKit is an installable Python package and collection of command-line
tools for Catholic parish operations automation.

The project name is ParishKit. The repository and Python distribution name are
`parishkit`.

This repository is intentionally parish-neutral. Parish names, domains,
ministry mappings, external object IDs, credentials, and deployment paths belong
in runtime configuration, not in reusable package code.

## Requirements

- Python 3.12 or newer.
- Runtime credentials supplied outside git when external services are used.

## Local Development

Create a virtual environment, then install the package and development tools:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the same validation commands represented in CI:

```sh
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

## Console Tools

The package exposes these planned commands:

- `parishkit-run`
- `parishkit-print-member`
- `parishkit-print-ministries`
- `parishkit-calendar-reservations`
- `parishkit-create-ministry-rosters`
- `parishkit-sync-google-group`
- `parishkit-sync-ps-to-cc`

The commands are installed through `pyproject.toml` entry points. During early
migration, some commands may exist as placeholders until their script phase is
implemented.

## CI and Credentials

Normal CI runs Ruff linting, Ruff format checking, Pytest, and DCO validation.
CI must not require real ParishSoft, Google, Constant Contact, Slack, or email
provider credentials.

External-service behavior should be covered by mocked tests in CI. When real
credentials are needed, add a documented human-run smoke-test tool that:

- Reads credentials from explicit runtime paths or environment references.
- Prefers read-only checks.
- Requires dry-run mode or explicit confirmation before writes.
- Redacts sensitive values from output.
- Is excluded from normal CI.
