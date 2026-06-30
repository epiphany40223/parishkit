# ParishKit specifications

Design and behavior specifications for ParishKit, written so the system could be
re-created from them. They cross-reference each other; shared material lives in
one place and is linked rather than duplicated. Each specification file is named
`spec.md`.

- **[intro/spec.md](intro/spec.md)** — top-level system spec: design intent,
  goals, architecture, the shared "common code" philosophy, configuration/runtime
  model, testing/CI philosophy, versioning, and development guidelines. Start
  here.

The related ParishSoft API comparison lives one level up in the docs root:
**[../parishsoft-api-analysis.md](../parishsoft-api-analysis.md)** (v1 vs. v2,
which API ParishKit uses, the write-capability analysis, and the switch/hybrid
recommendation).

Per-tool specifications (one folder per command in `scripts/`):

- **[pk-cron-runner/spec.md](pk-cron-runner/spec.md)** — scheduler/runner.
- **[pk-sync-ps-to-ggroup/spec.md](pk-sync-ps-to-ggroup/spec.md)** — Google Group
  membership sync.
- **[pk-sync-ps-to-cc/spec.md](pk-sync-ps-to-cc/spec.md)** — Constant Contact list
  sync.
- **[pk-create-ps-ministry-rosters/spec.md](pk-create-ps-ministry-rosters/spec.md)** —
  ministry rosters into Google Drive/Sheets.
- **[pk-validate-gcalendar-reservations/spec.md](pk-validate-gcalendar-reservations/spec.md)** —
  Google Calendar reservation auditor.
- **[pk-query-ps-memfam/spec.md](pk-query-ps-memfam/spec.md)** — member/family
  lookup.
- **[pk-print-ps-ministries/spec.md](pk-print-ps-ministries/spec.md)** —
  ministry-name listing.

The `scripts/smoke-tests/` helpers are intentionally not given a per-tool spec;
the smoke-test philosophy is part of the
[intro spec's testing section](intro/spec.md#testing-ci-and-quality-philosophy).
