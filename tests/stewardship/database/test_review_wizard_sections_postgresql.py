"""The review wizard's Family pages over HTTP (#2123 slice 2).

Each page draws the Family hub's section for its stream through the shared
section components: only the run's matched items, each at its live stage,
with the filtered-out line and the stream page's own row actions, whose
dialogs post to the stream page and redraw the page's region from its own
address. A subject the reader's scoped read no longer lists is not named.
"""

import re
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.review_run_models import ReviewRun

from ..census_factory import address
from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide, prepared, state
from .test_census_worklist_page_postgresql import ROUTE, staff_browser
from .test_hub_actions_postgresql import dialog_fields, icon
from .test_information_followup_postgresql import search
from .test_ministry_responses_postgresql import respond, revisit
from .test_report_workspace_postgresql import read
from .test_response_http_postgresql import answers_for
from .test_review_wizard_postgresql import START, location, start
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def page(browser, run, position=1):
    """GET one run page; its markup."""
    response, body = read(browser, f"{START}{position}/?run={run}")
    assert response.status_code == 200
    return body.decode()


def statements(browser, run, position=1):
    """GET one run page on the web login: its markup and its statement count."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as queries,
    ):
        markup = page(browser, run, position)
    return markup, len(queries)


def section(markup):
    """The page's section region, up to the link sentence after it."""
    return markup.split('id="review-section"')[1].split("data-review-links")[0]


def heading(markup):
    """The page's heading text."""
    return re.search(r"data-review-heading>(.*?)</h1>", markup, re.S)[1]


def test_a_census_family_page_shows_its_matched_changes(response_service, google):
    """A run at To enter by hand draws the Family page's accepted email
    opt-out only, with one filtered-out line for the home address still To
    review. Its pressed Accept, from the page's dialog, posts to Census
    changes and undoes the decision (the toggle buttons, #2131); the page
    drawn again shows it To review, and the same post at its old version is
    refused as on Census changes."""
    harness, _, staff, rows = prepared(response_service)
    home, opt_out = rows["home_address"], rows["email_opt_out"]
    decide(harness, staff, [opt_out], "accept")
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, _ = start(
            browser, mode="advanced", stage="to_enter_by_hand", kind="contact"
        )
        assert response.status_code == 303
        run = location(response)[1]["run"]
        assert ReviewRun.objects.get(pk=run).subjects[0]["items"] == [str(opt_out.pk)]
        markup = page(browser, run)
        drawn = section(markup)
        row_id = f"review-family-census-row-{opt_out.pk}"
        assert f'<tr id="{row_id}">' in drawn
        assert str(home.pk) not in drawn
        assert 'id="review-family-changed-caption"' in drawn
        # The unchanged fields stay behind Show unchanged, as on the hub.
        assert "Show unchanged" in drawn
        assert (
            '<p class="help" data-review-filtered-out>1 more in this stream for '
            "this Family is filtered out.</p>"
        ) in drawn
        # The region redraws from the page's own address.
        assert f'data-refresh-url="{START}1/?run={run}"' in markup
        assert "DUID" in heading(markup)
        action, fields = dialog_fields(drawn, "review-family-census-decide")
        assert action == ROUTE
        row = drawn.split(f'id="{row_id}"')[1].split("</tr>")[0]
        assert 'aria-pressed="true"' in row
        chosen = fields | icon(drawn, row_id, "accept") | {"note": ""}
        assert chosen["decide"] == "back_to_review"
        response, _ = search(browser, action, chosen)
        assert response.status_code == 200
        assert state(opt_out)[:2] == ("unreviewed", "pending")
        # Live on load: the page shows the stage now in force, not the run's.
        drawn = section(page(browser, run))
        row = drawn.split(f'id="{row_id}"')[1].split("</tr>")[0]
        assert 'aria-pressed="true"' not in row
        assert '<span class="status-word">To review</span>' in row
        # A decision over a version someone else changed is refused as on
        # Census changes, and records nothing (a new request: the same key
        # would replay the first answer).
        chosen["request_key"] = str(uuid4())
        response, body = search(browser, action, chosen)
        assert response.status_code == 200
        assert b"not recorded, changed by someone else." in body
        assert state(opt_out)[:2] == ("unreviewed", "pending")
    # The page's statements: the sign-in and page checks every Admin page
    # makes, then the section's reads (the hub's census read, Census
    # changes' read narrowed to the Family, one replacement lookup), the
    # heading's names and the view's audit. Pinned, so a change is seen.
    assert statements(browser, run)[1] == 76


