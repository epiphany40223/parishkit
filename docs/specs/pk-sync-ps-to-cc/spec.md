# pk-sync-ps-to-cc — ParishSoft workgroups → Constant Contact lists

## 1. Purpose and role

`pk-sync-ps-to-cc` keeps Constant Contact email lists in step with ParishSoft
**member workgroups**. For each configured pairing of one ParishSoft workgroup
to one Constant Contact list, it works out who *should* be on the list from the
workgroup's membership, **respects anyone who has unsubscribed in Constant
Contact** (the headline feature), and adds or removes contacts to match. It can
optionally correct contact first/last names and email a per-list change summary,
and it can emit a separate scheduled report naming people who are still in a
synced workgroup but have manually opted out in Constant Contact (so they can be
removed from the workgroup in ParishSoft).

Category: **Sync** (writes to Constant Contact lists). It is one of the two
membership-sync tools and follows the same read-source → read-target → diff →
apply-or-report reconciliation skeleton described in the
[top-level spec](../intro/spec.md#architecture-and-data-flow). ParishSoft is
the system of record; data flows outward to Constant Contact. The sibling
[`pk-sync-ps-to-ggroup`](../pk-sync-ps-to-ggroup/spec.md) shares that exact
reconciliation shape against Google Groups; see
[§11](#11-edge-cases-and-nuances) and [§14](#14-cross-references) for the
comparison.

This document specifies tool-specific behavior only. Shared infrastructure
(CLI/`CommonOptions` and precedence, config helpers, logging/Slack, retry,
secrets, the error funnel, dry-run/write-safety, the Constant Contact *client*
internals + OAuth mechanics, the ParishSoft client, the email provider layer) is
defined once in the intro spec and linked, not repeated.

## 2. Invocation

- **Console command:** `pk-sync-ps-to-cc` (installed launcher symlink under
  `<root>/bin/`).
- **Entry point:** `pk-sync-ps-to-cc = "parishkit.cli:sync_ps_to_cc_main"`
  (`pyproject.toml [project.scripts]`). `cli.sync_ps_to_cc_main(argv)` lazily
  imports `parishkit.pk_sync_ps_to_cc` and delegates to its `main()`.
- **Wrapper:** `scripts/pk-sync-ps-to-cc/pk-sync-ps-to-cc.py` — a
  `#!/usr/bin/env python3` shim that calls `sync_ps_to_cc_main()` and exits with
  its return code. It only delegates (no logic, no `sys.path` edits).
- **`main(argv=None, *, loader=…, cc_factory=None, email_provider=None,
  now=None)`** is the tool body. The keyword parameters are injection seams for
  tests (see [§12](#12-testing-notes)); production calls supply none of them.
- **`--version`** prints `pk-sync-ps-to-cc <parishkit version>` (from
  `importlib.metadata.version("parishkit")`) and returns `0`, short-circuiting
  before any config load, ParishSoft, or Constant Contact work.

## 3. Command-line options

Shared flags come from `parser_with_common_options(...)` and behave exactly as
documented in [Shared CLI layer](../intro/spec.md#shared-cli-layer): `--config`;
tri-state `--dry-run/--no-dry-run`, `--verbose/--no-verbose`,
`--debug/--no-debug`; `--log-file`, `--log-dir`; `--slack-token-file`,
`--slack-channel`, `--slack-log-level`; `--ps-api-key-file`, `--ps-cache-dir`,
`--ps-cache-limit`.

Tool-specific flags (added directly in `main`):

| Flag | Action | Effect |
| --- | --- | --- |
| `--version` | `store_true` | Print version and exit 0. |
| `--update-names` | `store_true` | Force the name-correction pass on. |

`--update-names` is **opt-in only**: the resolved value is
`sync.update_names (YAML) OR --update-names (CLI)`. There is no
`--no-update-names`; the flag can turn the toggle on but never off. (Comment in
`_run`: "CLI flags can only turn the toggles on, never off.")

This tool is write-capable, so it calls `require_explicit_write_mode` — the
operator must set `common.dry_run` explicitly via `--dry-run/--no-dry-run` or
`common.dry_run` in YAML; there is no implicit live-write default. See
[Dry-run and write safety](../intro/spec.md#dry-run-and-write-safety).

## 4. Configuration schema

One YAML file passed with `--config`. Shared sections (`common`, `logging`,
`slack`, `parishsoft`) are validated centrally
([Configuration system](../intro/spec.md#configuration-system)); the
`email` section is validated by the email provider layer
([email-provider-layer](../intro/spec.md#email-provider-layer)). The
tool-specific sections are `constant_contact` and `sync`. All key sets are
typo-proofed with `reject_unknown_keys`. See
`scripts/pk-sync-ps-to-cc/example-config.yaml` for an annotated template.

### 4.1 `constant_contact` section (required)

Allowed keys: **`client_id_file`**, **`access_token_file`** (both required,
non-empty strings; any other key is rejected).

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `client_id_file` | path string | yes | App/client JSON: client id + `endpoints.{api,auth,token}`. |
| `access_token_file` | path string | yes | OAuth token JSON produced by the device-OAuth smoke tool. |

Both are read at runtime via `load_client_id` / `get_access_token`. Relative
paths resolve against the **config file's directory** (`resolve_path(...,
base_dir=config_base_dir)`). The token is loaded with `allow_refresh = not
common.dry_run` (see [§8](#8-guardrails-and-write-safety)). These are the
documented credential files; the *client* and its OAuth lifecycle are specified
in [Constant Contact layer](../intro/spec.md#constant-contact-layer).

### 4.2 `sync` section (required)

Allowed keys: **`lists`**, **`notifications`**, **`unsubscribed_report`**,
**`update_names`**. Parsed by `cc_sync_config_from_yaml` into a `CCSyncConfig`
(`mappings`, `update_names`, `sender`, `unsubscribed_report`).

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `lists` | list of mappings | — (required, **non-empty**) | Empty/missing → `ConfigError "sync.lists must not be empty"`. |
| `notifications` | mapping | `{}` | Only key: `sender`. |
| `notifications.sender` | string \| null | `None` | From-address for all notification/report emails. Required if **any** mapping lists notification recipients. |
| `update_names` | bool | `false` | Also correct CC first/last names when they differ from ParishSoft. OR'd with `--update-names`. |
| `unsubscribed_report` | mapping | disabled | Scheduled opt-out report ([§4.4](#44-syncunsubscribed_report)). |

#### 4.3 `sync.lists[]` — the workgroup → list mapping

Each entry parses into a frozen `CCSyncMapping`. Allowed keys (both current
snake_case **and** legacy spaced aliases are accepted for backward
compatibility):

| Key (alias) | Type | Default | Validation |
| --- | --- | --- | --- |
| `source_workgroup` (`source ps member wg`) | string | — (required) | Non-empty; must exactly match a ParishSoft **member** workgroup name. |
| `target_list` (`target cc list`) | string | — (required) | Non-empty; must exactly match a Constant Contact list **name**; unique across mappings (case-insensitive). |
| `notifications` | list of strings | `()` | Email recipients for this mapping's summary/report. |
| `allow_empty` | bool | `false` | Permit emptying a populated list (empty-source guard). |
| `max_removals` | positive int | `25` | Absolute removal-count guard. |
| `max_removal_fraction` | number in `[0,1]` | `0.5` | Fractional removal guard. |

Mapping-level validation:

- `_required_string` for the two required fields (missing/blank → `ConfigError`).
- Duplicate `target_list` (compared with `casefold()`) → `ConfigError
  "sync.lists[].target_list values must be unique; …"` — one CC list may not be
  reconciled by two mappings (`_validate_unique_target_lists`).
- `max_removals` must be a positive int (bools rejected); `max_removal_fraction`
  must be a non-bool number in the inclusive range `0.0–1.0`.
- If **any** mapping has a non-empty `notifications` list but
  `sync.notifications.sender` is unset → `ConfigError "sync.notifications.sender
  is required when any sync.lists[].notifications recipient is configured"`.

#### 4.4 `sync.unsubscribed_report`

Parses into a frozen `CCUnsubscribedReportConfig`. Allowed keys: `enabled`,
`day_of_week`, `time`, `window_minutes`, `state_file`.

| Key | Type | Default | Validation |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Master toggle for the scheduled report. |
| `day_of_week` | weekday name \| null | `None` (every day) | `monday`–`sunday` or 3-letter form, case-insensitive, mapped to Python weekday index 0–6 (`WEEKDAY_NAMES`); `null`/`""` → every day; unknown → `ConfigError`. |
| `time` | `HH:MM`/`HH:MM:SS` string | `"02:00"` | Local time, **no** timezone allowed; parsed via `dt.time.fromisoformat`. |
| `window_minutes` | positive int | `60` | Length of the local send window starting at `time`. |
| `state_file` | path string | `default_run_dir()/pk-sync-ps-to-cc-unsubscribed-report.json` | JSON state recording per-day/per-mapping sends; relative paths resolve against the config dir. |

The module default (`DEFAULT_UNSUBSCRIBED_REPORT_STATE` /
`default_unsubscribed_report_state()`) is `<run dir>/…` where the run dir comes
from `cli.default_run_dir()` (`<PARISHKIT_ROOT-or-/opt/parishkit>/run`).

## 5. Source data

The tool loads ParishSoft via the injected `loader` (default
`load_families_and_members`) with **`active_only=True, parishioners_only=False`**
— active members regardless of parishioner status. See
[ParishSoft data layer](../intro/spec.md#parishsoft-data-layer) and
[`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) for what is read
and how it is cross-linked.

Desired membership derivation (`resolve_desired_state`):

- The relevant source is **member workgroups** (`data.member_workgroup_memberships`,
  indexed by workgroup `name`). Family workgroups are not used.
- For each mapping, the matching workgroup's `membership` list is walked. Each
  membership row's `"py member duid"` is resolved to a member in `data.members`.
- For each resolved member, **only the primary (first) email address** is used:
  the first item returned by `member_email_addresses(...)` (lowercased,
  de-duplicated; prefers the pre-split `"py emailAddresses"` list, else splits
  the raw semicolon-delimited `emailAddress`). Members with no email contribute
  nothing.
- Result: one lowercased `set[str]` of desired emails per mapping, in mapping
  order.

`parishsoft_members_by_email(data.members)` builds a reverse index (lowercased
email → list of member dicts; one member can appear under several addresses and
several members can share one address) used for create payloads and report
names.

## 6. Reconciliation algorithm

`_run` executes these steps in order (kept explicit for auditability):

1. **Common setup.** `resolve_common_options(args)`; `load_yaml_config`;
   `setup_logging(verbose = common.verbose OR common.dry_run, …)` — note a dry
   run is always at least INFO-verbose. Logger name
   `parishkit.pk_sync_ps_to_cc`.
2. **Write gate.** `require_explicit_write_mode(common, "pk-sync-ps-to-cc")`.
3. **Parse `sync`** into `CCSyncConfig`; OR `update_names` with `--update-names`.
4. **Load ParishSoft** (`parishsoft_client_from_config` then `loader(...,
   active_only=True, parishioners_only=False)`); log member/family/ministry/
   workgroup counts.
5. **Validate workgroups exist** (`validate_configured_parishsoft_workgroups`)
   — *before any Constant Contact access*. A missing `source_workgroup` raises a
   detailed `ConfigError` listing all available member workgroup names.
6. **Build the CC client** — `cc_factory(config)` if injected, else
   `constant_contact_client(config, base_dir, allow_token_refresh = not
   common.dry_run)`.
7. **Load CC state** (`load_cc_data`):
   - `get_all("contact_lists", "lists")` → all lists.
   - `get_all("contacts", "contacts", include="list_memberships",
     status="all")` → all contacts including unsubscribed.
   - Drop any contact carrying a `"deleted_at"` key (soft-deleted contacts are
     never reconciled).
   - `link_cc_data(contacts, [], lists)` resolves list ids → names and rebuilds
     each list's `"CONTACTS"` index (email → contact) from live, non-deleted
     contacts. (Custom fields are passed empty; this tool does not load them.)
8. **Cross-link.** `parishsoft_members_by_email(...)`; then
   `link_contacts_to_ps_members(cc_contacts, data.members)` attaches
   `"PS MEMBERS"` to matched contacts by email.
9. **Desired state.** `resolve_desired_state(...)` ([§5](#5-source-data)); raises
   `ConfigError` if a `target_list` name is not an existing CC list (lists all
   available list names) or a workgroup is missing.
10. **Empty-source guard** (`validate_non_empty_desired_state`) — evaluated on
    the *pre-unsubscribe* desired sets (see [§8](#8-guardrails-and-write-safety)).
11. **Unsubscribe filter** (`filter_unsubscribed`) — removes opted-out addresses
    from the desired sets *in place* and returns per-mapping report tuples
    ([§7](#7-unsubscribe--opt-out-handling)).
12. **Report schedule decision** (`unsubscribed_report_decision`, using the
    local `now`).
13. **Index contacts by email** — `contacts_by_email = {address.lower():
    contact}` (if two contacts share an address the last wins).
14. **Compute actions** (`compute_all_actions` + `detect_name_mismatches`):
    - `compute_create_actions`: for each desired email (union of all filtered
      desired sets, sorted) with **no** existing contact → a `create` action,
      attributed to the first mapping that wants it.
    - `compute_subscribe_unsubscribe_actions`: per mapping, with `current =
      set(list["CONTACTS"])`: `subscribe` for `sorted(desired − current)`;
      `unsubscribe` for `sorted(current − desired)`. Each carries
      `list_name`/`list_uuid` (the CC `list_id`) and `sync_index`.
    - `detect_name_mismatches` (only when `update_names`): for contacts in scope
      (see [§11](#11-edge-cases-and-nuances)) that have linked `PS MEMBERS`,
      compute `salutation_for_members(members)`, strip `.` from the first name,
      and emit an `update_name` action (carrying `new_first`/`new_last`) when it
      differs from the stored CC `first_name`/`last_name`. One action is emitted
      per scoping `sync_index` (for notification attribution).
15. **Large-removal guard** (`validate_large_removal_guard`) — after actions are
    known ([§8](#8-guardrails-and-write-safety)).
16. **Report config preflight** (`validate_unsubscribed_report_config`) — for a
    due, non-dry-run report with content: require per-list recipients, require
    `sender`, and probe state-file writability *before any writes*.
17. **Resolve email provider.** `provider = email_provider` if injected;
    otherwise, only when `not dry_run` and `needs_email`
    (`sync_notifications_will_send(...)` OR `unsubscribed_report_will_send(...)`),
    build it from the `email` section via `provider_from_config`. A live run
    with nothing to send never constructs a provider (so missing email creds
    don't block a no-op run).
18. **Report provider preflight** (`validate_unsubscribed_report_provider`) — a
    due report with content but no provider → `ConfigError`.
19. **Apply** (`execute_actions`).
20. **Notify** (`send_notifications`) then **scheduled report**
    (`send_unsubscribed_report`).
21. Log action count and "completed successfully" for N mappings; return `0`.
    Any `ConfigError` is logged at `error` and re-raised (handled by the funnel).

### Apply phase — POST vs PUT (`execute_actions`)

Actions are grouped by email so each contact incurs at most **one POST and one
PUT**:

- `post_body_for_actions` builds the **sign-up POST** body from `create` +
  `subscribe` actions. A `create` seeds the body from ParishSoft via
  `create_contact_dict(email, ps_members_by_email[email])` (name from the
  salutation); an existing contact (subscribe-only) reuses its stored name. The
  body's `list_memberships` is the set of subscribe `list_uuid`s.
- `put_body_for_actions` builds the **update PUT** body from `unsubscribe` +
  `update_name` actions. It starts from the contact's current
  `list_memberships`, removes each unsubscribed `list_uuid`, and applies the
  **last** `update_name` (`new_first`/`new_last`).
- When the same contact has *both* a POST and a PUT, the subscribe list ids are
  **folded into the PUT's `list_memberships`** so the final membership reflects
  both operations rather than one clobbering the other.
- Writes: `client.post("contacts/sign_up_form", sign_up_form_body(post_body))`
  (one-shot create) and `client.put("contacts/{contact_id}",
  update_contact_body(put_body))` (retryable). `sign_up_form_body` /
  `update_contact_body` copy only writable fields and strip periods from first
  names. See [Constant Contact layer](../intro/spec.md#constant-contact-layer)
  and [Retry policy](../intro/spec.md#retry-policy).
- **Dry run:** the POST/PUT bodies are still built (so the work is exercised and
  logged via `log_extra`) but no API call is issued.

## 7. Unsubscribe / opt-out handling

This is the tool's headline behavior. An opted-out person must never be
re-added, re-created, or re-subscribed to the lists, yet the operator must be
told they are still in the workgroup. Name updates are scoped separately: an
unsubscribed contact can still receive an `update_name` action if the contact is
currently in a configured target list and `sync.update_names` is enabled.

**Detection.** A Constant Contact contact is treated as opted-out when its
`email_address["permission_to_send"] == "unsubscribed"`. This is a *contact-level*
status (global to that contact), not per-list. `status="all"` on the contacts
read ensures unsubscribed contacts are present to be detected.

**Filtering (`filter_unsubscribed`).** For every contact with
`permission_to_send == "unsubscribed"`, its lowercased address is **discarded
from every desired set** that contains it. Because the address leaves the
desired sets:

- it produces **no `subscribe` action** (never re-added to a list), and
- it produces **no `create` action** (the create source is the union of the
  *filtered* desired sets) — so an opted-out person without a contact is never
  created either.

For each removed address the function records a `(email, names, duids)` tuple in
that mapping's slot of the returned per-mapping list, where `names` is the
comma-joined `"py friendly name FL"` of the ParishSoft members at that address
and `duids` the comma-joined `memberDUID`s. `desired_emails` is mutated in
place, so all later steps (name-update scoping, action computation) see the
filtered sets.

**Important nuance — list cleanup still happens.** Opting out blocks *adds*, not
*removals*. If an opted-out contact is currently a member of a target list
(`list_memberships` includes it), then after filtering the address is in
`current` but no longer in `desired`, so the normal diff emits an `unsubscribe`
(list-membership removal) action and the contact is PUT to drop that list. Such
a contact can therefore appear in **both** the "Actions Performed" table (as an
unsubscribe) and the "Filtered Unsubscribed Contacts" report — the two are
independent. The guarantee is: opted-out people are never subscribed or created.

**Reporting.** The filtered tuples drive either the regular sync email's
"Filtered Unsubscribed Contacts" section or, when the scheduled report is
enabled, the standalone report (see [§9](#9-outputs-reporting-and-notifications)).
The scheduled report's framing is that these members are still in the workgroup
but have manually unsubscribed and **should be removed from the workgroup in
ParishSoft** — ParishKit cannot do that itself (workgroup membership is
read-only; see [`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md)).

## 8. Guardrails and write safety

All guards raise `ConfigError` (→ exit 2) before any Constant Contact write.

- **Empty-source guard** (`validate_non_empty_desired_state`): for each mapping,
  skip if the desired set is non-empty *or* `allow_empty` is true. Otherwise, if
  the target list currently has contacts, abort — a workgroup that unexpectedly
  resolved to zero addresses must not silently unsubscribe everyone. The error
  points at `source_workgroup` and notes `allow_empty: true` as the intentional
  override. **This guard runs on the pre-unsubscribe desired set**, so a source
  whose members are *all* opted-out (non-empty before filtering, empty after)
  does **not** trip it; the run proceeds and only cleans up list membership.
- **Large-removal guard** (`validate_large_removal_guard` /
  `removal_guard_tripped`): per mapping, count `unsubscribe` actions for that
  `sync_index` against the list's current contact count. It trips **only when
  both** thresholds are exceeded: `removal_count > max_removals` **and**
  `removal_count / current_count > max_removal_fraction` (and `current_count >
  0`). Tripping aborts with a "would remove X of Y" error suggesting raising
  `max_removals` / `max_removal_fraction`.
- **Write intent.** `require_explicit_write_mode` forces an explicit `dry_run`
  choice ([§3](#3-command-line-options)).
- **One-shot create vs retryable update.** The sign-up POST is one-shot
  (`RetryPolicy(attempts=1)`) so a hidden-success transient response can't
  duplicate a contact; the update PUT uses the normal retry policy. (Client
  detail; see [Retry policy](../intro/spec.md#retry-policy).)
- **Dry-run never refreshes tokens.** `allow_token_refresh = not common.dry_run`
  is threaded into `get_access_token`, so a dry run with an expired token raises
  rather than rewriting the credential file; a dry run reads everything, writes
  no contacts, sends no email, and does not touch the report state file. See
  [Dry-run and write safety](../intro/spec.md#dry-run-and-write-safety).
- **Tenant guard.** `expected_organization` is enforced by the ParishSoft client
  before any sync work (intro spec).

## 9. Outputs, reporting, and notifications

**Console + JSONL log** (shared logging; no separate report artifact). INFO lines
report counts (configured mappings, members/families loaded, lists/contacts
loaded, desired emails per mapping, filtered-unsubscribed count, report-schedule
reason, actions computed, completion). `log_extra(...)` attaches structured
payloads to the JSONL log file: the mapping list, per-mapping desired emails,
the filtered-unsubscribed tuples, and — in dry run — the would-be POST/PUT
bodies per contact. See
[Logging and notifications](../intro/spec.md#logging-and-notifications).

**Regular sync notification email** (`send_notifications` →
`build_notification_email`), one per mapping that had actions or (when the
scheduled report is disabled) filtered unsubscribes, sent to that mapping's
`notifications` recipients from `sender`. Skipped entirely when there is no
provider, no `sender`, or the mapping has no recipients. Content:

- Subject `Constant Contact sync update: <target_list>`.
- Plain-text + HTML alternatives. Header: generated timestamp
  (`%Y-%m-%d %H:%M:%S`), source workgroup, target list.
- Summary counts: contacts created / subscribed / unsubscribed, and "Unsubscribed
  contacts filtered". When the scheduled report is enabled and there were
  filtered contacts, that count reads `N (handled by scheduled report)` and the
  per-row table is suppressed (details move to the report); otherwise it is the
  visible filtered count with a "Filtered Unsubscribed Contacts" table.
- An "Actions Performed" striped HTML table (Action / Contact Name(s) / PS Member
  DUID(s) / Email), actions sorted by type
  (`create, subscribe, unsubscribe, update_name`) then email; and, when shown,
  the filtered-unsubscribed table sorted by the first member's last/first name.
  Includes an automated-message footer.

**Scheduled unsubscribed report** (`send_unsubscribed_report` →
`build_unsubscribed_report_email`) — see [§7](#7-unsubscribe--opt-out-handling)
and below for scheduling. One email per mapping that has filtered unsubscribes,
subject `Constant Contact unsubscribed contacts report: <target_list>`,
explaining the members are in the workgroup but opted out and should be removed
from the workgroup in ParishSoft, with a striped table (PS Member Name(s) / PS
Member DUID(s) / Email). Sends are recorded in the JSON state file under an
exclusive `fcntl` lock, per-mapping and per-local-day.

**Slack** alerting on failure is handled by the shared logging Slack sink
(threshold defaults to `CRITICAL`); this tool emits no Slack-specific calls.

### Scheduled-report timing (`unsubscribed_report_decision`)

Given the local `now` (`dt.datetime.now(ZoneInfo(common.timezone))` unless `now`
is injected): not due if disabled, if `day_of_week` is set and today's weekday
differs, if `now` is before `time`, if `now` is at/after `time + window_minutes`,
or if the state file's `last_sent_date` already equals today (`run_date`).
Otherwise due. This lets the sync run frequently (e.g. every 15 min) while the
report is sent at most once per local day inside its window.

### Report state file (`state_file`)

JSON written atomically (`atomic_write_text`, owner-only) and guarded by an
adjacent `<name>.lock` (`fcntl.LOCK_EX`):

- `sent_reports`: `{ run_date: [ per-mapping key, … ] }`, where the key is
  `json.dumps([source_workgroup, target_list], separators=(",", ":"))` (compact
  JSON, no spaces). Lets a partially-failed run retry only the mappings not yet
  sent.
- `last_mapping_sent_at`: ISO timestamp of the last per-mapping write.
- `last_sent_date` / `last_sent_at`: set once *all* mappings for the day are sent
  after entering the state-locking path; `last_sent_date` is what the schedule
  check reads to dedupe per day. If the report is due but there are zero
  unsubscribed contacts across all mappings, the function logs that there is
  nothing to report and returns before updating state, so the empty run is not
  marked sent.

`mark_unsubscribed_report_mapping_sent` writes state immediately after each
successful per-list send (so a later failure cannot resend an already-sent
mapping); `mark_unsubscribed_report_sent` finalizes the day.

## 10. Failure modes and exit codes

`main` runs the body through `run_user_facing` (the shared
[error funnel](../intro/spec.md#shared-cli-layer)):

| Outcome | Exit |
| --- | --- |
| Success / `--version` | `0` |
| Expected operational error — `ConfigError`, `OSError`, `ParishSoftAPIError`, `CCAPIError`, `GoogleAPIError` (email path), `RetryError` | `2` (single `ERROR: …` line on stderr) |
| Unexpected exception (genuine bug) | propagates as a traceback |

Representative `ConfigError`s: empty/missing `sync.lists`; duplicate
`target_list`; unknown key in any tool section; missing `source_workgroup`
(workgroup not found in ParishSoft); missing `target_list` (CC list not found);
`sender` required when recipients configured; empty-source guard; large-removal
guard; due-report missing recipients / `sender` / provider / unwritable state;
expired token under dry run (`get_access_token` with `allow_refresh=False`).
A failed email/report send (e.g. provider raises `OSError`) propagates → exit 2,
but per-mapping report state already written before the failure means the next
run resends only the unsent mappings.

## 11. Edge cases and nuances

- **Verbose dry run.** Dry run forces verbose logging (`verbose or dry_run`).
- **Primary-email-only.** Only each member's first email is desired; secondary
  addresses are ignored for membership (but `parishsoft_members_by_email` and
  `link_contacts_to_ps_members` index *all* of a member's addresses for naming).
- **Soft-deleted contacts** (`deleted_at`) are excluded from both the contact
  set and every list's `CONTACTS` index.
- **Contact created once.** A desired email on several mappings yields a single
  `create`, attributed to the first mapping wanting it; subscribes are still
  emitted per mapping.
- **Name-update scoping** (`name_update_candidate_sync_indices`): renames are
  restricted to emails in scope — the (filtered) desired set ∪ the current
  `CONTACTS` of each configured target list. Because current target-list
  contacts remain in scope even when filtered out of the desired set, an
  unsubscribed contact already on a configured list can still receive a name
  update. Unrelated CC lists/contacts in the account are never renamed.
  `detect_name_mismatches` emits one
  `update_name` per scoping `sync_index`, but `execute_actions` collapses them
  into a single PUT (applying the last name update).
- **Period stripping.** Both the comparison (`detect_name_mismatches`) and the
  write bodies strip `.` from first names, so titles like "Fr." compare and
  store consistently.
- **All-opted-out source.** A non-empty workgroup whose members are all
  unsubscribed passes the empty-source guard (checked pre-filter) and still
  cleans up list membership for any of them currently on the list.
- **Legacy config keys.** `source ps member wg` / `target cc list` are accepted
  alongside the snake_case names.
- **Comparison to the sibling sync.** `pk-sync-ps-to-ggroup` shares the
  read→diff→apply shape, the empty-source and large-removal guards, and the
  dry-run discipline, but reconciles **Google Group membership** (add/
  remove/role) rather than CC list membership, has no unsubscribe/opt-out
  concept, no contact create/rename, and no scheduled opt-out report. See
  [`../pk-sync-ps-to-ggroup/spec.md`](../pk-sync-ps-to-ggroup/spec.md).

## 12. Testing notes

`tests/test_sync_ps_to_cc.py` (no real credentials, no network) locks down:

- **Config parsing/validation** — round-trip into `CCSyncConfig`; duplicate
  target-list rejection; unknown mapping key rejection; `sender` required for
  recipients; report-schedule parsing (weekday → index, time, window, state
  path); relative report-state path resolved against the config dir; missing
  `lists` rejected; default `state_file` equals `DEFAULT_UNSUBSCRIBED_REPORT_STATE`.
- **CC credential paths** resolve relative to the config dir; `allow_refresh`
  defaults true (`test_constant_contact_client_resolves_relative_credential_paths`).
- **`load_cc_data`** filters `deleted_at` contacts and excludes them from
  `CONTACTS`.
- **Desired state + filtering** — `resolve_desired_state` maps both members; the
  unsubscribed member is dropped from desired and reported with friendly name.
- **Guards** — empty-source abort (`allow_empty`), large-removal abort
  (`would remove 40 of 40`), and the all-opted-out-source case still issuing the
  stale-member PUT.
- **Action computation** — create+subscribe / subscribe / unsubscribe /
  update_name ordering; name updates scoped to configured lists/desired emails.
- **End-to-end** (`main` with injected `loader`, `cc_factory`, `email_provider`,
  `now`): live run posts + puts and emails the configured recipient with the
  expected subject/HTML; loader called with `active_only=True,
  parishioners_only=False`; "completed successfully" reaches the log file.
- **Scheduled report** — sent once per day and state-backed; relative state path
  honored regardless of cwd; suppresses the regular unsubscribed notice and
  rewrites its count as "handled by scheduled report"; waits for the configured
  weekday; due report requires recipients/sender/provider/writable state (each
  → exit 2 before writes, with `cc.calls` limited to the two reads or empty);
  per-list state retries only the unsent mapping after a partial email failure;
  preflight probe preserves an existing lock file.
- **Dry run** — only the two `get_all` reads, no post/put/email; an injected
  provider still receives `dry_run=True`; a live no-op never builds the provider.
- **Missing workgroup / missing list** — helpful `ConfigError`s, exit 2.

Injection seams used by these tests: `loader=`, `cc_factory=`,
`email_provider=`, `now=` on `main`; monkeypatched
`parishsoft_client_from_config` and `provider_from_config`. The fake `CCClient`
records `get_all`/`post`/`put`; the fake `EmailProvider` captures sent messages.

## 13. Re-creation task outline

1. Define dataclasses: `CCSyncMapping`, `CCUnsubscribedReportConfig`,
   `ReportScheduleDecision`, `CCSyncConfig`, `CCAction`; module defaults
   (`DEFAULT_UNSUBSCRIBED_REPORT_*`, `WEEKDAY_NAMES`).
2. Config parsers: `cc_sync_config_from_yaml` (+ `_mapping_config`,
   `_unsubscribed_report_config`, `_validate_unique_target_lists`) with full
   key-set/`reject_unknown_keys` validation and the typed scalar helpers
   (`_required_string`, `_string_list`, `_bool`, `_positive_int`, `_fraction`,
   `_time`, `_day_of_week`, `_path`).
3. `constant_contact_client` (read credential paths, `allow_token_refresh`).
4. `load_cc_data` (read lists/contacts, drop deleted, `link_cc_data`).
5. `resolve_desired_state`, `parishsoft_members_by_email`, and
   `validate_configured_parishsoft_workgroups`.
6. `filter_unsubscribed` (in-place desired mutation + report tuples).
7. Guards: `validate_non_empty_desired_state`, `removal_guard_tripped` /
   `validate_large_removal_guard`.
8. Action computation: `compute_create_actions`,
   `compute_subscribe_unsubscribe_actions`, `name_update_candidate_sync_indices`
   / `name_update_candidate_emails`, `detect_name_mismatches`,
   `compute_all_actions`.
9. Apply: `post_body_for_actions`, `put_body_for_actions`, `execute_actions`
   (group by email, POST vs PUT, fold subscribe ids into PUT, dry-run skip).
10. Notifications: `sync_notifications_will_send`, `send_notifications`,
    `build_notification_email`, count/label/sort helpers.
11. Scheduled report: `unsubscribed_report_decision`, state I/O + lock helpers,
    `validate_unsubscribed_report_config` / `_provider`,
    `ensure_unsubscribed_report_state_writable`, `unsubscribed_report_will_send`,
    `build_unsubscribed_report_email`, `send_unsubscribed_report`.
12. Orchestration: `main` (arg parse, `--version`, `--update-names`, seams) and
    `_run` (the ordered steps in [§6](#6-reconciliation-algorithm)).
13. Wrapper `scripts/pk-sync-ps-to-cc/pk-sync-ps-to-cc.py`, the
    `cli.sync_ps_to_cc_main` entry point, `example-config.yaml`, and operator
    README.
14. Tests mirroring [§12](#12-testing-notes).

## 14. Cross-references

- [Top-level system spec](../intro/spec.md) — especially
  [Shared CLI layer](../intro/spec.md#shared-cli-layer),
  [Configuration system](../intro/spec.md#configuration-system),
  [Logging and notifications](../intro/spec.md#logging-and-notifications),
  [Retry policy](../intro/spec.md#retry-policy),
  [Secrets and filesystem helpers](../intro/spec.md#secrets-and-filesystem-helpers),
  [ParishSoft data layer](../intro/spec.md#parishsoft-data-layer),
  [Constant Contact layer](../intro/spec.md#constant-contact-layer),
  [Email provider layer](../intro/spec.md#email-provider-layer), and
  [Dry-run and write safety](../intro/spec.md#dry-run-and-write-safety).
- [`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) — the ParishSoft
  v2 surface read here and why workgroup membership is read-only.
- [`pk-sync-ps-to-ggroup`](../pk-sync-ps-to-ggroup/spec.md) — sibling
  membership sync sharing the reconciliation shape.
- Operator guide: `scripts/pk-sync-ps-to-cc/README.md`; annotated config:
  `scripts/pk-sync-ps-to-cc/example-config.yaml`.
