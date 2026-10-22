"""The Participation charts block's "which charts" option (#1956).

New report email's Charts checkboxes and ``report-email preview`` store a
chosen subset and refuse an empty one, or pledges in a campaign without
financial stewardship; a send draws and captures only the chosen charts
(report-emails spec, "Participation charts block" and "Blocks field").
The fixture campaign has no financial stewardship.
"""

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.report_email_models import ReportEmailSnapshot

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import DAILY_INSTANT, add_report, allocated
from .report_builders import WEEKLY_INSTANT as INSTANT
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_admin_report_email_cli_postgresql import _preview, _refusal, _values
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_email_editor_postgresql import (
    confirm,
    fields,
    post,
    refused_at,
    reports,
    started,
)
from .test_report_emails_page_postgresql import apply_requests

pytestmark = pytest.mark.django_db(transaction=True)
FAMILIES = ["first_submissions", "cumulative_families"]


def charts_block(charts=None):
    """The Participation charts block entry with ``charts`` chosen."""
    options = {} if charts is None else {"charts": charts}
    return {"block": "participation_charts", "version": 1, "options": options}


def test_new_offers_and_stores_the_chosen_charts(live_response_service, google):
    """Two boxes without financial stewardship; a subset is stored, all is
    the default, and none (or only an unoffered pledges box) is refused at
    the Charts group (400)."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(path).content.decode()
        for chart in FAMILIES:
            assert f'name="charts" value="{chart}"' in page
        assert 'value="pledges"' not in page
        block = {"block": ["participation_charts"]}
        for ticked in ([], ["pledges"]):
            answer = post(admin, path, fields(**block, charts=ticked))
            refused_at(answer, "charts", "Choose at least one chart.")
        answer = post(admin, path, fields(**block, charts=["cumulative_families"]))
        assert answer.status_code == 200, answer.content
        assert b"Participation charts (Cumulative participating Families)" in (
            answer.content
        )
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        (entry,) = reports()["Staff requests"]["values"]["blocks"]
        assert entry == charts_block(["cumulative_families"])
        answer = post(admin, path, fields(**block, name="All", charts=FAMILIES))
        assert answer.status_code == 200, answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        (entry,) = reports()["All"]["values"]["blocks"]
        assert entry == charts_block()


def test_preview_create_checks_the_charts(
    live_response_service,
    google,
    cli,  # noqa: F811
):
    """``report-email preview`` refuses an empty or unknown choice and pledges
    without financial stewardship, at ``values.blocks``; a subset passes."""
    from .report_builders import users_form

    harness = live_response_service
    with campaign_clock(INSTANT):
        users_form(harness.service.store)
        complete_empty_catchup(harness.campaign, uuid4())
        signed_in()
        run = cli()
        for charts in ([], ["unknown"], ["pledges", "first_submissions"]):
            problems = _refusal(run, _values(blocks=[charts_block(charts)]))
            assert problems["values.blocks"][1].startswith("Blocks: Check the blocks")
        problems = _refusal(run, _values(blocks=[charts_block(["pledges"])]))
        assert problems == {
            "values.blocks": (
                "invalid",
                "Charts: Effective annual pledges needs financial stewardship, "
                "which this campaign does not have.",
            )
        }
        entry = {
            "operation": "create",
            "values": _values(blocks=[charts_block(["first_submissions"])]),
        }
        code, document = _preview(run, entry)
        assert code == 0, document
        # Every chart the campaign offers is stored as no choice, as New
        # stores it (test_new_offers_and_stores_the_chosen_charts).
        entry = {
            "operation": "create",
            "values": _values(name="All", blocks=[charts_block(FAMILIES)]),
        }
        code, document = _preview(run, entry)
        assert code == 0, document
        token = document["result"]["preview"]["token"]
        code, document = run("report-email", "confirm", "--token", token)
        assert code == 0, document
        apply_requests(run.service.store)
        (stored,) = reports()["All"]["values"]["blocks"]
        assert stored == charts_block()


def test_edit_keeps_a_saved_pledges_choice_until_the_boxes_change(
    live_response_service, google
):
    """Without financial stewardship, a rename that leaves the Charts boxes
    as Edit ticked them keeps a saved choice naming pledges; changing the
    boxes saves what is ticked, and the review names the dropped chart."""
    harness = live_response_service
    saved = ["first_submissions", "pledges"]
    with campaign_clock(INSTANT):
        admin = started(harness)
        identifier = add_report(
            harness.service.store,
            harness.campaign,
            name="Charts",
            blocks=[charts_block(saved)],
        )
        path = reverse("admin:report_email_edit", args=[identifier])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(path).content.decode()
        assert 'value="pledges"' not in page
        block = {"block": ["participation_charts"]}
        answer = post(
            admin, path, fields(**block, name="Renamed", charts=["first_submissions"])
        )
        assert answer.status_code == 200, answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        (entry,) = reports()["Renamed"]["values"]["blocks"]
        assert entry == charts_block(saved)
        answer = post(admin, path, fields(**block, name="Renamed", charts=FAMILIES))
        assert answer.status_code == 200, answer.content
        assert b"kept, not drawn: Effective annual pledges" in answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        (entry,) = reports()["Renamed"]["values"]["blocks"]
        assert entry == charts_block()


def test_a_send_draws_and_captures_only_the_chosen_charts(family_mail, google):  # noqa: F811
    """The snapshot records the chosen chart, and the alt text only its figure."""
    from parishkit.stewardship.accounts.reports_conversion import converted_values

    store, campaign = family_mail.service.store, family_mail.campaign
    values = converted_values(
        {
            "campaign_id": str(campaign.pk),
            "kind": "daily_digest",
            "time": "00:15:00",
            "weekday": None,
            "subject": "",
            "comparison": False,
        },
        None,
    )
    values["blocks"] = [
        charts_block(["first_submissions"])
        if entry["block"] == "participation_charts"
        else entry
        for entry in values["blocks"]
    ]
    with campaign_clock(DAILY_INSTANT):
        identifier = add_report(store, campaign, **values)
        preparation = allocated(family_mail, identifier=identifier)
    snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
    (data,) = [
        block["data"]
        for block in snapshot.blocks
        if block["block"] == "participation_charts"
    ]
    assert data["charts"] == ["first_submissions"]
    assert "First submissions that day:" in data["alt"]
    assert "Families that have responded" not in data["alt"]
    assert data["alt"] in snapshot.html
    assert snapshot.images[0][0] == "participation@parishkit"
