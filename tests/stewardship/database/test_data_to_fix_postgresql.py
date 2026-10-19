"""Data to fix in ParishSoft (#1011): the page and its command on real data.

Each section's count must be the count its detail page's own read gives,
for the sections the reader's role may open, in the decided order; Staff
see only the sections whose pages they may open, and a Ministry leader is
refused. The command prints the same counts on the restricted web login.
"""

import re
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import data_to_fix_views
from parishkit.stewardship.accounts.admin_dashboard import unreachable_families
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.reports import source_form

from ..policy_factory import address as rule_address
from .auth_builders import signed_in
from .campaign_builders import change
from .leader_builders import promote_leaders
from .response_builders import activate_response_service
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_census_changes_postgresql import submit_live
from .test_ministry_responses_postgresql import ministry_source, start
from .test_recipient_suppressions_postgresql import refused, remember
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/parish/data-to-fix/"
ORDER = (
    "form_cannot_open",
    "refused_addresses",
    "unreachable_families",
    "census_changes",
)


@pytest.fixture
def scenario(response_service):
    """A live campaign with one refused address and two census changes.

    The response renames Member 3 (Automatic) and moves the Family (By
    hand), both To review, which every role counts (Administrator
    decision, 2026-10-18: To review and Conflict).
    """
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    remember(refused(harness))
    source_form._cache.clear()
    return harness


def viewed():
    """Every ``data_to_fix_viewed`` context, oldest first."""
    return [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type="data_to_fix_viewed"
        ).order_by("created_at", "id")
    ]


def sections(body):
    """The section keys the page drew, in order."""
    return tuple(re.findall(rb'data-section="([a-z_]+)"', body))


def sign_in_as(harness, google, roles):
    """Add a login rule for reader@example.org with ``roles`` and sign in."""
    store = harness.service.store
    if roles == ("ministry_leader",):
        # A Ministry leader leads through a ParishSoft role (#922).
        promote_leaders(harness, ministry_source(), {"reader@example.org": [9]})
    rule = rule_address("reader@example.org", roles=roles)
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


def test_an_administrator_sees_every_section_counted(scenario, google):
    """Four sections in the decided order, each count matching its page's
    read, with links to the detail pages; the view is audited as a total."""
    unreachable = unreachable_families(scenario.campaign.pk)
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, PAGE)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert sections(body) == tuple(key.encode() for key in ORDER)
    text = body.decode()
    assert "<title>Data to fix in ParishSoft" in text
    assert "None: every Family that can use the portal can open the form." in text
    assert "1 refused email address is not cleared yet, from 1 Family." in text
    # The rename and the move, both To review.
    assert "2 census changes that Families reported are still to be handled." in text
    assert 'href="/admin/system/source-form/"' in text
    assert 'href="/admin/mail/refusals/"' in text
    assert 'href="/admin/reports/families/?reach=neither"' in text
    assert 'href="/admin/reports/census/"' in text
    # The menu offers the page, last in Parish data.
    assert f'href="{PAGE}" aria-current="page"' in text
    assert viewed() == [{"outcome": "succeeded", "count": 3 + (unreachable or 0)}]
    # Anything but a plain GET of the page is refused.
    assert get(browser, PAGE + "?other=1")[0].status_code == 400


def test_staff_see_only_the_sections_they_can_open(scenario, google):
    """Staff see Families no mail can reach and census changes (To review
    and Conflict, as an Administrator does), never a source-form or
    refused-address count."""
    browser = sign_in_as(scenario, google, ("staff",))
    response, body = get(browser, PAGE)
    assert response.status_code == 200
    assert sections(body) == (b"unreachable_families", b"census_changes")
    text = body.decode()
    assert "2 census changes that Families reported are still to be handled." in text
    assert "refused" not in text.lower() and "source-form" not in text
    # The menu shows the Parish data group with this one entry.
    assert f'href="{PAGE}"' in text
    assert 'href="/admin/parish/settings/"' not in text
    assert len(viewed()) == 1


