"""Bulk sending built outside the work-order lock (BG-12 PR 3, #447).

Each test runs the real restricted scheduler, worker and mail logins against
the bulk fixture's six mailable Families, with a fake provider standing in
for Gmail. The invitation is planned and prepared at its due time; the mail
consumer's bulk drain then builds each message's content outside the lock
and its commit half writes the build only if the message's send
fingerprint still matches.

What these tests prove, besides each feature:

- **Same result.** A commit from a build writes the same rows and sends the
  same mail, code and link as a commit that builds under the lock, in
  Production and in Testing.
- **Stale builds are never sent.** A change to any fingerprint input between
  build and commit makes the message rebuild under the lock (or be refused
  there, as before); a pause, a close or a response between build and commit
  is honored exactly as without builds.
- **Exactly once and credentials unchanged.** A crash after the
  "submitting" commit leaves at most that batch unknown and never resends;
  no credential row moves, and no build's code reaches a log.
"""

from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import (
    family_mail_bulk,
    family_mail_delivery_tasks,
    family_mail_dispatch,
    family_mail_dispatch_content,
    family_mail_send_builds,
)
from parishkit.stewardship.jobs.family_mail_bulk import BulkSettings
from parishkit.stewardship.jobs.family_mail_content import open_family_credentials
from parishkit.stewardship.jobs.family_mail_dispatch import (
    TASK_TYPE,
    commit_submission,
)
from parishkit.stewardship.jobs.family_mail_send_builds import (
    build_submission,
    send_fingerprint,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import (
    OutboxEvent,
    OutboxMessage,
    OutboxRender,
)
from parishkit.stewardship.jobs.outbox_storage import _status as delivery_status

from .auth_builders import unguarded
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_bulk_postgresql import (  # noqa: F401
    EXTRA,
    Provider,
    _db_now,
    _never,
    _wait_for_lease_expiry,
    due,
    families,
    mail_owner,
    plan,
    prepare_all,
    send_all,
    single,
)
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_prepare_ahead_postgresql import (  # noqa: F401
    RENDER_FIELDS,
    Rollback,
    credential_rows,
    family_mail_render,
    family_mail_sealed,
    invited,
    prepare_shortly_before,
    recorded_timings,
    reminder_messages,
    rotation_in_progress,
    seed,
)
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401
from .test_outbox_boundaries_postgresql import control
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)
FAMILIES = EXTRA + 1
ORIGIN = "http://localhost:8000"
MODES = ["testing", "production"]


def ready(families, monkeypatch, mode):  # noqa: F811
    """Plan and prepare the invitation in ``mode``; return harness, path, provider.

    The provider is installed for every later send. Everything is left due
    at ``due()``, so tests send inside ``campaign_clock(due())``.
    """
    harness, path = families
    if mode == "production":
        harness = activate_response_service(harness)
        complete_empty_catchup(harness.campaign, uuid4())
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
    assert OutboxMessage.objects.filter(state="pending").count() == FAMILIES
    return harness, path, provider


def delivery_tasks():
    """The due Family delivery tasks, oldest message first."""
    return list(
        TaskRun.objects.filter(
            task_type=TASK_TYPE, state__in=("queued", "retry_wait")
        ).order_by("not_before", "id")
    )


def prebuild(harness):
    """Build every due delivery's content now, as the mail login would.

    Returns ``{task id: build}``; a dropped build is None.
    """
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        try:
            return {
                task.pk: build_submission(
                    task.pk, private=harness.rings.private, public_origin=ORIGIN
                )
                for task in delivery_tasks()
            }
        finally:
            connections.close_all()


def use_builds(monkeypatch, builds):
    """Make the next bulk drains use ``builds``, made earlier, as their lookahead.

    Installed before the mail handler is built, since ``delivery_bulk``
    binds the build function then.
    """
    monkeypatch.setattr(
        family_mail_send_builds,
        "build_submission",
        lambda task_id, **kwargs: builds.get(task_id),
    )


def commits(timings):
    """The commit timing lines' values, in order."""
    return [values for kind, values in timings if kind == "commit"]


def message_of(task_id):
    """The outbox message a delivery task sends."""
    return OutboxMessage.objects.get(
        pk=TaskRun.objects.get(pk=task_id).domain_request_id
    )


