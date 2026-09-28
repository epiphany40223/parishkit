"""Move long Admin field help into a click-to-open toggletip beside the label.

A field whose help runs past about one line gets an "i" button beside its label
(components/toggletip.html; behavior in ui-v1.js) that opens the full text, and
keeps only an optional one-line hint visible under the label for what Admins
need every time, such as a format. The input stays described by the full text
(``aria-describedby`` also names the hidden bubble, which assistive technology
reads even while it is closed), so screen-reader users lose nothing.

Checkbox fields and fields that already use their own template keep their
help visible: their layouts place the help beside the box, and their help is
short enough to read in place.
"""

from django import forms
from django.forms.boundfield import BoundField

TIP_TEMPLATE = "stewardship/field-tip.html"
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


def eligible(field):
    """Whether the field's layout can take a tip beside its label."""
    return field.template_name is None and not isinstance(
        field.widget, forms.CheckboxInput
    )


def add(field, tip, hint=""):
    """Show ``tip`` behind the field's "i" button and ``hint`` under its label."""
    field.tip = tip
    field.help_text = hint
    field.template_name = TIP_TEMPLATE
    field.bound_field_class = TipBoundField


def shorten(form, hints=None):
    """Turn every long, eligible help text on ``form`` into a tip.

    ``hints`` maps field names to the one-line text that stays visible, for
    fields whose format or rule Admins need to see every time they fill it in.
    """
    hints = hints or {}
    for name, field in form.fields.items():
        text = field.help_text
        if text and len(str(text)) > LONG_HELP and eligible(field):
            add(field, text, hints.get(name, ""))
    return form
