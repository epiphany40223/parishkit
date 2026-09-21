"""The open review episodes of suspended seeds, read for the Portal users page.

Read under the page's observation lock, so the applied assignments, the
overlays, the retained evidence and the current source are one coherent
observation. The web role reads record ids, reasons, times and DUIDs here,
never contact data; names come from the promoted snapshot's own Ministry
payloads, which it reads for the Family form already.
"""

from django.db import connection

from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from .chair_models import ChairAssignmentReview, ChairSeedEvidence
from .policy_models import AssignmentOverlay, MinistryAssignment


def open_reviews(configuration, current):
    """Every open review as the row shaper expects, for the active configuration.

    A review whose seed is no longer in the active configuration is closed by
    the next reconciliation and is not shown meanwhile. Whether the retained
    Member is a current Chairperson elsewhere comes from the suggestion
    projection for the promoted snapshot.
    """
    reviews = list(
        ChairAssignmentReview.objects.filter(closed_by__isnull=True).select_related(
            "opened_by", "latest_by"
        )
    )
    if not reviews:
        return []
    identifiers = [review.assignment_record_id for review in reviews]
    assignments = {
        row.record_id: row
        for row in MinistryAssignment.objects.filter(
            configuration=configuration.active_configuration,
            record_id__in=identifiers,
            source="chair-seed",
        )
    }
    reasons = dict(
        AssignmentOverlay.objects.filter(
            assignment_record_id__in=identifiers
        ).values_list("assignment_record_id", "reason")
    )
    members = dict(
        ChairSeedEvidence.objects.filter(
            assignment_record_id__in=identifiers
        ).values_list("assignment_record_id", "member_duid")
    )
    generations = dict(
        SourceSnapshot.objects.filter(
            pk__in={review.opened_by.snapshot_id for review in reviews}
        ).values_list("id", "generation")
    )
    names, chairs = {}, set()
    if current is not None and current.snapshot_id is not None and assignments:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT (tm.source_key)::bigint,"
                " COALESCE(ministry.canonical::jsonb->>'name','')"
                " FROM stewardship_snapshot_ministry tm"
                " JOIN stewardship_source_ministry ministry"
                " ON ministry.id=tm.payload_id"
                " WHERE tm.snapshot_id=%s AND tm.source_key=ANY(%s)",
                [
                    current.snapshot_id,
                    [str(row.ministry_duid) for row in assignments.values()],
                ],
            )
            names = dict(cursor.fetchall())
            cursor.execute(
                "SELECT DISTINCT member_duid FROM stewardship_chair_suggestion"
                " WHERE snapshot_id=%s AND member_duid=ANY(%s)",
                [current.snapshot_id, [value for value in members.values()]],
            )
            chairs = {row[0] for row in cursor.fetchall()}
    rows = []
    for review in reviews:
        assignment = assignments.get(review.assignment_record_id)
        if assignment is None:
            continue
        member = members.get(review.assignment_record_id)
        rows.append(
            {
                "email": assignment.email,
                "ministry_duid": assignment.ministry_duid,
                "ministry_name": names.get(assignment.ministry_duid, ""),
                "member_duid": member,
                "reason": reasons.get(review.assignment_record_id, ""),
                "opened_at": review.opened_by.created_at,
                "generation": generations.get(review.opened_by.snapshot_id),
                "latest_at": review.latest_by.created_at,
                "elsewhere": member in chairs,
            }
        )
    return rows
