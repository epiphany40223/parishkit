"""Closed temporary content slots reuse the canonical campaign content contract."""

from copy import deepcopy
from uuid import UUID

from .content_schema import EMAIL_SLOTS, PAGE_SLOTS, validate_content_records

CONTENT_STEPS = tuple(
    f"{kind}_{slot}"
    for kind, slots in (("page", PAGE_SLOTS), ("email", EMAIL_SLOTS))
    for slot in sorted(slots)
)


def validate_content_step(step, record):
    """Accept canonical selected revisions or an explicit empty selection marker."""
    if (
        step not in CONTENT_STEPS
        or type(record) is not dict
        or set(record) != {"id", "values"}
    ):
        raise ValueError("Invalid setup content slot.")
    if record == {"id": None, "values": None}:
        return deepcopy(record)
    if type(record["id"]) is not str or str(UUID(record["id"])) != record["id"]:
        raise ValueError("Invalid setup content revision.")
    values = record["values"]
    kind, _, slot = step.partition("_")
    if type(values) is not dict or (values.get("kind"), values.get("slot")) != (
        kind,
        slot,
    ):
        raise ValueError("Setup content differs from its named slot.")
    validate_content_records(
        {
            "sections": {
                "campaigns": [
                    {
                        "id": values.get("campaign_id"),
                        "values": {"content_versions": {}},
                    }
                ],
                "content": [record],
            }
        }
    )
    return deepcopy(record)
