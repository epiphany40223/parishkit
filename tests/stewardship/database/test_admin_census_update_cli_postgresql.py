"""``census update-*``: Update ParishSoft from the command line (#1755).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, through the
Update ParishSoft page's own checks (``census_publication``) and services
(``request_preflight``, ``confirm_and_publish``, ``rerun_publication``) in
the page's command scope, so the SQL guards are the page's. The tasks run
on the restricted worker login through the real dispatcher with the
in-memory fake gateway (``World``), so nothing calls ParishSoft. The update
stays off unless a test turns the web's setting on.
"""

import io
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.reports import census_publication_views
from parishkit.stewardship.responses import publication_tasks as tasks
from parishkit.stewardship.responses.models import (
    PublicationConfirmation,
    PublicationPlan,
)

from .auth_builders import signed_in
from .automation_builders import pairing, start
from .test_admin_census_cli_postgresql import with_roles
from .test_admin_status_cli_postgresql import runner
from .test_publication_plans_postgresql import owner, ready  # noqa: F401
from .test_publication_tasks_postgresql import world  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

CHECK, CONFIRM, RERUN = (
    "admin_cmd_census_update_check",
    "admin_cmd_census_update_confirm",
    "admin_cmd_census_update_rerun",
)


def approved(service, browser_session, *, scope="full"):
    """An automation session approved from the signed-in browser's session."""
    from django.db import transaction

    secret, code = start(service, scope=scope)
    principal = current_principal(service.store, browser_session.principal_id)
    with transaction.atomic():
        automation.approve(
            principal,
            browser_session,
            pairing(service).request(code),
            scope="full" if scope == "full" else "read_only",
            days=30,
        )
    return secret


@pytest.fixture
def update_cli(settings, monkeypatch, world):  # noqa: F811
    """``run(*argv, answer=b"", secret=...)`` for the signed-in Administrator.

    Returns (exit code, the one document, standard error). Built after the
    browser signed in, so the session acts as that page's Administrator,
    who is not the ``World``'s (its plans are someone else's checks).
    """

    browsers = []

    def build(*, scope="full"):
        """The runner and a session of ``scope``, once the browser signed in."""
        commands = runner(settings.STEWARDSHIP_AUTH_RUNTIME, monkeypatch)
        if not browsers:
            # The signed-in browser's session; the World's own is forged.
            browsers.append(
                PortalSession.objects.filter(revoked_at__isnull=True)
                .exclude(pk=world.session.pk)
                .get()
            )
        secret = approved(commands.service, browsers[0], scope=scope)

        def run(*argv, answer=b"", secret=secret):
            """One command; its one document and what it wrote to stderr."""
            stderr = io.StringIO()
            code, documents = commands(
                *argv, secret=secret, answer=answer, stderr=stderr
            )
            assert len(documents) == 1, documents
            return code, documents[0], stderr.getvalue()

        run.service, run.secret = commands.service, secret
        return run

    return build


def count(event_type):
    """How many audit events of this type exist."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def refused(document, code, reason):
    """Whether ``document`` is the refusal ``code`` with the page's ``reason``."""
    return (
        not document["ok"]
        and document["error"]["code"] == code
        and document["error"].get("reason") == reason
    )


def test_switched_off_every_command_is_not_available(world, google, update_cli):  # noqa: F811
    """While the web's switch is off each step is refused with the page's
    publication_off, before anything is read or queued."""
    signed_in()
    run = update_cli()
    plan = world.planned(uuid4())
    before = TaskRun.objects.count()
    for argv in (
        ("census", "update-check"),
        ("census", "update-plan"),
        ("census", "update-confirm", str(plan.pk), "--yes"),
        ("census", "update-rerun", str(plan.pk), "--yes"),
    ):
        code, document, _ = run(*argv)
        assert code == 1 and refused(document, "not_available", "publication_off"), (
            argv,
            document,
        )
    assert TaskRun.objects.count() == before
    assert not PublicationConfirmation.objects.exists()
    assert (count(CHECK), count(CONFIRM), count(RERUN)) == (0, 0, 0)


