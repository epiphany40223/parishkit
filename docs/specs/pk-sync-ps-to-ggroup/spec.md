# pk-sync-ps-to-ggroup — Google Group membership sync from ParishSoft

## 1. Purpose and role

**Category: Sync.** `pk-sync-ps-to-ggroup` keeps Google Group membership in step
with ParishSoft. For each configured Google Group it builds a *desired* member
set from one or more ParishSoft sources (exact ministries, exact member
workgroups, rule-based selectors) plus optional hard-coded static members, reads
the group's *current* membership through the Admin SDK Directory API, and
reconciles the two: it adds missing members, removes members no source still
justifies, and changes roles between Google `MEMBER` and `OWNER`. ParishSoft is
the system of record; data flows outward only. The tool follows the shared
reconciliation skeleton (read source → read target → diff → apply or report)
described in [`../intro/spec.md#architecture-and-data-flow`](../intro/spec.md#architecture-and-data-flow),
and is one of the two pure sync tools alongside
[`../pk-sync-ps-to-cc/spec.md`](../pk-sync-ps-to-cc/spec.md), which has the
same reconciliation shape against Constant Contact lists.

Implementation: `src/parishkit/pk_sync_ps_to_ggroup.py`. Operator-facing docs:
`scripts/pk-sync-ps-to-ggroup/README.md`. Example config:
`scripts/pk-sync-ps-to-ggroup/example-config.yaml`.

## 2. Invocation

- **Console command**: `pk-sync-ps-to-ggroup` (declared in `pyproject.toml`
  `[project.scripts]` as `pk-sync-ps-to-ggroup = "parishkit.cli:sync_google_group_main"`).
- **cli.py entry point**: `sync_google_group_main(argv=None)` lazily imports
  `parishkit.pk_sync_ps_to_ggroup.main` and delegates (lazy import keeps the
  optional Google/email deps off the shared CLI import path until the command
  actually runs).
- **Wrapper**: `scripts/pk-sync-ps-to-ggroup/pk-sync-ps-to-ggroup.py` is a
  `#!/usr/bin/env python3` shim that calls `sync_google_group_main()` and exits
  with its return code. It performs no logic of its own.
- **`main(argv, *, loader, service_factory, email_provider)`** parses argv,
  handles `--version` early (prints `pk-sync-ps-to-ggroup <package version>` via
  `importlib.metadata.version("parishkit")` and returns `0`), then runs `_run`
  inside `run_user_facing` (the shared error funnel; see
  [`../intro/spec.md#shared-cli-layer`](../intro/spec.md#shared-cli-layer)).
  The three keyword-only parameters are injectable test seams (Section 11).

## 3. Command-line options

The tool adds exactly **one** tool-specific flag; everything else is the shared
common surface. See
[`../intro/spec.md#shared-cli-layer`](../intro/spec.md#shared-cli-layer) for
the full common flag set and CLI > config > default precedence.

- `--version` — print the installed entry-point version and exit `0`. (Note:
  this is a plain `store_true` flag, not argparse's `action="version"`; it is
  handled before any config is loaded.)

Shared flags accepted (resolved by `resolve_common_options`): `--config`;
tri-state `--dry-run/--no-dry-run`, `--verbose/--no-verbose`,
`--debug/--no-debug`; `--log-file`, `--log-dir`; `--slack-token-file`,
`--slack-channel`, `--slack-log-level`; `--ps-api-key-file`, `--ps-cache-dir`,
`--ps-cache-limit`. There are no other tool-specific flags — all group/source
configuration lives in YAML.

Verbose behavior nuance: `_run` calls `setup_logging` with
`verbose=common.verbose or common.dry_run`, so a dry run always logs at `INFO`
even without `--verbose`, making the planned diff visible by default.

## 4. Configuration schema

The tool loads a single YAML file (`--config`). Shared sections (`common`,
`logging`, `slack`, `parishsoft`) are validated centrally by
`resolve_common_options`; see
[`../intro/spec.md#configuration-system`](../intro/spec.md#configuration-system).
This tool owns and validates three sections: `google`, `email`, and `sync`.
Every owned mapping uses `reject_unknown_keys` (typo-proofing). All paths are
resolved with config-relative semantics via `resolve_path` against the config
file's directory.

### 4.1 `google` section (validated by `load_google_credentials`)

Allowed keys (exactly): `service_account_file`, `user_token_file`,
`delegated_subject`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_account_file` | string path | one of the two file keys | Service-account JSON key; loaded with `load_service_account_credentials(path, scopes, subject=delegated_subject)`. Enables domain-wide delegation. |
| `user_token_file` | string path | one of the two file keys | Installed-app OAuth user token; loaded with `load_user_credentials(path, scopes)`. Used mainly by tests. |
| `delegated_subject` | string | optional | The real Workspace user the service account impersonates (DWD). Must be a string if present. Operationally this should be a real delegated Workspace user, not the service-account email, but the loader does not inspect the service-account JSON to enforce that distinction. Only meaningful with `service_account_file`. |

Validation rules:

- Setting **both** `service_account_file` and `user_token_file` →
  `ConfigError`.
- `delegated_subject` not a string (when present) → `ConfigError`.
- Neither usable file key present → `ConfigError` ("…is required"). The loader
  only treats string values as usable credential paths; a non-string file value
  is not type-reported directly and instead falls through to this required-key
  error unless both file keys are truthy, which hits the both-set guard first.

Scopes requested are computed by the loader, not configured:

- `ADMIN_SCOPE = https://www.googleapis.com/auth/admin.directory.group.member`
  is always requested (membership reads/writes).
- `GROUP_SETTINGS_SCOPE = https://www.googleapis.com/auth/apps.groups.settings`
  is appended only when `include_settings_scope=True`.

`_run` loads two credential sets at different times: the Admin Directory service
is built eagerly with `include_settings_scope=False` (member scope only); the
Groups Settings service is built lazily with `include_settings_scope=True`
(both scopes) only when a notification needs the group's posting permission.
This is why a membership-only sync never needs the Groups Settings scope. See
[`../intro/spec.md#google-integration-layer`](../intro/spec.md#google-integration-layer)
for the auth/DWD mechanics.

### 4.2 `email` section (shared email provider layer)

Read lazily via `provider_from_config(config["email"], base_dir=…)` only when a
notification will actually be built (Section 6/8). Keys are owned and validated
by the email layer, not this tool. `provider` selects the backend:
`google-workspace` (or `google_workspace`) is implemented; `ms365` is a
placeholder that raises outside dry-run. See
[`../intro/spec.md#email-provider-layer`](../intro/spec.md#email-provider-layer).
The section is unnecessary when no group has notify recipients.

### 4.3 `sync` section (validated by `sync_config_from_yaml`)

Allowed top-level keys (exactly): `groups`, `notifications`,
`google_mail_domains`, `leader_roles`.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `groups` | list of group entries | — (required, non-empty) | Empty list → `ConfigError("sync.groups must not be empty")`. Targets must be unique (Section 4.4). |
| `notifications` | mapping | `{}` | Allowed key: `sender` only. |
| `notifications.sender` | string | none | From-address for summary emails. Required when **any** group has a `notify` recipient, else `ConfigError`. |
| `google_mail_domains` | list of strings | `["gmail.com"]` | Case-folded into a `frozenset`; used by email-equality normalization (Section 6.4). |
| `leader_roles` | list of strings | `["Chairperson", "Staff"]` (sorted `LEADER_ROLES`) | Case-sensitive ParishSoft ministry role labels that promote a matched member to Google `OWNER` for exact ministry sources and chair-style selectors. Stored as a `frozenset`. |

Parsed into `SyncConfig(groups, sender, google_mail_domains, leader_roles)`.

### 4.4 Group entry schema (`_group_sync`, one per `sync.groups[]`)

Allowed keys (exactly): `group`, `ggroup`, `notify`, `ministries`,
`workgroups`, `static_members`, `selectors`, `allow_empty`, `max_removals`,
`max_removal_fraction`.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `group` | string | — (required) | The Google Group email/key. `ggroup` is accepted as a **legacy alias** (`group` preferred). |
| `notify` | list of strings | `()` | Recipients of the per-group change-summary email. |
| `ministries` | list of strings | `()` | Exact ParishSoft ministry names to include. |
| `workgroups` | list of strings | `()` | Exact ParishSoft member workgroup names to include. |
| `static_members` | list | `()` | See Section 4.5. |
| `selectors` | list | `()` | See Section 4.6. |
| `allow_empty` | bool | `false` | Guardrail; see Section 7. |
| `max_removals` | positive int | `25` | Guardrail; must be `> 0` and not a bool. |
| `max_removal_fraction` | float in `[0.0, 1.0]` | `0.5` | Guardrail; rejects non-numbers, bools, and out-of-range. |

A group must declare at least one source among
`ministries`/`workgroups`/`static_members`/`selectors`, else
`ConfigError("… must configure a source")`.

**Uniqueness** (`_validate_unique_groups`): group targets are compared
case-folded; any duplicate raises `ConfigError("sync.groups[].group values must
be unique; …")`. This runs before reconciliation so two entries cannot fight
over one group.

### 4.5 Static member schema (`_static_member`)

Allowed keys (exactly): `email`, `leader`, `owner`.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `email` | string | — (required) | Lower-cased on load. |
| `leader` | bool | `false` | `true` → desired as Google `OWNER`. `owner` is accepted as an alias for `leader`. |

Static members are always desired even when absent from ParishSoft data; they
are merged last (Section 6.1).

### 4.6 Selector schema (`_selector`)

Allowed keys (exactly): `type`, `ministry_prefix`, `ministry_pattern`,
`member_roles`, `leader_roles`, `staff_owner_domains`, `purpose`.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `type` | string | — (required) | One of `all_ministry_chairs`, `ministry_chair`, `ministry_role` (any other value → `ConfigError("unknown sync selector type: …")`). |
| `ministry_prefix` | string | none | Ministry name must `startswith` this. |
| `ministry_pattern` | string (regex) | none | Compiled at parse time; invalid regex → `ConfigError`. Applied with `re.search`. Both prefix and pattern must match when both are set. |
| `member_roles` | list of strings | `()` | `ministry_role`: these roles → plain `MEMBER`. |
| `leader_roles` | list of strings | `()` | `ministry_role`: these roles → `OWNER`. (Distinct from the top-level `sync.leader_roles`.) |
| `staff_owner_domains` | list of strings | `()` | Lower-cased. `all_ministry_chairs`: matched chairs whose primary email domain is in this set become `OWNER`; others stay `MEMBER`. |
| `purpose` | string | none | Descriptive metadata only; surfaces in the email subject/rationale, never affects matching. |

See `scripts/pk-sync-ps-to-ggroup/example-config.yaml` for fully commented
examples of each selector type.

## 5. Source data

`_run` builds a `ParishSoftClient` via
`parishsoft_client_from_config(common, config)` and loads data with
`loader(client, active_only=True, parishioners_only=False)` (default loader
`load_families_and_members`). Loading is **active-only** and **not**
parishioner-restricted. See
[`../intro/spec.md#parishsoft-data-layer`](../intro/spec.md#parishsoft-data-layer)
and [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md). All
ParishSoft access is read-only; ministry/workgroup membership is read-only in
both API generations (the analysis doc confirms there is no write path back to
rosters/workgroups).

Per-member derived fields this tool consumes:

- `member["py ministries"]` — mapping keyed by ministry name; each value has
  `name`, `role` (from `ministryRoleName`), `start date`, `end date`, and the
  raw record. Only *current* memberships are present
  (`ministry_membership_is_current`).
- `member["py workgroups"]` — mapping keyed by workgroup name.
- Email addresses from `member_email_addresses(member)` — prefers the pre-split
  `py emailAddresses` list, otherwise splits the semicolon-delimited
  `emailAddress`; all cleaned, lower-cased, de-duplicated, blanks dropped.
- Display name: `member["py friendly name FL"]` ("First Last"), falling back to
  `member_display_name(member)` ("firstName lastName").

**Email selection precedence**: a qualifying member contributes **only their
first (primary) email** (`emails[0]`). A member with **no** email is skipped
entirely, because Google membership is email-keyed. (This is simpler than the
family-email layering used elsewhere; see `family_workgroup_emails` in the data
layer for the contrast.)

**Source-name validation** (`validate_configured_parishsoft_sources`, run
before any planning): every configured `ministries` name must appear in
`available_ministry_names(data)`; every `workgroups` name must appear in
`available_member_workgroup_source_names(data)`; every selector must match at
least one available ministry name. Any miss raises a `ConfigError` listing the
available names. Notably, `available_member_workgroup_source_names` also adds,
for each workgroup ending in `" Ldr"` or `" Leader"`, the base name without the
suffix — so a leadership sibling workgroup is accepted as a configurable source
name (Section 6.2).

## 6. Reconciliation algorithm

For each group, `plan_group` computes a `GroupSyncPlan`; the diff/plan is built
for every group **before any writes** (Section 9 preflight), then applied.

### 6.1 Build the desired set (`desired_members`)

1. For each loaded member, `member_matches_group` returns `(is_member,
   is_leader)` OR-combined across all of the group's ministries, workgroups, and
   selectors. A member that qualifies as a leader through any source is also
   forced to `is_member = True` (a leader is always a member).
2. Skip non-matching members. For a match, take `member_email_addresses(member)`;
   skip if empty. Add the primary email via `add_desired_member` with the
   member's `py friendly name FL`/display name.
3. After all members, append each `static_member` (name `None`).
4. `add_desired_member` keys `found` by `normalize_email` (Section 6.4) of the
   lower-cased address. On collision it OR-merges `leader` and *appends* the
   name to `DesiredMember.names` (so the email lists every person/source behind
   it); otherwise it creates a new `DesiredMember(email=lowercased, leader,
   names=[name] if name])`. The stored `email` is the lower-cased display form,
   not the normalized key.

#### Source matching detail

- **`member_in_ministries`**: a `py ministries` entry whose `name` is in the
  configured set → member; leader if its `role` is in `sync.leader_roles`
  (`is_ministry_leader`).
- **`member_in_workgroups`**: workgroup name present in `py workgroups` keys →
  member. Leadership is encoded as a *sibling* workgroup named `"<name> Ldr"` or
  `"<name> Leader"`; either spelling marks the member a leader (and a member).
- **Selectors** (`selector_matches_member`):
  - `all_ministry_chairs`: for any ministry where the member is a ministry
    leader (`is_ministry_leader` against `sync.leader_roles`) and the ministry
    name matches the selector's prefix/pattern → member `True`; leader is `True`
    only when the member's **primary** email domain is in `staff_owner_domains`.
  - `ministry_chair`: a ministry leader in a name-matching ministry →
    `(True, True)` (member and owner).
  - `ministry_role`: across name-matching ministries, if the ministry `role` is
    in the selector's `member_roles` or `leader_roles` → member; leader if the
    role is in the selector's `leader_roles` **or** the member is an
    `is_ministry_leader`. Leadership is OR-preserved across multiple ministries
    (a later non-leader role cannot demote an earlier leader match).

### 6.2 Read current membership

`list_group_members_or_config_error(admin_service, group.group)` calls
`list_group_members` (paginated). A `GoogleAPIError` with `status_code == 404`
is converted to a helpful `ConfigError` (group not found / check
`sync.groups[].group`); other Google errors propagate. Results pass through
`normalized_group_members`: each entry becomes `{email: lower-cased, role:
upper-cased (default MEMBER), id: id or email}`.

### 6.3 Empty-desired guard, then diff

- If `desired` is empty **and** `current` is non-empty **and** not
  `group.allow_empty` → `ConfigError` (refuse to empty a group from a
  zero-resolving source; instructs to check sources or set `allow_empty: true`).
- `compute_actions(desired, current, google_mail_domains)` produces the diff:
  - For each desired member, find the first current member whose email compares
    equal under `compare_email` (domain-aware normalization).
    - No match → **`add`** with `role = OWNER if leader else MEMBER`, carrying
      the `DesiredMember` for notification names.
    - Match → record it as matched; if the current upper-cased `role` differs
      from the desired role → **`change_role`** to the desired role, with
      `group_member_id = id or email` of the matched current member.
  - Every current member not matched by any desired member → **`delete`**, with
    `group_member_id = id or email`.

Roles are strictly **`MEMBER`** or **`OWNER`** in this tool; `MANAGER` is never
produced (the Google helper supports it, but the desired-role logic is binary on
`leader`).

### 6.4 Email normalization (`normalize_email` / `compare_email`)

- Always lower-cases. No `@` → return lower-cased input.
- Domain **not** in `google_mail_domains` → `local@domain` (lower-cased only;
  dots and `+tags` preserved — Workspace `first.last@` and `firstlast@` can be
  different mailboxes).
- Domain **in** `google_mail_domains` → strip any `+tag` suffix.
- Domain in `{gmail.com, googlemail.com}` → additionally remove all dots from
  the local part.

This prevents spurious churn when ParishSoft and Google hold equivalent
Gmail-style aliases of the same address.

### 6.5 Plan assembly and notification gating

`plan_group` returns early in **dry-run** with `GroupSyncPlan(group, actions)`
(no provider, no posting permission). In **live** mode it:

1. Lazily builds the email provider (if not injected) only when
   `group_has_notification_content(config, group, actions)` is true
   (i.e. `sender` and `notify` and at least one action).
2. Sets `should_notify = group_notification_will_send(...)` (provider present
   and content present).
3. If notifying and no settings service yet, builds one lazily
   (`settings_service_factory`); absence with notify needed → `ConfigError`.
4. Fetches `posting_permission = get_group_posting_permissions(...)` only when
   `should_notify` (else `None`). This Groups Settings read happens during
   planning — before any membership write.

Note: `sync_group(...)` is a thin compatibility wrapper that calls `plan_group`
and returns `list(plan.actions)`; it performs no writes.

### 6.6 Apply

`apply_group_plan` returns immediately in dry-run. Otherwise `apply_actions`
iterates the plan's actions and calls the Google helpers:

- `add` → `insert_group_member(service, group, email, role or "MEMBER")`.
- `change_role` → `update_group_member_role(service, group,
  group_member_id or email, role or "MEMBER")`.
- `delete` → `delete_group_member(service, group, group_member_id or email)`.
- Any other action → `ConfigError("unknown sync action: …")`.

## 7. Guardrails and write safety

This tool calls `require_explicit_write_mode(common, "pk-sync-ps-to-ggroup")`,
so the operator must set `common.dry_run` explicitly (config or
`--dry-run/--no-dry-run`); there is no implicit live default. See
[`../intro/spec.md#dry-run-and-write-safety`](../intro/spec.md#dry-run-and-write-safety).

Two destructive-change guardrails, both per-group:

1. **Empty-desired abort** (`allow_empty`, default `false`): if sources resolve
   to zero desired members while the group currently has members, the run
   aborts rather than deleting everyone (Section 6.3). Set `allow_empty: true`
   to intentionally empty a group.
2. **Large-removal guard** (`validate_large_removal_guard` /
   `removal_guard_tripped`): counts `delete` actions vs. current member count.
   The guard trips — raising `ConfigError` — only when **both** conditions hold:
   `removal_count > max_removals` **and**
   `removal_count / current_count > max_removal_fraction`
   (with `current_count > 0`). Defaults `max_removals=25`,
   `max_removal_fraction=0.5`. Raise either knob for an intentional large prune.

Write retry policy: Google membership writes (`insert`/`update`/`delete`) use a
**one-shot** policy (`RetryPolicy(attempts=1)` in `google/groups.py`) so a retry
cannot duplicate a non-idempotent change; reads (`list_group_members`,
`get_group_posting_permissions`) use the default multi-attempt policy. See
[`../intro/spec.md#retry-policy`](../intro/spec.md#retry-policy).

The tenant guard (`expected_organization`) is enforced by the shared ParishSoft
client before any data is used.

## 8. Outputs, reporting, and notifications

- **Console/log** (text console + JSONL file via `setup_logging`): counts of
  configured groups; loaded member/family/ministry/workgroup totals; per group,
  the desired-member count, current-member count, and the computed action list
  (`_actions_summary`). `INFO` shows summaries; `DEBUG` adds full desired/current
  member dumps with structured `log_extra` payloads. Dry-run logs
  "dry-run: would apply N action(s) for <group>". On success, logs "Google Group
  sync operation completed successfully for N group(s)". The JSONL file is the
  structured output; there is no separate report file.
- **Email summary** (per group, live mode only). Built by
  `build_notification_email` and sent by `send_notification` /
  `send_group_plan_notification` with `dry_run=False`, only after all groups are
  applied (Section 9). Sent only when `sender`, the group's `notify`, and at
  least one action are all present.
  - Subject: `Update to Google Group <group> for <sources>`, where `<sources>`
    is the ministries + workgroups + each selector's `purpose or type`
    (`_group_subject_sources`), or the group name if none.
  - Body: a `group_type` label — **Discussion** when `whoCanPostMessage` is one
    of `ANYONE_CAN_POST`, `ALL_IN_DOMAIN_CAN_POST`, `ALL_MEMBERS_CAN_POST`;
    otherwise **Broadcast** (`_group_type_label`). HTML is a styled striped
    table (columns: index, Name, Email address, Action) plus a "These email
    addresses were obtained from PS" ordered rationale list
    (`_source_rationale_text`: per-ministry, per-workgroup, per-selector, and
    "Hard-coded members" if any static members). A plain-text alternative
    mirrors the same content.
  - Per-row Name (`_action_display_names`): the joined ParishSoft display names;
    `[Static member]` when the desired member had no names; `[No ParishSoft
    member]` when there is no desired member (a delete).
  - Per-row Action text (`_group_action_message`): Broadcast groups state
    whether the member can/can-not post (owners can; members "can not");
    Discussion groups omit the posting note. Adds/role-changes/deletes get
    distinct wording.
- **Slack**: failures at/above the configured Slack threshold are posted by the
  shared logging Slack handler; healthy runs stay quiet. See
  [`../intro/spec.md#logging-and-notifications`](../intro/spec.md#logging-and-notifications).

## 9. Failure modes, exit codes, and ordering

- **Exit `0`**: `--version`, or a successful run (dry-run or live).
- **Exit `2`**: any expected operational error funneled by `run_user_facing` —
  `ConfigError`, `OSError`, `ParishSoftAPIError`, `GoogleAPIError`, `CCAPIError`,
  `RetryError` — printed as a single `ERROR: …` line on stderr. Examples:
  invalid/duplicate config, unknown selector type, missing ParishSoft source
  name, missing Google Group (404→ConfigError), Groups Settings denied (403),
  guardrail trips, notification send failure.
- **Unexpected exceptions** propagate as real tracebacks (genuine bugs stay
  visible). `ConfigError` is also logged at `error` level inside `_run` before
  re-raising.

**Two-phase ordering (preflight all groups before writes)**: `_run` first builds
a `GroupSyncPlan` for **every** group (this is where source validation, the
current-membership read, the empty/removal guards, and the posting-permission
read all happen), and only then applies the plans, and only then (if live) sends
notifications. Consequently a failure while planning a *later* group prevents
*earlier* groups' membership writes (tests confirm: a later 404 yields two
`list` calls and no writes). Notification sending happens after all writes, so a
notification failure does not roll back or skip the already-applied membership
changes — but it still surfaces as exit `2`.

## 10. Edge cases and nuances

- **Legacy key aliases**: `ggroup` is accepted for `group`; `owner` for static
  member `leader`.
- **Case-insensitive group dedupe** rejects e.g. `group@…` and `GROUP@…`.
- **Leader implies member** across all source types; a leader-only selector
  still adds the person as a member.
- **Primary-email-only**: each person maps to exactly one group entry via their
  first email; members with no email are silently skipped.
- **Blank email fragments** are dropped by `split_email_addresses`, so a member
  whose primary fragment is blank uses the next non-blank address.
- **Alias merging**: equivalent Gmail-style desired addresses collapse into one
  `DesiredMember`, accumulating all names; role changes/deletes use the matched
  *current* Google member id even when matched by alias normalization.
- **Dots preserved for Workspace domains**; `+tags` stripped for all configured
  `google_mail_domains`; dots removed only for `gmail.com`/`googlemail.com`.
- **`ministry_role` leader preservation**: a later non-leader ministry match
  cannot demote an earlier leader match for the same person.
- **`all_ministry_chairs` owner test uses the primary email domain** (the
  address that will actually be added), not any secondary address.
- **Membership-only syncs** (no `notify`) never call the Groups Settings API and
  never need its scope; the settings service and email provider are built
  lazily, so a no-op live run requires neither (tests assert
  `provider_from_config` and `build_groups_settings_service` are not invoked).
- **Posting-permission/settings failures happen during planning**, before
  membership writes, so a 403 on settings aborts without partial writes.
- **Selector matching nothing** (prefix/pattern matches no loaded ministry) is a
  startup `ConfigError`, distinct from a selector that matches ministries but no
  members (which simply yields zero desired members and is subject to the
  empty/removal guards).
- **Dry run** performs every read and the full diff, refreshes no credential
  files, issues no writes, and sends no email.

## 11. Testing notes (`tests/test_sync_google_group.py`)

Injection seams used by tests: `main(..., loader=, service_factory=,
email_provider=)`; `parishsoft_client_from_config` monkeypatched to a no-op
stand-in; fake `AdminService`/`SettingsService`/`Members`/`Groups`/`Request`
recording every call; fake `EmailProvider` capturing sent messages. Underlying
Google helpers also expose `build_fn` and reads use injectable `sleep` (see the
intro testing section).

Locked-down behavior asserted:

- **Config parsing**: valid selector parses (`ministry_role` with prefix/pattern/
  member_roles/leader_roles); duplicate group targets rejected (case-insensitive);
  unknown group key rejected ("unsupported key"); notify-without-sender rejected;
  group-without-source rejected; invalid `ministry_pattern` rejected.
- **Credentials**: relative `service_account_file` resolves against the config
  dir; `include_settings_scope=False` requests only the member scope.
- **Desired set**: ministry chair → leader, workgroup member → non-leader, static
  keeps its flag, in source order; configurable `leader_roles` controls owner
  mapping (e.g. `Coordinator`); blank primary fragment skipped; Gmail aliases
  merge and accumulate names; `all_ministry_chairs` pattern filtering and
  primary-domain owner test; `ministry_role` leader preserved across ministries.
- **`compute_actions`**: add/delete/change_role; role change uses the matched
  current member id (including alias matches); `normalize_email` examples.
- **Guards**: empty-desired with current members raises (mentions `allow_empty`);
  40-of-40 removal trips the large-removal guard.
- **End to end** (`main`): live run inserts new ministry/workgroup/static
  members, promotes the leader to `OWNER`, deletes the stale member, emails the
  notify address; verifies subject, "Discussion" label, table, rationale text
  (HTML-escaped ministry/workgroup phrases), `[Static member]`, "Added to group";
  loader called with `{active_only: True, parishioners_only: False}`; success
  message in the log file.
- **Notification posting**: `ALL_IN_DOMAIN_CAN_POST` → Discussion wording.
- **Dry run**: only a `list` call, no writes, no email (email section disabled).
- **No-op live run**: does not build the email provider or the settings scope.
- **Ordering/failure**: notification failure still applies all groups' writes
  (and exits 2); missing group → helpful config error, exit 2; preflight makes a
  later 404 prevent earlier writes; missing ParishSoft source aborts before any
  Google call; settings 403 aborts before writes; membership-only sync skips the
  Settings API; selector matching no ministry aborts before writes.

## 12. Re-creation task outline

1. Define dataclasses: `StaticMember`, `Selector`, `GroupSync`, `SyncConfig`,
   `DesiredMember`, `SyncAction`, `GroupSyncPlan`; constants `ADMIN_SCOPE`,
   `GROUP_SETTINGS_SCOPE`, `LEADER_ROLES = {"Chairperson", "Staff"}`.
2. Config parsing/validation: `sync_config_from_yaml`, `_group_sync`,
   `_static_member`, `_selector`, `_validate_unique_groups`, and the
   `_mapping/_list/_string_list/_required_string/_optional_string/_optional_regex/
   _bool/_positive_int/_fraction` helpers, all using `reject_unknown_keys` and
   raising `ConfigError`. Honor `ggroup`/`owner` aliases and required-source rule.
3. Credentials: `load_google_credentials` (mutually-exclusive file keys,
   scope assembly, DWD subject) and `build_google_services`.
4. Source resolution: `validate_configured_parishsoft_sources`,
   `available_ministry_names`, `available_member_workgroup_source_names`
   (with ` Ldr`/` Leader` base-name expansion), `selector_matches_any_ministry_name`.
5. Desired set: `desired_members`, `member_matches_group`, `member_in_ministries`,
   `member_in_workgroups`, `selector_matches_member`, `ministry_matches_selector`,
   `ministry_name_matches_selector`, `is_ministry_leader`, `add_desired_member`,
   `member_display_name`.
6. Email normalization: `normalize_email`, `compare_email`,
   `normalized_group_members`.
7. Diff/apply: `compute_actions`, `validate_large_removal_guard`,
   `removal_guard_tripped`, `apply_actions`, `apply_group_plan`,
   `list_group_members_or_config_error`.
8. Planning: `plan_group` (dry-run early return; lazy provider/settings;
   posting-permission fetch gate), `sync_group` compatibility wrapper,
   notification gates (`group_has_notification_content`,
   `group_notification_will_send`).
9. Notifications: `build_notification_email`, `send_notification`,
   `send_group_plan_notification`, and the `_group_*`/`_action_*`/`_source_*`/
   `_email_*` formatting helpers + style constants.
10. Orchestration: `main` (`--version`, `run_user_facing`) and `_run` (resolve
    common options, `require_explicit_write_mode`, load config + data, validate
    sources, build admin service eagerly + settings lazily, **plan all groups,
    then apply all, then notify all in live mode**, success log).
11. Wrapper `scripts/pk-sync-ps-to-ggroup/pk-sync-ps-to-ggroup.py`, the
    `cli.sync_google_group_main` entry point, `example-config.yaml`, and the
    operator README. Mirror the assertions in
    `tests/test_sync_google_group.py`.

## 13. Cross-references

- Shared CLI / CommonOptions / precedence / error funnel / write gate:
  [`../intro/spec.md#shared-cli-layer`](../intro/spec.md#shared-cli-layer),
  [`#dry-run-and-write-safety`](../intro/spec.md#dry-run-and-write-safety).
- Config / logging+Slack / retry / secrets:
  [`#configuration-system`](../intro/spec.md#configuration-system),
  [`#logging-and-notifications`](../intro/spec.md#logging-and-notifications),
  [`#retry-policy`](../intro/spec.md#retry-policy),
  [`#secrets-and-filesystem-helpers`](../intro/spec.md#secrets-and-filesystem-helpers).
- ParishSoft data layer + Google integration + email provider:
  [`#parishsoft-data-layer`](../intro/spec.md#parishsoft-data-layer),
  [`#google-integration-layer`](../intro/spec.md#google-integration-layer),
  [`#email-provider-layer`](../intro/spec.md#email-provider-layer).
- ParishSoft API specifics and roster read-only fact:
  [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md).
- Sibling sync tool (same reconciliation shape, Constant Contact):
  [`../pk-sync-ps-to-cc/spec.md`](../pk-sync-ps-to-cc/spec.md).
- Ministry roster tool (shares ParishSoft ministry/role concepts):
  [`../pk-create-ps-ministry-rosters/spec.md`](../pk-create-ps-ministry-rosters/spec.md).
- Operator docs and example config:
  `scripts/pk-sync-ps-to-ggroup/README.md`,
  `scripts/pk-sync-ps-to-ggroup/example-config.yaml`.
</content>
</invoke>
