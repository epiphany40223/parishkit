# Phase 1B independent review evidence

This accompanies the [phase implementation checkpoint](stewardship-phase-1b.md)
and the [controlling review cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
It records decisions, not raw reviewer logs or private runtime artifacts.

## Round one

Reviewed HEAD: `324a10c56c3517b7a8e23da72f6cba0dc734af5d`.
Diff base: `6fd21eb586a635333be9f55fc7db9caa39284e60`.
Pika session: `20260910-155133-5d7497`.
Both vendors completed; Pika automatically split Claude's large-diff review into
eight sections. No failed agents, verdict mismatches or degraded review results
were reported. Finalization consolidated 47 findings: one High and 46 Medium.

The following IDs preserve the finalized bucket order: A is the agreed finding,
C is Claude-only, and X is Codex-only. All are Medium except X1 (High).
Routine decisions follow the user's delegated implementation/triage authority.
Corrections and applicable post-correction validation are complete. This is one
completed review-and-fix round, not authorization to create a PR yet.

| ID | Disposition | Evidence / correction |
| --- | --- | --- |
| A1 | Fixed | Invalid-link durable audit is globally sampled once per five minutes; per-attempt keyed telemetry remains in the ephemeral aggregate detector, including early link throttling. No attempted value/fingerprint enters the sampled audit. |
| C1 | Fixed | Disposable volume cleanup starts only after successful creation. Teardown preserves an existing test failure while retaining cleanup failures as failures when the test otherwise succeeded. |
| C2 | Fixed | Browser matrix now includes Admin home, code table/pagination, availability and denial; Admin timer/interaction never invokes Family keepalive. |
| C3 | Fixed | Cross-campaign MAC test matches the scope trigger's exact diagnostic. An unrelated FK error can no longer satisfy its assertion. |
| C4 | Fixed | Opaque-link throttling/unavailability uses the Family retry destination. |
| C5 | Fixed | New SQL/model checks pin rehearsal lookup and reservation MAC algorithm/digest formats. Raw invalid inserts have regression coverage. |
| C6 | Fixed | The shipped code report includes the shared table component rather than duplicating its markup. |
| C7 | Fixed | Weakened-threshold warnings use a typed operational event and closed field-name metadata; the test exercises the actual redacting JSON formatter. |
| C8 | Fixed | Failed health probes advance the process throttle in `finally`; ordinary counter scripts still enforce admission. |
| C9 | Rejected claim; clarified | RLS does not grant column SELECT. Actual web-role tests grant only staging metadata columns and prove ciphertext SELECT fails. Model documentation now explicitly distinguishes column grants and row policies. |
| C10 | Fixed | Bounded session cleanup performs bulk revocation and bulk audit insertion before metadata/parent deletion. |
| C11 | Fixed | Configuration-coherence failures during the Google callback return the same safe 503/retry contract as other entry points. |
| C12 | Fixed | Added report guard-entry revocation, restore routing, invalid-filter HTML and real page-window navigation regressions. |
| C13 | Fixed | Added a separate real Production-mode benchmark at 1 and 5,000 MAC-index rows plus 100 Production sessions; corrected the earlier rehearsal-only evidence. |
| C14 | Fixed | Recovery test uses a successful real-Valkey call from a new limiter instance, not a direct incident-resolution helper. |
| C15 | Fixed | Bounded code-page preparation executes inside the read guard before headers. Cryptographic failures precede the generic ValueError handler and return 503, not partial 200 or input-error 400. |
| C16 | Rejected as premature optimization | Rotation is an explicitly bounded background operation, not an interactive query. The reviewer identifies a possible large-history scan cost, not a failed parish-scale budget. Keep mandatory complete ciphertext verification; full mixed/retained-history load belongs to ARC-08/OPS-09. |
| C17 | Fixed | Same-generation unchanged reconciliation does not rewrite Family versions. A genuinely newer promoted generation still updates provenance as required. |
| C18 | Fixed | Successful throttled health observation resolves durable outage state across worker replacement; same-worker recovery also remains immediate on a successful script. |
| C19 | Fixed | Progress includes defend against absent/non-numeric counts, retain zero as a valid value, and have direct rendering tests. Shared components intentionally precede later report/job consumers. |
| C20 | Rejected as premature optimization | Gate and view admissions have different responsibilities; guarded private reports additionally reauthorize before decryption. The measured Admin shell remains under the 64-query / two-second budget. Do not substitute cached authority at irreversible-effect boundaries. |
| C21 | Rejected proposed removal | Family-level last activity is retained beyond individual session cleanup. Its one-row update is intentional; the population trigger changes coverage only if eligibility/source generation changes. Production page measurements remain within budget. |
| C22 | Rejected proposed lock removal | Token operations inspect campaign state/pointers and, for rotation, Family eligibility. Joined-row locks keep those facts stable through mutation. Removing them without replacement admission locks permits stale state; retain the coherent transaction. |
| C23 | Fixed | Internal sealed-error helper requires an explicit status and no longer resembles a generic Django handler4xx callback. |
| C24 | Fixed | Active-generation reconciliation validates its public token ring before allocating/writing the population. |
| C25 | Rejected speculative migration conflict | The migration owns its named guard and reverse operation. No later migration invokes the generic builder for this table; any such future replacement must intentionally replace/review the existing guard. There is no duplicate object or exposed deletion path today. |
| C26 | Fixed | Valkey INFO/marker reads occur before the SQL health transaction. A database-clock observation boundary discards snapshots older than a competing baseline change, avoiding false loss on overlapping initial probes. |
| C27 | Fixed | Exclusive key acquisition now uses nonblocking advisory admission and the same safe retry exception as shared acquisition. A separate-connection contention test bounds completion. |
| C28 | Rejected split-commit proposal | Family population and newly eligible links must promote atomically. Initial activation already prepares its complete manifest in bounded transactions. An artificial subsequent 4,999-Family arrival took 11.359 seconds locally; retain this as extreme-promotion evidence for DAT-03/ARC-08 integration, not as an ordinary-page latency result or a complete mixed-load approval. |
| C29 | Fixed | An absent policy epoch yields a safe retryable configuration denial before constructing an invalid zero-epoch counter. |
| C30 | Fixed | Population generation cannot regress even when no Family rows exist. |
| C31 | Fixed | Renamed the surviving token's historical replacement timestamp to `rotated_at`, preserving stored values via a rename migration. Only destruction makes the retained token unavailable. |
| C32 | Fixed in documentation | Target-service recovery replays the same request after restoring its actual identity/keys/journal. Missing or unknown file evidence requires consistent operator recovery, never clearing reservations or bypassing SQL state guards. |
| C33 | Rejected boundary conflation | Internal attribution-only primitives support offline/storage owners; they are not public authorization APIs. Online adapters always provide locked current-Admin/CSRF/setup/restore admission, including retries. No public configuration/secret endpoint bypasses them. |
| C34 | Rejected; regression added | Collision-only demotion already verifies active-key backfill for every retained Family/rehearsal code. Issuance therefore checks the active MAC for historical codes. Added a forced duplicate candidate after actual verified demotion. |
| C35 | Fixed | Resize to the largest supported output before the fresh pixel-buffer metadata scrub, avoiding multiple full-resolution RGBA copies. |
| C36 | Fixed | HTML report validation returns the accessible HTML denial/retry page; the JSON helper's contract now describes enhanced clients only. |
| C37 | Rejected unsafe weakening | A surviving canary cannot prove that other authentication counters survived eviction. Server-wide eviction/reset remains conservative evidence of possibly lost controls; ignoring it would silently accept counter loss. |
| C38 | Duplicate of X2 | Same database-clock signing-retirement correction and skew regression. |
| C39 | Duplicate of X3 | Same complete consumer-set intake/SQL correction. |
| C40 | Fixed | Deadline abort shuts down transport but leaves socket descriptor ownership/close to the WSGI server. |
| C41 | Fixed | Session namespace, throttling, setup/restore, privileged-action and internal-route checks use Django's routing `path_info`; SCRIPT_NAME cannot bypass them. |
| C42 | Fixed | Shared installer grant inspection covers all non-system schemas, relation/column grants, sequences, schema CREATE and executable SECURITY DEFINER functions. SECURITY INVOKER helpers retain the same restricted table authority. |
| C43 | Fixed | Django messages use the appropriate database session, never a root-scoped readable message cookie. |
| X1 | Fixed (High) | Request and verify signed Google `auth_time`, reject absent/invalid/future evidence, and preserve its age for privileged freshness. Ordinary SSO with an older provider session remains allowed without granting privileged freshness; the one-use nonce binds the callback. |
| X2 | Fixed | Signing-key retirement compares its last possible credential expiry against PostgreSQL time, not the application host clock. |
| X3 | Fixed | Sealed requests require the complete trusted target mount-consumer set in both Python and raw SQL. Subset acknowledgements cannot mark a multi-consumer replacement applied. |

Totals: 35 corrected findings, 10 evidence-backed rejected claims/proposals,
and two duplicates. Rejected performance proposals are not proof of final
production sizing: later source/report/backup and mixed-worker load gates remain
mandatory, and production startup is still disabled.

### Intermediate correction evidence

- Expanded browser suite: 66 passing cases across three engines.
- Focused pure formatter/presentation/threshold suite: 121 passing tests.
- Initial broad correction integration: 139 passing PostgreSQL tests.
- Additional targeted regressions: 54 passing PostgreSQL tests after correcting
  the cryptographic-error exception ordering.
- Real Production index: 21 lookup queries at both 1 and 5,000 Families;
  observed p95 0.0147 seconds. Family page with 100 live Production sessions:
  25 queries, observed p95 0.019 seconds. This is not 100 concurrent requests.

### Completed correction validation

The complete scoped run passes 2,251 ordinary tests and 831 PostgreSQL tests,
with 94.69% line and 84.98% branch coverage. The rebuilt image passes all
30 Compose checks (including 2,251 in-image tests) and all 12 container-isolation
checks. Browser validation passes 66 cases. Ruff, Markdown lint and migration
drift checks pass. After the full run, ordinary Google SSO was separated from
privileged freshness; all 49 Google/recovery/privileged-action regressions pass.
Final integrated validation will be repeated after subsequent review corrections.

Subsequent independent rounds remain pending.

## Round two

Reviewed HEAD: `6b4f720` (round-one corrections).
Diff base: `6fd21eb586a635333be9f55fc7db9caa39284e60`.
Pika session: `20260910-173315-e522ff`.
Both vendors completed, with nine automatically selected Claude sections. No
failed agents, mismatched verdicts or degraded results were reported. The
consolidated result contains 30 Medium findings and no High/Critical findings.
Pika excluded Low findings from this Medium+ correction pass. C is Claude-only;
X is Codex-only. All accepted findings are corrected and post-correction
integrated validation passes.

| ID | Disposition | Evidence / correction |
| --- | --- | --- |
| C1 | Fixed | SQL operational-event admission includes the new threshold-warning event; every Event member has a real persistence regression. |
| C2 | Fixed | Cancelled sealed requests reject legacy cleanup before invoking its deletion callback and retain cleanup-pending state. |
| C3 | Fixed | Signed callback tests reject invalid/stale/future initiation evidence and authentication later than token issuance. |
| C4 | Fixed | Internal Docker liveness retries individual exec timeouts within its overall deadline. |
| C5 | Fixed | The unmerged sealed-candidate SQL guard uses the keyring's exact key-ID grammar; a table-driven parity test covers both accepted and rejected IDs. |
| C6 | Fixed | Middleware tests preserve marked HTML/JSON denial bodies and continue sealing unmarked errors. |
| C7 | Fixed | Real installer roles cannot select another target's ciphertext; the backup-reader role has explicit ciphertext-read coverage. |
| C8 | Fixed | Anonymous OAuth sessions expire after fifteen minutes; existing authenticated sessions retain their fixed absolute deadline during reauthentication. |
| C9 | Fixed | Every secret target has a SQL-versus-mount-consumer vocabulary parity test. |
| C10 | Fixed | All new credential mutation callbacks require exactly True, matching cipher/retirement admission. False/None fail closed; fixtures and callback documentation follow that contract. Existing earlier lifecycle-owner protocols are not silently redefined. |
| C11 | Fixed | Exact UTF-8 expansion is computed before combined allocation; sanitized output is also re-bounded. |
| C12 | Out-of-phase; clarified | No production startup is enabled. OPS-03/OPS-04 own actual proxy topology and both trust settings; the module and handoff now explicitly identify that required assembly. A transport-only settings projection is not presented as production ingress admission. |
| C13 | Fixed | Module-autouse opt-in gating precedes HTTP, asset and browser fixtures; missing static assets have an explicit diagnostic. |
| C14 | Fixed | Health probes claim their process slot nonblockingly. Other callers still execute their atomic counters; contention has a regression. |
| C15 | Fixed | A SCRIPT_NAME request mutates its session and asserts the actual Admin Set-Cookie name/path, rather than merely absence of a Family cookie. |
| C16 | Clarified and refreshed | Historical checkpoint counts stay historical. Current task evidence reports the measured 66-case browser matrix and links to the latest review validation. |
| C17 | Fixed | Retirement opens its durable transaction before the owner's transaction-scoped SQL locks. A separate backend proves exclusion at owner-context exit and release only after commit. |
| C18 | Fixed | Real configuration and activation workflows prove unrelated campaign changes retain the rehearsal gate and pointer activation clears it with a version increment. |
| C19 | Fixed | Admin callback failures use bounded deployment-wide sampled audit, even beyond public rate limits. Per-attempt keyed accounting stays ephemeral. |
| C20 | Fixed | Distributed manual-code failures use the same bounded policy and retain their per-attempt ephemeral aggregate count. |
| C21 | Duplicate | Same manual-code failure audit finding as C20. |
| C22 | Fixed | Limiter admission inside an existing transaction raises a typed retryable denial before any durable probe. The precondition is documented. |
| C23 | Fixed | SQL health/incident failures become retryable authentication unavailability, including when the incident store itself is unavailable. |
| C24 | Fixed | The unmerged downgrade guard removes FORCE RLS under exclusive table locks before inspecting history. A non-superuser table-owner test proves retained history blocks downgrade and failed validation rolls policy changes back. |
| C25 | Fixed | Report intent is followed by terminal server-side completion/failure and prepared row count, after the read-only guard closes. Tests cover success, admission/decryption failure and early close without duplicate terminal records. |
| C26 | Fixed | Direct token insertion pins Family eligibility and active campaign/runtime admission facts with row locks; an independent mutation cannot change eligibility mid-insert. |
| C27 | Fixed | Kernel pseudo mounts are enumerated by exact destination, filesystem, source and mount root, including Docker's masked null devices. Arbitrary descendant/bind mounts are rejected; private source/root strings stay out of diagnostics. |
| C28 | Fixed | Web staging-grant admission rejects ciphertext SELECT and is integrated into web consumer acknowledgement; production startup must use this assertion with its actual web identity. |
| C29 | Fixed | Already-dirty populations still take the manifest-row write lock. A competing raw-like Family update proves it cannot bypass serialization. |
| X1 | Fixed | Accepted internationalized origins are reconstructed with ASCII IDNA hosts and preserved ports/brackets; Django Host and CSRF-origin tests use the browser's punycode form. |

The reviewed commit passed 2,251 ordinary and 831 PostgreSQL tests, with 95.01%
line and 85.66% branch coverage. That is pre-correction evidence, not final
validation of this round's changes.

Post-correction validation: 2,263 ordinary tests and 883 PostgreSQL tests pass;
scoped coverage is 94.71% lines and 85.39% branches. The rebuilt image passes the
same 2,263 ordinary tests, all 30 Compose checks and all 12 container-isolation
checks. All 66 browser cases pass. Ruff checks/formatting, tracked Markdown lint,
migration drift and diff checks pass. The database run uses its own disposable
PostgreSQL cluster, independent of reviewer test resources. Round two is complete;
the third independent full-branch review remains required.
