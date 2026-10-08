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
        AUTOMATION_FRESH_FUNCTION,
        FAMILY_LOGIN_FUNCTION,
        MINISTRY_CATALOG_FUNCTION,
        runtime_functions,
    )

    assert runtime_functions(ServiceRole.CONFIG_INSTALLER) == {
        MINISTRY_CATALOG_FUNCTION
    }
    # Web also reads the 24-hour Family mail count for System health (ADM-13)
    # and checks a secret request's automation sign-in (ADM-11 PR 5).
    assert runtime_functions(ServiceRole.WEB) == {
        FAMILY_LOGIN_FUNCTION,
        "stewardship_family_daily_sends_v1()",
        AUTOMATION_FRESH_FUNCTION,
    }
    # The worker's definer routines purge ended Admin sessions (ADM-11) and
    # count an export's read-guard stops (#386).
    assert runtime_functions(ServiceRole.WORKER) == {
        "stewardship_admin_session_purge_v1(uuid[])",
        "stewardship_read_guard_kills_v1(uuid)",
        "stewardship_task_event_prune_v1(integer, integer, integer)",
    }
    for role in (ServiceRole.SCHEDULER, ServiceRole.MAIL_DISPATCH):
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


def test_every_configuration_activator_is_accounted_for():
    """The logins that can move the pointer guard, and which hold the catalog.

    Only the installer adds a Ministry to a live campaign. Setup completion
    (worker) activates a draft, bootstrap creates the singleton, and admin
    recovery changes login rules, so none reaches the guard's catalog read.
    A new activator must be checked against it (#342).
    """
    from parishkit.config import ConfigError
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.runtime_grants import (
        MINISTRY_CATALOG_FUNCTION,
        runtime_functions,
        runtime_grants,
    )

    activators = set()
    for role in ServiceRole:
        try:
            tables, _columns = runtime_grants(role)
        except ConfigError:
            continue
        if "INSERT" in tables.get("stewardship_config_activation", ()):
            activators.add(role)
    assert activators == {
        ServiceRole.WORKER,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.BOOTSTRAP,
        ServiceRole.ADMIN_RECOVERY,
    }
    holders = {
        role
        for role in activators - {ServiceRole.BOOTSTRAP}
        if MINISTRY_CATALOG_FUNCTION in runtime_functions(role)
    }
    assert holders == {ServiceRole.CONFIG_INSTALLER}
