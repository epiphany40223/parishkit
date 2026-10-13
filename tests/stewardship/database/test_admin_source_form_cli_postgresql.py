"""``pk-stewardship admin report source-form``: Families the form cannot open.

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The command
reads System health's list (#774) through the page's own functions and
prints counts and DUIDs only, recording the page's ``source_form_viewed``.
"""

from dataclasses import replace

import pytest

from parishkit.stewardship.accounts import source_form_views
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.reports import source_form
from parishkit.stewardship.source.family_names import family_display_name
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED

from .auth_builders import signed_in
from .automation_builders import paired
from .response_builders import response_source
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_test_sample_cli_postgresql import runner
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

FIELDS = {"campaign_id", "eligible", "families", "rows"}


def viewed():
    """Every ``source_form_viewed`` context, oldest first."""
    return [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type="source_form_viewed"
        ).order_by("created_at", "id")
    ]


def blocked_source(harness):
    """Promote a snapshot whose Member 3 has a refused first and last name."""
    data = response_source()
    data.members[3].update(firstName="x" * 101, lastName="private\tvalue")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    return data


def test_the_list_by_duid_for_any_session(response_service, google, cli):  # noqa: F811
    """A clean source lists nothing; a refused Member is listed by DUID and
    field only, for a full or read-only session, with the page's event."""
    harness = response_service
    family = (
        FamilyCampaign.objects.filter(campaign=harness.campaign, portal_eligible=True)
        .values_list("family_duid", flat=True)
        .get()
    )
    signed_in()
    run = cli()
    code, document = run("report", "source-form")
    assert code == 0, document
    assert document["result"] == {
        "campaign_id": str(harness.campaign.pk),
        "eligible": 1,
        "families": 0,
        "rows": [],
    }
    assert viewed() == [{"outcome": "succeeded", "count": 0}]

    data = blocked_source(harness)
    source_form._cache.clear()
    code, document = run("report", "source-form")
    assert code == 0, document
    result = document["result"]
    assert set(result) == FIELDS and result["families"] == 1
    assert {(row["family_duid"], row["member_duid"]) for row in result["rows"]} == {
        (family, 3)
    }
    assert {row["field"] for row in result["rows"]} == {"First name", "Last name"}
    assert all(
        set(row) == {"family_duid", "member_duid", "field"} for row in result["rows"]
    )
    # Never a value, and never the Family name the page shows.
    text = str(document)
    assert "private" not in text and "x" * 101 not in text
    assert family_display_name(data.families[1], NAME_NOT_RECORDED) not in text
    assert viewed()[-1] == {"outcome": "succeeded", "count": 1}

    # A read-only session reads the same list.
    reader = read_only_session(run.service)
    code, again = run("report", "source-form", secret=reader)
    assert code == 0 and again["result"] == result

    # An ended session reads nothing, and its refusal is recorded as none.
    events = len(viewed())
    revoke(run.service, run.secret)
    code, document = run("report", "source-form")
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert len(viewed()) == events


def test_a_reader_who_is_not_an_administrator_is_denied(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """Staff cannot read the list, as on the page: denied, nothing recorded.

    Pairing needs an Administrator, so the page's own admission is given
    the session's principal holding only Staff; the page's capability check
    then refuses it.
    """
    signed_in()
    run = cli()
    real = source_form_views.authenticated_admin

    def as_staff(*arguments, **options):
        """The real admission, its principal holding only Staff."""
        principal = real(*arguments, **options)
        return replace(principal, roles=frozenset({"staff"}))

    monkeypatch.setattr(source_form_views, "authenticated_admin", as_staff)
    code, document = run("report", "source-form")
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document and viewed() == []


def test_a_scan_that_cannot_run_is_unavailable(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """A scan refused under the guard is exit 3, recorded as failed."""
    signed_in()
    run = cli()

    def refused(campaign_id):
        """The source is being refreshed."""
        raise ReadUnavailable("The source is being refreshed.")

    monkeypatch.setattr(source_form, "blocked_families", refused)
    code, document = run("report", "source-form")
    assert code == 3 and document["error"]["code"] == "unavailable", document
    assert viewed() == [{"outcome": "failed", "count": 0}]


def test_no_current_campaign(auth_service, google, monkeypatch):
    """Without a current campaign: no rows, as the page's "no campaign" text."""
    commands = runner(auth_service, monkeypatch)
    _, secret, _ = paired(commands.service)
    code, document, _ = commands("report", "source-form", secret=secret)
    assert code == 0, document
    assert document["result"] == {
        "campaign_id": None,
        "eligible": 0,
        "families": 0,
        "rows": [],
    }
    assert viewed() == [{"outcome": "succeeded", "count": 0}]


def test_a_missing_configuration_row_fails_closed(auth_service, google, monkeypatch):
    """No configuration row is ``not_available``, never "no campaign".

    The guards keep the row from being deleted, so the page's read of it
    alone is made to find none.
    """
    commands = runner(auth_service, monkeypatch)
    _, secret, _ = paired(commands.service)
    manager = SystemConfiguration.objects
    real = manager.values_list
    reads = []

    def missing(*fields, **options):
        """The current campaign's read finds no row; any other is real."""
        if fields == ("current_campaign_id",):
            reads.append(fields)
            return manager.none().values_list(*fields, **options)
        return real(*fields, **options)

    monkeypatch.setattr(manager, "values_list", missing)
    code, document, _ = commands("report", "source-form", secret=secret)
    assert reads, "the command did not read the configuration row"
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document and viewed() == []
