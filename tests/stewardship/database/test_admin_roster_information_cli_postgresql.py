"""``followup roster-entered``, ``roster-bulk``, ``information-show`` and
``information-update`` (#463 PR 11b).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The changes
go through the pages' own services inside the page's command scope and
record the services' events plus the command's own, once per request key;
the item read goes through the information page's admission and selection
under the campaign read guard and records the page's ``information_viewed``.
The documents print ids, versions, flags and instants only.
"""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.reports import (
    information_views,
    ministry_followup_views,
    ministry_roster_views,
)
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    AdditionalInformationRevision,
)
from parishkit.stewardship.workflows.models import MinistryRosterEntry

from .auth_builders import signed_in
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import parity
from .test_admin_task_retry_cli_postgresql import approved_by_browser, runner
from .test_export_views_postgresql import post
from .test_information_followup_postgresql import search
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import setup
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

TICKED = "ministry_roster_entered"
TICK_EVENT = "admin_cmd_followup_roster_entered"
BULK_EVENT = "admin_cmd_followup_roster_bulk"
VIEWED = "information_viewed"
UPDATED = "information_updated"
UPDATE_EVENT = "admin_cmd_followup_information_update"
TEXT = "PRIVATE-TEXT please call our household"
NOTE = "PRIVATE-NOTE called them back"


@pytest.fixture
def cli(settings, monkeypatch):
    """``run(*argv, answer=b"")`` with a full-scope session from the browser.

    Returns (exit code, document); ``answer`` follows the preamble on
    standard input (a prompt's typed line, or a ``-`` input).
    """

    def build():
        """The runner and its session, once the browser has signed in."""
        commands = runner(settings.STEWARDSHIP_AUTH_RUNTIME, monkeypatch)
        secret = approved_by_browser(commands.service)

        def run(*argv, secret=secret, answer=b""):
            """One command that prints exactly one document."""
            code, document, _ = commands(*argv, secret=secret, answer=answer)
            return code, document

        run.service, run.secret = commands.service, secret
        return run

    return build


def count(event_type):
    """How many events of ``event_type`` have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def with_roles(monkeypatch, roles, ministries=frozenset()):
    """Give the session's principal these roles from now on (as Staff's).

    Pairing needs an Administrator, so the admission's principal is changed
    afterwards, in every page module that holds its own reference.
    """
    real = sessions.authenticated_admin

    def changed(*arguments, **options):
        """The real admission, its principal holding only ``roles``."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset(roles), ministries=ministries)

    monkeypatch.setattr(sessions, "authenticated_admin", changed)
    for module in (ministry_followup_views, ministry_roster_views, information_views):
        monkeypatch.setattr(module, "authenticated_admin", changed)


# ------------------------------------------------------------- roster tick


def resolved(response_service):
    """The harness's join (Ministry 9) resolved as joined, and the leave
    (Ministry 4) resolved as leave confirmed, by the browser's Administrator."""
    harness = setup(response_service)
    browser, _ = signed_in()
    admin = user("admin@example.org").pk
    join, leave = requests()
    edit(harness, admin, join, state="resolved", outcome="joined")
    edit(harness, admin, leave, state="resolved", outcome="leave_confirmed")
    return browser, join, leave


def sequence(target):
    """The tick's current sequence, as ``followup show`` prints it."""
    latest = (
        MinistryRosterEntry.objects.filter(request=target).order_by("-sequence").first()
    )
    return latest.sequence if latest else 0


def tick(run, target, choice="--set", *, version=None, key=None):
    """``followup roster-entered`` at the tick's current (or given) sequence."""
    return run(
        *("followup", "roster-entered", str(target.pk), choice),
        *("--expected-version", str(sequence(target) if version is None else version)),
        *("--request-key", str(key or uuid4())),
    )


