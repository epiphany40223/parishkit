"""The Response dashboard over HTTP, read as the restricted web login (#477, #1537).

The campaign is the response-metrics ``funnel`` fixture (a Testing campaign
dated from the database clock, with the corpus Family signed in through its
rehearsal credential), so the page reads real engagement and submission
evidence. The dashboard is checked for Administrators and Staff, with the
site in Production and in Testing mode (#2324), with the read guard,
``no-store`` and the audit.
"""

import re
from uuid import uuid4

import pytest
from django.db.models import F
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import RehearsalEpoch
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports import workspace_views

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_report_workspace_postgresql import read as get
from .test_response_metrics_postgresql import funnel, open_form, respond  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def tiles(body):
    """The Summary's Families by stage counts, in funnel order (#1537)."""
    stages = re.search(
        rb'<ul class="stat-tiles" aria-label="Families by stage">.*?</ul>', body, re.S
    )
    if stages is None:
        return []
    return [
        int(value)
        for value in re.findall(
            rb'<span class="stat-value">(\d+)</span>', stages.group(0)
        )
    ]


def site_mode(mode):
    """Put the site in ``mode``, as go-live or a return to Testing does."""
    with unguarded():
        SystemConfiguration.objects.update(mode=mode)


def test_dashboard_follows_the_site_mode_for_admin_and_staff(
    funnel,  # noqa: F811
    auth_service,
    google,
    monkeypatch,
    caplog,
):
    """The site's mode decides whose responses are shown (#2324): Testing
    mode with a rehearsal shows it to every reader, Production mode (or no
    rehearsal) the live responses; a ``mode`` in the address is refused, and
    every view is audited."""
    harness, epoch = funnel
    open_form(harness)
    respond(harness)
    route = reverse("admin:response_dashboard")
    admin, login = signed_in()
    assert login.status_code == 302
    site_mode("production")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(admin, route)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        # Production has nothing yet: the Family responded in the rehearsal.
        assert tiles(body) == [0, 0, 0, 0, 0]
        # No Responses shown choice and no mode in any link (#2324).
        # (The Admin chrome's own mail links may carry one.)
        region = body[body.index(b'id="response-dashboard"') :]
        assert b"Responses shown" not in body and b"mode=" not in region
        # No funnel chart: the tiles give its figures (#1537).
        assert b"response-funnel" not in body
        # The fixture's ParishSoft inputs give no statistics, so the
        # Production view still shows Families by stage and says the other
        # rows and panels are unavailable, rather than refusing the page
        # (#1537); the activity chart is gone. Without the statistics'
        # populations there is no Families counted choice either.
        assert b'id="production-unavailable"' in body
        assert b'id="participation-scope"' not in body
        # They come back by themselves: the figures status keeps watching
        # beside the line at its slow retry pace (#1586).
        assert b"appear here by themselves" in body
        assert b'data-live-interval="60000"' in body and b"retry=1" in body
        assert b'aria-label="Population"' not in body
        assert b'id="response-activity-spec"' not in body
        # An old link's mode is refused, whatever its value, as the
        # Timeline refuses it (#1570); no alias (#864).
        for invalid in ("?mode=testing", "?mode=production", "?size=7", "?search=x"):
            assert get(admin, route + invalid)[0].status_code == 400
    site_mode("testing")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The rehearsal's sign-in, form and submission are Testing evidence.
        response, body = get(admin, route)
        assert response.status_code == 200
        assert tiles(body) == [0, 1, 1, 1, 1]
        assert b"never counted in Production" in body
        # The Production-only panels are left out, saying why.
        assert b'id="production-only"' in body
        assert b'aria-label="Population"' not in body
        # An old bookmark's activity grain still loads, and is ignored.
        response, body = get(admin, route + "?grain=day")
        assert response.status_code == 200 and b"Response activity" not in body
    assert (
        AuditEvent.objects.filter(
            event_type="response_dashboard_viewed",
            subject_id=harness.campaign.pk,
        ).count()
        == 3
    )
    # Staff see what the site's mode shows, the rehearsal included.
    store = auth_service.store
    staff = address("staff@example.org", roles=("staff",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **staff}],
        ).state
        == "applied"
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200 and tiles(body) == [0, 1, 1, 1, 1]
        assert get(browser, route + "?mode=testing")[0].status_code == 400
    # With the rehearsal ended, Testing mode shows the live responses.
    RehearsalEpoch.objects.filter(pk=epoch).update(
        state="invalidated", invalidated_at=database_now(), version=F("version") + 1
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(admin, route)
        assert response.status_code == 200
        assert tiles(body) == [0, 0, 0, 0, 0]
        assert b"never counted in Production" not in body
        assert b'id="production-unavailable"' in body
        # A fault while shaping the page is the report's 503, not a 400 about
        # the reader's options, and the view is audited as failed.
        monkeypatch.setattr(workspace_views, "funnel_panels", broken)
        caplog.clear()
        assert get(admin, route)[0].status_code == 503
        assert any(
            record.msg == Event.REPORT_SHAPING_FAILED for record in caplog.records
        )
    assert AuditEvent.objects.filter(
        event_type="response_dashboard_viewed",
        subject_id=harness.campaign.pk,
        auditcontext__context__outcome="failed",
    ).exists()


def broken(*args, **kwargs):
    """A page builder that fails as a shaping bug would."""
    raise ValueError("A shaping fault.")
