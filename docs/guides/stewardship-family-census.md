# Family census increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-03 scope](../plans/stewardship/parishioner-portal.md#fam-03-family-census-step) ·
[Portal specification](../specs/stewardship/parishioner-portal/spec.md#family-census)

## Starting point and boundary

Branch `pr/stewardship-family-census` starts at PR #23's verified merge,
`4051b4a5250cdbfa4a8f41d23d9fab800f252b84`. The next independently testable
outcome is the complete household census step: read-only identity/registration,
home/mailing addresses, same-as-home behavior, email opt-out, review and revisit.
Include its source projection, typed validation, atomic proposal/SQL guards,
and browser/database tests in this increment rather than separate helper PRs.

The later existing/proposed/terminal Member expansion and Ministry/financial
modules remain explicitly owned by Phase 3B's subsequent increments. This
boundary reduces review size without releasing the integrated Gate 2 or
enabling real source writes, delivery, deployment or release.

## Internal checkpoints

1. Define typed household fields, availability and country-aware validation.
2. Extend the trusted source projection and effective/proposal owners, including
   exact-role SQL provenance/state checks and source-promotion reconciliation.
3. Add the mobile household step, same-as-home preservation, changed markers,
   complete review and stale-form resolution without persisted drafts.
4. Extend the real HTTP/SQL acceptance scenario and browser regressions; audit
   the fresh-install schema, complete three dual-model review/correction rounds,
   validate and land the protected PR before the next increment.

## Source and validation constraints

The current verified loader retains a primary contact address but does not
establish separate home/mailing values, country or registration date. Do not
invent home/mailing equivalence, a default source country, or a blank source
registration date. Unsupported source values retain explicit availability;
untouched unavailable fields must not manufacture proposed updates.

The source's `sendNoMail` is not the parish-wide email opt-out. Email opt-out is
manual follow-up and never changes campaign mail eligibility. The registered
address API capability is potential future handling, not permission to publish
or proof that a complete safe write payload is currently reconstructable.

Country-aware validation follows the portal specification. US state/ZIP rules
must not be imposed on international addresses; the subsystem plan's older
US-only wording has been aligned with that existing requirement.

The country selector uses the bundled ISO data in
[pycountry 26.2.16](https://pypi.org/project/pycountry/26.2.16/), with an immutable
sorted list of two-letter codes and labels. No household address is sent to a
country lookup or postal-verification service. Nonblank addresses require a
delivery line, city/locality and an explicit country; US addresses additionally
require a supported state/territory/military abbreviation and ZIP/ZIP+4 syntax.
Region/postal fields remain bounded optional text for other countries. A fully
blank address normalizes to null; it is not a fabricated upstream address.

Email opt-out preserves null only when the trusted source is unavailable or
known-null; explicit true/false remains distinguishable. The same-as-home flag
requires two explicitly submitted matching nonblank addresses. The server does
not silently copy a partially supplied value, and the browser must separately
preserve a distinct mailing draft until its confirmation flow completes.

All changes remain fresh-install-only. Never upgrade or delete a retained
development database to make a schema or test pass.

## Evidence

The pure household validators and shared text normalization pass 209 focused
tests together with the existing response-answer/comparison regressions.
The dependency lock adds only the pinned country package; existing package
versions remain unchanged. The remaining source/SQL/UI integration is not yet
implemented, so no FAM-03 task or additional milestone is claimed complete.
PR #23's final-head CI passed every check at `c440e5b`, including
all 2,220 database tests, the 492 browser tests and both coverage floors, before
the protected merge. The [Phase 3A review ledger](stewardship-phase-3a-reviews.md)
retains the exact reviewed commits, corrections and local diagnostic evidence.
