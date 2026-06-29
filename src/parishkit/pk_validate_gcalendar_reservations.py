"""Implementation for the pk-validate-gcalendar-reservations command."""

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
    require_explicit_write_mode,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config, resolve_path
from parishkit.google.auth import (
    load_service_account_credentials,
    load_user_credentials,
)
from parishkit.google.calendar import (
    build_calendar_service,
    list_events,
    patch_attendee_response,
)
from parishkit.logging import log_extra, setup_logging

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


@dataclass(frozen=True)
class ReservationCalendar:
    """A single bookable resource calendar to validate.

    ``calendar_id`` is the resource's email-style identifier, which also
    appears as an attendee on each reservation. ``check_conflicts`` enables
    declining reservations that overlap already-accepted bookings.
    """

    name: str
    calendar_id: str
    check_conflicts: bool = True


@dataclass(frozen=True)
class ReservationConfig:
    """Resolved settings controlling how reservations are validated.

    ``acceptable_domains`` holds casefolded creator domains allowed to book.
    ``lookback_days`` and ``lookahead_days`` bound the event window scanned
    around the current time.
    """

    acceptable_domains: frozenset[str]
    calendars: tuple[ReservationCalendar, ...]
    timezone: ZoneInfo
    lookback_days: int = 31
    lookahead_days: int = 547


@dataclass(frozen=True)
class EventDecision:
    """A pending response computed for one event before any API write.

    ``response`` is the attendee response to apply (for example ``accepted``
    or ``declined``); ``reason`` records a human-readable explanation when a
    reservation is declined.
    """

    event: dict[str, Any]
    response: str
    reason: str | None = None
    attendee_email: str | None = None


@dataclass(frozen=True)
class CalendarDecisionPlan:
    """All decisions for one calendar, computed before writes are applied."""

    calendar: ReservationCalendar
    decisions: tuple[EventDecision, ...]


ServiceFactory = Callable[[ConfigData], Any]


def _text_list(values: Sequence[str]) -> str:
    """Render a short list of strings for human-readable log messages."""
    return ", ".join(values) if values else "none"


def _calendar_summary(calendars: Sequence[ReservationCalendar]) -> str:
    """Return readable calendar names and IDs for log messages."""
    return _text_list(
        [f"{calendar.name} (ID: {calendar.calendar_id})" for calendar in calendars]
    )


def _event_summary(event: Mapping[str, Any]) -> str:
    """Return a compact event label for human-readable log messages."""
    summary = str(event.get("summary") or "untitled")
    event_id = str(event.get("id") or "unknown")
    return f"{summary} (ID: {event_id})"


def _events_summary(events: Sequence[Mapping[str, Any]]) -> str:
    """Return readable event labels for log messages."""
    return _text_list([_event_summary(event) for event in events])


def _decisions_summary(decisions: Sequence[EventDecision]) -> str:
    """Return readable decision labels for log messages."""
    return _text_list(
        [
            f"{decision.response} {_event_summary(decision.event)}"
            for decision in decisions
        ]
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    service_factory: ServiceFactory | None = None,
    now: Callable[[], dt.datetime] | None = None,
) -> int:
    """Parse arguments and run the reservation validator.

    ``service_factory`` and ``now`` are injection points used by tests to
    supply a fake Calendar service and a fixed clock; production runs leave
    them unset. Returns a process exit code.
    """
    parser = parser_with_common_options(
        "pk-validate-gcalendar-reservations",
        description="Accept or decline pending Google Calendar room reservations.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-validate-gcalendar-reservations {version('parishkit')}")
        return 0
    # Wrap the real work so expected operational errors become a clean,
    # non-traceback exit code and message.
    return run_user_facing(lambda: _run(args, service_factory, now=now))


