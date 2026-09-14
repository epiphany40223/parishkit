# Member household requests increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-04 plan](../plans/stewardship/parishioner-portal.md#fam-04-existing-and-proposed-member-steps) ·
[Portal specification](../specs/stewardship/parishioner-portal/spec.md#existing-member-census) ·
[Proposal semantics](../specs/stewardship/data/spec.md#proposed-changes)

## Boundary

Branch `pr/stewardship-member-requests` starts from PR #25's verified merge,
`eeb346342fe7c78bd4295801f6800dc5f6ec44fb`. Complete the remaining FAM-04
terminal and proposed-Member flow, including guarded complete submissions,
manual semantic proposals, repeat visits and mobile editing/review. The linked
specifications control the fields, confirmation and independent death-date
handling; this guide does not redefine them.

Ministry selections for proposed Members follow with FAM-05. No provider write,
automatic upstream Member creation, deployment or release is included. Gate 2
remains open for the remaining enabled-module flows and integrated acceptance.

## Internal checkpoints

1. Add pure terminal/proposed-Member validation and regression cases, retaining
   the closed existing household identity set and local UUID identities.
2. Extend the complete aggregate, effective presentation, proposal lifecycle,
   source dependencies and independent SQL reconstruction together. Audit a
   fresh database against the exact starting baseline without upgrading or
   deleting retained development databases.
3. Integrate confirmation, terminal-aware validation, proposed-Member add/edit/
   removal, complete review and stale-form edit preservation in tab memory.
4. Pass unit, exact-role database and real-browser tests, complete at least
   three dual-model review/correction rounds, then pass final-head CI and the
   normal protected merge queue before continuing.

## Evidence

Scope established; implementation and acceptance are in progress. No remaining
FAM-04 task is claimed complete yet. PR #25's closure is recorded in the
[ordinary Member guide](stewardship-member-census.md).
