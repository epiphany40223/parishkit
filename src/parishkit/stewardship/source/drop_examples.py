"""Example Families of a refused large ParishSoft change (ADM-13 PR 5c, #530).

When a full refresh is refused because a count fell too far, the
Administrator should check the change in ParishSoft before accepting it once.
Counts alone do not say where to look, so the worker records a few example
Families with the refusal: for each failing count that concerns Families,
Members or contacts, up to ``EXAMPLE_LIMIT`` Families that the current
promoted data counts but the refused load no longer does (for a Member or
contact count, the Family the lost record belonged to).

Kept deliberately small, because these are parish data:

- Only the Family's DUID and its name as the active parishioner family
  directory shows it (``snapshot_family_names``), from the current promoted
  data. Never an address, email, phone or Member identifier, nor which
  record was lost.
- The refused load's identifiers (``counted_keys``) travel with the refusal
  in memory only; the worker compares them with the current data in the
  refusal's own transaction (``record_drop_examples``) and keeps nothing
  else from the refused load.
- One SQL function, ``stewardship_source_drop_example_attempt_v1()``, names
  the one refusal whose examples may still be shown: the newest refusal,
  with no full refresh promoted since, not accepted, and less than seven
  days old. The page and the command read only that refusal's examples
  (``shown_examples``), and the worker's hourly maintenance deletes every
  other example (``prune_drop_examples``), so the two cannot disagree.
- They never go in a log line, an audit context, an event or a notice; the
  command line prints their DUIDs without names.

The table and its guard are ``schema/migrations/0085_drop_examples.sql``.
"""

import logging

from django.db import connection, transaction

from .count_names import DERIVED_COUNTS
from .source_status import NAME_NOT_RECORDED

# The counts that get example Families, in check order: Families, Members and
# contacts. Ministry, roster and fund counts have none.
EXAMPLE_MEASURES = ("family", "member", *DERIVED_COUNTS)
# At most this many example Families per failing count (decision 4).
EXAMPLE_LIMIT = 5
# The longest stored name; a longer directory name is cut short.
NAME_LIMIT = 300
# Names a Family whose record has no surname, in the shared words (#1216).
UNNAMED = NAME_NOT_RECORDED
# The one refusal whose examples may be shown (see the module docstring).
SHOWN_ATTEMPT_SQL = "SELECT public.stewardship_source_drop_example_attempt_v1()"


def _valid_email(row):
    """Whether a contact row holds at least one valid email address."""
    return any(email.get("valid") is True for email in row.get("emails") or ())


def counted_keys(corpus):
    """The keys each example count counted in one corpus, by measure.

    ``loading.derived_counts`` is the size of these same sets, so a count and
    its examples cannot be defined differently. Keys only: Family and Member
    keys are DUID strings, contact keys ``member:<DUID>`` or ``family:<DUID>``.
    A corpus read without Members (``loading.derived_counts``'s Family and
    contact rows) counts none.
    """
    families = corpus["family"]
    return {
        "family": frozenset(families),
        "member": frozenset(corpus.get("member", ())),
        "portal_eligible_families": frozenset(
            key for key, row in families.items() if row.get("portal_eligible") is True
        ),
        "email_eligible_families": frozenset(
            key for key, row in families.items() if row.get("email_eligible") is True
        ),
        "active_head_families": frozenset(
            key for key, row in families.items() if row.get("active_head_duids")
        ),
        "valid_email_contacts": frozenset(
            key for key, row in corpus["contact"].items() if _valid_email(row)
        ),
    }


def _family_of(measure, key, baseline):
    """The Family key a lost record of ``measure`` belongs to, or None.

    A Member's Family is its ``family_key``; a contact's is its owner Family,
    or its owner Member's Family. Both are read from the current data.
    """
    if measure == "member":
        return baseline["member"].get(key, {}).get("family_key")
    if measure == "valid_email_contacts":
        row = baseline["contact"].get(key, {})
        if row.get("owner_kind") == "family":
            return row.get("owner_key")
        return baseline["member"].get(row.get("owner_key"), {}).get("family_key")
    return key


