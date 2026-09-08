# Campaign domain tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/campaign-domain.md) ·
[Normative specification](../../specs/stewardship/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## DOM-01: Domain vocabulary and decision records

Scope and dependencies: [DOM-01 work package](../../plans/stewardship/campaign-domain.md#dom-01-domain-vocabulary-and-decision-records).

- [x] DOM-01.01 — Record the foundational architecture decisions.
- [x] DOM-01.02 — Define canonical domain enums and value objects.
- [x] DOM-01.03 — Test stable serialization and invalid states.
- [x] DOM-01.04 — Document persisted contracts and migration boundaries.

Evidence: Implementation commit `9cb45a5` on `pr/stewardship-implementation`
(`feat: define stewardship domain vocabulary`), following ARC-01 commit `fb3b25c`.
See the [decision index](../../../src/parishkit/stewardship/DECISIONS.md) and
[`test_domain.py`](../../../tests/stewardship/test_domain.py).
Host validation on September 7, 2026: `python -m pytest` passed 407 tests;
`python -m pytest tests/stewardship --cov=parishkit.stewardship --cov-fail-under=80`
passed 81 tests with 96.17% scoped coverage and 100% for `campaigns.domain`.
Ruff check/format and Markdown checks passed. DST resolution and lifecycle
authorization remain DOM-02 work; these are pure values, not admission policy.

## DOM-02: Campaign interval and lifecycle policy

Scope and dependencies: [DOM-02 work package](../../plans/stewardship/campaign-domain.md#dom-02-campaign-interval-and-lifecycle-policy).

- [ ] DOM-02.01 — Implement the canonical UTC interval resolver and DST rules.
- [ ] DOM-02.02 — Define the lifecycle transition registry.
- [ ] DOM-02.03 — Centralize access, work-admission, and lifecycle predicates.
- [ ] DOM-02.04 — Enforce exact date boundaries despite scheduler lag.
- [ ] DOM-02.05 — Test transitions, races, DST, and lifecycle invariants.

Evidence: Not started.

## DOM-03: Authorization capability policy

Scope and dependencies: [DOM-03 work package](../../plans/stewardship/campaign-domain.md#dom-03-authorization-capability-policy).

- [ ] DOM-03.01 — Implement named capability policies from the role matrix.
- [ ] DOM-03.02 — Implement role implication and object scopes.
- [ ] DOM-03.03 — Implement report-column privacy policies.
- [ ] DOM-03.04 — Integrate policies across views, jobs, exports, and audit.
- [ ] DOM-03.05 — Test every role, scope, and revocation combination.

Evidence: Not started.

## DOM-04: Shared presentation and client contracts

Scope and dependencies: [DOM-04 work package](../../plans/stewardship/campaign-domain.md#dom-04-shared-presentation-and-client-contracts).

- [ ] DOM-04.01 — Build shared responsive and accessible UI components.
- [ ] DOM-04.02 — Implement date, currency, number, and percentage formatters.
- [ ] DOM-04.03 — Prepare interface strings for localization.
- [ ] DOM-04.04 — Define browser and progressive-enhancement contracts.
- [ ] DOM-04.05 — Test formatters and accessible components.

Evidence: Not started.

## DOM-05: Cross-domain acceptance harness

Scope and dependencies: [DOM-05 work package](../../plans/stewardship/campaign-domain.md#dom-05-cross-domain-acceptance-harness).

- [ ] DOM-05.01 — Build cross-domain database factories.
- [x] DOM-05.02 — Build deterministic clock and timezone scenario helpers.
- [ ] DOM-05.03 — Map acceptance scenarios to executable tests and owners.
- [x] DOM-05.04 — Track coverage of every specification section and package.
- [ ] DOM-05.05 — Execute and close the complete acceptance matrix.

Evidence: Phase 0 clock/traceability commit `b5bdc01` on
`pr/stewardship-implementation`. See the [acceptance guide](../../development/stewardship-acceptance.md)
and [ownership manifest](../../development/stewardship-acceptance.yaml).
[`clock.py`](../../../src/parishkit/stewardship/clock.py) supplies an injectable
UTC contract and explicit IANA-zone projection; the test-only ManualClock
advances elapsed UTC without sleeps. Ten tests cover UTC selection, naive input
rejection, nonnegative advancement, spring gaps, autumn folds, and different
parish/browser local dates. The traceability test matches every specification
section, all 69 work packages, and all ten normative acceptance scenarios to
declared owners. New headings/packages and stale test references fail validation.

September 7, 2026: the complete host coverage runner passed 614 tests (3 opt-in
Docker checks skipped), with 96.99% scoped lines and 95.29% scoped branches.
Ruff and Markdown checks passed. DOM-05.01 remains deferred to DAT-01 for real
database factories/transaction clock integration. DOM-05.03 has owners but no
implemented end-to-end scenario nodes; all ten scenarios are explicitly
`planned`, not falsely covered by helper tests. Add executable scenario evidence
with each vertical slice and complete DOM-05.05 in Phase 7.
