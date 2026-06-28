# pk-sync-ps-to-ggroup

Synchronize Google Group membership from configured ParishSoft sources.

This wrapper delegates to the installed `pk-sync-ps-to-ggroup` command.
The command computes Google Group add, remove, and role-change actions from
configured ParishSoft sources.

## Usage

```sh
pk-sync-ps-to-ggroup --config example-config.yaml --dry-run
```

Mappings live in YAML under `sync.groups`. A group can combine
ParishSoft `ministries`, member `workgroups`, `static_members`, and supported
selectors. Leaders are written as Google Group owners; other desired members are
written as members. Dry-run still reads ParishSoft and Google Group membership,
but it does not write Google changes or send notifications.

Keep ParishSoft, Google, and email credentials outside git.

## Credential Smoke Tests

Automated tests mock Google Groups and email writes. Verify real credentials
manually with read-only smoke tests:

```sh
scripts/smoke-tests/google-api.py \
  --service admin \
  --version directory_v1 \
  --scope https://www.googleapis.com/auth/admin.directory.group.member.readonly \
  --service-account-file /opt/parishkit/credentials/google-service-account.json \
  --delegated-subject admin@example.org \
  --group-key group@example.org \
  --send
```
