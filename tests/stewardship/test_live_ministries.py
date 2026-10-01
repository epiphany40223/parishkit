"""Pure rules for changing a live campaign's Ministries (#342)."""

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.campaigns.live_ministries import live_change_admitted
from parishkit.stewardship.reports.ministry_documents import activity


@pytest.mark.parametrize(
    ("state", "locked", "admitted"),
    [
        ("scheduled", True, True),
        ("active", True, True),
        # An unlocked draft edits everything on Campaign settings instead.
        ("draft", False, False),
        ("closed", True, False),
        ("archived", True, False),
    ],
)
def test_only_open_locked_campaigns_take_the_exemption(state, locked, admitted):
    """Removal and addition both stop once the campaign closes."""
    assert live_change_admitted(state, locked=locked) is admitted


def test_audit_context_accepts_the_change_lists():
    """Each list is sorted, distinct positive DUIDs, like ministry_duids."""
    context = {
        "previous_ministry_duids": [4, 9],
        "ministry_duids": [4],
        "added_ministry_duids": [],
        "removed_ministry_duids": [9],
    }
    assert sanitize(ContextKind.ACTION, context) == context
    for bad in ([9, 4], [4, 4], [0], ["4"]):
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, {"removed_ministry_duids": bad})


@pytest.mark.parametrize(
    ("item", "label"),
    [
        ({"active": True}, "Active"),
        ({"active": True, "in_campaign": True}, "Active"),
        (
            {"active": False, "in_campaign": False},
            "Inactive; no longer in this campaign",
        ),
        (
            {"active": None, "in_campaign": False},
            "Unavailable; no longer in this campaign",
        ),
    ],
)
def test_summary_export_marks_removed_ministries(item, label):
    """Captures from before the flag existed have no in_campaign key."""
    assert activity(item) == label


def test_only_the_config_installer_may_read_the_catalog_function():
    """The definer catalog read is granted to one login; web keeps Family login."""
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.runtime_grants import (
        FAMILY_LOGIN_FUNCTION,
        MINISTRY_CATALOG_FUNCTION,
        runtime_functions,
    )

    assert runtime_functions(ServiceRole.CONFIG_INSTALLER) == {
        MINISTRY_CATALOG_FUNCTION
    }
    assert runtime_functions(ServiceRole.WEB) == {FAMILY_LOGIN_FUNCTION}
    for role in (ServiceRole.WORKER, ServiceRole.SCHEDULER, ServiceRole.MAIL_DISPATCH):
        assert runtime_functions(role) == frozenset()


def test_removal_reads_no_catalog(monkeypatch):
    """Only an addition reads the catalog the installer may lack access to."""
    from types import SimpleNamespace

    from parishkit.stewardship.campaigns import live_ministries

    def unavailable(document):
        """Fail the test if the catalog is read."""
        raise AssertionError("catalog read")

    monkeypatch.setattr(live_ministries, "addable_ministries", unavailable)
    campaign = SimpleNamespace(state="active", structural_locked=True)
    live_ministries.check_live_selection(campaign, [4, 9], [4], {})
