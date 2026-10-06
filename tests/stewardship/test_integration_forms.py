"""Integration forms expose only closed public fields and write-only credentials."""

import pytest

from parishkit.stewardship.accounts.integration_forms import (
    CredentialForm,
    IntegrationForm,
)


@pytest.mark.parametrize(
    ("target", "values", "expected"),
    [
        (
            "parishsoft",
            {"organization_id": "123"},
            {
                "organization_id": "123",
                "full_refresh": "daily",
                "full_refresh_times": ["02:00"],
                "nightly_time": "02:00",
                "delta_refresh": "quarter_hour",
            },
        ),
        (
            "google_workspace",
            {"delegated_email": "MAIL@example.org"},
            {"delegated_email": "mail@example.org"},
        ),
        (
            "email",
            {"sender": "MAIL@example.org", "reply_to": "STAFF@example.org"},
            {"sender": "mail@example.org", "reply_to": "staff@example.org"},
        ),
        ("slack", {"channel_id": "C123"}, {"channel_id": "C123"}),
        (
            "backup",
            {
                "target": "https://drive.google.com/drive/u/0/folders/"
                "1AbCdEfGhIjKlMnOpQrStUv?usp=sharing"
            },
            {
                "target": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv"
            },
        ),
    ],
)
def test_public_values_use_exact_yaml_types(target, values, expected):
    """UI normalization does not change the frozen canonical configuration schema."""
    form = IntegrationForm(target, {"base_digest": "a" * 64} | values)
    assert form.is_valid(), form.errors
    assert form.public_settings() == expected


@pytest.mark.parametrize(
    ("target", "values"),
    [
        ("parishsoft", {"organization_id": 0}),
        ("parishsoft", {"organization_id": 2**31}),
        ("slack", {"channel_id": "https://example.org/private"}),
        ("google_workspace", {"delegated_email": "invalid"}),
        ("backup", {"target": "https://docs.google.com/document/d/1AbCdEfGhIjKl"}),
    ],
)
def test_invalid_public_values_cannot_form_patch(target, values):
    """An invalid form cannot be mistaken for a partial settings dictionary."""
    form = IntegrationForm(target, {"base_digest": "a" * 64} | values)
    assert not form.is_valid()
    with pytest.raises(ValueError):
        form.public_settings()


def test_unsupported_target_is_not_an_arbitrary_configuration_editor():
    """OAuth/bootstrap and backup have separate operational owners."""
    with pytest.raises(ValueError):
        IntegrationForm("google_oauth")


def test_candidate_is_write_only_even_when_another_field_is_invalid():
    """Neither a successful binding nor validation error echoes the submitted key."""
    for intent in ("", "signed-intent"):
        form = CredentialForm(
            {"candidate": "SYNTHETIC-PRIVATE-CREDENTIAL", "intent": intent}
        )
        form.is_valid()
        assert "SYNTHETIC-PRIVATE-CREDENTIAL" not in form.as_div()
        assert form.cleaned_data["candidate"] == "SYNTHETIC-PRIVATE-CREDENTIAL"


def test_loaded_organization_is_read_only_and_cannot_change():
    """Once ParishSoft data is loaded, only that organization ID validates."""
    digest = {"base_digest": "a" * 64}
    same = IntegrationForm(
        "parishsoft", digest | {"organization_id": "123"}, loaded_organization=123
    )
    assert same.is_valid(), same.errors
    assert same.fields["organization_id"].widget.attrs["readonly"] is True
    other = IntegrationForm(
        "parishsoft", digest | {"organization_id": "456"}, loaded_organization=123
    )
    assert not other.is_valid()
    assert "Keep 123 here." in other.errors["organization_id"][0]
    before = IntegrationForm("parishsoft", digest | {"organization_id": "456"})
    assert before.is_valid(), before.errors
    assert "readonly" not in before.fields["organization_id"].widget.attrs


def schedule(**values):
    """A ParishSoft settings form with the given schedule fields."""
    return IntegrationForm(
        "parishsoft", {"base_digest": "a" * 64, "organization_id": "123"} | values
    )


@pytest.mark.parametrize("delta", ["quarter_hour", "hourly", "off"])
@pytest.mark.parametrize("times", ["02:00", "02:00, 14:00"])
def test_every_quick_update_choice_is_accepted(delta, times):
    """Full refreshes alone decide data age, so no gap is refused (#510).

    The form used to refuse hourly or no quick updates because an empty
    quick update reset the 30-minute staleness clock (#465); the alarm now
    measures from the scheduled full refreshes, so "Off" and "Hourly" are
    valid with any full-refresh times.
    """
    form = schedule(delta_refresh=delta, full_refresh_times=times)
    assert form.is_valid(), form.errors
    assert form.public_settings()["delta_refresh"] == delta


