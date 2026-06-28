# Agent and Developer Instructions

ParishKit contains reusable Python automation for Catholic parishes.

- Keep package code parish-neutral.
- Target Python 3.12 or newer.
- Store shared code under `src/parishkit`.
- Store executable wrappers under `scripts/<tool-name>/`.
- Keep command behavior in `src/parishkit` modules exposed through console
  entry points; wrapper scripts should only delegate to package code.
- Do not commit credentials, secrets, local logs, caches, generated reports, or
  local runtime configuration.
- Put parish-specific mappings and operational settings in YAML configuration.
- Use `/opt/parishkit/{config,credentials,cache,logs,reports,run}` as the
  fallback deployment root. If `PARISHKIT_ROOT` is set, all ParishKit default
  paths must be rooted under that directory instead. Every runtime path must
  still be overridable by CLI option or YAML config.
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
- `main` is production. This repository does not use release branches.
- Do work on a topic branch named `pr/<short-topic>` and land it through a
  GitHub pull request. Do not commit directly to `main`.
- Working in git worktrees is fine, especially when coordinating with multiple
  agents or teams. When working in a worktree, avoid repo-global commands such
  as `git stash` or `git worktree prune`.
- This repository uses semantic versioning. Releases are annotated git tags in
  the form `vVERSION`, such as `v1.2.3`. Do not push a release tag to GitHub
  unless a human explicitly authorizes that push.
- Sign off every commit. Each commit needs a `Signed-off-by:` line per the
  Contributor's Declaration; use `git commit -s`. Commits without it are not
  accepted. This applies to AI-assisted work too: the human submitter certifies
  the contribution. Use your real name and email.
- Commit messages should have a short first line saying what changed, then a
  blank line, then a body explaining why. An optional area prefix is fine, such
  as `docs: document Google Workspace setup`.
- Do not add AI tooling attribution to commits. Do not include `Co-Authored-By:`
  trailers for AI tools or "Generated with" trailers.
- Wrap commit message body lines at approximately 75 characters.
- Use an editor or message file for multi-line commit messages; avoid relying
  on inline `\n` sequences in command-line arguments.
- One logical change per commit. Keep incidental drive-by fixes as standalone
  commits, separate from the main change, so each can be reviewed and bisected
  on its own.
- Squash fixup commits appropriately before pull requests are merged into their
  target branches.
- GitHub issue and pull request descriptions should use one line per paragraph
  and let GitHub render wrapping.
