"""Bounded protected presentation of selected weekly inputs and current status."""

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.models import AdditionalInformationItem

from .digest_funnel import digest_funnel, funnel_context
from .weekly_capture import retained_selection
from .weekly_digest import WeeklyInformation, excerpt
from .weekly_models import WeeklyManualRequest

PAGE_SIZE = 50
DISPOSITIONS = {
    "current_actionable": "Current actionable request",
    "superseded": "Superseded by a later response",
    "withdrawn": "Withdrawn by the Family",
}


def page_number(query, *, detail=False):
    """Allow one small canonical page integer, never arbitrary report filtering."""
    if detail:
        if query:
            raise ValueError("Detail links do not accept query parameters.")
        return 1
    if set(query) - {"page"} or len(query.getlist("page")) > 1:
        raise ValueError("Unknown or repeated report parameters.")
    value = query.get("page", "1")
    if not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 7:
        raise ValueError("Invalid report page.")
    number = int(value)
    if number < 1 or str(number) != value:
        raise ValueError("Invalid report page.")
    return number


def snapshot_context(snapshot, *, page=1, item_id=None):
    """Read current dispositions only for selected rows, under the read barrier.

    The list pages also count the response funnel at the capture instant.

    Captured actionable text remains historical, never rewritten with a newer
    response. Correction captures contain no old text and this view does not
    retrieve it from the live item table. Every detail link must select an item
    belonging to this report's selected subset, not merely its source observation.
    """
    selection = retained_selection(snapshot)
    values = (*selection.information, *selection.corrections)
    pages = max(1, (len(values) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        raise ObjectDoesNotExist("Report page is unavailable.")
    if item_id is not None:
        visible = tuple(value for value in values if value.item_id == item_id)
        if not visible:
            raise ObjectDoesNotExist("This item is not part of the report.")
    else:
        visible = values[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    # Each request's current disposition and, for its Family name's Timeline
    # link (#1570), its Family's record id, in the same one read.
    statuses, families = {}, {}
    # The Timeline serves only the current campaign, so an earlier campaign's
    # retained snapshot shows plain names, not links that would be refused.
    linkable = (
        SystemConfiguration.objects.values_list("current_campaign_id", flat=True).get()
        == snapshot.campaign_id
    )
    for pk, disposition, family_id in AdditionalInformationItem.objects.filter(
        pk__in=[value.item_id for value in visible],
        submission__campaign_id=snapshot.campaign_id,
        submission__mode="live",
    ).values_list("pk", "disposition", "submission__family_id"):
        statuses[pk], families[pk] = disposition, family_id
    rows = []
    for value in visible:
        current = statuses.get(value.item_id)
        if current not in DISPOSITIONS:
            raise ReadUnavailable("A selected request is unavailable.")
        information = isinstance(value, WeeklyInformation)
        captured = "current_actionable" if information else value.disposition
        rows.append(
            {
                "value": value,
                "family_id": families.get(value.item_id) if linkable else None,
                "captured": DISPOSITIONS[captured],
                "current": DISPOSITIONS[current],
                # The stored keys, for the command line's ``digest weekly``
                # (ADM-11 PR 8d); the template shows the labels above.
                "information": information,
                "captured_key": captured,
                "current_key": current,
                "changed": current != captured,
                "actionable": current == "current_actionable",
                "text": (value.text if item_id is not None else excerpt(value.text))
                if information
                else None,
                "url": reverse(
                    "admin:weekly_digest_item", args=[snapshot.pk, value.item_id]
                ),
            }
        )
    # The response funnel at the capture instant (#477), counted again from
    # the durable timestamps the email used, so the two agree. A single
    # request's page shows only that request.
    mode = snapshot.preparation.mode
    funnel = (
        funnel_context(
            digest_funnel(snapshot.campaign_id, mode, snapshot.observed_at), mode
        )
        if item_id is None
        else funnel_context(None, None)
    )
    return {
        **funnel,
        "snapshot": snapshot,
        "manual": WeeklyManualRequest.objects.filter(
            pk=snapshot.preparation_id
        ).exists(),
        "rows": rows,
        "detail": item_id is not None,
        "information_count": len(selection.information),
        "correction_count": len(selection.corrections),
        "total": len(values),
        "page": page,
        "pages": pages,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < pages else None,
    }
