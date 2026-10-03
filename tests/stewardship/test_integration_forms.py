"""Integration forms expose only closed public fields and write-only credentials."""

import pytest

from parishkit.stewardship.accounts.integration_forms import (
    CredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy


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


def test_schedule_gaps_longer_than_the_freshness_window_are_refused(settings):
    """Hourly or no quick updates need a staleness window at least as long (#465).

    The default window is 30 minutes, so only quarter-hour quick updates fit
    it; a 60-minute window admits hourly updates, and a day-long window admits
    no quick updates with two full refreshes a day. The refusal names both
    the gap and the window.
    """
    assert schedule(delta_refresh="quarter_hour").is_valid()
    hourly = schedule(delta_refresh="hourly")
    assert not hourly.is_valid()
    (message,) = hourly.errors["delta_refresh"]
    assert "60 minutes between refreshes" in message
    assert "30-minute freshness window" in message
    # Every quarter hour full refresh covers the gap whatever the deltas.
    assert schedule(full_refresh="quarter_hour", delta_refresh="off").is_valid()
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=3600)
    assert schedule(delta_refresh="hourly").is_valid()
    twice = {"delta_refresh": "off", "full_refresh_times": "02:00, 14:00"}
    assert not schedule(**twice).is_valid()
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=86400)
    assert schedule(**twice).is_valid()


def test_schedule_fields_describe_the_window_and_stay_compact():
    """The delta help names the server's window; the long help becomes a tip."""
    form = IntegrationForm("parishsoft")
    assert "30 minutes" in str(form.fields["delta_refresh"].tip)
    assert str(form.fields["full_refresh_times"].help_text).startswith("HH:MM")
    assert "blank means 02:00" in str(form.fields["full_refresh_times"].tip)
    assert form.fields["full_refresh_times"].prepare_value(["02:00", "12:00"]) == (
        "02:00, 12:00"
    )


def test_staleness_refusal_offers_more_times_only_when_they_could_help(settings):
    """The fix list quotes the option as shown and suggests times only if they fit."""
    message = schedule(delta_refresh="hourly").errors["delta_refresh"][0]
    assert "\u201cEvery 15 minutes (recommended)\u201d, or ask" in message
    assert "add full refresh times" not in message
    # Eight full refreshes a day fit a three-hour window: more times would help.
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=10800)
    off = schedule(delta_refresh="off", full_refresh_times="02:00, 14:00")
    assert "add full refresh times" in off.errors["delta_refresh"][0]
    # They would not with an hourly delta, whose gap is fixed.
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=1800)
    assert (
        "add full refresh times"
        not in (schedule(delta_refresh="hourly").errors["delta_refresh"][0])
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
    ],
)
def test_typed_times_are_split_sorted_and_deduplicated(typed, stored):
    """Any separator works; the stored list is sorted and unique; blank is 02:00.

    The earliest listed time is stored as the nightly time as well.
    """
    form = schedule(full_refresh_times=typed)
    assert form.is_valid(), form.errors
    values = form.public_settings()
    assert values["full_refresh_times"] == stored
    assert values["nightly_time"] == stored[0]


@pytest.mark.parametrize(
    ("typed", "named"),
    [
        ("2:00", "2:00"),
        ("2pm", "2pm"),
        ("02:00, 25:00", "25:00"),
        ("02:00; 12:60", "12:60"),
        ("02:00:00", "02:00:00"),
    ],
)
def test_malformed_times_are_refused_by_name(typed, named):
    """The error names the entry that is not a 24-hour HH:MM time."""
    form = schedule(full_refresh_times=typed)
    assert not form.is_valid()
    (message,) = form.errors["full_refresh_times"]
    assert message.startswith(f"{named} is not a 24-hour HH:MM time")
    with pytest.raises(ValueError):
        form.public_settings()


def test_more_than_eight_times_are_refused():
    """The list is bounded at eight distinct times; repeats do not count."""
    nine = ", ".join(f"{hour:02d}:00" for hour in range(2, 11))
    form = schedule(full_refresh_times=nine)
    assert not form.is_valid()
    assert form.errors["full_refresh_times"] == ["Enter at most 8 times."]
    eight = ", ".join(f"{hour:02d}:00" for hour in range(2, 10))
    assert schedule(full_refresh_times=eight + ", 02:00").is_valid()
