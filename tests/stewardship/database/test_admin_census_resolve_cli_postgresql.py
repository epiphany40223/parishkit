"""``census resolve``: a Census changes row's Record outcome (#463, #528).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, resolving
through the page's own admission and service (``resolve_census_change``)
inside the page's command scope. The command records the service's
``census_change_updated`` and its own ``admin_cmd_census_resolve``, once
per request key, and prints ids, versions and states only.
"""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.reports import census_change_views
from parishkit.stewardship.responses import census_resolution
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange

from .auth_builders import signed_in
from .automation_builders import as_staff
from .test_admin_census_cli_postgresql import submitted, with_roles
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_status_cli_postgresql import (
    admin,  # noqa: F401
    one,
    session,
)

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_census_resolve"
UPDATED = "census_change_updated"


def rows():
    """The scenario's By-hand address change and automatic name change."""
    found = {row.field: row for row in ProposedChange.objects.all()}
    return found["home_address"], found["first_name"]


def count(event_type):
    """How many events of ``event_type`` have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def resolve(run, proposal, action, *extra, version=None, key=None, secret=None):
    """``census resolve`` for ``proposal`` at its current (or given) version."""
    proposal.refresh_from_db()
    argv = [
        *("census", "resolve", str(proposal.pk), "--action", action),
        *("--expected-version", str(proposal.version if version is None else version)),
        *("--request-key", str(key or uuid4())),
        *extra,
    ]
    return run(*argv, **({} if secret is None else {"secret": secret}))


def staff(monkeypatch):
    """Make every admission, the page's and the service's, a Staff member's.

    ``as_staff`` covers ``sessions`` and ``admin_editing``; the census page
    and its service import their admissions themselves, so they are patched
    too, and the service's own policy reload (``current_principal``) gets
    the same roles.
    """
    as_staff(monkeypatch)
    monkeypatch.setattr(
        census_change_views, "authenticated_admin", sessions.authenticated_admin
    )
    real = census_resolution.current_principal

    def reloaded(*arguments, **options):
        """The real policy reload, its principal holding only Staff."""
        return replace(real(*arguments, **options), roles=frozenset({"staff"}))

    monkeypatch.setattr(census_resolution, "current_principal", reloaded)


def test_an_administrator_enters_ignores_and_reopens(response_service, google, cli):  # noqa: F811
    """Each outcome through the page's service, with its event and the
    command's, and a document of ids, versions and states only."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, name = rows()

    key = uuid4()
    code, document = resolve(run, home, "ignored", "--note", "Duplicate", key=key)
    assert code == 0, document
    result = document["result"]
    home.refresh_from_db()
    assert result["created"] and result["request_key"] == str(key)
    assert result["resolution"]["proposal_id"] == str(home.pk)
    assert result["resolution"]["action"] == "ignored"
    assert result["resolution"]["expected_version"] == home.version - 1
    assert result["proposal"] == {
        "id": str(home.pk),
        "version": home.version,
        "decision": "ignored",
        "execution": "pending",
    }
    assert "Duplicate" not in str(document) and "Census Way" not in str(document)
    assert (count(EVENT), count(UPDATED)) == (1, 1)
    event = AuditEvent.objects.get(event_type=EVENT)
    assert AuditContext.objects.get(event=event).context == {"outcome": "succeeded"}

    # A repeat with the same key returns the first resolution and records
    # nothing new; the same key for another intent is invalid.
    code, document = resolve(
        run, home, "ignored", "--note", "Duplicate", key=key, version=home.version - 1
    )
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["resolution"] == result["resolution"]
    assert (count(EVENT), count(UPDATED)) == (1, 1)
    code, document = resolve(
        run, home, "ignored", "--note", "Other", key=key, version=home.version - 1
    )
    assert code == 1 and document["error"]["code"] == "invalid", document

    # Reopening needs a note; with one, the change is to review again.
    code, document = resolve(run, home, "reopened")
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = resolve(run, home, "reopened", "--note", "Not a duplicate")
    assert code == 0, document
    assert document["result"]["proposal"]["decision"] == "unreviewed"

    # Entered in ParishSoft cannot be undone, so it asks first, as the
    # page's dialog does: without an answer (or --yes) nothing changes.
    code, document = resolve(run, home, "entered")
    assert code == 4, document
    assert document["error"]["code"] == "confirmation_required"
    assert (count(EVENT), count(UPDATED)) == (2, 2)
    code, document = resolve(run, home, "entered", "--yes")
    assert code == 0, document
    assert document["result"]["proposal"]["execution"] == "resolved_external"
    assert (count(EVENT), count(UPDATED)) == (3, 3)
    assert ProposalResolution.objects.filter(proposal=home).count() == 3

    # A final change, and an automatic one, take no outcome: the page's 400.
    for proposal in (home, name):
        code, document = resolve(run, proposal, "ignored")
        assert code == 1 and document["error"]["code"] == "invalid", document
    assert count(EVENT) == 3