def submitted(harness, path, task_id, prebuilt):
    """Claim ``task_id``, run the commit half, capture what it wrote, roll back.

    As the mail login, inside one lock transaction like a send batch's.
    Returns the rows, render and mail the commit produced (without
    identifiers or times), what ``report`` said (True for a written build,
    False for a rebuild, absent without a build), and the sealed
    substitutions to open.
    """
    rows = {}
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        try:
            with work_transaction():
                owner = mail_owner(harness, path)
                claim, _ = family_mail_bulk._claim(task_id, owner, uuid4(), 60)
                used = []
                message_id = TaskRun.objects.get(pk=task_id).domain_request_id
                mail, deadline, configuration_id, attempt = commit_submission(
                    message_id,
                    claim,
                    private=harness.rings.private,
                    public_origin=ORIGIN,
                    prebuilt=prebuilt,
                    report=used.append,
                )
                message = OutboxMessage.objects.get(pk=message_id)
                render = OutboxRender.objects.get(pk=message.render_id)
                rows.update(
                    report=used,
                    message=(message.state, message.attempt, message.purpose),
                    render={name: getattr(render, name) for name in RENDER_FIELDS},
                    events=list(
                        OutboxEvent.objects.filter(message=message)
                        .order_by("version")
                        .values_list("version", "action", "state")
                    ),
                    mail=mail,
                    configuration=configuration_id,
                    attempt=attempt,
                    sealed=(
                        delivery_status(message).identity,
                        family_mail_render(render),
                        family_mail_sealed(message),
                    ),
                )
                raise Rollback
        except Rollback:
            pass
        finally:
            connections.close_all()
    return rows


@pytest.mark.parametrize("mode", MODES)
def test_a_prebuilt_commit_writes_what_the_whole_commit_writes(
    families,  # noqa: F811
    monkeypatch,
    mode,
):
    """Same rows, render, mail, code and link, from a build or under the lock."""
    harness, path, provider = ready(families, monkeypatch, mode)
    with campaign_clock(due()):
        task = delivery_tasks()[0]
        build = prebuild(harness)[task.pk]
        assert build is not None
        prebuilt = submitted(harness, path, task.pk, build)
        whole = submitted(harness, path, task.pk, None)
    assert prebuilt.pop("report") == [True] and whole.pop("report") == []
    sealed = prebuilt.pop("sealed"), whole.pop("sealed")
    assert prebuilt == whole
    assert prebuilt["message"][0] == "submitting"
    # The sealed substitutions differ only by their encryption's randomness.
    opened = [
        open_family_credentials(
            identity=identity,
            render=render,
            sealed=value,
            private=harness.rings.private,
        )
        for identity, render, value in sealed
    ]
    assert opened[0].code == opened[1].code
    assert opened[0].token_id == opened[1].token_id
    assert opened[0].code in prebuilt["mail"].text


@pytest.mark.parametrize("mode", MODES)
def test_the_bulk_send_writes_its_builds_once_each(
    families,  # noqa: F811
    monkeypatch,
    mode,
):
    """Every message is sent once, from its build, in Testing and Production.

    No credential row moves, and the one-at-a-time path and a second drain
    find nothing left to send.
    """
    harness, path, provider = ready(families, monkeypatch, mode)
    before = credential_rows()
    timings = recorded_timings(monkeypatch)
    with campaign_clock(due()):
        send_all(harness, path)
        send_all(harness, path)
        for message in OutboxMessage.objects.all():
            single(harness, path, message.task_id)
    lines = commits(timings)
    assert sum(values["prebuilt"] for values in lines) == FAMILIES
    assert sum(values["rebuilt"] for values in lines) == 0
    assert set(OutboxMessage.objects.values_list("state", flat=True)) == {"delivered"}
    assert len(provider.keys) == len(set(provider.keys)) == FAMILIES
    if mode == "testing":
        assert set(provider.calls) == {("test@example.org",)}
    assert credential_rows() == before


