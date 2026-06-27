# Manual Smoke Tests

Smoke tests in this directory are for human operators with real credentials.
They are not part of normal CI.

Run them only after reviewing what they will send or read. Prefer read-only
checks, use dry-run options where available, and keep token files outside git.

Slack smoke tests require the Slack optional dependency group:

```sh
python -m pip install '.[slack]'
```

The Slack notification smoke test previews the target and message by default.
Add `--send` only after confirming the preview is safe to post.
