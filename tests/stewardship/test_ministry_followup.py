"""Closed Ministry follow-up change grammar, validated without a database."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.workflows.followup import (
    MAX_CONTACT_NOTES,
    MAX_NOTES,
    WorkflowChange,
)

WHO, WHEN = uuid4(), datetime(2026, 9, 20, 12, tzinfo=UTC)
BASE = dict(assignee_id=None, state="in_progress", outcome=None, notes="")


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"state": "new"},
        {"state": "assigned", "assignee_id": WHO},
        {"assignee_id": WHO, "notes": "n" * MAX_NOTES},
        {"state": "resolved", "outcome": "joined"},
        {"state": "resolved", "outcome": "other", "notes": "Moved parishes"},
        {"state": "closed_no_response", "outcome": "no_response"},
        {"contact_channel": "phone", "contact_at": WHEN},
        {
            "contact_channel": "in_person",
            "contact_at": WHEN,
            "contact_notes": "c" * MAX_CONTACT_NOTES,
        },
    ],
)
def test_complete_workflow_is_accepted(values):
    """Every Staff-owned state, outcome pairing and contact shape round-trips."""
    change = WorkflowChange(**(BASE | values))
    assert all(getattr(change, key) == value for key, value in values.items())


@pytest.mark.parametrize(
    "values",
    [
        # Family- and worker-owned states are never a Staff edit.
        {"state": "cancelled"},
        {"state": "superseded"},
        {"state": "unknown"},
        # An outcome belongs to exactly its closed state.
        {"outcome": "joined"},
        {"state": "resolved"},
        {"state": "resolved", "outcome": "no_response"},
        {"state": "closed_no_response", "outcome": "declined"},
        {"state": "resolved", "outcome": "invented"},
        {"state": "resolved", "outcome": "other"},
        {"state": "resolved", "outcome": "other", "notes": "   "},
        # Assignment follows the state.
        {"state": "new", "assignee_id": WHO},
        {"state": "assigned"},
        {"assignee_id": str(WHO)},
        # A contact attempt is complete, zoned, bounded and from a closed set.
        {"contact_channel": "phone"},
        {"contact_at": WHEN},
        {"contact_notes": "Notes without an attempt"},
        {"contact_channel": "fax", "contact_at": WHEN},
        {"contact_channel": "phone", "contact_at": WHEN.replace(tzinfo=None)},
        {"contact_channel": "phone", "contact_at": "2026-09-20"},
        {
            "contact_channel": "phone",
            "contact_at": WHEN,
            "contact_notes": "c" * (MAX_CONTACT_NOTES + 1),
        },
        {"notes": "n" * (MAX_NOTES + 1)},
        {"notes": "nul\x00byte"},
        {"notes": None},
    ],
)
def test_incomplete_or_foreign_workflow_is_rejected(values):
    """A malformed form is a client error before any lock or query."""
    with pytest.raises(ValueError):
        WorkflowChange(**(BASE | values))
