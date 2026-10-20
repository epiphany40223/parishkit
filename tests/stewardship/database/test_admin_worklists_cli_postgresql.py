"""``information list``, the ``decide`` commands and the worklists' counts (#1735).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
read the Additional information and Limitations worklists through the
pages' own admissions and reads under the campaign read guard, and decide
through the pages' ``InformationDecisions`` and ``LimitationDecisions`` in
the pages' command scope, so the SQL guard of frozen migration 0126 checks
every row. Each records the pages' events; the ``decide`` commands also
record ``admin_cmd_<stream>_decide``, once per request key.
"""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.models import FamilyCampaign
from parishkit.stewardship.reports import information_views, limitation_views
from parishkit.stewardship.responses.census_review import bulk_key_for
from parishkit.stewardship.responses.limitation_review import answer_id
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    AdditionalInformationRevision,
    LimitationReview,
)

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_logs_cli_postgresql import counted, stream
from .test_admin_report_cli_postgresql import parity
from .test_admin_roster_information_cli_postgresql import cli  # noqa: F401
from .test_information_followup_postgresql import search
from .test_information_limitation_review_postgresql import submit_everyone
from .test_ministry_responses_postgresql import start
from .test_report_workspace_postgresql import read
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

INFORMATION_ROUTE = "/admin/reports/information/"
LIMITATIONS_ROUTE = "/admin/reports/limitations/"
LIMITATIONS_EXPORT = "/admin/reports/limitations/exports/"
INFORMATION_EVENT = "admin_cmd_information_decide"
LIMITATIONS_EVENT = "admin_cmd_limitations_decide"
UPDATED = "information_updated"
REVIEWED = "limitation_reviewed"
TEXT = "PRIVATE-TEXT please call our household"
NOTE = "PRIVATE-NOTE called them back"


def count(event_type):
    """How many events of ``event_type`` have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def contexts(event_type):
    """The context members of each recorded event of this type, oldest first."""
    return [
        set(context)
        for context in AuditContext.objects.filter(event__event_type=event_type)
        .order_by("event__created_at", "event__id")
        .values_list("context", flat=True)
    ]


def with_roles(monkeypatch, roles):
    """Give the session's principal these roles from now on (the pages' too)."""
    real = sessions.authenticated_admin

    def changed(*arguments, **options):
        """The real admission, its principal holding only ``roles``."""
        principal = real(*arguments, **options)
        return principal and replace(principal, roles=frozenset(roles))

    monkeypatch.setattr(sessions, "authenticated_admin", changed)
    for module in (information_views, limitation_views):
        monkeypatch.setattr(module, "authenticated_admin", changed)


# ------------------------------------------------- Additional information


def requested(harness, text=TEXT):
    """A Family's live Additional information request."""
    return AdditionalInformationItem.objects.get(submission=respond(harness, text))


def inform(run, action, *items, note=None, key=None, extra=(), answer=b""):
    """``information decide`` on ``items``, each at its current version."""
    argv = ["information", "decide", "--action", action]
    for item in items:
        item.refresh_from_db()
        argv += ["--item", f"{item.pk}:{item.version}"]
    if note is not None:
        argv += ["--note", note]
    return run(*argv, "--request-key", str(key or uuid4()), *extra, answer=answer)