def _duid(key):
    """A Family key as its positive DUID, or None when it is not one."""
    text = str(key)
    return int(text) if text.isascii() and text.isdigit() and int(text) > 0 else None


def choose_examples(measures, baseline, refused):
    """Pick each measure's example Family DUIDs, lowest first.

    ``measures`` are the failing counts that get examples; ``baseline`` is
    the current data (``_baseline``, shaped like a corpus), and ``refused``
    the refused load's ``counted_keys``. A Family is an example when one of
    its records counted in the baseline but not in the refused load. Lowest
    DUIDs first, so the same refusal always gives the same examples.
    """
    counted = counted_keys(baseline)
    chosen = {}
    for measure in measures:
        lost = counted[measure] - refused.get(measure, frozenset())
        duids = {_duid(_family_of(measure, key, baseline)) for key in lost}
        duids.discard(None)
        if duids:
            chosen[measure] = sorted(duids)[:EXAMPLE_LIMIT]
    return chosen


# The current promoted data's facts the example counts read, in one
# statement (one MVCC view), for the snapshot the caller read as current:
# each Family's eligibility flags and whether it has active heads, each
# Member's Family, and each contact with a valid email and its owner.
# Nothing else is read (booleans and keys, never names or addresses). A
# snapshot that is not promoted, or is compacted, returns nothing.
_BASELINE_SQL = """
WITH snapshot AS (
    SELECT s.id FROM stewardship_source_snapshot s
    WHERE s.id=%(snapshot)s AND s.state='promoted' AND s.compacted_at IS NULL
)
SELECT 'family', f.source_key,
       p.canonical::jsonb->'portal_eligible'='true'::jsonb,
       p.canonical::jsonb->'email_eligible'='true'::jsonb,
       CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                 AND jsonb_array_length(p.canonical::jsonb->'active_head_duids')>0
            THEN 'heads' ELSE '' END
  FROM stewardship_snapshot_family f
  JOIN stewardship_source_family p ON p.id=f.payload_id
 WHERE f.snapshot_id=(SELECT id FROM snapshot)
UNION ALL
SELECT 'member', m.source_key, NULL, NULL, p.family_key
  FROM stewardship_snapshot_member m
  JOIN stewardship_source_member p ON p.id=m.payload_id
 WHERE m.snapshot_id=(SELECT id FROM snapshot)
UNION ALL
SELECT 'contact', c.source_key, NULL, NULL,
       (p.canonical::jsonb->>'owner_kind')||':'||(p.canonical::jsonb->>'owner_key')
  FROM stewardship_snapshot_contact c
  JOIN stewardship_source_contact p ON p.id=c.payload_id
 WHERE c.snapshot_id=(SELECT id FROM snapshot)
   AND jsonb_typeof(p.canonical::jsonb->'emails')='array'
   AND EXISTS (SELECT 1 FROM jsonb_array_elements(p.canonical::jsonb->'emails') e
               WHERE e->'valid'='true'::jsonb)
"""


def _row(kind, portal, email, last):
    """One corpus-shaped row of ``_BASELINE_SQL``'s facts.

    ``last`` (text in every branch, so the union has one type) is
    ``heads`` for a Family with active heads (else empty), a Member's Family
    key, or a contact's ``owner_kind:owner_key``. Only the fields ``counted_keys`` and
    ``_family_of`` read are built.
    """
    if kind == "family":
        return {
            "portal_eligible": portal is True,
            "email_eligible": email is True,
            "active_head_duids": last == "heads",
        }
    if kind == "member":
        return {"family_key": last}
    owner_kind, _, owner_key = (last or "").partition(":")
    return {
        "owner_kind": owner_kind,
        "owner_key": owner_key,
        "emails": [{"valid": True}],
    }