def test_check_plan_and_update_as_the_page_does(
    world,  # noqa: F811
    google,
    settings,
    update_cli,
):
    """Check ParishSoft queues one preflight per key; the plan prints ids,
    counts and outcomes only; Update asks, confirms the plan with the
    command session's sign-in and queues one run per key; once it has run
    the plan shows each change's result."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    signed_in()
    run = update_cli()

    code, document, _ = run("census", "update-plan")
    assert code == 0, document
    result = document["result"]
    assert result["plan"] is None and result["rows"] == []
    assert result["waiting"] == {"changes": 1, "members": 1, "away": 0}
    assert result["check"] == {"available": True, "reason": None}
    assert not result["checking"]

    key = str(uuid4())
    code, document, errors = run("census", "update-check", "--request-key", key)
    assert code == 0, document
    first = document["result"]
    assert first["created"] and first["request_key"] == key
    assert first["task"]["state"] == "queued" and "request key" not in errors
    code, document, _ = run("census", "update-check", "--request-key", key)
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["task"] == first["task"]
    (task,) = TaskRun.objects.filter(task_type=tasks.PREFLIGHT_TASK_TYPE)
    assert str(task.pk) == first["task"]["id"]
    assert (count(CHECK), count("census_publication_preflight_requested")) == (1, 1)
    event = AuditEvent.objects.get(event_type=CHECK)
    assert AuditContext.objects.get(event=event).context == {"outcome": "succeeded"}
    # A check under way is the plan's to show; another check waits for it.
    code, document, _ = run("census", "update-plan")
    assert code == 0 and document["result"]["checking"], document

    assert world.execute(task.pk)
    plan = PublicationPlan.objects.get()
    code, document, _ = run("census", "update-plan")
    assert code == 0, document
    result = document["result"]
    shown = result["plan"]
    assert shown["id"] == str(plan.pk) and not shown["testing"]
    assert shown["checked_by"] == str(plan.actor_id)
    assert shown["counts"] == {
        "write_entities": 1,
        "write_fields": 1,
        "blocked_entities": 0,
        "already": 0,
    }
    assert not shown["confirmed"] and shown["run"] is None and not shown["expired"]
    assert shown["offer"] == {
        "kind": "update",
        "available": True,
        "reason": None,
        "unfinished": 0,
    }
    (row,) = result["rows"]
    assert row["proposal_id"] == str(world.first.pk)
    assert row["proposal_version"] == world.first.version
    assert row["field"] == world.first.field and row["outcome"] == "write"
    # Ids, counts and outcomes only: never the Family's answer or name.
    assert "Requested" not in str(document) and "@" not in str(document)
    views = [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type="census_publication_viewed"
        ).order_by("created_at")
    ]
    assert views[-1] == {"outcome": "succeeded", "count": 1}

    # No answer at the prompt: nothing is confirmed (exit 4).
    code, document, errors = run("census", "update-confirm", str(plan.pk))
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert "Write 1 change for 1 Member to ParishSoft?" in errors
    assert not PublicationConfirmation.objects.exists()

    key = str(uuid4())
    code, document, errors = run(
        "census", "update-confirm", str(plan.pk), "--request-key", key, answer=b"yes\n"
    )
    assert code == 0, document
    confirmed = document["result"]
    assert confirmed["created"] and confirmed["plan_id"] == str(plan.pk)
    assert confirmed["confirmation"]["write_entities"] == 1
    (confirmation,) = PublicationConfirmation.objects.all()
    assert confirmed["confirmation"]["id"] == str(confirmation.pk)
    # The command session's sign-in is the one the guard bound.
    command_session = AuditEvent.objects.get(event_type=CONFIRM).subject_id
    assert command_session is not None
    assert confirmation.actor_id != world.admin
    (update,) = TaskRun.objects.filter(task_type=tasks.PUBLICATION_TASK_TYPE)
    assert confirmed["task"]["id"] == str(update.pk)
    assert (count(CONFIRM), count("census_publication_confirmed")) == (1, 1)
    assert count("automation_fresh_gate") == 1
    # A repeat with the same key returns the same run, asking nothing new.
    code, document, _ = run(
        "census", "update-confirm", str(plan.pk), "--request-key", key, "--yes"
    )
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["task"]["id"] == str(update.pk)
    assert (count(CONFIRM), count("automation_fresh_gate")) == (1, 1)
    # Another key now: the plan's run is under way, as the page says.
    code, document, _ = run("census", "update-confirm", str(plan.pk), "--yes")
    assert code == 1 and refused(document, "not_available", "publication_running")
    code, document, _ = run("census", "update-check")
    assert code == 1 and refused(document, "not_available", "publication_running")

    assert world.execute(update.pk)
    assert len(world.gateway.writes) == 1
    code, document, _ = run("census", "update-plan", str(plan.pk))
    assert code == 0, document
    shown = document["result"]["plan"]
    assert shown["confirmed"] and shown["run"]["state"] == "succeeded"
    assert shown["offer"]["kind"] is None
    assert [row["outcome"] for row in document["result"]["rows"]] == ["updated"]
    # Nothing is Ready any more: Check ParishSoft is refused as invalid.
    assert document["result"]["check"] == {
        "available": False,
        "reason": "publication_nothing_approved",
    }
    code, document, _ = run("census", "update-check")
    assert code == 1 and refused(document, "invalid", "publication_nothing_approved")


def test_update_refuses_what_the_page_would_not_offer(
    world,  # noqa: F811
    google,
    settings,
    update_cli,
    monkeypatch,
):
    """An older plan, an hour-old plan and Testing mode are refused with the
    page's reasons, and a plan of no campaign of ours is not available;
    nothing is confirmed."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    signed_in()
    run = update_cli()
    older = world.planned(uuid4())
    newer = world.planned(uuid4())
    code, document, _ = run("census", "update-confirm", str(older.pk), "--yes")
    assert code == 1 and refused(
        document, "stale_version", "publication_plan_superseded"
    )
    code, document, _ = run("census", "update-plan", str(uuid4()))
    assert code == 1 and document["error"]["code"] == "not_available", document
    with monkeypatch.context() as patch:
        patch.setattr(publication, "PLAN_LIFETIME", timedelta(0))
        code, document, _ = run("census", "update-plan")
        plan = document["result"]["plan"]
        assert plan["expired"] and plan["offer"]["reason"] == (
            "publication_plan_expired"
        ), document
        code, document, _ = run("census", "update-confirm", str(newer.pk), "--yes")
        assert code == 1 and refused(
            document, "stale_version", "publication_plan_expired"
        )
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    code, document, _ = run("census", "update-confirm", str(newer.pk), "--yes")
    assert code == 1 and refused(document, "not_available", "publication_testing")
    assert not PublicationConfirmation.objects.exists()
    assert count(CONFIRM) == 0