# Each input a send build depends on, and a change to it. Every statement
# runs as the test owner with the guards off, inside a rolled-back
# transaction (the fingerprint test) or committed (the rebuild test).
SEND_CHANGES = {
    "message version": "UPDATE stewardship_outbox_message SET version=version+1 "
    "WHERE id=%(message)s",
    "message sealed substitutions": "UPDATE stewardship_outbox_message "
    "SET sealed_substitutions=sealed_substitutions||'x' WHERE id=%(message)s",
    "occurrence version": "UPDATE stewardship_schedule_occurrence "
    "SET version=version+1 WHERE id=%(occurrence)s",
    "schedule revision": "UPDATE stewardship_schedule_definition "
    "SET current_revision_id=gen_random_uuid() WHERE id=(SELECT definition_id "
    "FROM stewardship_schedule_occurrence WHERE id=%(occurrence)s)",
    "system configuration": "UPDATE stewardship_system_configuration "
    "SET active_configuration_id=gen_random_uuid()",
    "testing recipient": "UPDATE stewardship_system_configuration "
    "SET testing_recipient='other@example.org'",
    "campaign configuration": "UPDATE stewardship_campaign "
    "SET active_configuration_id=gen_random_uuid() WHERE id=%(campaign)s",
    "production cycle": "UPDATE stewardship_campaign "
    "SET production_cycle=production_cycle+1 WHERE id=%(campaign)s",
    "family deliverability": "UPDATE stewardship_family_campaign "
    "SET email_deliverable=NOT email_deliverable WHERE id=%(family)s",
    "family response": "UPDATE stewardship_family_campaign "
    "SET effective_submission_id=gen_random_uuid() WHERE id=%(family)s",
    "family source generation": "UPDATE stewardship_family_campaign "
    "SET source_generation=source_generation+1 WHERE id=%(family)s",
    "population": "UPDATE stewardship_campaign_credentials "
    "SET population_dirty=NOT population_dirty WHERE campaign_id=%(campaign)s",
    "promoted source": "UPDATE stewardship_source_current SET generation=generation+1",
    "address refusal": "INSERT INTO stewardship_recipient_refusal "
    "(id,correlation_id,family_id,event_id,address,organization_id,family_duid) "
    "SELECT gen_random_uuid(),gen_random_uuid(),f.id,gen_random_uuid(),"
    "'refused@example.org',s.organization_id,f.family_duid "
    "FROM stewardship_family_campaign f CROSS JOIN stewardship_source_current s "
    "WHERE f.id=%(family)s",
    "hosted file": "INSERT INTO stewardship_hosted_file "
    "(id,correlation_id,version,slug,original_name,kind,size,sha256,token,"
    "uploaded_by_id) VALUES (gen_random_uuid(),gen_random_uuid(),1,'flyer',"
    "'flyer.pdf','pdf',1,repeat('a',64),repeat('A',43),gen_random_uuid())",
    "branding bundle": "INSERT INTO stewardship_branding_bundle "
    "(id,correlation_id,version,owner_id,session_id,expires_at,state,base_id) "
    "VALUES (gen_random_uuid(),gen_random_uuid(),1,gen_random_uuid(),"
    "gen_random_uuid(),statement_timestamp()+interval '1 day','ready',"
    "gen_random_uuid())",
    "token generation": "UPDATE stewardship_family_token_generation "
    "SET state='superseded' WHERE campaign_id=%(campaign)s AND state='active'",
    "credential epoch": "UPDATE stewardship_credential_deployment "
    "SET family_link_epoch=gen_random_uuid()",
    "link token digest": "UPDATE stewardship_family_token "
    "SET digest=repeat('0',64) WHERE family_id=%(family)s",
    "rehearsal credential digest": "UPDATE stewardship_rehearsal_credential "
    "SET token_digest=repeat('0',64) WHERE family_id=%(family)s",
    "key inventory": "UPDATE stewardship_credential_key_state "
    "SET inventory_digest=repeat('0',64) WHERE kind='token_private'",
}
# The changes that only a Production message's fingerprint reads, and the
# one only a Testing message's reads.
PRODUCTION_ONLY = {"token generation", "credential epoch", "link token digest"}
TESTING_ONLY = {"rehearsal credential digest", "testing recipient"}


def mode_changes():
    """Every (mode, change) pair whose change the mode's fingerprint reads."""
    return [
        (mode, change)
        for mode in MODES
        for change in sorted(SEND_CHANGES)
        if not (mode == "testing" and change in PRODUCTION_ONLY)
        and not (mode == "production" and change in TESTING_ONLY)
    ]


def change_values(harness, message):
    """The identifiers a SEND_CHANGES statement names, for one message."""
    return {
        "message": message.pk,
        "occurrence": message.semantic_key,
        "family": message.family_id,
        "campaign": harness.campaign.pk,
    }


@pytest.mark.parametrize("mode,change", mode_changes())
def test_the_send_fingerprint_moves_with_every_input(
    families,  # noqa: F811
    monkeypatch,
    mode,
    change,
):
    """A change to any input between build and commit changes the fingerprint.

    The fingerprint is read under the real mail-dispatch login, which proves
    it needs no read the login lacks.
    """
    harness, path, provider = ready(families, monkeypatch, mode)
    message = OutboxMessage.objects.order_by("id").first()
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        try:
            with transaction.atomic():
                original = send_fingerprint(message.pk)
        finally:
            connections.close_all()
    assert original is not None
    values = change_values(harness, message)
    try:
        with unguarded(), connection.cursor() as cursor:
            assert send_fingerprint(message.pk) == original
            cursor.execute(SEND_CHANGES[change], values)
            assert cursor.rowcount >= 1
            assert send_fingerprint(message.pk) != original
            raise Rollback
    except Rollback:
        pass
    with transaction.atomic():
        assert send_fingerprint(message.pk) == original