def test_information_counts_ids_and_decisions(live_response_service, google, cli):  # noqa: F811
    """``report information --stage`` and ``information list`` read the
    worklist as the page does; ``information decide`` records the page's
    revision and event through its service, once per key; Handled asks."""
    harness = live_response_service
    item = requested(harness)
    browser, _ = signed_in()
    run = cli()

    parity(
        "information_viewed",
        lambda: read(browser, INFORMATION_ROUTE),
        lambda: run("information", "list"),
    )
    code, document = run("information", "list")
    assert code == 0, document
    (row,) = document["result"]["requests"]
    assert row["item_id"] == str(item.pk) and row["version"] == item.version
    assert (row["stage"], row["decision"], row["actions"]) == (
        "to_review",
        None,
        ["accept", "ignore"],
    )
    assert TEXT not in str(document) and "@" not in str(document)
    code, document = run("report", "information", "--stage", "to_review")
    assert code == 0, document
    assert document["result"]["matching"] == 1
    assert document["result"]["stages"] == {
        "to_review": 1,
        "to_handle": 0,
        "ignored": 0,
        "handled": 0,
        "history": 0,
    }
    code, document = run("report", "information", "--stage", "all", "--needed", "yes")
    assert code == 1 and document["error"]["code"] == "invalid", document

    # The page's Accept, then the command's Back to review and Accept: the
    # command's revision is the page's, with the same event.
    item.refresh_from_db()
    form = {
        "decide": "accept",
        "item": str(item.pk),
        "version": str(item.version),
        "request_key": str(uuid4()),
        "note": NOTE,
    }
    response, _ = search(browser, INFORMATION_ROUTE, form)
    assert response.status_code == 200
    page = AdditionalInformationRevision.objects.get()
    code, document = inform(run, "back_to_review")
    assert code == 2 and document["error"]["code"] == "usage", document
    code, document = inform(run, "back_to_review", item)
    assert code == 0, document
    key = uuid4()
    code, document = inform(run, "accept", item, key=key)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    (decided,) = result["decisions"]
    assert decided["item_id"] == str(item.pk)
    command = AdditionalInformationRevision.objects.get(pk=decided["id"])
    fields = ("review_decision", "follow_up_needed", "followed_up", "notes")
    assert [getattr(command, name) for name in fields] == [
        getattr(page, name) for name in fields
    ]
    assert command.expected_version == page.expected_version + 2
    # The actor is the session's Administrator, in the row and both events.
    assert command.actor_id == page.actor_id
    events = AuditEvent.objects.filter(event_type__in=(UPDATED, INFORMATION_EVENT))
    assert {event.actor_id for event in events} == {page.actor_id}
    assert len(set(map(frozenset, contexts(UPDATED)))) == 1
    assert (count(UPDATED), count(INFORMATION_EVENT)) == (3, 2)
    assert NOTE not in str(document)

    # A repeat with the same key returns the first row and records nothing;
    # the key with another decision is invalid; someone else's decision
    # first is stale.
    code, document = run(
        *("information", "decide", "--action", "accept"),
        *("--item", f"{item.pk}:{command.expected_version}"),
        *("--request-key", str(key)),
    )
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["decisions"] == result["decisions"]
    code, document = run(
        *("information", "decide", "--action", "ignore"),
        *("--item", f"{item.pk}:{command.expected_version}"),
        *("--request-key", str(key)),
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = run(
        *("information", "decide", "--action", "back_to_review"),
        *("--item", f"{item.pk}:{command.expected_version}"),
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = run("information", "list", "--stage", "to_handle")
    assert code == 0 and document["result"]["requests"][0]["actions"] == [
        "back_to_review",
        "handled",
    ]

    # Handled is final, so it asks first; --yes answers.
    code, document = inform(run, "handled", item)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    code, document = inform(run, "handled", item, answer=b"yes\n")
    assert code == 0, document
    assert document["result"]["decisions"][0]["followed_up"]
    assert (count(UPDATED), count(INFORMATION_EVENT)) == (4, 3)
    # Back to review undoes Handled (#1840), only with a new note.
    code, document = inform(run, "back_to_review", item)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert document["error"]["fields"][0]["field"] == "note"
    code, document = inform(run, "back_to_review", item, note="Undo")
    assert code == 0, document
    (undone,) = document["result"]["decisions"]
    assert (undone["decision"], undone["follow_up_needed"]) == ("accepted", True)
    assert not undone["followed_up"]
    code, document = run("information", "list", "--stage", "to_handle")
    assert code == 0 and document["result"]["requests"][0]["item_id"] == str(item.pk)


def test_ignored_needs_a_note_and_bulk_is_all_or_none(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """Back to review from Ignored needs a new note, as the page's dialog
    asks; a bulk decision with one stale request records nothing."""
    harness = live_response_service
    old = requested(harness, "PRIVATE first words")
    # Answering again with other words replaces the request: the first one
    # is History now.
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = TEXT
    item = AdditionalInformationItem.objects.get(
        submission=submit(harness, form, answers).submission
    )
    signed_in()
    run = cli()
    code, document = inform(run, "ignore", item)
    assert code == 0, document
    code, document = inform(run, "back_to_review", item)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert [field["field"] for field in document["error"]["fields"]] == ["note"]
    # Standard input carries the note, so it cannot carry an answer: --yes.
    code, document = inform(run, "back_to_review", item, note="-", answer=NOTE.encode())
    assert code == 2 and document["error"]["code"] == "usage", document
    code, document = inform(
        run, "back_to_review", item, note="-", extra=("--yes",), answer=NOTE.encode()
    )
    assert code == 0, document
    assert AdditionalInformationRevision.objects.latest("expected_version").notes == (
        NOTE
    )
    revisions = AdditionalInformationRevision.objects.count()
    code, document = run("information", "list", "--stage", "history")
    assert [row["item_id"] for row in document["result"]["requests"]] == [str(old.pk)]
    assert document["result"]["requests"][0]["actions"] == []
    code, document = inform(run, "accept", old, item)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert AdditionalInformationRevision.objects.count() == revisions
    assert count(INFORMATION_EVENT) == 2


def test_information_refusals(live_response_service, google, cli, monkeypatch):  # noqa: F811
    """An unknown request is not available; a read-only session and a
    Ministry leader are denied; Staff decide; none of the refusals records."""
    harness = live_response_service
    item = requested(harness)
    signed_in()
    run = cli()
    code, document = run(
        *("information", "decide", "--action", "accept", "--item", f"{uuid4()}:1")
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    reader = read_only_session(run.service)
    item.refresh_from_db()
    code, document = run(
        *("information", "decide", "--action", "accept"),
        *("--item", f"{item.pk}:{item.version}"),
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    code, document = run("information", "list", secret=reader)
    assert code == 0, document
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"})
        code, document = inform(run, "accept", item)
        assert code == 1 and document["error"]["code"] == "denied", document
        code, document = run("information", "list")
        assert code == 1 and document["error"]["code"] == "denied", document
    assert AdditionalInformationRevision.objects.count() == 0
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = inform(run, "ignore", item)
        assert code == 0 and document["result"]["created"], document
    assert (count(UPDATED), count(INFORMATION_EVENT)) == (1, 1)


# ------------------------------------------------------------ Limitations


def everyone(response_service):
    """A live response in which a Member, a proposed Member and the Family
    give their limitation; the three answers' item ids."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    response, added = submit_everyone(harness)
    duid = FamilyCampaign.objects.get(pk=response.family_id).family_duid
    return harness, [
        answer_id(duid, "member", "3"),
        answer_id(duid, "proposed_member", added),
        answer_id(duid, "family"),
    ]


def export(run, *argv):
    """``export limitations`` with ``argv``: (exit code, bytes, document)."""
    return stream(None, "export", "limitations", *argv, secret=run.secret)


def limit(run, action, versions, *extra, key=None, answer=b""):
    """``limitations decide`` on ``versions`` (item id to version)."""
    items = [
        value
        for item, version in versions.items()
        for value in ("--item", f"{item}:{version}")
    ]
    return run(
        *("limitations", "decide", "--action", action, *items),
        *("--request-key", str(key or uuid4())),
        *extra,
        answer=answer,
    )


def test_limitations_counts_files_and_decisions(response_service, google, cli):  # noqa: F811
    """``report limitations --stage`` counts by stage; ``export limitations
    --stage`` is the page's file at the Stage and JSON Lines with the ids;
    ``limitations decide`` records the page's rows, all or none, once per
    key, printing no DUID."""
    harness, (member, proposed, family) = everyone(response_service)
    browser, _ = signed_in()
    run = cli()

    parity(
        "talents_report_viewed",
        lambda: read(browser, LIMITATIONS_ROUTE),
        lambda: run("report", "limitations", "--stage", "to_review"),
    )
    code, document = run("report", "limitations")
    assert code == 0, document
    assert document["result"] | {"campaign_id": None} == {
        "campaign_id": None,
        "cannot_serve": 2,
        "cannot_attend": 1,
        "stage": "all",
        "stages": {"to_review": 3, "to_handle": 0, "ignored": 0, "handled": 0},
    }

    code, body, document = export(run, "--format", "jsonl")
    assert code == 0 and document["result"]["content_type"] == "application/x-ndjson"
    records = [json.loads(line) for line in body.decode().splitlines()]
    assert {record["item_id"] for record in records} == {member, proposed, family}
    assert {record["version"] for record in records} == {0}
    assert all(record["actions"] == ["accept", "ignore"] for record in records)

    key = uuid4()
    code, document = limit(run, "accept", {member: 0, proposed: 0, family: 0}, key=key)
    assert code == 0 and document["result"]["created"], document
    decisions = document["result"]["decisions"]
    assert [row["kind"] for row in decisions] == ["member", "proposed_member", "family"]
    assert {row["decision"] for row in decisions} == {"accepted"}
    assert "item_id" not in decisions[0] and str(member) not in str(document)
    rows = {row.kind: row for row in LimitationReview.objects.all()}
    assert {row.request_key for row in rows.values()} == {
        bulk_key_for(key, item) for item in (member, proposed, family)
    }
    assert (count(REVIEWED), count(LIMITATIONS_EVENT)) == (3, 1)
    # A replay records nothing; the key for one answer alone, or another
    # decision, is invalid.
    code, again = limit(run, "accept", {member: 0, proposed: 0, family: 0}, key=key)
    assert code == 0 and not again["result"]["created"], again
    assert again["result"]["decisions"] == decisions
    code, refused = limit(run, "accept", {member: 0}, key=key)
    assert code == 1 and refused["error"]["code"] == "invalid", refused
    assert (count(REVIEWED), count(LIMITATIONS_EVENT)) == (3, 1)

    # The page's download at the Stage is the command's, byte for byte.
    form = {"format": "csv", "timezone": "UTC", "show": "any", "stage": "to_handle"}
    found = {}

    def page():
        """The page's download, as the browser posts it."""
        response, found["page"] = search(browser, LIMITATIONS_EXPORT, form)
        assert response.status_code == 200

    parity(
        "talents_report_exported",
        page,
        lambda: found.update(
            command=export(run, "--stage", "to_handle", "--format", "csv")
        ),
    )
    code, body, document = found["command"]
    assert code == 0 and body == found["page"], document
    assert (document["result"]["stage"], document["result"]["count"]) == (
        "to_handle",
        3,
    )
    # A Stage that leaves every answer out: both events still count what
    # the read returned, as the page's does; the file lists none.
    parity(
        "talents_report_exported",
        lambda: search(browser, LIMITATIONS_EXPORT, form | {"stage": "to_review"}),
        lambda: found.update(
            command=export(run, "--stage", "to_review", "--format", "csv")
        ),
    )
    assert found["command"][2]["result"]["count"] == 0
    assert counted("talents_report_exported")[-2:] == [3, 3]
    code, body, document = export(run, "--stage", "ignored", "--format", "pdf")
    assert code == 0 and body.startswith(b"%PDF") and document["result"]["count"] == 0

    # One answer stale in a set: nothing is recorded.
    code, refused = limit(run, "handled", {member: 1, family: 0}, "--yes")
    assert code == 1 and refused["error"]["code"] == "stale_version", refused
    assert count(REVIEWED) == 3
    # Handled asks first; Ignore then reopening needs a note.
    code, document = limit(run, "handled", {member: 1})
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    code, document = limit(run, "handled", {member: 1}, "--yes")
    assert code == 0, document
    code, document = limit(run, "back_to_review", {family: 1})
    assert code == 0, document
    code, document = limit(run, "ignore", {family: 2})
    assert code == 0, document
    code, document = limit(run, "back_to_review", {family: 3})
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert [field["field"] for field in document["error"]["fields"]] == ["note"]
    code, document = limit(run, "back_to_review", {family: 3}, "--note", "Reopen")
    assert code == 0, document
    code, document = run("report", "limitations", "--stage", "to_review")
    assert code == 0, document
    assert (
        document["result"]["cannot_serve"],
        document["result"]["cannot_attend"],
    ) == (
        0,
        1,
    )
    assert document["result"]["stages"] == {
        "to_review": 1,
        "to_handle": 1,
        "ignored": 0,
        "handled": 1,
    }
    assert (count(REVIEWED), count(LIMITATIONS_EVENT)) == (7, 5)


def test_limitations_refusals(response_service, google, cli, monkeypatch):  # noqa: F811
    """A malformed id is invalid, another Family's not available; a
    read-only session and a Ministry leader are denied; none records."""
    _harness, (member, _proposed, _family) = everyone(response_service)
    signed_in()
    run = cli()
    code, document = limit(run, "accept", {"m041-3": 0})
    assert code == 1 and document["error"]["code"] == "invalid", document
    code, document = limit(run, "accept", {"f999999": 0})
    assert code == 1 and document["error"]["code"] == "not_available", document
    reader = read_only_session(run.service)
    code, document = run(
        *("limitations", "decide", "--action", "accept", "--item", f"{member}:0"),
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"})
        code, document = limit(run, "accept", {member: 0})
        assert code == 1 and document["error"]["code"] == "denied", document
        code, document = run("report", "limitations", "--stage", "to_review")
        assert code == 1 and document["error"]["code"] == "denied", document
    assert LimitationReview.objects.count() == 0 and count(LIMITATIONS_EVENT) == 0
