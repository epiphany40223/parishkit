# Stewardship isolated Family mail dispatch

## Scope and delivery boundary

Branch `pr/stewardship-family-mail-dispatch` starts at verified PR #38 merge
`72787c28d9108fee0c48e570e09b167d13e9f860`. It continues
[BG-06](../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders)
after [personalized preparation](stewardship-family-mail-preparation.md).

This increment connects a prepared invitation/reminder to the isolated MAIL
consumer, including current-source rendering, private-token resolution, final
admission, provider submission, recipient-specific outcomes, bounded retries,
pause/resume and crash reconciliation. It does not permit live-provider smoke
tests or Production activation before the controlling gate.

The following coherent increment owns the Admin delivery-resolution and
verified-refusal-clear workflows: external-evidence resolution, explicit
duplicate-risk resend, failed-message retry and durable portal notification.
Those actions remain unavailable, never inferred from an uncertain SMTP
acknowledgement. The full BG-06 checklist stays open until that integration and
its acceptance tests are complete. BG-10 retains operational escalation;
ADM-06 retains the broader campaign-control UI. Gate 3 remains closed.

## Implementation checkpoints

- SMTP uses one Family envelope, shared MIME construction and the configured
  Workspace identity. MAIL/RCPT and DATA are separated so a definitive refusal
  is distinguishable from lost acceptance. A stable Message-ID is correlation
  only; this provider has no assumed idempotent-send or status-query contract.
- A finite private helper receives credentials and resolved content through
  anonymous pipes. It emits only a closed result and recipient positions,
  never addresses, provider prose, tokens or keys. Missing/malformed/late
  acknowledgements remain uncertain.
- Per-address outcomes are recorded in the existing immutable outbox event's
  bounded evidence, bound to its numbered attempt and exact rendering. A
  permanently refused address can be suppressed independently of accepted,
  retryable or uncertain DATA outcomes. Transient refusals do not suppress an
  address, and partial acceptance does not resend to the accepted recipients.
- Current source, recipient selection, content and credentials are rechecked at
  the locked submission boundary. The mail process has no general-code key;
  it opens the prepared code/reference and decrypts only the corresponding
  retained token. Generation changes remain with restore/reopen ownership.
- Observed outcomes settle independently of subsequent lifecycle/configuration
  changes. Terminal outbox transitions scrub message substitutions; uncertainty
  retains them and blocks automatic resend. Task abandonment plus elapsed
  provider deadline is uncertainty, not proof of non-acceptance.

Implementation and acceptance coverage are in progress. Final validation and
the required review cycle are not complete. All checks use synthetic providers and owned
disposable databases; existing development databases are untouched.

## Fresh-install schema evidence

Independent empty PostgreSQL 18.6 databases installed the exact PR #38 baseline
and the current schema. The former matched its committed catalog fingerprint.
The latter adds four dispatch/result functions and six triggers, changes the
occurrence guard, refusal guard, refusal-effect function and scheduling work
view, and removes nothing. Columns, constraints, indexes and policies are
unchanged. The updated baseline records 401 functions and 409 triggers; it is
not an upgrade path or a promise of development-database compatibility.
The committed [catalog fingerprint](../../tests/stewardship/database/schema-baseline.json)
and [fresh-schema test](../../tests/stewardship/database/test_schema_baseline_postgresql.py)
enforce this locally captured evidence independently of model declarations.

The refusal-effect trigger uses a fixed search path and owner privileges only
to derive deliverability from validated immutable refusal evidence. It has no
callable runtime/public entry point. MAIL has no general Family UPDATE grant;
the real-role permission tests check both boundaries.

## Review evidence

Round 1, Pika `20260916-095640-8749e8`, reviewed the full branch from
`72787c28d9108fee0c48e570e09b167d13e9f860` through
`92db22265b09d8e9ef4a20b00e757ea31fed70a8` (tree
`8b2453ab7dcb286ba6ce01aea992d8dabe5d53b5`). Exact-path permission preflight
passed; both Claude shards and Codex completed without degradation. There were
20 raw findings: one High, seven Medium and 12 Low. All are dispositioned here,
including the 12 below Pika's displayed cutoff. Post-fix validation passed:
112 provider/private-transport tests, 73 affected database cases, a final
59-case dispatch database batch, and 5,908 credential-free baseline tests.
Ruff check/format and this guide's Markdown lint also passed. This completes
round 1; the High finding was fixed and subsequent independent rounds remain
required.

