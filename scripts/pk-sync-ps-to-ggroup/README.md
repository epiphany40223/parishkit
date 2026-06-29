# pk-sync-ps-to-ggroup

Keep Google Group membership in step with ParishSoft. Add or remove someone from
a ministry or workgroup in ParishSoft, and this tool adds or removes them from
the matching Google Group on its next run.

## What you need first

- A working ParishSoft API key.
- Google Cloud and Google Workspace set up for ParishKit, including a service
  account authorized for the Admin SDK scopes. See **Google Cloud and Google
  Workspace** in the [top-level README](../../README.md) for the one-time setup.
- A delegated Workspace user that is allowed to manage the target groups'
  membership.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-sync-ps-to-ggroup.yaml
```

The group mappings live under `sync.groups`. Each entry names one Google Group
and the ParishSoft sources that should populate it. A group can combine exact
`ministries`, member `workgroups`, `static_members`, and rule-based `selectors`.
ParishSoft ministry leaders (per `leader_roles`) are written as Google Group
**owners**; everyone else is written as a **member**. The comments in
`example-config.yaml` explain every field, including the selector types and the
`allow_empty` / `max_removals` / `max_removal_fraction` guardrails that stop a
run from making surprisingly large deletions.

## Run it

Always start with a dry run, which reads ParishSoft and the current Google Group
membership and reports the adds, removes, and role changes it *would* make
without writing anything or sending email:

```sh
pk-sync-ps-to-ggroup --config /opt/parishkit/config/pk-sync-ps-to-ggroup.yaml --dry-run
```

When the dry run looks right, set `common.dry_run: false` (or pass
`--no-dry-run`) to apply changes for real.

Keep ParishSoft, Google, and email credentials outside git.

## Verify your Google credentials

Automated tests mock Google Groups and email writes, so they do not prove your
real credentials work. Confirm them once with a read-only smoke test:

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