# The fingerprint's other inputs, each as (setup, change): the setup (or
# None) runs first, inside the same rolled-back transaction, so a change
# can alter a row the fixture lacks (an existing ready bundle's asset, a
# hosted file, an unresolved refusal), or so a change the table's checks
# would refuse alongside its other columns can be made alone (the
# constraint is dropped only inside that transaction). These only prove the fingerprint
# moves; SEND_CHANGES also drives the rebuild test, which needs changes
# the drain survives.
_REFUSAL = SEND_CHANGES["address refusal"]
_HOSTED = SEND_CHANGES["hosted file"]
_BUNDLE_WITH_ASSET = (
    "WITH bundle AS (" + SEND_CHANGES["branding bundle"] + " RETURNING id) "
    "INSERT INTO stewardship_branding_asset "
    "(id,correlation_id,bundle_id,label,width,height,size,sha256) "
    "SELECT gen_random_uuid(),gen_random_uuid(),bundle.id,'banner',10,10,1,"
    "repeat('b',64) FROM bundle"
)
OTHER_CHANGES = {
    "message state": (
        "ALTER TABLE stewardship_outbox_message "
        "DROP CONSTRAINT outbox_submitted_outcome",
        "UPDATE stewardship_outbox_message SET state='retry_wait' WHERE id=%(message)s",
    ),
    "message render": (
        None,
        "UPDATE stewardship_outbox_message SET render_id=gen_random_uuid() "
        "WHERE id=%(message)s",
    ),
    "message token generation": (
        None,
        "UPDATE stewardship_outbox_message "
        "SET token_generation_id=gen_random_uuid() WHERE id=%(message)s",
    ),
    "message credential epoch": (
        None,
        "UPDATE stewardship_outbox_message "
        "SET credential_epoch_id=gen_random_uuid() WHERE id=%(message)s",
    ),
    "message rehearsal epoch": (
        None,
        "UPDATE stewardship_outbox_message "
        "SET rehearsal_epoch_id=gen_random_uuid() WHERE id=%(message)s",
    ),
    "occurrence state": (
        None,
        "UPDATE stewardship_schedule_occurrence SET state='skipped' "
        "WHERE id=%(occurrence)s",
    ),
    "occurrence outbox": (
        None,
        "UPDATE stewardship_schedule_occurrence SET outbox_id=gen_random_uuid() "
        "WHERE id=%(occurrence)s",
    ),
    "occurrence target": (
        None,
        "UPDATE stewardship_schedule_occurrence "
        "SET target='family:'||gen_random_uuid() WHERE id=%(occurrence)s",
    ),
    "runtime mode": (
        None,
        "UPDATE stewardship_system_configuration SET mode=CASE mode "
        "WHEN 'testing' THEN 'production' ELSE 'testing' END",
    ),
    "runtime campaign": (
        None,
        "UPDATE stewardship_system_configuration "
        "SET current_campaign_id=gen_random_uuid()",
    ),
    "campaign state": (
        None,
        "UPDATE stewardship_campaign SET state='closed' WHERE id=%(campaign)s",
    ),
    "family active": (
        "ALTER TABLE stewardship_family_campaign "
        "DROP CONSTRAINT family_eligible_has_code_cohort",
        "UPDATE stewardship_family_campaign SET active=NOT active WHERE id=%(family)s",
    ),
    "family email eligibility": (
        "ALTER TABLE stewardship_family_campaign "
        "DROP CONSTRAINT family_deliverable_is_eligible",
        "UPDATE stewardship_family_campaign "
        "SET email_eligible=NOT email_eligible WHERE id=%(family)s",
    ),
    "go-live gate": (
        None,
        "UPDATE stewardship_campaign_credentials SET go_live_gate=NOT go_live_gate "
        "WHERE campaign_id=%(campaign)s",
    ),
    "population snapshot": (
        None,
        "UPDATE stewardship_campaign_credentials "
        "SET source_snapshot_id=gen_random_uuid() WHERE campaign_id=%(campaign)s",
    ),
    "population rehearsal epoch": (
        None,
        "UPDATE stewardship_campaign_credentials "
        "SET rehearsal_epoch_id=gen_random_uuid() WHERE campaign_id=%(campaign)s",
    ),
    "promoted snapshot": (
        None,
        "UPDATE stewardship_source_current SET snapshot_id=gen_random_uuid()",
    ),
    "refusal resolved": (
        _REFUSAL,
        "INSERT INTO stewardship_recipient_resolution "
        "(id,correlation_id,refusal_id,source_snapshot_id,source_generation,"
        "reason,evidence_note) SELECT gen_random_uuid(),gen_random_uuid(),"
        "refusal.id,gen_random_uuid(),1,'source_changed','' "
        "FROM stewardship_recipient_refusal refusal",
    ),
    "hosted file token": (
        _HOSTED,
        "UPDATE stewardship_hosted_file SET token=repeat('B',43)",
    ),
    "hosted file removed": (_HOSTED, "DELETE FROM stewardship_hosted_file"),
    "branding asset dimensions": (
        _BUNDLE_WITH_ASSET,
        "UPDATE stewardship_branding_asset SET width=width+1",
    ),
    "token generation epoch": (
        None,
        "UPDATE stewardship_family_token_generation "
        "SET credential_epoch=gen_random_uuid() WHERE campaign_id=%(campaign)s "
        "AND state='active'",
    ),
    "link token destroyed": (
        None,
        "UPDATE stewardship_family_token SET ciphertext=NULL,digest=NULL,"
        "destroyed_at=statement_timestamp() WHERE family_id=%(family)s",
    ),
}
OTHER_PRODUCTION_ONLY = {
    "token generation epoch",
    "link token destroyed",
    "message token generation",
    "message credential epoch",
}
OTHER_TESTING_ONLY = {"message rehearsal epoch"}


