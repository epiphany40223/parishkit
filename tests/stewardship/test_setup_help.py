"""Every visible wizard field explains itself, linked for assistive technology."""

import pytest
from django import forms

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
    """Help renders under the control and the control points at it."""
    fields = visible(form)
    assert fields
    for name, field in fields.items():
        assert str(field.help_text).strip(), name
        html = str(form[name])
        assert f'aria-describedby="{form[name].auto_id}_helptext"' in html, name


def test_mail_and_testing_help_states_the_operational_facts():
    """The facts an administrator needs are present, not just field names."""
    mail = FORMS["mail"]()
    assert "https://mail.google.com/" in str(mail.fields["delegated_email"].help_text)
    assert "2,000" in str(mail.fields["delegated_email"].help_text)
    assert "Send mail as" in str(mail.fields["sender"].help_text)
    testing = str(FORMS["testing"]().fields["testing_recipient"].help_text)
    assert "real information" in testing
    workspace = str(
        SetupCredentialForm("google_workspace").fields["candidate"].help_text
    )
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
    assert "encrypted" in str(kept.fields["candidate"].help_text)


def test_service_account_json_stays_a_write_only_text_area():
    """The multi-line Google key keeps its text area and never echoes."""
    form = SetupCredentialForm("google_workspace", {"candidate": '{"private": 1}'})
    html = str(form["candidate"])
    assert html.startswith("<textarea") and "private" not in html
