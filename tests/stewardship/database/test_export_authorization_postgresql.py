"""Current policy and SQL ownership must survive queued and completed exports."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction

from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import ExportPublication
from parishkit.stewardship.reports.export_services import (
    admit_campaign,
    authorize,
    cancel_export,
    consume_download,
    export_status,
    issue_download,
    retry_export,
)

from ..policy_factory import address, assignment
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_export_jobs_postgresql import (  # noqa: F401
    request_export,
    run_export,
    scenario,
)
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


def add_policy(setup, *rules):
    """Install actual policy through its normal immutable configuration workflow."""
    store, admin, _, _ = setup
    result = change(
        store,
        store.active(),
        admin.pk,
        [{"operation": "add", "section": "login_rules", **rule} for rule in rules],
    )
    assert result.state == "applied"


def test_staff_owns_only_own_jobs_and_admin_can_access_them(scenario):  # noqa: F811
    """Report capability does not confer authority over another requester's work."""
    add_policy(
        scenario,
        address("staff@example.org", ("staff",)),
        address("other@example.org", ("staff",)),
    )
    store, admin, facts, root = scenario
    staff, other = user("staff@example.org"), user("other@example.org")
    request = request_export((store, staff, facts, root))
    assert export_status(store, staff.pk, request.pk)["state"] == "queued"
    for operation in (export_status, cancel_export, issue_download):
        with pytest.raises(PermissionError):
            operation(store, other.pk, request.pk)
    run_export(scenario, request)
    grant = issue_download(store, admin.pk, request.pk)
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
        # Even bypassing the Python owner check cannot consume another user's grant.
        c.execute(
            "INSERT INTO stewardship_export_download_use "
            "(id, created_at, correlation_id, actor_id, grant_id) "
            "VALUES (%s,clock_timestamp(),%s,%s,%s)",
            (uuid4(), uuid4(), other.pk, grant.pk),
        )
    assert consume_download(store, admin.pk, grant.pk).request_id == request.pk


def test_ministry_leader_cannot_use_participation_export(scenario):  # noqa: F811
    """An independently valid ministry scope is not a parish-wide report grant."""
    add_policy(
        scenario,
        address("leader@example.org", ("ministry_leader",)),
        assignment(),
    )
    store, _, facts, root = scenario
    leader = user("leader@example.org")
    with pytest.raises(PermissionError):
        request_export((store, leader, facts, root))
    request = request_export(scenario)
    with pytest.raises(PermissionError):
        export_status(store, leader.pk, request.pk)


def test_admin_retries_staff_export_without_changing_owner(scenario):  # noqa: F811
    """Retry command ownership differs from the immutable report requester."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports.export_tasks import TASK_TYPE, export_handler

    from .test_taskrun_postgresql import act

    add_policy(scenario, address("staff@example.org", ("staff",)))
    store, admin, facts, root = scenario
    staff = user("staff@example.org")
    request = request_export((store, staff, facts, root))
    with work_transaction():
        claimed = act(_status(request.task), "claim")
        act(claimed, "permanent_failure")
    key = uuid4()
    retried = retry_export(store, admin.pk, request.pk, request_key=key)
    assert retry_export(store, admin.pk, request.pk, request_key=key) == retried
    assert TaskRun.objects.get(pk=retried.run_id).initiated_by_id == admin.pk
    assert execute_hint(
        retried.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: export_handler(store=store, root=root)},
    )
    assert (
        ExportPublication.objects.get(request=request).attempt.run_id == retried.run_id
    )
    request.refresh_from_db()
    assert request.requester_id == staff.pk
    assert (
        retry_export(store, admin.pk, request.pk, request_key=key).run_id
        == retried.run_id
    )


def test_sql_and_python_policy_agree_on_verified_email_case(scenario):  # noqa: F811
    """Google identity case does not override normalized exact-address matching."""
    store, _, facts, root = scenario
    mixed = user("Admin@Example.ORG")
    request = request_export((store, mixed, facts, root))
    assert run_export(scenario, request)
    assert ExportPublication.objects.get(request=request).size > 0


def test_revocation_after_render_prevents_publication(scenario, monkeypatch):  # noqa: F811
    """Fresh publication checks reject authorization lost while a file was written."""
    from parishkit.stewardship.reports import export_tasks

    _, principal, _, _ = scenario
    request = request_export(scenario)
    original = export_tasks.write_artifact

    def revoke_after_render(*args):
        """Commit on a separate connection while the renderer owns its read guard."""
        receipt = original(*args)

        def revoke():
            """Use the guarded PortalUser mutation, then close the test connection."""
            try:
                principal.disabled = True
                principal.version += 1
                principal.save()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(revoke).result(timeout=10)
        return receipt

    monkeypatch.setattr(export_tasks, "write_artifact", revoke_after_render)
    with pytest.raises(type(principal).DoesNotExist):
        run_export(scenario, request)
    assert not ExportPublication.objects.exists()


def test_revocation_after_grant_prevents_consumption(scenario):  # noqa: F811
    """A short-lived one-use grant cannot preserve an already revoked role."""
    store, principal, _, _ = scenario
    request = request_export(scenario)
    run_export(scenario, request)
    grant = issue_download(store, principal.pk, request.pk)
    principal.disabled = True
    principal.version += 1
    principal.save()
    with pytest.raises(type(principal).DoesNotExist):
        consume_download(store, principal.pk, grant.pk)


def test_download_role_can_authorize_inside_readonly_guard(scenario):  # noqa: F811
    """Real dedicated-download privileges cover the fresh policy and receipt query."""
    store, principal, _, _ = scenario
    request = request_export(scenario)
    run_export(scenario, request)

    def fresh(guard):
        """Use the same protected reads as the HTTP download's callback."""
        authorize(store, principal.pk, request=request)
        admit_campaign(request.campaign_id, mutating=False)
        assert ExportPublication.objects.get(request=request).size > 0

    with (
        task_login("download", reconnect=True),
        CampaignReadGuard([request.campaign_id], authorize=fresh, abort=lambda: None),
    ):
        pass


@pytest.mark.parametrize("role", [ServiceRole.WEB, ServiceRole.SCHEDULER, "download"])
def test_non_render_roles_cannot_forge_publications(role):
    """SQL privileges reject publication before even considering malformed values."""
    with (
        task_login(role),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("INSERT INTO stewardship_export_publication DEFAULT VALUES")
    assert error.value.__cause__.sqlstate == "42501"


def test_pinned_timezone_catalog_is_supported_by_python_and_postgres():
    """Every admitted browser zone must render and pass the database constraint."""
    from zoneinfo import ZoneInfo

    from parishkit.stewardship.schema_primitives import timezone_names

    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM pg_timezone_names")
        postgres = {row[0] for row in cursor.fetchall()}
    names = set(timezone_names())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT zone, stewardship_timezone_name_v1(zone) "
            "FROM unnest(%s::text[]) AS zone",
            (sorted(names),),
        )
        normalized = dict(cursor.fetchall())
    assert set(normalized.values()) <= postgres
    for name in sorted(names):
        assert ZoneInfo(name).key == name


def test_historical_browser_timezone_alias_is_preserved(scenario):  # noqa: F811
    """Supported aliases remain display metadata even when SQL uses canonical names."""
    request = request_export(scenario, browser_timezone="US/Eastern")
    assert run_export(scenario, request)
    request.refresh_from_db()
    assert request.browser_timezone == "US/Eastern"
