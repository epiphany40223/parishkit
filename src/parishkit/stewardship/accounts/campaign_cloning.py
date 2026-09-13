"""Copy only configuration structures into a new, explicitly dated draft.

The caller supplies an admitted archived source and a signed new campaign ID.
No Family, response, delivery, runtime, or fund record is read by this module.
Deterministic child IDs keep a form retry stable without reusing history IDs.
"""

from copy import deepcopy
from uuid import UUID, uuid5

from .campaign_forms import initial_fields


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
    ]
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
