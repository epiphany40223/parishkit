"""Copy only configuration structures into a new, explicitly dated draft.

The caller supplies an admitted archived source and a signed new campaign ID.
No Family, response, delivery, runtime, or fund record is read by this module.
Deterministic child IDs keep a form retry stable without reusing history IDs.
"""

from copy import deepcopy
from uuid import UUID, uuid5

from .campaign_forms import initial_fields
from .content_schema import RETIRED_ALERT
from .receipt_note import fold, legacy_note


def clone_structures(document, source, target):
    """Detach content, share choices and civil schedules from their old owner."""
    namespace = UUID(str(target))

    def identifier(kind, value):
        """Separate record types even if an old document reused a UUID across them."""
        return str(uuid5(namespace, f"{kind}:{value}"))

    sections = document["sections"]
    content = [
        {
            "id": identifier("content", row["id"]),
            "values": deepcopy(row["values"]) | {"campaign_id": str(target)},
        }
        for row in sections.get("content", [])
        if row["values"]["campaign_id"] == str(source["id"])
        # A clone's records are newly authored, and a retired, never-sent
        # alert template (#913) cannot be, so it is left behind.
        and (row["values"]["kind"], row["values"]["slot"]) != RETIRED_ALERT
    ]
    content = _fold_note(content, target)
    previous = {
        "modules": list(source["values"]["modules"]),
        "share_options": [
            deepcopy(row) | {"id": identifier("share", row["id"])}
            for row in source["values"]["share_options"]
        ],
        "content_versions": {
            slot: identifier("content", reference)
            for slot, reference in source["values"]["content_versions"].items()
        },
    }
    schedules = [
        {
            "id": identifier("schedule", row["id"]),
            "values": {
                name: deepcopy(row["values"][name])
                for name in ("kind", "time", "weekday", "subject")
            }
            | {
                "campaign_id": str(target),
                "date": None,
                "template_version": identifier(
                    "content", row["values"]["template_version"]
                ),
            },
        }
        for row in sections.get("schedules", [])
        if row["values"]["campaign_id"] == str(source["id"])
    ]
    return previous, content, schedules


def _fold_note(content, target):
    """Carry a retired receipt closing note inside the cloned confirmation email.

    A clone's records are newly authored, and a new closing note is refused
    (receipt_note), so the note's text moves into the confirmation email
    instead of being dropped. The email keeps its cloned ID when it exists.
    """
    note = legacy_note(content, target)
    if note is None:
        return content
    email = next(
        (
            row
            for row in content
            if (row["values"]["kind"], row["values"]["slot"])
            == ("email", "confirmation")
        ),
        None,
    )
    # The folded email takes the email's place, or the note's without one.
    target_row = email or note
    folded = {
        "id": target_row["id"],
        "values": fold(
            email["values"] if email else None, note["values"], campaign_id=target
        ),
    }
    return [
        folded if row is target_row else row
        for row in content
        if row is target_row or row is not note
    ]


def clone_initial(source, *, digest, timezone, ministries):
    """Retain module choices, but require new names, dates and current fund mapping."""
    values = initial_fields(source["values"], digest=digest)
    values.update(
        name="",
        year_label="",
        timezone=timezone,
        start_date=None,
        end_date=None,
        financial_start=None,
        financial_end=None,
        comparison_start=None,
        comparison_end=None,
        fund_duids=[],
        comparison_fund_duids=[],
        overlap_confirmed=False,
        ministry_duids=[key for key, _ in ministries]
        if "ministry" in source["values"]["modules"]
        else [],
    )
    return values


def clone_patch(target, values, content, schedules):
    """Create only fresh records, preserving explicitly deleted schedule choices."""
    rows = {row["id"]: deepcopy(row["values"]) for row in schedules.previous}
    for operation in schedules.patch():
        if operation["operation"] == "remove":
            rows.pop(operation["id"])
        else:
            rows[operation["id"]] = operation["values"]
    return [
        {
            "operation": "add",
            "section": "campaigns",
            "id": str(target),
            "values": values,
        },
        *({"operation": "add", "section": "content", **row} for row in content),
        *(
            {"operation": "add", "section": "schedules", "id": key, "values": value}
            for key, value in sorted(rows.items())
        ),
    ]