def test_counts_that_changed_after_the_question_are_refused(
    world,  # noqa: F811
    google,
    settings,
    update_cli,
    monkeypatch,
):
    """The confirmation binds the counts asked about: counts that differ by
    Apply are the page's publication_plan_changed, and nothing is recorded."""
    from parishkit.stewardship import admin_census_update

    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    signed_in()
    run = update_cli()
    plan = world.planned(uuid4())
    real = admin_census_update.update_preview

    def stale(*arguments, **options):
        """The question as asked, with counts that are no longer the plan's."""
        counts, lines = real(*arguments, **options)
        return counts | {"write_fields": counts["write_fields"] + 1}, lines

    monkeypatch.setattr(admin_census_update, "update_preview", stale)
    code, document, _ = run("census", "update-confirm", str(plan.pk), "--yes")
    assert code == 1 and refused(document, "stale_version", "publication_plan_changed")
    assert not PublicationConfirmation.objects.exists() and count(CONFIRM) == 0


def test_run_again_retries_a_run_that_did_not_finish(
    world,  # noqa: F811
    google,
    settings,
    update_cli,
):
    """A run refused by Testing mode leaves its Member unfinished; Check
    ParishSoft then waits, and Run again asks with the Members left and
    retries the plan's own run once per key; once finished it is refused."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    signed_in()
    run = update_cli()
    plan = world.planned(uuid4())
    _, first = world.confirm(plan)
    owner("UPDATE stewardship_system_configuration SET mode='testing'")
    assert world.execute(first.run_id)
    code, document, _ = run("census", "update-rerun", str(plan.pk), "--yes")
    assert code == 1 and refused(document, "not_available", "publication_testing")
    owner("UPDATE stewardship_system_configuration SET mode='production'")
    code, document, _ = run("census", "update-check")
    assert code == 1 and refused(document, "not_available", "publication_stopped")
    code, document, _ = run("census", "update-plan")
    assert document["result"]["plan"]["offer"] == {
        "kind": "rerun",
        "available": True,
        "reason": None,
        "unfinished": 1,
    }

    key = str(uuid4())
    code, document, errors = run(
        "census", "update-rerun", str(plan.pk), "--request-key", key, answer=b"yes\n"
    )
    assert code == 0, document
    assert "Try the 1 Member this run did not finish?" in errors
    retried = document["result"]
    assert retried["created"] and retried["plan_id"] == str(plan.pk)
    retry = TaskRun.objects.get(pk=UUID(retried["task"]["id"]))
    assert retry.root_id == first.root_id
    assert (count(RERUN), count("census_publication_rerun_requested")) == (1, 1)
    code, document, _ = run(
        "census", "update-rerun", str(plan.pk), "--request-key", key, "--yes"
    )
    assert code == 0 and not document["result"]["created"], document
    assert document["result"]["task"]["id"] == str(retry.pk)
    assert count(RERUN) == 1

    assert world.execute(retry.pk)
    code, document, _ = run("census", "update-rerun", str(plan.pk), "--yes")
    assert code == 1 and refused(document, "invalid", "publication_finished")
    assert count(RERUN) == 1


def test_staff_and_read_only_sessions(
    world,  # noqa: F811
    google,
    settings,
    update_cli,
    monkeypatch,
):
    """Staff are denied every step before anything is read or asked; a
    read-only session reads the plan but writes nothing."""
    settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = True
    signed_in()
    run = update_cli()
    plan = world.planned(uuid4())
    argvs = (
        ("census", "update-check"),
        ("census", "update-plan"),
        ("census", "update-confirm", str(plan.pk)),
        ("census", "update-rerun", str(plan.pk)),
    )
    with monkeypatch.context() as patch:
        with_roles(patch, {"staff"})
        # The page's module holds its own reference to the admission.
        from parishkit.stewardship.accounts import sessions

        patch.setattr(
            census_publication_views,
            "authenticated_admin",
            sessions.authenticated_admin,
        )
        for argv in argvs:
            code, document, errors = run(*argv)
            assert code == 1 and document["error"]["code"] == "denied", (argv, document)
            assert "Type yes" not in errors
    reader = update_cli(scope="read-only")
    code, document, _ = reader("census", "update-plan")
    assert code == 0 and document["result"]["plan"]["id"] == str(plan.pk), document
    for argv in (argvs[0], argvs[2] + ("--yes",), argvs[3] + ("--yes",)):
        code, document, _ = reader(*argv)
        assert code == 1 and document["error"]["code"] == "denied", (argv, document)
    assert not TaskRun.objects.filter(task_type=tasks.PUBLICATION_TASK_TYPE).exists()
    assert (count(CHECK), count(CONFIRM), count(RERUN)) == (0, 0, 0)