@pytest.mark.parametrize(
    "mode,change",
    [
        (mode, change)
        for mode in MODES
        for change in sorted(OTHER_CHANGES)
        if not (mode == "testing" and change in OTHER_PRODUCTION_ONLY)
        and not (mode == "production" and change in OTHER_TESTING_ONLY)
    ],
)
def test_the_send_fingerprint_moves_with_every_other_input(
    families,  # noqa: F811
    monkeypatch,
    mode,
    change,
):
    """The fingerprint's remaining columns and subquery predicates move it too.

    Including the predicates a typo could break silently: a refusal's
    resolution, a destroyed link token, and a ready bundle's asset size.
    """
    harness, path, provider = ready(families, monkeypatch, mode)
    message = OutboxMessage.objects.order_by("id").first()
    values = change_values(harness, message)
    setup, statement = OTHER_CHANGES[change]
    try:
        with unguarded(), connection.cursor() as cursor:
            if setup is not None:
                cursor.execute(setup, values)
                assert cursor.rowcount != 0
            before = send_fingerprint(message.pk)
            assert before is not None
            cursor.execute(statement, values)
            assert cursor.rowcount >= 1
            assert send_fingerprint(message.pk) != before
            raise Rollback
    except Rollback:
        pass


def owner_commit(statement, values=None):
    """Commit ``statement`` as the test owner, guards off, on its own connection.

    Stands in for another process's change landing while a build or a drain
    is in the middle of its own transaction on the mail login's connection.
    """
    import psycopg

    settings = connections["default"].settings_dict
    with (
        psycopg.connect(
            host=settings["HOST"],
            port=settings["PORT"],
            dbname=settings["NAME"],
            user=settings["USER"],
            password=settings["PASSWORD"],
        ) as other,
        other.cursor() as cursor,
    ):
        cursor.execute("SET session_replication_role = replica")
        cursor.execute(statement, values)
        assert cursor.rowcount >= 1


def test_a_change_while_a_build_seals_drops_that_build(
    families,  # noqa: F811
    monkeypatch,
):
    """The build's own recheck, under the key-set lock, drops a moved build.

    The credential epoch moves after the build's snapshot, committed by
    another connection while the build is sealing: the fingerprint read
    again in that transaction differs, so no build is returned (without
    the recheck one would be, and the commit half would catch it later).
    """
    harness, path, provider = ready(families, monkeypatch, "production")
    task = delivery_tasks()[0]
    real = family_mail_dispatch_content.built_content
    sealed = []

    def sealing(*args, **kwargs):
        """Seal as usual, then let another process move the epoch."""
        result = real(*args, **kwargs)
        sealed.append(True)
        owner_commit(
            "UPDATE stewardship_credential_deployment "
            "SET family_link_epoch=gen_random_uuid()"
        )
        return result

    monkeypatch.setattr(family_mail_dispatch_content, "built_content", sealing)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        try:
            build = build_submission(
                task.pk, private=harness.rings.private, public_origin=ORIGIN
            )
        finally:
            connections.close_all()
    assert sealed == [True] and build is None


@pytest.mark.parametrize("mode,change", mode_changes())
def test_a_change_between_build_and_commit_rebuilds_under_the_lock(
    families,  # noqa: F811
    monkeypatch,
    mode,
    change,
):
    """A build made before the change is never written.

    The changed message is rebuilt under the lock (``rebuilt``) or refused
    there exactly as without a build, and is then left to the one-at-a-time
    path; every unchanged message is written from its build. A changed
    Testing recipient reaches the provider, so the rebuild read it.
    """
    harness, path, provider = ready(families, monkeypatch, mode)
    builds = prebuild(harness)
    assert all(builds.values())
    target = message_of(delivery_tasks()[0].pk)
    # Read first: some changes leave the schedule's revision unreadable.
    when = due()
    seed(
        REBUILD_CHANGES.get(change, SEND_CHANGES[change]),
        change_values(harness, target),
    )
    use_builds(monkeypatch, builds)
    reports = recorded_reports(monkeypatch)
    with campaign_clock(when):
        send_all(harness, path)
    # The target's build was never written; every message sent went
    # through the commit half, and none was sent twice.
    assert True not in reports.get(target.pk, [])
    delivered = OutboxMessage.objects.filter(state="delivered")
    assert all(reports.get(message.pk) for message in delivered)
    assert len(provider.keys) == len(set(provider.keys)) == delivered.count()
    if change in HARMLESS:
        # Rebuilt under the lock and sent like any other.
        assert reports[target.pk] == [False]
        assert delivered.count() == FAMILIES
    if change == "testing recipient":
        # The rebuild read the new recipient.
        assert all(value == [False] for value in reports.values())
        assert set(provider.calls) == {("other@example.org",)}


# A send batch reads the active configuration's Workspace integration before
# its first item, so a dangling configuration id would stop the drain before
# any commit half ran; another column of the same runtime row stands in.
REBUILD_CHANGES = {
    "system configuration": "UPDATE stewardship_system_configuration "
    "SET restore_review_required=NOT restore_review_required",
}
# Changes that leave the message sendable: its rebuild under the lock sends it.
HARMLESS = {"occurrence version", "hosted file", "branding bundle"}


