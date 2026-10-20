"""``report pledges``, ``export pledges`` and ``pledges decide`` (#1733).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
read Pledges through the page's own admission, ``pledge_rows``
and writer under the campaign read guard, and decide through the page's
``PledgeDecisions`` in the page's command scope, so the SQL guard of frozen
migration 0127 checks every row. Each records the page's events; ``pledges
decide`` also records ``admin_cmd_pledges_decide``, once per request key.
"""

import json
from dataclasses import asdict, replace
from decimal import Decimal
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.reports import pledge_review_views
from parishkit.stewardship.reports.pledge_models import PledgeReview
from parishkit.stewardship.reports.pledge_review import bulk_key_for
from parishkit.stewardship.responses.models import Submission

from ..test_financial_answers import CHECK, OPTIONS
from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_logs_cli_postgresql import counted, stream
from .test_admin_report_cli_postgresql import cli, parity  # noqa: F401
from .test_admin_status_cli_postgresql import admin, session  # noqa: F401
from .test_financial_report_postgresql import family_session, head, pledge
from .test_financial_source_postgresql import financial_source
from .test_information_followup_postgresql import search
from .test_pledge_review_postgresql import pledged, state
from .test_report_workspace_postgresql import read
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/financial/pledges/"
EXPORT_ROUTE = "/admin/reports/financial/pledges/exports/"
EVENT = "admin_cmd_pledges_decide"
UPDATED = "pledge_review_updated"


def count(event_type):
    """How many events of ``event_type`` have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def decide(run, submission, action, *extra, version=None, key=None, secret=None):
    """``pledges decide`` for ``submission`` at its current (or given) version."""
    if version is None:
        found = PledgeReview.objects.filter(submission_id=submission.pk)
        version = max((row.sequence for row in found), default=0)
    argv = [
        *("pledges", "decide", "--action", action),
        *("--item", f"{submission.pk}:{version}"),
        *("--request-key", str(key or uuid4())),
        *extra,
    ]
    return run(*argv, **({} if secret is None else {"secret": secret}))


def export(run, *argv):
    """``export pledges`` with ``argv``: (exit code, bytes, document)."""
    return stream(None, "export", "pledges", *argv, secret=run.secret)


def with_roles(monkeypatch, roles):
    """Give the session's principal these roles from now on (the page's too)."""
    real = sessions.authenticated_admin

    def changed(*arguments, **options):
        """The real admission, its principal holding only ``roles``."""
        principal = real(*arguments, **options)
        return principal and replace(principal, roles=frozenset(roles))

    monkeypatch.setattr(sessions, "authenticated_admin", changed)
    monkeypatch.setattr(pledge_review_views, "authenticated_admin", changed)


def test_counts_and_the_page_s_files(response_service, google, cli):  # noqa: F811
    """``report pledges`` counts the page's rows by stage; ``export pledges``
    writes the page's CSV byte for byte, and JSON Lines with each pledge's
    item, version and actions. Both record the page's events."""
    harness, submission = pledged(response_service)
    browser, _ = signed_in()
    run = cli()

    found = {}
    parity(
        "pledges_to_enter_viewed",
        lambda: read(browser, ROUTE),
        lambda: found.update(report=run("report", "pledges")),
    )
    code, document = found["report"]
    assert code == 0, document
    assert document["result"] == {
        "campaign_id": str(harness.campaign.pk),
        "stage": "to_review",
        "matching": 1,
        "total": 1,
        "stages": {
            "to_review": 1,
            "conflict": 0,
            "to_enter_by_hand": 0,
            "entered_by_hand": 0,
            "entered_incorrectly": 0,
            "confirmed": 0,
            "ignored": 0,
        },
    }
    assert counted("pledges_to_enter_viewed")[-1] == 0
    code, document = run("report", "pledges", "--stage", "ignored")
    assert code == 0 and document["result"]["matching"] == 0, document

    form = {"stage": "to_review", "search": "", "format": "csv"}
    form |= {"timezone": "America/New_York"}

    def page():
        """The page's download, as the browser posts it."""
        response, found["page"] = search(browser, EXPORT_ROUTE, form)
        assert response.status_code == 200

    parity(
        "pledges_to_enter_exported",
        page,
        lambda: found.update(
            command=export(run, "--timezone", "America/New_York", "--format", "csv")
        ),
    )
    code, body, document = found["command"]
    assert code == 0, document
    assert body == found["page"]
    result = document["result"]
    assert (result["count"], result["stage"], result["format"]) == (
        1,
        "to_review",
        "csv",
    )
    assert result["file_name"].startswith("stewardship-pledges-")
    assert counted("pledges_to_enter_exported")[-2:] == [1, 1]

    code, body, document = export(run, "--format", "jsonl")
    assert code == 0, document
    assert document["result"]["content_type"] == "application/x-ndjson"
    [record] = [json.loads(line) for line in body.decode().splitlines()]
    assert record["item_id"] == str(submission.pk) and record["version"] == 0
    assert record["actions"] == ["accept", "edit", "ignore"]
    assert record["annual_pledge"] == "$1,234.50" and record["stage"] == "To review"
    code, body, document = export(run, "--stage", "entered_by_hand", "--format", "pdf")
    assert code == 0 and body.startswith(b"%PDF"), document
    assert document["result"]["count"] == 0


