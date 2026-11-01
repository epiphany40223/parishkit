"""The review wizard's Member pages over HTTP (#2123 slice 3).

Each Member page draws the Family hub Member tab's part for its stream
through the shared section components: Census changes' Member and New
Member pages, Ministry requests' page and Limitations' Member page. Only
the run's matched items the stream's scoped read lists for the reader are
drawn or counted, so a Ministry leader never sees another Ministry's
request for the same Member.
"""

import re
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.review_run_models import ReviewRun

from ..census_factory import member
from ..policy_factory import address
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_worklist_page_postgresql import ROUTE, staff_browser
from .test_hub_actions_postgresql import dialog_fields
from .test_ministry_responses_postgresql import respond, revisit
from .test_response_http_postgresql import answers_for
from .test_review_wizard_postgresql import location, start
from .test_review_wizard_sections_postgresql import (
    heading,
    member_line,
    page,
    section,
    statements,
)
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def position(run, kind, key=None):
    """The run's page for the subject of ``kind`` (and ``key``), 1-based."""
    subjects = ReviewRun.objects.get(pk=run).subjects
    for place, subject in enumerate(subjects, 1):
        if subject["kind"] == kind and key in (None, str(subject["key"])):
            return place
    raise AssertionError((kind, key, subjects))


def test_census_member_and_new_member_pages(response_service, google):
    """A run at To review: Member 3's page draws the prefix change alone
    (the accepted first name is filtered out) with Census changes' actions;
    the Member the Family added has its own page with its addition and the
    Family's answers for them."""
    harness, admin, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "accept")
    added = str(uuid4())
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["proposed_members"][added] = member(first_name="Zed")
        answers["ministries"]["proposed_members"][added] = {"join": []}
        respond(harness, form, answers)
    from parishkit.stewardship.responses.models import ProposedChange

    prefix = ProposedChange.objects.get(field="prefix", superseded_by=None)
    first = ProposedChange.objects.get(field="first_name", superseded_by=None)
    addition = ProposedChange.objects.get(field="new_member", superseded_by=None)
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, mode="advanced", stage="to_review")[0])[1]["run"]
        at = position(run, "member", "3")
        markup = page(browser, run, at)
        drawn = section(markup)
        assert f'<tr id="review-member-census-row-{prefix.pk}"' in drawn
        assert str(first.pk) not in drawn
        assert "1 more in this stream for this Member is filtered out." in drawn
        # The header: the Family's line, then the Member's, whose name opens
        # the Member's tab of the Family hub.
        assert heading(markup).startswith("Family: ")
        assert "(DUID 3)" in member_line(markup)
        assert re.search(r'^Member: <a href="[^"]*\?tab=[^"]+">', member_line(markup))
        action, _fields = dialog_fields(drawn, "review-member-census-decide")
        assert action == ROUTE
        # The New Member's page: its addition and its answers.
        at_new = position(run, "proposed_member", added)
        markup = page(browser, run, at_new)
        drawn = section(markup)
        assert "Adding this Member:" in drawn
        assert f'id="review-member-census-row-{addition.pk}"' in drawn
        assert "The Family's answers for this Member" in drawn and "Zed" in drawn
        # A New Member: the hub's badge, and no DUID yet.
        assert "New Member" in member_line(markup)
        assert "DUID" not in member_line(markup)
    # Pinned, as on the Family pages: the census read, Census changes' read
    # narrowed to the Family, one replacement lookup, the heading's names
    # and the view's audit.
    assert statements(browser, run, at)[1] == STATEMENTS["census"]


# Each Member page's statements on the web login (see the census test). The
# census page reads its reviewers' Names once (#2259).
STATEMENTS = {"census": 77, "ministry": 84, "limitations": 75}


def test_a_ministry_page_for_staff_and_for_a_leader(response_service, google):
    """Member 3 asked to join Food pantry (9) and leave Ministry 4. Staff
    with a Join run see the join, the leave counted as filtered out; the
    leader of Food pantry sees the join alone, with nothing counted, and
    Member's names in plain text."""
    from parishkit.stewardship.workflows.models import MinistryRequest

    from .test_ministry_exports_postgresql import leader
    from .test_ministry_reports_postgresql import setup

    harness = setup(response_service)
    join = MinistryRequest.objects.get(action="join", superseded_by=None)
    leave = MinistryRequest.objects.get(action="leave", superseded_by=None)
    store = harness.service.store
    rule = address("staff@example.org", roles=("staff",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    browser = staff_browser(google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(
            start(browser, stream="ministry", mode="advanced", kind="join")[0]
        )[1]["run"]
        markup = page(browser, run)
        drawn = section(markup)
        assert f'<tr id="review-ministry-row-{join.pk}"' in drawn
        assert str(leave.pk) not in drawn
        assert "1 more in this stream for this Member is filtered out." in drawn
        assert "Food pantry" in drawn
        action, _fields = dialog_fields(drawn, "review-ministry-decide")
        assert action == reverse("admin:ministry_report")
        # Staff: the header's names open the Family hub and the Member's tab.
        assert re.search(
            r'data-review-heading>Family: <a href="/admin/reports/families/', markup
        )
        assert "?tab=" in member_line(markup)
    assert statements(browser, run)[1] == STATEMENTS["ministry"]
    browser, *_ = leader(harness, google)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, stream="ministry")[0])[1]["run"]
        assert ReviewRun.objects.get(pk=run).size == 1
        markup = page(browser, run)
    drawn = section(markup)
    assert f'<tr id="review-ministry-row-{join.pk}"' in drawn
    assert str(leave.pk) not in markup and "filtered out" not in drawn
    # A leader cannot open the Timeline: every name is plain text.
    assert "/admin/reports/families/" not in markup
    assert "<a " not in member_line(markup)


def test_a_limitations_member_page(response_service, google):
    """A Limitations run's Member page draws Member 3's Cannot participate
    answer with Limitations' actions; the Family's own answer is on the
    Family page only."""
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.responses.limitation_review import answer_id

    from .response_builders import activate_response_service
    from .test_ministry_exports_postgresql import leader
    from .test_ministry_responses_postgresql import start as started
    from .test_review_work_postgresql import submit

    harness = response_service
    started(harness)
    harness = activate_response_service(harness)
    submission = submit(harness)
    duid = FamilyCampaign.objects.get(pk=submission.family_id).family_duid
    browser, *_ = leader(harness, google, roles=("staff",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        run = location(start(browser, stream="limitations")[0])[1]["run"]
        at = position(run, "member", "3")
        assert at == 2
        markup = page(browser, run, at)
    drawn = section(markup)
    own = answer_id(duid, "member", "3")
    assert f'<tr id="hub-other-limitations-row-{own}"' in drawn
    assert "Cannot participate in Ministries:" in drawn
    assert "Cannot attend Mass" not in drawn and "filtered out" not in drawn
    action, _fields = dialog_fields(drawn, "hub-other-limitations-decide")
    assert action == reverse("admin:limitations_report")
    assert member_line(markup).startswith("Member: ")
    assert statements(browser, run, at)[1] == STATEMENTS["limitations"]
