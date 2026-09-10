"""Strict campaign schema, historical parser preservation, and cross-record rules."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_schema import validator_for
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.campaigns.configuration import (
    campaign_values,
    schedule_values,
    validate_campaign_sections,
)

from .campaign_factory import campaign, financial, schedule
from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address


def document():
    """Independent v3 candidate retaining the required explicit Administrator."""
    value = configuration_document()
    value["sections"]["login_rules"] = [address()]
    row = campaign()
    value["sections"]["campaigns"] = [row]
    value["sections"]["schedules"] = [schedule(row["id"])]
    return value


def test_complete_campaign_round_trip():
    """Module-specific values and version references retain their exact input
    identity.
    """
    values = campaign(
        modules=["census", "financial", "ministry"],
        ministry_duids=[1, 2],
        financial=financial(),
        share_options=[
            {"id": str(uuid4()), "label": "{pronoun} will give", "free_text": False}
        ],
        content_versions={"thank_you": str(uuid4())},
        year_label=None,
    )["values"]
    assert campaign_values(values).start == datetime(2026, 10, 1, 4, tzinfo=UTC)
    value = document()
    value["sections"]["campaigns"][0]["values"] = values
    assert (
        configuration_version(value).document()["sections"]["campaigns"][0]["values"]
        == values
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("name", ""),
        ("name", " secret\n"),
        ("year_label", 2027),
        ("timezone", "Unknown/Private"),
        ("timezone", None),
        ("start_date", "20261001"),
        ("end_date", "2026-10-01"),
        ("end_date", "9999-12-31"),
        ("start_date", None),
        ("end_date", "2026-09-30"),
        ("modules", []),
        ("modules", ["census", "census"]),
        ("modules", ["ministry", "census"]),
        ("modules", ["private"]),
        ("modules", True),
        ("ministry_duids", [1]),
        ("ministry_duids", [True]),
        ("ministry_duids", [1.0]),
        ("ministry_duids", [2, 1]),
        ("ministry_duids", [0]),
        ("ministry_duids", [2**63]),
        ("ministry_duids", "private"),
        ("financial", financial()),
        ("share_options", [{}]),
        ("share_options", {}),
        ("content_versions", []),
        ("content_versions", {"private": str(uuid4())}),
        ("content_versions", {"welcome": "private"}),
        ("additional_information", 1),
    ],
)
def test_invalid_campaign_fields(key, value):
    """Reject malformed or module-inapplicable fields without echoing their values."""
    with pytest.raises(ConfigError) as error:
        campaign_values(campaign(**{key: value})["values"])
    assert "private" not in str(error.value).lower()


@pytest.mark.parametrize(
    "overrides",
    [
        {"end": "2027-12-30"},
        {"comparison_end": "2026-12-30"},
        {"fund_duids": [True]},
        {"comparison_fund_duids": [1, 1]},
        {"overlap_confirmed": 1},
        {"start": "9999-01-01", "end": "9999-12-30"},
        {"start": "2026-01-01", "end": "2026-12-31"},
    ],
)
def test_invalid_financial_periods(overrides):
    """Both periods obey whole-year semantics and overlap requires explicit
    confirmation.
    """
    with pytest.raises(ConfigError):
        campaign_values(
            campaign(modules=["financial"], financial=financial(**overrides))["values"]
        )


def test_overlap_and_leap_anniversary():
    """Confirmed overlap and leap-start financial years use the shared resolver."""
    values = campaign(
        modules=["financial"],
        financial=financial(
            start="2026-01-01",
            end="2026-12-31",
            overlap_confirmed=True,
            comparison_start="2024-02-29",
            comparison_end="2025-02-27",
        ),
    )["values"]
    campaign_values(values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": []},
        {"kind": "private"},
        {"campaign_id": "private"},
        {"template_version": "private"},
        {"subject": ""},
        {"date": "2026-11-01"},
        {"time": "09:00"},
        {"time": "09:00:00+00:00"},
        {"time": "09:00:00.000001"},
        {"time": None},
        {"weekday": True},
        {"kind": "daily_digest"},
        {"kind": "weekly_digest", "date": None, "weekday": True},
        {"kind": "weekly_digest", "date": None, "weekday": 7},
    ],
)
def test_invalid_schedule_fields(overrides):
    """Only canonical local dates/times and type-specific weekday fields are
    accepted.
    """
    row = campaign()
    with pytest.raises(ConfigError):
        schedule_values(schedule(row["id"], **overrides)["values"], row["values"])


@pytest.mark.parametrize(
    "kind,weekday", [("daily_digest", None), ("weekly_digest", 0), ("weekly_digest", 6)]
)
def test_recurring_schedules(kind, weekday):
    """Recurring definitions have no fabricated one-time instant."""
    row = campaign()
    assert (
        schedule_values(
            schedule(row["id"], kind=kind, date=None, weekday=weekday)["values"],
            row["values"],
        )
        is None
    )


@pytest.mark.parametrize(
    "case",
    [
        "owner",
        "name",
        "duplicate_initial",
        "early_reminder",
        "only_reminder",
        "duplicate_digest",
        "same_time",
    ],
)
def test_cross_record_constraints(case):
    """Global identities, chronological reminders and digest cardinality cannot
    drift.
    """
    value = document()
    campaigns, schedules = (
        value["sections"]["campaigns"],
        value["sections"]["schedules"],
    )
    if case == "owner":
        schedules[0]["values"]["campaign_id"] = str(uuid4())
    elif case == "name":
        campaigns.append(campaign(name="ANNUAL CAMPAIGN"))
    elif case == "only_reminder":
        schedules[0]["values"]["kind"] = "reminder"
    elif case == "duplicate_digest":
        schedules.extend(
            [
                schedule(campaigns[0]["id"], kind="daily_digest", date=None)
                for _ in range(2)
            ]
        )
    else:
        schedules.append(
            schedule(
                campaigns[0]["id"],
                kind="initial" if case == "duplicate_initial" else "reminder",
                time="08:00:00" if case == "early_reminder" else "09:00:00",
            )
        )
    with pytest.raises(ConfigError):
        validate_campaign_sections(value)


def test_frozen_schemas_and_patch_semantics():
    """V3 does not silently widen either historical validation or intent parsers."""
    value = document()
    version = configuration_version(value)
    for schema in ("parish-integrations-v1", "foundation-policy-v2"):
        with pytest.raises(ConfigError):
            validator_for(schema)(value)
    row = value["sections"]["campaigns"][0]
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": row["id"],
            "values": {"name": "Updated"},
        }
    ]
    result = build_candidate(version, patch, candidate_id=uuid4())
    assert (
        result.candidate.document()["sections"]["campaigns"][0]["values"]["name"]
        == "Updated"
    )
    for schema in ("parish-integrations-patch-v1", "foundation-policy-patch-v2"):
        with pytest.raises(ConfigError):
            build_candidate(version, patch, candidate_id=uuid4(), request_schema=schema)
    with pytest.raises(ConfigError):
        build_candidate(
            version,
            [
                {"operation": "remove", "section": "campaigns", "id": row["id"]},
                {
                    "operation": "remove",
                    "section": "schedules",
                    "id": value["sections"]["schedules"][0]["id"],
                },
            ],
            candidate_id=uuid4(),
        )


def test_schedule_identity_and_option_uniqueness():
    """Changing a logical schedule's type or duplicating share-option identity fails."""
    value = document()
    row = value["sections"]["schedules"][0]
    patch = [
        {
            "operation": "update",
            "section": "schedules",
            "id": row["id"],
            "values": {"kind": "daily_digest", "date": None},
        }
    ]
    with pytest.raises(ConfigError):
        build_candidate(configuration_version(value), patch, candidate_id=uuid4())
    option = {"id": str(uuid4()), "label": "Give", "free_text": True}
    for options in (
        [option, deepcopy(option)],
        [option | {"free_text": 1}],
        [option] * 101,
    ):
        with pytest.raises(ConfigError):
            campaign_values(
                campaign(modules=["financial"], share_options=options)["values"]
            )


@pytest.mark.parametrize("slot", ["ministry", "financial", "additional"])
def test_disabled_content_slot_is_rejected(slot):
    """Disabled feature content is a stray module-dependent value, not hidden state."""
    with pytest.raises(ConfigError):
        campaign_values(
            campaign(
                additional_information=False, content_versions={slot: str(uuid4())}
            )["values"]
        )


def test_campaign_interval_is_reused_for_all_schedules(monkeypatch):
    """One campaign validation suffices for a configuration with many reminders."""
    from parishkit.stewardship.campaigns import configuration

    value = document()
    identifier = value["sections"]["campaigns"][0]["id"]
    value["sections"]["schedules"].extend(
        schedule(identifier, kind="reminder", date=f"2026-10-{day:02}")
        for day in range(2, 20)
    )
    calls, original = [], configuration.campaign_values

    def tracked(values):
        """Count structural validation without changing its result."""
        calls.append(1)
        return original(values)

    monkeypatch.setattr(configuration, "campaign_values", tracked)
    validate_campaign_sections(value)
    assert len(calls) == 1