def test_the_tick_is_the_page_s_once_per_key(response_service, google, cli):
    """Set, repeat, clear, a stale sequence and a refused tick, with the
    service's event and the command's, as the request page's tick records."""
    browser, join, leave = resolved(response_service)
    run = cli()
    code, document = run("followup", "show", str(join.pk))
    assert code == 0 and document["result"]["request"]["roster"] == {
        "eligible": True,
        "entered": False,
        "sequence": 0,
    }, document

    key = uuid4()
    code, document = tick(run, join, key=key)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    assert (result["entry"]["sequence"], result["entry"]["entered"]) == (1, True)
    assert result["entry"]["request_id"] == str(join.pk)
    assert (count(TICKED), count(TICK_EVENT)) == (1, 1)
    # The same key and change returns the first entry and records nothing;
    # the same key for another change is invalid.
    code, document = tick(run, join, version=0, key=key)
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["entry"] == result["entry"]
    code, document = tick(run, join, "--clear", version=0, key=key)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert (count(TICKED), count(TICK_EVENT)) == (1, 1)

    # Already entered: invalid at --set. A stale sequence: stale_version.
    code, document = tick(run, join)
    assert code == 1 and document["error"]["fields"][0]["field"] == "set", document
    code, document = tick(run, join, "--clear", version=0)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = tick(run, join, "--clear")
    assert code == 0 and not document["result"]["entry"]["entered"], document
    assert (count(TICKED), count(TICK_EVENT)) == (2, 2)

    # The page's tick and the command share the sequence: a page tick makes
    # the command's view of the tick stale.
    route = reverse("admin:ministry_followup_roster", args=[join.pk])
    form = {"sequence": "3", "entered": "yes", "request_key": str(uuid4())}
    assert post(browser, route, form).status_code == 302
    code, document = tick(run, join, "--set", version=2)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert sequence(join) == 3

    # A leave confirmed takes the tick too; no document names the Member.
    code, document = tick(run, leave)
    assert code == 0 and document["result"]["entry"]["entered"], document
    assert "@" not in str(document)