| Source/order | Raw severity | Disposition |
| --- | --- | --- |
| Claude 1/1 | High | Fixed: metadata-only admission can never commit SUBMIT after resume; real-worker race regression. |
| Claude 1/2 | Medium | Fixed for in-flight holds: typed holds retain journaled reconciliation retries outside the failure budget. Pre-claim holds were already excluded by admission. |
| Claude 1/3 | Medium | Fixed known pre-launch validation and elapsed-budget outcomes. Lost ownership remains fatal, not a fabricated receipt: that exception is a BaseException and never entered the claimed generic handler. |
| Claude 1/4 | Medium | Fixed per-Family SMTPUTF8/malformed-recipient classification; only shared provider/configuration faults halt the current worker run. Restart resets that process-owned halt. |
| Claude 1/5 | Low | Fixed: recovery requires exactly one optimistic occurrence update and rolls back on mismatch. |
| Claude 1/6 | Low | Fixed defensively: absent population is a typed hold in either mode. |
| Claude 1/7 | Low | Retained safety/performance trade-off: each private-helper poll rechecks ownership and releases its database connection. The shared finite transport uses this pattern; pooling/throttling must preserve those fences and can be measured with BG-10 operational work. |
| Claude 1/8 | Medium | Fixed: added real-worker regression coverage for resume, credentials/configuration, launch deadline, admission holds, crash budget and actual systemic halt. |
| Claude 1/9 | Low | Fixed: configuration identity is checked before SUBMIT, so an edited configuration no longer generates a fictitious SMTP transient attempt. |
| Claude 2/1 | Medium | Fixed with Codex 1: transient token/EHLO/AUTH/MAIL errors retry; definitive shared credential/configuration refusals remain systemic. |
| Claude 2/2 | Low | Fixed with Claude 1/4: unsupported Family addresses do not halt others or invent RCPT refusal evidence. |
| Claude 2/3 | Low | Duplicate of Claude 1/5; same checked-update fix and regression. |
| Claude 2/4 | Low | Retained conservative boundary: after helper launch a hard deadline with no receipt is unknown, even if the child might not have reached DATA. Inferring its protocol position would be unsafe. Per-operation budget optimization is not required for correctness. |
| Claude 2/5 | Low | Fixed: temporary handshake, token outage/refusal, Unicode body and address negotiation tests. |
| Claude 2/6 | Low | Fixed: removed duplicate refusal SELECT entry. |
| Claude 2/7 | Low | Clarified evidence links to the committed catalog fixture and real schema test; the independent local comparison was already performed and passed. |
| Codex 1 | Medium | Fixed with Claude 2/1: temporary MAIL refusal is definitely unsent and retryable. |
| Codex 2 | Medium | Fixed: abandoned unsent work honors the preparation failure budget, excluding journaled admission holds. |
| Codex 3 | Low | Fixed: Reply-To participates in SMTPUTF8 negotiation; seven-bit body encoding avoids unadvertised raw eight-bit content. |
| Codex 4 | Low | Rejected: RFC 5321 section 4.3.2 lists RCPT success as 250/251; 252 belongs to VRFY/EXPN. No DATA is sent for an unexpected RCPT response, and it now fails only that message rather than halting the worker. |

The SMTP classifications and Unicode serialization were checked against
[RFC 5321 command/reply sequences](https://www.rfc-editor.org/rfc/rfc5321.html#section-4.3.2)
and [Python email policy](https://docs.python.org/3/library/email.policy.html#email.policy.Policy.cte_type).

Round-1 correction checks so far: 112 provider/private-transport tests and 73
real database worker/affected permission tests passed. The first full eight-shard
run exercised all 3,125 database cases but failed five outdated assertions about
the newly compiled MAIL grant surface; all five now pass in the affected batch.
Its successful credential-free baseline had 5,888 passes. That failed full run
is diagnostic evidence only, not a passing coverage receipt. Image build,
12 container-isolation checks, 17 runtime/provisioning/ingress/broker checks and
one fake-backed configured Compose startup scenario passed on the initial head.
