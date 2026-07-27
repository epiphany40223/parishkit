# Stewardship implementation plans

These plans break the Stewardship/Census application into bounded work packages
that another Codex can implement. Begin with the
[overall implementation plan](overall.md); it defines ordering, integration
milestones, and mandatory review gates. Each subsystem plan expands the work
package IDs used there.

| Plan | Normative specification | Responsibility |
| --- | --- | --- |
| [Campaign domain](campaign-domain.md) | [Root stewardship spec](../../specs/stewardship/spec.md) | Cross-cutting lifecycle, roles, terminology, and presentation contracts |
| [Architecture](architecture.md) | [Architecture spec](../../specs/stewardship/architecture/spec.md) | Django foundation, security, sessions, secrets, and service boundaries |
| [Data](data.md) | [Data spec](../../specs/stewardship/data/spec.md) | Models, migrations, constraints, merge, reconciliation, and retention |
| [Admin portal](admin-portal.md) | [Admin portal spec](../../specs/stewardship/admin-portal/spec.md) | Setup, configuration, roles, campaign operations, logs, and purge |
| [Family portal](parishioner-portal.md) | [Family portal spec](../../specs/stewardship/parishioner-portal/spec.md) | Family authentication, form flow, validation, and submission |
| [Background processing](background-processing.md) | [Background spec](../../specs/stewardship/background-processing/spec.md) | Durable jobs, refresh, scheduling, mail, exports, and publication |
| [Reports](reports.md) | [Reports spec](../../specs/stewardship/reports/spec.md) | Calculations, report UI, charts, workflow views, and exports |
| [Operations](operations.md) | [Operations spec](../../specs/stewardship/operations/spec.md) | Compose, deployment, backup/restore, observability, CI, and acceptance |

The plans intentionally link to specifications instead of repeating field-level
requirements. Completing a work package means implementing every applicable
normative requirement in its linked specification section, its tests, and its
operational documentation.

## Execution convention

For each work package, the implementing Codex must:

1. Read the linked specification sections and dependent completed packages.
2. Inspect existing shared ParishKit helpers before adding new abstractions.
3. Implement schema/code/templates/static assets and migrations as applicable.
4. Add unit, integration, authorization, browser, and operational tests named by
   the package.
5. Run the narrow test set while iterating and the repository validation suite
   before marking the package complete.
6. Update operator/developer documentation and the plan status in the same
   change when behavior or sequencing intentionally changes.

Do not substitute mocks, screenshots, or unchecked migrations for a completed
vertical behavior. External services remain fake-backed in normal CI; real-
credential checks belong in documented human-run smoke tests.