def test_schedule_fields_describe_the_window_and_stay_compact():
    """The delta help names the lateness margin; the long help becomes a tip."""
    form = IntegrationForm("parishsoft")
    tip = str(form.fields["delta_refresh"].tip)
    assert "more than 30 minutes late" in tip and "including none" in tip
    # The short hint no longer tells Administrators to keep 15 minutes.
    hint = str(form.fields["delta_refresh"].help_text)
    assert "Hourly or Off" in hint and "keep 15" not in hint
    assert str(form.fields["full_refresh_times"].help_text).startswith(
        "For example 2am, 14:00"
    )
    assert "blank means 02:00" in str(form.fields["full_refresh_times"].tip)
    assert form.fields["full_refresh_times"].prepare_value(["02:00", "12:00"]) == (
        "02:00, 12:00"
    )


def test_form_offers_three_frequencies_with_daily_default():
    """An omitted choice keeps the documented once-a-day default."""
    assert schedule().public_settings()["full_refresh"] == "daily"
    hourly = schedule(full_refresh="hourly")
    assert hourly.is_valid() and hourly.public_settings()["full_refresh"] == "hourly"
    assert not schedule(full_refresh="weekly").is_valid()
    assert schedule().public_settings()["delta_refresh"] == "quarter_hour"


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("", ["02:00"]),
        ("   ", ["02:00"]),
        ("03:15", ["03:15"]),
        ("14:00, 02:00 08:00; 14:00", ["02:00", "08:00", "14:00"]),
        ("20:00,02:00", ["02:00", "20:00"]),
        ("23:59 00:00", ["00:00", "23:59"]),
        # Any common form (#631), with a lone suffix kept with its time.
        ("2:00", ["02:00"]),
        ("2pm, 2 am\n0800", ["02:00", "08:00", "14:00"]),
        ("2:30 PM; noon 1430", ["12:00", "14:30"]),
        ("02:00:00", ["02:00"]),
        # Only separators is blank, not an empty list (which would leave no
        # nightly time).
        (",", ["02:00"]),
        (" , ; ", ["02:00"]),
    ],
)
def test_typed_times_are_split_sorted_and_deduplicated(typed, stored):
    """Any separator and form works; the stored list is sorted and unique.

    Blank is 02:00. The earliest listed time is stored as the nightly time as
    well.
    """
    form = schedule(full_refresh_times=typed)
    assert form.is_valid(), form.errors
    values = form.public_settings()
    assert values["full_refresh_times"] == stored
    assert values["nightly_time"] == stored[0]


@pytest.mark.parametrize(
    ("typed", "named"),
    [
        ("2:5", "2:5"),
        ("13pm", "13pm"),
        ("02:00, 25:00", "25:00"),
        ("02:00; 12:60", "12:60"),
        ("02:00:30", "02:00:30"),
    ],
)
def test_malformed_times_are_refused_by_name(typed, named):
    """The error names the entry that cannot be read as a time."""
    form = schedule(full_refresh_times=typed)
    assert not form.is_valid()
    (message,) = form.errors["full_refresh_times"]
    assert message.startswith(f"\u201c{named}\u201d")
    with pytest.raises(ValueError):
        form.public_settings()


def test_more_than_eight_times_are_refused():
    """The list is bounded at eight distinct times; repeats do not count."""
    nine = ", ".join(f"{hour:02d}:00" for hour in range(2, 11))
    form = schedule(full_refresh_times=nine)
    assert not form.is_valid()
    assert form.errors["full_refresh_times"] == ["Enter at most 8 different times."]
    eight = ", ".join(f"{hour:02d}:00" for hour in range(2, 10))
    assert schedule(full_refresh_times=eight + ", 2am").is_valid()


@pytest.mark.parametrize("delta", ["off", "hourly", "quarter_hour"])
def test_separators_only_is_the_default_time_with_any_quick_update(delta):
    """ "," saves 02:00 alone, whatever the quick-update choice (#510)."""
    form = schedule(full_refresh_times=",", delta_refresh=delta)
    assert form.is_valid(), form.errors
    assert form.public_settings()["full_refresh_times"] == ["02:00"]
    assert form.public_settings()["nightly_time"] == "02:00"


def test_refresh_times_render_as_a_parish_time_list_entry():
    """The box is marked for the page script with its blank and its bound."""
    html = str(IntegrationForm("parishsoft")["full_refresh_times"])
    assert 'data-time-entry="list"' in html
    assert 'data-time-blank="02:00"' in html and 'data-time-max="8"' in html
    assert 'data-show-when="full_refresh=daily"' in html
    assert 'value="02:00"' in html
