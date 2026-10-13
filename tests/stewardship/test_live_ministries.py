"""Pure rules for changing a live campaign's Ministries (#342)."""

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.campaigns.live_ministries import live_change_admitted


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


def test_only_the_config_installer_may_read_the_catalog_function():
    """The definer catalog read is granted to one login; web keeps Family login."""
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.runtime_grants import (
        ADDRESS_RETRY_OPEN_FUNCTION,
        ADDRESS_RETRY_TOKEN_FUNCTION,
        AUTOMATION_FRESH_FUNCTION,
        DEBUG_OFF_FUNCTION,
        FAMILY_LOGIN_FUNCTION,
        LEADER_CHANGES_FUNCTION,
        LEADER_SCOPE_FUNCTION,
        MINISTRY_CATALOG_FUNCTION,
        runtime_functions,
    )

    # The installer and the worker also count the Ministry leader pairs
    # an activation or a promotion gained and lost (#1034). Every runtime
    # login reads whether the debug-off switch is set (0078, #530).
    assert runtime_functions(ServiceRole.CONFIG_INSTALLER) == {
        MINISTRY_CATALOG_FUNCTION,
        LEADER_CHANGES_FUNCTION,
        DEBUG_OFF_FUNCTION,
    }
    # Web also reads the 24-hour Family mail count and when it falls below the
    # Family limit for System health (ADM-13, #530),
    # checks a secret request's automation sign-in (ADM-11 PR 5) and reads a
    # user's role-derived Ministry scope (#922), the users upgrade
    # preview's identities and Member names, New user's Name lookup and the
    # Users page's ParishSoft leaders without a user (#952) and, for an
    # Administrator, one email's recipient addresses (#1024) and each one's
    # latest outcome and any follow-up retry (#806) and records a reported
    # bounce (#806 slice 4), and the campaign comparison report's aggregates
    # (#1018).
    assert runtime_functions(ServiceRole.WEB) == {
        FAMILY_LOGIN_FUNCTION,
        "stewardship_family_daily_sends_v1()",
        "stewardship_family_daily_resume_v1()",
        AUTOMATION_FRESH_FUNCTION,
        LEADER_SCOPE_FUNCTION,
        "stewardship_portal_identities_v1()",
        "stewardship_member_names_v1()",
        "stewardship_member_names_for_v1(text, text, uuid)",
        "stewardship_leaders_without_user_v1(text, uuid)",
        "stewardship_delivery_recipients_v1(uuid, uuid)",
        "stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid)",
        "stewardship_campaign_archive_renewal_v1(uuid, uuid, integer)",
        "stewardship_delivery_recipient_outcomes_v1(uuid, uuid)",
        "stewardship_delivery_address_retries_v1(uuid, uuid)",
        "stewardship_report_recipient_bounce_v1(uuid, uuid, text, uuid, text)",
        DEBUG_OFF_FUNCTION,
    }
    # The worker's definer routines purge ended Admin sessions (ADM-11),
    # count an export's read-guard stops (#386) and read a user's
    # role-derived Ministry scope (#922), as a download does. It also
    # reads the campaign comparison's aggregates for the daily digest's
    # "Compared with past campaigns" section (#1018).
    assert runtime_functions(ServiceRole.WORKER) == {
        "stewardship_admin_session_purge_v1(uuid[])",
        "stewardship_read_guard_kills_v1(uuid)",
        "stewardship_task_event_prune_v1(integer, integer, integer)",
        LEADER_SCOPE_FUNCTION,
        LEADER_CHANGES_FUNCTION,
        "stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid)",
        "stewardship_campaign_archive_renewal_v1(uuid, uuid, integer)",
        # The archive's parts of each live answer, for the campaign archive
        # workbook (#1018 part (a)).
        "stewardship_campaign_archive_answers_v1(uuid, timestamp with time zone)",
        # Analyze a staged snapshot's tables before the ready transition
        # (0086, #895).
        "stewardship_source_snapshot_analyze_v1(uuid)",
        # The publication plans' audit answer (0087, #528).
        "stewardship_publication_audited_v1(text, uuid, uuid, jsonb)",
        DEBUG_OFF_FUNCTION,
    }
    assert runtime_functions("download") == {LEADER_SCOPE_FUNCTION}
    # Mail dispatch reads one claimed message's link token (0073, #389) and
    # opens and re-tokens an address follow-up send (0077, #806).
    assert runtime_functions(ServiceRole.MAIL_DISPATCH) == {
        "stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid)",
        ADDRESS_RETRY_OPEN_FUNCTION,
        ADDRESS_RETRY_TOKEN_FUNCTION,
        DEBUG_OFF_FUNCTION,
    }
    assert runtime_functions(ServiceRole.SCHEDULER) == {DEBUG_OFF_FUNCTION}


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
