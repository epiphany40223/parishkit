"""Resolve requested roster state only from the fenced promoted Family corpus."""

from django.db.models import F

from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.workflows.models import MinistryRequest

from .baselines import _pin_admission
from .ministry_requests import ACTIONABLE_STATES


def reconcile_ministry_requests(snapshot, corpus, *, campaign_id):
    """Roster proof is independent of local visibility, but never of ownership.

    Hidden requests retain their intent and may legitimately be completed in
    the source system. Missing catalog/Member rows, a moved Member and a local
    proposed UUID supply no completion proof. No provider request is made here.
    """
    require_work_order()
    memberships = {
        (row["member_key"], row["ministry_key"])
        for row in corpus["roster"].values()
        if row["current"]
    }
    rows = MinistryRequest.objects.filter(
        submission__campaign_id=campaign_id,
        entity_kind="member",
        state__in=ACTIONABLE_STATES,
    ).annotate(family_duid=F("submission__family__family_duid"))
    count = 0
    for row in rows.iterator(chunk_size=500):
        member = corpus["member"].get(row.entity_key)
        ministry = corpus["ministry"].get(str(row.ministry_duid))
        if (
            member is None
            or member["family_key"] != str(row.family_duid)
            or not member["active"]
            or member["deceased"]
            or ministry is None
            or not ministry["catalog_present"]
        ):
            continue
        current = (row.entity_key, str(row.ministry_duid)) in memberships
        if current != (row.action == "join"):
            continue
        pin_snapshot(
            snapshot.pk,
            parent_kind="submission",
            parent_id=row.submission_id,
            admit=_pin_admission,
        )
        MinistryRequest.objects.filter(pk=row.pk).update(
            state="resolved",
            outcome="joined" if current else "leave_confirmed",
            resolved_at=_now(),
            resolution_source=snapshot,
            version=F("version") + 1,
        )
        count += 1
    return count
