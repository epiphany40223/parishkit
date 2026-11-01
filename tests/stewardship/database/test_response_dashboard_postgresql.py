"""The Response dashboard over HTTP, read as the restricted web login (#477, #1537).

The campaign is the response-metrics ``funnel`` fixture (a Testing campaign
dated from the database clock, with the corpus Family signed in through its
rehearsal credential), so the page reads real engagement and submission
evidence. The dashboard is checked for Administrators and Staff, with the
site in Production and in Testing mode (#2324), with the read guard,
``no-store`` and the audit.
"""

import re
from datetime import timedelta
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
    mode with a rehearsal shows it to an Administrator, Production mode (or
    no rehearsal) the live responses; Staff always see the live responses,
    since Testing stays for Administrators only (#477). A ``mode`` in the
    address is refused, and every view is audited."""
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
        # populations there is no Families counted choice either; a fixed
        # line says the stage tiles count every Family (#2324).
        assert b'id="production-unavailable"' in body
        assert b'id="participation-scope"' not in body
        assert b"every Family of the campaign, until" in body
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
    # Staff never see the rehearsal (#477), in Testing mode too.
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
        assert response.status_code == 200 and tiles(body) == [0, 0, 0, 0, 0]
        assert b"never counted in Production" not in body
        assert b'id="production-unavailable"' in body
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


def figures(body, label):
    """One stat-tile row's values by tile key, as the page draws them."""
    row = re.search(
        rb'<ul class="stat-tiles" aria-label="' + label + rb'">.*?</ul>', body, re.S
    )
    assert row is not None, label
    return {
        key.decode(): value.decode().strip()
        for key, value in re.findall(
            rb'data-stat="(\w+)"><span class="stat-label">[^<]*</span> '
            rb'<span class="stat-value[^"]*">([^<]*)',
            row.group(0),
        )
    }


def test_every_figure_counts_the_chosen_population_with_a_family_that_left(
    response_service, google, monkeypatch
):
    """One Family responds with a pledge, then leaves the campaign (#2324).

    For each Families counted population, the Population tiles, the Families
    by stage tiles (the funnel's Submitted count), the daily chart's last day
    and the export's captured stages all count the same Families: the
    Family under Including Families that left, nobody under In this campaign
    today. With the daily facts unreadable, a ``scope`` still filters the
    stage tiles from the live statistics, under a fixed line; with the
    statistics unreadable too, the line says every Family is counted.
    """
    from parishkit.stewardship.reports import participation_summary
    from parishkit.stewardship.reports.facts import FactUnavailable
    from parishkit.stewardship.reports.materialization import materialize_fact_set
    from parishkit.stewardship.reports.statistics import StatisticsUnavailable

    from .response_builders import activate_response_service, response_source
    from .test_fact_materialization_postgresql import allocation
    from .test_financial_source_postgresql import financial_source
    from .test_ministry_responses_postgresql import respond as submit
    from .test_response_http_postgresql import answers_for, load_form
    from .test_source_families_postgresql import prepare, promote
    from .test_source_snapshots_postgresql import permit

    harness = response_service
    financial_source(harness)
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {"annual_pledge": "100.01", "frequency": "monthly"}
    answers["financial"]["shares"] = {}
    response = submit(harness, form, answers)
    # The form stamps the submission with the fixture's campaign clock,
    # which runs ahead of the database's; the funnel is read at a cutoff
    # just after it, so it counts the submission (#2324).
    from parishkit.stewardship.reports import response_dashboard

    cutoff = response.submitted_at + timedelta(minutes=1)
    monkeypatch.setattr(response_dashboard, "database_now", lambda: cutoff)
    # The Family's group is made inactive, so it leaves the campaign.
    data = response_source()
    data.family_groups[7] = "Inactive"
    snapshot, lease = prepare(data)
    snapshot = promote(snapshot, lease, harness.campaign, harness.rings)
    charts = {}
    for scope in ("historical", "current"):
        record, claim = allocation(
            harness,
            population=scope,
            watermark=response.campaign_sequence,
            source=snapshot,
        )
        # Interactive, so each is published as its scope's chart.
        charts[scope] = materialize_fact_set(
            record.pk, claim, admit=permit, interactive=True
        )
    site_mode("production")
    route = reverse("admin:response_dashboard")
    admin, login = signed_in()
    assert login.status_code == 302
    expected = {"historical": 1, "current": 0}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for scope, families in expected.items():
            response, body = get(admin, f"{route}?scope={scope}")
            assert response.status_code == 200, scope
            assert str(charts[scope].pk).encode() in body, scope
            population = figures(body, b"Population")
            stages = figures(body, b"Families by stage")
            assert population["active_families"] == str(families), scope
            # The Family's own pledge counts only while it is counted.
            assert ("100.01" in population["annual_pledges"]) is bool(families)
            assert stages["submitted"] == str(families), scope
            last = charts[scope].days.order_by("local_date").last()
            assert last.cumulative_responses == families, scope
            summary = participation_summary.capture(harness.campaign.pk, scope)
            submitted = [
                row for row in summary["stages"] if row["label"] == "Submitted"
            ]
            assert [row["count"] for row in submitted] == [families], scope
            population_rows = {
                row["label"]: row["value"] for row in summary["population"]
            }
            assert population_rows["Families"] == families, scope

        # The daily facts cannot be read: the statistics still name the
        # population, so a scope still counts, and a fixed line says which.
        def unreadable(*args, **kwargs):
            """Daily facts that cannot be read, as a lost generation is."""
            raise FactUnavailable("unreadable")

        monkeypatch.setattr(workspace_views, "guarded_participation", unreadable)
        for scope, families in expected.items():
            response, body = get(admin, f"{route}?scope={scope}")
            assert response.status_code == 200, scope
            assert b'id="production-unavailable"' in body, scope
            assert b'id="participation-scope"' not in body, scope
            label = {"historical": "Including Families that left"}.get(
                scope, "In this campaign today"
            )
            assert f"Families counted: {label}</p>".encode() in body, scope
            stages = figures(body, b"Families by stage")
            assert stages["submitted"] == str(families), scope

        # Nor the statistics: every Family is counted, and the line says so.
        def no_statistics(*args, **kwargs):
            """Statistics whose inputs cannot be read."""
            raise StatisticsUnavailable("unreadable")

        monkeypatch.setattr(workspace_views, "calculate_statistics", no_statistics)
        response, body = get(admin, f"{route}?scope=current")
        assert response.status_code == 200
        assert b"every Family of the campaign, until" in body
        assert figures(body, b"Families by stage")["submitted"] == "1"
