"""Integration, backup key and Share options forms gate what they send (#563).

The integration settings pages wait for their required fields (the complete
gate), and while Slack is not set up its token is one of them. Test access
on the off-site backups page is shown unavailable, with why, until Google
Workspace mail is set up. Both steps of a backup key change check their
entry's shape in the browser, with a pattern the server's own rule also
admits, and show a refusal at the field. A Share options row ticked Delete
greys its fields in place. The browser behavior is in
browser/test_conditional_integrations.py.
"""

import re
from types import SimpleNamespace as Value

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import integration_views
from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.backup_sealing import (
    display_code,
    generate_keypair,
)

from .test_share_forms import data_for, form_for

HINT = 'id="integration-complete-hint" data-complete-hint hidden'


def settings_page(target, *, credential=None, **context):
    """Render one integration settings page as the view draws it."""
    return render_to_string(
        "stewardship/integration-settings.html",
        {
            "target": target,
            "label": target,
            "form": IntegrationForm(target, initial={"base_digest": "a" * 64}),
            "credential": credential,
            "configured": credential is None
            or not credential["candidate"].field.required,
            "complete_gate": True,
            **context,
        },
    )


def tag(html, pattern):
    """The single opening tag matching ``pattern``."""
    (found,) = re.findall(pattern, html)
    return found


def field(html, name):
    """The opening tag of the form control called ``name``."""
    return tag(html, rf'<(?:input|textarea)[^>]*name="{name}"[^>]*>')


def test_a_slack_key_is_required_only_while_slack_is_not_set_up():
    """Not set up, the token is required with set-up wording and a missing hint;
    once set up, a blank token keeps the current one."""
    setup = InlineCredentialForm("slack", initial={"intent": "x"}, required=True)
    candidate = setup.fields["candidate"]
    assert candidate.required and candidate.label == "Slack bot token"
    assert "Leave this blank" not in str(candidate.help_text)
    assert (
        candidate.widget.attrs["data-missing-hint"]
        == "Paste the Slack bot token to set up Slack."
    )
    refused = InlineCredentialForm(
        "slack", {"intent": "x", "candidate": ""}, required=True
    )
    assert not refused.is_valid()
    assert refused.errors["candidate"] == ["Paste the Slack bot token to set up Slack."]
    replace = InlineCredentialForm("slack", {"intent": "x", "candidate": ""})
    assert replace.is_valid()
    assert "data-missing-hint" not in replace.fields["candidate"].widget.attrs


def test_the_parishsoft_key_says_it_is_not_saved_with_a_schedule_change():
    """The field's own help warns before the server refuses the pair."""
    form = InlineCredentialForm("parishsoft", initial={"intent": "x"})
    assert "save the schedule change first" in str(form.fields["candidate"].tip)


@pytest.mark.parametrize("target", ["slack", "email", "google_workspace", "backup"])
def test_integration_settings_wait_for_their_required_fields(target):
    """Save is held by the complete gate, its hint after the buttons."""
    html = settings_page(target, workspace_configured=True)
    assert "data-require-complete" in tag(
        html, r'<form method="post" class="panel"[^>]*>'
    )
    assert HINT in html
    save = tag(html, r"<button[^>]*>(?=Save<)")
    assert 'aria-describedby="integration-complete-hint"' in save
    # The hint follows every button, so it never moves one.
    assert html.index(HINT) > html.rindex("</button>")


def test_a_slack_page_not_set_up_requires_its_token():
    """The token field is drawn required, so Save waits for it."""
    credential = InlineCredentialForm("slack", initial={"intent": "x"}, required=True)
    html = settings_page("slack", credential=credential)
    candidate = field(html, "candidate")
    assert " required" in candidate
    assert 'data-missing-hint="Paste the Slack bot token to set up Slack."' in candidate


def test_test_access_is_unavailable_with_why_until_workspace_mail_is_set_up():
    """Without Workspace mail, Test access is a disabled button named by its
    reason, which links to that setup; with it, Test access submits."""
    html = settings_page("backup", workspace_configured=False)
    button = tag(html, r"<button[^>]*>(?=Test access)")
    assert "disabled" in button and 'type="button"' in button
    assert 'aria-describedby="backup-test-reason"' in button
    reason = tag(html, r'<span class="help" id="backup-test-reason">[^<]*')
    assert "Google Workspace mail to be set up first" in reason
    assert 'href="/admin/system/integrations/google_workspace/"' in html
    ready = settings_page("backup", workspace_configured=True)
    button = tag(ready, r"<button[^>]*>(?=Test access)")
    assert "disabled" not in button and 'value="test"' in button
    assert "backup-test-reason" not in ready


def test_test_access_is_unavailable_with_why_in_the_local_environment(monkeypatch):
    """LOCAL never reaches Google Drive, so Test access is drawn disabled and
    named by that reason even with Workspace mail set up, never offered and
    then refused (#519, #563); the page's context says so from the same
    check the view's refusal uses."""
    html = settings_page("backup", workspace_configured=True, drive_in_local=True)
    button = tag(html, r"<button[^>]*>(?=Test access)")
    assert "disabled" in button and 'type="button"' in button
    assert 'value="test"' not in button
    assert 'aria-describedby="backup-test-reason"' in button
    reason = tag(html, r'<span class="help" id="backup-test-reason">[^<]*')
    assert "not available in the local environment" in reason
    monkeypatch.setattr(integration_views, "_drive_in_local", lambda: True)
    monkeypatch.setattr(integration_views, "backup_state", lambda *args: {})
    request = Value(portal_session=Value(principal_id="0" * 32))
    assert integration_views._backup_context(request, None) == {"drive_in_local": True}


