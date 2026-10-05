"""Read models of the Admin automation command line (ADM-11 PR 3).

Each read command of ``pk-stewardship admin`` gets its data from the
function its page uses (``admin_dashboard.observe``, ``jobs.task_reads``,
``jobs.send_reads``, ``accounts.schedule_reads``) and returns a frozen read
model. A model's fields are its **defined projection**: counts, states,
stored enumeration values, identifiers and instants, never Family names,
emails, addresses, phone numbers, codes, access tokens, signed controls or
translated labels. ``to_document()`` turns those fields into JSON values
(instants as UTC ISO 8601 strings); the command catalog lists ``FIELDS``.

Every read authorizes as its page does, with the same capability, but
passively: it never records session activity, so a read-only session can
run it. It reads in the page's snapshot, rechecks the session afterwards,
and records exactly the view event the page records, once (not on the
passive polls of ``--watch``, as a page's polls are not audited). An ended
session found at either check raises ``SessionUnusable`` (exit 5).

Nothing here takes a request (see the specification's "Read models").
"""

from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from django.db import transaction
from django.http import QueryDict


class Unavailable(Exception):
    """The system cannot answer now (no setup, a restore under review); retry."""


class NotAvailable(Exception):
    """The record the command names does not exist (``not_available``, exit 1).

    A missing task, an unknown campaign, or no current campaign at all. Only
    this, never a bare ``LookupError``, is reported as ``not_available``.
    """


def plain(value):
    """A JSON value for ``value``: instants in UTC, identifiers as strings.

    Containers are converted member by member; anything else is returned
    as is, so a non-JSON value fails loudly when the document is printed.
    """
    from datetime import UTC

    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


class ReadModel:
    """A read command's result: its fields are exactly its document's members."""

    # Whether a ``--watch`` of this read stops: only progress reads change.
    terminal: ClassVar[bool] = True

    @classmethod
    def field_names(cls):
        """The document's top-level members, in declaration order."""
        return tuple(item.name for item in fields(cls))

    def to_document(self):
        """The read's ``result`` member: its fields as JSON values."""
        return {name: plain(getattr(self, name)) for name in self.field_names()}


# ---------------------------------------------------------------- admission


def _admit(caller, store, capability=None):
    """Admit the caller passively, as a page's first check, or refuse.

    Never records activity (``activity`` false), so read-only sessions read.
    An ended session, or one whose roles changed, is ``session_ended``.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import allows
    from .accounts.sessions import authenticated_admin

    principal = authenticated_admin(caller, store=store)
    if principal is None:
        raise SessionUnusable("session_ended")
    if capability is not None and not allows(principal, capability):
        raise PermissionError("This read needs another capability.")
    return principal


def _recheck(caller, store, actor, capability=None):
    """Repeat authorization after the read, as the page does before release.

    A revocation committed while the read ran withholds the document.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import allows
    from .accounts.sessions import authenticated_admin

    current = authenticated_admin(caller, store=store, read_only=True)
    if current is None:
        raise SessionUnusable("session_ended")
    if current.identity != actor.identity or (
        capability is not None and not allows(current, capability)
    ):
        raise PermissionError("The reader's access changed.")
    return current


def _held(step):
    """Run a read, waiting out a configuration change that is activating.

    As the web's task reads do (``jobs.views._held``): a read that lands in
    the second between a change's YAML selection and its activation is
    retried briefly. Any configuration refusal that remains (the change
    still activating, a stuck activation, a restore under review, the YAML
    and database disagreeing) is ``unavailable`` (exit 3), as the pages
    answer 503: a read changes nothing, so retrying is always safe.
    """
    from parishkit.config import ConfigError

    from .activation_hold import WEB_HOLD_SECONDS, wait_out_activation

    try:
        return wait_out_activation(step, limit=WEB_HOLD_SECONDS, durable=False)
    except ConfigError:
        raise Unavailable("The configuration cannot be read now.") from None


