"""The optional From name field shared by setup and post-setup mail settings."""

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.sender_name import MAX_SENDER_NAME, clean_sender_name
from parishkit.stewardship.web.field_rules import (
    SPACE,
    FieldRule,
    apply,
    char_class,
    spans_of,
)

# The characters clean_sender_name refuses: those that could read as or
# smuggle an address (< > @ " \) and every control or format character
# (Unicode categories Cc and Cf, line breaks and bidirectional overrides
# among them), as Python's unicodedata lists them; test_field_rules pins
# this list to unicodedata, so a new Python's Unicode data shows up there.
_CONTROL_AND_FORMAT = (
    (0x0, 0x1F), (0x7F, 0x9F), (0xAD, 0xAD), (0x600, 0x605), (0x61C, 0x61C),
    (0x6DD, 0x6DD), (0x70F, 0x70F), (0x890, 0x891), (0x8E2, 0x8E2),
    (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064),
    (0x2066, 0x206F), (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x110BD, 0x110BD),
    (0x110CD, 0x110CD), (0x13430, 0x1343F), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0001, 0xE0001), (0xE0020, 0xE007F),
)  # fmt: skip
REFUSED = (*_CONTROL_AND_FORMAT, *spans_of('<>@"\\'))

# A From name, checked by the browser and the server alike (#1577): any
# characters but the refused ones. The field strips the spaces around it
# first (some of them, such as a tab, are control characters), so the
# pattern admits any whitespace there.
SENDER_NAME = FieldRule(
    name="sender_name",
    pattern=rf"[{SPACE}]*{char_class(REFUSED, negated=True)}*[{SPACE}]*",
    message=_(
        "Use a plain name of at most 100 characters, without "
        'line breaks or the characters < > @ " \\.'
    ),
    code="sender_name",
)


class SenderNameField(forms.CharField):
    """A single-line display name; blank means "use the Parish name"."""

    def __init__(self, **kwargs):
        """Optional, bounded, and never rendered with browser autocomplete."""
        super().__init__(
            label=_("From name"),
            required=False,
            max_length=MAX_SENDER_NAME,
            **kwargs,
        )
        apply(self, SENDER_NAME)

    def clean(self, value):
        """Collapse whitespace and refuse characters that could read as an address.

        The shared rule refuses them first, in its words; clean_sender_name
        is what it mirrors, and still decides.
        """
        value = super().clean(value)
        if value:
            SENDER_NAME.check(value)
        try:
            return clean_sender_name(value)
        except ValueError:
            raise forms.ValidationError(
                SENDER_NAME.message, code=SENDER_NAME.code
            ) from None