def recorded_reports(monkeypatch):
    """Record, per message, what each bulk commit half reported.

    True for a written build, False for one rebuilt under the lock; a
    message refused before or at its content step reports nothing.
    Installed before the mail handler is built, which binds the function.
    """
    reports = {}
    real = family_mail_dispatch.commit_submission

    def commit(identifier, claim, *, report=None, **kwargs):
        """Pass through, keeping each report."""

        def record(value):
            """Keep the report and pass it on."""
            reports.setdefault(identifier, []).append(value)
            if report is not None:
                report(value)

        return real(identifier, claim, report=record, **kwargs)

    monkeypatch.setattr(family_mail_dispatch, "commit_submission", commit)
    return reports


def test_a_carried_build_is_dropped_at_the_end_of_a_drain_and_never_logged(
    families,  # noqa: F811
    monkeypatch,
    caplog,
):
    """Builds a batch never reached are dropped when the drain stops.

    Each batch takes one message (a tiny hold budget), and the drain is
    stopped after its first send: the other messages' builds are dropped
    unused. No build's code or link appears in any log line, and a build's
    repr shows none of its content.
    """
    from threading import Event

    harness, path, provider = ready(families, monkeypatch, "production")
    monkeypatch.setattr(family_mail_bulk, "HOLD_SECONDS", 0.000001)
    stop = Event()
    provider.on_call = lambda count: stop.set()
    made, left = [], []
    real_build = family_mail_send_builds.build_submission

    def build(task_id, **kwargs):
        """Keep each build made, to look for its code in the logs."""
        value = real_build(task_id, **kwargs)
        if value is not None:
            made.append(value)
        return value

    monkeypatch.setattr(family_mail_send_builds, "build_submission", build)
    real_clear = family_mail_bulk._Lookahead.clear

    def clear(self):
        """Record how many builds the drain's end drops."""
        left.append(len(self.builds))
        real_clear(self)
        assert not self.builds

    monkeypatch.setattr(family_mail_bulk._Lookahead, "clear", clear)
    caplog.set_level("DEBUG")
    owner = mail_owner(harness, path)
    hint = delivery_tasks()[0].pk
    with (
        campaign_clock(due()),
        task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True),
    ):
        try:
            owner.bulk(hint, stop=stop)
        finally:
            connections.close_all()
    assert len(provider.calls) == 1
    assert len(made) == FAMILIES and left == [FAMILIES - 1]
    text = caplog.text
    for value in made:
        assert value.reference.code not in text
        assert value.reference.code not in repr(value)
        assert "render=" not in repr(value) and "sealed=" not in repr(value)
    # The rest are left for later passes, which send each one once.
    with campaign_clock(due()):
        send_all(harness, path)
    assert len(provider.keys) == len(set(provider.keys)) == FAMILIES


def test_a_carried_build_changed_after_the_first_batch_is_rebuilt(
    families,  # noqa: F811
    monkeypatch,
):
    """A change between batches reaches a carried build in a later batch.

    With B = 2 and a hold budget that ends each batch after one message,
    each batch builds the next two candidates and carries one. After the
    first batch, a change to the carried message's occurrence (committed
    by another process while the second batch runs) makes it rebuild under
    the lock there; every other message is written from its build, and
    each is sent once.
    """
    harness, path, provider = ready(families, monkeypatch, "production")
    before = credential_rows()
    monkeypatch.setattr(family_mail_bulk, "HOLD_SECONDS", 0.000001)
    reports = recorded_reports(monkeypatch)
    real = family_mail_bulk._run_batch
    batches, target = [], []

    def run_batch(ids, item, **kwargs):
        """Change the first batch's carried message as the second batch starts."""
        batches.append(list(ids))
        if len(batches) == 1:
            target.append(message_of(ids[1]))
        elif len(batches) == 2:
            owner_commit(
                "UPDATE stewardship_schedule_occurrence SET version=version+1 "
                "WHERE id=%s",
                [target[0].semantic_key],
            )
        return real(ids, item, **kwargs)

    monkeypatch.setattr(family_mail_bulk, "_run_batch", run_batch)
    small = mail_owner(harness, path, BulkSettings(True, 2))
    with campaign_clock(due()):
        send_all(harness, path, small)
    assert len(batches) >= 3 and batches[1][0] == batches[0][1]
    assert reports[target[0].pk] == [False]
    assert all(value == [True] for key, value in reports.items() if key != target[0].pk)
    delivered = OutboxMessage.objects.filter(state="delivered")
    assert delivered.count() == FAMILIES == len(reports)
    assert len(provider.keys) == len(set(provider.keys)) == FAMILIES
    assert credential_rows() == before