def test_a_refused_key_is_shown_at_the_key_field(monkeypatch):
    """A refusal redraws the key field empty, marked and described by the
    message, with a fresh intent; a missing set-up key says what to paste."""
    monkeypatch.setattr(integration_views, "_handoff", lambda target: None)
    configuration = Value(active_configuration=Value(digest="a" * 64))
    request = Value(portal_session=Value(principal_id="0" * 32))
    missing = integration_views._refused_key(configuration, request, "slack")
    assert missing.errors["candidate"] == ["Paste the Slack bot token to set up Slack."]
    message = "Nothing was saved: save the schedule change first."
    paired = integration_views._refused_key(
        configuration, request, "parishsoft", message
    )
    assert paired.errors["candidate"] == [message]
    html = str(paired["candidate"])
    assert 'aria-invalid="true"' in html and "value=" not in html
    assert paired["intent"].value()


def backup_key_page(**context):
    """Render the backup encryption key page."""
    return render_to_string(
        "stewardship/backup-key.html",
        {"label": "Backup encryption key", "current": Value(source="unknown")}
        | context,
    )


def pattern(html, name):
    """The HTML pattern of the field called ``name``, as a Python regex."""
    value = re.search(r'pattern="([^"]*)"', field(html, name)).group(1)
    return re.compile(value.replace("\\/", "/"))


def test_the_new_public_key_is_checked_in_the_browser():
    """The pattern admits a real key (with the newline backup-keygen prints)
    and refuses what the server refuses by shape; Continue waits for it."""
    html = backup_key_page()
    key = pattern(html, "public_key")
    _, public = generate_keypair()
    assert key.fullmatch(public) and key.fullmatch(public.strip())
    for wrong in ("", public.strip()[:-1], '{"public_key": "x"}', public * 2):
        assert not key.fullmatch(wrong)
    assert (
        '<form method="post" class="panel" autocomplete="off" data-require-complete>'
        in html
    )
    assert 'data-missing-hint="Paste the public_key value' in field(html, "public_key")


def test_the_code_from_backup_prove_is_read_as_leniently_as_the_server_reads_it():
    """Grouped, lower-case or spaced codes pass; a short one waits."""
    html = backup_key_page(challenge="PKBKP1:AAAA", intent="x", new_fingerprint="f")
    code = pattern(html, "code")
    for typed in (
        display_code("ABCD2345EFGH"),
        "abcd2345efgh",
        " ABCD 2345 EFGH ",
    ):
        assert code.fullmatch(typed), typed
    for wrong in ("", "ABCD-2345", "ABCD-2345-EFGH-J", "ABCD_2345_EFGH"):
        assert not code.fullmatch(wrong), wrong


@pytest.mark.parametrize(
    ("context", "name", "message"),
    [
        ({"key_error": "Not a key."}, "public_key", "Not a key."),
        (
            {
                "challenge": "PKBKP1:AAAA",
                "intent": "x",
                "new_fingerprint": "f",
                "code_error": "That code does not match.",
            },
            "code",
            "That code does not match.",
        ),
    ],
)
def test_backup_key_refusals_are_shown_at_their_field(context, name, message):
    """The field is marked and described by the message beside it; no notice."""
    html = backup_key_page(**context)
    control = field(html, name)
    assert 'aria-invalid="true"' in control
    (error_id,) = re.findall(
        rf'<ul class="errorlist" id="([^"]+)"><li>{message}</li>', html
    )
    assert (
        error_id in re.search(r'aria-describedby="([^"]*)"', control).group(1).split()
    )
    assert "notice-error" not in html


def test_a_share_row_ticked_delete_greys_its_fields():
    """Each row's label, free text and order name its own Delete box."""
    formset = ShareOptions(prefix="options", previous=default_share_options())
    for form in formset:
        rule = f"{form.add_prefix('DELETE')}!=on"
        for name in ("label", "free_text", "ORDER"):
            assert form.fields[name].widget.attrs["data-enabled-when"] == rule
        assert "data-enabled-when" not in form.fields["DELETE"].widget.attrs
        assert "data-enabled-when" not in form.fields["id"].widget.attrs


def test_a_deleted_row_redraws_its_saved_values_and_is_still_deleted():
    """A greyed row sends no label; the redrawn row shows the saved one,
    disabled, and the saved options lose only that row."""
    previous = default_share_options()
    data = data_for(previous)
    del data["options-1-label"]
    data["options-1-DELETE"] = "on"
    formset = form_for(previous, data)
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    assert [row["id"] for row in formset.values()] == [
        row["id"] for index, row in enumerate(previous) if index != 1
    ]
    deleted = formset.forms[1]
    assert deleted.fields["label"].disabled
    assert f'value="{previous[1]["label"]}"' in str(deleted["label"]).replace(
        "&#x27;", "'"
    )
    assert not formset.forms[0].fields["label"].disabled
