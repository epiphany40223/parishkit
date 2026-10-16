"""``census review``: an Automatic row's Review (#528, ADM-09).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, reviewing
through the page's own admission and service (``review_census_change``)
inside the page's command scope, so its guards (in Python and in 0096's
SQL) are the page's. The command records the service's
``census_change_updated`` and its own ``admin_cmd_census_review``, once per
request key, and prints ids, versions and states only.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.responses.models import ProposalReview

from .auth_builders import signed_in
from .automation_builders import as_staff
from .test_admin_census_cli_postgresql import submitted, with_roles
from .test_admin_census_resolve_cli_postgresql import count, rows
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_status_cli_postgresql import (
    admin,  # noqa: F401
    one,
    session,
)
from .test_publication_plans_postgresql import owner

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_census_review"
UPDATED = "census_change_updated"


def review(run, proposal, decision, *extra, version=None, key=None, secret=None):
    """``census review`` for ``proposal`` at its current (or given) version."""
    proposal.refresh_from_db()
    argv = [
        *("census", "review", str(proposal.pk), "--decision", decision),
        *("--expected-version", str(proposal.version if version is None else version)),
        *("--request-key", str(key or uuid4())),
        *extra,
    ]
    return run(*argv, **({} if secret is None else {"secret": secret}))


def test_an_administrator_approves_ignores_and_sends_back(
    response_service,
    google,
    cli,  # noqa: F811
):
    """Each decision through the page's service, with its event and the
    command's, a document of ids, versions and states only, and no prompt."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()

    key = uuid4()
    code, document = review(run, name, "approved", "--note", "Checked", key=key)
    assert code == 0, document
    result = document["result"]
    name.refresh_from_db()
    assert result["created"] and result["request_key"] == str(key)
    assert result["review"]["proposal_id"] == str(name.pk)
    assert result["review"]["decision"] == "approved"
    assert result["review"]["expected_version"] == name.version - 1
    assert result["proposal"] == {
        "id": str(name.pk),
        "version": name.version,
        "decision": "approved",
        "execution": "pending",
    }
    assert "Checked" not in str(document) and "Requested" not in str(document)
    assert ProposalReview.objects.get().note == "Checked"
    assert (count(EVENT), count(UPDATED)) == (1, 1)
    event = AuditEvent.objects.get(event_type=EVENT)
    assert AuditContext.objects.get(event=event).context == {"outcome": "succeeded"}

    # A repeat with the same key returns the first review and records
    # nothing new; the same key for another intent is invalid.
    code, document = review(
        run, name, "approved", "--note", "Checked", key=key, version=name.version - 1
    )
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["review"] == result["review"]
    assert (count(EVENT), count(UPDATED)) == (1, 1)
    code, document = review(
        run, name, "ignored", "--note", "Checked", key=key, version=name.version - 1
    )
    assert code == 1 and document["error"]["code"] == "invalid", document

    # The same decision again is no change: the page's 400.
    code, document = review(run, name, "approved")
    assert code == 1 and document["error"]["code"] == "invalid", document

    # Ignore, then Back to To review; each advances the version.
    code, document = review(run, name, "ignored")
    assert code == 0 and document["result"]["proposal"]["decision"] == "ignored"
    code, document = review(run, name, "unreviewed")
    assert code == 0 and document["result"]["proposal"]["decision"] == "unreviewed"
    assert (count(EVENT), count(UPDATED)) == (3, 3)
    assert [row.decision for row in ProposalReview.objects.order_by("created_at")] == [
        "approved",
        "ignored",
        "unreviewed",
    ]


@pytest.mark.parametrize(
    ("execution", "decision", "allowed"),
    [
        ("failed", "approved", True),
        ("conflict", "ignored", True),
        # A Conflict is not approved until it is resolved.
        ("conflict", "approved", False),
        # Queued for publication: the publish worker owns it (#1245 review
        # note 5), so no review is taken. A queued change is already
        # approved, so these are the decisions that would change it.
        ("queued", "ignored", False),
        ("queued", "unreviewed", False),
        ("published", "ignored", False),
        ("resolved_upstream", "approved", False),
    ],
)
def test_only_an_open_change_is_reviewed(
    response_service,
    google,
    cli,  # noqa: F811
    execution,
    decision,
    allowed,
):
    """As on the page: pending, failed or in conflict only, never queued or
    final; a refused review is invalid and records nothing."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()
    # A queued change has been approved; the others start as the scenario
    # leaves them (To review).
    owner(
        "UPDATE stewardship_proposed_change SET execution=%s, decision=%s WHERE id=%s",
        [execution, "approved" if execution == "queued" else "unreviewed", name.pk],
    )
    code, document = review(run, name, decision)
    if allowed:
        assert code == 0, document
        assert document["result"]["proposal"]["decision"] == decision
        assert (count(EVENT), count(UPDATED)) == (1, 1)
        return
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)
    assert not ProposalReview.objects.exists()


def test_by_hand_stale_unknown_and_read_only_refusals(
    response_service,
    google,
    cli,  # noqa: F811
):
    """A By-hand change has no Review (invalid), a changed version is stale,
    an unknown change is not available, and a read-only session changes
    nothing."""
    submitted(response_service)
    signed_in()
    run = cli()
    home, name = rows()
    code, document = review(run, home, "approved")
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = review(run, name, "approved", version=name.version + 1)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = run(
        *("census", "review", str(uuid4()), "--decision", "approved"),
        *("--expected-version", "1", "--request-key", str(uuid4())),
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    reader = read_only_session(run.service)
    code, document = review(run, name, "approved", secret=reader)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)
    assert not ProposalReview.objects.exists()


def test_staff_and_a_ministry_leader_are_denied(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """Review is the Administrator's: Staff are denied by the early role
    check, before the note is read, and so is a Ministry leader."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()
    with monkeypatch.context() as patch:
        as_staff(patch)
        code, document = review(run, name, "approved")
        assert code == 1 and document["error"]["code"] == "denied", document
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"}, frozenset({4}))
        code, document = review(run, name, "approved")
        assert code == 1 and document["error"]["code"] == "denied", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)


def test_another_campaign_s_change_is_not_available(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """A change is reviewed only from its own campaign's list: when another
    campaign is the current one (stood in for here, past the page's
    campaign check), it is not available and nothing is recorded."""
    from parishkit.stewardship import admin_report_reads
    from parishkit.stewardship.reports import read_admission

    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()
    other = uuid4()
    monkeypatch.setattr(admin_report_reads, "_current", lambda: other)
    monkeypatch.setattr(read_admission, "admit_report_read", lambda campaign: None)
    code, document = review(run, name, "approved")
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)


def test_an_ended_session_changes_nothing(response_service, google, cli):  # noqa: F811
    """A revoked session is exit 5, with nothing reviewed or recorded."""
    submitted(response_service)
    signed_in()
    run = cli()
    _, name = rows()
    revoke(run.service, run.secret)
    code, document = review(run, name, "approved")
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert (count(EVENT), count(UPDATED)) == (0, 0)


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    secret = session(admin)
    code, document = one(
        admin,
        *("census", "review", str(uuid4()), "--decision", "approved"),
        *("--expected-version", "1", "--request-key", str(uuid4())),
        secret=secret,
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert count(EVENT) == 0
