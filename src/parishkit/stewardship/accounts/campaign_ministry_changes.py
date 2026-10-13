"""A live campaign's Ministry change: what may be added, its impact and audit.

A live campaign's structural settings are locked, except its Ministry
selections (#342, ``campaigns/live_ministries``). Campaign settings leaves the
Ministry list open while the campaign is scheduled or active (#1119) and
reviews a change of it in the shared review dialog. Its review counts what a
change touches, and confirming it records an ordinary configuration request
plus an audit event naming the Administrator, the selections before and after,
and the Ministries added and removed. Nothing here deletes or rewrites an
answer: a removed Ministry's requests stay in reports and follow-up, marked as
no longer in the campaign, and show unmarked again if it is added back.
"""

from django.utils import timezone

from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.source.version_models import SnapshotMinistry
from parishkit.stewardship.workflows.models import MinistryRequest

# A live Ministry change's previews are signed apart from the page's other
# changes, so only they carry and record the audit companion (``audit``).
SALT = "stewardship-live-ministries-preview-v1"
# Request states that still need Staff follow-up, as in Ministry follow-up.
OPEN_REQUEST_STATES = ("new", "assigned", "in_progress")


def addable(choices, source, selected):
    """Keep current selections plus Ministries ParishSoft still lists.

    The settings catalog offers every locally active snapshot Ministry, even
    one marked "Not in ParishSoft data". The installer and SQL refuse to add
    such a Ministry to a live campaign, so the live list never offers it.
    """
    present = (
        set()
        if source is None or source.snapshot_id is None
        else {
            str(int(row.source_key))
            for row in SnapshotMinistry.objects.filter(
                snapshot_id=source.snapshot_id
            ).select_related("payload")
            if row.payload.payload.get("catalog_present") is True
        }
    )
    keep = present | {str(duid) for duid in selected}
    return [row for row in choices if row[0] in keep]


def impact(campaign, duids):
    """What changing these Ministries touches: open forms and retained answers.

    Every open Family form lists every offered Ministry, so the number of
    Families with a form open now is the same for each changed Ministry; each
    of them is asked to review their form before submitting. Per Ministry,
    ``answers`` counts current join and stop requests (not ones a Family later
    replaced or withdrew), and ``open`` those still awaiting Staff follow-up.
    """
    open_forms = (
        FamilyFormBaseline.objects.filter(
            family__campaign_id=campaign.pk,
            mode="live",
            state="open",
            expires_at__gt=timezone.now(),
        )
        .values("family_id")
        .distinct()
        .count()
    )
    requests = MinistryRequest.objects.filter(
        submission__campaign_id=campaign.pk,
        submission__mode="live",
        ministry_duid__in=duids,
    ).exclude(state__in=("cancelled", "superseded"))
    counts = {duid: {"answers": 0, "open": 0} for duid in duids}
    for duid, state in requests.values_list("ministry_duid", "state"):
        counts[duid]["answers"] += 1
        counts[duid]["open"] += state in OPEN_REQUEST_STATES
    return open_forms, counts


def review(campaign, names, before, after):
    """The impact review of changing ``before`` to ``after`` (sorted DUIDs).

    ``names`` maps each offered DUID (a string) to its display name. Returns
    the Ministries to add and to remove, each with its counts, the Families
    with a form open now, and the audit companion the signed preview carries.
    """
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    open_forms, counts = impact(campaign, added + removed)

    def rows(duids):
        """One review row per changed Ministry, with its display name and counts."""
        return [
            {"duid": duid, "name": names.get(str(duid), duid), **counts[duid]}
            for duid in duids
        ]

    return {
        "added": rows(added),
        "removed": rows(removed),
        "open_forms": open_forms,
    }, {
        "campaign_id": str(campaign.pk),
        "before": list(before),
        "after": list(after),
        "added": added,
        "removed": removed,
    }


def audit(request, extra):
    """Record who asked for which change, in the request's own transaction."""
    record_action(
        Action.CAMPAIGN_MINISTRIES_REQUESTED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=request.actor_id,
        subject_id=request.pk,
        campaign_id=extra["campaign_id"],
        current_parish=True,
        context={
            "previous_ministry_duids": extra["before"],
            "ministry_duids": extra["after"],
            "added_ministry_duids": extra["added"],
            "removed_ministry_duids": extra["removed"],
        },
    )
