"""The scheduler records and discards go-live link work only for its requester.

Frozen migration 0025 (#462) lets the scheduler's go-live producer record an
inactive Family link preparation and its discard, but only for the
Administrator who started the go-live (the request's ``initiated_by_id``),
still a current Administrator, while the transition request is
``cleanup_complete``. The web login keeps today's check, any other login is
refused as before, and a discard of selected live links is still refused.
The migration's DO block refuses to commit over the previous definition,
which these tests rebuild from the installed one.
"""

import re
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.campaigns.activation_cleanup import request_cancellation
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import (
    CLEANUP_TASK_TYPE,
    TASK_TYPE,
    prepared_generation,
    request_preparation,
)
from parishkit.stewardship.campaigns.cleanup_requests import begin_cleanup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import enqueue

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_cleanup_inventory_postgresql import settle_test_receipts
from .test_cleanup_tasks_postgresql import run
from .test_upgrade_parity_postgresql import ROOT

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    ROOT
    / "src/parishkit/stewardship/schema/migrations/0025_scheduler_link_preparation.sql"
)
REQUESTER_ONLY = "requires the go-live requester"
# The scheduler branch 0025 adds, from its first line to the web branch that
# follows it; the previous definition is the installed one without it.
SCHEDULER_BRANCH = re.compile(
    r"        IF session_user='pk_stewardship_scheduler' THEN\n.*?"
    r"        ELSIF (session_user<>'pk_stewardship_web')",
    re.S,
)


def cleaned_up(harness, actor):
    """A transition request ``actor`` started, with its cleanup complete."""
    now = timezone.now()
    with work_transaction():
        settle_test_receipts(harness)
    status = begin_cleanup(
        campaign_id=harness.campaign.pk,
        request_key=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        readiness_digest="b" * 64,
        acknowledged_at=now,
        reauthenticated_at=now - timedelta(seconds=1),
        admit=lambda *args: True,
    )
    assert run(status)
    return status.request_id


def prepare(transition, actor):
    """Request link preparation for ``transition`` as ``actor``."""
    return request_preparation(
        transition_id=transition,
        request_key=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )


def discard(preparation, actor):
    """Request the discard of ``preparation`` through its owner, as ``actor``."""
    return request_cancellation(
        preparation_id=preparation.pk,
        request_key=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )


def raw_discard(preparation, actor, *, task_actor=None):
    """Record a discard directly, past the owner's own Python refusals.

    The owner refuses a selected generation before writing anything, so only
    a direct write reaches the SQL check that the scheduler path relies on.
    ``task_actor`` attributes the task root when ``actor`` (the discard row's
    own actor) is deliberately missing.
    """
    with work_transaction():
        identifier, correlation = uuid4(), uuid4()
        task = enqueue(
            task_type=CLEANUP_TASK_TYPE,
            domain_request_id=identifier,
            actor_id=task_actor or actor,
            correlation_id=correlation,
            idempotency_key=identifier,
            admit=lambda action, status: action == "enqueue",
        )
        return ProductionTokenCancellation.objects.create(
            id=identifier,
            preparation=preparation,
            task_id=task.root_id,
            request_key=uuid4(),
            actor_id=actor,
            correlation_id=correlation,
        )


def scheduler():
    """Run the block as the scheduler login."""
    return task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True)


def sqlstate(error):
    """The PostgreSQL error code behind a Django database error."""
    return getattr(error.__cause__, "sqlstate", None)


def login_rule(store, email):
    """The id of the address login rule for ``email`` in the applied document."""
    return next(
        item["id"]
        for item in store.active().document()["sections"]["login_rules"]
        if item["values"].get("email") == email
    )


@pytest.fixture
def requester(response_service, auth_service, google):
    """The requester, a second current Administrator and the cleaned-up request.

    Both sign in through the ordinary HTTP login, so both are real, current
    Administrators; only the first starts the go-live.
    """
    _, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get().pk
    store = auth_service.store
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("other@example.org"),
                }
            ],
        ).state
        == "applied"
    )
    google[0].update(email="other@example.org", sub="synthetic-other")
    _, login = signed_in()
    assert login.status_code == 302
    other = PortalUser.objects.exclude(pk=admin).get().pk
    return admin, other, cleaned_up(response_service, admin), store


def test_scheduler_prepares_and_discards_only_for_the_requester(requester):
    """Another current Administrator is refused; the requester passes."""
    admin, other, transition, _ = requester
    with scheduler():
        with pytest.raises(DatabaseError, match=REQUESTER_ONLY) as refused:
            prepare(transition, other)
        assert sqlstate(refused.value) == "42501"
        preparation = prepare(transition, admin)
        with pytest.raises(DatabaseError, match=REQUESTER_ONLY):
            discard(preparation, other)
        cancellation = discard(preparation, admin)
    assert ProductionTokenPreparation.objects.filter(pk=preparation.pk).exists()
    assert cancellation.preparation_id == preparation.pk


