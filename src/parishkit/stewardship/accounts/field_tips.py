"""Move long Admin field help into a click-to-open toggletip beside the label.

A field whose help runs past about one line gets an "i" button beside its label
(components/toggletip.html; behavior in ui-v1.js) that opens the full text, and
keeps only an optional one-line hint visible under the label for what Admins
need every time, such as a format. The input stays described by the full text
(``aria-describedby`` also names the hidden bubble, which assistive technology
reads even while it is closed), so screen-reader users lose nothing.

Fields that already use their own template keep their help visible: their
help is short enough to read in place (one line, #2130;
``test_admin_help_guards``). A checkbox keeps its help under its words too,
unless it has a one-line hint to keep there: then its long help moves into
the tip beside its words like any other field's (Campaign settings' modules,
#2144), so a checkbox never loses its one help line.
The exceptions are a field with a shared format rule
(``web.field_rules.apply``), whose field group, with a reserved error line,
draws a tip itself, and a field whose own template draws one
(``TIP_TEMPLATES``), so the field keeps both.
"""

from django import forms
from django.forms.boundfield import BoundField
from django.utils.text import format_lazy

TIP_TEMPLATE = "stewardship/field-tip.html"
# Field templates that draw the field's tip themselves: a field already
# drawn by one can still move long help into a tip (a form that shortens its
# own help, which a view's setup_help.apply then shortens again).
TIP_TEMPLATES = {TIP_TEMPLATE, "stewardship/file-size-field.html"}
# Longer help than this (about one line of the form column) becomes a tip.
LONG_HELP = 120


class TipBoundField(BoundField):
    """A bound field that labels itself for its tip and is described by it."""

    @property
    def tip_id(self):
        """The bubble's element id, derived from the field's own id."""
        return f"{self.auto_id}_tip"

    @property
    def tip_label_id(self):
        """The label's (or legend's) id, which describes the tip button."""
        return f"{self.auto_id}_label"

    def tip_label(self):
        """The label or legend with an id, so the tip button can name its field."""
        tag = self.legend_tag if self.use_fieldset else self.label_tag
        return tag(attrs={"id": self.tip_label_id})

    @property
    def aria_describedby(self):
        """Add the tip bubble after Django's hint and error descriptions."""
        described = super().aria_describedby
        if described is None or not self.auto_id or self.is_hidden:
            return described
        return " ".join(filter(None, [described, self.tip_id]))


def eligible(field, hint=""):
    """Whether the field's layout can take a tip beside its label.

    A checkbox can only when ``hint`` keeps one help line under its words.
    """
    own_template = field.template_name not in {None, *TIP_TEMPLATES} and not ruled(
        field
    )
    checkbox = isinstance(field.widget, forms.CheckboxInput)
    return not own_template and (bool(hint) or not checkbox)


def ruled(field):
    """Whether the field has a shared format rule (``web.field_rules``)."""
    return getattr(field, "field_rule", None) is not None


def add(field, tip, hint=""):
    """Show ``tip`` behind the field's "i" button and ``hint`` under its label."""
    field.tip = tip
    field.help_text = hint
    # A ruled field's group, with its reserved error line, draws the tip too,
    # as do the TIP_TEMPLATES, which are kept.
    if not ruled(field) and field.template_name not in TIP_TEMPLATES:
        field.template_name = TIP_TEMPLATE
    field.bound_field_class = TipBoundField


def shorten(form, hints=None):
    """Turn every long, eligible help text on ``form`` into a tip.

    ``hints`` maps field names to the one-line text that stays visible, for
    fields whose format or rule Admins need to see every time they fill it in.
    """
    hints = hints or {}
    for name, field in form.fields.items():
        text, hint = field.help_text, hints.get(name, "")
        if text and len(str(text)) > LONG_HELP and eligible(field, hint):
            add(field, text, hint)
    return form


def lock(field, reason):
    """Draw ``field`` disabled where it stands, with ``reason`` in its tip.

    A disabled control is not sent, and Django's disabled field takes its
    saved value whatever is posted, so a locked setting can never change
    through its form. A field whose long help already moved into a tip gets
    the reason first in that tip; any other gets a tip of its own, keeping
    its short help visible. A field drawn by its own template is only
    disabled. Campaign settings (#1119) and Share options
    on a locked campaign (#1569) lock their fields this way (ui-conventions:
    a setting that can't change stays in place, disabled, with the reason in
    its tip).
    """
    field.disabled = True
    # A ruled field's group template draws a tip too (see ``add``).
    if field.template_name not in (None, TIP_TEMPLATE) and not ruled(field):
        return
    tip = getattr(field, "tip", None)
    add(field, format_lazy("{} {}", reason, tip) if tip else reason, field.help_text)
