# Stewardship implementation tasks

Start with the [top-level task execution plan](overall.md) for coordinated
execution across the checklists. It links to the controlling
[overall implementation plan](../../plans/stewardship/overall.md) for full phase
scope and dependencies. The eight
lists contain 371 implementation tasks across all 69 planned work packages.
The [milestone checklist](milestones.md) tracks integrated demonstrations and
the five required review-and-correction gates separately.

| Task list | Specification | Work packages | Implementation tasks |
| --- | --- | --- | --- |
| [Campaign domain](campaign-domain.md) | [Root specification](../../specs/stewardship/spec.md) | DOM-01 through DOM-05 | 24 |
| [Architecture](architecture.md) | [Architecture](../../specs/stewardship/architecture/spec.md) | ARC-01 through ARC-08 | 48 |
| [Data and reconciliation](data.md) | [Data](../../specs/stewardship/data/spec.md) | DAT-01 through DAT-09 | 48 |
| [Administration portal](admin-portal.md) | [Admin portal](../../specs/stewardship/admin-portal/spec.md) | ADM-01 through ADM-10 | 55 |
| [Parishioner portal](parishioner-portal.md) | [Family portal](../../specs/stewardship/parishioner-portal/spec.md) | FAM-01 through FAM-08 | 44 |
| [Background processing](background-processing.md) | [Background processing](../../specs/stewardship/background-processing/spec.md) | BG-01 through BG-11 | 59 |
| [Reports and exports](reports.md) | [Reports](../../specs/stewardship/reports/spec.md) | RPT-01 through RPT-09 | 45 |
| [Operations and quality](operations.md) | [Operations](../../specs/stewardship/operations/spec.md) | OPS-01 through OPS-09 | 48 |

## Execution and completion

1. Read the normative specification, the owning plan item, and its dependencies
   before implementing a task. Specifications own behavior; plans own scope and
   sequencing; these files own task completion status. Short task labels are
   navigation aids, not abridged acceptance criteria.
2. A task such as `DAT-02.04` maps to item 4 of work package DAT-02. Read the
   complete item, including continuations and linked requirements. Implement
   the complete deliverable and applicable tests, migrations, and documentation.
3. Execute only the package or portion admitted by the current master-plan
   phase. Numeric task order within a file is not the global execution order.
   In particular, DOM-02.01 precedes DAT-02's interval constraints; the rest of
   DOM-02 integrates afterward. Packages such as DOM-05, ARC-08, ADM-06, RPT-08,
   and OPS-08/OPS-09 span phases. Leave a partially delivered task unchecked and
   record completed portions and remaining scope in its package evidence.
4. Mark a task complete only when its deliverable and relevant verification
   pass. Documentation of intended behavior, a placeholder, or a stub is not
   implemented functionality. All checkboxes begin unchecked; existing shared
   helpers may satisfy part of a task after their integration is verified.
5. Replace each package's `Evidence: Not started.` line with implementation
   commit/PR references, test commands/results, remaining phase-specific scope,
   and blockers where applicable. Use task IDs when identifying partial work.
   Link evidence rather than committing generated reports, logs, or credentials.
6. A package is complete only when all its tasks satisfy the master's
   [definition of done](../../plans/stewardship/overall.md#package-definition-of-done).
   Complete the corresponding demonstration and review gate before progressing
   to a phase blocked by that gate. Record gate evidence in [milestones](milestones.md).
7. Preserve task IDs once implementation starts. If the specification or plan
   changes, update its task mapping in the same change; document splits or
   replacements explicitly instead of silently renumbering completed work.

## Handoff to an implementing Codex

Provide the overall plan, this index, the active phase, and the selected task
IDs. The implementer reads the linked sources, checks dependency evidence,
implements a reviewable increment, verifies it, and updates completion status
and evidence. Shared helpers remain under `src/parishkit`; the repository's
branch, sign-off, validation, and credential-free CI rules apply.

The checklists do not authorize deployment, external messages or writes, purge
against retained parish data, merge, or release. Follow the existing authority
and review requirements in the overall plan for those actions.
