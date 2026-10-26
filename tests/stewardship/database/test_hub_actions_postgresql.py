"""The Family hub's row actions over HTTP (#2064).

A hub census tab draws each change's Census changes icons and dialogs; a
decision posts to Census changes with the fields the hub's dialog form
holds, and the hub tab drawn again shows the decision in force. A reader
the stream page would offer nothing (a held campaign) sees no icon.
"""

import html
import re
from dataclasses import asdict

import pytest
from django.urls import reverse

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.hub_census import read_hub_census
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    ProposalReview,
)

from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import prepared, state
from .test_census_worklist_page_postgresql import ROUTE, staff_browser
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_hub_streams_postgresql import answer
from .test_information_followup_postgresql import search
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import load_form
from .test_response_submission_postgresql import form_and_answers
from .test_roster_entries_postgresql import resolved
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def tab(browser, family_id, key):
    """GET one hub tab; its panel's markup, unescaped attributes kept."""
    response, body = get(
        browser, f"{reverse('admin:timeline', args=[family_id])}?tab={key}"
    )
    assert response.status_code == 200
    return body.decode().split(f'id="family-tabs-panel-{key}"')[1]


def dialog_fields(panel, form_id):
    """The hidden fields of the hub's dialog form ``form_id``, as posted."""
    form = panel.split(f'<form id="{form_id}"')[1].split("</form>")[0]
    action = re.search(r'action="([^"]+)"', form)[1]
    fields = dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form)
    )
    fields.pop("csrfmiddlewaretoken", None)
    return action, fields


def icon(panel, row_id, action):
    """The row's icon button for ``action``: its own post fields (the JS
    sends them with the dialog's form) and its item."""
    # A table row, or an item drawn on its own line (the pledge).
    row = re.split(r"</tr>|</p>", panel.split(f'id="{row_id}"')[1])[0]
    button = re.search(rf'<button[^>]*data-row-action="{action}"[^>]*>', row)[0]
    extra = html.unescape(re.search(r'data-confirm-with="([^"]+)"', button)[1])
    fields = dict(part.split("=", 1) for part in extra.split("&"))
    fields["item"] = re.search(r'value="([^"]+)"', button)[1]
    return fields


def test_staff_accept_a_change_from_the_family_tab(response_service, google):
    """The Family tab's home address offers Census changes' Accept, whose
    dialog posts there; the tab then shows it accepted, Accept selected."""
    harness, _, _, rows = prepared(response_service)
    home = rows["home_address"]
    family_id = home.submission.family_id
    browser = staff_browser(google)
    panel = tab(browser, family_id, "family")
    row_id = f"hub-family-census-row-{home.pk}"
    assert f'<tr id="{row_id}"' in panel
    assert 'id="hub-family-content" data-in-place-region' in panel
    assert "data-confirm-answer-page" not in panel
    action, fields = dialog_fields(panel, "hub-family-census-decide")
    assert action == ROUTE
    # The dialog also sends its note, left empty here.
    chosen = fields | icon(panel, row_id, "accept") | {"note": ""}
    response, body = search(browser, action, chosen)
    assert response.status_code == 200 and b"accepted." in body
    assert state(home)[:2] == ("approved", "pending")
    assert ProposalReview.objects.get().proposal_id == home.pk
    panel = tab(browser, family_id, "family")
    # A table row, or an item drawn on its own line (the pledge).
    row = re.split(r"</tr>|</p>", panel.split(f'id="{row_id}"')[1])[0]
    assert "is-selected" in row and 'data-row-action="accept"' in row
    assert 'aria-pressed="true"' in row
    # Its Back to review is now offered, as on Census changes.
    assert "decide=back_to_review" in row


def test_a_member_tab_offers_its_own_changes_only(response_service, google):
    """A Member tab draws its Member's changes' icons and dialogs, not the
    Family's: each tab holds only its own rows' dialogs."""
    harness, _, _, rows = prepared(response_service)
    family_id = rows["first_name"].submission.family_id
    browser = staff_browser(google)
    panel = tab(browser, family_id, "member-1")
    assert f"hub-member-1-census-row-{rows['first_name'].pk}" in panel
    assert f"hub-member-1-census-row-{rows['prefix'].pk}" in panel
    assert str(rows["home_address"].pk) not in panel
    assert f"hub-member-1-census-edit-dialog-{rows['first_name'].pk}" in panel


def test_no_icon_while_other_campaign_work_holds_the_campaign(
    response_service, google, monkeypatch
):
    """A held campaign offers no action on Census changes; nor on the hub."""
    from parishkit.stewardship.reports import hub_actions

    harness, _, _, rows = prepared(response_service)
    family_id = rows["home_address"].submission.family_id
    browser = staff_browser(google)
    monkeypatch.setattr(hub_actions, "mutable", lambda campaign_id: False)
    panel = tab(browser, family_id, "family")
    assert "data-row-action" not in panel
    assert "hub-family-census-decide-dialog" not in panel
    assert "reviewed on the Census changes page, not here." in panel


