# Phase 1C review ledger

This ledger follows the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [Phase 1C scope](stewardship-phase-1c.md). Review outputs remain private local
artifacts, not repository content. Review completion does not release Gate 1;
human approval remains required before Phase 2.

## Round one

Reviewed implementation: `abf407720a5ccf52ead21813381dbe08b8996aed`.
Base: PR #20 merge `18a37cb5b6c90bbf2b5f60c5fff37f199cd52201`.
Local session: `20260910-225925-d0301e`.
Five Pika-generated Claude shards and Pika's single Codex reviewer completed;
finalization has no failed agents, degradation or verdict mismatch. The slowest
Claude shard completed in approximately 32 minutes, within the approved window.

Raw severities: 3 High, 22 Medium, 62 Low. Pika's Medium cutoff retained
25 findings: 21 Claude-only and 4 Codex-only. The Low observations were not
retained as actionable findings. The result is **request changes**, not approval.

Corrections and post-fix validation are complete for this round: 23 findings
corrected, one duplicate and one unsupported premise rejected. The following
dispositions record the evidence-backed technical decisions.
Identifiers preserve each vendor's finalized order, including duplicates.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| C1 | High | Recovery preview indexes roles on Ministry assignments | Corrected: restrict Admin listing to address rules; assignment records now exercise every preview case. |
| C2 | Medium | Database-provision missing from offline mount matrix | Corrected: actual distinct operator password and all-read-only mount matrix. |
| C3 | Medium | Response helper ignores operational download pool | Corrected: resolve the startup-admitted pool; no ordinary-web-login fallback; missing admission returns 503 before private reads. |
| C4 | Medium | Migration safety branches lack focused evidence | Added direct refusal tests and real Compose configured-upgrade hold plus unchanged-capacity retry verification. |
| C5 | Medium | Operator profile missing from topology assertions | Corrected: database-provision explicitly remains profile-gated with no restart policy. |
| C6 | Medium | Runtime SQL connection cap not compared with process budget | Corrected: online/offline/migration admission checks actual role cap. Health-thread headroom is explicitly budgeted. |
| C7 | Medium | Closed grant registry lacks fast tests | Added model/SQL-table inventory, supported/unsupported identities, narrow download, role-cap and column-grant tests. |
| C8 | Medium | Failed metrics reuse query can retain valid=True | Corrected: every validation exception leaves the candidate invalid; PostgreSQL regression proves no replacement after a transient lookup failure. |
| C9 | Medium | Renderer admits unsupported replicas | Corrected: renderer, provisioner and cohort confirmation all require one operational web container. |
| C10 | Medium | Budget admits timeouts rejected by read limits | Corrected: validate fixed lock/interactive floors before provisioning; boundary regressions added. |
| C11 | Medium | Additive grants silently retain obsolete broader access | Corrected: inspect existing table/column grants and refuse excess; never revoke or repair unrelated authority automatically. |
| C12 | Medium | Bootstrap RLS visibility lacks non-superuser proof | Added actual schema-owner visibility and bootstrap rejection with foreign credential history in a disposable container, plus unknown-RLS refusal. |
| C13 | Medium | Mutable SQL password-reference map | Corrected: copy into a read-only mapping, including dataclass replacements; serializer avoids deep-copying mapping proxies. Hashability is not claimed: other existing mapping fields were already unhashable. |
| C14 | Medium | Download role inherits unrelated web reads | Corrected: separate closed streaming/authorization registry excludes credential/link/audit/queue inventories. Later report owners add their data explicitly. |
| C15 | Medium | Service configuration can overlap writable stores | Corrected: validate path/aliases/containment, with only the intended service-metadata directory exception. |
| C16 | Medium | Partial initial provisioning marker strands retry | Corrected: lock before writing; resume an exact empty/prefix intent only when no side effects exist; fsync completion before creating other artifacts. |
| C17 | Medium | Monitoring can exhaust request threads | Corrected: bounded single-flight observations with short cache/deadline and SQL statement/lock timeouts; at most two observation threads per process, included in connection budgets. |
| C18 | Medium | Proxy lease refusal lacks diagnosis | Corrected diagnostic. Retain immediate refusal: a maintenance exclusion must not quietly turn into delayed ingress activation. Production restart behavior is handled by X1. |
| C19 | Medium | Credential override can overlap Valkey ACL/shared parent | Corrected reserved ACL directory; dedicated owner-only closed parent inventory addresses shared authority. Independent-root overrides remain supported as required, rather than imposing the suggested root-only restriction. |
| C20 | Medium | Alternate migration owner can hide RLS rows | Rejected as unsupported premise: operational migration requires exact current/session identity pk_stewardship_migration, and role names are not configurable. Actual non-superuser owner/RLS regression verifies the supported path. Restore ownership remains OPS-06, not an alternate bootstrap entry point. |
| C21 | Medium | Long-lived production services do not restart | Duplicate of X1; same correction and evidence. |
| X1 | High | Production crash/daemon recovery missing | Corrected: long-running production services use unless-stopped; offline commands retain no restart. Added actual synthetic web-supervisor crash/restart proof. |
| X2 | High | File override lends installer its shared parent | Corrected with dedicated private-directory and closed protocol-file admission during rendering and actual online startup. Symlinks, hardlinks, subdirectories and unrelated files are refused. |
| X3 | Medium | Provisioning retry adopts unexpected storage | Corrected: revalidate the complete closed destination inventory on every retry, preserving/refusing injected files. |
| X4 | Medium | Installers/Valkey/Caddy lack health checks | Corrected: PID/start-time/recent-loop heartbeat, authenticated-default-denial broker liveness, and bounded local proxy socket checks. These are liveness, not external-provider readiness. |

