"""Every masked secret field uses the shared SecretInput and its Show control.

UX conventions spec, Secret fields (#1475): the setup wizard's ParishSoft
and Slack credential steps and the Integrations ParishSoft and Slack pages
draw their key or token through one widget, masked, never redisplayed, with a
server-drawn Show/Hide button. The guard below keeps any other masked input
out of the Admin portal. The browser behavior is in
browser/test_secret_reveal.py.
"""

import re
from pathlib import Path

import pytest

from parishkit.stewardship.accounts.integration_forms import InlineCredentialForm
from parishkit.stewardship.accounts.secret_input import SecretInput
from parishkit.stewardship.accounts.setup_credential_views import SetupCredentialForm

from .test_conditional_integrations import settings_page
from .test_prerequisite_gates import credential_page

SOURCE = Path(__file__).parents[2] / "src/parishkit/stewardship"
FIELDS = {"parishsoft": "ParishSoft API key", "slack": "Slack bot token"}
SECRET = "synthetic-secret-1475"


def forms_for(target):
    """Every form that draws ``target``'s single-line secret, bound to it."""
    data = {"candidate": SECRET, "intent": "x"}
    forms = [
        SetupCredentialForm(target, data),
        InlineCredentialForm(target, data),
    ]
    if target == "slack":
        # Slack's first set-up form, whose token is required.
        forms.append(InlineCredentialForm(target, data, required=True))
    return forms


def control(html):
    """The opening tags of the secret input and its Show button."""
    (field,) = re.findall(r'<input[^>]*name="candidate"[^>]*>', html)
    (button,) = re.findall(r"<button[^>]*data-secret-reveal[^>]*>", html)
    return field, button


@pytest.mark.parametrize("target", ["parishsoft", "slack"])
def test_every_secret_form_draws_the_shared_masked_widget(target):
    """Masked, never echoed, not altered by the browser, with the control."""
    for form in forms_for(target):
        form.is_valid()
        widget = form.fields["candidate"].widget
        assert isinstance(widget, SecretInput)
        html = str(form["candidate"])
        assert SECRET not in html
        field, button = control(html)
        assert 'type="password"' in field and "value=" not in field
        for attribute in ('autocomplete="off"', 'spellcheck="false"'):
            assert attribute in field
        assert 'autocapitalize="off"' in field
        maxlength = form.fields["candidate"].max_length
        assert f'maxlength="{maxlength}"' in field
        input_id = re.search(r'id="([^"]+)"', field).group(1)
        name = FIELDS[target]
        # No aria-label or aria-pressed: the name is the visible word plus
        # the visually hidden field name, and data-shown holds the state.
        assert button == (
            '<button type="button" class="button-secondary secret-reveal" '
            f'data-secret-reveal aria-controls="{input_id}" data-shown="false" '
            f'data-shown-status="Your {name} is shown." '
            f'data-hidden-status="Your {name} is hidden.">'
        )
        # Both words are drawn, so the button keeps the longer one's width.
        assert (
            '<span class="secret-reveal-show">Show</span>'
            '<span class="secret-reveal-hide">Hide</span>'
            f'<span class="visually-hidden"> the {name}</span></button>'
        ) in html
        assert html.endswith(
            '<span class="visually-hidden" role="status" data-secret-status>'
            "</span></span>"
        )


def test_the_google_key_stays_a_visible_text_area():
    """The multi-line service-account JSON is not masked and has no control."""
    for form in (
        SetupCredentialForm("google_workspace"),
        InlineCredentialForm("google_workspace"),
    ):
        html = str(form["candidate"])
        assert html.startswith("<textarea") and "data-secret-reveal" not in html


@pytest.mark.parametrize("target", ["parishsoft", "slack"])
def test_setup_credential_pages_show_the_control(target):
    """The setup ParishSoft and Slack steps draw the field with its control."""
    html = credential_page(target)
    field, button = control(html)
    assert 'type="password"' in field and "data-secret-reveal" in button
    assert f"> the {FIELDS[target]}</span>" in html