def test_the_bulk_mark_is_the_list_s_all_or_none(response_service, google, cli):
    """The list's bulk mark: asks first, all or none, a repeat replays, the
    command's event once per run that marked something."""
    _, join, leave = resolved(response_service)
    run = cli()
    argv = (
        *("followup", "roster-bulk"),
        *("--request", f"{join.pk}:0", "--request", f"{leave.pk}:0"),
    )
    key = str(uuid4())
    # Not confirmed: exit 4, nothing recorded.
    code, document = run(*argv, "--request-key", key, answer=b"no\n")
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert count(TICKED) == 0
    code, document = run(*argv, "--request-key", key, answer=b"yes\n")
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["changed"] == 2
    assert {entry["request_id"] for entry in result["entries"]} == {
        str(join.pk),
        str(leave.pk),
    }
    assert (count(TICKED), count(BULK_EVENT)) == (2, 1)
    # A repeat replays both entries and records nothing.
    code, document = run(*argv, "--request-key", key, "--yes")
    assert code == 0 and document["result"]["changed"] == 0, document
    assert (count(TICKED), count(BULK_EVENT)) == (2, 1)
    # A stale sequence, or a request already marked, saves nothing.
    code, document = run(
        *("followup", "roster-bulk", "--yes", "--request", f"{join.pk}:0")
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document = run(
        *("followup", "roster-bulk", "--yes", "--request", f"{join.pk}:1")
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert count(TICKED) == 2
    # The same request twice is invalid before the prompt.
    code, document = run(
        *("followup", "roster-bulk", "--request", f"{join.pk}:1"),
        *("--request", f"{join.pk}:1"),
    )
    assert code == 1 and document["error"]["code"] == "invalid", document


def test_roster_refusals(response_service, google, cli, monkeypatch):
    """Staff tick; a Ministry leader is denied before anything (and before
    the bulk prompt); another campaign's request is not available; a
    read-only or ended session changes nothing."""
    from parishkit.stewardship import admin_report_reads

    _, join, leave = resolved(response_service)
    run = cli()
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = tick(run, join)
        assert code == 0 and document["result"]["created"], document
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"}, frozenset({4, 9}))
        code, document = tick(run, leave)
        assert code == 1 and document["error"]["code"] == "denied", document
        code, document = run("followup", "roster-bulk", "--request", f"{leave.pk}:0")
        assert code == 1 and document["error"]["code"] == "denied", document
    with monkeypatch.context() as patch:
        patch.setattr(admin_report_reads, "_current", lambda: uuid4())
        code, document = tick(run, leave)
        assert code == 1 and document["error"]["code"] == "not_available", document
        code, document = run(
            "followup", "roster-bulk", "--yes", "--request", f"{leave.pk}:0"
        )
        assert code == 1 and document["error"]["code"] == "not_available", document
    entries = MinistryRosterEntry.objects.count()

    reader = read_only_session(run.service)
    code, document = run(
        *("followup", "roster-entered", str(leave.pk), "--set"),
        *("--expected-version", "0", "--request-key", str(uuid4())),
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    revoke(run.service, run.secret)
    for argv in (
        (
            *("followup", "roster-entered", str(leave.pk), "--set"),
            *("--expected-version", "0", "--request-key", str(uuid4())),
        ),
        ("followup", "roster-bulk", "--yes", "--request", f"{leave.pk}:0"),
    ):
        code, document = run(*argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
    assert MinistryRosterEntry.objects.count() == entries


def test_a_request_that_takes_no_tick_is_refused(response_service, google, cli):
    """A request still open takes no tick: ``invalid`` at ``request``, and
    nothing is recorded (the service's own eligibility check, end to end)."""
    harness = setup(response_service)
    signed_in()
    join, _ = requests()
    admin = user("admin@example.org").pk
    run = cli()
    code, document = tick(run, join)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert document["error"]["fields"][0]["field"] == "request", document
    # Resolved by a person but as declined: still no tick to set.
    edit(harness, admin, join, state="resolved", outcome="declined")
    code, document = tick(run, join)
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert document["error"]["fields"][0]["field"] == "request", document
    assert MinistryRosterEntry.objects.count() == 0
    assert count(TICKED) == 0 and count(TICK_EVENT) == 0


# ------------------------------------------------ information follow-up


def information(harness):
    """A Family's live Additional information item and a signed-in browser."""
    submission = respond(harness, TEXT)
    browser, login = signed_in()
    assert login.status_code == 302
    return browser, AdditionalInformationItem.objects.get(submission=submission)


def update(run, item, *extra, version=None, key=None, answer=b""):
    """``followup information-update`` at the item's current (or given) version."""
    item.refresh_from_db()
    return run(
        *("followup", "information-update", str(item.pk)),
        *("--expected-version", str(item.version if version is None else version)),
        *("--request-key", str(key or uuid4())),
        *extra,
        answer=answer,
    )


def latest_notes(item):
    """The item's current Staff notes, as the page's form opens with them."""
    revision = (
        AdditionalInformationRevision.objects.filter(item=item)
        .order_by("-expected_version")
        .first()
    )
    return revision.notes if revision else ""


def test_the_item_read_is_the_page_s_without_its_words(
    live_response_service, google, cli
):
    """The item page's read and event; no Family, text or notes printed."""
    browser, item = information(live_response_service)
    run = cli()
    route = reverse("admin:information_item", args=[item.pk])
    parity(
        VIEWED,
        lambda: read(browser, route),
        lambda: run("followup", "information-show", str(item.pk)),
    )
    code, document = run("followup", "information-show", str(item.pk))
    assert code == 0, document
    result = document["result"]
    assert result["item"]["id"] == str(item.pk)
    assert result["item"]["version"] == item.version
    assert result["item"]["disposition"] == "current_actionable"
    assert not result["item"]["completed"]
    assert (result["history"], result["more_history"]) == ([], False)
    assert TEXT not in str(document) and "@" not in str(document)
    code, document = run("followup", "information-show", str(uuid4()))
    assert code == 1 and document["error"]["code"] == "not_available", document
    reader = read_only_session(run.service)
    code, document = run("followup", "information-show", str(item.pk), secret=reader)
    assert code == 0, document


def test_the_save_is_the_page_s_once_per_key(live_response_service, google, cli):
    """A Save through the page's service, kept values, the clearing
    confirmation, a stale version, and a key shared with the page."""
    browser, item = information(live_response_service)
    run = cli()
    key = uuid4()
    code, document = update(
        run,
        item,
        *("--needed", "yes", "--completed", "yes", "--notes", "-"),
        key=key,
        answer=NOTE.encode(),
    )
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    assert result["revision"]["followed_up"] and result["item"]["follow_up_needed"]
    assert result["item"]["followed_up_at"] is not None
    assert NOTE not in str(document) and TEXT not in str(document)
    assert latest_notes(item) == NOTE
    assert (count(UPDATED), count(UPDATE_EVENT)) == (1, 1)
    # The same key and change replays, even with the values left out.
    code, document = update(run, item, version=result["item"]["version"] - 1, key=key)
    assert code == 0 and not document["result"]["created"], document
    assert (count(UPDATED), count(UPDATE_EVENT)) == (1, 1)
    code, document = update(
        run, item, "--notes", "Other", version=result["item"]["version"] - 1, key=key
    )
    assert code == 1 and document["error"]["code"] == "invalid", document

    # Left out, the flags and notes are kept.
    code, document = update(run, item, "--needed", "no")
    assert code == 0, document
    item.refresh_from_db()
    assert not item.follow_up_needed and item.followed_up_at is not None
    assert latest_notes(item) == NOTE
    # Clearing a completed follow-up needs --confirm-clear.
    code, document = update(run, item, "--completed", "no")
    assert code == 1 and document["error"]["fields"][0]["field"] == "confirm_clear"
    code, document = update(run, item, "--completed", "no", "--confirm-clear")
    assert code == 0, document
    item.refresh_from_db()
    assert item.followed_up_at is None
    # A stale version.
    code, document = update(run, item, "--needed", "yes", version=1)
    assert code == 1 and document["error"]["code"] == "stale_version", document

    # The worklist's Accept (#1685) and the command share the key: the
    # same change under it replays the page's revision.
    item.refresh_from_db()
    shared = uuid4()
    version = item.version
    form = {
        "decide": "accept",
        "item": str(item.pk),
        "version": str(version),
        "request_key": str(shared),
        "note": NOTE,
    }
    response, _ = search(browser, reverse("admin:information_queue"), form)
    assert response.status_code == 200
    before = (count(UPDATED), count(UPDATE_EVENT))
    code, document = update(
        run,
        item,
        *("--needed", "yes", "--completed", "no", "--notes", NOTE),
        version=version,
        key=shared,
    )
    assert code == 0 and not document["result"]["created"], document
    assert (count(UPDATED), count(UPDATE_EVENT)) == before
    code, document = run("followup", "information-show", str(item.pk))
    assert code == 0 and len(document["result"]["history"]) == 4, document
    assert NOTE not in str(document)


def test_information_refusals(live_response_service, google, cli, monkeypatch):
    """A Ministry leader is denied; another campaign's item is not
    available; a read-only or ended session changes nothing."""
    from parishkit.stewardship import admin_report_reads

    _, item = information(live_response_service)
    run = cli()
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"}, frozenset({9}))
        code, document = update(run, item, "--needed", "yes")
        assert code == 1 and document["error"]["code"] == "denied", document
        viewed = count(VIEWED)
        code, document = run("followup", "information-show", str(item.pk))
        assert code == 1 and document["error"]["code"] == "denied", document
        # The refusal comes before the page's started event: no view row.
        assert count(VIEWED) == viewed
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = update(run, item, "--needed", "yes")
        assert code == 0, document
    with monkeypatch.context() as patch:
        patch.setattr(admin_report_reads, "_current", lambda: uuid4())
        code, document = update(run, item, "--needed", "no")
        assert code == 1 and document["error"]["code"] == "not_available", document
    revisions = AdditionalInformationRevision.objects.count()
    reader = read_only_session(run.service)
    item.refresh_from_db()
    code, document = run(
        *("followup", "information-update", str(item.pk)),
        *("--expected-version", str(item.version), "--needed", "yes"),
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    revoke(run.service, run.secret)
    before = count(VIEWED)
    for argv in (
        ("followup", "information-show", str(item.pk)),
        (
            *("followup", "information-update", str(item.pk)),
            *("--expected-version", str(item.version), "--needed", "yes"),
        ),
    ):
        code, document = run(*argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
    assert count(VIEWED) == before
    assert AdditionalInformationRevision.objects.count() == revisions