def _run(
    args: argparse.Namespace,
    service_factory: ServiceFactory | None,
    *,
    now: Callable[[], dt.datetime] | None,
) -> int:
    """Wire up config, logging, and the Calendar service, then process events.

    Resolves common options, loads and validates the reservation config, sets
    up logging (including optional Slack notifications), and obtains a Calendar
    service either from the injected ``service_factory`` (tests) or from real
    credentials. Always returns 0; failures surface as raised exceptions that
    the caller's ``run_user_facing`` wrapper converts to an exit code.
    """
    common = resolve_common_options(args)
    log = setup_logging(
        verbose=common.verbose or common.dry_run,
        debug=common.debug,
        log_file=common.log_file,
        log_dir=common.log_dir,
        logger_name="parishkit.pk_validate_gcalendar_reservations",
        slack_token_file=common.slack_token_file,
        slack_channel=common.slack_channel,
        slack_level=common.slack_log_level,
    )
    try:
        require_explicit_write_mode(common, "pk-validate-gcalendar-reservations")
        config = load_yaml_config(common.config)
        reservation_config = calendar_reservation_config(
            config,
            default_timezone=common.timezone,
        )
        service = (
            service_factory(config)
            if service_factory is not None
            else build_calendar_service(
                load_calendar_credentials(
                    config,
                    base_dir=common.config.parent if common.config else None,
                )
            )
        )
    except ConfigError as exc:
        log.error("Configuration validation failed: %s", exc)
        raise
    log.info(
        "Configured %s calendar(s), %s acceptable domain(s), and timezone %s",
        len(reservation_config.calendars),
        len(reservation_config.acceptable_domains),
        reservation_config.timezone,
    )
    log.debug(
        "Reservation calendars: %s",
        _calendar_summary(reservation_config.calendars),
        extra=log_extra(reservation_config.calendars),
    )
    log.debug(
        "Reservation window is %s day(s) back and %s day(s) ahead",
        reservation_config.lookback_days,
        reservation_config.lookahead_days,
    )
    log.debug("Dry-run mode is %s", "enabled" if common.dry_run else "disabled")
    process_calendars(
        service,
        reservation_config,
        dry_run=common.dry_run,
        log=log,
        now=now,
    )
    log.info(
        "Google Calendar reservation validation completed successfully for "
        "%s calendar(s)",
        len(reservation_config.calendars),
    )
    return 0


def calendar_reservation_config(
    config: ConfigData,
    *,
    default_timezone: str = "UTC",
) -> ReservationConfig:
    """Build a validated ReservationConfig from raw YAML config.

    Reads the ``calendars`` section, normalizing acceptable
    domains to casefold for case-insensitive matching and resolving the
    timezone name. ``calendars.timezone`` overrides the supplied
    default timezone, which lets commands inherit ``common.timezone`` while
    preserving the per-script override. Raises ConfigError if any field is
    missing, malformed, or names an unknown timezone.
    """
    section = _mapping(config.get("calendars", {}), "calendars")
    domains = _string_list(
        section.get("acceptable_domains"),
        "calendars.acceptable_domains",
    )
    calendars = _calendars(section.get("calendars"))
    timezone_name = section.get("timezone", default_timezone)
    if not isinstance(timezone_name, str):
        raise ConfigError("calendars.timezone must be a string")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"calendars.timezone is unknown: {timezone_name}") from exc
    lookback_days = _positive_int(
        section.get("lookback_days", 31),
        "calendars.lookback_days",
    )
    lookahead_days = _positive_int(
        section.get("lookahead_days", 547),
        "calendars.lookahead_days",
    )
    return ReservationConfig(
        acceptable_domains=frozenset(domain.casefold() for domain in domains),
        calendars=tuple(calendars),
        timezone=timezone,
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
    )