def test_decisions_go_through_the_page_s_service_and_guard(
    response_service,
    google,
    cli,  # noqa: F811
):
    """Accept, Back to review, Edit and Entered by hand, each through the
    page's service with its event and the command's, the actor recorded; a
    repeat is a replay, and every refusal records nothing."""
    harness, submission = pledged(response_service)
    signed_in()
    run = cli()

    key = uuid4()
    code, document = decide(run, submission, "accept", key=key)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    [row] = result["decisions"]
    assert row["item_id"] == str(submission.pk) and row["sequence"] == 1
    assert (row["action"], row["decision"], row["entered"]) == (
        "accept",
        "accepted",
        False,
    )
    assert (count(EVENT), count(UPDATED)) == (1, 1)
    # The actor is the session's Administrator, in the row and both events.
    recorded = PledgeReview.objects.get()
    events = AuditEvent.objects.filter(event_type__in=(EVENT, UPDATED))
    assert {event.actor_id for event in events} == {recorded.actor_id}
    assert state(harness, submission).stage == "to_enter_by_hand"

    # A repeat with the same key returns the first row and records nothing;
    # the same key for another decision is invalid.
    code, document = decide(run, submission, "accept", key=key, version=0)
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["decisions"] == result["decisions"]
    code, document = decide(run, submission, "ignore", key=key, version=0)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(EVENT), count(UPDATED)) == (1, 1)

    # Someone else's decision first is stale; Edit is not offered here.
    code, document = decide(run, submission, "ignore", version=0)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = decide(run, submission, "edit", "--amount", "10")
    assert code == 1 and document["error"]["code"] == "invalid", document

    code, document = decide(run, submission, "back_to_review", "--note", "Recheck")
    assert code == 0, document
    # A pledge above zero needs a frequency: refused at its option.
    code, document = decide(run, submission, "edit", "--amount", "100")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert [field["field"] for field in document["error"]["fields"]] == ["frequency"]
    code, document = decide(
        run, submission, "edit", *("--amount", "1,300", "--frequency", "quarterly")
    )
    assert code == 0, document
    assert document["result"]["decisions"][0]["decision"] == "accepted_edited"
    edited = PledgeReview.objects.get(sequence=3)
    assert (edited.reviewed_annual_pledge, edited.reviewed_frequency) == (
        Decimal("1300.00"),
        "quarterly",
    )
    assert "1,300" not in str(document) and "Recheck" not in str(document)

    # Entered by hand can be undone (#1840), so it asks nothing.
    code, document = decide(run, submission, "entered_by_hand")
    assert code == 0, document
    assert document["result"]["decisions"][0]["entered"]
    assert state(harness, submission).stage == "entered_by_hand"
    assert (count(EVENT), count(UPDATED)) == (4, 4)
    # Back to review undoes the mark only with a note, keeping the Edit.
    code, document = decide(run, submission, "back_to_review")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(EVENT), count(UPDATED)) == (4, 4)
    code, document = decide(run, submission, "back_to_review", "--note", "Undo")
    assert code == 0, document
    (undone,) = document["result"]["decisions"]
    assert (undone["action"], undone["decision"], undone["entered"]) == (
        "back_to_review",
        "accepted_edited",
        False,
    )
    assert state(harness, submission).stage == "to_enter_by_hand"
    assert (count(EVENT), count(UPDATED)) == (5, 5)
    # --yes is still accepted, and asks nothing either way.
    code, document = decide(run, submission, "entered_by_hand", "--yes")
    assert code == 0, document
    assert (count(EVENT), count(UPDATED)) == (6, 6)