def test_the_additional_tab_acts_through_limitations_and_its_queue(
    live_response_service, google
):
    """A Yes to cannot attend offers Limitations' actions and the current
    Additional information item its queue's; an Ignore posted to
    Limitations from the hub's dialog shows Ignored when the tab is drawn
    again."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    response = answer(
        harness,
        form,
        answers,
        cannot_attend=True,
        additional_information="Please call about the choir",
    )
    family_id = response.family_id
    duid = FamilyCampaign.objects.get(pk=family_id).family_duid
    item = AdditionalInformationItem.objects.get()
    browser, login = signed_in()
    assert login.status_code == 302
    panel = tab(browser, family_id, "other")
    limitation = f"hub-other-limitations-row-f{duid}"
    assert f'<tr id="{limitation}"' in panel
    assert f'<tr id="hub-other-information-row-{item.pk}"' in panel
    action, fields = dialog_fields(panel, "hub-other-limitations-decide")
    assert action == reverse("admin:limitations_report")
    assert dialog_fields(panel, "hub-other-information-decide")[0] == reverse(
        "admin:information_queue"
    )
    chosen = fields | icon(panel, limitation, "ignore") | {"note": ""}
    response, body = search(browser, action, chosen)
    assert response.status_code == 200 and b"ignored." in body
    panel = tab(browser, family_id, "other")
    row = panel.split(f'id="{limitation}"')[1].split("</tr>")[0]
    assert '<span class="status-word">Ignored</span>' in row
    assert "decide=back_to_review" in row


def test_a_ministry_request_is_accepted_from_its_member_tab(response_service, google):
    """A Member tab's To review leave offers Ministry requests' Accept,
    posted there with the hub form's fields; the tab then shows it To enter
    by hand."""
    harness, _admin, join, leave = resolved(response_service)
    family_id = leave.submission.family_id
    census = read_hub_census(harness.campaign.pk, family_id, census=False)
    (member,) = [item for item in census.members if item.entity == ("member", "3")]
    browser, login = signed_in()
    assert login.status_code == 302
    panel = tab(browser, family_id, member.key)
    row_id = f"hub-{member.key}-ministry-row-{leave.pk}"
    assert f'<tr id="{row_id}"' in panel
    action, fields = dialog_fields(panel, f"hub-{member.key}-ministry-decide")
    assert action == reverse("admin:ministry_report")
    chosen = fields | icon(panel, row_id, "accept") | {"note": ""}
    # On the web login, as the page's own decision tests post (the
    # Ministry decision's SQL runs under its grants).
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, action, chosen)
    assert response.status_code == 200 and b"accepted." in body
    panel = tab(browser, family_id, member.key)
    row = panel.split(f'id="{row_id}"')[1].split("</tr>")[0]
    assert '<span class="status-word">To enter by hand</span>' in row
    assert 'data-row-action="accept"' in row and "is-selected" in row


def test_a_pledge_is_edited_from_the_financial_tab(response_service, google):
    """The pledge's Edit posts Pledges' editor fields (an amount, a
    frequency and the share methods ticked, #2177) to Pledges; the tab then
    shows the Reviewer's value in its table's column, as an edited census
    field does, Share methods included."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        response = pledge(
            harness, load_form(harness), shares={CHECK: "", OTHER: "Stock gift"}
        )
    family_id = response.family_id
    browser, login = signed_in()
    assert login.status_code == 302
    panel = tab(browser, family_id, "financial")
    row_id = f"hub-financial-pledges-row-{response.pk}"
    assert f'<tr id="{row_id}"' in panel
    form_id = f"hub-financial-pledges-edit-{response.pk}"
    action, fields = dialog_fields(panel, form_id)
    assert action == reverse("admin:pledges_to_enter")
    editor = panel.split(f'<form id="{form_id}"')[1].split("</form>")[0]
    assert 'value="quarterly"' in editor and 'value="annual"' in editor
    chosen = fields | icon(panel, row_id, "edit")
    chosen |= {"note": "", "value_amount": "1500.00", "value_frequency": "quarterly"}
    # The editor starts from the Family's share methods; the reviewer keeps
    # only the bank check, as a browser posts the one box left ticked.
    assert editor.count(" checked ") == 2 and "Stock gift</textarea>" in editor
    chosen |= {f"value_share_{CHECK}": "on"}
    response, body = search(browser, action, chosen)
    assert response.status_code == 200
    panel = tab(browser, family_id, "financial")
    assert '<th scope="col">Reviewer\'s value</th>' in panel
    annual, frequency, shares = panel.split(f'<tr id="{row_id}"')[1].split("</tr>")[:3]
    assert "<td>$1,500.00</td>" in annual
    assert "<td>Quarterly ($375.00 each)</td>" in frequency
    assert "<td>Bank check</td>" in shares