def load_calendar_credentials(
    config: ConfigData,
    *,
    base_dir: Path | None = None,
) -> Any:
    """Load Google Calendar credentials from the ``google`` config section.

    Supports either a service account file (optionally impersonating
    ``delegated_subject``) or a stored user token, but not both. Raises
    ConfigError if both are set, neither is set, or a field has the wrong type.
    """
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
            resolve_path(
                service_account_file,
                "google.service_account_file",
                base_dir=base_dir,
            ),
            scopes=[CALENDAR_SCOPE],
            subject=delegated_subject,
        )
    if isinstance(user_token_file, str):
        return load_user_credentials(
            resolve_path(
                user_token_file,
                "google.user_token_file",
                base_dir=base_dir,
            ),
            scopes=[CALENDAR_SCOPE],
        )
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
    """Download and respond to events for every configured calendar.

    Computes a UTC time window around the current time (``now`` is injectable
    for tests), then for each calendar lists events in that window, decides a
    response per event, and applies it. When ``dry_run`` is true the responses
    are logged but not written back.
    """
    # Default to wall-clock UTC; tests pass a fixed ``now`` for determinism.
    current_time = (now or (lambda: dt.datetime.now(dt.UTC)))()
    # Treat a naive clock value as UTC so the window math stays unambiguous.
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=dt.UTC)
    time_min = current_time.astimezone(dt.UTC) - dt.timedelta(days=config.lookback_days)
    time_max = current_time.astimezone(dt.UTC) + dt.timedelta(
        days=config.lookahead_days
    )
    log.info("Checking reservations from %s through %s", time_min, time_max)
    plans: list[CalendarDecisionPlan] = []
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
        log.debug(
            "Calendar %s events: %s",
            calendar.name,
            _events_summary(events),
            extra=log_extra(events),
        )
        decisions = reservation_decisions(events, calendar, config, log=log)
        log.info(
            "Computed %s decision(s) for %s",
            len(decisions),
            calendar.name,
        )
        log.debug(
            "Calendar %s decisions: %s",
            calendar.name,
            _decisions_summary(decisions),
            extra=log_extra(decisions),
        )
        plans.append(CalendarDecisionPlan(calendar, tuple(decisions)))
    for plan in plans:
        respond_to_decisions(
            service,
            plan.calendar,
            plan.decisions,
            dry_run=dry_run,
            log=log,
        )


def reservation_decisions(
    events: Sequence[dict[str, Any]],
    calendar: ReservationCalendar,
    config: ReservationConfig,
    *,
    log: logging.Logger | None = None,
) -> list[EventDecision]:
    """Decide a response for each pending event on one calendar.

    Pending (``needsAction``) events whose creator domain is not acceptable
    are declined immediately. The rest are accepted unless conflict checking
    is enabled and the event overlaps an already-accepted booking. Events the
    resource has not been asked about, or has already declined, are treated as
    fixed background and contribute to conflict detection but get no decision.

    Returning decisions separately from API writes lets dry-run mode and tests
    inspect exactly what would happen.
    """
    pending_events: list[tuple[dict[str, Any], str]] = []
    existing_events: list[dict[str, Any]] = []
    decisions: list[EventDecision] = []
    for event in events:
        resource_attendee = calendar_attendee(event, calendar.calendar_id)
        resource_status = (
            resource_attendee.get("responseStatus") if resource_attendee else None
        )
        if resource_status == "needsAction":
            attendee_email = str(resource_attendee.get("email", calendar.calendar_id))
            creator_email = str(event.get("creator", {}).get("email", ""))
            if creator_domain(creator_email) not in config.acceptable_domains:
                decisions.append(
                    EventDecision(
                        event=event,
                        response="declined",
                        reason=(
                            f"creator {creator_email} is not in an acceptable domain"
                        ),
                        attendee_email=attendee_email,
                    )
                )
            else:
                pending_events.append((event, attendee_email))
        elif resource_status != "declined" and not event_is_transparent(event):
            existing_events.append(event)

    if not calendar.check_conflicts:
        for event, email in pending_events:
            if event_interval_or_none(event, config.timezone, log=log) is None:
                decisions.append(malformed_time_decision(event, email))
            else:
                decisions.append(
                    EventDecision(
                        event=event,
                        response="accepted",
                        attendee_email=email,
                    )
                )
        return decisions

    # Seed the conflict baseline with events already on the calendar, then
    # grow it as pending events are accepted so later pending events also
    # avoid colliding with earlier accepted ones.
    accepted_intervals = []
    for event in existing_events:
        interval = event_interval_or_none(event, config.timezone, log=log)
        if interval is not None:
            accepted_intervals.append((event, interval))
    # Process pending events oldest-first by creation time so that, among
    # mutually conflicting requests, the earliest booking wins deterministically.
    for event, attendee_email in sorted(
        pending_events, key=lambda item: str(item[0].get("created", ""))
    ):
        interval = event_interval_or_none(event, config.timezone, log=log)
        if interval is None:
            decisions.append(malformed_time_decision(event, attendee_email))
            continue
        conflict = next(
            (
                existing
                for existing, existing_interval in accepted_intervals
                if intervals_overlap(interval, existing_interval)
            ),
            None,
        )
        if conflict is None:
            decisions.append(
                EventDecision(
                    event=event,
                    response="accepted",
                    attendee_email=attendee_email,
                )
            )
            if not event_is_transparent(event):
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
                    attendee_email=attendee_email,
                )
            )
    return decisions


