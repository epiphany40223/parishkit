from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.pk_validate_gcalendar_reservations import (
    ReservationCalendar,
    calendar_reservation_config,
    load_calendar_credentials,
    reservation_decisions,
)
from parishkit.pk_validate_gcalendar_reservations import (
    main as calendar_reservations_main,
)


class Request:
    """Fake Google API request whose execute() returns a canned response."""

    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class Events:
    """Fake Calendar events resource recording list/patch calls.

    ``pages`` is consumed one entry per list() call to simulate pagination, and
    every list and patch invocation is recorded for later assertions.
    """

    def __init__(self, pages):
        """Initialize the instance."""
        self.pages = list(pages)
        self.list_calls = []
        self.patch_calls = []

    def list(self, **kwargs):
        """Record the list args and return the next pre-canned page."""
        self.list_calls.append(kwargs)
        return Request(self.pages.pop(0))

    def patch(self, **kwargs):
        """Record the patch args and return an empty response."""
        self.patch_calls.append(kwargs)
        return Request({})


class Service:
    """Fake Calendar service exposing the recording Events resource."""

    def __init__(self, pages):
        self._events = Events(pages)

    def events(self):
        return self._events


def write_config(tmp_path: Path, *, dry_run: bool = False) -> Path:
    """Write a calendars config YAML and return its path."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
common:
  dry_run: {str(dry_run).lower()}
google:
  user_token_file: {tmp_path / "google-user-token.json"}
calendars:
  timezone: America/New_York
  acceptable_domains:
    - example.org
  calendars:
    - name: Room
      calendar_id: room@example.org
      check_conflicts: true
""",
        encoding="utf-8",
    )
    return config


