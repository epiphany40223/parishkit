"""``report roster``: the roster changes to enter, as counts (#463, ADM-11).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, reading the
list through its own admission and ``roster_rows`` under the campaign read
guard. The list's page, Roster changes to enter, was folded into Ministry
requests' To enter by hand Stage (R39 finding 2); the command keeps the
list's counts and its ``roster_changes_viewed`` event.
"""

from dataclasses import replace

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.reports import ministry_roster_views

from .auth_builders import signed_in
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli, parity  # noqa: F401
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_status_cli_postgresql import (
    admin,  # noqa: F401
    one,
    session,
)
from .test_roster_entries_postgresql import resolved, tick

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "roster_changes_viewed"


def with_roles(monkeypatch, roles, ministries=frozenset()):
    """Give the session's principal these roles from now on.

    Pairing needs an Administrator, so the admission's principal is changed
    afterwards, as a Staff member's or a Ministry leader's would be. The
    page's module holds its own reference to the admission, so it is
    patched there too.
    """
    real = sessions.authenticated_admin

    def changed(*arguments, **options):
        """The real admission, its principal holding only ``roles``."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset(roles), ministries=ministries)

    monkeypatch.setattr(sessions, "authenticated_admin", changed)
    monkeypatch.setattr(ministry_roster_views, "authenticated_admin", changed)


def succeeded_counts():
    """The ``count`` of every succeeded ``roster_changes_viewed`` so far."""
    return sorted(
        context["count"]
        for context in AuditContext.objects.filter(event__event_type=EVENT).values_list(
            "context", flat=True
        )
        if context["outcome"] == "succeeded"
    )


def test_the_counts_are_the_page_s_list(response_service, google, cli):  # noqa: F811
    """The list counted, with its Show and Ministry choices, its event, and
    no Member, Family or address printed."""
    harness, admin_id, join, _ = resolved(response_service)
    signed_in()
    run = cli()

    code, document = run("report", "roster")
    assert code == 0, document
    # The command shows no row.
    assert succeeded_counts() == [0]

    code, document = run("report", "roster")
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == str(harness.campaign.pk)
    assert (result["show"], result["ministry"]) == ("todo", None)
    assert (result["matching"], result["joins"], result["leaves"]) == (1, 1, 0)
    assert (result["entered"], result["replaced"]) == (0, 0)
    [ministry] = result["ministries"]
    assert ministry["ministry"] == 9 and ministry["matching"] == 1
    assert isinstance(ministry["name"], str) and ministry["name"]
    text = str(document)
    assert "@" not in text and str(join.pk) not in text

    # Marked entered: gone from the default list, counted under --show all.
    tick(harness, admin_id, join, True)
    code, document = run("report", "roster")
    assert code == 0 and document["result"]["matching"] == 0, document
    assert document["result"]["ministries"] == []
    code, document = run("report", "roster", "--show", "all")
    assert code == 0, document
    assert (document["result"]["matching"], document["result"]["entered"]) == (1, 1)
    # Another Ministry's changes only: none.
    code, document = run("report", "roster", "--show", "all", "--ministry", "4")
    assert code == 0, document
    assert document["result"]["ministry"] == 4
    assert document["result"]["matching"] == 0

    # A Ministry DUID outside the page's range is the page's refusal, with
    # nothing read or recorded.
    before = AuditEvent.objects.count()
    for duid in ("0", "2147483648"):
        code, document = run("report", "roster", "--ministry", duid)
        assert code == 1 and document["error"]["code"] == "invalid", duid
    assert AuditEvent.objects.count() == before

    # A read-only session reads it.
    reader = read_only_session(run.service)
    code, document = run("report", "roster", secret=reader)
    assert code == 0 and document["result"]["matching"] == 0, document


def test_staff_read_and_a_leader_is_denied(response_service, google, cli, monkeypatch):  # noqa: F811
    """Staff read the list, as on the page; a Ministry leader is refused,
    even for their own Ministry, with nothing printed or recorded."""
    resolved(response_service)
    signed_in()
    run = cli()
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = run("report", "roster")
        assert code == 0 and document["result"]["matching"] == 1, document
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    with_roles(monkeypatch, {"ministry_leader"}, frozenset({9}))
    code, document = run("report", "roster", "--ministry", "9")
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_an_ended_session_reads_nothing(response_service, google, cli):  # noqa: F811
    """A revoked session is exit 5, before the choice check, with nothing
    read or recorded."""
    resolved(response_service)
    signed_in()
    run = cli()
    revoke(run.service, run.secret)
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    for argv in (("report", "roster"), ("report", "roster", "--ministry", "0")):
        code, document = run(*argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    secret = session(admin)
    code, document = one(admin, "report", "roster", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()