Docker restart policy and health-check behavior follow the
[Compose service reference](https://docs.docker.com/reference/compose-file/services/).
No Docker daemon restart is performed on the developer's shared environment.

Post-fix evidence: 2,623 baseline tests and 940 PostgreSQL tests pass; scoped
coverage is 91.24% lines and 82.75% branches. The rebuilt image passes all 47
combined operational Compose, native provisioning, existing Compose and isolation
checks, including synthetic web-supervisor crash/restart and restricted-owner RLS
visibility. All 66 browser checks pass. Ruff, formatting, Markdown, migration drift
and diff whitespace checks pass. This completes round one, not the three-round
exit or Gate 1. Image digest: `sha256:7ba3f6008efc9d73d754928ef40ab1afe6b73e66d403c409a4eab2fd9e34b632`.

## Integrated Gate 1 review scope

Rounds two and three must review the cumulative Phase 1 diff from Phase 0's
merged handoff `509245d997c9b278f47b5c7630894ddc7b38650b`, including already
merged Phase 1A/1B work. Round one alone is not the complete integrated gate
review. The gate remains pending until those reviews, corrections, demonstrations
and final validation pass and the human approves release.

## Round two: integrated review corrections

Reviewed implementation: `15a82b0985212112f00cb9b8a6519086d9808828`.
The cumulative base is the Phase 0 handoff above. Session
`20260910-235922-283abd` completed all 31 Pika-generated Claude shards and the
single Pika-launched Codex reviewer in approximately 18 minutes, with no failed
agents, degradation, verdict mismatch or salvage requirement. Raw severity
counts are 3 High, 53 Medium and 329 Low; the severity/confidence cutoff retained
55 findings (54 Claude-only, one Codex-only). The result requests changes.

The unchanged reviewed commit passes 2,623 baseline tests, 940 PostgreSQL tests,
48 real-container/Compose checks and 66 browser checks. Scoped coverage is
91.29% lines and 83.40% branches. Ruff, format, Markdown and migration checks pass.
These are pre-correction results, not round-two completion evidence.

Correction checkpoints, with identifiers retaining finalized vendor order:

- C1/C8: translate the operator OAuth document into allauth's actual secret
  field; add library-consumer and composed-runtime probes.
- C2: add the exact missing EXISTS/DEL ACL commands; the composed probe exercises
  limiter operations and rejects unrelated keys/administrative commands.
- C3/C14: exercise restricted-role activation with a current campaign/schedule;
  add boundary UPDATE and TaskRun SELECT required by its invoker triggers.
  Exceptional reopen remains refused without its later ADM-06 owner; unrelated
  future transition authority is not granted preemptively.
- Related restricted-web tests expose and correct missing Family epoch/token
  row-lock and activity/dirty-trigger column privileges. Code/token login and
  the authenticated page pass with the actual closed web registry.
- C4/C5/C10: strengthen same-registry excess-grant tests, real diagnostics
  lease/admission/cleanup branches and production Gunicorn option coverage.
- C6: ordinary configured routes skip duplicate gate authentication and leave
  expiry/revocation/rotation to their owning view. Setup/maintenance routes keep
  full authentication because their placeholders have no independent identity
  owner. A read-only precheck was tested and rejected: it incorrectly prevented
  expired-session revocation and privilege-change cookie rotation.
- C9/C28: malformed query input receives Invalid request; server-side formatting
  failures receive private retry responses and failed terminal audit evidence.
- C11/C15: multipart activity input is a 400; Family exchange reuses only the
  configuration scope freshly resolved under its credential-population locks.
- C13: invalidate and clean a real testing Family session and its protected
  Django parent, checking the independent bounded cleanup counts.
- C16/C36: current source-promotion attribution reaches eligibility history;
  callers cannot supply the allocator's history-suppression placeholder.
- C17: give the invalidated-rehearsal retention exception a distinct forward
  migration identity and an explicit conformance-test exception.
- C18: login/grant resolution shares normalization and rejects ignored targets.
- C19/C29: historical reverse-function rebuilds restate trusted search paths;
  partial-downgrade regressions check the complete retained pin inventory.
- C20: the online queue excludes interrupted offline-owned recovery intents.
- C25/C26: session-disappearance races and non-owning sealed-request expiry fail
  with their typed denial before durable mutation.
- C27: response teardown failure still records exactly one failed terminal audit.
- C31: failed post-commit counter cleanup cannot discard the new session cookie.
- C33: protected diagnostics report ordinary offline maintenance distinctly.
- C23/C49: bound both durable observation/incident lock waits. Keep cross-worker
  recovery observation: a worker that did not see the original outage must
  still resolve its durable incident after recovery.
- X1: add the new synthetic runtime modules to CI and release validation;
  the exact validation command fails on any skip. Final validation remains pending.

Remaining finalized findings and dispositions:

- C7/C40/C45: lock only mutable campaign rows, explicitly pin runtime/epoch
  before campaign/generation inputs, and retain the consistent restore/lifecycle
  order. Real independent-connection NOWAIT tests cover preparation, verification
  and cancellation; the actual web role exercises campaign/projection reads.
- C12: retain the later BG-02 token-issuance owner. Web deliberately has no token
  INSERT authority, so granting it the suggested issuance-trigger privileges
  would not authorize that workflow and would expand its scope. Corrected the
  actual code/token-login row-lock and activity grants instead, with real-role
  HTTP coverage; issuance keeps its owning-service admission requirement.
- C21/C22: assert exact Linux proc/PID identity and hyphenated service override
  names in regression fixtures.
- C24: fetch full canonical history/projections in batches of 64; keep complete
  lineage verification, compact ancestry metadata and parsing outside the writer
  lock. Query-growth tests span three batches, without a trust watermark or
  arbitrary history cutoff.
- C30: cancellation has a transaction-bound admission callback, rechecked even
  on exact retries. False/absent confirmation from a supplied callback denies.
- C32/C42: forward SQL guards reject stale sealed-request authentication at
  intake and refuse sealed payloads attached to legacy empty-consumer requests.
- C34: selected installer failures carry their durable request correlation and
  closed diagnostic category; no exception text, path or provider value is logged.
- C35: real Chromium/Firefox/WebKit tests verify CSP blocks unrelated form
  destinations. The test must use a function rather than string evaluation to
  avoid violating the policy itself.
- C37: competing restore decisions return a typed stale conflict; identical
  retry intent still returns its original receipt without new evidence.
- C38: omit unknown dependency metrics while readiness continues to fail closed.
- C39: reject regressing pause/resume timestamps in SQL. Reject the suggested
  campaign-start lower bound: pre-start scheduled Production pauses are expressly
  supported and already tested.
- C41/C43: reject false premises, proven by actual production-shaped Compose
  tests: runtime settings already admitted loopback probes, and pinned Caddy
  redirects to the standard public HTTPS port, not its internal 8443 listener.
  Docker restart policy is not a health-triggered restart mechanism.
- C44: reject after the existing real-SQL bootstrap regression disproved the
  premise. Initial singleton INSERT is empty, then atomic activation UPDATE
  creates the campaign and selects its pointer. A complete legacy root therefore
  cannot leave the alleged dangling projection. Operational bootstrap still
  generates only its minimal login-rule document. The proposed extra restriction
  was removed rather than breaking the supported complete-root storage path.
- C46: preserve pinned Caddy's default untrusted-client XFF normalization;
  add forged multi-hop XFF to the real TLS ingress regression rather than
  changing the application to trust arbitrary chains.
- C47: a metrics receipt-reuse SQL outage remains retryable in Testing state,
  preserves the prior file and cannot be mistaken for candidate invalidity.
- C48: document the intentionally isolated, fixed container-tmpfs/PID namespace.
  Reject the unsupported shared-host-process premise; these transient evidence
  files are not durable deployment-root stores and must never be host-shared.
- C50/C51: refuse unverified external SQL connections and restrict loopback Host
  exceptions to admitted internal probes. Canonical application origin remains
  mandatory; private Compose SQL and isolated literal-loopback tests are supported.
- C52/C54: owning-service admission errors and restore-held boundaries remain
  retryable instead of terminally rejecting intent or reporting storage corruption.
- C53: reject the proposed success prerequisite for coalesced coverage. The
  background-processing/data specs require transactional coverage when assigning
  a replacement, preventing duplicate rearming across schedule revisions. That
  receipt is not provider success; dependent-resolution consumers still inspect
  the replacement outcome under their later owning task contracts.

Initial correction runs pass 46 focused runtime tests, 35 grant/limiter-contract
tests, and 97 PostgreSQL authentication/response/recovery/incident cases.
Further findings, migration tests, composed validation and the complete
post-correction suite remain in progress. This does not complete round two or
the Gate 1 review cycle.

Final correction checkpoint: all 55 retained findings have dispositions above;
none require a new product decision. The complete regression run exposed the
C6 session-precheck regression, C9 HTML-error compatibility, C34 SQL event
vocabulary and C44 false premise. Those are corrected; all 192 affected
PostgreSQL cases now pass. The latest baseline passes 2,658 tests; all 69 browser
checks and two complete 48-check container runs pass. The final rebuilt image
and complete PostgreSQL/coverage repeat remain required, and run alongside the
third independent review. No gate release is implied by these corrections.