def test_a_message_held_during_a_pause_is_sent_from_its_build(
    families,  # noqa: F811
    monkeypatch,
):
    """After a resume, a held message's build is written, not rebuilt.

    The one-at-a-time path holds every message while the send is paused.
    After the resume, each is built while still held, and the commit half
    compares the fingerprint before its hold release moves the message's
    version, so every build is written.
    """
    harness, path, provider = ready(families, monkeypatch, "production")
    before = credential_rows()
    with campaign_clock(due()):
        control(harness.campaign, "pause")
        # The bulk send passes paused mail by; the one-at-a-time path holds it.
        for task in delivery_tasks():
            single(harness, path, task.pk)
    assert provider.calls == []
    assert OutboxMessage.objects.filter(pause_hold_id__isnull=False).count() == (
        FAMILIES
    )
    # Each held task waits out its retry delay in database time; let it be
    # due now, as it would be after that delay.
    seed(
        "UPDATE stewardship_task_run SET not_before=statement_timestamp() "
        "WHERE task_type=%(type)s AND state='retry_wait'",
        {"type": TASK_TYPE},
    )
    reports = recorded_reports(monkeypatch)
    with campaign_clock(due()):
        control(harness.campaign, "resume")
        send_all(harness, path)
        send_all(harness, path)
    assert reports and all(value == [True] for value in reports.values())
    assert set(OutboxMessage.objects.values_list("state", flat=True)) == {"delivered"}
    assert len(provider.keys) == len(set(provider.keys)) == FAMILIES
    assert credential_rows() == before


def test_two_drains_with_builds_send_each_message_once(
    families,  # noqa: F811
    monkeypatch,
):
    """Both mail consumers building and sending at once still send once each.

    Both build the head of the queue; each message is committed by one of
    them, from a build, rebuilt or with none (the one that loses the race
    for a message it built skips it). The counts are what PR 4's rehearsal
    reads per consumer.
    """
    harness, path, provider = ready(families, monkeypatch, "production")
    before = credential_rows()
    timings = recorded_timings(monkeypatch)
    owners = [mail_owner(harness, path) for _ in range(2)]
    start, errors = Barrier(2), []
    with campaign_clock(due()):
        hint = delivery_tasks()[0].pk

        def run(owner):
            """One consumer's bulk pass, started together with the other."""
            try:
                start.wait()
                owner.bulk(hint)
            except Exception as error:  # noqa: BLE001
                errors.append(error)
            finally:
                connections.close_all()

        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            connections.close_all()
            threads = [Thread(target=run, args=(owner,)) for owner in owners]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(120)
        send_all(harness, path)
    assert not errors
    assert set(OutboxMessage.objects.values_list("state", flat=True)) == {"delivered"}
    assert len(provider.keys) == len(set(provider.keys)) == FAMILIES
    lines = commits(timings)
    assert (
        sum(
            values["prebuilt"] + values["rebuilt"] + values["unbuilt"]
            for values in lines
        )
        == sum(values["items"] for values in lines)
        == FAMILIES
    )
    assert credential_rows() == before


@contextmanager
def closed_clock(harness):
    """The campaign clock a day after the campaign's end (a close)."""
    harness.campaign.refresh_from_db()
    with campaign_clock(harness.campaign.active_configuration.ends_at + timedelta(1)):
        yield


@pytest.mark.parametrize("change", ["pause", "close"])
def test_a_pause_or_close_between_build_and_commit_sends_nothing(
    families,  # noqa: F811
    monkeypatch,
    change,
):
    """The commit half honors a pause or close that lands after the builds."""
    harness, path, provider = ready(families, monkeypatch, "production")
    with campaign_clock(due()):
        builds = prebuild(harness)
    assert all(builds.values())
    use_builds(monkeypatch, builds)
    timings = recorded_timings(monkeypatch)
    if change == "pause":
        with campaign_clock(due()):
            control(harness.campaign, "pause")
        clock = campaign_clock(due())
    else:
        clock = closed_clock(harness)
    with clock:
        send_all(harness, path)
    assert provider.calls == []
    assert sum(values["prebuilt"] for values in commits(timings)) == 0
    assert not OutboxMessage.objects.filter(
        state__in=("submitting", "delivered", "delivery_unknown")
    ).exists()


def test_a_response_between_build_and_commit_cancels_that_reminder(
    invited,  # noqa: F811
    monkeypatch,
):
    """A Family that responds after its reminder was built is not sent it."""
    harness, path, provider, due_at = invited
    sent = len(provider.calls)
    prepare_shortly_before(harness, due_at)
    with campaign_clock(due_at):
        builds = prebuild(harness)
    assert len(builds) == FAMILIES and all(builds.values())
    responder = FamilyCampaign.objects.get(family_duid=1)
    with campaign_clock(due_at):
        form, answers = form_and_answers(harness)
        assert submit(harness, form, answers).submission is not None
    use_builds(monkeypatch, builds)
    with campaign_clock(due_at):
        send_all(harness, path)
        send_all(harness, path)
        for message in reminder_messages().filter(family_id=responder.pk):
            single(harness, path, message.task_id)
    assert len(provider.calls) - sent == FAMILIES - 1
    assert (
        not reminder_messages()
        .filter(family_id=responder.pk, state="delivered")
        .exists()
    )
    assert reminder_messages().filter(state="delivered").count() == FAMILIES - 1
    assert len(provider.keys) == len(set(provider.keys))


