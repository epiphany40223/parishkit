# Agent and Developer Instructions

`parishkit` contains reusable Python automation for Catholic parishes.

- Keep package code parish-neutral.
- Target Python 3.12 or newer.
- Store shared code under `src/parishkit`.
- Store executable wrappers under `scripts/<tool-name>/`.
- Keep command behavior in `src/parishkit` modules exposed through console
  entry points; wrapper scripts should only delegate to package code.
- Do not commit credentials, secrets, local logs, caches, generated reports, or
  local runtime configuration.
- Put parish-specific mappings and operational settings in YAML configuration.
- Use `/opt/parishkit/{config,credentials,cache,logs,reports,run}` as deployment
  defaults only; every runtime path must be overridable by CLI option or YAML
  config.
- Use shared CLI, logging, configuration, retry, and authentication helpers.
- Shared `parishkit.cli`, `parishkit.config`, `parishkit.logging`, and
  `parishkit.retry` helpers are the default place for common option parsing,
  YAML loading, startup validation, logging, Slack notification, and retry
  behavior.
- New wrapper scripts must start with `#!/usr/bin/env python3` and have their
  executable bit set.
- Do not add ad hoc `sys.path` changes to import package code.
- Use `ruff` and `pytest` for local validation.
- Match CI locally with:
  - `python -m ruff check .`
  - `python -m ruff format --check .`
  - `python -m pytest`
- Normal CI must not require real ParishSoft, Google, Constant Contact, Slack,
  or email-provider credentials.
- Credential-dependent validation belongs in documented, human-run smoke-test
  tools that read credentials at runtime, redact sensitive output, and stay out
  of normal CI.
- REST API usage may be modernized during migration when current supported APIs
  or client-library patterns are better than old script patterns; preserve
  behavior unless the intentional change is documented.
- Preserve existing tool behavior unless an intentional behavior change is
  requested or documented.
- Commits require a Developer Certificate of Origin `Signed-off-by:` trailer.
- There is no requirement for LLM attribution in commits.
- Use Common Convention-style commit subjects such as
  `docs: document Google Workspace setup`.
- Prefer self-contained commits that keep the repository bisectable.
- Put drive-by fixes in their own commits.
- Squash fixup commits before pull requests are merged into target branches.
- Write commit messages that explain why the change exists, not only what
  changed.
- Wrap commit message body lines at approximately 75 characters.
- GitHub issue and pull request descriptions should use one line per paragraph
  and let GitHub render wrapping.
