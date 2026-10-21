"""Bounded ordered share-option forms with campaign-owned immutable identities."""

from uuid import uuid4

from django import forms
from django.forms import formset_factory
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.configuration import (
    SHORT_LABEL_LIMIT,
    short_label_key,
    validate_short_label,
)
from parishkit.stewardship.schema_primitives import text
from parishkit.stewardship.web.admin_forms import AdminForm, AdminFormSet
from parishkit.stewardship.web.content import SHARE_PLACEHOLDERS, validate_share_label
from parishkit.stewardship.web.field_rules import (
    SPACE,
    FieldRule,
    RuleField,
    char_class,
    placeholder_text_pattern,
    spans_of,
)

from . import field_tips

# A row's editable fields, which grey with its Delete box.
ROW_FIELDS = ("label", "short_label", "free_text", "ORDER")
# The fields a live campaign's structure lock keeps fixed: all but the short
# label (#1569).
STRUCTURE_FIELDS = ("label", "free_text", "ORDER", "DELETE")
# Why, on a locked campaign, the page, each locked field's tip and a refused
# change all say the same (#1569). "Locked" rather than "live": the mode is
# on for any current campaign whose settings cannot be edited, including a
# draft outside Testing mode.
SHORT_LABELS_ONLY = _("This campaign is locked, so only the short labels can change.")