@pytest.mark.parametrize(
    "target,required", [("parishsoft", False), ("slack", False), ("slack", True)]
)
def test_integration_pages_show_the_control(target, required):
    """Integrations ParishSoft and Slack (replace and first set-up) draw it."""
    credential = InlineCredentialForm(
        target, initial={"intent": "x"}, required=required
    )
    html = settings_page(target, credential=credential)
    field, button = control(html)
    assert 'type="password"' in field and "data-secret-reveal" in button
    assert f"> the {FIELDS[target]}</span>" in html


# A masked input built any other way: Django's PasswordInput, a widget given
# attrs={"type": "password"} or a custom input_type = "password" in Python; a
# password input written into a template; or a page script that creates or
# switches a field to password (.type = "password", setAttribute).
PASSWORD_PY = re.compile(
    r"""\bPasswordInput\b|["']type["']\s*:\s*["']password["']"""
    r"""|\binput_type\s*=\s*["']password["']"""
)
PASSWORD_HTML = re.compile(r"""type=["']?password\b""")
PASSWORD_JS = re.compile(
    r"""\.type\s*=(?!=)[^;]*["']password["']"""
    r"""|setAttribute\(\s*["']type["']\s*,\s*["']password["']"""
)
# The one script line allowed to switch a field: the data-secret-reveal
# handler in ui-v1.js.
SHARED_HANDLER = 'field.type = shown ? "text" : "password";'


def js_offenders(path):
    """The lines of page script ``path`` that make a field a password field."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if PASSWORD_JS.search(line)
        and not (path.name == "ui-v1.js" and line.strip() == SHARED_HANDLER)
    ]


def test_no_admin_form_builds_its_own_masked_input():
    """Only SecretInput and its handler make a masked input anywhere."""
    python = sorted(SOURCE.rglob("*.py"))
    templates = sorted(SOURCE.rglob("templates/**/*.html"))
    scripts = sorted(SOURCE.rglob("static/**/*.js"))
    assert len(python) > 100 and len(templates) > 100 and len(scripts) > 10
    offenders = [
        str(path.relative_to(SOURCE))
        for path in python
        if path.name != "secret_input.py" and PASSWORD_PY.search(path.read_text())
    ] + [
        str(path.relative_to(SOURCE))
        for path in templates
        if PASSWORD_HTML.search(path.read_text())
    ]
    offenders += [
        f"{path.relative_to(SOURCE)}: {line}"
        for path in scripts
        for line in js_offenders(path)
    ]
    assert not offenders, f"use SecretInput (accounts/secret_input.py): {offenders}"
    # The allowed handler line is still there, so the exemption is not stale.
    handler = SOURCE / "accounts/static/stewardship/ui-v1.js"
    assert SHARED_HANDLER in handler.read_text()


def test_the_guard_catches_what_it_describes():
    """Each pattern matches a masked input; a password-free one does not."""
    assert PASSWORD_PY.search("widget=forms.PasswordInput(render_value=False)")
    assert PASSWORD_PY.search("forms.TextInput(attrs={'type': 'password'})")
    assert PASSWORD_PY.search('    input_type = "password"')
    assert not PASSWORD_PY.search('forms.TextInput(attrs={"name": "password"})')
    assert PASSWORD_HTML.search('<input type="password" name="x">')
    assert PASSWORD_HTML.search("<input type=password name=x>")
    assert not PASSWORD_HTML.search('<input type="text" name="password">')
    assert PASSWORD_JS.search('input.type = "password";')
    assert PASSWORD_JS.search("el.type = shown ? 'text' : 'password';")
    assert PASSWORD_JS.search('input.setAttribute("type", "password");')
    assert not PASSWORD_JS.search('if (field.type === "password") return;')
    assert not PASSWORD_JS.search('node.matches("input[type=password]")')
