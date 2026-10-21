"""Bounded ordered share-option forms with campaign-owned immutable identities."""

from uuid import uuid4

from django import forms
from django.forms import formset_factory
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.schema_primitives import text
from parishkit.stewardship.web.admin_forms import AdminForm, AdminFormSet
from parishkit.stewardship.web.content import SHARE_PLACEHOLDERS, validate_share_label
from parishkit.stewardship.web.field_rules import (
    SPACE,
    FieldRule,
    RuleField,
    placeholder_text_pattern,
)

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


# A label's characters and placeholders, checked by the browser and the
# server alike (#1577): what text() and validate_share_label admit, with no
# control character (C0, DEL or C1) anywhere, and the spaces around it that
# the field strips first (a tab among them).
_CONTROLS = "".join(map(chr, [*range(0x20), *range(0x7F, 0xA0)]))
SHARE_LABEL = FieldRule(
    name="share_label",
    pattern=(
        rf"[{SPACE}]*{placeholder_text_pattern(SHARE_PLACEHOLDERS, _CONTROLS)}"
        rf"[{SPACE}]*"
    ),
    message=_(
        "Use visible text and parish_name, pronoun, campaign_year, "
        "financial_period, financial_start or financial_end placeholders."
    ),
    code="invalid_label",
    empty=_("Enter a label for each option, or tick its Delete box."),
)


def default_share_options():
    """Create fresh option IDs, not shared identities between successive campaigns."""
    return [
        {"id": str(uuid4()), "label": label, "free_text": label == "Other"}
        for label in DEFAULT_LABELS
    ]


class ShareOptionForm(AdminForm):
    """Identity is retained only from this campaign; order/deletion are explicit."""

    # A saved option's identity: the change gate counts it (ui-v1.js formState).
    id = forms.UUIDField(
        required=False, widget=forms.HiddenInput(attrs={"data-change-setting": True})
    )
    label = RuleField(SHARE_LABEL, label=_("Option label"), max_length=1024)
    free_text = forms.BooleanField(
        label=_("Include a free-text response"), required=False
    )

    def clean_label(self):
        """Share labels admit only non-private Parish/pronoun substitutions.

        The field's rule (SHARE_LABEL) has refused what these refuse, in
        the same words; they stay the authority.
        """
        value = self.cleaned_data["label"]
        try:
            text(value, 1024)
            validate_share_label(value)
        except ValueError:
            raise forms.ValidationError(
                SHARE_LABEL.message, code=SHARE_LABEL.code
            ) from None
        return value


class ShareOptionsBase(AdminFormSet):
    """The management form bounds work, but never controls authoritative identities."""

    def __init__(self, *args, previous=(), **kwargs):
        """Saved IDs are known server-side; requests cannot import foreign options."""
        self.previous = list(previous)
        super().__init__(*args, initial=self.previous, **kwargs)

    def add_fields(self, form, index):
        """Grey a row's fields in place while its Delete box is ticked (#563).

        A row marked for deletion keeps only its identity and the Delete box:
        its label, free-text choice and order no longer apply, so the page
        script disables them (``data-enabled-when``, greyed and not sent)
        rather than hiding them, which would move the rows below. A redrawn
        form (a Review's answer, or a refusal) shows a deleted row's saved
        values, disabled, since its own were not sent; unticking Delete gives
        them back.
        """
        super().add_fields(form, index)
        delete = form.add_prefix("DELETE")
        for name in ("label", "free_text", "ORDER"):
            form.fields[name].widget.attrs["data-enabled-when"] = f"{delete}!=on"
        if form.is_bound and form.fields["DELETE"].widget.value_from_datadict(
            form.data, form.files, delete
        ):
            for name in ("label", "free_text", "ORDER"):
                form.fields[name].disabled = True

    def clean(self):
        """Reject replayed IDs, foreign IDs and forged initial-management counts."""
        if any(self.errors):
            return
        if self.management_form.cleaned_data.get("INITIAL_FORMS") != len(self.previous):
            raise forms.ValidationError(
                _("Reload the share options before saving."), code="reload"
            )
        allowed = {row["id"] for row in self.previous}
        seen = set()
        for form in self.forms:
            identifier = form.cleaned_data.get("id")
            if identifier is None:
                continue
            identifier = str(identifier)
            if identifier not in allowed or identifier in seen:
                raise forms.ValidationError(
                    _("Share option identities have changed. Reload before saving."),
                    code="identity_changed",
                )
            seen.add(identifier)
        # Deleted rows retain original identities: omitting a saved row is not
        # an explicit deletion or a way to manufacture replacement identities.
        if seen != allowed:
            raise forms.ValidationError(
                _("Every saved option must be retained or explicitly deleted."),
                code="missing_rows",
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
    # A direct save (#1404 PR 4) posts the same rows as a Review.
    rows = parameters.get("action") in {"preview", "save"}
    if rows and (
        not total.isascii()
        or not total.isdecimal()
        or len(total) > 3
        or str(int(total)) != total
        or int(total) > 101
    ):
        raise ValueError("Invalid share option count.")
    fields.update(
        f"options-{index}-{name}"
        for index in range(int(total) if rows else 0)
        for name in ("id", "label", "free_text", "ORDER", "DELETE")
    )
    return form_action(parameters, preview_fields=fields, direct=True)
