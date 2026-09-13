"""Temporary named content uses the same canonical parser as activated content."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.setup_content_values import (
    CONTENT_STEPS,
    validate_content_step,
)
from parishkit.stewardship.accounts.setup_forms import validate_values

from .content_factory import content


@pytest.mark.parametrize("step", CONTENT_STEPS)
def test_content_slots_and_clear_markers(step):
    """Every named slot is typed, copied and explicitly removable."""
    kind, _, slot = step.partition("_")
    record = content(str(uuid4()), kind=kind, slot=slot)
    assert validate_values(step, record) == record
    assert validate_values(step, record) is not record
    assert validate_values(step, {"id": None, "values": None}) == {
        "id": None,
        "values": None,
    }


@pytest.mark.parametrize(
    "invalid",
    ["id", "slot", "kind", "html", "text", "campaign", "extra", "shape"],
)
def test_invalid_temporary_content_is_rejected(invalid):
    """Executable markup and unrelated identities do not become staged selections."""
    record = content(str(uuid4()))
    if invalid == "id":
        record["id"] = "not-a-revision"
    elif invalid == "shape":
        record["values"] = []
    elif invalid == "extra":
        record["extra"] = True
    else:
        record["values"][
            {
                "slot": "slot",
                "kind": "kind",
                "html": "html",
                "text": "text",
                "campaign": "campaign_id",
            }[invalid]
        ] = {
            "slot": "review",
            "kind": "email",
            "html": '<p onclick="unsafe()">Hello</p>',
            "text": "{{ private_value }}",
            "campaign": "not-a-campaign",
        }[invalid]
    with pytest.raises((ValueError, ConfigError)):
        validate_content_step("page_welcome", record)


def test_unknown_content_step_is_rejected():
    with pytest.raises(ValueError):
        validate_content_step("page_unknown", content(str(uuid4())))
