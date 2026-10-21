"""``followup list``, ``show``, ``update`` and ``bulk`` (#463 PR 11a).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The reads go
through the Ministry requests page's own admission and selection under the
campaign read guard and record the page's ``ministry_followup_viewed``; the
changes go through the page's services inside its command scope and record
the services' events plus the command's own, once per request key. The
documents print ids, versions, states and Ministries only.
"""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import admin_editing, sessions
from parishkit.stewardship.admin_followup import FUTURE_CONTACT
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.reports import ministry_followup_views
from parishkit.stewardship.workflows.followup import _latest_notes
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryWorkflowRevision,
)

from .auth_builders import signed_in
from .test_admin_export_cli_postgresql import read_only_session
from .test_admin_report_cli_postgresql import parity
from .test_admin_report_email_cli_postgresql import revoke
from .test_admin_status_cli_postgresql import (
    admin,  # noqa: F401
    one,
    session,
)
from .test_admin_task_retry_cli_postgresql import approved_by_browser, runner
from .test_export_views_postgresql import post
from .test_ministry_followup_postgresql import requests
from .test_ministry_reports_postgresql import setup
from .test_ministry_responses_postgresql import ministry_source
from .test_report_workspace_postgresql import read

pytestmark = pytest.mark.django_db(transaction=True)

QUEUE = "/admin/reports/ministries/"
VIEWED = "ministry_followup_viewed"
UPDATED = "ministry_request_updated"
BULK = "ministry_followup_bulk_applied"
UPDATE_EVENT = "admin_cmd_followup_update"
BULK_EVENT = "admin_cmd_followup_bulk"
NOTE = "PRIVATE-NOTE left a message"
CONTACT = "PRIVATE-CONTACT 555-0100"


def member_names():
    """The harness Member's first and last name, which no document prints."""
    member = ministry_source().members[3]
    return [member["firstName"], member["lastName"]]


def private(document):
    """Whether a document prints the Member, the notes or the contact notes."""
    text = str(document)
    return any(value in text for value in [*member_names(), NOTE, CONTACT, "@"])


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


def scenario(response_service):
    """The harness's join (Ministry 9) and leave (Ministry 4), a signed-in
    Administrator's browser, and its command line."""
    setup(response_service)
    browser, _ = signed_in()
    join, leave = requests()
    return browser, join, leave


def update(run, target, *extra, version=None, key=None, answer=b""):
    """``followup update`` of ``target`` at its current (or given) version."""
    target.refresh_from_db()
    return run(
        *("followup", "update", str(target.pk)),
        *("--expected-version", str(target.version if version is None else version)),
        *("--request-key", str(key or uuid4())),
        *extra,
        answer=answer,
    )


def test_the_reads_are_the_page_s_without_members(response_service, google, cli):
    """The queue and a request read as the page reads them, with the page's
    events, its filters, and no Member, address or notes printed."""
    browser, join, leave = scenario(response_service)
    run = cli()
    detail = reverse("admin:ministry_followup_item", args=[join.pk])

    parity(VIEWED, lambda: read(browser, QUEUE), lambda: run("followup", "list"))
    parity(
        VIEWED,
        lambda: read(browser, detail),
        lambda: run("followup", "show", str(join.pk)),
    )

    code, document = run("followup", "list")
    assert code == 0, document
    result = document["result"]
    assert (result["page"], result["size"], result["total"]) == (1, 50, 2)
    assert result["filters"]["state"] == "unresolved"
    rows = {row["id"]: row for row in result["rows"]}
    assert set(rows) == {str(join.pk), str(leave.pk)}
    assert (rows[str(join.pk)]["ministry"], rows[str(join.pk)]["action"]) == (9, "join")
    assert rows[str(leave.pk)]["action"] == "leave"
    assert rows[str(join.pk)]["state"] == "new" and rows[str(join.pk)]["open"]
    assert rows[str(join.pk)]["member_status"] == "current"
    assert "@" not in str(document) and "member" not in set(rows[str(join.pk)])

    # The page's filters: one Ministry, one kind, a status, the last page.
    code, document = run("followup", "list", "--ministry", "9")
    assert code == 0 and [r["id"] for r in document["result"]["rows"]] == [
        str(join.pk)
    ], document
    code, document = run("followup", "list", "--action", "leave")
    assert code == 0 and [r["id"] for r in document["result"]["rows"]] == [
        str(leave.pk)
    ], document
    code, document = run("followup", "list", "--state", "resolved")
    assert code == 0 and document["result"]["total"] == 0, document
    code, document = run("followup", "list", "--page", "9", "--size", "25")
    assert code == 0 and document["result"]["page"] == 1, document
    assert len(document["result"]["rows"]) == 2

    # A Ministry DUID the page refuses is invalid, with nothing recorded.
    before = count(VIEWED)
    code, document = run("followup", "list", "--ministry", "0")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert count(VIEWED) == before

    code, document = run("followup", "show", str(join.pk))
    assert code == 0, document
    result = document["result"]
    assert result["request"]["id"] == str(join.pk) and result["request"]["version"] == 1
    assert (result["history"], result["more_history"]) == ([], False)
    # Another request, or one that does not exist, is not available.
    code, document = run("followup", "show", str(uuid4()))
    assert code == 1 and document["error"]["code"] == "not_available", document

    # A read-only session reads both.
    reader = read_only_session(run.service)
    code, document = run("followup", "list", secret=reader)
    assert code == 0 and document["result"]["total"] == 2, document
    code, document = run("followup", "show", str(leave.pk), secret=reader)
    assert code == 0, document


