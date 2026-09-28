"""The optional From name field shared by setup and post-setup mail settings."""

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.sender_name import MAX_SENDER_NAME, clean_sender_name


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

    def clean(self, value):
        """Collapse whitespace and refuse characters that could read as an address."""
        value = super().clean(value)
        try:
            return clean_sender_name(value)
        except ValueError:
            raise forms.ValidationError(
                _(
                    "Use a plain name of at most 100 characters, without "
                    'line breaks or the characters < > @ " \\.'
                ),
                code="sender_name",
            ) from None