def test_a_disabled_requester_is_refused(requester):
    """A disabled requester cannot be acted for, even by the scheduler."""
    admin, _, transition, _ = requester
    with unguarded():
        PortalUser.objects.filter(pk=admin).update(
            disabled=True, version=F("version") + 1
        )
    with scheduler(), pytest.raises(DatabaseError, match=REQUESTER_ONLY):
        prepare(transition, admin)


def test_a_demoted_requester_is_refused(requester):
    """A requester whose Administrator role was removed cannot be acted for.

    Changing the login rules applies a new configuration, which makes any
    new preparation stale in Python first, so the demotion is checked on the
    discard of a preparation made before it, written directly so that only
    the SQL intake check decides.
    """
    admin, _, transition, store = requester
    with scheduler():
        preparation = prepare(transition, admin)
    # The second Administrator remains, so the policy stays valid.
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "remove",
                    "section": "login_rules",
                    "id": login_rule(store, "admin@example.org"),
                }
            ],
        ).state
        == "applied"
    )
    with scheduler(), pytest.raises(DatabaseError, match=REQUESTER_ONLY):
        raw_discard(preparation, admin)
    assert not ProductionTokenCancellation.objects.exists()


def test_a_discard_is_refused_once_the_request_leaves_cleanup_complete(requester):
    """The request's state is the scheduler's only state check on a discard."""
    admin, _, transition, _ = requester
    with scheduler():
        preparation = prepare(transition, admin)
    request = ProductionTransitionRequest.objects.get(pk=transition)
    with unguarded():
        ProductionTransitionRequest.objects.filter(pk=transition).update(
            state="cancelled", version=request.version + 1
        )
    with scheduler(), pytest.raises(DatabaseError, match=REQUESTER_ONLY):
        raw_discard(preparation, admin)
    assert not ProductionTokenCancellation.objects.exists()


def test_selected_live_links_cannot_be_discarded_by_the_scheduler(
    requester, response_service
):
    """A generation the campaign selected is refused by SQL, not only Python."""
    admin, _, transition, _ = requester
    with scheduler():
        preparation = prepare(transition, admin)
    handlers = {TASK_TYPE: token_handler(public=response_service.rings.public)}
    assert execute_hint(
        preparation.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers=handlers,
    )
    generation = prepared_generation(preparation)
    assert generation.state == "ready"
    with unguarded():
        Campaign.objects.filter(pk=response_service.campaign.pk).update(
            active_token_generation_id=generation.pk, version=F("version") + 1
        )
    with (
        scheduler(),
        pytest.raises(DatabaseError, match="Selected live links cannot be cancelled"),
    ):
        raw_discard(preparation, admin)


def test_other_logins_are_still_refused(requester):
    """The worker, which runs the preparation, still cannot record one.

    It has no INSERT grant on the table, so PostgreSQL refuses it before the
    intake trigger runs; either refusal is insufficient privilege.
    """
    admin, _, transition, _ = requester
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError) as refused,
    ):
        prepare(transition, admin)
    assert sqlstate(refused.value) == "42501"
    assert not ProductionTokenPreparation.objects.exists()


def test_a_missing_actor_is_refused_as_insufficient_privilege(requester):
    """With no actor the scheduler is refused by the requester check (42501).

    The later "attributed actor" check (23514) is never reached from the
    scheduler; either way nothing is recorded.
    """
    admin, _, transition, _ = requester
    with scheduler():
        preparation = prepare(transition, admin)
        with pytest.raises(DatabaseError, match=REQUESTER_ONLY) as refused:
            raw_discard(preparation, None, task_actor=admin)
    assert sqlstate(refused.value) == "42501"
    assert not ProductionTokenCancellation.objects.exists()


@contextmanager
def previous_definition():
    """Install the intake function without the scheduler branch, then restore it.

    The previous definition is rebuilt from the installed one by removing the
    branch 0025 adds, so the test keeps meaning "before 0025" after later
    releases ship it.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'stewardship_production_tokens_intake_v1()'::regprocedure)"
        )
        current = cursor.fetchone()[0]
    old, count = SCHEDULER_BRANCH.subn(r"        IF \1", current)
    assert count == 1, "the scheduler branch is installed exactly once"
    assert "pk_stewardship_scheduler" not in old and "SECURITY DEFINER" in old
    with connection.cursor() as cursor:
        cursor.execute(old)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(current)


def test_the_check_block_refuses_the_previous_definition():
    """Over the definition without the scheduler branch the DO block raises."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with (
        previous_definition(),
        pytest.raises(DatabaseError, match="go-live scheduler branch"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(check)
    # The installed definition passes.
    with connection.cursor() as cursor:
        cursor.execute(check)
