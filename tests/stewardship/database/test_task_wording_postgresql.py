"""Task pages explain restarts from the real event history, not a guess."""

import pytest
from django.db import connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.task_wording import (
    RETRY_REASONS,
    refresh_result,
    retry_reason,
)
from parishkit.stewardship.source.leases import release_source

from .auth_builders import signed_in
from .source_builders import source_corpus
from .test_background_grants_postgresql import task_login
from .test_source_snapshots_postgresql import (
    prepared,
    publish,
    source_singletons,  # noqa: F401
)
from .test_taskrun_postgresql import act, expire, new

pytestmark = pytest.mark.django_db(transaction=True)


def row(status):
    """The page's task metadata fields that decide whether a reason shows."""
    run = TaskRun.objects.get(pk=status.run_id)
    return {"id": str(run.pk), "attempt": run.attempt, "state": run.state}


def reclaim(status):
    """Wait out the one-second retry delay, then start the next attempt."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    return act(status, "claim")


def test_a_run_that_never_restarted_has_no_reason():
    """A first attempt shows no retry notice."""
    status = act(new(), "claim")
    assert retry_reason(row(status)) is None


def test_a_lost_worker_reads_as_an_unexpected_stop():
    """An expired lease (for example a server restart) names that cause."""
    status = act(expire(act(new(), "claim", lease_seconds=1)), "recovery_retry")
    assert row(status)["state"] == "retry_wait"
    assert retry_reason(row(status)) == RETRY_REASONS["recovery_retry"]
    assert "server restarted" in str(retry_reason(row(status)))


def test_a_temporary_failure_reads_as_trying_again():
    """A retryable failure names a temporary problem, not a restart."""
    status = act(act(new(), "claim"), "retryable_failure")
    assert row(status) | {"id": None} == {
        "id": None,
        "attempt": 1,
        "state": "retry_wait",
    }
    assert retry_reason(row(status)) == RETRY_REASONS["retryable_failure"]


def test_a_finished_run_no_longer_says_it_is_trying_again():
    """Once a restarted run has finished, the present-tense notice is gone."""
    status = reclaim(act(act(new(), "claim"), "retryable_failure"))
    assert retry_reason(row(status)) == RETRY_REASONS["retryable_failure"]
    status = act(status, "complete")
    assert row(status)["attempt"] == 2 and row(status)["state"] == "succeeded"
    assert retry_reason(row(status)) is None


def test_the_task_page_explains_a_restart_under_the_web_role(auth_service, google):
    """The real page, read as the restricted web role, shows the reason."""
    status = reclaim(act(act(new(), "claim"), "retryable_failure"))
    assert row(status)["attempt"] == 2 and row(status)["state"] == "running"
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = browser.get(f"/admin/background/task/{status.run_id}")
    assert page.status_code == 200
    body = page.content.decode()
    assert "temporary problem" in body and "This is attempt 2." in body


@pytest.mark.usefixtures("source_singletons")
def test_a_refresh_result_says_how_many_records_changed_under_the_web_role():
    """The web role reads checked and changed counts from promoted snapshots."""
    first, claim = prepared()
    publish(first, claim)
    release_source(claim)
    same, claim = prepared()
    publish(same, claim)
    release_source(claim)
    changed, claim = prepared(source_corpus(name="Changed"))
    publish(changed, claim)
    release_source(claim)
    pending, claim = prepared(source_corpus(name="Pending"))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert refresh_result(first.task_id) == (
            "Checked 9 records from ParishSoft; 9 changed (1 Family, 1 Member, "
            "1 contact, 1 address, 1 Ministry, 1 Ministry roster entry, 1 fund, "
            "1 pledge, 1 contribution)."
        )
        assert refresh_result(same.task_id) == (
            "Checked 9 records from ParishSoft; 0 changed."
        )
        assert refresh_result(changed.task_id) == (
            "Checked 9 records from ParishSoft; 1 changed (1 Family)."
        )
        # A validated snapshot that was never promoted reports nothing.
        assert refresh_result(pending.task_id) is None