def test_a_ministry_leader_is_refused(scenario, google):
    """A Ministry leader may open no section's page, so not this one."""
    browser = sign_in_as(scenario, google, ("ministry_leader",))
    response, body = get(browser, PAGE)
    assert response.status_code in {403, 404}
    assert b"census change" not in body
    # The shared denial, not Sent mail's error page (#1036 review).
    assert b"Delivery action unavailable" not in body
    assert viewed() == []


def test_a_failed_section_says_why_and_the_rest_still_count(
    scenario, google, monkeypatch
):
    """One section's read failing leaves its block, saying it could not
    count, while the others count; the page still loads."""

    def broken(campaign_id):
        """The source-form scan fails."""
        raise RuntimeError("synthetic")

    monkeypatch.setattr(source_form, "blocked_families", broken)
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, PAGE)
    assert response.status_code == 200
    assert sections(body) == tuple(key.encode() for key in ORDER)
    assert str(data_to_fix_views.FAILED).encode() in body
    assert b"1 refused email address is not cleared yet" in body


def test_the_command_prints_the_page_counts(scenario, google, cli):  # noqa: F811
    """``report data-to-fix`` prints each visible section's count, for a full
    or read-only session, and records the page's event."""
    unreachable = unreachable_families(scenario.campaign.pk)
    signed_in()
    run = cli()
    code, document = run("report", "data-to-fix")
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == str(scenario.campaign.pk)
    assert set(result["sections"]) == set(ORDER)
    assert result["sections"]["form_cannot_open"] == {"count": 0}
    assert result["sections"]["refused_addresses"] == {"count": 1, "families": 1}
    assert result["sections"]["census_changes"] == {"count": 2}
    assert result["sections"]["unreachable_families"] == (
        {"count": unreachable}
        if unreachable is not None
        else {"unavailable": str(data_to_fix_views.NO_SOURCE)}
    )
    # Counts only: never an address.
    assert "valid@example.org" not in str(document)
    assert viewed()[-1]["count"] == 3 + (unreachable or 0)
    reader = read_only_session(run.service)
    code, again = run("report", "data-to-fix", secret=reader)
    assert code == 0 and again["result"] == result


def test_the_command_shows_staff_their_sections_only(
    scenario,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """A Staff reader's command lists only the sections the page shows Staff.

    Pairing needs an Administrator, so the page's own admission is given
    the session's principal holding only Staff.
    """
    signed_in()
    run = cli()
    real = data_to_fix_views.authenticated_admin

    def as_staff(*arguments, **options):
        """The real admission, its principal holding only Staff."""
        principal = real(*arguments, **options)
        return replace(principal, roles=frozenset({"staff"}))

    monkeypatch.setattr(data_to_fix_views, "authenticated_admin", as_staff)
    code, document = run("report", "data-to-fix")
    assert code == 0, document
    assert set(document["result"]["sections"]) == {
        "unreachable_families",
        "census_changes",
    }
    assert document["result"]["sections"]["census_changes"] == {"count": 2}


def test_without_a_campaign_each_section_says_why(auth_service, google):
    """No current campaign: every block stays, refused addresses still count,
    and the directory and census links are greyed out as their menu entries
    are. No ParishSoft data has been downloaded either, so the source-form
    list is greyed out with that reason, as System health's link is (#1242)."""
    browser, login = signed_in()
    assert login.status_code == 302
    response, body = get(browser, PAGE)
    assert response.status_code == 200
    assert sections(body) == tuple(key.encode() for key in ORDER)
    text = body.decode()
    assert "No current campaign." in text
    assert "No refused addresses: campaign email reaches every address" in text
    assert text.count("No current campaign") >= 3
    assert 'href="/admin/system/source-form/"' not in text
    assert 'id="data-to-fix-open-form_cannot_open-tip"' in text
    assert "ParishSoft data has not been downloaded yet." in text
    assert 'id="data-to-fix-open-unreachable_families-tip"' in text
    assert 'id="data-to-fix-open-census_changes-tip"' in text
    # The menu greys Census changes out too, so no link to it anywhere.
    assert 'href="/admin/reports/census/"' not in text
    assert viewed() == [{"outcome": "succeeded", "count": 0}]