def test_unknown_read_only_and_leader_refusals(
    response_service,
    google,
    cli,  # noqa: F811
    monkeypatch,
):
    """An unknown pledge is not available; a read-only session and a
    Ministry leader are denied; none records anything."""
    _harness, submission = pledged(response_service)
    signed_in()
    run = cli()
    code, document = run(
        *("pledges", "decide", "--action", "accept", "--item", f"{uuid4()}:0")
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    code, document = run(
        *("pledges", "decide", "--action", "accept"),
        *("--item", f"{submission.pk}:0", "--item", f"{uuid4()}:0"),
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    reader = read_only_session(run.service)
    code, document = decide(run, submission, "accept", secret=reader)
    assert code == 1 and document["error"]["code"] == "denied", document
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"})
        code, document = run("report", "pledges")
        assert code == 1 and document["error"]["code"] == "denied", document
        code, body, document = export(run)
        assert code == 1 and document["error"]["code"] == "denied", document
        assert body == b""
        code, document = decide(run, submission, "accept")
        assert code == 1 and document["error"]["code"] == "denied", document
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = decide(run, submission, "ignore")
        assert code == 0 and document["result"]["created"], document
    assert (count(EVENT), count(UPDATED)) == (1, 1)


def bulk(run, submissions, action, *extra, key, versions=None):
    """``pledges decide`` for several pledges at their current (or given) versions."""
    items = []
    for index, submission in enumerate(submissions):
        found = PledgeReview.objects.filter(submission_id=submission.pk)
        version = max((row.sequence for row in found), default=0)
        if versions is not None:
            version = versions[index]
        items += ["--item", f"{submission.pk}:{version}"]
    return run(
        *("pledges", "decide", "--action", action, *items),
        *("--request-key", str(key)),
        *extra,
    )


def test_bulk_decisions_are_all_or_none_and_a_key_is_used_once(
    response_service,
    google,
    cli,  # noqa: F811
):
    """Two pledges at once take the bulk bar's per-pledge keys; a replay
    records nothing; a stale pledge in the set records nothing; and a key
    reused for fewer pledges or another Edit value is invalid,
    never a second decision."""
    harness = response_service
    # Family 2 needs a head of household to receive its own credential.
    financial_source(
        harness, options=map(asdict, OPTIONS), extra_members={21: head(21, 2)}
    )
    harness = activate_response_service(harness)
    with web_login():
        first = pledge(harness, load_form(harness), shares={CHECK: ""})
        other = family_session(harness, 2)
        pledge(other, load_form(other), annual_pledge="500", frequency="weekly")
    # The helper returns the harness Family's response; read Family 2's.
    second = Submission.objects.exclude(family_id=first.family_id).get(mode="live")
    signed_in()
    run = cli()

    key = uuid4()
    code, document = bulk(run, [first, second], "accept", key=key)
    assert code == 0 and document["result"]["created"], document
    recorded = {
        row.submission_id: row.request_key for row in PledgeReview.objects.all()
    }
    assert recorded == {
        first.pk: bulk_key_for(key, first.pk),
        second.pk: bulk_key_for(key, second.pk),
    }
    assert (count(EVENT), count(UPDATED)) == (1, 2)
    # The same post again is a replay of both rows.
    code, again = bulk(run, [first, second], "accept", key=key, versions=[0, 0])
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["decisions"] == document["result"]["decisions"]
    # The key for one of them alone is another use of it.
    code, refused = decide(run, first, "accept", key=key, version=0)
    assert code == 1 and refused["error"]["code"] == "invalid", refused
    assert (count(EVENT), count(UPDATED)) == (1, 2)

    # One stale pledge in the set: nothing is recorded for either.
    code, refused = bulk(run, [first, second], "back_to_review", key=uuid4())
    assert code == 0, refused
    code, refused = bulk(run, [first, second], "ignore", key=uuid4(), versions=[2, 1])
    assert code == 1 and refused["error"]["code"] == "stale_version", refused
    assert PledgeReview.objects.count() == 4
    assert (count(EVENT), count(UPDATED)) == (2, 4)

    # An Edit's key repeats only with the same value.
    edit = uuid4()
    code, document = decide(
        run, first, "edit", *("--amount", "700", "--frequency", "annual"), key=edit
    )
    assert code == 0 and document["result"]["created"], document
    code, document = decide(
        run,
        first,
        "edit",
        *("--amount", "700", "--frequency", "annual"),
        key=edit,
        version=2,
    )
    assert code == 0 and not document["result"]["created"], document
    code, document = decide(
        run,
        first,
        "edit",
        *("--amount", "800", "--frequency", "annual"),
        key=edit,
        version=2,
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    # A decision that cannot be posted is refused before the prompt.
    code, document = decide(run, first, "entered_by_hand", "--amount", "1")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(EVENT), count(UPDATED)) == (3, 5)


def test_a_confirmed_pledge_is_undone_at_the_exported_version(
    response_service,
    google,
    cli,  # noqa: F811
):
    """After a refresh Confirmed a pledge ParishSoft already held, JSON
    Lines gives the page's version (with the check) and Back to review as
    its action; ``pledges decide`` at that version needs the note, then
    sends the pledge back to To review (#1840)."""
    from .test_pledge_refresh_postgresql import ANSWER, refresh

    harness, submission = pledged(response_service)
    refresh(harness, ANSWER)
    signed_in()
    run = cli()
    code, body, document = export(run, "--stage", "confirmed", "--format", "jsonl")
    assert code == 0, document
    [record] = [json.loads(line) for line in body.decode().splitlines()]
    assert (record["version"], record["actions"]) == (1, ["back_to_review"])
    version = record["version"]
    code, document = decide(run, submission, "back_to_review", version=version)
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = decide(
        run, submission, "back_to_review", "--note", "Recheck", version=version
    )
    assert code == 0, document
    assert document["result"]["decisions"][0]["decision"] is None
    assert state(harness, submission).stage == "to_review"
