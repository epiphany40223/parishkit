"""Synthetic immutable content records for pure and PostgreSQL tests."""

from uuid import uuid4

from .campaign_factory import campaign
from .configuration_factory import configuration_document
from .policy_factory import address


def content(owner, *, kind="page", slot="welcome", **overrides):
    """Each invocation allocates a fresh revision rather than mutating a slot ID."""
    return {
        "id": str(uuid4()),
        "values": {
            "campaign_id": owner,
            "kind": kind,
            "slot": slot,
            "subject": None if kind == "page" else "{{ parish_name }} campaign",
            "html": "<p>Welcome to {{ parish_name }}.</p>",
            "text": "Welcome to {{ parish_name }}.",
            **overrides,
        },
    }


def content_document():
    """Complete synthetic authority with a selected, referenced welcome revision."""
    document = configuration_document()
    owner = campaign()
    revision = content(owner["id"])
    owner["values"]["content_versions"]["welcome"] = revision["id"]
    document["sections"].update(
        login_rules=[address()], campaigns=[owner], content=[revision]
    )
    return document