def test_stale_unknown_and_read_only_refusals(response_service, google, cli):  # noqa: F811
    """A changed version is stale, an unknown change is not available, and a
    read-only session changes nothing."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, _ = rows()
    code, document = resolve(run, home, "entered", "--yes", version=home.version + 1)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = run(
        *("census", "resolve", str(uuid4()), "--action", "ignored"),
        *("--expected-version", "1", "--request-key", str(uuid4())),
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    reader = read_only_session(run.service)
    code, document = resolve(run, home, "entered", "--yes", secret=reader)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)
    assert not ProposalResolution.objects.exists()


def test_staff_enter_and_ignore_but_never_reopen(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """As on the page: Staff record Entered and Ignore; Reopen is the
    Administrator's, so Staff reopening is denied with nothing recorded. A
    Ministry leader may do none of them."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, _ = rows()
    with monkeypatch.context() as patch:
        staff(patch)
        code, document = resolve(run, home, "ignored")
        assert code == 0 and document["result"]["created"], document
        code, document = resolve(run, home, "reopened", "--note", "Staff cannot")
        assert code == 1 and document["error"]["code"] == "denied", document
    assert (count(EVENT), count(UPDATED)) == (1, 1)
    home.refresh_from_db()
    assert home.decision == "ignored"
    # The Administrator reopens it; then Staff enter it.
    code, document = resolve(run, home, "reopened", "--note", "Not a duplicate")
    assert code == 0, document
    with monkeypatch.context() as patch:
        staff(patch)
        # The early role check is the page's own capability (#1341), which
        # Staff hold: they still reach the prompt.
        code, document = resolve(run, home, "entered")
        assert code == 4, document
        assert document["error"]["code"] == "confirmation_required"
        code, document = resolve(run, home, "entered", "--yes")
        assert code == 0, document
    assert document["result"]["proposal"]["execution"] == "resolved_external"
    assert (count(EVENT), count(UPDATED)) == (3, 3)
    # A Ministry leader is refused by the page's own admission.
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"}, frozenset({4}))
        code, document = resolve(run, home, "ignored")
        assert code == 1 and document["error"]["code"] == "denied", document
    assert (count(EVENT), count(UPDATED)) == (3, 3)


def test_another_campaign_s_change_is_not_available(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """A change is resolved only from its own campaign's list, as on the
    page: when another campaign is the current one (stood in for here, past
    the page's campaign check), the change is not available and nothing is
    recorded. So is a repeat after the current campaign changed."""
    from parishkit.stewardship import admin_report_reads
    from parishkit.stewardship.reports import read_admission

    submitted(response_service)
    signed_in()
    run = cli()
    home, _ = rows()
    key = uuid4()
    code, document = resolve(run, home, "ignored", key=key)
    assert code == 0, document
    other = uuid4()
    monkeypatch.setattr(admin_report_reads, "_current", lambda: other)
    monkeypatch.setattr(read_admission, "admit_report_read", lambda campaign: None)
    code, document = resolve(run, home, "reopened", "--note", "Elsewhere")
    assert code == 1 and document["error"]["code"] == "not_available", document
    code, document = resolve(run, home, "ignored", key=key, version=home.version - 1)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert (count(EVENT), count(UPDATED)) == (1, 1)


def test_an_ended_session_changes_nothing(response_service, google, cli):  # noqa: F811
    """A revoked session is exit 5, with nothing resolved or recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, _ = rows()
    revoke(run.service, run.secret)
    code, document = resolve(run, home, "entered", "--yes")
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    secret = session(admin)
    code, document = one(
        admin,
        *("census", "resolve", str(uuid4()), "--action", "ignored"),
        *("--expected-version", "1", "--request-key", str(uuid4())),
        secret=secret,
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert count(EVENT) == 0
