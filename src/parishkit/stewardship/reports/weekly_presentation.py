"""Bounded protected presentation of selected weekly inputs and current status."""

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.models import AdditionalInformationItem

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
    statuses = dict(
        AdditionalInformationItem.objects.filter(
            pk__in=[value.item_id for value in visible],
            submission__campaign_id=snapshot.campaign_id,
            submission__mode="live",
        ).values_list("pk", "disposition")
    )
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
                "captured": DISPOSITIONS[captured],
                "current": DISPOSITIONS[current],
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
    return {
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
        "report_url": reverse("admin:weekly_digest_snapshot", args=[snapshot.pk]),
    }