def test_a_crash_after_the_submitting_commit_leaves_one_batch_unknown(
    families,  # noqa: F811
    monkeypatch,
):
    """A drain killed after its first prebuilt batch's commit never resends.

    With B = 3, the first batch commits three messages from their builds
    as "submitting"; the consumer dies after the first send. After the
    lease, recovery records exactly that batch delivery_unknown, and the
    rest are sent once each.
    """
    harness, path, provider = ready(families, monkeypatch, "production")
    monkeypatch.setattr(family_mail_bulk, "HOLD_SECONDS", 60)
    monkeypatch.setattr(family_mail_bulk, "SEND_LEASE_SECONDS", 5)
    monkeypatch.setattr(family_mail_bulk, "LEASE_MARGIN_SECONDS", 1)
    monkeypatch.setattr(family_mail_bulk, "FIRST_DEADLINE_SECONDS", 4)
    monkeypatch.setattr(family_mail_bulk, "DEADLINE_STEP_SECONDS", 0)
    monkeypatch.setattr(family_mail_bulk, "MIN_LAUNCH_SECONDS", 1)
    timings = recorded_timings(monkeypatch)

    class Killed(BaseException):
        """Stands in for the process being killed mid-send."""

    def killed(*args, **kwargs):
        """Kill the consumer after one send, before any outcome is recorded."""
        provider(*args, **kwargs)
        raise Killed

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", killed)
    real_finish = family_mail_dispatch.finish_submission
    monkeypatch.setattr(family_mail_dispatch, "finish_submission", _never)
    small = mail_owner(harness, path, BulkSettings(True, 3))
    with campaign_clock(due()), pytest.raises(Killed):
        send_all(harness, path, small)
    (first,) = commits(timings)
    assert first["items"] == first["prebuilt"] == 3
    submitting = list(OutboxMessage.objects.filter(state="submitting"))
    assert len(submitting) == 3 and len(provider.calls) == 1
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    monkeypatch.setattr(family_mail_dispatch, "finish_submission", real_finish)
    _wait_for_lease_expiry()
    with campaign_clock(due()):
        for message in submitting:
            single(harness, path, message.task_id)
        send_all(harness, path)
    unknown = {message.pk for message in submitting}
    for message in OutboxMessage.objects.all():
        assert message.state == (
            "delivery_unknown" if message.pk in unknown else "delivered"
        )
    assert len(provider.keys) == len(set(provider.keys)) == 1 + FAMILIES - 3


def test_a_busy_or_stale_key_set_never_uses_a_build(
    families,  # noqa: F811
    monkeypatch,
):
    """A rotation holding the key-set lock drops builds and refuses commits.

    Building while a rotation holds the lock drops every build. Builds made
    before it are not written while it holds the lock: the commit half
    refuses each message as the whole path does, leaving it unsent, and
    once the rotation is done the same builds are written and sent once.
    """
    harness, path, provider = ready(families, monkeypatch, "production")
    with campaign_clock(due()):
        with rotation_in_progress():
            assert not any(prebuild(harness).values())
        builds = prebuild(harness)
    assert all(builds.values())
    use_builds(monkeypatch, builds)
    timings = recorded_timings(monkeypatch)
    real = family_mail_bulk._run_batch
    batches = []

    def run_batch(*args, **kwargs):
        """Run the first send batch while a rotation holds the lock."""
        batches.append(1)
        if len(batches) > 1:
            return real(*args, **kwargs)
        with rotation_in_progress():
            return real(*args, **kwargs)

    monkeypatch.setattr(family_mail_bulk, "_run_batch", run_batch)
    with campaign_clock(due()):
        send_all(harness, path)
        assert commits(timings)[0]["items"] == 0
        monkeypatch.setattr(family_mail_bulk, "_run_batch", real)
        send_all(harness, path)
    assert sum(values["prebuilt"] for values in commits(timings)) == FAMILIES
    assert len(provider.keys) == len(set(provider.keys)) == FAMILIES


def test_a_send_build_skips_what_it_must_not_build(
    families,  # noqa: F811
    monkeypatch,
):
    """Only claimable scheduled Family mail with a current scope is built.

    A finished task, an unknown task and messages whose mode no longer
    matches the runtime are each left to the lock, as before.
    """
    harness, path, provider = ready(families, monkeypatch, "testing")
    tasks = delivery_tasks()
    with campaign_clock(due()):
        single(harness, path, tasks[0].pk)
    sent = OutboxMessage.objects.get(state="delivered")
    tasks = tasks[1:]
    seed("UPDATE stewardship_system_configuration SET mode='production'")
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        try:
            for task in tasks:
                assert (
                    build_submission(
                        task.pk, private=harness.rings.private, public_origin=ORIGIN
                    )
                    is None
                )
            assert (
                build_submission(
                    sent.task_id, private=harness.rings.private, public_origin=ORIGIN
                )
                is None
            )
            assert (
                build_submission(
                    uuid4(), private=harness.rings.private, public_origin=ORIGIN
                )
                is None
            )
        finally:
            connections.close_all()
