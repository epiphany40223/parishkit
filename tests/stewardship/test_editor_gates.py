"""Settings editors' change and complete gates and field errors (#1330, #563).

The pages draw the gates the page script applies: integration Save and the
Pages and emails editor's Preview changes wait for a change on a first
draw, Test access never does, Slack's Save is unavailable while its key
can't be stored, New and Edit user wait for their fields, the user code and
the Parish website carry patterns the server's own rules also admit, and the
leader roles need one ticked. Their browser behavior is in
browser/test_editor_gates.py; the server's refusals stay and are tested
beside the views.
"""

import re

import pytest
from django import forms
from django.template.loader import render_to_string

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.automation_sessions import normalized_code
from parishkit.stewardship.accounts.integration_forms import (
    FOLDER_LINK_PATTERN,
    IntegrationForm,
)
from parishkit.stewardship.accounts.parish_views import WEBSITE, ParishForm
from parishkit.stewardship.accounts.setup_forms import SetupParishForm
from parishkit.stewardship.backup_drive import folder_id_from_url
from parishkit.stewardship.schema_primitives import typed

from .test_conditional_integrations import settings_page, tag
from .test_review_dialog import TEMPLATES

WEBSITES = [
    "https://example.org",
    "https://example.org/",
    "https://example.org/parish/@home",
    "http://example.org:8080/a/b",
    "https://example.org/?",
    "https://example.org?",
    "https://example.org/#",
    "https://user@example.org/",
    "https://user:secret@example.org/",
    "https://example.org/?a=1",
    "https://example.org/#top",
]
FOLDERS = [
    "https://drive.google.com/drive/folders/" + "a" * 20,
    "https://drive.google.com/drive/u/1/folders/" + "a" * 20 + "?usp=sharing",
    "HTTPS://Drive.Google.com/open?id=" + "a" * 20,
    "  " + "a" * 20 + "  ",
    "https://docs.google.com/document/d/x",
    "https://example.org/folders/" + "a" * 20,
    "x",
]
CODES = ["ABCD-EFGH", "abcd efgh", " ab-cd-ef-gh ", "ABCDEFG", "ABCD-EFGI", "ABCDEFGHJ"]


def admitted(check, value):
    """Whether the server's rule ``check`` admits ``value``."""
    try:
        check(value)
    except (ConfigError, ValueError):
        return False
    return True


def browser_match(pattern, value):
    """Whether the browser would match ``value`` to ``pattern`` (anchored).

    Python's ``re`` reads these patterns as the browser's "v" flag does:
    they use only classes, groups, counts and escaped punctuation.
    """
    return re.fullmatch(pattern, value) is not None


@pytest.mark.parametrize("value", WEBSITES)
def test_the_website_pattern_admits_every_website_the_server_admits(value):
    """The browser never refuses a website the configuration would store."""
    if admitted(lambda text: typed(text, "url"), value):
        assert browser_match(WEBSITE.pattern, value)


def test_the_website_pattern_refuses_credentials_queries_and_fragments():
    """What the browser catches at once: a user name, a query, a fragment."""
    for value in WEBSITES[-4:]:
        assert not browser_match(WEBSITE.pattern, value)


@pytest.mark.parametrize("form_class", [ParishForm, SetupParishForm])
def test_a_refused_website_is_shown_at_its_field(form_class):
    """Parish settings and setup refuse the website at the field, in the
    words the browser's hint uses, and draw the pattern."""
    form = form_class()
    attrs = form.fields["website"].widget.attrs
    assert attrs["pattern"] == WEBSITE.pattern
    assert attrs["data-missing-hint"] == WEBSITE.message
    assert attrs["data-empty-hint"] == WEBSITE.empty
    form.cleaned_data = {"website": "https://example.org/?a=1"}
    with pytest.raises(forms.ValidationError) as refused:
        form.clean_website()
    assert refused.value.messages == [str(WEBSITE.message)]


@pytest.mark.parametrize("value", FOLDERS)
def test_the_folder_link_pattern_admits_every_link_the_server_reads(value):
    """A link folder_id_from_url reads is never refused in the browser, and
    the backup form draws the pattern."""
    if admitted(folder_id_from_url, value):
        assert browser_match(FOLDER_LINK_PATTERN, value)
    attrs = IntegrationForm("backup").fields["target"].widget.attrs
    assert attrs["pattern"] == FOLDER_LINK_PATTERN