def test_update_records_the_page_s_save_once_per_key(response_service, google, cli):
    """A Save through the page's service, its event and the command's once
    per key; a key shared with the page; notes kept unless given."""
    browser, join, leave = scenario(response_service)
    run = cli()

    key = uuid4()
    code, document = update(
        run,
        join,
        *("--state", "in_progress", "--notes", NOTE),
        *("--contact-channel", "phone", "--contact-at", "2026-01-02T15:04:00-08:00"),
        *("--contact-notes", "-"),
        key=key,
        answer=CONTACT.encode(),
    )
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    assert result["revision"]["expected_version"] == 1
    assert result["revision"]["state"] == "in_progress"
    assert result["revision"]["contact_channel"] == "phone"
    assert result["revision"]["contact_at"] == "2026-01-02T23:04:00+00:00"
    assert result["request"] == {
        "id": str(join.pk),
        "version": 2,
        "state": "in_progress",
        "outcome": None,
    }
    assert not private(document)
    assert MinistryWorkflowRevision.objects.get().contact_notes == CONTACT
    assert (count(UPDATED), count(UPDATE_EVENT)) == (1, 1)
    code, document = run("followup", "show", str(join.pk))
    assert code == 0 and len(document["result"]["history"]) == 1, document
    assert not private(document)

    # The same key and change returns the first revision, recording nothing;
    # the same key for another change is invalid.
    code, document = update(
        run, join, "--state", "in_progress", "--notes", NOTE, version=1, key=key
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    same = (
        *("--state", "in_progress", "--notes", NOTE),
        *("--contact-channel", "phone", "--contact-at", "2026-01-02T15:04:00-08:00"),
        *("--contact-notes", CONTACT),
    )
    code, document = update(run, join, *same, version=1, key=key)
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["revision"] == result["revision"]
    assert (count(UPDATED), count(UPDATE_EVENT)) == (1, 1)

    # Without --notes the notes are kept, as the page's form keeps them.
    code, document = update(run, join, "--state", "new")
    assert code == 0, document
    assert _latest_notes([join.pk])[join.pk] == NOTE
    # Notes from standard input replace them.
    code, document = update(
        run, join, "--state", "in_progress", "--notes", "-", answer=b"From stdin\n"
    )
    assert code == 0, document
    assert _latest_notes([join.pk])[join.pk] == "From stdin"

    # The page's Save and the command share the key: the page's form, then
    # the command with the same key and change, records nothing new.
    join.refresh_from_db()
    shared = uuid4()
    form = {
        "expected_version": str(join.version),
        "request_key": str(shared),
        "state": "in_progress",
        "outcome": "",
        "notes": "Shared",
        "contact_channel": "",
    }
    route = reverse("admin:ministry_followup_update", args=[join.pk])
    assert post(browser, route, form).status_code == 302
    before = (count(UPDATED), count(UPDATE_EVENT))
    code, document = update(
        run,
        join,
        *("--state", "in_progress", "--notes", "Shared"),
        version=int(form["expected_version"]),
        key=shared,
    )
    assert code == 0 and not document["result"]["created"], document
    assert (count(UPDATED), count(UPDATE_EVENT)) == before

    # A stale version, and a correctable refusal at its option.
    code, document = update(run, join, "--state", "new", version=1)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # The request is checked before the field rules, as the page's
    # _refused does: a stale form with a mistake is still stale_version.
    code, document = update(run, join, "--state", "resolved", version=1)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # Options without effect are usage errors, never silently dropped.
    for extra in (
        ("--state", "in_progress", "--outcome", "joined"),
        ("--state", "new", "--contact-at", "2026-01-02T10:00:00Z"),
        ("--state", "new", "--contact-notes", "Dropped"),
    ):
        code, document = update(run, join, *extra)
        assert code == 2 and document["error"]["code"] == "usage", extra
    # Past the page's lengths: field errors naming the option.
    code, document = update(
        run,
        join,
        *("--state", "new", "--contact-channel", "phone"),
        *("--contact-at", "2026-01-02T10:00:00Z", "--contact-notes", "c" * 2001),
    )
    assert code == 1 and [f["field"] for f in document["error"]["fields"]] == [
        "contact_notes"
    ], document
    code, document = update(run, join, "--state", "new", "--notes", "n" * 5001)
    assert code == 1 and [f["field"] for f in document["error"]["fields"]] == [
        "notes"
    ], document
    code, document = update(run, join, "--state", "resolved")
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert [field["field"] for field in document["error"]["fields"]] == ["outcome"]
    # Other needs notes: cleared here, since left out they would be kept.
    code, document = update(
        run, join, "--state", "resolved", "--outcome", "other", "--notes", ""
    )
    assert code == 1 and [f["field"] for f in document["error"]["fields"]] == [
        "notes"
    ], document
    code, document = update(run, join, "--state", "new", "--contact-channel", "email")
    assert code == 1 and [f["field"] for f in document["error"]["fields"]] == [
        "contact_at"
    ], document
    code, document = update(
        run,
        join,
        *("--state", "new", "--contact-channel", "email"),
        *("--contact-at", "2999-01-01T00:00:00+00:00"),
    )
    assert code == 1 and document["error"]["fields"] == [
        {"field": "contact_at", "code": "invalid", "message": FUTURE_CONTACT}
    ], document
    # A join cannot record Left.
    code, document = update(
        run, join, "--state", "resolved", "--outcome", "leave_confirmed"
    )
    assert code == 1 and document["error"]["code"] == "invalid", document

    # Resolved, the request is closed: a later Save is stale.
    code, document = update(run, join, "--state", "resolved", "--outcome", "joined")
    assert code == 0 and document["result"]["request"]["state"] == "resolved"
    code, document = update(run, join, "--state", "new")
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # Closed, a form mistake is still stale_version, never a field error.
    code, document = update(run, join, "--state", "resolved")
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # An unknown request is not available.
    code, document = run(
        *("followup", "update", str(uuid4()), "--expected-version", "1"),
        *("--state", "new", "--request-key", str(uuid4())),
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert count(UPDATE_EVENT) == 4


def bulk(run, outcome, *targets, key=None, answer=b"", extra=()):
    """``followup bulk`` of ``targets``, each at its current version."""
    chosen = []
    for target in targets:
        target.refresh_from_db()
        chosen += ["--request", f"{target.pk}:{target.version}"]
    return run(
        *("followup", "bulk", "--outcome", outcome, *chosen),
        *("--request-key", str(key or uuid4())),
        *extra,
        answer=answer,
    )


def test_bulk_asks_then_resolves_all_or_none(response_service, google, cli):
    """The page's bulk outcome after its confirmation, with its events and
    the command's once per key; a mixed or stale choice saves nothing."""
    _, join, leave = scenario(response_service)
    run = cli()

    # Unanswered, nothing changes.
    code, document = bulk(run, "join:joined", join)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert (count(BULK), count(BULK_EVENT)) == (0, 0)
    # The other kind is refused for the whole choice, naming the request.
    code, document = bulk(run, "join:joined", join, leave, answer=b"yes\n")
    assert code == 1 and document["error"]["code"] == "invalid", document
    [problem] = document["error"]["fields"]
    assert problem["field"] == "request" and str(leave.pk) in problem["message"]
    assert not private(document)
    # A contact channel needs its instant; more than 250 requests are
    # refused before the prompt (exit 1, not 4), with nothing changed.
    code, document = bulk(
        run, "join:joined", join, answer=b"yes\n", extra=("--contact-channel", "email")
    )
    assert code == 1 and [f["field"] for f in document["error"]["fields"]] == [
        "contact_at"
    ], document
    many = [item for _ in range(251) for item in ("--request", f"{uuid4()}:1")]
    code, document = run("followup", "bulk", "--outcome", "join:joined", *many)
    assert code == 1 and [f["field"] for f in document["error"]["fields"]] == [
        "request"
    ], document
    assert not MinistryWorkflowRevision.objects.exists()
    # A stale version refuses the whole choice too.
    code, document = run(
        *("followup", "bulk", "--outcome", "join:joined"),
        *("--request", f"{join.pk}:9", "--yes"),
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # Chosen twice is invalid.
    code, document = run(
        *("followup", "bulk", "--outcome", "join:joined", "--yes"),
        *("--request", f"{join.pk}:1", "--request", f"{join.pk}:1"),
    )
    assert code == 1 and document["error"]["code"] == "invalid", document

    key = uuid4()
    code, document = bulk(
        run,
        "join:joined",
        join,
        key=key,
        answer=b"yes\n",
        extra=("--contact-channel", "email", "--contact-at", "2026-01-02T10:00:00Z"),
    )
    assert code == 0, document
    result = document["result"]
    assert (result["created"], result["changed"], result["outcome"]) == (
        True,
        1,
        "join:joined",
    )
    assert result["requests"] == [
        {"id": str(join.pk), "version": 2, "state": "resolved", "outcome": "joined"}
    ]
    assert (count(BULK), count(UPDATED), count(BULK_EVENT)) == (1, 1, 1)

    # A repeat with the key (and its versions) records nothing new.
    code, document = run(
        *("followup", "bulk", "--outcome", "join:joined"),
        *("--request", f"{join.pk}:1", "--request-key", str(key), "--yes"),
        *("--contact-channel", "email", "--contact-at", "2026-01-02T10:00:00Z"),
    )
    assert code == 0, document
    assert (document["result"]["created"], document["result"]["changed"]) == (False, 0)
    assert (count(BULK), count(UPDATED), count(BULK_EVENT)) == (1, 1, 1)
    # The leave, with its own outcome.
    code, document = bulk(run, "leave:declined", leave, extra=("--yes",))
    assert code == 0 and document["result"]["changed"] == 1, document
    assert MinistryRequest.objects.get(pk=leave.pk).outcome == "declined"
    assert count(BULK_EVENT) == 2


def with_roles(monkeypatch, roles, ministries=frozenset()):
    """Give the session's principal these roles from now on (as Staff's).

    Pairing needs an Administrator, so the admission's principal is changed
    afterwards. The page's module and the role-first check
    (``admin_editing``) hold their own references to the admission, so both
    are patched too.
    """
    real = sessions.authenticated_admin

    def changed(*arguments, **options):
        """The real admission, its principal holding only ``roles``."""
        principal = real(*arguments, **options)
        if principal is None:
            return None
        return replace(principal, roles=frozenset(roles), ministries=ministries)

    monkeypatch.setattr(sessions, "authenticated_admin", changed)
    monkeypatch.setattr(ministry_followup_views, "authenticated_admin", changed)
    monkeypatch.setattr(admin_editing, "authenticated_admin", changed)


def test_refusals(response_service, google, cli, monkeypatch):
    """Staff read the queue; a read-only session changes nothing; an ended
    session is exit 5 for every command, with nothing recorded."""
    _, join, leave = scenario(response_service)
    run = cli()
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        code, document = run("followup", "list")
        assert code == 0 and document["result"]["total"] == 2, document
        # Staff record follow-up and bulk outcomes, as on the page.
        code, document = update(run, join, "--state", "in_progress")
        assert code == 0 and document["result"]["created"], document
        code, document = bulk(run, "leave:declined", leave, extra=("--yes",))
        assert code == 0 and document["result"]["changed"] == 1, document
    # A principal with no role is denied before the bulk prompt (exit 1,
    # not 4), with nothing recorded.
    with monkeypatch.context() as patch:
        with_roles(patch, set())
        code, document = bulk(run, "join:joined", join)
        assert code == 1 and document["error"]["code"] == "denied", document
    # A Ministry leader runs a bulk outcome on their own Ministry's request,
    # as the page admits them; another Ministry's request is not available.
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"}, frozenset({9}))
        code, document = bulk(run, "leave:declined", leave, extra=("--yes",))
        assert code == 1 and document["error"]["code"] == "not_available", document
        code, document = bulk(run, "join:declined", join, extra=("--yes",))
        assert code == 0 and document["result"]["changed"] == 1, document
    assert MinistryRequest.objects.get(pk=join.pk).outcome == "declined"
    # A Ministry leader of another Ministry: the request answers exactly as
    # an unknown one does, so nothing says it exists.
    with monkeypatch.context() as patch:
        with_roles(patch, {"ministry_leader"}, frozenset({4}))
        code, hidden = update(run, join, "--state", "resolved")
        code_unknown, unknown = run(
            *("followup", "update", str(uuid4()), "--expected-version", "1"),
            *("--state", "resolved", "--request-key", str(uuid4())),
        )
    assert (code, code_unknown) == (1, 1)
    assert hidden["error"] == unknown["error"], (hidden, unknown)
    assert hidden["error"]["code"] == "not_available"
    revisions = MinistryWorkflowRevision.objects.count()

    reader = read_only_session(run.service)
    code, document = run(
        *("followup", "update", str(join.pk), "--expected-version", "1"),
        *("--state", "in_progress", "--request-key", str(uuid4())),
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    code, document = run(
        *("followup", "bulk", "--outcome", "join:joined", "--yes"),
        *("--request", f"{join.pk}:1"),
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "denied", document
    assert MinistryWorkflowRevision.objects.count() == revisions

    revoke(run.service, run.secret)
    before = count(VIEWED)
    for argv in (
        ("followup", "list"),
        ("followup", "show", str(join.pk)),
        (
            *("followup", "update", str(join.pk), "--expected-version", "1"),
            *("--state", "in_progress", "--request-key", str(uuid4())),
        ),
        (
            *("followup", "bulk", "--outcome", "join:joined", "--yes"),
            *("--request", f"{join.pk}:1"),
        ),
    ):
        code, document = run(*argv)
        assert code == 5 and document["error"]["code"] == "session_ended", argv
    assert count(VIEWED) == before
    assert MinistryWorkflowRevision.objects.count() == revisions


def test_another_campaign_s_request_is_not_available(
    response_service,
    google,
    cli,
    monkeypatch,
):
    """A request is changed only from its own campaign's queue: when another
    campaign is the current one (stood in for here), it is not available
    for a Save or a bulk outcome, and nothing is recorded."""
    from parishkit.stewardship import admin_report_reads

    _, join, _ = scenario(response_service)
    run = cli()
    monkeypatch.setattr(admin_report_reads, "_current", lambda: uuid4())
    code, document = update(run, join, "--state", "in_progress")
    assert code == 1 and document["error"]["code"] == "not_available", document
    code, document = bulk(run, "join:joined", join, extra=("--yes",))
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert not MinistryWorkflowRevision.objects.exists()


def test_without_a_current_campaign(admin, google):  # noqa: F811
    """No current campaign: not_available, and nothing recorded."""
    secret = session(admin)
    for argv in (
        ("followup", "list"),
        (
            *("followup", "update", str(uuid4()), "--expected-version", "1"),
            *("--state", "new", "--request-key", str(uuid4())),
        ),
    ):
        code, document = one(admin, *argv, secret=secret)
        assert code == 1 and document["error"]["code"] == "not_available", argv
    assert count(VIEWED) == 0 and count(UPDATE_EVENT) == 0
