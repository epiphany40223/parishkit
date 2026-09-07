# ParishKit documentation

Project documentation for ParishKit. Operator-facing setup, install, and
scheduling instructions live in the repository [`README.md`](../README.md); this
directory holds design/analysis material.

- **[specs/](specs/README.md)** — design and behavior specifications, written so
  ParishKit could be re-created from them. Start with
  [specs/intro/spec.md](specs/intro/spec.md) (the top-level system spec), then the
  [Stewardship/Census web application spec](specs/stewardship/spec.md) or the
  per-tool specs under `specs/<tool-name>/spec.md`.
- **[plans/](plans/README.md)** — implementation work breakdowns and sequenced
  review gates derived from the specifications.
- **[parishsoft-api-analysis.md](parishsoft-api-analysis.md)** — comparison of the
  two ParishSoft API generations (v1 vs. v2), which one ParishKit uses, the
  write-capability analysis, and the switch/hybrid recommendation.
- **[reference/](reference/README.md)** — non-normative source narratives and
  development-workflow captures retained for traceability.