def _restore_review():
    """Whether a restore is under review, which withholds every read."""
    from .accounts.runtime_models import SystemConfiguration

    return SystemConfiguration.objects.filter(restore_review_required=True).exists()


def _audit(action, actor, **values):
    """Record the page's view event for the reader."""
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    context = {"outcome": Outcome.SUCCEEDED}
    if "count" in values:
        context["count"] = values.pop("count")
    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor.identity,
        context=context,
        **values,
    )


def query(**values):
    """The page's query parameters from command options; None values are omitted."""
    parameters = QueryDict(mutable=True)
    for name, value in values.items():
        if value is not None:
            parameters[name] = str(value)
    return parameters


# ------------------------------------------------------------------- status


@dataclass(frozen=True)
class Status(ReadModel):
    """The Admin home summary, background counts and the Family presence count.

    Sections the Administrator's capabilities would not show on the page
    are None. ``security_events``, ``automation_notices`` and
    ``ministry_catalog`` are counts only: the page names people, sessions
    and Ministries.
    """

    as_of: datetime
    mode: str
    campaign: dict | None
    source: dict
    next_mail: dict | None
    families: dict | None
    unreachable_families: int | None
    offsite_backup: dict | None
    unfinished_keys: list | None
    ministry_catalog: dict | None
    security_events: int | None
    recent_failures: list | None
    automation_notices: int | None
    tasks: dict | None
    presence: dict | None

    @classmethod
    def build(cls, config, data, now, tasks, presence, notices=None):
        """Project the home summary (``admin_dashboard.summary``) and the counts."""
        campaign = data["campaign"]
        refresh = data["full_refresh"]
        catalog = data.get("ministry_catalog")
        offsite = data.get("offsite")
        families = data.get("families")
        return cls(
            as_of=now,
            mode=config.mode,
            campaign=None
            if campaign is None
            else {
                "id": campaign.pk,
                "name": campaign.active_configuration.name,
                "state": campaign.state,
                "version": campaign.version,
                "starts_at": campaign.active_configuration.starts_at,
                "ends_at": campaign.active_configuration.ends_at,
                "delivery_paused": campaign.delivery_paused,
            },
            source={
                "refreshed_at": data["refreshed_at"],
                "full_succeeded_at": refresh.succeeded_at,
                "full_failed_at": refresh.failed_at,
                "full_failed_task_id": refresh.failed_task_id,
                "full_running": refresh.running,
                "delta_succeeded_at": refresh.delta_succeeded_at,
                "delta_failed_at": refresh.delta_failed_at,
                "frequency": refresh.frequency,
                "next_full_at": refresh.next_full_at,
                "delta_refresh": refresh.delta_refresh,
            },
            next_mail=None
            if data.get("next_mail") is None
            else {
                "kind": data["next_mail"]["kind"],
                "due_at": data["next_mail"]["due_at"],
            },
            families=None
            if families is None
            else {
                key: families[key]
                for key in ("active", "eligible", "responded", "eligible_responded")
            },
            unreachable_families=data.get("unreachable"),
            offsite_backup=(
                {
                    "state": offsite.kind,
                    "at": offsite.at,
                    "last_copy_at": offsite.last_copy_at,
                    "set_name": offsite.set_name,
                }
                if offsite is not None
                else {"state": "unset"}
                if data.get("offsite_unset")
                else None
            ),
            unfinished_keys=None
            if "unfinished_keys" not in data
            else [{"target": key["target"]} for key in data["unfinished_keys"]],
            ministry_catalog=None
            if catalog is None
            else {
                "refreshes": len(catalog["refreshes"]),
                "missing": len(catalog["missing"]),
                "retired": len(catalog["retired"]),
            },
            security_events=None
            if "security_events" not in data
            else len(data["security_events"]),
            automation_notices=notices,
            recent_failures=None
            if "recent_failures" not in data
            else [
                {
                    "id": task["id"],
                    "type": task["task_type"],
                    "updated_at": task["updated_at"],
                }
                for task in data["recent_failures"]
            ],
            tasks=tasks,
            presence=presence,
        )


