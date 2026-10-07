"""Nightly source time is public versioned policy, never authentication scope."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validator_for,
)
from parishkit.stewardship.accounts.integration_selection import (
    authentication_scope,
    integration_records,
)
from parishkit.stewardship.accounts.request_patch import (
    build_candidate,
    credential_request_schema,
    default_schema,
)
from parishkit.stewardship.accounts.source_cadence_schema import (
    CREDENTIAL_SCHEMA,
    RECOVERY_SCHEMA,
    REQUEST_SCHEMA,
    SCHEMA,
)

from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address


def base_version():
    """Start with an actual earlier policy schema, before nightly settings exist."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    return configuration_version(document)


def cadence_patch(base, value):
    """Replace complete public settings while preserving organization and secrets."""
    record = base.document()["sections"]["integrations"][0]
    return [
        {
            "section": "integrations",
            "operation": "update",
            "id": record["id"],
            "values": {"settings": {"organization_id": "12345", "nightly_time": value}},
        }
    ]


@pytest.mark.parametrize("value", ["00:00", "02:00", "12:30", "23:59"])
def test_nightly_time_upgrade_preserves_base_and_credential_scope(value):
    """The new parser is explicit; old retained parsers continue rejecting it."""
    base = base_version()
    original = deepcopy(base.document())
    patch = cadence_patch(base, value)
    assert default_schema(base, patch) == REQUEST_SCHEMA
    candidate = build_candidate(base, patch, candidate_id=uuid4()).candidate
    assert base.document() == original
    assert schema_for(candidate.document()) == SCHEMA
    assert authentication_scope(
        "parishsoft", integration_records(candidate.document())
    ) == {
        "organization_id": 12345,
    }
    for old in ("foundation-policy-v2", "campaign-content-v5"):
        with pytest.raises(ConfigError):
            validator_for(old)(candidate.document())


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        2,
        {},
        "",
        "2:00",
        "24:00",
        "12:60",
        "02:00:00",
        " 02:00",
        "０２:００",
        "02:00Z",
    ],
)
def test_closed_local_time_shape_fails_before_persistence(value):
    """No timezone suffix, seconds, coercion or private detail enters the schema."""
    base = base_version()
    with pytest.raises(ConfigError):
        build_candidate(base, cadence_patch(base, value), candidate_id=uuid4())


def test_new_discriminators_retain_fingerprint_and_recovery_boundaries():
    """Cadence does not make general patches able to select credentials or grants."""
    base = base_version()
    base = build_candidate(
        base, cadence_patch(base, "03:15"), candidate_id=uuid4()
    ).candidate
    patch = cadence_patch(base, "04:00")
    patch[0]["values"] = {"credential_fingerprint": "b" * 64}
    assert credential_request_schema(base.document()) == CREDENTIAL_SCHEMA
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())
    changed = build_candidate(
        base, patch, candidate_id=uuid4(), request_schema=CREDENTIAL_SCHEMA
    )
    assert (
        changed.candidate.document()["sections"]["integrations"][0]["values"][
            "settings"
        ]["nightly_time"]
        == "03:15"
    )
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            patch,
            candidate_id=uuid4(),
            request_schema="integration-credential-patch-v6",
        )
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            cadence_patch(base, "04:00"),
            candidate_id=uuid4(),
            request_schema=RECOVERY_SCHEMA,
        )
    rule = address("recovered@example.org")
    rule["values"]["grants"]["administrator"]["manual"] = rule["values"][
        "creation_operation"
    ]
    recovery = [{"section": "login_rules", "operation": "add", **rule}]
    restored = build_candidate(
        base, recovery, candidate_id=uuid4(), request_schema=RECOVERY_SCHEMA
    )
    assert schema_for(restored.candidate.document()) == SCHEMA


@pytest.mark.parametrize("frequency", ["daily", "hourly", "quarter_hour"])
def test_full_refresh_frequency_is_public_cadence_not_credential_scope(frequency):
    """A frequency alone selects the cadence schema and is never key scope."""
    base = base_version()
    patch = cadence_patch(base, "02:00")
    patch[0]["values"]["settings"] = {
        "organization_id": "12345",
        "full_refresh": frequency,
    }
    assert default_schema(base, patch) == REQUEST_SCHEMA
    candidate = build_candidate(base, patch, candidate_id=uuid4()).candidate
    assert schema_for(candidate.document()) == SCHEMA
    assert authentication_scope(
        "parishsoft", integration_records(candidate.document())
    ) == {"organization_id": 12345}


@pytest.mark.parametrize("frequency", ["", "weekly", "Hourly", 15, None])
def test_unknown_full_refresh_frequency_fails_before_persistence(frequency):
    """Only the three offered frequencies can be stored."""
    base = base_version()
    patch = cadence_patch(base, "02:00")
    patch[0]["values"]["settings"]["full_refresh"] = frequency
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())


def schedule_patch(base, **settings):
    """A complete ParishSoft settings replacement with the given schedule keys."""
    patch = cadence_patch(base, "02:00")
    patch[0]["values"]["settings"] = {"organization_id": "12345"} | settings
    return patch


