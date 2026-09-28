"""Every visible wizard field explains itself, linked for assistive technology."""

import pytest
from django import forms

from parishkit.stewardship.accounts import field_tips, setup_help
from parishkit.stewardship.accounts.setup_branding_views import SetupLogoForm
from parishkit.stewardship.accounts.setup_campaign_views import SetupCampaignForm
from parishkit.stewardship.accounts.setup_content_views import SetupContentForm
from parishkit.stewardship.accounts.setup_credential_views import SetupCredentialForm
from parishkit.stewardship.accounts.setup_forms import FORMS


def visible(form):
    """Fields a person fills in; hidden identity/version fields need no help."""
    return {
        name: field
        for name, field in form.fields.items()
        if not isinstance(field.widget, forms.HiddenInput)
    }


def full_help(field):
    """The field's complete help: its tip when it has one, else its visible help."""
    return str(getattr(field, "tip", field.help_text))


@pytest.mark.parametrize(
    "form",
    [
        *(form_type() for step, form_type in FORMS.items() if step != "branding"),
        SetupLogoForm(),
        SetupCampaignForm(),
        SetupCredentialForm("parishsoft"),
        SetupCredentialForm("google_workspace"),
        SetupCredentialForm("slack"),
        SetupContentForm(kind="email", slot="initial"),
    ],
    ids=lambda form: type(form).__name__,
)
def test_every_visible_setup_field_has_help_linked_by_aria(form):
    """Help renders under the control or in its tip, and the control points at it.

    Long help moves into a tip beside the label (field_tips.py); the control is
    then described by the tip's bubble too, so no explanation is lost to
    assistive technology.
    """
    fields = visible(form)
    assert fields
    for name, field in fields.items():
        assert full_help(field).strip(), name
        bound = form[name]
        described = [f"{bound.auto_id}_helptext"] if field.help_text else []
        if hasattr(field, "tip"):
            described.append(f"{bound.auto_id}_tip")
            assert len(str(field.help_text)) <= field_tips.LONG_HELP, name
        html = str(bound)
        assert f'aria-describedby="{" ".join(described)}"' in html, name
        # Whatever the control references is rendered in the field group.
        group = bound.as_field_group()
        assert all(f'id="{element}"' in group for element in described), name


def test_visible_hints_fit_on_one_line():
    """A hint that stays visible beside a tip must itself be short."""
    for name, hint in setup_help.HINTS.items():
        assert len(str(hint)) <= field_tips.LONG_HELP, name


def test_mail_and_testing_help_states_the_operational_facts():
    """The facts an administrator needs are present, not just field names."""
    mail = FORMS["mail"]()
    assert "https://mail.google.com/" in full_help(mail.fields["delegated_email"])
    assert "2,000" in full_help(mail.fields["delegated_email"])
    assert "Send mail as" in full_help(mail.fields["sender"])
    testing = full_help(FORMS["testing"]().fields["testing_recipient"])
    assert "real information" in testing
    workspace = full_help(SetupCredentialForm("google_workspace").fields["candidate"])
    assert "service_account" in workspace and "oauth2.googleapis.com" in workspace


@pytest.mark.parametrize("target", ["parishsoft", "slack"])
def test_single_line_secrets_use_a_password_input_that_never_echoes(target):
    """API keys and bot tokens are one line; the value is never re-rendered."""
    form = SetupCredentialForm(target, {"candidate": "synthetic-secret"})
    html = str(form["candidate"])
    assert html.startswith("<input") and 'type="password"' in html
    assert 'autocomplete="off"' in html and 'spellcheck="false"' in html
    assert "maxlength=" in html and "required" in html
    assert "synthetic-secret" not in html
    kept = SetupCredentialForm(target, saved=True)
    assert "required" not in str(kept["candidate"])
    assert "encrypted" in full_help(kept.fields["candidate"])


def test_service_account_json_stays_a_write_only_text_area():
    """The multi-line Google key keeps its text area and never echoes."""
    form = SetupCredentialForm("google_workspace", {"candidate": '{"private": 1}'})
    html = str(form["candidate"])
    assert html.startswith("<textarea") and "private" not in html


def test_multi_select_help_says_how_to_choose_several():
    """Setup's own text keeps the shared multi-select instructions."""
    form = SetupCampaignForm()
    for name in ("ministry_duids", "fund_duids", "comparison_fund_duids"):
        # The how-to line stays visible; the full help is in the tip.
        text = str(form.fields[name].help_text)
        assert "Ctrl" in text and "Shift" in text, name
    assert "ParishSoft funds" in full_help(form.fields["fund_duids"])
