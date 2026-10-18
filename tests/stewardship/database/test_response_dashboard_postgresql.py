"""The Response dashboard over HTTP, read as the restricted web login (#477, #1537).

The campaign is the response-metrics ``funnel`` fixture (a Testing campaign
dated from the database clock, with the corpus Family signed in through its
rehearsal credential), so the page reads real engagement and submission
evidence. The dashboard is checked for Administrators and Staff, in
Production and Testing, with the read guard, ``no-store`` and the audit.
"""

import re
from uuid import uuid4

import pytest
from django.db.models import F
from django.urls import reverse

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import RehearsalEpoch
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports import workspace_views

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_report_workspace_postgresql import read as get
from .test_response_metrics_postgresql import funnel, open_form, respond  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def tiles(body):
    """The stage tiles' counts, in funnel order, from the rendered page."""
    stages = re.search(
        rb'<section class="panel" id="response-stages".*?</section>', body, re.S
    )
    if stages is None:
        return []
    return [
        int(value)
        for value in re.findall(
            rb'<span class="stat-value">(\d+)</span>', stages.group(0)
        )
    ]


def test_dashboard_for_admin_and_staff_in_both_modes(
    funnel,  # noqa: F811
    auth_service,
    google,
    monkeypatch,
    caplog,
):
    """Production by default; Testing for Administrators only; every view audited."""
    harness, epoch = funnel
    open_form(harness)
    respond(harness)
    route = reverse("admin:response_dashboard")
    admin, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(admin, route)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        # Production has nothing yet: the Family responded in the rehearsal.
        assert tiles(body) == [0, 0, 0, 0, 0]
        assert b"Production" in body
        # The mode link names the dashboard region as its fragment (#519).
        testing = route + "?scope=historical&amp;page=1&amp;mode=testing"
        assert b'href="' + testing.encode() + b'#response-dashboard"' in body
        assert b'id="response-funnel-spec"' in body
        # The fixture's ParishSoft inputs give no statistics, so the
        # Production view still shows the funnel and says the other panels
        # are unavailable, rather than refusing the page (#1537); the
        # activity chart is gone.
        assert b'id="production-unavailable"' in body
        assert b'id="response-activity-spec"' not in body
        # The rehearsal's sign-in, form and submission are Testing evidence.
        response, body = get(admin, route + "?mode=testing")
        assert response.status_code == 200
        assert tiles(body) == [0, 1, 1, 1, 1]
        assert b"Testing rehearsal" in body and b"never counted in Production" in body
        # The Production-only panels are left out, saying why.
        assert b'id="production-only"' in body
        assert b"Current active population statistics" not in body
        # An old bookmark's activity grain still loads, and is ignored.
        response, body = get(admin, route + "?mode=testing&grain=day")
        assert response.status_code == 200 and b"Response activity" not in body
        for invalid in ("?mode=live", "?size=7", "?search=x"):
            assert get(admin, route + invalid)[0].status_code == 400
    assert (
        AuditEvent.objects.filter(
            event_type="response_dashboard_viewed",
            subject_id=harness.campaign.pk,
        ).count()
        == 3
    )
    # Staff read the Production dashboard but never Testing responses.
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
        assert response.status_code == 200 and b"mode=testing" not in body
        assert get(browser, route + "?mode=testing")[0].status_code == 403
    # With the rehearsal ended there is no Testing evidence to show.
    RehearsalEpoch.objects.filter(pk=epoch).update(
        state="invalidated", invalidated_at=database_now(), version=F("version") + 1
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(admin, route + "?mode=testing")
        assert response.status_code == 200
        assert b"no Testing responses to show" in body and tiles(body) == []
        assert b"never counted in Production" not in body
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
