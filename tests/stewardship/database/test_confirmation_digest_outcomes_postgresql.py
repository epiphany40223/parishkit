"""Compare actual durable digest fanout, without predicting live report contents."""

from copy import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.confirmation_digest_outcomes import digest_outcomes
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.reports.digest_fanout import fanout_daily

from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_daily_digest_fanout_postgresql import ready_report
from .test_daily_digest_planning_postgresql import INSTANT as DAILY
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_weekly_capture_postgresql import INSTANT as WEEKLY
from .test_weekly_fanout_postgresql import captured, detached, retain
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["daily", "weekly"])
def test_actual_digest_counts_follow_positive_fanout_and_exclude_other_scopes(
    request, kind
):
    """Use real activation, catch-up and per-recipient owners, with no provider IO."""
    harness = request.getfixturevalue(
        "family_mail" if kind == "daily" else "response_service"
    )
    with campaign_clock(DAILY if kind == "daily" else WEEKLY):
        harness = activate_response_service(harness)
        demand = ActivationCatchUpDemand.objects.get(campaign=harness.campaign)
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))
        demand.refresh_from_db()
        assert demand.completed_at
        if kind == "daily":
            claim, _ = ready_report(harness, additional_admins=("second@example.org",))
        else:
            respond(harness, "Actionable live request")
            claim, _ = captured(harness, additional_admins=("second@example.org",))
        with web_login():
            before = digest_outcomes(demand)
            assert before[f"{kind}_messages"] == {"actual": 0, "complete": False}
        if kind == "daily":
            with task_login(ServiceRole.WORKER, exact=True), work_transaction():
                assert fanout_daily(claim).phase == "complete"
        else:
            page, contents = detached(claim)
            assert retain(claim, page, contents).phase == "complete"
        with web_login():
            result = digest_outcomes(demand)
            assert result[f"{kind}_messages"] == {"actual": 2, "complete": True}
            other = "weekly" if kind == "daily" else "daily"
            assert result[f"{other}_messages"]["actual"] == 0
            wrong = copy(demand)
            wrong.campaign_id = uuid4()
            assert digest_outcomes(wrong)[f"{kind}_messages"]["actual"] == 0
            future = copy(demand)
            future.created_at = demand.created_at + timedelta(days=1)
            assert digest_outcomes(future)[f"{kind}_messages"]["actual"] == 0
            earlier = copy(demand)
            earlier.cutoff = demand.cutoff - timedelta(days=40)
            assert digest_outcomes(earlier)[f"{kind}_messages"]["actual"] == 0