def event_is_transparent(event: Mapping[str, Any]) -> bool:
    """Return whether Google marks an event as free/non-blocking time."""
    return event.get("transparency") == "transparent"


def malformed_time_decision(
    event: dict[str, Any],
    attendee_email: str,
) -> EventDecision:
    """Decline a pending reservation whose start/end cannot be interpreted."""
    return EventDecision(
        event=event,
        response="declined",
        reason="event start/end time is malformed",
        attendee_email=attendee_email,
    )


def respond_to_decisions(
    service: Any,
    calendar: ReservationCalendar,
    decisions: Sequence[EventDecision],
    *,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    """Log and apply each computed decision to the calendar.

    Events without an ID are skipped because the API cannot patch them. Missing
    titles use an ``untitled`` label so malformed unauthorized reservations are
    still accepted or declined instead of remaining pending forever. In
    ``dry_run`` mode the intended response is logged but never written.
    """
    for decision in decisions:
        event = decision.event
        event_id = str(event.get("id", ""))
        summary = str(event.get("summary") or "untitled")
        if not event_id:
            log.warning(
                "Event %s does not have an ID; refusing to respond",
                summary,
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
            attendee_email=decision.attendee_email,
        )


def calendar_attendee(
    event: Mapping[str, Any], calendar_id: str
) -> Mapping[str, Any] | None:
    """Return this calendar account's attendee entry, matched case-insensitively."""
    wanted = calendar_id.casefold()
    for attendee in event.get("attendees", []):
        if not isinstance(attendee, Mapping):
            continue
        email = attendee.get("email")
        if isinstance(email, str) and email.casefold() == wanted:
            return attendee
    return None


def attendee_status(event: Mapping[str, Any], calendar_id: str) -> str | None:
    """Return this account’s attendee status for an event."""
    attendee = calendar_attendee(event, calendar_id)
    return attendee.get("responseStatus") if attendee else None


def creator_domain(email: str) -> str:
    """Return the domain part of an event creator email."""
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].casefold()


def event_interval(
    event: Mapping[str, Any],
    timezone: dt.tzinfo,
) -> tuple[dt.datetime, dt.datetime]:
    """Return the (start, end) interval an event occupies, as datetimes.

    The endpoints are nudged inward by one second so that back-to-back events
    (one ending exactly when the next begins) are not treated as overlapping
    by :func:`intervals_overlap`.
    """
    one_second = dt.timedelta(seconds=1)
    return (
        event_time(event["start"], timezone) + one_second,
        event_time(event["end"], timezone) - one_second,
    )


