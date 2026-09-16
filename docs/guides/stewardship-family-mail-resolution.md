# Family mail Admin resolution increment

Branch `pr/stewardship-family-mail-resolution` starts at the verified PR #39
merge, `9e5b6e99d1fbfd1c33e386a46525630e9dd73f0a`, on refreshed `origin/main`.
This is the next coherent increment under the
[controlling plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
and [BG-06 tasks](../tasks/stewardship/background-processing.md#bg-06-family-invitations-and-reminders).

## Scope and acceptance

Implement the [delivery-resolution contract](../specs/stewardship/background-processing/spec.md#family-invitations-and-reminders)
and its [Admin indicator](../specs/stewardship/admin-portal/spec.md#background-indicators):

- Admin-only searchable, paginated delivery metadata and private evidence notes;
  no message bodies, credential substitutions, or raw provider responses.
- Persistent unresolved-delivery warning and deduplicated WARNING audit.
- External-evidence acceptance, explicit duplicate-risk acknowledged resend,
  and retries of failed deliveries or failed unsent preparation tasks.
- Verified refusal clearance with immutable evidence, current source identity,
  atomic deliverability recalculation and the existing recovery-edge policy.
- Current authorization, CSRF, replay/version fences, safe lifecycle admission,
  immutable prior attempts and atomic outbox/occurrence/fulfillment effects.

SMTP has no contractual status-query or idempotent-send capability. The UI must
state this limitation; absence of a lookup result never establishes failure.
An external receipt may resolve old uncertainty after close, but cannot authorize
a new out-of-interval send. A cleared address cannot duplicate an initial
message already accepted for another head in the same Family.

## Internal checkpoints

1. Restricted metadata and verified-refusal clearance, with database tests.
2. Evidence-backed resolution and fresh-credential retry services, with ownership
   and race tests. These are internal checkpoints, not separate PR boundaries.
3. Responsive Admin UI and durable warning integration, including browser tests.
4. Integrated validation, three dual-source review/fix rounds, exact-head CI,
   protected queue, verified main ancestry, then the next dependency-ready work.

Implementation is present and integrated validation is in progress; no review
round is claimed complete. Fresh-install baseline changes only; no upgrade compatibility
or retained development-database deletion. BG-10 retains operational email/Slack
escalation, ADM-06 retains broader campaign controls, and Gate 3 remains closed.

## Implementation checkpoint

The Admin navigation now links to bounded Family-mail metadata, immutable attempt
history and private evidence notes. Every command requires a current Admin,
CSRF, an exact idempotency key and the observed delivery/source version.
Successful retries allocate a linked Task, rerender current source/settings and
seal current credential references without Web access to the private token key
or provider transport. Preparation, retry and dispatch share public rendering.
Prior attempts remain immutable; acceptance atomically fulfills the semantic
slot, and a duplicate-risk acknowledged resend preserves its unknown history.

`delivery_unknown` has a durable, deduplicated WARNING and persistent Admin
indicator, independent of Task status. Forms explain SMTP's lack of status-query
and idempotent-send guarantees. Refusal clearance requires verified evidence and
updates only current Family deliverability; other unresolved refusals continue
to suppress the address. A cleared address does not directly send mail. Failed
local-preparation Task pages expose replay-safe retry only for the latest run.

Campaign end and Production pause deny new retries but permit evidence-backed
confirmation of past delivery. Ordinary source, mode, epoch, restore, gate and
semantic-fulfillment checks remain in force. Error pages provide fixed recovery
links without echoing submitted private evidence.

## Fresh-install schema audit

Two newly created disposable databases independently installed the exact PR #39
baseline and this branch's current SQL. The reference fingerprints matched the
committed baseline before comparison. The audited delta adds one command table,
13 columns, 14 constraints, three indexes, five functions and four triggers;
no existing relation, column, index, trigger or policy was removed or changed.
Existing changes are limited to the operational-event and refusal-resolution
checks plus occurrence recovery, verified-refusal admission and deliverability
effect functions. The 28 policies are unchanged. Catalog fingerprints are
updated only after this comparison, not copied from a failing test.

The resulting function catalog has 406 entries, hash
`4a170c41803f992ef30c40d7cad56a1c983bd5a8afc3e0bc983b5b45560cc13e`.
The command trigger consumes and scrubs ephemeral preparation before persistence;
the Web role receives neither outbox UPDATE nor render/private-token SELECT.
No upgrade path or retained database was changed.

The local comparison command was `.venv/bin/python
/tmp/parishkit-family-resolution.BL98wu/audit-schema.py`, using fresh databases
`stewardship_resolution_base_20260916c` and
`stewardship_resolution_current_20260916c` on the owned disposable port 55440.
The temporary helper is local evidence, not a committed or permanent tool.
It installed the SQL file set from the exact base above and from the working
tree independently, checked the base inventory against that commit's fixture,
then compared the object dictionaries from `schema_inventory.inventory`.
The reproducible repository check for the resulting current baseline is
`tests/stewardship/database/test_schema_baseline_postgresql.py`, run with
`--ds=parishkit.stewardship.settings.database_test --require-postgresql-tests`
and explicitly configured disposable PostgreSQL/Valkey ports. Its fixture is
not generated from the test result.

## Review round 1

Pika session `20260916-124256-503707` reviewed the complete diff from
`9e5b6e99d1fbfd1c33e386a46525630e9dd73f0a` to
`c43b5e9d48bc072b7c429fac3eb263b8132da662`. The exact-path Claude permission
probe passed. Both manifest-selected Claude shards and the single Pika-launched
Codex completed; finalization retained the artifacts, with no degradation,
failed reviewer, verdict mismatch or salvage. Raw findings: one High, five
Medium, seventeen Low; five survived the finalizer's cutoff. All 23 are
dispositioned below, including those below that cutoff.

| Source/item | Raw severity | Disposition and evidence |
| --- | --- | --- |
| Claude 1.1: missing target treated as outage | Medium | Fixed: missing delivery/refusal/task becomes fixed 404; unavailable configuration remains 503. Real-session recovery tests cover GET and all command paths. |
| Claude 1.2: SQL denials treated as outages | Medium | Fixed: SQLSTATE 23514/23505 become reloadable 409; genuine database failures remain private 503. Refusal and retry forms consult current admission. |
| Claude 1.3: stale retry actions visible | Low | Fixed with 1.2; Production pause hides resend while keeping external-evidence acceptance. |
| Claude 1.4: misleading paginated emptiness | Low | Fixed: scoped empty-page wording and previous-page links preserve the bounded shared page window. |
| Claude 1.5: replay needlessly loads keys | Low | Fixed: lazy preparation inputs run only after replay/version/state checks; replay test makes the key loader fail if called. |
| Claude 1.6: partially applied polling response | Low | Fixed: validate the complete response before DOM updates; malformed-count browser regression preserves both indicators. |
| Claude 1.7: raw labels and inaccessible acceptance caveat | Low | Fixed: closed translated labels and the caveat's `aria-describedby` link, verified in real browsers. |
| Claude 1.8: weak activity assertions | Low | Fixed: dashboard activity strictly increases; list, detail and refusal reads do not extend idle expiry. |
| Claude 1.9: form test does not assert effects | Low | Fixed: actual Web POST tests assert versions, state, linked retry, fulfillment, audit, replay and stale 409. |
| Claude 1.10: unreproducible schema claim | Low | Fixed: exact local command/method and the persistent repository baseline check are documented above; the temporary helper is not presented as permanent evidence. |
| Claude 2.1: forged sealed context not exercised | Medium | Accepted test gap: real Web insertion of cross-context ciphertext is rejected by isolated dispatch before a new attempt. Existing key/inventory guards and dispatch identity binding remain authoritative. |
| Claude 2.2: unexplained two-event resend | Low | Fixed: SQL comment explains retained authorization, separate preparation event, unique command IDs and the two-version advance. |
| Claude 2.3: extra Admin count/query cost | Low | Fixed: combine independent indexed warning totals into one query, preserving immediate no-JavaScript warning visibility and the existing query budget. |
| Claude 2.4: copied grouped DUID fails search | Low | Fixed: accept strictly valid US-grouped or canonical positive bigint identifiers. Retain required US display formatting; reject malformed grouping and overflow. |
| Claude 2.5: raw list labels | Low | Duplicate of 1.7; the shared label filter covers both list and detail. |
| Claude 2.6: changing warning is not announced | Low | Fixed: always-present polite, atomic status region with browser assertions. |
| Claude 2.7: missing retry inputs fail deeply | Low | Fixed: explicit keyring/origin validation at preparation entry, after replay checks but before retry allocation. |
| Claude 2.8: `retry_unsent` naming | Low | Fixed without changing the durable action key: explain definitive non-acceptance in the UI and code; unknown acceptance still requires duplicate-risk consent. |
| Claude 2.9: redundant nonnegative constraint | Low | Already handled: retain Django PositiveBigIntegerField catalog parity plus the stricter positive-version constraint; the model/schema contract checks both. |
| Codex 1: Web content becomes phishing mail | High | Rejected delivery-authority premise with direct regression evidence: isolated `begin_submission` calls `current_content`, which authenticates retained context, resolves same-Family credentials, then independently re-renders current source/template/routing. Correctly resealed forged subject/HTML/text and a forged stored digest never become provider content. Pending rendering is not transport authority. |
| Codex 2: session check outside command transaction | Medium | Fixed: acquire current session under the work boundary, retain its lock through effects, and recheck expiry before commit. Nine real-session tests cover all three command types, revocation/expiry and evidence redaction. |
| Codex 3: private POST evidence in error reports | Medium | Fixed: both evidence endpoints mark `note` with Django sensitive-POST protection; unexpected exception reporting cannot expose it. |
| Codex 4: warning accessibility | Low | Duplicate of Claude 2.6; fixed in the shared always-present live region. |

The five adversarial render/context cases exercise actual restricted Web and
isolated dispatch roles; they are not mocks of the admission decision. No Web
transport permission, private-key mount, or unvalidated direct-send path was
added. An additional regression rejects Web impersonation of source-owned
refusal correction after the new INSERT grant.

The initial full eight-shard validation was not green: it found omitted Docker
SQL allowlists, stale Web-grant and immutable-guard inventory assertions, and
one extra query beyond the unchanged Admin budget. Those are corrected, without
weakening assertions or raising the budget. The first run's local artifacts
remain under `/tmp/parishkit-resolution-quality.PU6Xks`; no failed receipt counts
as passing evidence. Post-correction integration validation and the two further
required review rounds remain in progress.

Post-correction checks so far: 35 real-database form/recovery/refusal tests,
42 pure command-validation tests, 41 storage/query-budget tests, and 36 browser
checks (including axe and 320/1280-pixel layouts across Chromium, Firefox and
WebKit) pass. Ruff, formatting, all tracked Markdown and migration-state checks
pass. The stricter model/schema comparison additionally found two new constraint
definitions with equivalent logic but nonidentical deparsed forms; the SQL now
matches the model without weakening the contract. A fresh independent comparison
after that correction retained all object counts and changed only the constraint
fingerprint to `79c7c444f7b7c1250bbd45511bcae9f9f301bf50f00754b2aff05f39873e9889`.