@pytest.mark.parametrize(
    "settings",
    [
        {"full_refresh_times": ["02:00"]},
        {"nightly_time": "02:00", "full_refresh_times": ["02:00", "12:00", "17:30"]},
        {"nightly_time": "08:00", "full_refresh_times": ["08:00", "20:00"]},
        {"full_refresh_times": [f"{hour:02d}:00" for hour in range(2, 10)]},
        {"delta_refresh": "quarter_hour"},
        {"delta_refresh": "hourly"},
        {"delta_refresh": "off", "full_refresh_times": ["02:00", "14:00"]},
    ],
)
def test_full_refresh_times_and_delta_cadence_are_public_cadence(settings):
    """The new optional settings select the cadence schema and are never key scope."""
    base = base_version()
    patch = schedule_patch(base, **settings)
    assert default_schema(base, patch) == REQUEST_SCHEMA
    candidate = build_candidate(base, patch, candidate_id=uuid4()).candidate
    assert schema_for(candidate.document()) == SCHEMA
    stored = candidate.document()["sections"]["integrations"][0]["values"]["settings"]
    assert stored == {"organization_id": "12345"} | settings
    assert authentication_scope(
        "parishsoft", integration_records(candidate.document())
    ) == {"organization_id": 12345}


@pytest.mark.parametrize(
    "settings",
    [
        {"full_refresh_times": []},
        {"full_refresh_times": "02:00"},
        {"full_refresh_times": ["12:00", "02:00"]},
        {"full_refresh_times": ["02:00", "02:00"]},
        {"full_refresh_times": ["02:00", "2:30"]},
        {"full_refresh_times": ["02:00", 1230]},
        # More than eight times needs a schedule saved with its rules (#632).
        {"full_refresh_times": [f"{hour:02d}:00" for hour in range(2, 11)]},
        # The nightly time (02:00 by default) must be the earliest listed time.
        {"full_refresh_times": ["03:00"]},
        {"nightly_time": "04:00", "full_refresh_times": ["02:00", "12:00"]},
        {"nightly_time": "12:00", "full_refresh_times": ["02:00", "12:00"]},
        {"full_refresh_times": ["01:00", "02:00"]},
        {"delta_refresh": "weekly"},
        {"delta_refresh": ""},
        {"delta_refresh": None},
        {"delta_refresh": "Hourly"},
    ],
)
def test_malformed_time_lists_and_delta_cadences_fail_before_persistence(settings):
    """Only sorted unique canonical lists naming the nightly time are stored."""
    base = base_version()
    with pytest.raises(ConfigError):
        build_candidate(base, schedule_patch(base, **settings), candidate_id=uuid4())


def rules_schedule(**overrides):
    """Settings of a schedule saved with its rules (#632), with overrides."""
    from parishkit.stewardship.source.refresh_rules import stored_settings

    document = {
        "rules": [
            {"kind": "full", "at": "02:00"},
            {"kind": "full", "every": 60, "from": "08:00", "to": "20:00"},
            {"kind": "quick", "every": 15, "from": "06:00", "to": "07:45"},
        ],
        "skips": [{"from": "12:00", "to": "13:00"}],
        "skip_around_family_emails": False,
    }
    return stored_settings(document) | overrides


def test_a_schedule_saved_with_its_rules_is_public_cadence():
    """More than eight full times and listed quick times are stored with the rules."""
    settings = rules_schedule()
    assert len(settings["full_refresh_times"]) == 13
    base = base_version()
    patch = schedule_patch(base, **settings)
    assert default_schema(base, patch) == REQUEST_SCHEMA
    candidate = build_candidate(base, patch, candidate_id=uuid4()).candidate
    assert schema_for(candidate.document()) == SCHEMA
    stored = candidate.document()["sections"]["integrations"][0]["values"]["settings"]
    assert stored == {"organization_id": "12345"} | settings
    # Refresh timing is never part of the key's scope.
    assert authentication_scope(
        "parishsoft", integration_records(candidate.document())
    ) == {"organization_id": 12345}


@pytest.mark.parametrize(
    "settings",
    [
        # Listed quick times exist only beside their rules.
        {"delta_refresh": "times", "quick_refresh_times": ["06:00"]},
        {"quick_refresh_times": ["06:00"]},
        # Until the scheduler can skip around Family emails, it is refused.
        rules_schedule(
            refresh_rules=rules_schedule()["refresh_rules"]
            | {"skip_around_family_emails": True}
        ),
        rules_schedule(refresh_rules={"rules": []}),
        rules_schedule(full_refresh="hourly"),
        rules_schedule(delta_refresh="quarter_hour"),
        rules_schedule(delta_refresh="off"),
        rules_schedule(quick_refresh_times=["02:00", "06:00"]),
        rules_schedule(quick_refresh_times=["06:00", "06:05"]),
        rules_schedule(quick_refresh_times=[]),
        # Two times less than 15 minutes apart, around midnight too.
        rules_schedule(nightly_time="00:00", full_refresh_times=["00:00", "23:50"]),
    ],
)
def test_malformed_rule_schedules_fail_before_persistence(settings):
    """The v8 schema checks the new keys' shape and spacing."""
    base = base_version()
    with pytest.raises(ConfigError):
        build_candidate(base, schedule_patch(base, **settings), candidate_id=uuid4())
