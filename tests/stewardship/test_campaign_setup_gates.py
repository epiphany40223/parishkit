"""Campaign and Slack setup forms say what their action waits for (#563).

The page script's complete gate (ui-v1.js, data-require-complete) keeps an
action unavailable until the form's shown required fields are filled in.
These checks pin the markup it reads: which forms opt in, which fields are
conditional, and where each hint sits (after the action, so it never moves
a control above it). The browser behavior itself is in
browser/test_campaign_setup_gates.py.
"""

import re

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.campaign_forms import (
    FINANCIAL_REQUIRED,
    CampaignForm,
)
from parishkit.stewardship.accounts.setup_campaign_views import SetupCampaignForm
from parishkit.stewardship.accounts.setup_confirmation_views import (
    SetupConfirmationForm,
)
from parishkit.stewardship.accounts.setup_forms import FORMS
from parishkit.stewardship.accounts.setup_wizard import BY_KEY as SETUP_STEPS

from .test_setup_final_steps import page

# The wizard's Save button, naming the complete gate's hint.
SAVE_NAMES_HINT = r'aria-describedby="setup-complete-hint"[^>]*>Save and continue'


def form_tag(html):
    """The page's one POST form's opening tag."""
    (tag,) = re.findall(r'<form method="post" class="panel"[^>]*>', html)
    return tag


def after_actions(html):
    """The markup between the wizard's action row and the end of the form."""
    return html.split('<div class="setup-actions">', 1)[1].split("</form>", 1)[0]


def test_slack_channel_shows_and_is_required_only_while_slack_is_enabled():
    """The channel field follows its box; the step's Save waits for it."""
    html = page(
        "setup-step.html",
        "slack",
        form=FORMS["slack"](),
        step="slack",
        step_label=SETUP_STEPS["slack"].label,
    )
    assert "data-require-complete" in form_tag(html)
    (channel,) = re.findall(r'<input[^>]*name="channel_id"[^>]*>', html)
    assert 'data-show-when="enabled=on"' in channel
    assert 'data-required-when-shown=""' in channel
    assert "Enter the Slack channel ID, or untick" in channel
    # Not required in the markup: hidden, it is disabled and not sent.
    assert " required" not in channel
    # The hint follows the action row, named by the Save button.
    tail = after_actions(html)
    assert re.search(SAVE_NAMES_HINT, tail)
    assert (
        '<p class="help" id="setup-complete-hint" data-complete-hint hidden>'
        in (tail.split("</div>", 1)[1])
    )


def test_other_setup_steps_and_finish_keep_their_plain_action_row():
    """Only the forms this slice gates opt in; the rest render as before."""
    html = page(
        "setup-step.html",
        "testing",
        form=FORMS["testing"](),
        step="testing",
        step_label=SETUP_STEPS["testing"].label,
    )
    assert "data-require-complete" not in form_tag(html)
    assert "data-complete-hint" not in html
    assert "aria-describedby" not in after_actions(html)
    finish = page(
        "setup-confirmation.html",
        "confirmation",
        form=SetupConfirmationForm(),
        candidate_digest="a" * 64,
    )
    assert "data-complete-hint" not in finish


def test_first_campaign_waits_for_a_module_and_the_financial_fields():
    """First campaign opts into the gate with its hint after Save."""
    html = page(
        "setup-campaign.html",
        "campaign",
        form=SetupCampaignForm(funds=[("9", "Offertory")]),
    )
    assert "data-require-complete" in form_tag(html)
    assert "data-campaign-form" in form_tag(html)
    tail = after_actions(html)
    assert re.search(SAVE_NAMES_HINT, tail)
    assert 'id="setup-complete-hint" data-complete-hint hidden' in tail


def test_campaign_fields_group_the_modules_and_mark_the_financial_fields():
    """One require-one group holds exactly the three module boxes; every
    field clean() requires for Financial stewardship is conditional on it."""
    html = render_to_string(
        "stewardship/campaign-fields.html", {"form": CampaignForm(prefix=None)}
    )
    (hint,) = re.findall(r'<div data-require-one data-missing-hint="([^"]+)">', html)
    assert hint.startswith("Choose at least one: Census")
    # The group runs from its mark to the Additional information field.
    group = html.split("data-require-one", 1)[1].split("additional_information", 1)[0]
    boxes = re.findall(r'<input type="checkbox" name="(\w+)"', group)
    assert boxes == ["census", "ministry", "financial_enabled"]
    for name in FINANCIAL_REQUIRED:
        (field,) = re.findall(rf'<(?:input|select)[^>]*name="{name}"[^>]*>', html)
        assert 'data-required-when="financial_enabled=on"' in field
    assert 'data-missing-hint="Fill in both financial periods and their funds."' in html
    # The overlap group carries its own hint, closer than the fieldset's.
    (overlap,) = re.findall(r"<div data-overlap-confirmation[^>]*>", html)
    assert 'data-missing-hint="Confirm the overlap' in overlap
    # Additional information stays an ordinary field after the modules.
    assert html.index('name="additional_information"') > html.index(
        'name="financial_enabled"'
    )
