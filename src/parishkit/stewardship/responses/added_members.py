"""Link Members a Family added on the form to their later ParishSoft record.

A Member added on the form has only the form's local UUID. Once Staff
create the person in ParishSoft and mark the New Member change Entered by
hand, a refresh shows the new Member in the Family, and the Family answers
for them under that ParishSoft DUID from then on. Each promotion records the
link between the two keys (``AddedMemberLink``) when the evidence is causal
and unique, never by matching names, email or birth dates (data spec,
"Proposed changes"; #1670). The decision is SQL's,
``stewardship_added_member_candidates_v1``, which the link table's guard
asks again, so Python only records what it returns.
"""

from django.db import connection

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .models import AddedMemberLink

CANDIDATES = (
    "SELECT family_id, proposal_id, proposed_key, member_key "
    "FROM public.stewardship_added_member_candidates_v1(%s, %s)"
)


def _candidates(snapshot_id, campaign_id):
    """The links ``snapshot_id``'s evidence supports for the campaign."""
    with connection.cursor() as cursor:
        cursor.execute(CANDIDATES, [snapshot_id, campaign_id])
        return cursor.fetchall()


def link_added_members(snapshot, *, campaign_id):
    """Record every added Member link the promoted ``snapshot`` supports.

    Runs inside promotion, after reconciliation, in its work-order
    transaction; the guard admits a link only there. Returns how many links
    were recorded. A Family gets at most one: one with two unlinked
    Entered by hand New Members, or two new ParishSoft Members, is
    ambiguous and gets none.
    """
    require_work_order()
    rows = _candidates(snapshot.pk, campaign_id)
    for family_id, proposal_id, proposed_key, member_key in rows:
        AddedMemberLink.objects.create(
            family_id=family_id,
            proposal_id=proposal_id,
            proposed_key=proposed_key,
            member_key=member_key,
            snapshot_id=snapshot.pk,
        )
    return len(rows)


def added_members_current(snapshot_id, *, campaign_id):
    """Whether promotion of a corpus equal to ``snapshot_id``'s links nobody.

    The quick update's skip check (#630): a link waiting to be recorded (a
    New Member just marked Entered by hand) makes the quick update promote.
    """
    return not _candidates(snapshot_id, campaign_id)
