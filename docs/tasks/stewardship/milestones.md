# Stewardship milestones and review tasks

[Task index](README.md) · [Task execution plan](overall.md) ·
[Controlling plan](../../plans/stewardship/overall.md)

Implementation completion lives in the eight subsystem task lists. This file
tracks integration evidence and gate completion, not a second copy of package
status. Execute each phase's exact package order and partial-package scope from
the controlling plan. A review gate remains open until its whole protocol and
exit criteria pass, even if all implementation checkboxes are checked.

## Phase 0: Skeleton

Scope: [Phase 0](../../plans/stewardship/overall.md#phase-0-reproducible-project-skeleton).

- [x] M0.01 — Demonstrate local bind-mount reload, routes, and internal health.
- [x] M0.02 — Run the same baseline tests on the host and in the image.
- [x] M0.03 — Demonstrate safe startup rejection for incomplete production settings.
- [x] M0.04 — Review and correct the scaffold before foundation work.

Evidence: September 7, 2026, macOS/arm64 host and Linux/arm64 image. The
[OPS-01 evidence](operations.md#ops-01-development-and-production-compose-topology)
records 570 passing baseline tests on both environments and 5 passing opt-in
Compose checks. Production rejects startup rather than using development keys.
OPS-09 coverage/CI and DOM-05 clock/traceability now implement their Phase 0
scope. Final pre-review validation passed 614 tests on both host and rebuilt
image (3 opt-in checks skipped), and all 5 separately enabled Compose tests.
Host scoped coverage is 96.99% lines and 95.29% branches at that pre-review SHA.
M0.04 is in progress; no foundation work or formal gate is released.

First M0 review: September 7, 2026, reviewed implementation SHA `b5bdc01`, base
`c810839`, local session `20260907-194036-a3712e`. Codex and three Claude shards
completed with no reviewer failures after the authorized findings-validation
retry. The finalized verdict was COMMENT: 12 validated Medium findings (10
Claude-only, 2 Codex-only), no agreed findings. This is not gate approval.

Initial triage (Claude findings C1–C10 and Codex findings X1–X2 in finalized
order):

- Auto-fixed C1: updated task evidence to the rebased implementation commits.
- Auto-skipped C2 as a duplicate of C1; campaign-domain evidence is corrected.
- Auto-fixed C5 and C7: logging tests now install sentinel handlers, verify
  closure/routing, and capture actual redacted stderr JSONL from root and all
  five framework/service logger routes. This tests the configuration hook,
  not future Gunicorn/Celery startup integration.
- Auto-fixed C6: wrapper documentation lists all commands and filesystem effects.
- Auto-skipped C8 as already handled by the explicit OPS-02 path-integration
  deferral in the [Compose guide](../../development/stewardship-compose.md#configuration-and-unfinished-boundaries).
  Typed deployment paths are not yet connected to a running consumer.
- Auto-skipped C9 as a false-positive: the clock helper deliberately exposes
  standard ZoneInfo errors, has no request caller, and promises no single error
  type. Request-boundary validation must accompany its future HTTP consumers.
- Auto-skipped C10 as already assigned to OPS-02/ARC-06 ownership and mount
  integration in the [authority guide](../../development/stewardship-authority.md).
  Cross-UID authority readability remains an integration requirement; the
  scaffold neither mounts these files to web nor enables production consumers.
- Fixed C3 with human approval: shared parser selection preserves existing
  tools' full option set; stewardship exposes config-only shared flags and the
  coverage runner exposes none. Command-inapplicable flags fail before dispatch,
  `--version` is standalone, and long-option abbreviations are disabled for
  stewardship. Regression tests cover real no-write rejection, all unsupported
  shared flags, all inapplicable command/option pairs before and after the
  command, redacted option diagnostics, help, and coverage-runner nonexecution.
- Fixed C4 with human approval: deployment parsing recognizes every existing
  ParishKit top-level section without interpreting other tools' fields. Unknown
  sections (including `deploymnet`) still fail. Regression tests load all eight
  tool examples with and without explicit deployment metadata, verify section
  isolation, and reject unknown names even alongside a valid deployment section.
- Fixed X1 with human approval: strict reads now bound bytes actually read,
  composition nodes/depth, and alias-expanded nodes/depth before construction
  or merge flattening. Unexpected recursion and invalid UTF-8 become sanitized
  ConfigError responses. Existing authority checks and legacy non-strict loading
  remain intact. Tests cover inclusive limits, bounded reads despite file growth,
  cycles, deep mappings/alias chains, merge amplification, ordinary alias/merge
  compatibility, and redacted deployment/CLI/authority read failures. The earlier
  isolated diagnostic reproduced the original uncaught 550-level recursion.
- Fixed X2 with human approval: host/CI installs preinstall the build lock and
  disable isolated editable-build dependency resolution. Release packaging uses
  the installed locked build tools too; no release was run. Checkout instructions
  match this order, and eight regression tests cover workflow commands, build-lock
  compatibility, Docker parity, and documentation. The test-only Compose service
  mounts the required workflow/documentation fixtures read-only.

All 12 validated findings have triage dispositions: 4 auto-fixed, 4 auto-skipped,
4 fixed with human approval, and no interactive skips or remaining decisions.
This closes the first triage pass, not M0.04 or the required re-review.

Initial corrections passed 619 host tests (3 opt-in Docker checks skipped),
Ruff checks/formatting, and tracked Markdown lint. After C3, the complete host
coverage runner passed 680 tests (3 opt-in Docker checks skipped), with 97.04%
scoped lines and 95.49% scoped branches. After C4, 703 host tests passed (3 opt-in
Docker checks skipped), with the same scoped coverage. After X1, 728 host tests
passed (3 opt-in Docker checks skipped), with 97.13% scoped lines and 95.70%
scoped branches. After X2, a fresh Python 3.12.13 environment installed both locks
successfully; `pip check` passed and all 78 applicable installed distributions
matched their lock entries. Its complete host suite passed 736 tests (3 opt-in
Docker checks skipped), with 97.13% scoped lines and 95.70% scoped branches.
Ruff checks/formatting, tracked Markdown lint, and migration drift checks passed.
The development image was rebuilt, and all 5 opt-in Compose checks passed,
including the complete in-image baseline, reload, private routes, durable
replacement, and production refusal. Corrections are committed for re-review:
pika branch mode compares committed HEAD, so the dirty-tree attempt in session
`20260907-202524-ac35dc` was aborted before Claude launch and has no verdict.
Re-review and human gate approval remain pending.

Second M0 review: September 7, 2026, reviewed clean implementation SHA `85d94e7`,
base `c810839`, local session `20260907-202815-cc5525`. Codex and three Claude
shards completed without failures. The finalized verdict was COMMENT: 14
validated Medium findings (13 Claude-only, 1 Codex-only), no agreed findings.
The six signed-off correction commits through this SHA were pushed before
review. M0.04 remains open.

Second triage uses C1–C13 and X1 in this session's finalized order, independent
of the first review's identifiers. All findings were checked against current
source, callers, tests, and the approved Phase 0 scope before decisions:

- Auto-fixed C2: README local validation now includes the scoped coverage runner,
  migration drift with test settings, report-path and PowerShell guidance, and
  the Compose validation link. Two regression cases compare README commands
  with CI steps using the existing workflow-test pattern.
- Auto-fixed C6: the Compose guide identifies the checked-in Caddyfile as an
  uninstalled reference template. Operators must copy it and supply its path;
  Compose does not validate its contents. Production ingress remains disabled.
- Auto-fixed C7: the CLI deployment-validation test removes ambient deployment
  overrides and PARISHKIT_ROOT with monkeypatch. The YAML-failure tests already
  reject their invalid document before consulting environment settings.
- Auto-skipped C1 (false-positive): Django's configure_logging integration calls
  the custom hook with LOGGING settings. Its argument is required for that
  protocol; this scaffold intentionally installs a fixed redacted stderr setup,
  not arbitrary caller-supplied handlers.
- Auto-skipped C3 (false-positive): select() first reads the persisted version
  from the configured root. A missing or mistyped root fails before any write;
  atomic_write_text cannot make the described empty-root activation succeed.
  Concurrent mount/ownership integration remains assigned to OPS-02/ARC-06.
- Auto-skipped C4 (false-positive): only Money.to_string promises canonical
  output. from_string promises exact decimal value parsing, not canonical input
  rejection. Existing tests deliberately normalize strings such as 0 and 1.2.
- Auto-skipped C5 (duplicate/false-positive): same input-policy claim as C4;
  authority document hashing has no Money parser dependency. Normalizing exact
  values does not invalidate canonical output or authority digest checks.
- Auto-skipped C8 (already handled): prepare-development explicitly provisions
  only the Phase 0 layout, not configuration authority/bootstrap. OPS-02/ARC-06
  own the later typed-path provisioning and service mount integration.
- Auto-skipped C9 (already handled): standard ZoneInfo lookup errors are an
  intentional tested contract, consistent with the earlier clock disposition.
  LocalDayInterval has no request caller or promise of one exception type;
  future request-boundary validation must handle the documented input types.
- Auto-skipped C12 (false-positive): installed pytest-django metadata requires
  pytest unconditionally, but Django only under its optional django extra.
  The alleged unconditional unbounded Django upgrade does not occur; the
  supported checkout installation includes the locked stewardship dependencies.
- Fixed decision 1, C10 with human approval: the opt-in Compose lifecycle check
  independently collects the full host baseline and compares its exact node IDs
  with the actual in-image baseline run. A test-only pytest hook emits structured
  collection evidence without new dependencies or broader mounts. Comparison
  retains duplicate occurrences, ignores order only, and reports missing and
  unexpected IDs. Empty, malformed, or missing evidence fails closed. Tests
  cover equal-count substitutions, duplicates, missing IDs, and real collection
  of parameterized/skipped synthetic tests without executing their bodies.
- Fixed decision 2, C11 with human approval: added a root .dockerignore fallback
  synchronized with the Dockerfile-specific policy by a normal regression test.
  A narrow read-only mount makes that policy fixture available in image tests.
  Two opt-in scratch builds independently exercise each ignore file with only
  synthetic allowed/private files, exporting locally without publishing images.
  These checks exposed directory exceptions in the original specialized policy
  that also admitted unlisted descendants (private YAML, bytecode, and an extra
  requirements file). Removed those exceptions from both copies; explicit file
  patterns still admit all required build inputs. Both functional checks now
  pass. The guide requires maintained BuildKit/Buildx usage, records tested
  versions, and documents the fallback and limits of filename-based exclusions.
- Skipped decision 3, C13 at human direction: compatibility with older Docker
  versions is not required. No legacy Compose JSON adapter will be added. The
  guide replaces the broad v2+ claim with current maintained tooling and records
  the validated Compose 5.3.1 baseline alongside Docker/Buildx versions. The
  existing per-container JSON check already passes on that baseline. Retain the
  approved root ignore policy as defense in depth, not legacy-builder support.
- Fixed decision 4, X1 with human approval of selective redaction: stewardship
  uses an opt-in parser that replaces structured argument failures with explicit
  authored hints about recognized fields and enum choices. Raw input values,
  converter exception messages, unknown options, and extra positional tokens
  never enter syntax diagnostics. Existing safe application validation messages
  use a separate trusted path; help remains available. Both stewardship entry
  points use this policy; other ParishKit parsers are unchanged. Regression
  tests cover private values in split/equals-form options, unknown option names,
  positional values, invalid choices, missing values, converter failures, safe
  application messages, and rejection before dispatch or coverage execution.

Automatic corrections passed 738 host tests (3 opt-in Docker checks skipped),
with 97.13% scoped lines and 95.70% branches. All 87 deployment tests also passed
with deliberately conflicting/unknown ambient deployment overrides. Ruff,
formatting, and migration drift checks passed. No container rebuild, new review,
commit, or gate approval is implied by this partial triage evidence.

After C10, the complete host baseline passed 750 tests (3 opt-in Docker checks
skipped), with 97.13% scoped lines and 95.70% branches. The rebuilt development
image passed the same baseline; all 753 collected node IDs matched the host.
All 17 separately enabled Compose checks passed, including the lifecycle smoke,
reload, private routes, durable replacement, and production startup refusal.
An earlier smoke invocation overlapped test-file edits and failed; the recorded
passing validation used the stable updated files. Three decisions remain open;
no commit, push, or M0.04 approval has occurred in this triage pass.

After C11, 751 host baseline tests passed (5 opt-in Docker checks skipped), with
97.13% scoped lines and 95.70% branches. The rebuilt image passed the same
baseline and matched all 756 host collection IDs. All 19 separately enabled
Docker checks passed, including both synthetic build-context tests and the
development lifecycle. Docker Engine/CLI 29.7.2 and Buildx 0.36.0-desktop.1 were
used. Ruff, formatting, tracked Markdown lint, and diff whitespace checks passed.
Two interactive decisions remain open; changes are not committed or pushed.

After the C13 decision, one interactive finding remains (X1). Only documentation
and disposition evidence changed; Markdown lint and diff whitespace checks
passed. No compatibility code was added and no new runtime test run is claimed.

Second triage is complete: 3 auto-fixed (C2, C6, C7), 7 auto-skipped (C1, C3, C4,
C5, C8, C9, C12), 3 fixed with human approval (C10, C11, X1), and 1 skipped at
human direction (C13). All 14 findings have dispositions; none await a decision.

After X1, the complete host baseline passed 776 tests (5 opt-in Docker checks
skipped), with 97.17% scoped lines and 95.72% branches. The rebuilt image passed
the same baseline and matched all 781 host collection IDs. All 19 separately
enabled Docker checks passed. Ruff, formatting, tracked Markdown lint, migration
drift, and diff whitespace checks passed. This includes 25 new selective-error
regression cases; shared-tool behavior remains unchanged.

These corrections are committed for re-review in logical signed-off commits
after human authorization. No push is included in this commit-only handoff.
A fresh independent review of the corrected branch and human approval are still
required before M0.04 can close or foundation work can begin. Pika branch review
uses committed changes, so the next review can now cover these corrections.

### Third local review and triage

Session `20260907-215721-1698c1` reviewed clean committed HEAD `3605c28`
against `c810839`. Verdict: COMMENT, with 14 validated MEDIUM findings,
all Claude-only; no agreed findings, failed reviewers, or finalization
degradations. The human authorized parent-side validation of the existing
reviewer artifacts after reviewer-local command permissions blocked validation.
No review was rerun or posted remotely during that recovery.

Third-triage identifiers C1–C14 follow this session's finalized order; they are
independent of earlier sessions' identifiers. Initial automatic dispositions:

- Auto-fixed C11: render both Compose overlays with all profiles explicitly
  selected and require exact service-name coverage. The new assertion first
  failed for both overlays, confirming that the previous check covered only
  web, PostgreSQL, and Valkey. Rendering does not start pending services.
- Auto-fixed C12: test the provisioned development directories against the
  typed deployment defaults, with the intentional parish-authority exclusion
  and required Caddy data/config children. Full path integration stays OPS-02.
- Auto-skipped C6 (already handled): the standard ZoneInfo lookup exception is
  explicitly tested; this domain value has no configuration/request caller
  through `validate_with`. The earlier exception-contract decision still holds.
- Auto-skipped C8 (false-positive): there is no mismatched alias today, and the
  existing CLI regression asserts the authored config hint. Adding an alias
  that breaks the mapping would fail that test, contrary to the finding.
- Auto-skipped C9 (false-positive): `select()` itself calls `read_version()`
  before writing, not only through its coordinator. The claimed missing-root
  invocation fails before the shared atomic writer can create directories.
- Auto-skipped C13 (false-positive): inspection of the pinned PostgreSQL 18.6
  image's entrypoint confirms `docker_setup_env` reads the password file and
  unsets its `_FILE` variable before `gosu` re-execution. The claimed reversed
  ordering is incorrect. This does not claim native Linux-host validation.
- Auto-skipped C14 (false-positive): the pinned Caddy 2.11.4 image successfully
  adapted the unchanged template with networking disabled and a synthetic
  hostname. Its JSON puts the three internal-path 404 responses before the
  reverse proxy; in-route matcher placement is accepted by this version.

Initially seven findings awaited human decisions, in order: C1 (automated Caddy
validation now versus its existing OPS-03.05 assignment), C2 (release validation parity),
C3 (strict-loader diagnostic privacy), C4 (test-bind missing-path behavior),
C5 (Caddy hardening and explicit production prerequisites), C7 (CLI import
overhead), and C10 (host development-server listen address). C7's host import
diagnostic measured about 74 ms for `parishkit.cli`, including about 50 ms for
`requests`; this establishes avoidable imports, not a health timeout failure.

Focused validation after the two automatic test fixes: 119 tests passed,
including both all-profile Compose renders and synthetic build-context checks;
the separately gated lifecycle test was not enabled. No application runtime
behavior changed, no pending service started, and no commit, push, deployment,
release, or gate approval is implied. M0.04 remains open.

Human-approved C1 is fixed: the opt-in Compose suite now passes the committed
template over stdin to the production overlay's pinned Caddy image and runs
`caddy adapt --validate`. Exact adapted-JSON assertions cover the enclosing
host match, all three internal-path 404 rules, and their precedence over the
catch-all proxy. The disposable validation container has no networking,
published ports, or host mounts and uses temporary Caddy storage. This does
not start ingress, request certificates, or complete OPS-03's live tests.

After C1, the focused Compose/service suite passed 120 tests, with one lifecycle
test skipped because its separate opt-in was not enabled. Ruff and formatting
passed. The complete host baseline passed 777 tests with 6 Docker checks
skipped; changed Markdown and diff whitespace checks also passed. Six
interactive findings remain: C2, C3, C4, C5, C7, and C10. Nothing is committed
or pushed, and M0.04 remains open.

Human-approved C2 is fixed: release validation now runs the same scoped
line/branch coverage command and test-settings migration-drift check as CI,
before artifact building. Two regression cases require exact step parity,
including environment and failure/skip policy, and ordering before the build.
Both cases failed against the prior release workflow, then passed after the
change; all 13 build-contract tests passed.

The local scoped coverage run passed 779 tests (6 opt-in Docker checks skipped),
with 97.17% lines and 95.72% branches. The migration check detected no changes.
This validation does not execute the release workflow, build release artifacts,
or authorize publication. Five interactive findings remain: C3, C4, C5, C7,
and C10. All triage changes remain uncommitted and M0.04 remains open.

Human-approved C3 is fixed: strict YAML loading has its own diagnostic boundary
covering path expansion/existence/read failures, parsing, scalar conversion,
and top-level shape validation. It preserves authored duplicate/unhashable-key
hints, numeric parser locations, repair guidance, and resource-limit messages,
but omits configuration paths, source snippets, and chained exception details
from normal traceback rendering. Legacy non-strict loading is unchanged.

All 12 new regression cases failed against the old implementation and passed
after the fix. They exercise direct exception/traceback rendering, missing and
optional files, parser failures with and without marks, file-operation errors,
and legacy diagnostic compatibility. The focused configuration/stewardship
suite passed 185 tests. The full scoped run passed 791 tests (6 opt-in Docker
checks skipped), with 97.30% lines and 95.86% branches; Ruff and formatting
passed. Four interactive findings remain: C4, C5, C7, and C10. Nothing is
committed or pushed, and M0.04 remains open.

Human-approved C4 is fixed: all 36 short-form test-fixture mounts now use a
shared long-form read-only bind definition with `create_host_path: false`.
Their source/target pairs are unchanged, and the existing live-source mount
retains its explicit no-creation setting. A new structural regression first
failed against the old short syntax and now verifies existing checkout sources,
unique matching container targets, read-only access, and disabled creation.
The real all-profile development render also checks the complete fixture
mapping and effective mount flags.

After C4, the host scoped run passed 792 tests (6 opt-in checks skipped), with
97.30% lines and 95.86% branches. The separately enabled Compose suite passed
all 21 checks, including Caddy validation and the disposable development
lifecycle. Its in-image baseline passed 792 tests (6 skipped), matching all
798 host collection IDs. Ruff, formatting, tracked Markdown, and diff whitespace
checks passed. No image publication, deployment, commit, or push occurred.
Three interactive findings remain: C5, C7, and C10; M0.04 remains open.

Human-directed C5 is deferred to OPS-03, not claimed as runtime remediation.
The controlling plan now makes Caddy identity/capability/filesystem hardening,
restricted writable state, network isolation, and their runtime validation
explicit prerequisites for enabling ingress. Existing OPS-03.01/05 checklist
labels point to that work without adding or renumbering tasks. The Compose
comment and development guide link the unfinished boundary to its owner.
The `pending-ingress` profile and all effective runtime settings are unchanged;
neither template validation nor this documentation authorizes ingress startup.
Two interactive findings remain: C7 and C10. Nothing is committed or pushed,
and M0.04 remains open.

After the C5 documentation/comment change, the focused Compose and traceability
suite passed 21 tests, with its separately gated lifecycle check skipped.
Both real all-profile renders and pinned Caddy validation passed. Ruff,
formatting, tracked Markdown lint, and diff whitespace checks passed; no new
runtime hardening or full lifecycle run is claimed for this documentation step.

Human-approved C7 is fixed: shared argument registration and its default-value
constants now live in the provider-free `parishkit.cli_arguments` module and
remain available through `parishkit.cli`. Stewardship imports the lightweight
module directly. Flags, help text, defaults, and legacy exception-handling
imports retain their behavior. The new shared module is explicitly included
in the scoped coverage manifest and its regression test.

Five isolated-interpreter tests cover all/config/none argument registration,
CLI help, and the real healthcheck with a synthetic local response. They assert
that legacy CLI, provider modules, requests, and urllib3 remain unloaded.
The pre-fix health/help cases reproduced the unwanted imports; the three helper
cases failed because the extracted module did not yet exist. A separate test
checks the legacy re-export and shared defaults.

After C7, the host scoped run passed 798 tests (6 opt-in checks skipped), with
97.31% lines and 95.86% branches. All 21 separately enabled Compose checks
passed, including lifecycle and an in-image baseline of 798 passed/6 skipped
matching all 804 host collection IDs. Ruff, formatting, migration drift,
tracked Markdown lint, and diff whitespace checks passed. Only C10 remains
for an interactive decision. Nothing is committed or pushed; M0.04 is open.

Human-approved C10 is fixed: direct development web launches default to
`127.0.0.1:8000`. The explicit `--bind-all-interfaces` option is accepted only
for the development web service; Compose supplies it inside the container
while continuing to publish solely on host loopback. Unrelated commands,
unimplemented profiles/roles, abbreviations, and value-bearing forms are
rejected without dispatch or private-value disclosure. The service API retains
two-argument compatibility with a new default-false keyword-only opt-in.

Tests cover both bind modes across the service/profile matrix, default and
explicit CLI dispatch, options before/after commands, safe syntax errors, and
the raw/rendered Compose command and localhost port. The pre-fix default/opt-in
CLI and Compose checks failed, then passed after the change. Updated CLI and
Compose documentation warns against opting into network exposure on a host.

Third triage is complete, with all 14 finalized findings accounted for:

| Findings | Disposition |
| --- | --- |
| C11, C12 | Auto-fixed: profile coverage and provisioned-path regression tests |
| C6 | Auto-skipped: intentional, previously handled timezone exception contract |
| C8, C9, C13, C14 | Auto-skipped: verified false positives |
| C1, C2, C3, C4, C7, C10 | Fixed with human approval |
| C5 | Deferred at human direction to explicit OPS-03 hardening prerequisites |

Totals: 2 auto-fixed, 5 auto-skipped, 6 interactively fixed, and 1 interactively
deferred. The final host scoped run passed 865 tests (6 opt-in Docker checks
skipped), with 97.31% lines and 95.89% branches. The rebuilt local image
`448845e0ffe6650d5aa159c1b22d73b9420ced6c504c6a924e829d1528920c59`
passed the same baseline and matched all 871 host collection IDs. All 21
separately enabled Compose checks passed. Ruff, formatting, tracked Markdown,
migration drift, and diff whitespace checks passed.

No commit, push, merge, deployment, release, or gate approval occurred during
the triage pass itself. Following human authorization, the corrections were
committed as separate logical, signed-off changes, with this evidence recorded
in a final documentation commit. No push or new review was performed as part
of that commit handoff. M0.04 remains open pending a fresh independent review
and human approval. Pika branch review reads committed HEAD, so the next review
can now cover these corrections.

### Fourth local review and initial triage

Session `20260907-225359-7d63ab` reviewed clean committed HEAD `cf1c7b9`
against `c810839`. Verdict: COMMENT, with 10 validated MEDIUM findings,
all Claude-only; no agreed findings, failed reviewers, or finalization
degradations. Of 55 raw findings, 45 fell below the reporting cutoff.
The human authorized simpler Claude launch commands after lean-ctx rejected
shell-array syntax, then parent-side validation of existing reviewer artifacts
after reviewer-local permissions blocked their self-check command. All four
Claude documents passed pika's unchanged validator; no reviewer was rerun,
finding content edited, or review posted remotely. Recovery evidence remains
in the session directory.

Fourth-triage identifiers C1–C10 follow this session's finalized order and are
independent of prior sessions' identifiers. Initial dispositions:

- Auto-fixed C2: derive the pinned Caddy adapter test's expected internal paths
  from Django's `internal_patterns`, retaining exact enclosing-route and proxy
  ordering checks. Adding an internal route now requires updating the template.
  This test-only change does not implement or enable production ingress.
- Auto-fixed C4: require each application service's UID to contain only ASCII
  decimal digits and have a nonzero numeric value. The topology assertion no
  longer accepts `root`, `root:root`, or zero-padded UID zero as non-root.
- Auto-skipped C1 (false-positive): the Dockerfile already sets both Python
  environment variables in the common image. A service-specific Compose
  environment mapping does not erase unrelated image ENV defaults, so the
  claimed runtime loss does not follow from YAML's shallow anchor merge.
- Auto-skipped C3 (false-positive/already handled): `select()` first reads and
  validates the persisted version, so an absent authority root fails before
  the atomic writer. Mount/ownership validation and concrete provisioning
  remain explicitly assigned to OPS-02/ARC-06; the development scaffold
  intentionally creates no parish authority. No new filesystem integration
  or exception-contract change is justified by the repeated missing-root claim.
- Auto-skipped C5 (false-positive): an authorized temporary-script diagnostic
  using the installed pytest exception renderer retained both the original
  AssertionError and the teardown failure, with exception chaining. The
  claimed loss of the original diagnostic is not reproduced. Cleanup errors
  still fail the test; no conversion to a warning was made.
- Auto-skipped C7 (already handled): production startup is unconditionally
  disabled, and ARC-03 explicitly owns secure production cookies, HSTS,
  CSRF, and safe proxy settings before the Phase 1 gate. No operational
  production configuration is claimed by the current development skeleton.
- Auto-skipped C10 (already handled): the fixed development-only key belongs
  to an explicitly non-persistent, unauthenticated scaffold with dummy storage.
  Direct launches default to loopback; the human-approved explicit all-interface
  opt-in remains documented. Production cannot load these settings. Real
  signing keys and authenticated runtime settings remain later-phase work.

Three findings await human decisions, in order: C6 (coverage-runner failure
diagnostics), C8 (live versus image-baked build-contract fixtures), and C9
(standalone dev-extra compatibility with global Django pytest configuration).
C6's missing-pytest example is overstated: that subprocess reports its own
nonzero exit status. Root resolution and process-launch OS failures do receive
the misleading generic message; any correction must retain private-value
redaction rather than echo arbitrary exception text.

Validation after the two test-only fixes: 100 focused tests passed, including
pinned Caddy validation, both all-profile Compose renders, and build-context
checks; the separate lifecycle opt-in was not enabled. The full host baseline
passed 865 tests with 6 opt-in Docker checks skipped. Ruff, formatting, tracked
Markdown lint, and diff whitespace checks passed. No image rebuild, full
lifecycle run, new coverage measurement, commit, push, or gate approval is
claimed for this step. M0.04 remains open.

Human-approved C6 is fixed: the coverage runner now identifies invalid repository
directories, invalid manifests, process-launch failures, and invalid report
paths/content separately, without echoing paths or exception details. Pytest's
nonzero exit status still returns unchanged before reading any stale report.
Report structure is explicitly validated so malformed JSON shapes raise
validation errors; unexpected TypeError/KeyError exceptions are no longer
silently relabeled as bad input by the runner.

The 21 initially added/strengthened cases failed against the old implementation
and passed after correction; an additional report-path failure regression also
passes. All 61 quality-runner tests passed. The complete host scoped run passed
886 tests (6 opt-in Docker checks skipped), with 97.37% lines and 95.99% branches.
Ruff, formatting, migration drift, tracked Markdown, and diff whitespace checks
passed. C8 and C9 remain for human decisions. Changes are uncommitted; no image
rebuild, Compose lifecycle run, push, or gate approval is claimed for this step.

Human-approved C8 is fixed: four narrow read-only checkout reference mounts
provide README, project metadata, and both build/runtime locks at separate
comparison paths. The test service enables a byte-for-byte freshness check;
missing, unreadable, or changed inputs fail with an authored rebuild message
without printing file contents. Image-baked metadata and installed dependencies
remain untouched. The guide documents rebuild requirements and narrow versus
directory fixture mounts; raw and rendered Compose tests enforce the paths,
read-only/no-creation flags, and activation environment variable.

Synthetic regressions cover matching inputs and each of the four changed or
missing files. A new opt-in test uses temporary reference data with networking
disabled: matching README bytes pass, then deliberately stale bytes produce
the rebuild diagnostic. It changes no checkout file or application data.

After C8, the full host scoped run passed 901 tests (7 opt-in Docker checks
skipped), with 97.37% lines and 95.99% branches. Rebuilt local image
`1a7ca5a8d9bf97dfc6495d6dca5a91fdef7d64a3f709cb4b0827e737be0f5ad8`
passed the same baseline, matching all 908 host collection IDs. All 22 separately
enabled Compose checks passed, including freshness, lifecycle, and persistence.
Ruff, formatting, migration drift, tracked Markdown, and diff whitespace checks
passed. Only C9 remains for a human decision. Nothing is committed or pushed,
and M0.04 remains open.

Human-approved C9 implementation now makes `dev` include the existing
`stewardship` extra by package self-reference, without duplicating its dependency
ranges or changing base-package requirements. Its regression first failed and
then passed; all 29 build-contract tests passed. Regenerating the universal lock
produced no changes. After lean-ctx rejected pip's requirements-file option, the
human authorized the documented uv equivalent for fresh-environment validation.

A new Python 3.12.13 environment installed only `.[dev]` with the existing locks
as constraints and passed `uv pip check` (62 installed packages). Django startup
is fixed: the full baseline collected 909 tests, with 900 passed, 7 opt-in skips,
and 2 failures in unchanged Google Drive tests because the separately optional
`googleapiclient` library is absent. The scoped runner therefore correctly failed;
its generated report is not passing final validation evidence. Expanding `dev`
to include the existing Google extra awaits human direction. No Google/provider
request or credentials were used. C9 is not yet recorded as fully validated and
triage remains open; nothing is committed or pushed.

The human subsequently approved including the existing `google` extra in `dev`
as well. C9 is now fixed and validated: one self-reference includes both owned
extras, and the regression prevents duplicate dependency declarations. The
expanded assertion first failed without Google and passed after the change.
Regenerating the universal lock still produced no changes.

A second fresh Python 3.12.13 environment installed only `.[dev]`, after the
locked build tools and with the runtime lock as constraints. `uv pip check`
passed for all 78 installed packages; the full scoped run passed 902 tests
(7 opt-in Docker checks skipped), with 97.37% lines and 95.99% branches.
No provider credentials or live calls were needed. The rebuilt local image
`a16562bd0fbbdd46197253a34b380f5ae6f1425868679b1abe399b7f1c7ff9b9`
passed the same baseline, matching all 909 host collection IDs. All 22 separately
enabled Compose checks passed. Ruff, formatting, migration drift, tracked
Markdown, and diff whitespace checks passed.

Fourth triage is complete, with all 10 finalized findings accounted for:

| Findings | Disposition |
| --- | --- |
| C2, C4 | Auto-fixed: internal-route parity and numeric non-root UID tests |
| C1 | Auto-skipped: image ENV already supplies the supposedly lost settings |
| C3 | Auto-skipped: persisted-version read precedes writes; integration already assigned |
| C5 | Auto-skipped: pytest preserves both exceptions and their diagnostics |
| C7, C10 | Auto-skipped: intentional scaffold boundaries and explicit later-phase ownership |
| C6 | Fixed with human approval: stage-specific redacted coverage diagnostics |
| C8 | Fixed with human approval: separate build-input reference mounts and freshness tests |
| C9 | Fixed with human approval: standalone dev extra includes stewardship and Google dependencies |

Totals: 2 auto-fixed, 5 auto-skipped, 3 interactively fixed, and 0 interactively
skipped. No findings or decisions remain in this triage pass. During triage no
commit, push, merge, deployment, release, or gate approval occurred. The human
subsequently authorized signed-off commits and a push of the corrected PR branch.
The corrections are recorded in separate logical commits with this final
evidence handoff. M0.04 remains open pending fresh independent review and human
approval; committing or pushing does not release the gate. Pika branch review
reads committed HEAD, so the next review can now cover these corrections.

Fifth M0 review and triage: finalized September 8, 2026, reviewed SHA `fa55500`,
base `c810839`, session `20260907-234727-dfcba6`. All five Claude shards and the
Codex reviewer completed their reviews. Four Claude drafts could not run the
child-session validator; the parent subsequently validated all five artifacts
with `pika review check-findings`: all five passed. Four needed draft-to-final
promotion; the fifth had already been delivered by its reviewer. All five
artifacts were included in finalization; no shard's findings were excluded.
No findings were rewritten or reviewers rerun during delivery recovery. Finalize
reported COMMENT, 9 retained Medium findings from 49 raw (1 agreed, 7
Claude-only, 1 Codex-only), 39 below-cutoff findings, no High/Critical findings,
and no final reviewer failures, verdict mismatches, or degradations.

The human delegated routine technical/triage decisions and approved the
[automated phase delivery cycle](../../plans/stewardship/overall.md#automated-phase-delivery-cycle).
This replaces routine per-finding approval stops, not authorization boundaries
or final human PR merge approval. Identifiers below use the finalized bucket
order: agreed A1, Claude-only C1–C7, Codex-only X1.

| Finding | Disposition and evidence |
| --- | --- |
| A1 | Fixed: image-baked build-input snapshots now include Dockerfile and both ignore policies; separate checkout references cannot overwrite evidence. Synthetic changed/missing cases cover all seven inputs; Docker checks prove matching/stale recipe and ignore-policy behavior. |
| C1 | Fixed: all eight shipped example configurations pass the actual deployment loader verbatim, in addition to synthetic coexistence tests. |
| C2, C5 | Discarded as duplicates of A1; the same snapshot/reference change resolves both. |
| C3 | Fixed: development provisioning expands user paths before making them absolute; regressions verify the resolved target and absence of writes on resolution failure. |
| C4 | Fixed: deployment path resolution converts expected expansion errors to sanitized ConfigError at the owning boundary; loader and CLI regressions cover RuntimeError, ValueError, and OSError. |
| C6 | Addressed with explicit adjacent service comments: vendor infrastructure entrypoint/capability hardening belongs to OPS-02 before Gate 1. Production application startup is still unavailable, and the scaffold admits no parish data. No unsupported capability recipe was introduced in Phase 0. |
| C7 | Addressed: native Windows skips POSIX authority filesystem tests explicitly, while pure schema tests remain portable. The development guide requires Linux Compose/WSL for full authority durability validation; production fsync guarantees are not weakened. |
| X1 | Fixed: active-manifest inspection uses guarded lstat, accepts only regular files, and treats only FileNotFoundError as absence. Regression recovery rejects inaccessible entries even when pathlib predicates suppress errors. |

Validation after corrections: all 928 host tests passed with 10 opt-in Docker
checks skipped, scoped coverage 97.39% lines / 95.96% branches. The rebuilt image
`405dd37af3f19d2d90556336c81f9aa3f6fed0c9cba5b83d934376b9b95c6b19`
passed the same 928 tests/10 skips with exact parity across 938 collected IDs.
All 25 opt-in Compose tests passed, including four matching/stale input cases,
both synthetic default-deny context checks, reload, persistence, and production
refusal. Ruff, formatting, all tracked Markdown, migration drift, and whitespace
checks passed. Coverage evidence is `triage-coverage.json` in the review session.
No provider calls, deployment, release, merge, or gate approval occurred.

Five review/triage rounds are now complete. M0.04 remains open: material fixes
will receive a fresh independent review before PR/CI handoff and human merge
approval. Phase 1 has not started.

Sixth M0 review and triage: September 8, 2026, reviewed SHA `ca19f99`, base
`c810839`, session `20260908-110155-8cbc6a`. The permission preflight passed;
all five Claude shards delivered validated final artifacts without parent-side
permission recovery. Codex also completed successfully. Finalize reported
COMMENT, 11 retained Medium findings from 47 raw (1 agreed, 9 Claude-only,
1 Codex-only), 35 below-cutoff findings, no High/Critical findings, and no failed
reviewers, verdict mismatches, or degradations. Routine triage used the delegated
phase workflow, with A1/C1–C9/X1 identifiers in finalized bucket order.

| Finding | Disposition and evidence |
| --- | --- |
| A1 | Fixed: version reads now use the same guarded lstat/regular-file boundary as active manifests. Both boundaries have directory and real POSIX FIFO tests proving no YAML open occurs. |
| C1 | Clarified: OPS-01/OPS-09 counts are explicitly historical, with links to the current M0 correction/validation evidence; historical successful runs were not misrepresented as current runs. |
| C2 | Discarded: the normative operations specification explicitly names ghcr.io/\<repository\>/parishkit. The repeated parishkit segment is intentional when the GitHub repository is named parishkit. Added guide clarification and a rendered-image assertion; no image naming contract changed. |
| C3 | Fixed: the shared example set is collected once and a separate assertion requires at least the eight shipped examples, preventing empty parametrizations from silently removing coverage. |
| C4 | Discarded: the claim depends on an unimplemented ARC-03 consumer. The current public_origin contract validates a bare origin, not canonical spelling; _host strips a trailing dot only during label validation, not in its return value. No runtime consumer currently maps this value into ALLOWED_HOSTS/CSRF settings. Browser-origin integration remains owned by ARC-03 before Gate 1. |
| C5, C7 | Discarded as duplicates of A1; the version-file guard and both-boundary regressions cover them. |
| C6 | Fixed: strict YAML presence uses guarded stat, distinguishing missing/not-a-directory from access errors, and rejects non-regular inputs before open. Legacy non-strict behavior is unchanged. |
| C8 | Clarified: all five fifth-round Claude artifacts passed; four needed promotion and the fifth was already delivered. None was excluded from finalization. |
| C9, X1 | Fixed together: the coverage runner resolves and rejects checkout-local destinations, exclusively reserves a new output, and refuses existing reports without modifying them. Zero-exit pytest with no new measurement fails; tests emit reports during simulated execution rather than pre-seeding stale output. |

Post-correction host validation passed 938 tests with 10 opt-in Docker skips;
scoped coverage is 97.42% lines / 96.01% branches. The in-image baseline passed
the same 938 tests/10 skips with exact parity across 948 collected IDs. All 25
opt-in Compose checks passed. Ruff, formatting, all tracked Markdown, migration
drift, and whitespace checks passed. Coverage evidence is `triage-coverage.json`
in this session. Six rounds are complete; material input/output guard fixes
will receive independent re-review before PR/CI handoff. No merge, deployment,
release, real-provider operation, or human gate approval occurred.

Seventh M0 review and triage: September 8, 2026, reviewed SHA `7e6a4c1`, base
`c810839`, session `20260908-112244-2adbb1`. The permission preflight passed;
all five Claude shards delivered validated final artifacts without recovery,
and Codex completed successfully. Finalize reported COMMENT, three retained
Medium findings from 43 raw (one Claude-only, two Codex-only), 40 below-cutoff
Low findings, no High/Critical findings, and no failed reviewers, verdict
mismatches, or degradations. All retained findings were accepted and fixed
under delegated triage; none requires a product decision.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Fixed in f1ff517: correlation-header regression now includes a valid UUID and explicitly rejects equality with the supplied value, alongside the malformed-header case. |
| X1 | Fixed in c412c22: the coverage subprocess removes ambient PYTEST controls; regression seeds a deselection and confirms it cannot reach the child while unrelated environment values remain intact. |
| X2 | Fixed in c412c22: raw coverage uses fresh external storage beside the report, ignoring ambient COVERAGE/COV_CORE controls. Tests preserve existing checkout data under both default and overridden COVERAGE_FILE, and reject storage failures before launch. |

Post-correction validation at implementation SHA `f1ff517`: 942 host tests
passed with 10 opt-in Docker skips, scoped coverage 97.43% lines / 96.01%
branches. Rebuilt image baseline: the same 942 passes/10 skips, exact parity
across 952 collected IDs. All 25 opt-in Compose checks passed. Ruff, formatting,
all tracked Markdown, migration drift, and whitespace checks passed. Coverage
evidence is `triage-coverage.json` in this session; the runner also retains its
raw database in fresh external diagnostic storage.

Seven review/fix rounds are complete. The final correction pass changes only
developer validation and regression tests, not application behavior. The
automated loop exit criteria are satisfied: at least three completed rounds,
no final-round High/Critical findings, no unresolved accepted Medium-or-higher
findings, and passing validation. Proceed to PR/CI handoff; M0.04 remains open
for human merge approval. No Phase 1 work, merge, deployment, release, real
provider operation, or gate approval is authorized by this evidence.

Initial [Phase 0 PR #8](https://github.com/epiphany40223/parishkit/pull/8) CI at
`ce6844c` passed validation and DCO but failed the Linux Compose lifecycle:
PostgreSQL could not traverse its bind-mount root after its entrypoint dropped
privileges. The pinned PostgreSQL 18 entrypoint chowns nested PGDATA, not its
parent. Docker Desktop's host permission translation masked this locally.
Reproduced using the pinned image and Linux tmpfs owned by another UID: 0700
fails traversal and 0711 allows initialization.

The development provisioner now gives only that mount root traversal permission
(0711); enclosing host directories, credentials, and actual PGDATA stay private.
No production prerequisite or existing-runtime migration is bypassed. Two
opt-in tmpfs regressions exercise actual vendor privilege-drop behavior with
both provisioned and deliberately restricted modes, without host mounts or a
database. Local correction validation: 942 host/image passes, 12 opt-in skips,
exact parity across 954 IDs, all 27 opt-in Compose tests passing, scoped coverage
97.43% lines / 96.01% branches, plus lint, formatting, migration drift, and
whitespace checks. Evidence is `ci-correction-coverage.json` in the seventh
session. The correction will receive independent review and fresh PR CI;
M0.04 and human merge approval remain open.

Eighth M0 review and triage: September 8, 2026, reviewed SHA `e50a394`, base
`c810839`, session `20260908-114901-553509`. The permission preflight, five
Claude shards, and Codex all completed successfully without artifact recovery.
Finalize reported COMMENT: eight Claude-only Medium findings from 48 raw,
40 Low findings below cutoff, no High/Critical findings, and no failed reviewers,
verdict mismatches, or degradations. C1–C8 below use finalized bucket order.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Discarded as inaccurate: select calls read_version before atomic_write_text, so a missing root cannot reach the parent-creating writer. A new regression proves selection neither creates the root nor its parent. The coordinator also writes the version before materializer.prepare, contrary to the claimed ordering. |
| C2 | Fixed in 0220afc: invalid IANA identifiers and malformed paths consistently raise a value-free ValueError. Tests cover unknown, empty, and relative-path zone names. |
| C3 | Fixed in cf2d2e8: acceptance references are checked against exact pytest collection, not flattened AST names. A collection-only regression covers parameterized functions and class methods while excluding nested closures and fixtures; stale parameter IDs cannot match the exact set. |
| C4 | Fixed in 8031ab9: active checks the provisioned authority root before treating a missing manifest as unconfigured. Missing, non-directory, and inaccessible roots fail without preparing materializer state. This does not imply the root-creation path claimed in C1 exists. |
| C5 | Discarded: the parser accepts exact Decimal-compatible inputs; only serialization promises canonical two-place output. Existing round-trip tests intentionally normalize 0, -0, and 1.2. Requiring canonical spelling on input would change that contract; no current digest/deduplication consumer compares raw parser input as alleged. |
| C6 | Discarded as already handled/out of phase: ingress remains disabled and the guide explicitly describes the operator-supplied mount and its limitations. OPS-03 items 1 and 5 already own installation and validation of effective ingress before enabling it. Defaulting to a checkout template would contradict the production no-checkout-mount boundary. |
| C7 | Fixed in 8031ab9 after reproducing PyYAML normalization of U+0085. Emission now escapes Unicode separators; five write/read/select regressions prove exact document and digest preservation, including accented text. |
| C8 | Fixed in a6936a3: the traversal marker is emitted by the PostgreSQL-user process after the access check, and both cases require successful setup/process exit. Source, mkdir, or gosu failures cannot impersonate expected denied traversal. |

PR CI at `e50a394` passed PostgreSQL startup but exposed a test-only reloader
race: HTTP can serve before the restarted watcher's first timestamp snapshot.
The copied fixture is now re-touched while the expected response is pending,
without extending the deadline or changing application code. Both forward and
reverse reload assertions remain. Three regressions cover old content, wrong
status, and connection startup failures. Bounded logs from only the synthetic
UUID test project are retained on Compose failures before teardown.

Post-correction validation at implementation SHA `a6936a3`: 955 host/image
baseline passes, 12 opt-in skips, exact parity across 967 collected IDs, all 30
opt-in Compose checks passed, and scoped coverage 97.44% lines / 96.04% branches.
Ruff, formatting, tracked Markdown, migration drift, and whitespace checks
passed. Coverage is `triage-coverage.json` in the eighth session. An initial
local Compose attempt exited during PostgreSQL startup without service logs;
it did not recur in the diagnostic-enabled lifecycle and full-suite reruns.
No additional cause is claimed for that isolated attempt.

Eight review/fix rounds are complete: five eighth-round findings fixed and
three rejected with evidence; no accepted Medium-or-higher findings remain.
The final round found no High/Critical issues. The delivery-cycle wording now
matches the human's requested stopping rule: validated corrections belong to
their round, without automatically requiring a finding-free next review.
Fresh CI at the corrected PR head is still required. M0.04 remains open for
human merge approval; Phase 1, merge, deployment, and release remain unapproved.

## Phase 1: Secure foundation

Scope: [Phase 1](../../plans/stewardship/overall.md#phase-1-secure-foundation-and-durable-domain).

- [x] M1.01 — Demonstrate fake-provider Google login, denial, and session expiry.
- [x] M1.02 — Demonstrate Family access and exact campaign-boundary denial.
- [x] M1.03 — Prove single-current-campaign constraints under concurrent requests.
- [x] M1.04 — Verify service mounts, configuration activation, and restart durability.
- [ ] M1.05 — Complete Gate 1 before beginning Phase 2.

Phase 0 handoff: human-approved [PR #8](https://github.com/epiphany40223/parishkit/pull/8)
merged on September 8, 2026, at 18:48:21 UTC. Its final head `259863b` passed
validation, Compose, and DCO in CI run `34264954252`; merge commit `509245d`
is the base of this phase. The preceding M0 entries are historical evidence,
including their then-open merge condition; that condition is now satisfied.

Evidence: Phase 1 has started with the bounded DAT-01.01/.04 storage increment
described in the [owning checklist](data.md#dat-01-storage-conventions-and-base-records).
This is not the complete M1 demonstration. No G1 or Phase 2 release is claimed.

Current Phase 1C evidence: seeded Family code/token/session and boundary tests,
concurrent campaign/lifecycle SQL cases and 48 real-container checks demonstrate
M1.02-.04. The latter includes restricted identities, atomic authority, durable
replacement, metrics credential rotation, offline exclusion and supervisor crash
recovery in development/production-shaped environments. All 69 browser cases
pass. The final affected Google/session/report/SQL regression run passes all
192 cases, completing M1.01's demonstration. The complete validation repeat,
third review and Gate 1 approval remain open. Current evidence is in the
[Phase 1C ledger](../../guides/stewardship-phase-1c-reviews.md).

First storage-increment review: September 8, 2026, reviewed SHA `99b715d`,
base `509245d`, session `20260908-150154-92d717`. The permission preflight
and both reviewers completed successfully; no failed reviewers or verdict
mismatches. Finalize reported REQUEST_CHANGES: one High and ten Medium findings
from 23 raw, with 12 Low findings below cutoff. Routine decisions used the
delegated phase workflow, not an additional product-approval stop.

| Finding | Disposition and evidence |
| --- | --- |
| C1 (High) | Fixed: the PostgreSQL CI command explicitly selects its profile and requires database verification; pytest fails wrong-profile, missing-selection, or skipped required tests. Real subprocess regressions cover profile failure, skip failure, and successful execution. |
| C2 | Fixed: field/domain validation remains inside the locked transaction, while PostgreSQL owns uniqueness/CHECK validation; a query-count regression excludes redundant constraint SELECTs. |
| C3 | Fixed: actor/correlation UUID types are checked before acquiring a lock or invoking the callback. The claim that Django skips all non-editable fields was inaccurate; this is early explicit validation, not a repair of missing full_clean coverage. |
| C4 | Fixed: every SQL session update must advance the version, preserve identity/creation, and receive a server write timestamp. Tests cover bypass rejection and invalidation of old optimistic tokens. |
| C5 | Fixed: malformed expected versions raise ValueError; StaleRecordError denotes only a real concurrent change. |
| C6 | Documented and tested: standalone clearsessions aborts on protected metadata. ARC-04.03 explicitly owns ordered cleanup before authentication is enabled. |
| C7 | Fixed: record defaults reuse the existing request/task correlation scope, with fresh UUIDs only for unscoped operations. |
| C8 | Fixed: explicitly offset ISO strings round-trip through Django serialization; naive, invalid, and implicit-date inputs still fail without value disclosure. |
| C9 | Corrected tracking: DAT-01.01 remains partial until explicit Parish ownership is integrated with DAT-01.02; deployment-level ownership is not presented as that integration. |
| X1 | Duplicate of C7; the shared scope accessor resolves both reports. |
| X2 | Fixed: PostgreSQL rejects activity at/after expiry and revocation before authentication, including queryset bypass attempts. |

Post-correction validation: 980 host/image baseline passes, 39 explicit opt-in
skips, exact parity across 1,019 collected IDs, 27 required PostgreSQL tests,
and all 30 opt-in Compose checks passed. Ruff, formatting, tracked Markdown,
migration drift, and whitespace checks passed. Scoped baseline coverage is
93.75% lines / 94.51% branches; database tests separately exercise the SQL paths.
Coverage artifact: `/tmp/stewardship-phase1-evidence.OKnATz/round1-coverage.json`.
One review/fix round is complete; two more are required before this increment's
PR handoff. The first round's High finding is fixed, not reclassified.

Second storage-increment review: September 8, 2026, reviewed SHA `37f39e8`,
base `509245d`, session `20260908-160609-17a9f0`. The permission preflight and
both reviewers completed normally. Finalize reported COMMENT: eight Medium
findings from 21 raw, 13 Low findings below cutoff, and no High/Critical findings,
failed reviewers, verdict mismatches, or degradations.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Already labeled historical (the sentence began with initial); removed duplicated counts anyway and linked the owning milestone's round-specific evidence to prevent drift. |
| C2 | Fixed: a frozen reusable migration builder generates equivalent table-local guards; a PostgreSQL test requires an enabled row-level guard for every concrete MutableRecord model. Table-local function lifetimes avoid cross-app reverse-migration dependencies. |
| C3 | Fixed: new default creation/initial-write timestamps now use PostgreSQL statement-time defaults, matching later updates. A clock-skew regression sets the application clock to 2100 and verifies database-generated instants. Explicit historical timestamps remain possible for controlled imports; counters, not wall clocks, establish mutation order. |
| C4 | Fixed: principal, Django session binding, and authentication instant cannot change through the service or SQL. Rebinding requires a new attribution record. |
| C5 | Fixed: structurally forbidden history/binding changes raise StorageInvariantError, distinct from user-field ValidationError. |
| C6 | Clarified the delivered boundary explicitly: DAT-01.04 PostgreSQL session configuration is verified solely in the disposable profile; non-test connection/startup integration still belongs to ARC-02/OPS-04, and production remains disabled. |
| X1 | Fixed: mutate_record binds the supplied operation correlation around its transaction/callback and restores the outer context on success and rollback; dependent audit records share the operation ID. |
| X2 | Fixed: inserts and version-advancing updates reject activity after revocation, with PostgreSQL regressions for both paths. |

Post-correction validation: 988 host/image baseline passes, 48 explicit opt-in
skips, exact parity across 1,036 collected IDs, 36 required PostgreSQL tests,
and all 30 Compose checks passed. Ruff, formatting, tracked Markdown, migration
drift, and whitespace checks passed. Scoped baseline coverage is 93.05% lines /
94.54% branches. Coverage artifact:
`/tmp/stewardship-phase1-evidence.OKnATz/round2-coverage.json`.
Two rounds are complete; the third remains required before PR handoff.

Third storage-increment review: September 8, 2026, reviewed SHA `0979f39`,
base `509245d`, session `20260908-162435-21dd84`. The permission preflight and
both reviewers completed normally. Finalize reported COMMENT: five Claude-only
Medium findings from 12 raw, seven Low findings below cutoff, no High/Critical
findings, and no failed reviewers, verdict mismatches, or degradations. Codex
returned no findings; its telemetry's parsed=false means zero accepted findings
in this pika version, not a failed reviewer. Finalize performs artifact cleanup;
the finalized result and manifest remain the durable review evidence.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Fixed: the all-model PostgreSQL test reads installed function definitions and compares every model-declared immutable/write-once column plus the version-advance predicate, preventing Python-only guard drift. |
| C2 | Fixed: immutable_guard_v1 supplies reusable table-local append-only guards; every concrete ImmutableRecord table must have an enabled BEFORE UPDATE OR DELETE row trigger. |
| C3 | Fixed: both SQL guard families raise SQLSTATE 23514/IntegrityError; raw audit mutation and migration-reapplication tests assert the specific exception type. |
| C4 | Fixed: optional audit subject IDs allow blank values in model validation; a subject-less deployment event passes full_clean and persists without a fabricated subject. |
| C5 | Fixed: session revocation is write-once once non-null. Both ORM mutation and version-advancing SQL reject clearing or moving the cutoff; unchanged revoked metadata remains writable without reviving access. |

Final local validation: 989 host/image baseline passes, 52 explicit opt-in
skips, exact parity across 1,041 collected IDs, 40 required PostgreSQL tests,
and all 30 Compose checks passed. Ruff, formatting, tracked Markdown, migration
drift, and whitespace checks passed. Scoped baseline coverage is 92.72% lines /
94.54% branches. Coverage artifact:
`/tmp/stewardship-phase1-evidence.OKnATz/round3-coverage.json`.

Three review/fix rounds are complete, every accepted Medium-or-higher finding
is resolved, and the final round had no High/Critical findings. Corrections and
their passing regressions belong to that round under the controlling delivery
cycle. This bounded increment is ready for PR/CI handoff and human merge
approval. Phase 1 and Gate 1 remain incomplete; no production startup, merge,
deployment, release, or real-provider operation is authorized by this evidence.

### Configuration-preparation increment

The human merged [PR #9](https://github.com/epiphany40223/parishkit/pull/9)
on September 8, 2026, at 21:53:20 UTC, merge `0e4f1c0`. Its final head
`ab9d92d` passed all CI checks in run `34276341522`; the preceding open-merge
condition is now historical. The configuration-preparation branch starts at
that merge and delivers the partial DAT-01 scope recorded in the
[owning checklist](data.md#dat-01-storage-conventions-and-base-records).
This does not release M1/G1 or enable production startup.

Preparation round 1: reviewed `84c88f4` against `0e4f1c0`, session
`20260908-180455-a30ec6`. Both reviewers and the permission preflight completed
normally; no failed reviewers, mismatches, or degradations. Finalize returned
COMMENT: four Medium findings from 15 raw, 11 Low below cutoff, no High/Critical.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Fixed: PostgreSQL cases cover absent, empty, and deliberately unsorted multi-integration inputs, asserting canonical UUID-text ordering matches stored UUID ordering and exact normalized digests. |
| C2 | Fixed: database checks constrain nonempty parish identity fields, US phone syntax, HTTP(S) website prefix, and supported validation evidence. Specific-constraint INSERT tests exclude unrelated uniqueness failures. Semantic URL and pinned-application IANA validation remain in the strict parser rather than a drifting database catalog. |
| X1 | Fixed: iterative whole-ancestry verification rejects an incomplete predecessor even below a self-consistent child/grandchild. A visited-ID guard rejects cycles without imposing a fixed limit on legitimate history; this linear internal verification is not a per-request readiness API. |
| X2 | Fixed: every verified ancestor must retain the same parish record ID, including on the existing-candidate retry path. Forged internally consistent changed-owner children fail preparation and cannot be extended. |

An independently observed validation issue was also fixed: a random UUID in a
pytest parameter label made host/image collection IDs differ. The uppercase
UUID test now uses a deterministic synthetic value. One subsequent local
Compose run hit a temporary PostgreSQL bind-mount ownership failure; a fresh
disposable-project rerun passed all 30 checks without changing mounts, runtime
permissions, or existing data.

Post-correction validation: 1,028 host/image baseline passes, 85 explicit opt-in
skips, exact parity across 1,113 collected IDs; 73 required PostgreSQL tests
(112 with the 39 pure schema tests); all 30 Compose checks passed. Ruff, format,
tracked Markdown, migration drift, Django system checks, and whitespace passed.
Scoped baseline coverage: 89.86% lines / 90.29% branches, artifact
`/tmp/stewardship-configuration-evidence.lLYSIF/round1-corrected-coverage.json`.
The focused pure/PostgreSQL suite separately passed 72 tests with 100% lines and
branches over the three new configuration modules. Round 1 is complete; two
more independent review/fix rounds remain before PR handoff.

Preparation round 2: reviewed `7713230` against `0e4f1c0`, session
`20260908-182452-5337ca`. Both reviewers and permission preflight completed
normally; no failures, mismatches, or degradations. Finalize returned COMMENT:
four Medium findings from 21 raw, 17 Low below cutoff, no High/Critical.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Fixed the serialization/round-trip amplification: recursive lineage loading and projection prefetches use three queries independent of depth; all full parsing/verification runs before the global preparation lock. Tests at depths 1 and 40 assert query counts and observe the real lock acquisition. Full ancestry validation remains linear CPU/memory rather than trusting unverified stored prefixes; it is not a per-request readiness API. Deterministic two-connection and injected-writer races verify bounded exact-candidate rechecks under the lock. |
| C2 | Fixed: timezone validation uses the pinned wheel's leaf-name catalog, never the host TZPATH. A host-only valid zone is rejected while bundled zones remain valid after replacing the host lookup path. No process-global timezone policy is changed in production code. |
| C3 | Fixed with the same catalog boundary: region directories/unknown names never become filesystem paths. OSError, corrupt resources, and a missing package produce safe ConfigError diagnostics. Tests prove directory-like input and resource locations are not disclosed. |
| X1 | Fixed: integration kind/record-ID mappings remain stable over the full verified lineage, including removal/re-addition. Changed IDs and historical ID reuse fail both new preparation and forged-existing-candidate retry; re-addition with the original ID succeeds. |

Post-correction validation: 1,038 host/image baseline passes, 94 explicit opt-in
skips, exact parity across 1,132 collected IDs; 82 required PostgreSQL tests
(131 with 49 pure schema tests); all 30 Compose checks passed. Ruff, format,
migration drift, tracked Markdown, and whitespace checks passed. Scoped baseline
coverage: 88.39% lines / 86.57% branches, artifact
`/tmp/stewardship-configuration-evidence.lLYSIF/round2-corrected-coverage.json`.
Two review/fix rounds are complete; a third remains required before PR handoff.

Preparation round 3: reviewed `b1a8f8c` against `0e4f1c0`, session
`20260908-213954-c73bd5`. Both reviewers and permission preflight completed
normally; no failures, mismatches, or degradations. Finalize returned COMMENT:
five Medium findings from 20 raw, 15 Low below cutoff, no High/Critical.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Fixed: preparation rejects nested transactions and disabled autocommit, then owns a durable atomic block. An independent connection verifies committed visibility and immediate advisory-lock release after return. PostgreSQL tests use real transactions rather than enclosing test savepoints. |
| C2 | Fixed: unavailable or corrupt installed schema data raises a distinct, safely worded SchemaEnvironmentError. Both preparation and historical verification propagate it instead of misclassifying the history as invalid. |
| C3 | Fixed: historical validation dispatches using each row's stored discriminator. A registry and explicit database known-name constraint permit later schema migrations without reinterpreting old rows. A regression changes the current validator while retaining successful historical verification. |
| C4 | Fixed: version 1 owns a frozen, hash-checked timezone-name asset rather than consulting an upgradable dependency. Tests cover dependency drift, asset corruption, and inclusion of the exact catalog in a built wheel. |
| X1 | Duplicate of C4; resolved by the same frozen-schema catalog and regression tests. |

The three required dual-model review/fix rounds are complete, with no accepted
unresolved Medium-or-higher findings and no High/Critical in the final round.
Below-cutoff Low findings remain in the review artifacts. Corrections and their
validation belong to the round that found them under the approved loop policy;
this is not a claim of an independent APPROVED verdict on the corrected head.
Human PR merge approval and the full integrated Gate 1 remain required.

Post-round-3 validation: 1,042 host/image baseline passes, 97 explicit opt-in
skips (85 PostgreSQL and 12 Docker), exact parity across 1,139 collected IDs;
138 required PostgreSQL plus pure schema tests passed. The focused coverage
suite passed 98 tests over the three configuration modules: 234/235 statements
and 87/88 branches covered. Whole stewardship baseline coverage is 88.19% lines
and 86.24% branches. Artifacts are `round3-coverage.json` and
`round3-database-coverage.json` under the same local evidence directory above.
All 30 Compose checks passed against rebuilt image
`sha256:e9dd180e3746cf62e862e3053f02c005f871ba31d23e2b142e8420b25aaf6f47`.
The first run repeated the intermittent local PostgreSQL bind-mount ownership
error; a fresh disposable-project rerun passed without permission or data changes.
The initial wheel test exposed missing pinned build tools in the local venv;
installing the existing build requirements fixed it, with no dependency change.
Ruff, formatting, tracked Markdown, both profiles' migration drift, Django system
checks, and whitespace checks passed. This increment is ready for PR/CI handoff;
it does not activate configuration or close any partially delivered DAT-01 task.

The human merged [PR #10](https://github.com/epiphany40223/parishkit/pull/10)
at 03:16:48 UTC on September 9, 2026, merge `c4366cd`, final head `8901bf8`.
[CI run 34306442600](https://github.com/epiphany40223/parishkit/actions/runs/34306442600)
completed successfully at 03:17:56 UTC, including the Docker check that was still
running at merge time. All three jobs and DCO passed. This records the human's
merge, not an agent merge or deployment.

### Configuration-request intake increment

Branch `pr/stewardship-configuration-requests` starts at refreshed `origin/main`
merge `c4366cd`. The [owning DAT-01 checklist](data.md#dat-01-storage-conventions-and-base-records)
and [intake guide](../../guides/stewardship-configuration-requests.md) define the
partial delivery and next installer/runtime/secret work. No installer, Admin
authorization, Applied response, or production startup is enabled. Review rounds
and PR handoff remain pending for this increment; M1 and G1 remain incomplete.

Initial validation: 1,070 host/image baseline passes, 118 explicit opt-in skips,
exact collection parity across 1,188 IDs; 106 required PostgreSQL tests plus 28
pure patch tests passed. The focused 49-test request suite covers 182/184
statements and 58/60 branches over the three new modules. Whole stewardship
baseline coverage is 85.93% lines / 82.66% branches. All 30 Compose checks passed
with image `sha256:3210e10338e9720645a7eb7ab069d78257077f98c6142f8d8dfc17b0eb4c10d2`.
Ruff, format, Markdown, migration drift, and whitespace passed. Local artifacts
are `initial-corrected-coverage.json` and `initial-database-coverage.json` under
`/tmp/stewardship-request-evidence.DwDSa0` (not committed runtime output).

Request intake round 1 reviewed `364ac2d` against `c4366cd`, session
`20260908-233350-a8bd3b`. Permission preflight and both reviewers completed
normally, without failures, mismatches, or degradation. Finalize returned COMMENT:
seven Medium findings from 21 raw, 14 Low below cutoff, no High/Critical.

| Finding | Disposition and evidence |
| --- | --- |
| C1 | Fixed: intake verifies only its selected canonical snapshot/projections and direct predecessor digest; full preparation still verifies all ancestry. Empty/malformed containers fail before queries. Tests at depths 1 and 40 prove constant canonical parsing and bounded query counts for intake/retry. Integration additions use only a UUID-link/historical-binding query, not historical JSON hydration. |
| C2 | Fixed: privileged disposable-data corruption tests remove or alter a base projection and verify rejection without request, checkpoint, or audit rows. |
| C3 | Fixed: updates cannot contain kind; additions check historical kind/ID mappings. Tests reject retired-ID reuse, kind replacement, and remove/add identity replacement while allowing the original binding's re-addition. |
| C4 | Fixed: updates cannot contain credential fingerprints, and additions require null. Only the future target-specific secret workflow can establish replacement evidence; activation's file/consumer verification obligation is explicit. |
| X1 | Duplicate of C3; resolved by the same stable-identity checks and regressions. |
| X2 | Fixed: choose the request schema and validate its patch after acquiring the per-key lock. An independent-connection regression commits a v1 winner after local preflight and proves its format wins over the loser's stricter current builder. |
| X3 | Duplicate of C4; resolved by the same credential-evidence restriction and tests. |

Post-round-1 validation: 1,076 host/image baseline passes, 128 explicit opt-in
skips, exact parity across 1,204 IDs; 203 combined tests (116 required PostgreSQL,
34 patch, 53 configuration-schema) passed. Whole stewardship coverage is 84.78%
lines / 81.01% branches, artifact `round1-coverage.json` in the evidence directory
above. All 30 Compose checks passed with rebuilt image
`sha256:89c82dbd14b3e0927886251c393ed7934c44d50ab275b365fd659426dacce7ab`.
Ruff, formatting, Markdown, migration drift, and whitespace passed. One review/fix
round is complete; two more are required before PR handoff.

Request intake round 2 reviewed `e77f6b6` against `c4366cd`, session
`20260908-235055-f9d60d`. Both reviewers and the permission preflight completed
normally; no failures, mismatches, or degradation. Finalize returned APPROVE
at the Medium cutoff: zero Medium/High/Critical, 13 raw Low findings below cutoff.
No corrections were required. The unchanged code retained the preceding full
validation and passed all 203 combined checks again before the next round.
Codex's zero-finding success is not a parse failure: pika's telemetry sets its
`parsed` convenience flag to `accepted > 0`, independently of structured-result
failure/degradation classification. Its full-branch prompt includes all added
files even though the auxiliary diff is tier-filtered.

Request intake round 3 independently reviewed the same `e77f6b6` against
`c4366cd`, session `20260909-000229-b5a898`. Both reviewers and the permission
preflight completed normally; no failures, mismatches, or degradation. Finalize
returned COMMENT: one Medium finding from 13 raw, 12 Low below cutoff, no
High/Critical. C1 was fixed: the historical-identity SQL now derives quoted
table, primary-key, predecessor, integration-FK, kind, and record-ID identifiers
from model metadata. A real PostgreSQL transactional rename test uses table/FK
names containing spaces and proves the guard still accepts the original binding
and rejects replacement. Disposable DDL is rolled back and metadata restored.

The three required review/fix rounds are complete with corrected validation
passing; every accepted Medium-or-higher issue is resolved, and the final
round had no High/Critical. Corrections and their regression validation belong
to the finding's round under the approved loop policy, not an independent
APPROVED verdict on the later corrected head. Low findings remain below the
requested cutoff in the review artifacts. Full integrated G1 and human PR merge
approval remain required; no incomplete DAT-01 task is checked off.

Final corrected validation: 1,076 host/image baseline passes, 129 explicit opt-in
skips (117 PostgreSQL, 12 Docker), exact parity across 1,205 collected IDs;
204 combined schema/PostgreSQL tests passed. The focused request suite passed
66 tests with 220/222 statements and 76/78 branches covered across four modules.
Whole stewardship baseline coverage is 84.43% lines / 81.01% branches. Reports
are `round3-coverage.json` and `round3-database-coverage.json` under the same local
evidence directory. All 30 Compose checks passed with rebuilt image
`sha256:aea1b16b76181829372e1dc46d9dd5a6c3a0b05742b2af97496e1ec0f02373ad`.
Ruff, formatting, tracked Markdown, both profiles' migration drift, Django system
checks, and whitespace passed. The increment is ready for PR/CI handoff and human
merge approval; runtime/installer/secret and complete request-state work remains
the next dependency-ready DAT-01.02/.03 scope.

PR #11 merged by human approval at 11:11:33 UTC on September 9, 2026, as
`f939b65647c0ee6932db005e53118758ae3d58fe`. All four CI checks for `bbe7970`
passed before merge ([run 34310477305](https://github.com/epiphany40223/parishkit/actions/runs/34310477305)).

### Configuration-activation increment

Branch `pr/stewardship-configuration-activation` starts from PR #11's refreshed
`origin/main` merge. The [activation guide](../../guides/stewardship-configuration-activation.md)
defines delivered scope and remaining integration. DAT-01.02/.03/.06 and all M1/G1
checks remain partial; no production service or external provider write is enabled.

Initial validation: 1,079 baseline tests passed with 159 explicit opt-in skips;
all 147 PostgreSQL tests passed, including 30 new activation/recovery cases.
The combined baseline/database coverage gate preserves complete scope and separate
80% floors: 97.83% lines and 95.69% branches. Ruff, formatting, Markdown, and both
test-profile migration drift checks passed. Review rounds and final Compose/CI
evidence follow below; this is not yet a review-gate release.

Round 1: Pika `20260909-092104-842763` reviewed `98d7cd7` against `f939b65`.
Both vendors completed without failure, mismatch, or degradation. Raw severities:
five Medium, 16 Low, no High/Critical. All five validated Medium findings were
accepted through delegated local-review-triage:

| Source | Finding | Correction |
| --- | --- | --- |
| Claude C1 | Pre-bootstrap `.get()` violates nullable digest contract | Return null; request initialization failure stays resumable |
| Claude C2 | Email validator admits values rejected by SQL | Apply the SQL shape restriction before any write; test localhost and quoted whitespace |
| Claude C3 | Interrupted setup freezes recipient prematurely | Commit runtime creation/recipient with root activation; test file/selection interruptions |
| Claude C4 | Active-base corruption terminally fails valid intent | Propagate base verification outside candidate-error classification; test repair/retry |
| Codex X1 | Partial downgrade removes guards before old-state constraint fails | Reverse preflight locks/checks retained history before any removal; test populated downgrade refusal |

All 38 focused activation PostgreSQL tests pass after these corrections. The
16 below-cutoff Low findings are outside the mandatory Medium+ correction set.

Round 1 post-fix validation passed: 1,079 baseline tests (167 opt-in skips),
155 required PostgreSQL tests, all 30 Compose checks, and exact host/image
parity over 1,246 collected IDs. Combined coverage: 97.70% lines / 95.55%
branches. Ruff, formatting, tracked Markdown, and migration drift passed.

Round 2: Pika `20260909-093630-fea2be` reviewed `a91c418` against `f939b65`.
Both reviewers completed without failure, mismatch, or degradation. Raw findings:
three Medium, 18 Low, no High/Critical. All Medium findings were accepted:

| Source | Finding | Correction |
| --- | --- | --- |
| Claude C1 | Unlock failure strands Django on a closed raw connection | Close through the owning wrapper; preserve replacements; test retries and direct driver close |
| Claude C2 | Unavailable deployment claims staged requests too early | Run read-only preflight before claiming; recheck cancellation after preflight |
| Codex X1 | Direct runtime INSERT bypasses atomic initialization invariant | Deferred database constraint requires root activation before commit |

The Low findings remain below the mandatory correction cutoff. Post-fix full
validation and the independent third round follow; no gate is released here.

Round 2 post-fix validation passed: 1,079 baseline tests (172 opt-in skips),
160 required PostgreSQL tests, all 30 Compose checks, and host/image parity over
1,251 collected IDs. Combined coverage is 97.89% lines / 95.76% branches.
Ruff, formatting, tracked Markdown, and migration drift also passed.

Round 3: Pika `20260909-095324-d04391` reviewed `c822f94` against `f939b65`.
Pika split Claude into two file shards; their union and the independent Codex
full-diff review completed without failure, mismatch, or degradation. Raw totals:
six Medium, 18 Low, no High/Critical. All six validated findings were Claude-only
and accepted through delegated triage:

| Finding | Correction |
| --- | --- |
| C1: Unknown installer request leaks ORM lookup type | Match the subsystem's safe LookupError contract |
| C2: Failure/state CHECKs lack isolated negative tests | Assert named constraints against direct inserts, independently of transition triggers |
| C3: Unlock failure masks workflow failure | Discard the failed connection while preserving the original exception; test all three error classes |
| C4: State reads eagerly validate full applied projections | Keep state/identity metadata reads lightweight; load verified affected values explicitly and lazily |
| C5: Coherence rereads/parses the full YAML document | Recheck only the small atomic manifest reference; test single document read and concurrent reference change |
| C6: Corruption-test DDL can leave a disabled guard | Wrap disable/mutate/enable in a transaction; test failed mutation rolls back DDL |

Focused post-fix validation passed all 226 PostgreSQL/authority tests. Eighteen
Low findings remain below the mandatory correction floor.

Final post-correction validation passed: 1,079 baseline tests (184 opt-in skips),
all 172 required PostgreSQL tests, and all 30 Compose checks. The rebuilt image
passed the same baseline with exact host/image parity across 1,263 collected IDs.
Combined coverage is 97.96% lines / 95.99% branches; evidence is retained outside
the checkout in `final-confirmed.json` under the activation validation directory.
Ruff, formatting, tracked Markdown, both test-profile migration drift checks, and
diff whitespace checks passed. All test data and provider inputs were synthetic.

All three review/fix rounds are complete, with 14 accepted Medium findings fixed
and no unresolved accepted Medium-or-higher findings. The final round had no
High/Critical findings. The automated cycle's local exit criteria are satisfied;
proceed to PR/CI handoff and human merge approval. The PR records its exact final
head and CI results. This does not release Gate 1 or complete Phase 1; the next
dependency-ready scope remains DAT-01 runtime/secret records and installer
integration, followed by TaskRun chains.

### Secret-request storage increment

The human merged PR #12 at `a241205` on September 9, 2026, after all four CI
checks passed at implementation head `00577dc`. Branch
`pr/stewardship-secret-requests` starts from that refreshed `origin/main` tip.
Scope is the [secret-request storage boundary](../../guides/stewardship-secret-requests.md),
not operational credential replacement. DAT-01 and M1/G1 remain partial.
Validation and the required three independent review/fix rounds follow below.

Initial validation passed 1,079 baseline tests (221 explicit opt-in skips),
209 required PostgreSQL tests including 37 new staging/cleanup cases, and all
30 Compose checks. Host/image baseline parity covered 1,300 collected IDs.
Combined coverage is 98.08% lines / 96.21% branches. Ruff, formatting, tracked
Markdown, both migration-drift profiles and diff whitespace checks passed.
An initial full-suite run exposed historical downgrade-test schema leakage;
restoring the whole migration graph in its finally block corrected it before
independent review. No secret files or provider credentials were used.

Round 1: Pika `20260909-105953-2d814c` reviewed `cef011e` against `a241205`.
Both reviewers completed without failure, mismatch, or degradation. Raw findings:
three Medium, seven Low, no High/Critical. Delegated triage accepted both distinct
issues; two reports described the same attribution issue:

| Finding | Disposition |
| --- | --- |
| Claude C1: Migration assertions run after restoration | Fixed: assert marker, retained data and active guards before finally restores the graph in both tests |
| Claude C2 / Codex X1: SQL permits fabricated human expiry/cleanup attribution | Fixed together: require null actor on system transitions; test expiry and both terminal outcomes through raw SQL |

Low findings remain below the mandatory correction floor. Post-fix validation
and subsequent independent rounds follow.

Round 1 post-fix validation passed: 1,079 baseline tests (224 opt-in skips),
212 required PostgreSQL tests, all 30 Compose checks, and exact host/image
parity across 1,303 collected IDs. Coverage remains 98.08% lines / 96.21%
branches; Ruff, formatting, Markdown, both migration-drift profiles and diff
whitespace checks passed. Round 1 is complete; no gate is released.

Round 2: Pika `20260909-111100-d5115f` reviewed `96660c8` against `a241205`.
Both reviewers completed without failure, mismatch, or degradation. Raw findings:
two Medium, eight Low, no High/Critical. Both Medium findings were Claude-only
and accepted through delegated triage:

| Finding | Disposition |
| --- | --- |
| C1: Missing cross-reason cleanup tests | Fixed: test both sequential orders, terminal retries and an independent-connection cancellation/expiry race with durable attribution |
| C2: Safe receipts omit the winning cleanup reason | Fixed: include the closed reason enum and verify status/retry consistency without exposing staging references |

Low findings remain below the mandatory correction floor. Post-fix validation
and the third independent round follow.

Round 2 post-fix validation passed: 1,079 baseline tests (227 opt-in skips),
215 required PostgreSQL tests, all 30 Compose checks, and host/image parity
across 1,306 collected IDs. Coverage is 98.08% lines / 96.21% branches.
Ruff, formatting, tracked Markdown, both migration-drift profiles and diff
whitespace checks passed. Round 2 is complete; no gate is released.

Round 3: Pika `20260909-112510-f0ac17` reviewed `78f8769` against `a241205`.
Both reviewers completed without failure, mismatch, or degradation. Codex's
prompt required the full branch diff and listed all 11 files despite the smaller
auxiliary tier-filtered diff. Raw findings: two Medium, 10 Low, no High/Critical.
Both Medium findings were Claude-only and accepted through delegated triage:

| Finding | Disposition |
| --- | --- |
| C1: Django timestamp validation escapes the service error contract | Fixed: normalize malformed/naive/missing timestamps to safe ConfigError; test both timestamp fields |
| C2: Distant expiry can reserve a target indefinitely | Fixed: service and SQL enforce a 24-hour ceiling, with DB-owned intake timestamps and exact-boundary/forged-time tests |

Low findings remain below the mandatory correction floor. Final post-fix
validation follows; the third round has no High/Critical findings.

Final post-fix validation passed 1,079 baseline tests (236 explicit opt-in skips),
all 224 PostgreSQL tests including 52 secret-request cases, and all 30 Compose
checks. The rebuilt image passed the same baseline with host/image parity across
1,315 collected IDs. Coverage is 98.09% lines / 96.23% branches, measured in
external `final.json` under the secret-request validation directory. Ruff,
formatting, tracked Markdown, both test-profile migration-drift checks and diff
whitespace passed. One final Compose attempt failed at PostgreSQL startup with
a Docker Desktop bind-mount wrong-ownership diagnostic (29 checks passed);
rerunning unchanged in a fresh disposable project passed all 30. No permission
changes, source changes, or retained-data repairs were needed for that retry.

All three rounds are complete. Seven accepted Medium reports described six
distinct issues, all fixed; no accepted Medium+ issue remains. The last round
had no High/Critical findings, satisfying the automated cycle's local exit
criteria. Proceed to PR/CI handoff and human merge approval; the PR records its
exact final head and CI results. No service, provider, deployment, release or
merge is enabled by this evidence. DAT-01 and Phase 1/Gate 1 remain partial.
Next dependency-ready work is explicit Parish audit ownership; remaining
runtime/installer integration and TaskRun chains retain their owning plan scope.

### Audit-ownership increment

The human merged PR #13 on September 9, 2026, after all four CI checks passed
at implementation head `7407982af34af560c6472c69a3489fad2149a441`.
The [successful CI run](https://github.com/epiphany40223/parishkit/actions/runs/34371683609)
and [merged PR](https://github.com/epiphany40223/parishkit/pull/13) record that handoff.
Branch `pr/stewardship-audit-ownership` starts at refreshed `origin/main`,
`fe900b7e0e63a4f091bf05e2d4399b4fc465b7b6`.

Scope: complete DAT-01.01's explicit Parish audit ownership using immutable
profiles, safe soft campaign references, transactional insertion guards and
non-rewriting legacy migration. See the
[integration boundary](../../guides/stewardship-audit-ownership.md).
DAT-01.02/.03/.05/.06 remain open for their remaining scope. The next ready task
is TaskRun claim/retry-chain storage. Full Phase 1 and Gate 1 remain incomplete.

Initial validation: 1,079 baseline tests passed (248 PostgreSQL and 12 opt-in
Compose cases skipped by design); all 248 PostgreSQL tests and all 30 opt-in
Compose checks passed. Host/image collection parity covers 1,339 test IDs.
Combined scoped coverage is 98.10% lines / 96.23% branches, recorded outside the
repository at `/tmp/stewardship-audit-validation.Q4u5Ol/initial.json`.
Ruff, formatting, tracked Markdown and both migration-drift checks passed.

Review round 1: Pika session `20260909-120356-63b599`, reviewed implementation
`4bc51f70a7a763c1179b1d7fecefa12ba6556d62`, completed both independent reviewers
without failed/degraded sources, mismatch or salvage. Three Medium findings,
no High/Critical findings; all three accepted under delegated triage:

| Finding | Disposition |
| --- | --- |
| Deployment-only downgrade/reapply branch lacked direct coverage | Added a populated round-trip test preserving old fields and exercising the restored trigger |
| Ownership migration depended on unrelated secret schema | Narrowed dependency to accounts 0013; added configured/unconfigured secret-downgrade refusal tests proving ownership survives |
| Corruption fixture disabled broader guards without restoration evidence | Narrowed to the immutable Parish guard, used a durable transaction and asserted enabled state after rollback |

Round-1 post-correction validation: 1,079 baseline and 251 PostgreSQL tests
passed, plus all 30 rebuilt Compose checks with parity over 1,342 collected IDs.
Scoped coverage remains 98.10% lines / 96.23% branches in the external
`round1.json` report. Markdown, Ruff and migration drift checks passed.

Review round 2: Pika session `20260909-121747-406594`, reviewed implementation
`33a4163f418761d1de9126e5c8ea7f105a4666e4`, completed both independent reviewers
without failure/degradation, mismatch or salvage. Two validated Medium findings
(one agreed by both reviewers), no High/Critical; both accepted:

| Finding | Disposition |
| --- | --- |
| Legacy deployment-owned history allowed a partially committed broad downgrade before an older accounts guard refused | Preflight now locks/checks retained activation, installer checkpoint and optional secret history before ownership removal; added legacy configured/secret regression cases and the minimum dependency graph case |
| Caller search path could shadow attribution tables | Fixed the function search path to catalog/public/temporary-last; tested shadows of all four context tables without changing unrelated legacy guards |

Round-2 post-correction validation: 1,079 baseline and 255 PostgreSQL tests
passed, plus all 30 rebuilt Compose checks with parity over 1,346 collected IDs.
Scoped coverage remains 98.10% lines / 96.23% branches in `round2.json` in the
external validation directory. Ruff, Markdown and both migration checks passed.

Review round 3: Pika session `20260909-123354-2a76ad`, reviewed implementation
`662177d2e94c17de4fb08de69fde1262d4e2c7ca`, completed both independent reviewers
without failure/degradation, mismatch or salvage. Two validated Medium findings,
no High/Critical:

| Finding | Disposition |
| --- | --- |
| The installer-checkpoint downgrade clause lacked a case where it alone blocks reversal | Accepted and added a legacy-upgrade validating-checkpoint case with no activation or secret history |
| Older SQL emitters can target a temporary shadow audit table before the new guard runs | Auto-skipped as pre-existing: unchanged unqualified INSERTs and caller-path functions are present in base accounts 0010/0013/0015. Explicitly tracked with required emitter regression tests in OPS-02 before runtime-role grants/Gate 1; ownership guide now states this boundary |

Six distinct accepted Medium findings across three complete rounds have been
corrected; one pre-existing finding has an evidence-backed disposition and an
explicit prerequisite owner. No High/Critical findings were reported in any
round. Final post-correction validation passed: 1,079 baseline tests (256
PostgreSQL and 12 opt-in cases skipped by design), all 256 PostgreSQL tests,
and all 30 Compose checks. Host/image collection parity covers 1,347 test IDs.
Combined scoped coverage remains 98.10% lines / 96.23% branches in the external
`final.json` report. Ruff, formatting, tracked Markdown, whitespace and both
migration-drift checks passed. No real provider credentials or parish data were
used. The PR records its exact final head and CI results; human merge approval
remains required, and no auto-merge, production startup or release is enabled.
The final-round Medium test correction does not itself require a fourth round
under the controlling automated delivery cycle. This does not release Gate 1.

### TaskRun storage increment

The human merged the prerequisite audit-ownership increment,
[PR #15](https://github.com/epiphany40223/parishkit/pull/15),
on September 9, 2026, after all four checks passed at
`5bffbb3707f72064911ce4612cf9645f4bd73cf3`; see the
[successful CI run](https://github.com/epiphany40223/parishkit/actions/runs/34378791266).
Branch `pr/stewardship-taskrun-storage` starts at refreshed `origin/main`,
`2483d0864897f135ba4a72bd956d4f6c8b2b8f72`.

Scope is DAT-01.05's [base TaskRun storage](../../guides/stewardship-taskrun-storage.md):
claims/fencing, canonical transitions, logical retry-chain identity, explicit
retry-command deduplication and atomic immutable attempt/audit history. BG-01
operational execution and domain-specific admission/reconciliation are not
enabled. DAT-01.02/.03/.06 and full Phase 1/Gate 1 remain incomplete.

Initial validation passed: 1,104 baseline tests, 305 PostgreSQL tests (including
49 TaskRun cases), 30 Compose cases, exact host/image collection parity of
1,421 IDs, and combined coverage of 98.16% lines / 96.28% branches. Ruff,
formatting, tracked Markdown, whitespace and both migration-drift profiles passed.
The external quality report is
`/tmp/stewardship-taskrun-validation.ez5fm9/initial.json`.

Review round 1 (`20260909-144123-d222c1`) examined
`788b5b7da9105cf1460c5a8559bf7b92c71bff14` with both vendor reviewers completing
without degradation or verdict mismatch. All eight validated findings were Medium
and accepted: key-scoped enqueue locking (with no lock for unkeyed allocations),
raw SQL delete-guard coverage, empty migration reversal/reapply coverage,
per-attempt progress reset, persisted heartbeat renewal evidence, composed-write
correlation propagation, non-racy negative timing checks and binding validation
before a replay admission callback. The corrected focused PostgreSQL suite passes
58 cases. Renewal is asserted by its actual deadline extension, not a short
wall-clock race. Below-cutoff findings are not accepted pending work.

Round-1 post-fix quality validation passes 1,104 baseline and 314 PostgreSQL tests
with 98.17% line / 96.29% branch coverage (`round1.json` alongside the initial
report). Ruff, formatting, Markdown, whitespace and migration drift also pass.
Round-1 post-fix Docker checks also pass: 30 Compose cases, 1,104 containerized
baseline tests and exact host/image collection parity of 1,430 IDs.

Review round 2 (`20260909-145945-9227be`) examined
`f328a8ca74ff634b6cf33f45399be013e8db55ba`; both vendor reviews completed without
degradation or verdict mismatch. Six validated findings were Medium, with no
High/Critical findings. Accepted corrections move action-specific actor validation
before database access, compute deadlines in the UPDATE statement's clock,
expose immutable prior-worker/parent/retry-position metadata, and validate retry
bindings before callbacks. Both reviewers reported that last issue; its one fix
also distinguishes bound replay admission from fresh retry-budget allocation.

The remaining finding claimed SQL rejection aborts the caller's entire outer
transaction. Rejected: `_locked` already wraps every mutation/callback in its own
`transaction.atomic()` savepoint. New coverage catches an SQL rejection outside
the primitive, commits surrounding domain writes and proves the rejected callback
write rolled back. The guide clarifies the low-level exception/savepoint contract
without duplicating canonical SQL transition logic. Focused suites pass 61
PostgreSQL cases and 27 pure validation cases.

Round-2 post-fix quality validation passes 1,106 baseline and 317 PostgreSQL tests,
with 98.20% line / 96.44% branch coverage (`round2.json` alongside the initial
report). Ruff, formatting, Markdown, whitespace and both migration-drift profiles
also pass. Docker also passes 30 Compose checks, 1,106 container baseline tests
and host/image parity of 1,435 IDs.

Review round 3 (`20260909-151444-6b986a`) examined
`984de8ec9bda09096ea780adbb51facd664a8b6a`. Both reviewers completed without
degradation or mismatch. Raw severities were five Medium and sixteen Low,
with no High/Critical findings; all five Medium findings received dispositions:

- Accepted the admission-order correction: stale versions, fences and worker
  identities now fail before callbacks. Regression coverage proves no callback
  invocation on any of those bindings.
- Clarified that PR #15 is the prerequisite audit-ownership merge, not TaskRun
  merge approval. The original paragraph already described branching from that
  merge, but now names the predecessor explicitly.
- Clarified chain-local retry-command identity, matching the root/command lookup
  and unique constraint. A regression proves separate chains independently admit
  the same UUID and each deduplicates its own repeat.
- Rejected duplicating the canonical SQL state graph to support an incomplete
  migration graph: jobs `0001` alone is not a supported runtime. The populated
  downgrade refuses before removing `0002`, as the existing regression proves;
  empty reapply reinstalls guards. The guide explicitly requires all migrations.
  ARC-04/OPS-02 retain startup/readiness enforcement before operational callers.
- Rejected the claim that task audits are permanently deployment-owned. The
  required audit `0006` INSERT trigger resolves the active Parish projection for
  generic `task_*` events. New configured/unconfigured regressions prove both
  ownership paths. Campaign links remain BG-01/DAT-07 scope, now explicit in the
  guide. No duplicate ownership mechanism or historical backfill was added.

All accepted Medium+ corrections are implemented. Final validation passes:
1,106 baseline tests; 320 PostgreSQL tests, including 64 TaskRun cases; 30 Compose
checks; 1,106 containerized baseline tests; and exact host/image collection parity
of 1,438 IDs. Combined scoped coverage is 98.20% lines / 96.44% branches, with
`final.json` alongside the preceding external quality reports. Ruff, formatting,
tracked Markdown, whitespace and both migration-drift profiles pass. All tests
use synthetic identities and disposable storage, without provider credentials.

Three complete independent review/fix rounds satisfy this increment's automated
delivery exit criterion. The last round had no High/Critical findings; no accepted
Medium+ findings remain unresolved. Routine corrections and their regression
tests belong to the same round, per the controlling delivery cycle. The PR/CI
handoff will carry the final pushed SHA and check results. Human merge approval
is still required; neither Phase 1 nor Gate 1 is released.

### Authorization and recovery batch

Branch `pr/stewardship-phase-1a` starts from PR #16 merge
`e5706c8a745d4cd33198918eb006180485be50b9`. Implementation `3206aa8` groups
versioned policy, provenance, capability/column decisions, security effects,
offline-recovery records/protocol and the DST interval prerequisite. See the
[integration boundary and batch rationale](../../guides/stewardship-authorization-foundation.md).
This does not release Phase 1A or Gate 1; operational identity, recovery commands,
campaign lifecycle/schedules, secret integration and later consumers remain open.

Round 1: Pika session `20260909-172302-7d3155` reviewed the complete branch against
the merge base, using Codex and two Pika-generated Claude file shards. Exact-path
Claude permission preflight passed. All reviewers completed and finalization
reported no failed agents, mismatches or degradation: 10 Medium, no High/Critical.
Nine findings were corrected: request-bound manual provenance; clock-skew-tolerant
session revocation; explicit seed rejection tests; invalid-principal column denial;
v2 unsupported-section/legacy-dispatch tests; reuse of verified policy projections;
retired-ID intake and installer preflight; normalized seeded lookup; explicit
first-policy alert-recipient coverage. The proposed single-day campaign change was
rejected because the user's original requirement explicitly requires the end date
to be after the start date. Empty prior-Admin recipients on a historical pre-policy
upgrade are deliberate, not invented Admin authority for the Testing recipient.

Initial complete validation passed 1,386 baseline tests, 336 PostgreSQL tests and
30 opt-in Compose tests, including built-image test parity. Stewardship coverage
was 97.57% lines and 93.79% branches. Ruff, Markdown, migration drift and Docker
build passed. Round-1 correction PostgreSQL validation passed 340 tests; complete
post-correction coverage and remaining review rounds are in progress. These
intermediate counts are not final PR or CI evidence. Complete round-1 corrections
at `bc46d4d` passed 1,394 baseline and 340 PostgreSQL tests, with 97.60% scoped
lines and 93.93% branches, followed by a fresh image and 30 passing Compose checks.

Round 2: session `20260909-174131-9affa5`, again complete-branch Codex plus two
Claude shards, passed exact-path permission preflight and finalized without
degradation, failed agents or mismatches. Thirteen Medium findings, no High or
Critical. Twelve addressed: model-derived SQL identifiers; confirmed-target replay
tests; malformed recovery input/already-Admin tests; safe uninitialized-runtime
diagnostics; installer-side ancestry failure tests; manual-Admin update and missing
grant tests; explicit failed-receipt semantics; normalized confirmation; bound
recovery creation provenance; invalid optional hosted-domain handling; denial epochs
for restored manual scope/origins; and same-patch address replacement rejection.
The deployment binding already has SQL/immutability enforcement and an early
runtime mismatch test; tests do not disable those guards merely to mutate history.
The proposed cross-request cache was declined: no measured performance failure
justifies weakening current manifest/projection corruption detection without an
invalidation contract. ARC-04 performance work may revisit it with evidence.
Deleting an override in one operation and explicitly creating a later override
is not silently rewriting the provenance of an existing rule; the new guard
specifically rejects same-patch identity replacement.

Post-round-2 coverage passed 1,399 baseline and 362 PostgreSQL tests, with 97.74%
scoped lines and 94.51% branches. Ruff and Markdown passed. Round 3 and final
image/CI validation remain required before PR handoff.

Round 3: session `20260909-180150-c7745e` reviewed the complete branch at
`096353b`, with the same two-source/sharded roster and successful exact-path
permission preflight. All reviewers completed; no degradation, failed agents
or verdict mismatch. Four Medium findings, no High/Critical. The canonical
Ministry-scope finding was fixed with exact integer/range checks and bool,
float, string, container and out-of-range regression cases. Three were rejected:

- The online-installer recovery-denial test already exists in
  `test_additive_recovery_is_attributed_revoking_and_idempotent`; its checkpoint
  preservation assertion was made explicit as well.
- Removing an exact denial does not add Admin, create a domain, or add Staff
  to a domain. The specification deliberately limits high-impact notifications
  to those three categories; the existing ordinary audit and denial-epoch
  effects are appropriate, without inventing a fourth notification category.
- Request UUIDs were never authorization credentials. The installer is an
  unexposed internal primitive; ARC-04/ADM-07/service admission remains mandatory
  before any operational caller, regardless of UUID randomness. A separate
  random handle is not a substitute for that boundary.

All three independent review/fix rounds are complete. Accepted Medium findings
have been corrected. Final local validation passed 1,427 baseline tests and
362 PostgreSQL tests, with 97.77% scoped line and 94.62% branch coverage. All
30 opt-in Compose checks passed, including the freshly rebuilt image's 1,427
baseline tests. Ruff check/format, Markdown, migration drift and Docker build
passed. No real provider credentials or parish data were used. PR CI checks and
human approval remain merge requirements; this satisfies the batch review cycle,
not the incomplete Phase 1A or G1.

### Campaign configuration and lifecycle policy batch

September 10, 2026: branch `pr/stewardship-campaign-lifecycle` starts from merged
PR #17 (`4e8ac93`). See the [batch boundary](../../guides/stewardship-campaign-foundation.md)
for delivered configuration/policy work and explicit remaining DAT-02 integration.
The required three independent review/fix rounds are recorded below. Phase 1A
and Gate 1 remain incomplete.

Round 1: Pika session `20260909-215117-8c4b3b` reviewed `5da411c`, with two
Claude shards and one independent full-branch Codex review. Finalization had
no failures, degradation or verdict mismatch; nine findings passed its filter.

| Finding | Disposition |
| --- | --- |
| High: a hypothetical later migration could overwrite policy schema predicates | False-positive as a present defect: no such migration exists; explicit dependencies order the current extension, and real v3 installation already exercises it. Added a full-forward function-definition regression check as requested. |
| Medium: missing raw projection/completeness/update tests | Fixed with forged direct inserts, deferred completeness, invalid module and raw UPDATE cases. |
| Medium: unrelated activations claim a campaign edit | Fixed with `campaign_reprojected` audit semantics; projection/version advancement remains intentional and documented. |
| Medium: repeated structural validation per schedule | Fixed by reusing the validated interval within each preparation/verification call; added a call-count regression. |
| Medium: bootstrap allegedly leaves the current pointer null | False-positive: the activation UPDATE follows bootstrap INSERT and atomically creates/selects the draft. Added root-v3 installation and subsequent Parish-timezone-edit regression coverage. |
| Medium: current archived target passes pure purge predicate | Fixed by rejecting a current target and naming the separate global no-current guard. |
| Medium: SQL permits forged resolved UTC columns | Fixed with independent SQL resolution and direct-insert denial tests, plus gap/fold/half-hour/skipped-day parity cases. |
| Medium: disabled-module content is accepted | Fixed with module/additional-information slot validation and negative tests. |
| Medium: conflicting raw schedule identity insert race | Fixed with the preparation advisory lock in the SQL guard and an independent-connection raw-writer race. |

Related integrity hardening also verifies duplicate JSON/indexed fields exactly
instead of allowing reconstructed values to mask a mismatch. Seven findings are
fixed; two claims are rejected with regression evidence. Later rounds must still
meet the human's minimum-three-round/no-final-High requirement.

Round 1 correction validation: 1,666 baseline tests and 402 PostgreSQL tests
passed; scoped coverage was 97.75% lines and 94.76% branches. Ruff, Markdown,
whitespace and migration-drift checks passed. Reports are local disposable
artifacts under `/tmp/parishkit-campaign-round1-quality*`, not repository data.

Round 2: Pika session `20260909-221600-a55bea` reviewed `f8b2fb8`, again with two
Claude shards and one independent full-branch Codex review. All completed without
degradation, failure or verdict mismatch: nine Medium findings, no High/Critical.

| Finding | Disposition |
| --- | --- |
| Financial SQL lacked direct coverage | Added real ordinary/leap-day financial installations and raw malformed-period rejection tests. |
| Lifecycle guard metadata lacked contract tests | Added exact actor, mode and guard obligations for every action, plus the full action/state/mode matrix. |
| Testing ignores delivery-pause flags | Intentional: the controlling live-pause specification is Production-only. Clarified and tested explicit Testing-send semantics. |
| Temporary campaign holds terminally reject requests | Fixed with a distinct retryable operational exception; a staged request survives the hold and applies when cleared. |
| Timezone-data disagreement can block retained lineage | Documented isolated upgrade verification, whole-lineage impact and safe recovery limitations; added fail-closed/restored-rules regression coverage. |
| SQL admits duplicate/unsorted module sets | Fixed independent SQL canonical-set checks and direct malformed-header tests. |
| SQL admits correctly resolved schedules outside the campaign | Fixed half-open owning-interval checks and raw start/end-edge coverage. |
| SQL omits cross-schedule relationships | Added deferred initial/reminder chronology/uniqueness and recurring-kind cardinality checks, with complete raw-snapshot negative and positive tests. |
| Lifecycle mode constraints are incomplete | Added explicit mode contracts, including Testing-only purge edges. Retained normative Testing acceptance for Return to Testing and reopen's preserve/assert-Production workflow rather than adopting overly restrictive suggestions. |

Related lifecycle correction: overdue start remains eligible after the end, and
close requires the intermediate active state. Tests preserve ordered start/close
effects without outside-interval access; runtime atomic boundary transactions
remain assigned to DAT-02/BG-02. Boundary-worker fencing/restore/purge guards and
global no-current/fencing obligations for all purge-worker edges are explicit.
The following third round completes the minimum review count.

Round 2 correction validation: 1,861 baseline tests and 419 PostgreSQL tests
passed; scoped coverage was 97.83% lines and 94.94% branches. Ruff, Markdown,
whitespace and migration-drift checks passed. Disposable reports are under
`/tmp/parishkit-campaign-round2-quality*`.

Round 3: Pika session `20260909-224152-346bad` reviewed `084c829`, with two
Claude shards and one independent full-branch Codex review. Finalization reports
eight Medium findings, no High/Critical, no failed agents, degradation or verdict
mismatch. The delegated triage dispositions are:

| Finding | Disposition |
| --- | --- |
| Milestone evidence displaces navigation and phase hierarchy | Moved this batch under Phase 1, after the preceding authorization batch; existing anchors are unchanged. |
| Cross-app schema primitives create an import cycle | Extracted public shared primitives, retaining their frozen semantics, catalog asset/checksum and exception compatibility. Updated catalog fault-injection and packaging tests. |
| Reopen accepts a noncanonical proposed interval | Added explicit UTCInterval validation and wrong-type regression cases. |
| A campaign hold would also block unrelated recovery in future runtime states | Auto-skipped as already scoped: Production/restore-review states are forbidden by this foundation's Python and SQL runtime guards. Documented the requirement to replace both layers for unrelated reprojection/recovery when later runtime work enables those states; no premature bypass was introduced. |
| Full timezone catalog lacks database compatibility coverage | Added winter/summer resolution parity for every frozen name. This exposed missing backward aliases and ambiguous abbreviations in the pinned database image; the SQL resolver now freezes IANA 2026c link normalization without changing stored names or the accepted schema catalog. |
| SQL lacks financial overlap confirmation | Added the inclusive overlap/explicit-true guard and raw snapshot rejection/acceptance tests. |
| Retained verification misses database-only timezone drift | Added one set-based SQL check per loaded lineage and independent campaign/schedule database-rule drift tests; existing Python drift coverage remains. |
| Work admission conflates rehearsals and explicit test sends | Replaced the delivery boolean with canonical immutable work classifications and full state/mode/kind/date tests. Explicit preview/readiness sends are date-exempt; ordinary rehearsals are not. Durable routing/authorization remain owning-service obligations. |

Seven findings are fixed; one future-state concern is explicitly scoped to its
owning runtime migration. All accepted Medium-or-higher issues are resolved.
Final correction validation at implementation `1edef35`: 1,948 baseline tests
and 425 PostgreSQL tests passed; scoped coverage was 97.82% lines and 94.93%
branches. The rebuilt image passed the same 1,948 baseline tests, and all 30
Compose checks passed. Ruff, Markdown, whitespace and migration-drift checks
passed. Disposable reports/logs are under `/tmp/parishkit-campaign-final-*`.
All three review/fix rounds are complete, and the final round found no High or
Critical issues. PR CI and human merge approval remain required. Phase 1A and
Gate 1 are not complete; continue the remaining DAT-02 storage/read-guard batch
after the human-approved merge.

### Phase 1A completion batch

PR #18 merged at `9f644b0e1fdef1fb09e009bc1576f979c096f643`. The branch
`pr/stewardship-phase-1a-completion` completes the remaining Phase 1A scope in
one PR. DAT-02 and DOM-02 are implemented; the other mixed-phase packages
explicitly retain only their later operational integration. See the
[completion guide](../../guides/stewardship-phase-1a-completion.md) for the
contracts, demonstrations, review dispositions and validation evidence.

Round 1: Pika session `20260910-074438-7d9457`, reviewed `f4c9588`, four
Claude shards plus independent Codex, 38 validated findings. Round 2: session
`20260910-083115-c257ba`, reviewed `d7483b0`, five Claude shards plus independent
Codex, 19 validated findings. Both rounds completed without degradation or
failed reviewers; accepted Medium+ corrections are implemented and regression
tested. Round 3 (`20260910-100528-1d2bfd`, reviewed `9e31e37`) also completed
with both vendors and no degradation. It found one real High callback-arity
issue, corrected with strict-signature regression fixtures before round 4.
Round 4 (`20260910-103435-666968`, reviewed `577248f`) completed with both
vendors, no degradation/failures, 27 Medium findings and zero High/Critical.
Its 23 corrections/test improvements and four retained behaviors are documented
in the [guide](../../guides/stewardship-phase-1a-completion.md#validation-and-reviews).
All accepted Medium+ findings are resolved, and the four-round exit criterion
is satisfied. Final validation passes 1,958 baseline and 634 PostgreSQL tests
(96.75% lines, 90.44% branches), all 30 rebuilt-image/Compose checks, Ruff,
formatting, Markdown and migration-drift checks. CI and human merge approval
are tracked on the associated PR. Phase 1A is complete; Gate 1 is not released.
After merge, proceed to Phase 1B as a coherent batch; Gate 1 still follows the
integrated Phase 1B/1C foundation.

### Phase 1B identity and web-foundation batch

PR #19 merged at `6fd21eb586a635333be9f55fc7db9caa39284e60`; branch
`pr/stewardship-phase-1b` delivers the complete Phase 1B scope as one batch.
The [implementation guide](../../guides/stewardship-phase-1b.md) and
[review ledger](../../guides/stewardship-phase-1b-reviews.md) own detailed scope,
validation and dispositions. Three independent full-branch dual-vendor reviews
have completed without degradation. Round three reviewed `92e259f` and found
30 Medium issues, no High/Critical issues. Code corrections and integrated
validation are complete; the owner approved round-three X1's normative
audit-retention clarification. The three-round review exit is satisfied, not
integrated Gate 1 approval. After passing CI and human-approved merge, the next
dependency-ready batch is Phase 1C, starting with OPS-02.

## Gate 1: Foundation and security

Scope: [Gate 1](../../plans/stewardship/overall.md#review-gate-1-foundation-and-security).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G1.01 — Prepare coherent signed commits and pass gate-specific validation.
- [x] G1.02 — Obtain two independent reviews of the complete gate diff.
- [ ] G1.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G1.04 — Repeat validation, the phase demonstration, and independent review.
- [ ] G1.05 — Resolve all validated Critical/High/Medium findings; document Low deferrals.
- [ ] G1.06 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: the [Phase 1C review ledger](../../guides/stewardship-phase-1c-reviews.md)
records the cumulative Phase 1 review from the Phase 0 merged handoff `509245d`.
Both vendors completed round two, including all Pika-generated shards, without
degradation. Corrections, the third round, final validation and human release
approval remain open. This gate does not authorize Phase 2 yet.

## Phase 2: Setup and source truth

Scope: [Phase 2](../../plans/stewardship/overall.md#phase-2-source-truth-initial-setup-and-campaign-preparation).

- [ ] M2.01 — Demonstrate empty deployment through a configured Testing campaign.
- [ ] M2.02 — Prove YAML/database agreement and safe installer/wizard interruption.
- [ ] M2.03 — Demonstrate atomic full/delta refresh and stable campaign identities.
- [ ] M2.04 — Preview each Family page/email without creating live fulfillment.
- [ ] M2.05 — Complete the focused import/setup correction pass before the Family slice.

Evidence: Not started.

## Phase 3: Family response

Scope: [Phase 3](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).

- [ ] M3.01 — Demonstrate the minimal login-to-submit-to-repeat-visit vertical slice.
- [ ] M3.02 — Exercise every enabled-module combination and all Family response fields.
- [ ] M3.03 — Prove in-memory drafts, atomic submission, and stale/duplicate protection.
- [ ] M3.04 — Demonstrate upstream merge, proposal provenance, and follow-up supersession.
- [ ] M3.05 — Complete representative mobile/accessibility/privacy evidence and Gate 2.

Evidence: Not started.

## Gate 2: Data, privacy, and Family UX

Scope: [Phase 3 and Gate 2](../../plans/stewardship/overall.md#phase-3-complete-family-response-vertical-slice).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G2.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G2.02 — Obtain two independent reviews of the complete gate diff.
- [ ] G2.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G2.04 — Repeat validation, the phase demonstration, and independent review.
- [ ] G2.05 — Resolve all validated Critical/High/Medium findings; document Low deferrals.
- [ ] G2.06 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 4: Production scheduling and mail

Scope: [Phase 4](../../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).

- [ ] M4.01 — Demonstrate Testing routing and operational-alert classification.
- [ ] M4.02 — Prove resumable Testing cleanup and atomic Production activation.
- [ ] M4.03 — Exercise scheduled mail, receipts, and digests through outages and retries.
- [ ] M4.04 — Verify unknown delivery, pause/resume, and close-during-pause behavior.
- [ ] M4.05 — Recheck real service queue/mount needs using fake-backed integrations.

Keep live dispatch and final Production activation within the environments
permitted by the master plan until Gate 3 is complete.

Evidence: Not started.

## Phase 5: Reporting and staff workflows

Scope: [Phase 5](../../plans/stewardship/overall.md#phase-5-reports-exports-users-and-follow-up).

- [ ] M5.01 — Demonstrate all report formats and historical campaign selection.
- [ ] M5.02 — Verify Family-code, financial, and assigned-Ministry access boundaries.
- [ ] M5.03 — Prove shared values across web, charts, exports, and pinned digests.
- [ ] M5.04 — Deny queued/download access after role or assignment revocation.
- [ ] M5.05 — Complete Gate 3 before beginning publication and destructive workflows.

Evidence: Not started.

## Gate 3: Async processing, RBAC, and reports

Scope: [Gate 3](../../plans/stewardship/overall.md#review-gate-3-async-processing-rbac-and-reporting).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G3.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G3.02 — Obtain two independent reviews of the complete gate diff.
- [ ] G3.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G3.04 — Repeat validation, the phase demonstration, and independent review.
- [ ] G3.05 — Resolve all validated Critical/High/Medium findings; document Low deferrals.
- [ ] G3.06 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 6: Reconciliation and campaign completion

Scope: [Phase 6](../../plans/stewardship/overall.md#phase-6-reconciliation-and-post-campaign-operations).

- [ ] M6.01 — Demonstrate proposal review, conflicts, manual outcomes, and partial publication.
- [ ] M6.02 — Prove source fencing, verification, and safe retry of unresolved entities.
- [ ] M6.03 — Restore an isolated backup and exercise every release/pointer state.
- [ ] M6.04 — Demonstrate reopen, post-close mail resolution, archive, and Return to Testing.
- [ ] M6.05 — Exercise purge gates, rollback limits, resume, cleanup, and tombstones on disposable data.
- [ ] M6.06 — Complete Gate 4 before release hardening can declare these workflows ready.

Evidence: Not started.

## Gate 4: External writes and destructive workflows

Scope: [Gate 4](../../plans/stewardship/overall.md#review-gate-4-external-writes-and-destructive-workflows).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G4.01 — Prepare coherent signed commits and pass gate-specific validation.
- [ ] G4.02 — Obtain independent transaction/migration and security/authorization reviews.
- [ ] G4.03 — Run authorized isolated restore/purge exercises and boundary failure injection.
- [ ] G4.04 — Triage findings, implement corrections, and add regression coverage.
- [ ] G4.05 — Repeat validation, demonstrations, and independent review after corrections.
- [ ] G4.06 — Resolve every validated Critical/High/Medium finding; document Low deferrals.
- [ ] G4.07 — Record reviewed SHA, evidence, and human approval before phase release.

Evidence: Not started.

## Phase 7: Release readiness

Scope: [Phase 7](../../plans/stewardship/overall.md#phase-7-production-hardening-and-release-readiness).

- [ ] M7.01 — Close every subsystem task and acceptance traceability entry.
- [ ] M7.02 — Complete supported-browser, scale, coverage, and accessibility verification.
- [ ] M7.03 — Validate production images and exercise the documented operational runbooks.
- [ ] M7.04 — Collect authorized human-run integration smoke-test evidence.
- [ ] M7.05 — Complete retention/privacy review and operator/developer handoff documents.
- [ ] M7.06 — Demonstrate the full disposable campaign lifecycle from production images.
- [ ] M7.07 — Complete Gate 5 before release artifact publication.

Evidence: Not started.

## Gate 5: Release readiness

Scope: [Gate 5](../../plans/stewardship/overall.md#review-gate-5-release-readiness).
Apply the complete [review protocol](../../plans/stewardship/overall.md#review-gate-protocol).

- [ ] G5.01 — Prepare the complete review diff and pass all required release validation.
- [ ] G5.02 — Obtain two independent reviews of the complete implementation.
- [ ] G5.03 — Triage findings, implement corrections, and add regression coverage.
- [ ] G5.04 — Repeat full validation and independent review of the corrected diff.
- [ ] G5.05 — Resolve release blockers and record all permitted Low deferrals.
- [ ] G5.06 — Record reviewed SHA and human product/security/operations approval.
- [ ] G5.07 — Complete the normal PR handoff and separately authorized release procedure.

Evidence: Not started.