def event(
    event_id,
    *,
    status="needsAction",
    creator="staff@example.org",
    created="2026-01-01T00:00:00Z",
    start="2026-02-01T10:00:00-05:00",
    end="2026-02-01T11:00:00-05:00",
    summary=None,
):
    """Build a Calendar event dict with sensible defaults for each field."""
    return {
        "id": event_id,
        "summary": summary or event_id,
        "created": created,
        "creator": {"email": creator},
        "attendees": [
            {"email": "room@example.org", "responseStatus": status},
        ],
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


def test_calendar_reservations_config_validation(tmp_path):
    """Config parsing lowercases acceptable_domains and builds calendar entries."""
    config = calendar_reservation_config(
        {
            "calendars": {
                "timezone": "America/New_York",
                "acceptable_domains": ["Example.ORG"],
                "calendars": [
                    {
                        "name": "Room",
                        "calendar_id": "room@example.org",
                        "check_conflicts": False,
                    }
                ],
            }
        }
    )

    assert config.acceptable_domains == frozenset({"example.org"})
    assert config.calendars == (
        ReservationCalendar(
            name="Room",
            calendar_id="room@example.org",
            check_conflicts=False,
        ),
    )
    assert str(config.timezone) == "America/New_York"


def test_calendar_reservations_inherits_common_timezone_default():
    """calendars.timezone falls back to the caller's common default."""
    config = calendar_reservation_config(
        {
            "calendars": {
                "acceptable_domains": ["example.org"],
                "calendars": [{"name": "Room", "calendar_id": "room@example.org"}],
            }
        },
        default_timezone="America/Kentucky/Louisville",
    )

    assert str(config.timezone) == "America/Kentucky/Louisville"


def test_calendar_reservations_config_rejects_missing_domains():
    """Config without acceptable_domains raises ConfigError."""
    with pytest.raises(ConfigError, match="acceptable_domains"):
        calendar_reservation_config(
            {
                "calendars": {
                    "calendars": [{"name": "Room", "calendar_id": "room@example.org"}],
                }
            }
        )


def test_calendar_reservations_config_describes_bad_calendar_id():
    """A malformed calendar_id error names the entry and bad value type."""
    with pytest.raises(ConfigError) as exc_info:
        calendar_reservation_config(
            {
                "calendars": {
                    "acceptable_domains": ["example.org"],
                    "calendars": [
                        {
                            "name": "Room",
                            "calendar_id": 12345,
                        }
                    ],
                }
            }
        )

    message = str(exc_info.value)
    assert "calendars.calendars[0] ('Room').calendar_id" in message
    assert "non-empty string" in message
    assert "int 12345" in message
    assert "indented under the same '- name:' item" in message


def test_reservation_decisions_reject_domains_and_conflicts(tmp_path):
    """reservation_decisions declines out-of-domain creators and time conflicts,
    accepts otherwise, and skips events already accepted.

    The first event is pre-accepted to act as the existing booking the others
    are checked against. The setup stays local to this test so fixtures remain
    easy to understand and change.
    """
    config = calendar_reservation_config(
        {
            "calendars": {
                "acceptable_domains": ["example.org"],
                "calendars": [{"name": "Room", "calendar_id": "room@example.org"}],
            }
        }
    )
    calendar = config.calendars[0]
    decisions = reservation_decisions(
        [
            event("existing", status="accepted", start="2026-02-01T09:30:00-05:00"),
            event(
                "bad-domain",
                creator="visitor@outside.test",
                start="2026-02-01T08:00:00-05:00",
                end="2026-02-01T09:00:00-05:00",
            ),
            event("conflict", created="2026-01-02T00:00:00Z"),
            event(
                "open",
                created="2026-01-03T00:00:00Z",
                start="2026-02-01T12:00:00-05:00",
                end="2026-02-01T13:00:00-05:00",
            ),
        ],
        calendar,
        config,
    )

    assert [(item.event["id"], item.response) for item in decisions] == [
        ("bad-domain", "declined"),
        ("conflict", "declined"),
        ("open", "accepted"),
    ]
    assert "not in an acceptable domain" in (decisions[0].reason or "")
    assert "conflicts with existing event" in (decisions[1].reason or "")


def test_reservation_decisions_accepts_non_conflict_calendar_without_checking():
    """When check_conflicts is off, only pending events are decided and no
    conflict lookup against accepted events is performed."""
    config = calendar_reservation_config(
        {
            "calendars": {
                "acceptable_domains": ["example.org"],
                "calendars": [
                    {
                        "name": "Main",
                        "calendar_id": "room@example.org",
                        "check_conflicts": False,
                    }
                ],
            }
        }
    )

    decisions = reservation_decisions(
        [event("existing", status="accepted"), event("pending")],
        config.calendars[0],
        config,
    )

    assert [(item.event["id"], item.response) for item in decisions] == [
        ("pending", "accepted")
    ]


def test_calendar_reservations_main_lists_and_patches_events(tmp_path):
    """main paginates through events, then patches each calendar's responses.

    Two list pages exercise nextPageToken handling. The expected patch order is
    declined-before-accepted, matching how main groups decisions. The setup
    stays local to this test so fixtures remain easy to understand and change.
    """
    service = Service(
        [
            {
                "items": [event("one")],
                "nextPageToken": "next",
            },
            {
                "items": [
                    event(
                        "two",
                        creator="visitor@outside.test",
                        start="2026-02-02T12:00:00-05:00",
                    )
                ],
            },
        ]
    )

    assert (
        calendar_reservations_main(
            ["--config", str(write_config(tmp_path))],
            service_factory=lambda _config: service,
            now=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )
        == 0
    )

    assert len(service._events.list_calls) == 2
    assert service._events.list_calls[0]["calendarId"] == "room@example.org"
    # timeMin is a one-month lookback window from the injected "now".
    assert service._events.list_calls[0]["timeMin"].startswith("2025-12-01")
    assert service._events.patch_calls == [
        {
            "calendarId": "room@example.org",
            "sendUpdates": "all",
            "eventId": "two",
            "body": {
                "attendeesOmitted": True,
                "attendees": [
                    {
                        "email": "room@example.org",
                        "responseStatus": "declined",
                    }
                ],
            },
        },
        {
            "calendarId": "room@example.org",
            "sendUpdates": "all",
            "eventId": "one",
            "body": {
                "attendeesOmitted": True,
                "attendees": [
                    {
                        "email": "room@example.org",
                        "responseStatus": "accepted",
                    }
                ],
            },
        },
    ]


def test_calendar_reservations_patches_event_without_summary(tmp_path):
    """A pending event missing a summary is still patched by event ID."""
    untitled = event("one")
    del untitled["summary"]
    service = Service([{"items": [untitled]}])

    assert (
        calendar_reservations_main(
            ["--config", str(write_config(tmp_path))],
            service_factory=lambda _config: service,
            now=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )
        == 0
    )

    assert service._events.patch_calls[0]["eventId"] == "one"


def test_calendar_reservations_dry_run_does_not_patch(tmp_path):
    """In dry-run mode main computes decisions but issues no patch calls."""
    service = Service([{"items": [event("one")]}])

    assert (
        calendar_reservations_main(
            ["--config", str(write_config(tmp_path, dry_run=True))],
            service_factory=lambda _config: service,
        )
        == 0
    )

    assert service._events.patch_calls == []


def test_calendar_reservations_main_logs_config_validation_error(tmp_path, capsys):
    """Tool-specific config validation failures are logged at ERROR."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
common:
  dry_run: true
calendars:
  acceptable_domains:
    - example.org
  calendars:
    - name: Room
      calendar_id: 12345
""",
        encoding="utf-8",
    )

    assert (
        calendar_reservations_main(
            ["--config", str(config)],
            service_factory=lambda _config: Service([]),
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_validate_gcalendar_reservations" in error
    assert "Configuration validation failed" in error
    assert "calendars.calendars[0] ('Room').calendar_id" in error


def test_calendar_config_rejects_boolean_day_window():
    """YAML booleans are not accepted as positive integer day counts."""
    with pytest.raises(ConfigError, match="lookback_days"):
        calendar_reservation_config(
            {
                "calendars": {
                    "acceptable_domains": ["example.org"],
                    "lookback_days": True,
                    "calendars": [
                        {
                            "name": "Room",
                            "calendar_id": "room@example.org",
                        }
                    ],
                }
            }
        )


def test_calendar_credentials_resolve_relative_paths(tmp_path, monkeypatch):
    """Relative Google credential paths resolve against the config directory."""
    calls = []

    def fake_load(path, *, scopes):
        """Capture the resolved token path instead of parsing credentials."""
        calls.append((path, scopes))
        return object()

    monkeypatch.setattr(
        "parishkit.pk_validate_gcalendar_reservations.load_user_credentials",
        fake_load,
    )

    load_calendar_credentials(
        {"google": {"user_token_file": "credentials/google-token.json"}},
        base_dir=tmp_path,
    )

    assert calls[0][0] == tmp_path / "credentials" / "google-token.json"
