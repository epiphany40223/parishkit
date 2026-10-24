"""The Loading parish data page after a failed or discarded load (#1353).

A load that fails for good returns setup to collecting, so its progress page
offers the way back instead of saying to cancel setup. A discarded load's
page stays readable, passively only, and says the load is no longer used.
These run the real web and worker paths under their own logins, as the void
tests do.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import date
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_progress import source_progress
from parishkit.stewardship.accounts.setup_progress_views import summary
from parishkit.stewardship.accounts.setup_source import start_source_load
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.local.fake_parishsoft import (
    FakeConfiguration,
    FakeParishSoft,
)
from parishkit.stewardship.local.synthetic_parish import generate
from parishkit.stewardship.source.setup_execution import setup_source_handler

from .setup_request_budgets import short_request_budgets
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_load_void_postgresql import dispatch, drained, stage
from .test_setup_loading_postgresql import (
    fake_parishsoft_exchange,
    installer_reply,
    queued,
)
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def observed(request, service, task_id, **options):
    """One progress reading of ``task_id`` on the web login, and its status key."""
    with web_login():
        progress = source_progress(request, service, task_id, **options)
    return progress, summary(progress)


def test_a_failed_load_offers_the_way_back_and_a_discarded_one_is_no_longer_used(
    setup_service, monkeypatch
):
    """Failed → "failed" (not "ended"); after a new key the old page is
    "voided", never renewed; the new load is the attempt's own again."""
    short_request_budgets(monkeypatch)
    request, attempt, task, private = queued(setup_service)
    assert dispatch(monkeypatch, private, task.run_id, invalid=True) == "failed"
    progress, key = observed(request, setup_service, task.run_id)
    assert key == "failed"
    assert progress["bound"] and not progress["loaded"] and not progress["active"]

    stage(request, setup_service, candidate=b"another-synthetic-wizard-key")
    assert SetupAttempt.objects.get().source_task_id is None
    progress, key = observed(request, setup_service, task.run_id)
    assert key == "voided"
    assert not progress["bound"] and not progress["active"]
    # A discarded load's page is read passively only: renewal is refused, and
    # so is a Task that is not one of this attempt's loads.
    for identifier, renew in ((task.run_id, True), (uuid4(), False)):
        with pytest.raises(LookupError), web_login():
            source_progress(request, setup_service, identifier, renew=renew)
    # Another sign-in cannot read it either.
    with pytest.raises(LookupError), web_login():
        source_progress(login(setup_service), setup_service, task.run_id)

    row = SetupAttempt.objects.get()
    with web_login():
        again = start_source_load(
            request, setup_service, row.pk, expected_version=row.version
        )
    drained()
    assert dispatch(monkeypatch, private, again.run_id) == "succeeded"
    progress, key = observed(request, setup_service, again.run_id)
    assert key == "done" and progress["bound"] and progress["loaded"]
    # The first load stays no longer used.
    assert observed(request, setup_service, task.run_id)[1] == "voided"


def test_a_changed_organization_turns_a_loaded_page_into_no_longer_used(
    setup_service, monkeypatch
):
    """An open "Parish data is loaded" page reads "voided" once the
    organization changes, so its Continue cannot outlive the discard."""
    request, attempt, task, private = queued(setup_service)
    assert dispatch(monkeypatch, private, task.run_id) == "succeeded"
    progress, key = observed(request, setup_service, task.run_id)
    assert key == "done" and progress["loaded"]
    # The same key and organization keep the load.
    stage(request, setup_service)
    assert observed(request, setup_service, task.run_id)[1] == "done"
    stage(request, setup_service, organization=2)
    assert observed(request, setup_service, task.run_id)[1] == "voided"


def test_a_failed_load_names_the_organization_it_could_not_load(
    setup_service, monkeypatch
):
    """The fake's organization that holds nothing, chosen anyway, fails the
    first load (empty); the progress names it as "Name (ID)" for the page's
    way back to choose another organization (#1554). A load that has not
    failed names nothing."""
    short_request_budgets(monkeypatch)
    configuration = FakeConfiguration.parse(
        {
            "seed": 1,
            "families": 5,
            "anchor_date": "2026-09-16",
            "release_at": None,
            "other_organizations": 2,
        }
    )
    fake = FakeParishSoft(
        configuration, parish=generate(1, 5, date(2026, 9, 16), organization_id=1)
    )
    request, attempt, task, private = queued(setup_service, organization_id=3)
    progress, key = observed(request, setup_service, task.run_id)
    assert key == "queued" and progress["organization_label"] is None
    installer_reply(monkeypatch, private)
    fake_parishsoft_exchange(monkeypatch, fake)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task.run_id,
            queue=WorkQueue.SOURCE,
            worker_id=uuid4(),
            handlers={"setup_source_load": setup_source_handler()},
        )
    assert TaskRun.objects.get(pk=task.run_id).state == "failed"
    progress, key = observed(request, setup_service, task.run_id)
    assert key == "failed"
    # The lookup's answer named it "St. Example Parish" (setup_lookup_builders).
    assert progress["organization_label"] == "St. Example Parish (3)"