def test_the_folder_link_pattern_refuses_links_off_drive():
    """Another site, or a word that is no folder ID, is caught at once."""
    for value in FOLDERS[-3:]:
        assert not browser_match(FOLDER_LINK_PATTERN, value)


def approval(**context):
    """The automation approval's code entry, as the view draws it."""
    return render_to_string(
        "stewardship/automation-approval.html",
        {"state": "enter", "back": "/b", "next": "/n"} | context,
    )


@pytest.mark.parametrize("value", CODES)
def test_the_user_code_pattern_reads_codes_as_the_server_does(value):
    """The code's pattern matches exactly the codes normalized_code reads."""
    html = approval()
    (pattern,) = re.findall(r'id="automation-code"[^>]* pattern="([^"]*)"', html)
    assert browser_match(pattern, value) == (normalized_code(value) is not None)


def test_a_refused_code_is_shown_at_the_field():
    """The refusal is the field's error, described by it, and Continue waits
    for a code with its hint reserved after it."""
    html = approval(refused=True)
    code = tag(html, r'<input id="automation-code"[^>]*>')
    assert 'aria-invalid="true"' in code
    assert 'aria-describedby="automation-code-error"' in code
    assert 'id="automation-code-error"' in html
    assert "notice-error" not in html.split('id="code-heading"')[1]
    assert "data-require-complete" in tag(html, r"<form[^>]*data-require-complete>")
    button = tag(html, r"<button[^>]*>(?=Continue)")
    assert 'aria-describedby="automation-code-hint"' in button
    assert 'id="automation-code-hint" data-complete-hint="reserve"' in html


def test_integration_save_waits_for_a_change_on_its_first_draw():
    """change_gate marks the form; Test access is exempt; the hint follows
    the buttons. A redrawn refusal (no change_gate) is not gated."""
    html = settings_page("backup", workspace_configured=True, change_gate=True)
    form = tag(html, r'<form method="post"[^>]*id="integration-form"[^>]*>')
    assert 'data-require-change="base_digest"' in form
    test = tag(html, r"<button[^>]*>(?=Test access)")
    assert "data-change-exempt" in test
    assert "data-unchanged-hint data-idle>Change a setting to save it." in html
    slack = settings_page("slack", change_gate=True)
    assert html.index("data-unchanged-hint") > html.index(">Test access<")
    assert "data-unchanged-hint" in slack
    refused = settings_page("slack")
    assert "data-require-change" not in refused
    assert "data-unchanged-hint" not in refused


def test_slack_save_is_unavailable_while_its_key_cannot_be_stored():
    """Not set up, with the key installer down: Save is drawn disabled,
    named by the notice; set up, the other settings stay savable."""
    html = settings_page(
        "slack", configured=False, credential_unavailable=True, change_gate=True
    )
    save = tag(html, r"<button[^>]*>(?=Save<)")
    assert " disabled data-server-disabled" in save
    assert 'aria-describedby="integration-key-unavailable"' in save
    notice = tag(html, r'<p class="notice" id="integration-key-unavailable">[^<]*')
    assert "Setting this up is temporarily unavailable" in notice
    configured = settings_page(
        "slack", configured=True, credential_unavailable=True, change_gate=True
    )
    save = tag(configured, r"<button[^>]*>(?=Save<)")
    assert "disabled" not in save
    assert "You can still change the other settings." in configured


def content_page(change_gate):
    """The Pages and emails editor around an unbound email form."""
    from parishkit.stewardship.accounts.content_forms import ContentForm

    return render_to_string(
        "stewardship/content-settings.html",
        {
            "campaign": {"active_configuration": {"name": "Sample"}},
            "label": "Confirmation email",
            "placeholders": [],
            "change_gate": change_gate,
            "form": ContentForm(
                kind="email",
                slot="confirmation",
                initial={"base_digest": "a" * 64, "subject": "Hi", "html": "<p>x</p>"},
            ),
        },
    )


def test_preview_changes_waits_for_a_change_only_when_asked():
    """With change_gate the form names its version field and Preview
    changes has its reserved reason; without it, Preview is plain."""
    html = content_page(True)
    form = tag(html, r"<form[^>]*data-content-form[^>]*>")
    assert 'id="content-form"' in form and 'data-require-change="base_digest"' in form
    button = tag(html, r"<button[^>]*>(?=Preview changes<)")
    assert 'autocomplete="off"' in button
    assert "data-unchanged-hint data-idle>Change the content to preview it." in html
    plain = content_page(False)
    assert "data-require-change" not in plain and "data-unchanged-hint" not in plain


