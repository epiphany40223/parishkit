"""Named temporary content remains owned by the original first-campaign draft."""

from uuid import uuid4

from .content_forms import EMAIL_LABELS, applicable_slots, default_values, page_slots
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


# Which slots default_updates() writes. A slot is "never set" while the draft
# has no section row for it at all; an Admin's explicit clear stores the
# {"id": None, "values": None} marker instead, so the two stay distinguishable.
FILL_UNSET = "unset"  # Only never-set slots: automatic fills keep a clear.
FILL_EMPTY = "empty"  # Never-set and explicitly cleared slots (the fill button).
FILL_ALL = "all"  # Every applicable slot, replacing saved text (reset all).


def default_updates(sections, campaign, attempt_id, *, which):
    """Setup draft updates that put default text into the chosen applicable slots.

    ``sections`` are the draft's current sections and ``campaign`` the
    first-campaign values whose enabled modules decide which page slots
    apply. Pages of disabled modules are never touched. Each default goes
    through the normal editor validation (``default_values``). A reset skips
    slots that already hold exactly the default text, so it creates no
    needless revision for them.
    """
    if which not in {FILL_UNSET, FILL_EMPTY, FILL_ALL}:
        raise ValueError("Unknown default fill.")
    updates = {}
    for kind, slot in applicable_slots(campaign):
        step = f"{kind}_{slot}"
        record = sections.get(step)
        values = default_values(kind, slot, campaign_id=attempt_id)
        if record is not None and (
            (which == FILL_UNSET)
            or (which == FILL_EMPTY and record["values"])
            or record["values"] == values
        ):
            continue
        updates[step] = {"id": str(uuid4()), "values": values}
    return updates


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


def admit_content_values(request, service, attempt_id, step, record, campaign=None):
    """An opaque revision cannot be imported from another campaign or named slot.

    ``campaign`` is the first-campaign values being saved in the same edit, if
    any, which the caller has already admitted. Content saved together with
    its campaign (the automatic default fill) is checked against those new
    values rather than the not-yet-written previous ones.
    """
    if campaign is None:
        _, campaign = draft_campaign(request, service, attempt_id)
    kind, _, slot = step.partition("_")
    content_label(campaign, kind, slot)
    value = record["values"]
    if value is not None and value["campaign_id"] != str(attempt_id):
        raise ValueError("Content belongs to the original setup campaign.")
    # A saved revision is temporary, not a previously applied ContentVersion.
    # Finalization assigns this attempt's stable UUID to its first campaign.
