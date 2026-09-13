"""Bounded ordered share-option forms with campaign-owned immutable identities."""

from uuid import uuid4

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.schema_primitives import text
from parishkit.stewardship.web.content import validate_template

DEFAULT_LABELS = (
    "{{ pronoun }} will have my bank send a check (or this is already set up)",
    "{{ pronoun }} will use {{ parish_name }}'s online giving. The parish may "
    "update my withdrawal or credit card charge for the stewardship period if needed.",
    "{{ pronoun }} will start to use {{ parish_name }}'s electronic giving.",
    "A stock gift to {{ parish_name }}",
    "An IRA distribution directly sent to {{ parish_name }}",
    "Offertory envelopes (the parish will have these sent to you)",
    "Other",
)


def default_share_options():
    """Create fresh option IDs, not shared identities between successive campaigns."""
    return [
        {"id": str(uuid4()), "label": label, "free_text": label == "Other"}
        for label in DEFAULT_LABELS
    ]


class ShareOptionForm(forms.Form):
    """Identity is retained only from this campaign; order/deletion are explicit."""

    id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    label = forms.CharField(label=_("Option label"), max_length=1024)
    free_text = forms.BooleanField(
        label=_("Include a free-text response"), required=False
    )

    def clean_label(self):
        """Share labels admit only non-private Parish/pronoun substitutions."""
        value = self.cleaned_data["label"]
        try:
            text(value, 1024)
            names = validate_template(value)
            if not names <= {"parish_name", "pronoun"}:
                raise ValueError
        except ValueError:
            raise forms.ValidationError(
                _("Use visible text and only the parish_name and pronoun placeholders.")
            ) from None
        return value


class ShareOptionsBase(BaseFormSet):
    """The management form bounds work, but never controls authoritative identities."""

    def __init__(self, *args, previous=(), **kwargs):
        """Saved IDs are known server-side; requests cannot import foreign options."""
        self.previous = list(previous)
        super().__init__(*args, initial=self.previous, **kwargs)

    def clean(self):
        """Reject replayed IDs, foreign IDs and forged initial-management counts."""
        if any(self.errors):
            return
        if self.management_form.cleaned_data.get("INITIAL_FORMS") != len(self.previous):
            raise forms.ValidationError(_("Reload the share options before saving."))
        allowed = {row["id"] for row in self.previous}
        seen = set()
        for form in self.forms:
            identifier = form.cleaned_data.get("id")
            if identifier is None:
                continue
            identifier = str(identifier)
            if identifier not in allowed or identifier in seen:
                raise forms.ValidationError(
                    _("Share option identities have changed. Reload before saving.")
                )
            seen.add(identifier)
        # Deleted rows retain original identities: omitting a saved row is not
        # an explicit deletion or a way to manufacture replacement identities.
        if seen != allowed:
            raise forms.ValidationError(
                _("Every saved option must be retained or explicitly deleted.")
            )

    def values(self):
        """Django orders surviving rows; unchanged saved IDs remain stable."""
        if not self.is_valid():
            raise ValueError("Valid share options are required.")
        return [
            {
                "id": str(form.cleaned_data.get("id") or uuid4()),
                "label": form.cleaned_data["label"],
                "free_text": form.cleaned_data["free_text"],
            }
            for form in self.ordered_forms
        ]


ShareOptions = formset_factory(
    ShareOptionForm,
    formset=ShareOptionsBase,
    extra=1,
    can_order=True,
    can_delete=True,
    max_num=100,
    validate_max=True,
    absolute_max=101,
)


def share_action(parameters):
    """Admit only bounded formset fields; duplicate scalars are never last-wins."""
    from .admin_editing import form_action

    fields = {
        "base_digest",
        "options-TOTAL_FORMS",
        "options-INITIAL_FORMS",
        "options-MIN_NUM_FORMS",
        "options-MAX_NUM_FORMS",
    }
    total = parameters.get("options-TOTAL_FORMS", "")
    if parameters.get("action") == "preview" and (
        not total.isascii()
        or not total.isdecimal()
        or len(total) > 3
        or str(int(total)) != total
        or int(total) > 101
    ):
        raise ValueError("Invalid share option count.")
    fields.update(
        f"options-{index}-{name}"
        for index in range(int(total) if parameters.get("action") == "preview" else 0)
        for name in ("id", "label", "free_text", "ORDER", "DELETE")
    )
    return form_action(parameters, preview_fields=fields)