class Unbound:
    """A form stub as _page reads it: unbound, with empty HTML."""

    is_bound = False

    def __getitem__(self, name):
        """Each field's value is empty."""
        return type("Field", (), {"value": lambda self: ""})()


@pytest.mark.parametrize("started,stale", [(True, None), (False, {})])
def test_the_view_gates_preview_only_for_saved_content_as_shown(
    monkeypatch, started, stale
):
    """The unsaved default text and a saved text that needs re-saving can be
    previewed as shown; an untouched saved text waits for a change."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import content_views

    seen = {}

    def render(request, template, context, status):
        """Keep the context instead of drawing the page."""
        seen.update(context)
        return SimpleNamespace()

    monkeypatch.setattr(content_views, "render", render)
    monkeypatch.setattr(content_views.admin_navigation, "place", lambda *a, **k: None)
    request = SimpleNamespace(path="/x")
    content_views._page(request, Unbound(), None, "L", started=started, stale=stale)
    assert seen["change_gate"] is False
    content_views._page(request, Unbound(), None, "L")
    assert seen["change_gate"] is True


def user_entry(new):
    """New or Edit user as the view draws it."""
    from parishkit.stewardship.accounts.user_views import UserForm

    return render_to_string(
        "stewardship/user-entry.html",
        {
            "new": new,
            "form": UserForm(new=new),
            "roles": [],
            "user": {"email": "a@example.org"},
            "post_url": "/p",
            "list_url": "/l",
            "apply_url": "/a",
            "base_digest": "a" * 64,
        },
    )


@pytest.mark.parametrize("new", [True, False])
def test_user_review_waits_for_its_fields(new):
    """Add the user or Save waits for a complete entry, with the reason in a
    reserved line it names; Email and Name say what they wait for."""
    html = user_entry(new)
    assert "data-require-complete" in tag(html, r'<form[^>]*id="user-entry-form"[^>]*>')
    button = tag(html, r"<button[^>]*>(?=(?:Add the user|Save)<)")
    assert 'aria-describedby="user-complete-hint"' in button
    assert 'id="user-complete-hint" data-complete-hint="reserve"' in html
    name = tag(html, r'<input[^>]*name="name"[^>]*>')
    assert 'data-missing-hint="Enter a name."' in name
    if new:
        email = tag(html, r'<input[^>]*name="email"[^>]*>')
        assert (
            'data-empty-hint="Enter the email address this person signs in with."'
            in email
        )
        assert "data-missing-hint=" in email


def test_leader_roles_need_one_ticked_while_shown():
    """The leader roles are a data-require-one group with the server's words."""
    source = (TEMPLATES / "campaign-fields.html").read_text(encoding="utf-8")
    assert (
        "<div data-require-one data-missing-hint=\"{% translate 'Choose at least "
        "one role.' %}\">{{ form.ministry_leader_roles.as_field_group }}"
    ) in source


def test_hidden_settings_are_marked_for_the_change_gate():
    """The change gate counts a hidden field only when it is marked
    data-change-setting (#1334 re-review): a saved row's identity, the
    refresh rules' kind and ParishSoft's stored quick updates are; a signed
    one-time intent and a version field are not. The browser guard
    (browser/test_editor_gates.py) checks every gated fixture page."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.integration_forms import (
        InlineCredentialForm,
    )
    from parishkit.stewardship.accounts.refresh_schedule_forms import RuleForm
    from parishkit.stewardship.accounts.schedule_forms import ScheduleForm
    from parishkit.stewardship.accounts.share_forms import ShareOptionForm

    def marked(field):
        """Whether the field's widget carries the change gate's mark."""
        return "data-change-setting" in field.widget.attrs

    assert marked(ShareOptionForm.base_fields["id"])
    assert marked(ScheduleForm.base_fields["id"])
    assert not marked(ScheduleForm.base_fields["drawn_at"])
    assert marked(RuleForm.base_fields["kind"])
    assert not marked(InlineCredentialForm.base_fields["intent"])
    assert not marked(IntegrationForm.base_fields["base_digest"])
    quick = SimpleNamespace(
        index=0,
        number=1,
        quick=SimpleNamespace(fields=[("rules-0-kind", "quick")], words="Quick"),
        messages=[],
    )
    html = render_to_string("stewardship/refresh-schedule-rule.html", {"row": quick})
    assert (
        '<input type="hidden" name="rules-0-kind" value="quick" data-change-setting>'
        in html
    )
