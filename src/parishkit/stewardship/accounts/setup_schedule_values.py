"""Closed temporary schedules and coherent cross-section first-campaign edits."""

from copy import deepcopy
from uuid import UUID

from parishkit.stewardship.campaigns.configuration import validate_campaign_sections

from .content_schema import validate_content_records
from .setup_content_values import CONTENT_STEPS


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
        # First structural save has no children; its source admission is owned
        # by save_sections. Every child edit requires that original saved parent.
        if set(updates) != {"campaign"}:
            raise LookupError("Save the first campaign before its schedules.")
        return updates
    if "campaign" not in updates:
        draft_campaign(request, service, attempt_id)
    combined = draft.sections | updates
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
            raise ValueError(
                "Remove or change this template's schedules before clearing it."
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
        raise ValueError("Choose a saved first-campaign email template.")
    if records != combined.get("schedules", {"records": []})["records"]:
        updates = updates | {"schedules": {"records": records}}
    return updates