def read_status(caller, service, *, audit=True):
    """``status``: the home page's read, plus its header's background counts.

    Audited as the home page (``dashboard_viewed``); the background counts
    and the presence count are passive header reads there, never audited.
    """
    from .accounts.admin_dashboard import observe
    from .accounts.automation_sessions import open_notices
    from .accounts.policy import Capability, allows
    from .accounts.presence import active_count
    from .audit.schemas import Action
    from .campaigns.work_locks import read_transaction
    from .jobs.delivery_metadata import unknown_count
    from .jobs.task_reads import counts

    def step():
        """Admit, read in one snapshot, recheck and audit."""
        actor = _admit(caller, service.store)
        with read_transaction():
            observed = observe(actor, service.store)
            if observed is None:
                raise Unavailable("A restore is under review.")
            config, data, now = observed
            tasks = presence = notices = None
            if "administrator" in actor.roles:
                # Unacknowledged automation notices, as the dashboard counts
                # them for this Administrator.
                notices = open_notices(actor, limit=0)["total"]
            if allows(actor, Capability.BACKGROUND_WORK):
                tasks = counts(now) | {"delivery_unknown": unknown_count()}
            if allows(actor, Capability.CONFIGURE):
                presence = {"count": active_count(config, now)}
        model = Status.build(config, data, now, tasks, presence, notices)
        with transaction.atomic():
            current = _recheck(caller, service.store, actor)
            if audit:
                _audit(
                    Action.DASHBOARD_VIEWED,
                    current,
                    parish_id=config.active_configuration.parish.pk,
                    campaign_id=config.current_campaign_id,
                )
        return model

    return _held(step)


# -------------------------------------------------------------------- tasks


@dataclass(frozen=True)
class TaskList(ReadModel):
    """One page of Background work: task metadata only, never task arguments."""

    as_of: datetime
    counts: dict
    state: str
    task_type: str | None
    page: int
    size: int
    sort: str
    has_next: bool
    matching: int
    matching_capped: bool
    tasks: list


@dataclass(frozen=True)
class TaskShow(ReadModel):
    """One task and a page of its history; terminal once the task is."""

    as_of: datetime
    task: dict
    latest_run_id: str
    page: int
    size: int
    sort: str
    has_next: bool
    matching: int
    matching_capped: bool
    events: list

    @property
    def terminal(self):
        """A task that is no longer queued, running or waiting has finished."""
        from .jobs.models import NONTERMINAL_STATES

        return self.task["state"] not in NONTERMINAL_STATES


def _read_tasks(caller, service, parameters, identifier, *, audit):
    """Background work's read (``jobs.views._read``) for the command line.

    Audited as the page (``background_viewed``, with the task as subject
    for ``task show``), never on a watch's later polls.
    """
    from .accounts.policy import Capability
    from .accounts.runtime_models import SystemConfiguration
    from .audit.schemas import Action
    from .jobs.ownership import database_now
    from .jobs.task_reads import detail, listing, parse_window

    def step():
        """Admit, read, recheck and audit in one transaction, as the page."""
        actor = _admit(caller, service.store, Capability.BACKGROUND_WORK)
        window, state, task_type, sort = parse_window(
            parameters, listing=identifier is None
        )
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                raise Unavailable("A restore is under review.")
            instant = database_now()
            if identifier is None:
                data, shown = listing(window, state, task_type, sort, instant)
            else:
                data, shown = detail(identifier, window, sort, instant)
            # As the page: the session is rechecked before anything is
            # reported, including that the named task does not exist.
            current = _recheck(caller, service.store, actor, Capability.BACKGROUND_WORK)
            if data is None:
                raise NotAvailable("No such task.")
            model = (
                TaskList(state=state, task_type=task_type, **data)
                if identifier is None
                else TaskShow(**data)
            )
            if audit:
                _audit(
                    Action.BACKGROUND_VIEWED,
                    current,
                    subject_id=identifier,
                    count=shown,
                )
        return model

    return _held(step)


