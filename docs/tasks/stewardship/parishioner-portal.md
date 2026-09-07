# Parishioner portal tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/parishioner-portal.md) ·
[Normative specification](../../specs/stewardship/parishioner-portal/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## FAM-01: Availability, code entry, and secure-link exchange

Scope and dependencies: [FAM-01 work package](../../plans/stewardship/parishioner-portal.md#fam-01-availability-code-entry-and-secure-link-exchange).

- [ ] FAM-01.01 — Build all campaign-availability and code-entry states.
- [ ] FAM-01.02 — Integrate code/token login and clean session exchange.
- [ ] FAM-01.03 — Enforce Testing acknowledgement and banners.
- [ ] FAM-01.04 — Gate every Family route by eligibility, interval, mode, and epoch.
- [ ] FAM-01.05 — Audit Family access and maintain presence metadata.
- [ ] FAM-01.06 — Test access, Testing, token, and lifecycle denial cases.

Evidence: Not started.

## FAM-02: In-memory form engine and navigation

Scope and dependencies: [FAM-02 work package](../../plans/stewardship/parishioner-portal.md#fam-02-in-memory-form-engine-and-navigation).

- [ ] FAM-02.01 — Build the accessible mobile multi-step form shell.
- [ ] FAM-02.02 — Prefill the merged Family model and change indicators.
- [ ] FAM-02.03 — Keep drafts in memory and warn on loss or expiry.
- [ ] FAM-02.04 — Integrate validation, idle warning, and activity keepalive.
- [ ] FAM-02.05 — Test navigation, lost drafts, expiry, and mobile focus.

Evidence: Not started.

## FAM-03: Family census step

Scope and dependencies: [FAM-03 work package](../../plans/stewardship/parishioner-portal.md#fam-03-family-census-step).

- [ ] FAM-03.01 — Build Family identity, addresses, and email opt-out fields.
- [ ] FAM-03.02 — Apply shared census validation and normalization.
- [ ] FAM-03.03 — Implement mailing-same-as-home with preservation.
- [ ] FAM-03.04 — Build changed-field and review summaries.
- [ ] FAM-03.05 — Test invalid addresses and disabled census.

Evidence: Not started.

## FAM-04: Existing and proposed Member steps

Scope and dependencies: [FAM-04 work package](../../plans/stewardship/parishioner-portal.md#fam-04-existing-and-proposed-member-steps).

- [ ] FAM-04.01 — Build existing-Member census sections.
- [ ] FAM-04.02 — Implement moved-household and deceased semantics.
- [ ] FAM-04.03 — Build proposed-Member add, edit, and removal.
- [ ] FAM-04.04 — Apply terminal-aware mandatory-field rules.
- [ ] FAM-04.05 — Test Member fields, dates, terminal choices, and removal.

Evidence: Not started.

## FAM-05: Ministry and financial stewardship steps

Scope and dependencies: [FAM-05 work package](../../plans/stewardship/parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps).

- [ ] FAM-05.01 — Build current, join, and leave Ministry controls.
- [ ] FAM-05.02 — Apply campaign Ministry selection boundaries.
- [ ] FAM-05.03 — Build financial aggregates, pledge, and installment calculation.
- [ ] FAM-05.04 — Build share options and zero/one/many Member wording.
- [ ] FAM-05.05 — Show financial periods and unavailable source data.
- [ ] FAM-05.06 — Test Ministry, financial, and terminal-Member interactions.

Evidence: Not started.

## FAM-06: Additional information, review, and atomic submit

Scope and dependencies: [FAM-06 work package](../../plans/stewardship/parishioner-portal.md#fam-06-additional-information-review-and-atomic-submit).

- [ ] FAM-06.01 — Build the optional additional-information field.
- [ ] FAM-06.02 — Build complete response review and attestation.
- [ ] FAM-06.03 — Implement atomic Submit with Family-specific source concurrency.
- [ ] FAM-06.04 — Render Thank You, queue receipt, and invalidate session.
- [ ] FAM-06.05 — Preserve in-memory edits through errors and refreshed-baseline review.
- [ ] FAM-06.06 — Enforce Testing acknowledgement at final submission.
- [ ] FAM-06.07 — Test duplicate, concurrent-response, source-promotion, and interrupted submissions.

Evidence: Not started.

## FAM-07: Repeat visits and source-change merge

Scope and dependencies: [FAM-07 work package](../../plans/stewardship/parishioner-portal.md#fam-07-repeat-visits-and-source-change-merge).

- [ ] FAM-07.01 — Prefill repeat visits and prior submission time.
- [ ] FAM-07.02 — Integrate source merge without misattributing Admin edits.
- [ ] FAM-07.03 — Explain caught-up, pending, and conflicting changes.
- [ ] FAM-07.04 — Supersede derived work only upon final Submit.
- [ ] FAM-07.05 — Test repeated census, information, and Ministry changes.

Evidence: Not started.

## FAM-08: Responsive, accessibility, privacy, and browser completion

Scope and dependencies: [FAM-08 work package](../../plans/stewardship/parishioner-portal.md#fam-08-responsive-accessibility-privacy-and-browser-completion).

- [ ] FAM-08.01 — Exercise the supported device and browser matrix.
- [ ] FAM-08.02 — Complete automated and manual accessibility checks.
- [ ] FAM-08.03 — Verify exclusion of form answers from persistent client data.
- [ ] FAM-08.04 — Test slow networks, expiry, delays, and server failures.
- [ ] FAM-08.05 — Measure maximum-size forms and correct UX regressions.

Evidence: Not started.
