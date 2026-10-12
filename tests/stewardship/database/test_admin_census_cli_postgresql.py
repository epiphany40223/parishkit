"""``report census``: the Census changes list, as counts (#463, ADM-11).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, reading the
list through the page's own admission, filters and ``census_changes`` under
the campaign read guard. The command's counts are the page's rows counted,
and it records the page's ``census_changes_viewed`` with the same outcomes
and context members as a page view.
"""

from dataclasses import replace

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.reports import census_change_views

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli, parity  # noqa: F401
from .test_admin_status_cli_postgresql import (
    admin,  # noqa: F401
    one,
    session,
)
from .test_census_changes_postgresql import submit_live
from .test_ministry_responses_postgresql import start
from .test_report_workspace_postgresql import read

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/census/"
EVENT = "census_changes_viewed"


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
    monkeypatch.setattr(census_change_views, "authenticated_admin", changed)


def submitted(response_service):
    """A live response with an automatic Member rename and a By-hand address."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    return harness


def succeeded_counts():
    """The ``count`` of every succeeded ``census_changes_viewed`` so far."""
    return sorted(
        context["count"]
        for context in AuditContext.objects.filter(event__event_type=EVENT).values_list(
            "context", flat=True
        )
        if context["outcome"] == "succeeded"
    )


def test_the_counts_are_the_page_s_list(response_service, google, cli):  # noqa: F811
    """The list counted as the page lists it, with its filters, the page's
    event, and no Member, Family, address or answer printed."""
    harness = submitted(response_service)
    browser, _ = signed_in()
    run = cli()

    parity(EVENT, lambda: read(browser, ROUTE), lambda: run("report", "census"))
    # The page recorded the two rows it showed an Administrator; the
    # command shows none.
    assert succeeded_counts() == [0, 2]

    code, document = run("report", "census")
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == str(harness.campaign.pk)
    assert result["filters"]["status"] == "open" and result["filters"]["history"] == ""
    # The default view is the work to do: for an Administrator, the
    # automatic rename to review and the address to enter by hand.
    assert result["matching"] == 2
    assert result["routes"] == {"automatic": 1, "by_hand": 1}
    assert result["kinds"] == {
        "contact": 2,
        "moved": 0,
        "deceased": 0,
        "new_member": 0,
    }
    assert (result["statuses"]["to_review"], result["statuses"]["to_do"]) == (1, 1)
    assert "superseded" not in result["statuses"]
    text = str(document)
    for private in ("Requested", "Census Way", "@"):
        assert private not in text, private

    # The page's filters narrow the list; the summary stays the campaign's.
    code, document = run("report", "census", "--status", "all", "--route", "by_hand")
    assert code == 0, document
    assert document["result"]["matching"] == 1
    assert document["result"]["routes"] == {"automatic": 0, "by_hand": 1}
    assert document["result"]["statuses"]["to_review"] == 1
    code, document = run("report", "census", "--status", "conflict", "--history")
    assert code == 0 and document["result"]["matching"] == 0, document
    assert document["result"]["statuses"]["superseded"] == 0
    code, document = run(
        "report",
        "census",
        "--status",
        "all",
        "--start",
        "2000-01-01",
        "--zone",
        "America/New_York",
    )
    assert code == 0 and document["result"]["matching"] == 2, document

    # A filter the page refuses is invalid, with nothing read or recorded.
    before = AuditEvent.objects.count()
    for argv in (
        ("--status", "bad"),
        ("--kind", "everything"),
        ("--start", "2054-13-01", "--zone", "UTC"),
        # Days need the zone they fall in.
        ("--start", "2000-01-01"),
    ):
        code, document = run("report", "census", *argv)
        assert code == 1 and document["error"]["code"] == "invalid", argv
    assert AuditEvent.objects.count() == before

    # A read-only session reads it.
    reader = read_only_session(run.service)
    code, document = run("report", "census", secret=reader)
    assert code == 0 and document["result"]["matching"] == 2, document


def test_staff_read_and_a_leader_is_denied(response_service, google, cli, monkeypatch):  # noqa: F811
    """Staff read the list, as on the page, without the Administrator's
    To review rows; a Ministry leader is refused, with nothing printed or
    recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = run("report", "census")
        assert code == 0, document
        # Staff's work to do is the address alone: automatic changes are
        # the Administrator's to review.
        assert document["result"]["matching"] == 1
        assert document["result"]["routes"] == {"automatic": 0, "by_hand": 1}
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    with_roles(monkeypatch, {"ministry_leader"}, frozenset({4}))
    code, document = run("report", "census")
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_an_ended_session_reads_nothing(response_service, google, cli):  # noqa: F811
    """A revoked session is exit 5, before the filter check, with nothing
    read or recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    revoke(run.service, run.secret)
    before = AuditEvent.objects.filter(event_type=EVENT).count()
    for argv in (("report", "census"), ("report", "census", "--status", "bad")):
        code, document = run(*argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
    assert AuditEvent.objects.filter(event_type=EVENT).count() == before


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    secret = session(admin)
    code, document = one(admin, "report", "census", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()
