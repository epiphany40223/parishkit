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
Failed-task retry also owns an unsent pending message whose preparation Task
exhausted its budget. That row retains its occurrence and failure journal for
an explicit linked retry; it is not silently skipped or automatically resent.
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
- Shared temporary token/connection/handshake failures retain a definite-unsent
  retry outcome and impose a 60-second process-wide new-send cooldown. Three
  consecutive shared outages stop that sending run and log CRITICAL. An
  observed healthy provider result resets the consecutive-failure count; local
  unobserved outcomes do not. No result can undo an existing halt. Deterministic
  shared TLS/configuration/protocol faults
  stop immediately. Restart resets this process-owned circuit; BG-10 owns
  durable operational escalation. Already-submitted outcomes can always drain.
- Delivery certainty and provider health are separate closed values in the
  private IPC and SQL evidence. A DATA connection fault can be both uncertain
  delivery and an unhealthy provider; it never becomes a safe resend. Earlier
  exact RCPT refusals survive a subsequent shared fault. Temporary TLS EOF,
  closed-connection and syscall failures use cooldown; certificate/protocol
  faults stop the run. Local helper-launch failures also use shared cooldown.
- Without SMTPUTF8, an unsupported Family address fails only that Family's
  message, with no fabricated RCPT refusal. The adapter does not silently remove
  a configured recipient to deliver an ASCII subset. The next Admin resolution
  increment owns retry after correcting the address or provider capability.
  Unsupported shared sender/Reply-To headers instead stop the sending run.

Implementation and local correction validation of this dispatch boundary are
complete; protected PR delivery is pending. All checks use synthetic
providers and owned disposable databases; existing development databases are
untouched. The follow-on Admin workflows and Gate 3 remain open.

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
including the 12 below Pika's displayed cutoff. This completes round 1; the
High finding was fixed and subsequent independent rounds remain required.
Exact-head validation receipts are listed separately below.

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
| Codex 4 | Low | Rejected: RFC 5321 section 4.3.2 lists RCPT success as 250/251; 252 belongs to VRFY/EXPN. No DATA is sent for an unexpected RCPT response. Round 2 further classifies it as a bounded per-message retry, not a shared halt. |