def read_task_list(caller, service, parameters, *, audit=True):
    """``task list``: one page of Background work, filtered and sorted as the page."""
    return _read_tasks(caller, service, parameters, None, audit=audit)


def read_task(caller, service, identifier, parameters, *, audit=True):
    """``task show``: one task with a page of its history."""
    return _read_tasks(caller, service, parameters, identifier, audit=audit)


# -------------------------------------------------------------------- sends


def _send_counts(sent):
    """A ``send_progress.SendProgress`` as counts, states and instants."""
    counts = sent.counts
    return {
        "kind": counts.kind,
        "active": sent.active,
        "paused": sent.paused,
        "stalled": sent.stalled,
        "held": counts.held,
        "total": sent.total,
        "done": sent.done,
        "percent": sent.percent,
        "sent": counts.sent,
        "failed": counts.failed,
        "uncertain": counts.uncertain,
        "remaining": counts.remaining,
        "unprepared": counts.unprepared,
        "unplanned": counts.unplanned,
        "waiting": counts.waiting,
        "unreachable": counts.unreachable,
        "not_needed": counts.not_needed,
        "rate_per_minute": sent.rate,
        "started_at": counts.started_at,
        "last_settled_at": counts.last_settled_at,
        "finished_at": sent.finished_at,
        "finish_at": sent.finish_at,
        "minutes_left": sent.minutes_left,
    }


@dataclass(frozen=True)
class SendProgressRead(ReadModel):
    """The Family send in progress, or the most recent one; counts only.

    A watch stops once no send is in progress and none is about to start.
    """

    campaign_id: UUID | None
    mode: str
    paused: bool
    upcoming: bool
    send: dict | None

    @property
    def terminal(self):
        """Nothing is sending and nothing is about to start."""
        return not (self.send and self.send["active"]) and not self.upcoming


@dataclass(frozen=True)
class SendHistory(ReadModel):
    """One page of the current campaign's Family sends, newest first."""

    campaign_id: UUID | None
    page: int
    pages: int
    count: int
    size: int | None
    sends: list


def send_row(row):
    """One ``send_history.SendRow`` as counts, states, identifiers and instants.

    ``send`` is the send's key (``definition:revision:mode:cycle``), the
    value Outgoing mail's ``send`` filter takes.
    """
    return {
        "send": row.listed.key.token,
        "kind": row.listed.kind,
        "number": row.listed.number,
        "mode": row.listed.key.mode,
        "cycle": row.listed.key.cycle,
        "scheduled_at": row.listed.scheduled,
        "replaced": row.listed.replaced,
        "current": row.current,
        "earlier": row.earlier,
        "live": row.live,
        "cancelled": row.emails.get("cancelled", 0),
        "minutes": row.minutes,
        **_send_counts(row.send),
    }


def read_send_progress(caller, service, *, audit=True):
    """``send progress``: Family email progress (``send_reads.read_progress``).

    Audited as the page (``delivery_viewed``), never on a watch's polls.
    """
    from .accounts.policy import Capability
    from .audit.schemas import Action
    from .jobs.send_reads import audited_count, read_progress

    def step():
        """Admit, read in the page's snapshot, recheck and audit."""
        actor = _admit(caller, service.store, Capability.BACKGROUND_WORK)
        data = read_progress()
        if data is None:
            raise Unavailable("A restore is under review.")
        sent = data["send"]
        model = SendProgressRead(
            campaign_id=data["campaign"].pk if data["campaign"] else None,
            mode="testing" if data["testing"] else "production",
            paused=data["paused"],
            upcoming=data["upcoming"],
            send=None if sent is None else _send_counts(sent),
        )
        with transaction.atomic():
            current = _recheck(caller, service.store, actor, Capability.BACKGROUND_WORK)
            if _restore_review():
                raise Unavailable("A restore is under review.")
            if audit:
                _audit(Action.DELIVERY_VIEWED, current, count=audited_count(sent))
        return model

    return _held(step)


