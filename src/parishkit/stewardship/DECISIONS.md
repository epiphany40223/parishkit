# Stewardship architecture decision index

DOM-01 records accepted contracts here; linked specifications own the rationale
and complete rules. Implementation must not infer authorization from an enum.

| Decision | Controlling specification | Code consequence |
| --- | --- | --- |
| Exactly one current campaign; successor preparation is sequential | [Lifecycle](../../../docs/specs/stewardship/spec.md#campaign-lifecycle) | One canonical `CampaignState`; DAT-02 owns transactional guards. |
| Testing and Production are separate from lifecycle and mail pause | [Lifecycle](../../../docs/specs/stewardship/spec.md#campaign-lifecycle) | `SystemMode` is independent, with no combined campaign-state enum. |
| ParishSoft remains truth with proposal overlays | [Merge](../../../docs/specs/stewardship/data/spec.md#effective-value-merge) | Distinguish baseline/current/submitted/proposed origins. |
| Manual codes are campaign-bound, low-sensitivity credentials | [Family access](../../../docs/specs/stewardship/architecture/spec.md) | Codes are not Google roles; actual credentials belong to ARC-05. |
| Submissions are immutable; reviews are separate | [Concurrency](../../../docs/specs/stewardship/data/spec.md#submission-concurrency) | Never replace Family-submitted values with unresolved Admin edits. |
| No public integration API | [Public interfaces](../../../docs/specs/stewardship/architecture/spec.md#public-interfaces) | Python helpers and browser partials are internal interfaces. |

## Persisted contracts

The string values in `campaigns.domain` and Django app labels are stable
contracts. Changing their meaning or spelling requires versioned YAML import
compatibility, explicit data migrations for persisted values, and regression
fixtures. Unknown strings fail parsing; display labels may change independently.
Database-specific review/job/outbox enums will live in their owning data models,
not in a second copy of this vocabulary.

Money is signed USD integer cents with magnitude at most $999,999,999.99 and
canonical two-place string serialization. Negative source adjustments are
representable; FAM validation separately forbids negative pledges. Percentages
serialize exact integer inputs and represent zero-denominator results as
unavailable. These encodings avoid float-driven drift; a future change to bounds
or serialization requires compatibility review of stored values and exports.

UTC intervals are half-open and normalize aware inputs to UTC. Local-day values
retain a date and campaign timezone snapshot. Empty intervals can represent a
skipped civil date; the canonical DST resolver belongs to DOM-02. The value
constructor is not an authorization or campaign interval resolver.

Python class/function names, helper signatures, and namespace organization are
internal implementation details and may be refactored with their callers.
Serialized names, timestamps, amounts, applied configuration, and migration app
labels must not silently change during such refactors. Stable serialization here
does not establish a public API.
