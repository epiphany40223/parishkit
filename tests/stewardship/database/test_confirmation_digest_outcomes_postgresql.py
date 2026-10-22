"""Compare actual durable report fanout, without predicting live report contents.

Report emails replaced the Daily and Weekly digests (#1786): the counts are
of the messages a report shaped as the converted Daily or Weekly makes.
"""

from copy import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.confirmation_digest_outcomes import digest_outcomes
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint

from .campaign_builders import campaign_clock
from .report_builders import DAILY_INSTANT as DAILY
from .report_builders import WEEKLY_INSTANT as WEEKLY
from .report_builders import add_digest, execute, messages, produce, set_user
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_setup_mail_views_postgresql import web_login
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["daily", "weekly"])
def test_actual_report_counts_follow_positive_fanout_and_exclude_other_scopes(
    request, kind
):
    """Use real activation, catch-up and per-recipient owners, with no provider IO."""
    harness = request.getfixturevalue(
        "family_mail" if kind == "daily" else "response_service"
    )
    store = harness.service.store
    add_digest(store, harness.campaign, weekly=kind == "weekly")
    set_user(store, "second@example.org", "administrator", name="Second, Admin")
    with campaign_clock(DAILY if kind == "daily" else WEEKLY):
        harness = activate_response_service(harness)
        demand = ActivationCatchUpDemand.objects.get(campaign=harness.campaign)
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))
        demand.refresh_from_db()
        assert demand.completed_at
        if kind == "weekly":
            respond(harness, "Actionable live request")
        statuses = produce()
        assert statuses
        with web_login():
            before = digest_outcomes(demand)
            assert before[f"{kind}_messages"] == {"actual": 0, "complete": False}
        for status in statuses:
            execute(status)
        assert messages().count() == 2
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