The SMTP classifications and Unicode serialization were checked against
[RFC 5321 command/reply sequences](https://www.rfc-editor.org/rfc/rfc5321.html#section-4.3.2)
and [Python email policy](https://docs.python.org/3/library/email.policy.html#email.policy.Policy.cte_type).

Round 2, Pika `20260916-102413-01fd55`, reviewed corrections from `92db222`
through `be5bbc1c3faf5f44d8da13b329fe28518e5b5175` (tree
`98843fa5e744aac3b9174d3488aefe747a3c9c53`). Exact-path permission preflight
passed; Claude and Codex completed without degradation. All 13 raw findings
(six Medium and seven Low, no High) are dispositioned below. The read-only
Codex reviewer could not run pytest without writable temporary storage; the
independent validation runs below supply executable evidence.

| Source/order | Raw severity | Disposition |
| --- | --- | --- |
| Claude 1 | Medium | Fixed with Codex 2: distinct durable-submission and helper-launch flags retain definite non-acceptance for a pre-launch clock/settings failure; real-worker regression. |
| Claude 2 | Medium | Fixed: shared settings/credential/invocation validation failures are systemic; only a message's transport-size overflow is per-message permanent failure. |
| Claude 3 | Medium | Fixed: explicit shared-unavailable result, 60-second circuit cooldown and three-consecutive-outage run halt; deterministic TLS/protocol failures halt immediately. Durable evidence and actual-worker admission tests cover the new outcome. |
| Claude 4 | Low | Clarified ownership: the following Admin-resolution increment owns explicit linked retry of failed preparation Tasks and their retained pending messages. An unsent crash is not fabricated provider failure or silent cancellation. |
| Claude 5 | Low | Fixed: unexpected RCPT replies are definitely unsent bounded retries without invented address-refusal evidence. |
| Claude 6 | Low | Retained and documented the one-Family-envelope choice: never silently omit a configured recipient. Added the missing unsupported shared-header regression. |
| Claude 7 | Low | Fixed with Codex 1: real crash-after-hold and hold-followed-by-failure tests verify immutable phase accounting and claim reset. |
| Claude 8 | Low | Fixed: negative mutation probes assert SQLSTATE 23514 only for the compiled MAIL outbox write, otherwise 42501. |
| Claude 9 | Low | Fixed: validation receipts below name their exact heads and distinguish failed diagnostics from passing full coverage. |
| Claude 10 | Low | Fixed: rewrapped the paragraph consistently. |
| Codex 1 | Medium | Fixed: reconciliation-phase recovery_retry events count as held attempts, alongside ordinary retryable_failure holds. |
| Codex 2 | Medium | Duplicate of Claude 1; same launch-certainty fix and real-worker clock-failure regression. |
| Codex 3 | Medium | Fixed: explicit TLS, response-bearing exception and malformed-reply classification; observed protocol outcomes survive a failing QUIT. |

Round 3, Pika `20260916-104656-947776`, reviewed `be5bbc1` through
`b1b222babe7e7fa2d05be8c17d32361e5f6becd4` (tree
`ecb6f96b896fd72e3ea003931c60bde0d15081b2`), broadening to unchanged dispatch,
process ownership and SQL contracts. Exact-path permission preflight and both
reviewers completed without degradation or verdict mismatch. All eight raw
findings (four Medium and four Low; no High/Critical) are dispositioned here.
Corrections and their validation belong to this third round under the
[delivery-cycle contract](../plans/stewardship/overall.md#automated-phase-delivery-cycle).

| Source/order | Raw severity | Disposition |
| --- | --- | --- |
| Claude 1 | Medium | Fixed: explicit unobserved health never resets the circuit; helper-launch failures and pre-launch shared failures participate in cooldown. Delivery certainty remains independently preserved. |
| Claude 2 | Medium | Fixed: typed TLS EOF/closed/syscall interruptions are temporary; certificate and unclassified protocol faults are systemic. Bounded inspection handles requests/urllib3 wrappers without parsing provider prose. |
| Claude 3 | Low | Duplicate of Codex 1; shared RCPT exceptions retain prior indices and affect the circuit. |
| Claude 4 | Low | Rejected: a shared RCPT-stage failure may follow a genuine earlier RCPT 550. Neither Python nor SQL should discard that independent refusal. Added real database cases for unavailable/systemic results with an earlier permanent refusal. Unobserved outcomes, unlike shared faults, now explicitly forbid recipient evidence in both decoders. |
| Claude 5 | Low | Fixed: DeliveryCircuit.blocks_new_send names the cooldown/halt behavior explicitly at admission call sites. |
| Claude 6 | Low | Fixed: parametrized MAIL/RCPT/DATA fault tests, wrapped TLS tests, local-only circuit behavior and real unknown-delivery/systemic-halt worker coverage. |
| Codex 1 | Medium | Fixed: MAIL and RCPT connection exceptions use the shared classifier without losing earlier refusal indices; unexpected numeric RCPT replies remain per-message bounded retries. |
| Codex 2 | Medium | Fixed: UNKNOWN retains uncertain delivery while a separate closed health value triggers cooldown/halt. SQL rejects forged health/status combinations; observed acceptance survives QUIT. |

The typed TLS distinctions follow the
[Python SSL exception contract](https://docs.python.org/3.12/library/ssl.html#exceptions)
and [urllib3's wrapped-error contract](https://urllib3.readthedocs.io/en/stable/reference/urllib3.exceptions.html#urllib3.exceptions.MaxRetryError).

## Validation receipts

- Initial head `92db222`: image build, 12 container-isolation checks, 17
  runtime/provisioning/ingress/broker checks and one fake-backed configured
  Compose startup passed. The initial full database run failed five outdated
  grant assertions among 3,125 cases; its 5,888-pass baseline is diagnostic
  evidence only, not a passing full-run receipt.
- Round-1 correction head `be5bbc1`: 112 provider/private-transport tests, 73
  affected database cases and a separate 59-case dispatch batch passed. The
  full credential-free baseline passed 5,908 tests. All 3,134 database cases
  passed across eight shards, and verified combined stewardship coverage was
  93.99% lines / 85.20% branches. Ruff check/format and tracked Markdown passed.
- Round-2 corrections: 97 provider/private-transport/circuit tests, 90 affected
  real-database cases and 5,924 credential-free baseline tests passed. Ruff
  check/format and guide Markdown passed. Full final-head validation remains
  pending. The independent empty-database schema comparison was repeated after
  adding the shared-unavailable result;
  the reference still matches PR #38 and the same 401-function/409-trigger
  catalog surface remains, with only the result decoder's body changed.
- Reviewed head `b1b222b`: image build, 12 isolation checks, 17 runtime/
  provisioning/ingress/broker checks and the synthetic configured Compose
  startup passed. Its full local database rerun passed 3,140 cases but failed
  one performance assertion under eight-way local contention (lookup p95
  2.78 seconds, limit 2 seconds). The unchanged performance test passed alone:
  5,000-Family lookup p95 0.021 seconds and 100-session page p95 0.038 seconds.
  This failed full rerun is diagnostic evidence, not passing combined coverage.
- Round-3 corrections: 125 provider/private/circuit cases pass. A new independent
  fresh-schema audit again matched the PR #38 reference and updated only the
  current result-decoder fingerprint. All 90 affected database cases and 5,952
  credential-free baseline tests pass, as do Ruff check/format, all tracked
  Markdown and the model-drift check. All three review/fix rounds are complete,
  with no accepted Medium-or-higher issue unresolved and no High/Critical in
  the final round. Final exact-head CI must pass before protected merge.
