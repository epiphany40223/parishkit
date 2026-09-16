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

Implementation is present; the three-review cycle is documented below, with
protected CI/merge still pending. Fresh-install baseline changes only; no upgrade compatibility
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
as passing evidence.

Post-correction checks so far: 35 real-database form/recovery/refusal tests,
42 pure command-validation tests, 41 storage/query-budget tests, and 36 browser
checks (including axe and 320/1280-pixel layouts across Chromium, Firefox and
WebKit) pass. Ruff, formatting, all tracked Markdown and migration-state checks
pass. The stricter model/schema comparison additionally found two new constraint
definitions with equivalent logic but nonidentical deparsed forms; the SQL now
matches the model without weakening the contract. A fresh independent comparison
after that correction retained all object counts and changed only the constraint
fingerprint to `79c7c444f7b7c1250bbd45511bcae9f9f301bf50f00754b2aff05f39873e9889`.

The full frozen-tree run at `7ada2ced960b258a93afc9182a1b8be216f714e0`
passed all 5,994 baseline tests and all 3,217 PostgreSQL tests across eight
isolated shards. The longest database shard took 687.52 seconds. The repository
`quality_ci combine` command verified complete, disjoint test accounting and
reported 93.97% line / 85.19% branch coverage, above both 80% requirements.
Local receipts and combined report are under
`/tmp/parishkit-resolution-quality.VQ9QPL`. This completes round 1; subsequent
corrections retain their separate validation rather than relabeling this tree.

## Review round 2

Pika session `20260916-131540-43efe0` reviewed the exact correction delta
`c43b5e9d48bc072b7c429fac3eb263b8132da662` to
`7ada2ced960b258a93afc9182a1b8be216f714e0`, with surrounding service, SQL,
session, rendering and specification context. Its fresh exact-path permission
probe passed. Both manifest reviewers completed normally; finalization retained
the artifacts with no degradation, mismatch, failed agent or salvage. Eleven
raw findings: one Medium, ten Low; no High or Critical. One finding passed the
finalizer cutoff. Both reviewers independently confirmed the isolated dispatch
rerender/credential boundary behind round 1's rejected High premise.

| Source/item | Raw severity | Disposition and evidence |
| --- | --- | --- |
| Claude 1: retry warning after acceptance | Medium | Fixed: show the warning only for retry-capable message states. Actual post-acceptance page rendering must not claim a delivered message needs retry recovery. |
| Claude 2: rollback can discard session rotation | Low | Fixed: explicitly retain the session row lock, perform non-mutating in-transaction authorization, and do timeout/revocation/rotation maintenance only after effect rollback. The replacement browser cookie must name committed session records. |
| Claude 3: lock retention lacks concurrent test | Low | Fixed: for each command type, a second restricted Web connection calls real logout and hits bounded SQL lock timeout while the effect owns its session; logout succeeds after commit without removing the command receipt. |
| Claude 4: repeated warning purpose filter | Low | Fixed: a single `PURPOSES` tuple supplies both the ORM polling query and the parameterized initial-page count. |
| Claude 5: clearance gate omits snapshot predicates | Low | Already handled by `stewardship_source_current_guard`: its pointer must identify a promoted, non-compacted snapshot with matching generation and organization. The UI reads that pointer; the command independently revalidates under its owning locks. No weaker command admission or speculative new definer was added. |
| Claude 6: SQL authorization denials share conflict response | Low | Intentional fail-closed boundary: Python's current Admin checks return 403; SQL state/version/admission rejection returns a fixed 409 without exposing SQL details. The source-impersonation branch is unreachable through the closed form. No write is accepted on either response. |
| Claude 7: eager and lazy preparation ambiguity | Low | Fixed: reject mixed input sources and non-callable lazy providers explicitly. Retain eager inputs for existing internal callers and lazy input for browser replay. |
| Claude 8: key loading under global lock | Low | False-positive premise: `family_authentication.runtime()` only retrieves the already constructed settings bundle; it reads no mounted files or network under the lock. |
| Claude 9: unused `values` binding | Low | Fixed by selecting the window directly. Restoring `_` as suggested would shadow the gettext function and break command labels. |
| Claude 10: unchanged live-region reannouncement | Low | Fixed: change warning text/visibility only when the value changes. A browser MutationObserver asserts that an identical poll causes no warning mutation. |
| Codex 1: retry warning after acceptance | Low | Duplicate of Claude 1; same correction and real POST/render regression. |

Round 2 corrections pass 40 command/session/view/recovery PostgreSQL tests and
12 additional refusal/query-budget tests, 44 pure command cases, and six
three-engine keyboard/polling browser cases. The 15 session cases were rerun
with additional actual `pk_admin` response-cookie and durable timeout-audit
assertions; all pass. Ruff, formatting, tracked Markdown and migration-state
checks pass. This completes round 2; the third independent review and protected
final-head CI remain required. No merge, deployment or Gate 3 release is implied.

## Review round 3

Pika session `20260916-133355-c55f80` reviewed
`7ada2ced960b258a93afc9182a1b8be216f714e0` through
`d592d47a1fa89f2a30034e224d50d7c9be4b7451`, with surrounding authorization,
transaction, SQL, dispatch and rendering context. Its fresh exact-path permission
probe passed. Both exact manifest reviewers completed with exit zero. Codex's
raw result is `APPROVED` with no findings; Claude reported five Low findings.
Finalization approved with no degradation, failed reviewer, mismatch or salvage.
No raw Medium, High or Critical finding was reported.

| Source/item | Raw severity | Disposition and evidence |
| --- | --- | --- |
| Claude 1: rotation test only covers pre-effect change | Low | Fixed: parameterize all three command types for before-effect and after-real-effect fingerprint changes; both assert effect rollback, actual committed `pk_admin` cookie, replacement session and one authority-change audit. |
| Claude 2: command still repeats purpose tuple | Low | Fixed: the command now uses the shared `PURPOSES` constant as well as both metadata readers. |
| Claude 3: retry-state mapping could be centralized | Low | Optional refactor not taken: the closed state vocabulary is consistent, and service admission and UI commands intentionally retain independently readable conditions. Per-action effect tests plus paused/accepted rendering regressions cover their consistency. No functional defect or new state is left unresolved. |
| Claude 4: maintenance failure supersedes denial | Low | Documented intentional fail-closed priority: if post-rollback session maintenance is unavailable, return fixed private 503, not a claim that current session authority was established. Added all-three-command regression coverage; no receipt commits. |
| Claude 5: concurrency diagnostic assumes exception cause | Low | Fixed: read SQLSTATE defensively. The probe invokes actual `end_admin`, whose sole lock is its selected PortalSession row; a second connection must block and then succeed after commit. Additional lock-catalog instrumentation is unnecessary for this concrete causal test. |

At the reviewed head, the standard baseline suite passes 5,996 tests (4,067
explicitly gated integration/browser/container skips and two existing warnings)
in 55.78 seconds. Those skips are not substituted for the separately recorded
real database/browser checks or required protected CI. Final round-three focused
validation passes 40 PostgreSQL session/resolution cases in 91.93 seconds and
44 pure input cases, plus Ruff, formatting, Markdown and whitespace checks.
There are no unresolved accepted Medium-or-higher findings. Round 3 is complete.
The existing three-round contract includes these
corrections within round 3 and does not demand a fourth finding-free review.
