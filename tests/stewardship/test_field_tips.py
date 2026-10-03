"""Long Admin field help moves into a toggletip beside the label."""

from django import forms

from parishkit.stewardship.accounts import field_tips
from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.schedule_forms import NO_TEMPLATES, ScheduleForm
from parishkit.stewardship.accounts.setup_credential_views import SetupCredentialForm

LONG = "Long help <b>" + "x" * field_tips.LONG_HELP


class Sample(forms.Form):
    """One field of each kind the tip rule distinguishes."""

    short = forms.CharField(help_text="Short.")
    long = forms.CharField(help_text=LONG)
    choices = forms.MultipleChoiceField(
        choices=[("a", "A")], widget=forms.CheckboxSelectMultiple, help_text=LONG
    )
    box = forms.BooleanField(help_text=LONG)


def test_only_long_help_on_eligible_fields_becomes_a_tip():
    """Short help and checkbox help stay visible; long help keeps its hint."""
    form = field_tips.shorten(Sample(), {"long": "Hint."})
    assert not hasattr(form.fields["short"], "tip")
    assert not hasattr(form.fields["box"], "tip")
    assert form.fields["box"].help_text == LONG
    assert form.fields["long"].tip == LONG
    assert form.fields["long"].help_text == "Hint."
    assert form.fields["choices"].help_text == ""


def test_tip_markup_is_labelled_described_and_escaped():
    """The button names its field via the label id; the control reads the tip."""
    form = field_tips.shorten(Sample(), {"long": "Hint."})
    html = form["long"].as_field_group()
    assert '<div class="label-row"><label id="id_long_label" for="id_long">' in html
    assert 'aria-controls="id_long_tip"' in html
    assert 'aria-describedby="id_long_label"' in html
    assert 'id="id_long_tip" hidden>Long help &lt;b&gt;' in html
    assert 'aria-describedby="id_long_helptext id_long_tip"' in html
    # A fieldset keeps its legend first and describes the group by the tip.
    group = form["choices"].as_field_group()
    assert '<fieldset aria-describedby="id_choices_tip">' in group
    assert '<legend id="id_choices_label"' in group


def test_errors_stay_in_the_description_before_the_tip():
    """A refused value still announces its error alongside the full help."""
    form = field_tips.shorten(Sample({"short": "a", "box": "on"}))
    assert not form.is_valid()
    html = str(form["long"])
    assert 'aria-describedby="id_long_error id_long_tip"' in html


def test_setup_credential_help_is_a_tip_with_a_visible_hint():
    """The long key help opens from the tip; the paste rule stays visible."""
    field = SetupCredentialForm("parishsoft").fields["candidate"]
    assert "stored encrypted" in str(field.tip)
    assert str(field.help_text) == "Paste it exactly. It is never displayed again."


def test_admin_integration_and_schedule_hints():
    """Regular Admin forms shorten their longest help the same way."""
    refresh = IntegrationForm("parishsoft").fields["full_refresh"]
    assert "never overlap" in str(refresh.tip)
    assert "few minutes" in str(refresh.help_text)
    key = InlineCredentialForm("slack").fields["candidate"]
    assert "xoxb-" in str(key.tip)
    assert str(key.help_text) == "Leave blank to keep the current key."
    empty = ScheduleForm(templates=[]).fields["template_version"]
    assert empty.help_text == NO_TEMPLATES and hasattr(empty, "tip")