def test_a_replaced_change_is_shown_by_its_replacement(response_service, google):
    """The Family answers again with another home address after the run
    started: the page shows the new change, which superseded the stored one."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, mode="advanced", stage="all")[0])
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["family"]["home_address"] = address(line1="12 Other Way")
        respond(harness, form, answers)
    from parishkit.stewardship.responses.models import ProposedChange

    old = rows["home_address"]
    new = ProposedChange.objects.get(field="home_address", superseded_by=None)
    assert new.pk != old.pk
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        drawn = section(page(browser, run[1]["run"]))
    assert f"review-family-census-row-{new.pk}" in drawn
    assert str(old.pk) not in drawn and "12 Other Way" in drawn


def test_information_and_limitations_family_pages(response_service, google):
    """Additional information's page draws the Family's item with its queue's
    actions; the Limitations Family page draws Cannot attend Mass with
    Limitations' actions and no Member's answer."""
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    from .response_builders import activate_response_service
    from .test_ministry_exports_postgresql import leader
    from .test_ministry_responses_postgresql import start as started
    from .test_review_work_postgresql import submit

    harness = response_service
    started(harness)
    harness = activate_response_service(harness)
    submission = submit(harness)
    duid = FamilyCampaign.objects.get(pk=submission.family_id).family_duid
    item = AdditionalInformationItem.objects.get(submission_id=submission.pk)
    browser, *_ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, stream="information")[0])[1]["run"]
        drawn = section(page(browser, run))
        assert f'<tr id="hub-other-information-row-{item.pk}">' in drawn
        assert "Please call about the parish picnic." in drawn
        assert "Cannot attend" not in drawn and "filtered out" not in drawn
        action, _fields = dialog_fields(drawn, "hub-other-information-decide")
        assert action == reverse("admin:information_queue")
        run = location(start(browser, stream="limitations")[0])[1]["run"]
        drawn = section(page(browser, run))
        assert f'<tr id="hub-other-limitations-row-f{duid}">' in drawn
        assert "Cannot attend Mass or prayer services" in drawn
        assert "Cannot participate" not in drawn and "picnic" not in drawn
        action, _fields = dialog_fields(drawn, "hub-other-limitations-decide")
        assert action == reverse("admin:limitations_report")


def test_a_pledges_page_shows_the_current_pledge(response_service, google):
    """The Pledges page draws the Family's pledge with Pledges' actions; after
    the Family answers again it draws the new pledge in its place."""
    from .test_pledge_review_postgresql import again, pledged
    from .test_pledge_review_postgresql import staff_browser as pledge_staff

    harness, submission = pledged(response_service)
    browser, _ = pledge_staff(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, stream="pledges")[0])[1]["run"]
        drawn = section(page(browser, run))
    assert f'<tr id="hub-financial-pledges-row-{submission.pk}">' in drawn
    assert 'id="review-pledge-caption"' in drawn and "$1,234.50" in drawn
    # Pinned as on the census page: here Pledges' read and the hub's
    # Financial read.
    assert statements(browser, run)[1] == 80
    action, _fields = dialog_fields(
        drawn, f"hub-financial-pledges-edit-{submission.pk}"
    )
    assert action == reverse("admin:pledges_to_enter")
    replaced = again(harness, annual_pledge="2,000")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        drawn = section(page(browser, run))
    assert f'<tr id="hub-financial-pledges-row-{replaced.pk}">' in drawn
    assert "$2,000.00" in drawn and str(submission.pk) not in drawn


def test_a_leader_who_loses_the_ministry_sees_no_name(response_service, google):
    """A leader's run page names its Member while they lead the request's
    Ministry; once ParishSoft no longer lists them as its leader (they still
    lead another), the page names no one and says the Member is no longer
    listed, with Previous and Next kept."""
    from .test_ministry_exports_postgresql import leader
    from .test_ministry_reports_postgresql import LEADERS, prepare, setup
    from .test_ministry_responses_postgresql import ministry_source
    from .test_source_families_postgresql import promote

    harness = setup(response_service)  # A join for Ministry 9, a leave for 4.
    browser, *_ = leader(harness, google)  # The leader of Ministry 9.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, stream="ministry")[0])[1]["run"]
        markup = page(browser, run)
    name = heading(markup).strip()
    assert "data-review-unlisted" not in markup and name
    assert "Member of" in markup
    # ParishSoft now lists them as Ministry 4's leader only.
    snapshot, claim = prepare(ministry_source(), LEADERS | {"leader@example.org": [4]})
    promote(snapshot, claim, harness.campaign, harness.rings)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        markup = page(browser, run)
    assert "This Member is no longer listed for this stream." in markup
    assert heading(markup) == "No longer listed"
    assert name not in markup and "Member of" not in markup
    assert markup.count('data-review-move="finish"') == 2


def test_a_limitations_family_page_draws_cannot_contribute(response_service, google):
    """A Family that cannot contribute financially: a Limitations run's
    Family page draws the answer by the pledge section, with Pledges' own
    actions, for Staff, who may see pledges."""
    from dataclasses import asdict

    from ..test_financial_answers import OPTIONS
    from .response_builders import activate_response_service
    from .test_financial_report_postgresql import pledge
    from .test_financial_source_postgresql import financial_source
    from .test_pledge_refresh_postgresql import refresh
    from .test_pledge_review_postgresql import staff_browser as pledge_staff
    from .test_response_http_postgresql import load_form

    harness = response_service
    financial_source(
        harness, modules=["financial", "ministry"], options=map(asdict, OPTIONS)
    )
    harness = activate_response_service(harness)
    # ParishSoft holds a pledge, so the answer differs from it (To review).
    refresh(harness, "300.00")
    with web_login():
        submission = pledge(
            harness,
            load_form(harness),
            annual_pledge="",
            frequency="",
            shares={},
            cannot_give=True,
        )
    browser, _ = pledge_staff(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, _ = start(browser, stream="limitations")
        assert response.status_code == 303
        run = location(response)[1]["run"]
        assert ReviewRun.objects.get(pk=run).subjects[0]["items"] == [
            f"pledge-{submission.pk}"
        ]
        drawn = section(page(browser, run))
    assert f'<tr id="hub-financial-pledges-row-{submission.pk}">' in drawn
    assert "Cannot contribute financially" in drawn
    assert 'id="review-pledge-caption"' in drawn
    assert "Cannot attend Mass" not in drawn and "filtered out" not in drawn
    action, _fields = dialog_fields(drawn, "hub-financial-pledges-decide")
    assert action == reverse("admin:pledges_to_enter")
