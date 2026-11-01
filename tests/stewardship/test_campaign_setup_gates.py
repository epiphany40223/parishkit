"""Campaign and setup forms say what their action waits for (#563).

The page script's complete gate (ui-v1.js, data-require-complete) keeps an
action unavailable until the form's shown required fields are filled in.
These checks pin the markup it reads: which forms opt in, which fields are
conditional, and where each hint sits (after the action, so it never moves
a control above it). The browser behavior itself is in
browser/test_campaign_setup_gates.py.
"""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.campaign_forms import (
    FINANCIAL_REQUIRED,
    CampaignForm,
)
from parishkit.stewardship.accounts.setup_branding_views import SetupLogoForm
from parishkit.stewardship.accounts.setup_confirmation_views import (
    SetupConfirmationForm,
)
from parishkit.stewardship.accounts.setup_forms import FORMS
from parishkit.stewardship.accounts.setup_wizard import BY_KEY as SETUP_STEPS
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES

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


# Each form step's own hint, used for a missing field without a hint of its
# own (#1332 item 27).
STEP_HINTS = {
    "parish": "Fill in the parish name, website, time zone and telephone correctly",
    "access": "Fill in every required field correctly to save.",
    "mail": "Enter a valid mailbox, From address and Reply-to address to save.",
    "testing": "Enter a valid Testing recipient address to save.",
}


@pytest.mark.parametrize("step", sorted(STEP_HINTS))
def test_every_setup_form_step_waits_for_its_required_fields(step):
    """Every form step's Save waits for its fields, as Slack's does, with its
    own hint after the action row (#1332 item 27)."""
    html = page(
        "setup-step.html",
        step,
        form=FORMS[step](),
        step=step,
        step_label=SETUP_STEPS[step].label,
    )
    tag = form_tag(html)
    assert "data-require-complete" in tag
    assert f'data-missing-hint="{STEP_HINTS[step]}' in tag
    tail = after_actions(html)
    assert re.search(SAVE_NAMES_HINT, tail)
    assert (
        '<p class="help" id="setup-complete-hint" data-complete-hint hidden>'
        in (tail.split("</div>", 1)[1])
    )


def test_setup_logo_checks_its_size_and_waits_for_a_file():
    """Setup › Parish logos' field carries the page's size check, in the
    server's words, with its reserved error line, and Save waits for a file
    only until a logo is saved (#1332 item 29)."""
    html = page(
        "setup-branding.html",
        "branding",
        form=SetupLogoForm(),
        assets=[],
    )
    (tag,) = re.findall(r'<form method="post" enctype[^>]*>', html)
    assert "data-require-complete" in tag
    assert 'data-missing-hint="Choose the parish logo image to save."' in tag
    (field,) = re.findall(r'<input type="file"[^>]*>', html)
    assert f'data-max-bytes="{MAX_IMAGE_BYTES}"' in field
    assert 'data-max-bytes-message="Parish logo: the file is {size}' in field
    assert " required" in field
    assert '<ul class="errorlist" id="id_logo_error" hidden>' in html
    tail = after_actions(html)
    assert re.search(SAVE_NAMES_HINT, tail)
    assert '<p class="help" id="setup-complete-hint" data-complete-hint hidden>' in tail
    saved = SetupLogoForm(saved=True)
    (field,) = re.findall(r'<input type="file"[^>]*>', str(saved["logo"]))
    assert " required" not in field


def test_setup_logo_refuses_a_large_file_in_the_pages_words():
    """A hand-built upload over 5 MB is still refused by the server, in the
    words the page shows for it (#1332 item 29)."""
    upload = SimpleUploadedFile(
        "logo.png", b"\0" * (MAX_IMAGE_BYTES + 1), content_type="image/png"
    )
    form = SetupLogoForm({"version": "1"}, {"logo": upload})
    assert not form.is_valid()
    (message,) = form.errors["logo"]
    assert message.startswith("Parish logo: the file is ")
    assert message.endswith("the limit is 5 MB.")


def test_finish_setup_keeps_its_plain_action_row():
    """Finish setup has its own gates, not the complete gate."""
    finish = page(
        "setup-confirmation.html",
        "confirmation",
        form=SetupConfirmationForm(),
        candidate_digest="a" * 64,
    )
    assert "data-complete-hint" not in finish


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
    assert (
        'data-missing-hint="Fill in the commitment and comparison periods and '
        'their funds."' in html
    )
    # The commitment period's start waits for a day after the campaign
    # end (#2293); the overlap confirmation it replaced is gone.
    (start,) = re.findall(r'<input[^>]*name="financial_start"[^>]*>', html)
    assert 'data-not-before="id_end_date"' in start
    assert "data-overlap-confirmation" not in html
    # Additional information stays an ordinary field after the modules.
    assert html.index('name="additional_information"') > html.index(
        'name="financial_enabled"'
    )
