"""A live campaign's end date (#912): the rule, the patch, admission, the form."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.live_end_date import (
    admit_end_edit,
    end_edit_campaign,
    live_end_editable,
)

NOW = datetime(2054, 10, 10, 12, tzinfo=UTC)
CLOSES = datetime(2054, 11, 1, 4, tzinfo=UTC)


def facts(**overrides):
    """The configuration and campaign a live end-date decision reads."""
    identifier = uuid4()
    values = {
        "current": identifier,
        "mode": "production",
        "restore": False,
        "locked": True,
        "state": "active",
        "ends_at": CLOSES,
    } | overrides
    configuration = SimpleNamespace(
        current_campaign_id=values["current"],
        mode=values["mode"],
        restore_review_required=values["restore"],
    )
    row = SimpleNamespace(
        pk=identifier,
        structural_locked=values["locked"],
        state=values["state"],
        active_configuration=SimpleNamespace(ends_at=values["ends_at"]),
    )
    return configuration, row


def test_only_the_current_live_production_campaign_before_its_close():
    """Scheduled or open, locked, current, in Production, unheld, not yet closed."""
    for state in ("scheduled", "active"):
        assert live_end_editable(*facts(state=state), held=False, now=NOW)
    assert not live_end_editable(*facts(), held=True, now=NOW)
    assert not live_end_editable(*facts(), held=False, now=CLOSES)
    for overrides in (
        {"current": uuid4()},
        {"mode": "testing"},
        {"restore": True},
        {"locked": False},
        {"state": "draft"},
        {"state": "closed"},
    ):
        assert not live_end_editable(*facts(**overrides), held=False, now=NOW)


def test_the_patch_names_the_campaign_whose_end_date_changes():
    """Only a campaign update that sets the end date is an end edit."""
    identifier = str(uuid4())

    def update(section="campaigns", operation="update", **values):
        """One patch operation."""
        return {
            "operation": operation,
            "section": section,
            "id": identifier,
            "values": values,
        }

    assert end_edit_campaign([update(end_date="2054-11-15")]) == identifier
    assert end_edit_campaign([update(section="schedules"), update(end_date="x")])
    assert end_edit_campaign([update(name="Renamed")]) is None
    assert end_edit_campaign([update("schedules", end_date="2054-11-15")]) is None
    assert end_edit_campaign([update(operation="add", end_date="x")]) is None
    assert end_edit_campaign([{"operation": "delete", "section": "campaigns"}]) is None


def test_the_installer_owns_end_edits_but_not_the_unbuilt_reopen():
    """Every end-edit action is admitted; reopen (#527) has no owner yet."""
    end, reopen = SimpleNamespace(action="edit_end"), SimpleNamespace(action="reopen")
    admit_end_edit("edit_end", None, None, None)
    for action in ("edit_end", "configuration_receipt", "abort_configuration"):
        admit_end_edit(action, None, None, end)
    for action, intent in (
        ("reopen", None),
        ("reopen", reopen),
        ("configuration_receipt", reopen),
        ("something_else", end),
    ):
        with pytest.raises(ConfigError):
            admit_end_edit(action, None, None, intent)
