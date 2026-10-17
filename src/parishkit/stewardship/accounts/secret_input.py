"""The one masked input for a single-line secret an Admin types (#1475).

UX conventions spec, Secret fields: every API key or bot token an Admin
pastes is masked by default and has the shared Show/Hide control beside it.
The control is drawn here, by the server, so it is in place on first paint
and nothing moves when the page script starts; ``ui-v1.js``
(``data-secret-reveal``) only switches the field between ``password`` and
``text``. The value is never drawn back (``render_value=False``), so a saved
secret or a refused one never reaches the browser and the control shows only
what was typed in this page view.

``tests/stewardship/test_secret_input.py`` refuses any other masked input in
the Admin portal (Python, templates and page scripts), so a new secret field
cannot skip the control.
"""

from django import forms
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

# The name of each integration's secret, as its field's label gives it. The
# control's accessible name is its visible word followed by "the <name>"
# (visually hidden), and its status line says "Your <name> is shown."
SECRET_NAMES = {
    "parishsoft": _("ParishSoft API key"),
    "slack": _("Slack bot token"),
}


class SecretInput(forms.PasswordInput):
    """A masked, never-redisplayed secret field with the shared Show control."""

    template_name = "stewardship/widgets/secret-input.html"

    def __init__(self, target):
        """Mask ``target``'s secret; browsers neither fill nor alter it."""
        super().__init__(
            render_value=False,
            attrs={
                "autocomplete": "off",
                "spellcheck": "false",
                "autocapitalize": "off",
            },
        )
        self.secret_name = SECRET_NAMES[target]

    def get_context(self, name, value, attrs):
        """Add the control's hidden field name and status lines to the context."""
        context = super().get_context(name, value, attrs)
        field = {"field": self.secret_name}
        context["widget"].update(
            reveal_name=gettext("the %(field)s") % field,
            shown_status=gettext("Your %(field)s is shown.") % field,
            hidden_status=gettext("Your %(field)s is hidden.") % field,
        )
        return context