def _baseline():
    """The current promoted data's example facts, shaped like a corpus.

    Returns ``(snapshot_id, corpus)``, or ``(None, None)`` when there is no
    usable current snapshot.
    """
    from .models import SourceCurrent

    snapshot_id = (
        SourceCurrent.objects.filter(singleton=True)
        .values_list("snapshot_id", flat=True)
        .first()
    )
    if snapshot_id is None:
        return None, None
    corpus = {"family": {}, "member": {}, "contact": {}}
    with connection.cursor() as cursor:
        cursor.execute(_BASELINE_SQL, {"snapshot": snapshot_id})
        for kind, key, portal, email, last in cursor.fetchall():
            corpus[kind][key] = _row(kind, portal, email, last)
    if not corpus["family"]:
        return None, None
    return snapshot_id, corpus


def example_measures(checks):
    """The failing counts of a refusal that get examples, in check order.

    None for a load with no Families or no Members: it is refused at any
    setting and can never be accepted, so there is nothing to check.
    """
    if any(
        check.measure in {"family", "member"} and check.after == 0 for check in checks
    ):
        return ()
    return tuple(
        check.measure
        for check in checks
        if check.failed and check.measure in EXAMPLE_MEASURES
    )


def record_drop_examples(attempt_id, checks, refused, *, task_id=None):
    """Record a refusal's example Families; return how many were recorded.

    Runs in the refusal's own transaction (``failures.settle_failed_read``),
    after its drop counts, which the table's guard requires. ``refused`` is
    the refused load's ``counted_keys`` (empty when the refusal carries
    none). Written under a savepoint: if anything here fails, the refusal
    and its counts still commit without examples, and one WARNING names
    only the failure's category (``emit_failure``), never a Family.
    """
    from .drop_models import SourceDropExample
    from .snapshot_names import snapshot_family_names

    measures = example_measures(checks)
    if not refused or not measures:
        return 0
    try:
        with transaction.atomic():
            snapshot_id, baseline = _baseline()
            if baseline is None:
                return 0
            chosen = choose_examples(measures, baseline, refused)
            names = snapshot_family_names(
                snapshot_id, {duid for duids in chosen.values() for duid in duids}
            )
            rows = [
                SourceDropExample(
                    attempt_id=attempt_id,
                    measure=measure,
                    position=position,
                    family_duid=duid,
                    family_name=(names.get(duid) or UNNAMED)[:NAME_LIMIT],
                )
                for measure, duids in chosen.items()
                for position, duid in enumerate(duids, start=1)
            ]
            SourceDropExample.objects.bulk_create(rows)
    except Exception as error:  # noqa: BLE001 - the refusal must still commit
        from ..observability import emit_failure

        emit_failure(error, level=logging.WARNING, task_id=task_id, name_class=True)
        return 0
    return len(rows)


def shown_examples(attempt_id):
    """The example Families of ``attempt_id``'s refusal that may be shown.

    Maps each measure to its ``(family_duid, family_name)`` pairs in order;
    empty unless ``attempt_id`` is the one refusal whose examples may still
    be shown (``SHOWN_ATTEMPT_SQL``), so an example whose deletion is due is
    never shown, even before housekeeping deletes it.
    """
    from .drop_models import SourceDropExample

    with connection.cursor() as cursor:
        cursor.execute(SHOWN_ATTEMPT_SQL)
        shown = cursor.fetchone()[0]
    if attempt_id is None or shown != attempt_id:
        return {}
    examples = {}
    for measure, duid, name in (
        SourceDropExample.objects.filter(attempt_id=attempt_id)
        .order_by("measure", "position")
        .values_list("measure", "family_duid", "family_name")
    ):
        examples.setdefault(measure, []).append((duid, name))
    return {measure: tuple(pairs) for measure, pairs in examples.items()}


def prune_drop_examples():
    """Delete every example Family that may no longer be shown; return how many.

    The worker's hourly maintenance runs it inside its fenced effect. It
    keeps only the examples of the one refusal ``SHOWN_ATTEMPT_SQL`` names,
    so a superseded, promoted-over, accepted or week-old refusal's examples
    go. Idempotent; the table holds at most a few dozen rows.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM public.stewardship_source_drop_example WHERE attempt_id "
            "IS DISTINCT FROM public.stewardship_source_drop_example_attempt_v1()"
        )
        return cursor.rowcount