# The default options' (label, short label) pairs: the short label is what
# Admin tables and charts show (#1569); Families see the label.
DEFAULT_OPTIONS = (
    (
        "{{ pronoun }} will have my bank send a check (or this is already set up)",
        "Bank check",
    ),
    (
        "{{ pronoun }} will use {{ parish_name }}'s online giving. The parish may "
        "update my withdrawal or credit card charge for the stewardship period "
        "if needed.",
        "Online",
    ),
    (
        "{{ pronoun }} will start to use {{ parish_name }}'s electronic giving.",
        "New e-giving",
    ),
    ("A stock gift to {{ parish_name }}", "Stock gift"),
    ("An IRA distribution directly sent to {{ parish_name }}", "IRA"),
    ("Offertory envelopes (the parish will have these sent to you)", "Envelopes"),
    ("Other", "Other"),
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

# A short label's characters (#1569), checked by the browser and the server
# alike: what validate_short_label admits, plain visible text with no brace
# and no control character, and the spaces around it that the field strips
# first. Its length is the field's maxlength.
SHORT_LABEL_RULE = FieldRule(
    name="share_short_label",
    pattern=(
        rf"[{SPACE}]*{char_class(spans_of('{}' + _CONTROLS), negated=True)}*"
        rf"[{SPACE}]*"
    ),
    message=_("Use plain text without placeholders or braces."),
    code="invalid_short_label",
)
# Said at every option whose short label another kept option also has: by
# the server on Save, and by the page as the Admin types (ui-v1.js
# data-distinct, which compares the values as short_label_key does).
DUPLICATE_SHORT_LABEL = _("Another option already has this short label.")


def default_share_options():
    """Create fresh option IDs, not shared identities between successive campaigns."""
    return [
        {
            "id": str(uuid4()),
            "label": label,
            "free_text": label == "Other",
            "short_label": short,
        }
        for label, short in DEFAULT_OPTIONS
    ]


class ShareOptionForm(AdminForm):
    """Identity is retained only from this campaign; order/deletion are explicit."""

    # A saved option's identity: the change gate counts it (ui-v1.js formState).
    id = forms.UUIDField(
        required=False, widget=forms.HiddenInput(attrs={"data-change-setting": True})
    )
    label = RuleField(SHARE_LABEL, label=_("Option label"), max_length=1024)
    # Optional (#1569); blank means Admin screens show the full label.
    short_label = RuleField(
        SHORT_LABEL_RULE,
        label=_("Short label"),
        max_length=SHORT_LABEL_LIMIT,
        required=False,
        help_text=_(
            "Optional. Shown in the Admin tables and charts instead of the long "
            "label. Families still see the full label."
        ),
        # Every row's short label is one group: no two kept options share
        # one, checked as the Admin types (UX conventions "Field errors and
        # live checks"); the server's check below stays the authority.
        widget=forms.TextInput(
            attrs={
                "data-distinct": "short-label",
                "data-distinct-message": DUPLICATE_SHORT_LABEL,
            }
        ),
    )
    free_text = forms.BooleanField(
        label=_("Include a free-text response"), required=False
    )

    def __init__(self, *args, **kwargs):
        """Reserve the short label's error line for either of its messages."""
        super().__init__(*args, **kwargs)
        # Its reserved line (file-size-field.html) keeps the height of the
        # taller of the two messages the page says there, so one wrapping
        # at 320 px moves nothing below it.
        self.fields["short_label"].error_sizes = (
            SHORT_LABEL_RULE.message,
            DUPLICATE_SHORT_LABEL,
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

    def clean_short_label(self):
        """A short label is plain visible text: no placeholders (#1569).

        The field's rule (SHORT_LABEL_RULE) has refused what this refuses,
        in the same words; it stays the authority.
        """
        value = self.cleaned_data["short_label"]
        try:
            if value:
                validate_short_label(value)
        except ValueError:
            raise forms.ValidationError(
                SHORT_LABEL_RULE.message, code=SHORT_LABEL_RULE.code
            ) from None
        return value


class ShareOptionsBase(AdminFormSet):
    """The management form bounds work, but never controls authoritative identities."""

    def __init__(self, *args, previous=(), short_only=False, **kwargs):
        """Saved IDs are known server-side; requests cannot import foreign options.

        ``short_only`` is a locked campaign's form (#1569): every saved row
        keeps its label, free-text choice, order and Delete box, which are
        disabled with the reason in their tips (Django then uses the saved
        values, whatever is posted), and there is no blank row, so only the
        short labels can change.
        """
        self.previous = list(previous)
        self.short_only = short_only
        if short_only:
            self.extra = 0
        super().__init__(*args, initial=self.previous, **kwargs)

    def add_fields(self, form, index):
        """Grey a row's fields in place while its Delete box is ticked (#563).

        A row marked for deletion keeps only its identity and the Delete box:
        its label, short label, free-text choice and order no longer apply, so
        the page script disables them (``data-enabled-when``, greyed and not sent)
        rather than hiding them, which would move the rows below. A redrawn
        form (a Review's answer, or a refusal) shows a deleted row's saved
        values, disabled, since its own were not sent; unticking Delete gives
        them back.
        """
        super().add_fields(form, index)
        if self.short_only:
            for name in STRUCTURE_FIELDS:
                field_tips.lock(form.fields[name], SHORT_LABELS_ONLY)
            return
        delete = form.add_prefix("DELETE")
        for name in ROW_FIELDS:
            form.fields[name].widget.attrs["data-enabled-when"] = f"{delete}!=on"
        if form.is_bound and form.fields["DELETE"].widget.value_from_datadict(
            form.data, form.files, delete
        ):
            for name in ROW_FIELDS:
                form.fields[name].disabled = True

    def clean(self):
        """Reject replayed IDs, foreign IDs and forged initial-management counts."""
        if any(self.errors):
            return
        management = self.management_form.cleaned_data
        if management.get("INITIAL_FORMS") != len(self.previous) or (
            # A live campaign's form has no blank row to add an option in.
            self.short_only and management.get("TOTAL_FORMS") != len(self.previous)
        ):
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
        self._unique_short_labels()

    def _unique_short_labels(self):
        """Refuse a short label another kept option already uses (#1569).

        Compared ignoring case and surrounding spaces (``short_label_key``),
        as a reader would. Every option sharing the label is marked, as the
        page's live check marks them, so the one just typed in is marked
        whichever row it is. Deleted rows do not count, since their labels go
        with them (the page disables their fields).
        """
        kept = [form for form in self.forms if not self._should_delete_form(form)]
        keys = [
            short_label_key(form.cleaned_data.get("short_label") or "") for form in kept
        ]
        for form, key in zip(kept, keys, strict=True):
            if key and keys.count(key) > 1:
                form.add_error(
                    "short_label",
                    forms.ValidationError(
                        DUPLICATE_SHORT_LABEL, code="duplicate_short_label"
                    ),
                )

    def values(self):
        """Django orders surviving rows; unchanged saved IDs remain stable."""
        if not self.is_valid():
            raise ValueError("Valid share options are required.")
        # The short label is stored only when set (#1569), so an option
        # without one keeps the shape every earlier configuration has.
        return [
            {
                "id": str(form.cleaned_data.get("id") or uuid4()),
                "label": form.cleaned_data["label"],
                "free_text": form.cleaned_data["free_text"],
            }
            | (
                {"short_label": form.cleaned_data["short_label"]}
                if form.cleaned_data["short_label"]
                else {}
            )
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
        for name in ("id", *ROW_FIELDS, "DELETE")
    )
    return form_action(parameters, preview_fields=fields, direct=True)
