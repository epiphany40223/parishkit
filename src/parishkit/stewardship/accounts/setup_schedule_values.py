"""Closed temporary schedules and coherent cross-section first-campaign edits."""

from copy import deepcopy
from uuid import UUID

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.configuration import validate_campaign_sections
from parishkit.stewardship.web.refusals import UserFacingError

from .content_schema import validate_content_records
from .setup_content_values import CONTENT_STEPS, without_retired

# Why an email cannot be cleared, by the kind of schedule that sends it.
SCHEDULE_USES = {
    "initial": _("This email is used by your Initial invitation schedule."),
    "reminder": _("This email is used by a Reminder schedule."),
}


def validate_schedule_step(value):
    """Reject malformed/big inventories before consulting owned campaign evidence."""
    if (
        type(value) is not dict
        or set(value) != {"records"}
        or type(value["records"]) is not list
        or len(value["records"]) > 100
    ):
        raise ValueError("Invalid setup schedules.")
    seen = set()
    for row in value["records"]:
        if (
            type(row) is not dict
            or set(row) != {"id", "values"}
            or type(row["id"]) is not str
            or str(UUID(row["id"])) != row["id"]
            or row["id"] in seen
            or type(row["values"]) is not dict
        ):
            raise ValueError("Invalid setup schedule identity.")
        seen.add(row["id"])
    return deepcopy(value)


def reconcile_preparation(request, service, attempt_id, updates):
    """Keep content consumers and simultaneous date/schedule changes atomic.

    Draft schedules never own delivery work. Their immutable template selections
    still need reconciliation before any future configuration can be compiled.
    This runs inside the original attempt's save transaction, so an invalid
    replacement cannot leave either half of the edit behind.
    """
    from .setup_content import draft_campaign

    if not ({"campaign", "schedules", *CONTENT_STEPS} & updates.keys()):
        return updates
    from .setup_drafts import view_draft

    draft = view_draft(request, service, attempt_id)
    if "campaign" not in draft.sections:
        # The first structural save may carry only its default content with it
        # (no schedules); its source admission is owned by save_sections.
        # Every other child edit requires that original saved parent.
        if "campaign" not in updates or not set(updates) <= {
            "campaign",
            *CONTENT_STEPS,
        }:
            from .setup_content import first_campaign_missing

            raise first_campaign_missing()
        if set(updates) == {"campaign"}:
            return updates
    if "campaign" not in updates:
        draft_campaign(request, service, attempt_id)
    # A retired step left in an older draft is never re-validated or carried
    # into the content checked below (validate_content_records refuses it).
    combined = without_retired(draft.sections) | updates
    records = deepcopy(combined.get("schedules", {"records": []})["records"])
    for step in CONTENT_STEPS:
        if step not in updates:
            continue
        old = draft.sections.get(step)
        affected = [
            row
            for row in records
            if old and row["values"]["template_version"] == old["id"]
        ]
        if affected and updates[step]["values"] is None:
            raise UserFacingError(
                SCHEDULE_USES.get(
                    affected[0]["values"]["kind"],
                    _("This email is used by a mail schedule."),
                ),
                fix=_(
                    "Change that schedule to another email, or remove it, under "
                    "Dates and mail schedules first. Replacing the text (for "
                    "example, resetting it to the default) keeps the schedule."
                ),
                link=reverse("admin:setup_schedules"),
                link_label=_("Go to “Dates and mail schedules”"),
            )
        for row in affected:
            row["values"].update(
                template_version=updates[step]["id"],
                subject=updates[step]["values"]["subject"],
            )
    content = [
        combined[step] for step in CONTENT_STEPS if combined.get(step, {}).get("values")
    ]
    document = {
        "sections": {
            "campaigns": [
                {"id": str(attempt_id), "values": combined["campaign"]["campaign"]}
            ],
            "content": content,
            "schedules": records,
        }
    }
    validate_campaign_sections(document)
    validate_content_records(document)
    selected = {row["id"] for row in content}
    if any(row["values"]["template_version"] not in selected for row in records):
        raise UserFacingError(
            _("A mail schedule uses an email template that is not saved."),
            fix=_("Choose an email saved under Pages and emails."),
            link=reverse("admin:setup_content"),
            link_label=_("Go to “Pages and emails”"),
        )
    if records != combined.get("schedules", {"records": []})["records"]:
        updates = updates | {"schedules": {"records": records}}
    return updates