def read_send_history(caller, service, parameters, *, audit=True):
    """``send history``: the Family email sends page (``send_reads.read_history``)."""
    from .accounts.policy import Capability
    from .audit.schemas import Action
    from .jobs.send_reads import read_history
    from .web.contracts import filters

    def step():
        """Admit, read in the page's snapshot, recheck and audit."""
        actor = _admit(caller, service.store, Capability.BACKGROUND_WORK)
        data = read_history(filters(parameters, allowed={"page", "size"}))
        if data is None:
            raise Unavailable("A restore is under review.")
        table = data["table"]
        model = SendHistory(
            campaign_id=data["campaign"].pk if data["campaign"] else None,
            page=table.number,
            pages=table.pages,
            count=table.count,
            size=table.size,
            sends=[send_row(row) for row in table.rows],
        )
        with transaction.atomic():
            current = _recheck(caller, service.store, actor, Capability.BACKGROUND_WORK)
            if _restore_review():
                raise Unavailable("A restore is under review.")
            if audit:
                _audit(Action.DELIVERY_VIEWED, current, count=len(table.rows))
        return model

    return _held(step)


# ---------------------------------------------------------------- schedules


@dataclass(frozen=True)
class ScheduleShow(ReadModel):
    """The campaign's dates and mail schedules as the applied configuration has them.

    ``version`` is the applied configuration's digest, the base a schedule
    change (PR 4) is previewed against. ``editable`` says whether the
    campaign's dates may still change. Each schedule lists its civil values
    and its first resolved send times (``resolved``, at most five, with
    ``more`` when there are others).
    """

    campaign_id: UUID
    version: str
    editable: bool
    window: dict
    schedules: list


def schedule_entry(row, campaign):
    """One schedule record with its first resolved send times.

    ``campaign`` is the campaign's applied values (its time zone and dates).
    """
    from .campaigns.schedule_evaluation import preview_slots

    values = row["values"]
    page = preview_slots(values, campaign)
    return {
        "id": row["id"],
        "kind": values["kind"],
        "date": values["date"],
        "time": values["time"],
        "weekday": values["weekday"],
        "subject": values["subject"],
        "template_version": values["template_version"],
        "resolved": [{"key": slot.key, "due_at": slot.due_at} for slot in page.slots],
        "more": not page.exhausted,
    }


def read_schedule(caller, service, campaign_id):
    """``schedule show``: the Mail schedules page's read, without its forms.

    The page records no view event, so neither does this. The current
    campaign is the default. As on the page, an unknown campaign, or none
    at all, is ``not_available``; a campaign that is not the current one,
    or that background work holds, is ``stale_version``.
    """
    from .accounts.policy import Capability
    from .accounts.schedule_reads import campaign_schedules, schedule_state
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read in one snapshot, then recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            if campaign_id is None:
                from .accounts.admin_editing import editable_configuration

                target = editable_configuration(service).current_campaign_id
                if target is None:
                    raise NotAvailable("There is no current campaign.")
            else:
                target = campaign_id
            try:
                state, campaign, editable = schedule_state(service, target)
            except KeyError:
                # A KeyError is a bug, never "no such campaign": internal.
                raise
            except LookupError:
                raise NotAvailable("No such campaign.") from None
            values = campaign.active_configuration.values
            model = ScheduleShow(
                campaign_id=campaign.pk,
                version=state[0].active_configuration.digest,
                editable=editable,
                window={
                    "start_date": values["start_date"],
                    "end_date": values["end_date"],
                    "timezone": values["timezone"],
                },
                schedules=[
                    schedule_entry(row, values)
                    for row in campaign_schedules(state[0], target)
                ],
            )
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return model

    return _held(step)
