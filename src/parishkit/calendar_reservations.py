"""Implementation for the parishkit-calendar-reservations command."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parishkit.cli import (
    parser_with_common_options,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.google.auth import (
    load_service_account_credentials,
    load_user_credentials,
)
from parishkit.google.calendar import (
    build_calendar_service,
    list_events,
    patch_attendee_response,
)
from parishkit.logging import setup_logging

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


@dataclass(frozen=True)
class ReservationCalendar:
    name: str
    calendar_id: str
    check_conflicts: bool = True


@dataclass(frozen=True)
class ReservationConfig:
    acceptable_domains: frozenset[str]
    calendars: tuple[ReservationCalendar, ...]
    timezone: ZoneInfo
    lookback_days: int = 31
    lookahead_days: int = 547


@dataclass(frozen=True)
class EventDecision:
    event: dict[str, Any]
    response: str
    reason: str | None = None


ServiceFactory = Callable[[ConfigData], Any]


def main(
    argv: Sequence[str] | None = None,
    *,
    service_factory: ServiceFactory | None = None,
    now: Callable[[], dt.datetime] | None = None,
) -> int:
    parser = parser_with_common_options(
        "parishkit-calendar-reservations",
        description="Accept or decline pending Google Calendar room reservations.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"parishkit-calendar-reservations {version('parishkit')}")
        return 0
    return run_user_facing(lambda: _run(args, service_factory, now=now))


def _run(
    args: argparse.Namespace,
    service_factory: ServiceFactory | None,
    *,
    now: Callable[[], dt.datetime] | None,
) -> int:
    common = resolve_common_options(args)
    config = load_yaml_config(common.config)
    reservation_config = calendar_reservation_config(config)
    log = setup_logging(
        verbose=common.verbose,
        debug=common.debug,
        log_file=common.log_file,
        log_dir=common.log_dir,
        logger_name="parishkit.calendar_reservations",
        slack_token_file=common.slack_token_file,
        slack_channel=common.slack_channel,
        slack_level=common.slack_log_level,
    )
    log.info(
        "Configured %s calendar(s), %s acceptable domain(s), and timezone %s",
        len(reservation_config.calendars),
        len(reservation_config.acceptable_domains),
        reservation_config.timezone,
    )
    log.debug("Reservation calendars: %s", reservation_config.calendars)
    log.debug(
        "Reservation window is %s day(s) back and %s day(s) ahead",
        reservation_config.lookback_days,
        reservation_config.lookahead_days,
    )
    log.debug("Dry-run mode is %s", "enabled" if common.dry_run else "disabled")
    service = (
        service_factory(config)
        if service_factory is not None
        else build_calendar_service(load_calendar_credentials(config))
    )
    process_calendars(
        service,
        reservation_config,
        dry_run=common.dry_run,
        log=log,
        now=now,
    )
    return 0


def calendar_reservation_config(config: ConfigData) -> ReservationConfig:
    section = _mapping(config.get("calendar_reservations", {}), "calendar_reservations")
    domains = _string_list(
        section.get("acceptable_domains"),
        "calendar_reservations.acceptable_domains",
    )
    calendars = _calendars(section.get("calendars"))
    timezone_name = section.get("timezone", "UTC")
    if not isinstance(timezone_name, str):
        raise ConfigError("calendar_reservations.timezone must be a string")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"calendar_reservations.timezone is unknown: {timezone_name}"
        ) from exc
    lookback_days = _positive_int(
        section.get("lookback_days", 31),
        "calendar_reservations.lookback_days",
    )
    lookahead_days = _positive_int(
        section.get("lookahead_days", 547),
        "calendar_reservations.lookahead_days",
    )
    return ReservationConfig(
        acceptable_domains=frozenset(domain.casefold() for domain in domains),
        calendars=tuple(calendars),
        timezone=timezone,
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
    )


def load_calendar_credentials(config: ConfigData) -> Any:
    google = _mapping(config.get("google", {}), "google")
    service_account_file = google.get("service_account_file")
    user_token_file = google.get("user_token_file")
    delegated_subject = google.get("delegated_subject")
    if service_account_file and user_token_file:
        raise ConfigError(
            "google configuration must not set both service_account_file "
            "and user_token_file"
        )
    if delegated_subject is not None and not isinstance(delegated_subject, str):
        raise ConfigError("google.delegated_subject must be a string")
    if isinstance(service_account_file, str):
        return load_service_account_credentials(
            Path(service_account_file),
            scopes=[CALENDAR_SCOPE],
            subject=delegated_subject,
        )
    if isinstance(user_token_file, str):
        return load_user_credentials(Path(user_token_file), scopes=[CALENDAR_SCOPE])
    raise ConfigError(
        "google.service_account_file or google.user_token_file is required"
    )


def process_calendars(
    service: Any,
    config: ReservationConfig,
    *,
    dry_run: bool,
    log: logging.Logger,
    now: Callable[[], dt.datetime] | None = None,
) -> None:
    current_time = (now or (lambda: dt.datetime.now(dt.UTC)))()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=dt.UTC)
    time_min = current_time.astimezone(dt.UTC) - dt.timedelta(days=config.lookback_days)
    time_max = current_time.astimezone(dt.UTC) + dt.timedelta(
        days=config.lookahead_days
    )
    log.info("Checking reservations from %s through %s", time_min, time_max)
    for calendar in config.calendars:
        log.info(
            "Downloading events from %s (ID: %s)", calendar.name, calendar.calendar_id
        )
        events = list_events(
            service,
            calendar.calendar_id,
            time_min=time_min.isoformat(),
            time_max=time_max.isoformat(),
        )
        log.info(
            "Downloaded %s event(s) from %s",
            len(events),
            calendar.name,
        )
        log.debug("Calendar %s events: %s", calendar.name, events)
        decisions = reservation_decisions(events, calendar, config)
        log.info(
            "Computed %s decision(s) for %s",
            len(decisions),
            calendar.name,
        )
        log.debug("Calendar %s decisions: %s", calendar.name, decisions)
        respond_to_decisions(
            service,
            calendar,
            decisions,
            dry_run=dry_run,
            log=log,
        )


def reservation_decisions(
    events: Sequence[dict[str, Any]],
    calendar: ReservationCalendar,
    config: ReservationConfig,
) -> list[EventDecision]:
    pending_events: list[dict[str, Any]] = []
    existing_events: list[dict[str, Any]] = []
    decisions: list[EventDecision] = []
    for event in events:
        resource_status = attendee_status(event, calendar.calendar_id)
        if resource_status == "needsAction":
            creator_email = str(event.get("creator", {}).get("email", ""))
            if creator_domain(creator_email) not in config.acceptable_domains:
                decisions.append(
                    EventDecision(
                        event=event,
                        response="declined",
                        reason=(
                            f"creator {creator_email} is not in an acceptable domain"
                        ),
                    )
                )
            else:
                pending_events.append(event)
        elif resource_status != "declined":
            existing_events.append(event)

    if not calendar.check_conflicts:
        decisions.extend(
            EventDecision(event=event, response="accepted") for event in pending_events
        )
        return decisions

    accepted_intervals = [
        (event, event_interval(event, config.timezone)) for event in existing_events
    ]
    for event in sorted(pending_events, key=lambda item: str(item.get("created", ""))):
        interval = event_interval(event, config.timezone)
        conflict = next(
            (
                existing
                for existing, existing_interval in accepted_intervals
                if intervals_overlap(interval, existing_interval)
            ),
            None,
        )
        if conflict is None:
            decisions.append(EventDecision(event=event, response="accepted"))
            accepted_intervals.append((event, interval))
        else:
            decisions.append(
                EventDecision(
                    event=event,
                    response="declined",
                    reason=(
                        "conflicts with existing event "
                        f"'{conflict.get('summary', '')}' (ID: {conflict.get('id')})"
                    ),
                )
            )
    return decisions


def respond_to_decisions(
    service: Any,
    calendar: ReservationCalendar,
    decisions: Sequence[EventDecision],
    *,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    for decision in decisions:
        event = decision.event
        event_id = str(event.get("id", ""))
        summary = event.get("summary")
        if not summary:
            log.warning(
                "Event %s does not have a title; refusing to respond",
                event_id,
            )
            continue
        because = f" because {decision.reason}" if decision.reason else ""
        log.info(
            "Event '%s' (ID: %s) will be %s%s",
            summary,
            event_id,
            decision.response,
            because,
        )
        if dry_run:
            log.info(
                "dry-run: would have %s event %s %s",
                decision.response,
                summary,
                event_id,
            )
            continue
        patch_attendee_response(
            service,
            calendar.calendar_id,
            event_id,
            decision.response,
        )


def attendee_status(event: Mapping[str, Any], calendar_id: str) -> str | None:
    for attendee in event.get("attendees", []):
        if attendee.get("email") == calendar_id:
            return attendee.get("responseStatus")
    return None


def creator_domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].casefold()


def event_interval(
    event: Mapping[str, Any],
    timezone: dt.tzinfo,
) -> tuple[dt.datetime, dt.datetime]:
    one_second = dt.timedelta(seconds=1)
    return (
        event_time(event["start"], timezone) + one_second,
        event_time(event["end"], timezone) - one_second,
    )


def event_time(value: Mapping[str, str], timezone: dt.tzinfo) -> dt.datetime:
    if "dateTime" in value:
        parsed = dt.datetime.fromisoformat(value["dateTime"])
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone)
    parsed_date = dt.date.fromisoformat(value["date"])
    return dt.datetime.combine(parsed_date, dt.time(), tzinfo=timezone)


def intervals_overlap(
    left: tuple[dt.datetime, dt.datetime],
    right: tuple[dt.datetime, dt.datetime],
) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    if not value:
        raise ConfigError(f"{name} must not be empty")
    return value


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _calendars(value: Any) -> list[ReservationCalendar]:
    if not isinstance(value, list) or not value:
        raise ConfigError("calendar_reservations.calendars must be a non-empty list")
    calendars = []
    for index, raw_calendar in enumerate(value):
        name = f"calendar_reservations.calendars[{index}]"
        item = _mapping(raw_calendar, name)
        calendar_name = item.get("name")
        calendar_id = item.get("calendar_id", item.get("id"))
        check_conflicts = item.get("check_conflicts", True)
        if not isinstance(calendar_name, str) or not calendar_name:
            raise ConfigError(f"{name}.name must be a string")
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ConfigError(f"{name}.calendar_id must be a string")
        if not isinstance(check_conflicts, bool):
            raise ConfigError(f"{name}.check_conflicts must be a boolean")
        calendars.append(
            ReservationCalendar(
                name=calendar_name,
                calendar_id=calendar_id,
                check_conflicts=check_conflicts,
            )
        )
    return calendars