def event_interval_or_none(
    event: Mapping[str, Any],
    timezone: dt.tzinfo,
    *,
    log: logging.Logger | None = None,
) -> tuple[dt.datetime, dt.datetime] | None:
    """Return an event interval, logging and skipping malformed event data."""
    try:
        return event_interval(event, timezone)
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        summary = str(event.get("summary") or "untitled")
        event_id = str(event.get("id") or "unknown")
        if log is not None:
            log.warning(
                "Skipping event '%s' (ID: %s) because its start/end time is "
                "malformed: %s",
                summary,
                event_id,
                exc,
            )
        return None


def event_time(value: Mapping[str, str], timezone: dt.tzinfo) -> dt.datetime:
    """Parse a Google Calendar event start/end field into a datetime.

    Timed events use a ``dateTime`` value; an absent offset falls back first to
    the event field's own ``timeZone`` value and then to the configured
    ``timezone``. All-day events use a ``date`` value and are anchored to
    midnight in ``timezone``.
    """
    if "dateTime" in value:
        parsed = dt.datetime.fromisoformat(value["dateTime"])
        if parsed.tzinfo is not None:
            return parsed
        event_timezone = value.get("timeZone")
        if event_timezone:
            return parsed.replace(tzinfo=ZoneInfo(event_timezone))
        return parsed.replace(tzinfo=timezone)
    parsed_date = dt.date.fromisoformat(value["date"])
    return dt.datetime.combine(parsed_date, dt.time(), tzinfo=timezone)


def intervals_overlap(
    left: tuple[dt.datetime, dt.datetime],
    right: tuple[dt.datetime, dt.datetime],
) -> bool:
    """Return whether two event intervals overlap."""
    return left[0] <= right[1] and right[0] <= left[1]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    """Read a mapping config value."""
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    """Read a string list config value."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    if not value:
        raise ConfigError(f"{name} must not be empty")
    return value


def _positive_int(value: Any, name: str) -> int:
    """Read a positive integer config value."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _value_description(value: Any) -> str:
    """Describe a malformed config value without overwhelming the operator."""
    if value is None:
        return "null"
    return f"{type(value).__name__} {value!r}"


def _calendar_entry_name(item: Mapping[str, Any]) -> str:
    """Return a human-friendly label for a configured calendar entry."""
    value = item.get("name")
    return f" ({value!r})" if isinstance(value, str) and value else ""


def _calendars(value: Any) -> list[ReservationCalendar]:
    """Parse configured calendar reservations."""
    if not isinstance(value, list) or not value:
        raise ConfigError("calendars.calendars must be a non-empty list")
    calendars = []
    for index, raw_calendar in enumerate(value):
        name = f"calendars.calendars[{index}]"
        item = _mapping(raw_calendar, name)
        calendar_name = item.get("name")
        calendar_id = item.get("calendar_id", item.get("id"))
        check_conflicts = item.get("check_conflicts", True)
        if not isinstance(calendar_name, str) or not calendar_name:
            raise ConfigError(
                f"{name}.name must be a non-empty string; "
                f"got {_value_description(calendar_name)}"
            )
        if not isinstance(calendar_id, str) or not calendar_id:
            raise ConfigError(
                f"{name}{_calendar_entry_name(item)}.calendar_id must be a "
                f"non-empty string; got {_value_description(calendar_id)}. "
                "Check the calendar_id value for this calendar entry and make "
                "sure it is indented under the same '- name:' item."
            )
        if not isinstance(check_conflicts, bool):
            raise ConfigError(
                f"{name}{_calendar_entry_name(item)}.check_conflicts must be a "
                f"boolean; got {_value_description(check_conflicts)}"
            )
        calendars.append(
            ReservationCalendar(
                name=calendar_name,
                calendar_id=calendar_id,
                check_conflicts=check_conflicts,
            )
        )
    return calendars
