"""Named temporary content remains owned by the original first-campaign draft."""

from .content_forms import EMAIL_LABELS, page_slots
from .setup_campaign import admit_campaign_values


def draft_campaign(request, service, attempt_id):
    """Reuse current source binding and original-owner validation before reading."""
    from .setup_drafts import view_draft

    draft = view_draft(request, service, attempt_id)
    selected = draft.sections.get("campaign")
    if draft.status.state != "collecting" or selected is None:
        raise LookupError("Save the first campaign before preparing its content.")
    admit_campaign_values(request, service, attempt_id, selected)
    return draft, selected["campaign"]


def content_label(campaign, kind, slot):
    """Disabled page introductions remain stored but cannot be newly selected."""
    labels = (
        page_slots(campaign)
        if kind == "page"
        else EMAIL_LABELS
        if kind == "email"
        else {}
    )
    if slot not in labels:
        raise LookupError("This first-campaign content slot is unavailable.")
    return labels[slot]


def admit_content_values(request, service, attempt_id, step, record):
    """An opaque revision cannot be imported from another campaign or named slot."""
    _, campaign = draft_campaign(request, service, attempt_id)
    kind, _, slot = step.partition("_")
    content_label(campaign, kind, slot)
    value = record["values"]
    if value is not None and value["campaign_id"] != str(attempt_id):
        raise ValueError("Content belongs to the original setup campaign.")
    # A saved revision is temporary, not a previously applied ContentVersion.
    # Finalization assigns this attempt's stable UUID to its first campaign.
