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
    if step == "page_submission_confirmation":
        # Retired (#260): the step name stays in the frozen schema, but no
        # closing note can be staged any more.
        with pytest.raises(ConfigError):
            validate_values(step, record)
        return
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


def test_default_updates_distinguish_never_set_cleared_and_saved():
    """Automatic fills keep clears; the button refills them; reset replaces all."""
    from parishkit.stewardship.accounts.content_forms import (
        applicable_slots,
        matches_default,
    )
    from parishkit.stewardship.accounts.setup_content import (
        FILL_ALL,
        FILL_EMPTY,
        FILL_UNSET,
        default_updates,
    )

    from .campaign_factory import campaign

    attempt = uuid4()
    values = campaign()["values"]
    steps = {f"{kind}_{slot}" for kind, slot in applicable_slots(values)}
    everything = default_updates({}, values, attempt, which=FILL_UNSET)
    assert set(everything) == steps and len(steps) == 11 + 6
    assert all(
        matches_default(row["values"])
        and row["values"]["campaign_id"] == str(attempt)
        and set(validate_values(step, row)) == {"id", "values"}
        for step, row in everything.items()
    )
    mine = content(str(attempt), slot="welcome")
    sections = everything | {
        "page_welcome": mine,
        "page_review": {"id": None, "values": None},
    }
    del sections["email_initial"]
    assert set(default_updates(sections, values, attempt, which=FILL_UNSET)) == {
        "email_initial"
    }
    assert set(default_updates(sections, values, attempt, which=FILL_EMPTY)) == {
        "email_initial",
        "page_review",
    }
    # A reset leaves slots that already hold exactly their default alone.
    assert set(default_updates(sections, values, attempt, which=FILL_ALL)) == {
        "email_initial",
        "page_review",
        "page_welcome",
    }
    with pytest.raises(ValueError):
        default_updates({}, values, attempt, which="other")


def test_fill_result_parameters_are_closed():
    """Only one complete, bounded filled/reset pair is accepted."""
    from django.http import QueryDict

    from parishkit.stewardship.accounts.setup_content_views import _filled, result_url

    assert _filled(QueryDict("")) is None
    assert _filled(QueryDict("filled_pages=3&filled_emails=0")) == {
        "action": "filled",
        "pages": 3,
        "emails": 0,
    }
    assert _filled(QueryDict("reset_pages=1&reset_emails=2"))["action"] == "reset"
    for query in (
        "filled_pages=1",
        "filled_pages=1&reset_emails=1",
        "filled_pages=x&filled_emails=1",
        "filled_pages=100&filled_emails=1",
        "other=1",
    ):
        with pytest.raises(ValueError):
            _filled(QueryDict(query))
    assert result_url("filled", ["page_welcome", "email_initial", "page_review"]) == (
        "/admin/setup/content?filled_pages=2&filled_emails=1"
    )
    assert result_url("reset", ["page_welcome", "email_confirmation"]) == (
        "/admin/setup/content?reset_pages=1&reset_emails=1"
    )
