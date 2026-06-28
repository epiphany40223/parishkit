from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from parishkit.calendar_reservations import (
    ReservationCalendar,
    calendar_reservation_config,
    reservation_decisions,
)
from parishkit.calendar_reservations import (
    main as calendar_reservations_main,
)
from parishkit.config import ConfigError


class Request:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class Events:
    def __init__(self, pages):
        self.pages = list(pages)
        self.list_calls = []
        self.patch_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return Request(self.pages.pop(0))

    def patch(self, **kwargs):
        self.patch_calls.append(kwargs)
        return Request({})


class Service:
    def __init__(self, pages):
        self._events = Events(pages)

    def events(self):
        return self._events


def write_config(tmp_path: Path, *, dry_run: bool = False) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
common:
  dry_run: {str(dry_run).lower()}
google:
  user_token_file: {tmp_path / "google-user-token.json"}
calendar_reservations:
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
    config = calendar_reservation_config(
        {
            "calendar_reservations": {
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


def test_calendar_reservations_config_rejects_missing_domains():
    with pytest.raises(ConfigError, match="acceptable_domains"):
        calendar_reservation_config(
            {
                "calendar_reservations": {
                    "calendars": [{"name": "Room", "calendar_id": "room@example.org"}],
                }
            }
        )


def test_reservation_decisions_reject_domains_and_conflicts(tmp_path):
    config = calendar_reservation_config(
        {
            "calendar_reservations": {
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
    config = calendar_reservation_config(
        {
            "calendar_reservations": {
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
    assert service._events.list_calls[0]["timeMin"].startswith("2025-12-01")
    assert service._events.patch_calls == [
        {
            "calendarId": "room@example.org",
            "sendUpdates": "all",
            "eventId": "two",
            "body": {
                "attendees": [
                    {
                        "email": "room@example.org",
                        "responseStatus": "declined",
                    }
                ]
            },
        },
        {
            "calendarId": "room@example.org",
            "sendUpdates": "all",
            "eventId": "one",
            "body": {
                "attendees": [
                    {
                        "email": "room@example.org",
                        "responseStatus": "accepted",
                    }
                ]
            },
        },
    ]


def test_calendar_reservations_dry_run_does_not_patch(tmp_path):
    service = Service([{"items": [event("one")]}])

    assert (
        calendar_reservations_main(
            ["--config", str(write_config(tmp_path, dry_run=True))],
            service_factory=lambda _config: service,
        )
        == 0
    )

    assert service._events.patch_calls == []
