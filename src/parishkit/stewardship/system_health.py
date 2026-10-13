"""The System health page's read model (ADM-13 PR 2, #530).

One read answers "is the system healthy, and what is running?" for the
Administrator-only System health page (admin-portal spec, "System health"):
the problems list and its six panels (why sends are waiting, mail sender,
ParishSoft refresh, backups, debug logging and version). It reads one
read-only snapshot, takes no lock (in particular not the work-order lock that
sending takes) and reads only small rows and indexed counts: the service
status records, open incidents, the refresh outcomes that Home already reads,
the newest backup and off-site copy, the waiting retries, the 24-hour send
count (through its definer function) and the applied migrations. It never
calls ParishSoft, Google or Slack.

``SystemHealth`` is the read model; ``to_document()`` is its defined
projection (states, counts, stored enumeration values and instants, never
a name, an address, a reason someone typed or a translated label), which the
``system health`` command will print. The page renders the same model in
words. The actions (take a backup now, clear a halt, accept a large change,
turn off debug logging) come with ADM-13 PR 3 to PR 6, each with its own
service function here; Take a backup now (PR 3), Accept this change once
(PR 5b) and Turn off debug logging (PR 6b) have landed, and share
``_record_fresh``.

Pure helpers (``group_processes``, ``find_problems``) take every input as an
argument, so they are unit tested without a database.
"""

import logging
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from functools import cache

from .admin_reads import ReadModel, plain
from .service_status import NOT_RUNNING_SECONDS

# A process whose record is older than this is shown as not running.
NOT_RUNNING = timedelta(seconds=NOT_RUNNING_SECONDS)
# The services every deployment runs; one that never reported is a problem.
# The installers are shown when they report, but a deployment can run
# without a credential installer for a target, so their absence is not.
CORE_SERVICES = ("web", "worker", "scheduler", "mail-dispatch")
# Display order of the services, matching the deployment's own list.
SERVICE_ORDER = (
    "web",
    "worker",
    "scheduler",
    "mail-dispatch",
    "config-installer",
    "credential-installer",
)
PROCESS_ORDER = ("main", "source", "mail")
# Open incidents the page names, by the panel that shows them. Every one of
# them is also a problem at the top of the page.
MAIL_INCIDENTS = ("mail_provider_unavailable",)
SOURCE_INCIDENTS = (
    "source_destructive_change",
    "source_tenant_mismatch",
    "source_retention_failing",
)
BACKUP_INCIDENTS = ("backup_rpo_breach", "backup_offsite_failed", "backup_key_changed")
INCIDENTS = MAIL_INCIDENTS + SOURCE_INCIDENTS + BACKUP_INCIDENTS
# The operator runbooks the page links, in the public repository's guides.
RUNBOOKS = {
    "deployment": (
        "https://github.com/epiphany40223/parishkit/blob/main/docs/guides/"
        "stewardship-deployment-runbook.md"
    ),
    "backup_schedule": (
        "https://github.com/epiphany40223/parishkit/blob/main/docs/guides/"
        "stewardship-backup-runbook.md#the-nightly-backup"
    ),
}
# Family email purposes, whose waiting retries the page counts.
FAMILY_PURPOSES = ("initial", "reminder")


def process_label(service, process, target=None):
    """A process's name in plain words, for the page.

    Mail dispatch runs two mail consumers by default; the page calls them
    mail sender 1 and 2. Without ``process`` it names the whole service.
    """
    from django.utils.translation import gettext as _

    if process is None:
        return {
            "web": _("Web portal"),
            "worker": _("Background worker"),
            "scheduler": _("Scheduler"),
            "mail-dispatch": _("Mail sender"),
        }.get(service, service)
    if service == "credential-installer":
        return _("Key installer (%(target)s)") % {"target": target}
    return {
        ("web", "main"): _("Web portal"),
        ("worker", "main"): _("Background worker"),
        ("worker", "source"): _("ParishSoft refresh worker"),
        ("scheduler", "main"): _("Scheduler"),
        ("mail-dispatch", "main"): _("Mail sender 1"),
        ("mail-dispatch", "mail"): _("Mail sender 2"),
        ("config-installer", "main"): _("Settings installer"),
    }.get((service, process), service)


def process_phrase(service, process, target=None, *, start=False):
    """A process's name as it reads inside a sentence, or at its start.

    Each form is written out whole for translators ("the web portal",
    "The web portal", "mail sender 1"), never derived by changing a label's
    case or adding an article in code, so names such as "ParishSoft" keep
    their spelling wherever they fall in a list.
    """
    from django.utils.translation import gettext as _

    if service == "credential-installer":
        form = (
            _("The key installer (%(target)s)")
            if start
            else _("the key installer (%(target)s)")
        )
        return form % {"target": target}
    phrases = {
        ("web", None): (_("The web portal"), _("the web portal")),
        ("worker", None): (_("The background worker"), _("the background worker")),
        ("scheduler", None): (_("The scheduler"), _("the scheduler")),
        ("mail-dispatch", None): (_("The mail sender"), _("the mail sender")),
        ("web", "main"): (_("The web portal"), _("the web portal")),
        ("worker", "main"): (_("The background worker"), _("the background worker")),
        ("worker", "source"): (
            _("The ParishSoft refresh worker"),
            _("the ParishSoft refresh worker"),
        ),
        ("scheduler", "main"): (_("The scheduler"), _("the scheduler")),
        ("mail-dispatch", "main"): (_("Mail sender 1"), _("mail sender 1")),
        ("mail-dispatch", "mail"): (_("Mail sender 2"), _("mail sender 2")),
        ("config-installer", "main"): (
            _("The settings installer"),
            _("the settings installer"),
        ),
    }
    pair = phrases.get((service, process))
    if pair is None:
        return service
    return pair[0] if start else pair[1]


@dataclass(frozen=True)
class ProcessLine:
    """One process the page lists: a (service, process, target) group's state.

    Every restart writes a new status row under a new identity, and the old
    row stays until housekeeping removes it a day later, so rows are grouped
    by (service, process, target) and the newest live row stands for the
    group. ``live`` counts the rows that reported within ``NOT_RUNNING``:
    several web worker processes share (web, main), so for web it is how
    many are running. ``debug_logging`` is true when any live row of the
    group has it in effect.

    Two limits of that rule, accepted because the record is for display:
    for up to ``NOT_RUNNING`` after the web service restarts, the replaced
    processes' rows still count as live, so ``live`` over-counts; and a
    group shows only its standing row's version, so different versions
    among live web processes during a rolling restart are not reported as
    ``versions_differ``.
    """

    service: str
    process: str
    target: str | None
    running: bool
    live: int
    version: str
    started_at: datetime
    reported_at: datetime
    debug_logging: bool
    sender_state: str | None = None
    sender_since: datetime | None = None
    sender_until: datetime | None = None

    @property
    def sender(self):
        """Whether this is a mail consumer, which reports a sender state."""
        return self.service == "mail-dispatch"

    @property
    def label(self):
        """The process's name in plain words."""
        return process_label(self.service, self.process, self.target)


@dataclass(frozen=True)
class Subject:
    """A process (or, without ``process``, a whole service) a problem is about."""

    service: str
    process: str | None = None
    target: str | None = None

    @property
    def label(self):
        """Its name in plain words."""
        return process_label(self.service, self.process, self.target)


def _names(subjects, *, start):
    """The subjects' names as one phrase for a sentence.

    "The web portal, the scheduler and mail sender 1" when the sentence
    begins with them (``start``), "the web portal, the scheduler and mail
    sender 1" in its middle; each name is its own translated phrase
    (``process_phrase``). The mail-dispatch service runs exactly two mail
    senders (main and mail), so when both are named they read "both mail
    senders"; with a third consumer this would need another phrase.
    """
    from django.utils.text import get_text_list
    from django.utils.translation import gettext as _

    senders = [subject for subject in subjects if subject.service == "mail-dispatch"]
    both = len(senders) == 2 and all(subject.process for subject in senders)
    phrases, said = [], False
    for subject in subjects:
        first = start and not phrases
        if both and subject in senders:
            if not said:
                phrases.append(
                    _("Both mail senders") if first else _("both mail senders")
                )
                said = True
            continue
        phrases.append(
            process_phrase(
                subject.service, subject.process, subject.target, start=first
            )
        )
    return get_text_list(phrases, _("and"))


@dataclass(frozen=True)
class Problem:
    """One current problem: its stable kind, and what it names.

    ``subjects`` are the processes (or services) it is about; a condition
    that affects several of them at once (debug logging, a stopped or
    silent service, a halted mail sender) is one problem naming them all,
    so it is listed and announced once (#686). ``at`` is the time the
    sentence states (when it began, or until when).
    """

    kind: str
    subjects: tuple = ()
    at: datetime | None = None

    @property
    def names(self):
        """The subjects' names, for a sentence that begins with them."""
        return _names(self.subjects, start=True)

    @property
    def names_inline(self):
        """The subjects' names, for the middle of a sentence."""
        return _names(self.subjects, start=False)

    @property
    def several(self):
        """Whether the problem is about more than one process."""
        return len(self.subjects) > 1

    @property
    def summary(self):
        """A few plain words naming the problem, for the announcement."""
        from django.utils.translation import gettext as _

        words = {
            "sender_halted": _("%(name)s halted"),
            "sender_outage": _("%(name)s paused after an outage"),
            "mail_provider_unavailable": _("email provider unavailable"),
            "not_running": _("%(name)s not running"),
            "not_reported": _("%(name)s not reporting"),
            "source_failing": _("ParishSoft not answering"),
            "source_failed": _("ParishSoft refresh failed"),
            "source_late": _("ParishSoft refresh late"),
            "source_destructive_change": _("ParishSoft refresh refused"),
            "source_tenant_mismatch": _("ParishSoft organization changed"),
            "source_retention_failing": _("old ParishSoft copies kept"),
            "backup_rpo_breach": _("backup overdue"),
            "backup_offsite_failed": _("off-site copy failed"),
            "backup_key_changed": _("backup key changed"),
            "debug_logging": _("debug logging on in %(name)s"),
            "versions_differ": _("versions differ"),
            "schema_mismatch": _("database does not match"),
        }.get(self.kind, self.kind)
        if "%(name)s" not in words:
            return words
        name = self.names if words.startswith("%(name)s") else self.names_inline
        return words % {"name": name}


@dataclass(frozen=True)
class DropCount:
    """One count a refused ParishSoft load was checked on."""

    measure: str
    before: int | None
    after: int
    limit_percent: int
    failed: bool

    @property
    def fell_percent(self):
        """The share the count fell by, in whole percent rounded up (so a
        count that fell by more than the limit never reads as the limit
        itself), or None when it did not fall or had no before value."""
        if not self.before or self.after >= self.before:
            return None
        return -(-(self.before - self.after) * 100 // self.before)

    @property
    def label(self):
        """What the count counts, in plain words."""
        from django.utils.translation import gettext as _

        return {
            "family": _("Families"),
            "member": _("Members"),
            "ministry": _("Ministries"),
            "roster": _("Ministry roster entries"),
            "fund": _("Funds"),
            "portal_eligible_families": _("Families who can use the Family portal"),
            "email_eligible_families": _("Families with an email address"),
            "active_head_families": _("Families with an active head of household"),
            "valid_email_contacts": _("Valid email addresses"),
        }.get(self.measure, self.measure)


def _order(line):
    """Sort processes in the deployment's service and process order."""
    return (
        SERVICE_ORDER.index(line.service),
        PROCESS_ORDER.index(line.process),
        line.target or "",
    )


def group_processes(rows, now):
    """Group status rows into ``ProcessLine`` values, one per process.

    ``rows`` are mappings with the ``stewardship_service_status`` columns.
    In each (service, process, target) group the newest-started live row
    stands for it; with no live row the newest-started row does, shown as
    not running. An older row whose process was replaced by a restart is
    therefore never shown as not running.
    """
    groups = {}
    for row in rows:
        key = (row["service"], row["process"], row["target"])
        groups.setdefault(key, []).append(row)
    lines = []
    for (service, process, target), members in groups.items():
        live = [row for row in members if now - row["reported_at"] <= NOT_RUNNING]
        current = max(live or members, key=lambda row: row["started_at"])
        lines.append(
            ProcessLine(
                service=service,
                process=process,
                target=target,
                running=bool(live),
                live=len(live),
                version=current["application_version"],
                started_at=current["started_at"],
                reported_at=current["reported_at"],
                debug_logging=any(row["debug_logging"] for row in live or [current]),
                sender_state=current["sender_state"],
                sender_since=current["sender_since"],
                sender_until=current["sender_until"],
            )
        )
    return tuple(sorted(lines, key=_order))


def _grouped(kind, lines, at=None):
    """One problem about every line in ``lines``, or none when there are none.

    ``at`` picks the time the sentence states from the lines' times.
    """
    if not lines:
        return []
    subjects = tuple(Subject(line.service, line.process, line.target) for line in lines)
    return [Problem(kind, subjects, at(lines) if at else None)]


def find_problems(
    *,
    mode,
    processes,
    missing,
    schema_current,
    incidents,
    refresh,
    backup_at,
):
    """Every current problem, in the order the page lists them.

    Mail first (what an Administrator watches during a send), then the
    services, ParishSoft, backups, debug logging and the version, the order
    of the panels. ``refresh`` is a ``FullRefreshStatus`` (or None),
    ``incidents`` maps each open incident kind to when it began, and
    ``backup_at`` is when the newest backup finished. A pause an
    Administrator chose, Gmail's own limit and the daily limit are reasons
    sends wait, shown in their panel, not problems.
    """
    running = [line for line in processes if line.running]
    senders = [line for line in running if line.sender]
    # A halt is stated from when the first sender halted; an outage pause
    # until the last sender tries again.
    problems = _grouped(
        "sender_halted",
        [line for line in senders if line.sender_state == "halted"],
        lambda lines: min(
            (line.sender_since for line in lines if line.sender_since),
            default=None,
        ),
    )
    problems += _grouped(
        "sender_outage",
        [line for line in senders if line.sender_state == "outage_paused"],
        lambda lines: max(
            (line.sender_until for line in lines if line.sender_until),
            default=None,
        ),
    )
    problems.extend(
        Problem(kind, at=incidents[kind])
        for kind in MAIL_INCIDENTS
        if kind in incidents
    )
    # Stopped processes are stated from the most recent report among them.
    problems += _grouped(
        "not_running",
        [line for line in processes if not line.running],
        lambda lines: max(line.reported_at for line in lines),
    )
    if missing:
        problems.append(
            Problem("not_reported", tuple(Subject(service) for service in missing))
        )
    if refresh is not None:
        connection = refresh.connection
        if connection is not None and connection.state == "failing":
            problems.append(Problem("source_failing", at=connection.at))
        elif refresh.failed_at is not None:
            problems.append(Problem("source_failed", at=refresh.failed_at))
        if refresh.out_of_date and not refresh.held_for_send:
            problems.append(Problem("source_late", at=refresh.overdue_at))
    problems.extend(
        Problem(kind, at=incidents[kind])
        for kind in SOURCE_INCIDENTS
        if kind in incidents
    )
    for kind in BACKUP_INCIDENTS:
        if kind in incidents:
            # The overdue backup's sentence states when the newest one finished.
            at = backup_at if kind == "backup_rpo_breach" else incidents[kind]
            problems.append(Problem(kind, at=at))
    if mode == "production":
        problems += _grouped(
            "debug_logging", [line for line in running if line.debug_logging]
        )
    if len({line.version for line in processes if line.running}) > 1:
        problems.append(Problem("versions_differ"))
    if not schema_current:
        problems.append(Problem("schema_mismatch"))
    return tuple(problems)


@dataclass(frozen=True)
class SystemHealth(ReadModel):
    """Everything the System health page shows, read in one snapshot.

    ``refresh`` is the ``FullRefreshStatus`` Home and the refresh page show;
    ``offsite`` the ``OffsiteStatus`` Home shows (None when off-site copies
    are not set up). ``refused_at`` and ``refused_counts`` describe the
    newest refused ParishSoft load, when no full refresh has promoted since.
    ``backup_key_matches`` is None when no backup key is configured or no
    backup has finished; ``backup_bytes`` and ``backup_version`` are the
    newest backup's size and the version that took it, and
    ``backup_request`` the newest Take a backup now request (ADM-13 PR 3).
    ``debug_switch`` is the newest debug-off switch (ADM-13 PR 6), or None
    when none was ever set; who set it is the page's alone (``read_health``).
    ``drop_acceptance`` is the Administrator's one-time acceptance of the
    refusal shown (ADM-13 PR 5b), or None while it has none; who gave it is
    the page's alone too. ``delivery_paused`` is Production's live delivery
    pause; ``send_active`` and ``planning_held`` come from Family email
    progress. ``daily_sent`` is
    the 24-hour recipient count the mail senders compare with the daily
    limit, and ``daily_limit`` the share of it Family invitations, reminders
    and Family tests may use (ADM-13 PR 2c); ``daily_resume_at`` is when that
    count falls below ``daily_limit`` again, or None while it is below
    (#530).
    """

    checked_at: datetime
    mode: str
    problems: tuple
    processes: tuple
    missing_services: tuple
    versions: tuple
    schema_current: bool
    delivery_paused: bool
    paused_at: datetime | None
    send_active: bool
    planning_held: bool
    retry_waiting: int
    next_retry_at: datetime | None
    daily_sent: int
    daily_limit: int
    daily_resume_at: datetime | None
    incidents: dict
    refresh: object
    refused_at: datetime | None
    refused_counts: tuple
    backup_at: datetime | None
    backup_key_matches: bool | None
    backup_bytes: int | None
    backup_version: str | None
    backup_request: object
    offsite: object
    debug_switch: object
    # Last, with a default, so a model built before it existed still builds.
    drop_acceptance: object = None

    def to_document(self):
        """The defined projection: states, counts and instants only.

        The refresh and off-site outcomes are projected to their states and
        times; their translated messages are not part of the document.
        """
        document = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "refresh":
                value = None if value is None else _refresh_document(value)
            elif item.name == "offsite":
                value = (
                    {"state": "unset"}
                    if value is None
                    else {
                        "state": value.kind,
                        "at": value.at,
                        "last_copy_at": value.last_copy_at,
                    }
                )
            elif item.name == "incidents":
                # A list of fixed members, not a mapping keyed by kind, so
                # the document's member names never depend on the data.
                value = [
                    {"kind": kind, "since": since}
                    for kind, since in sorted(value.items())
                ]
            elif item.name in {"backup_request", "debug_switch", "drop_acceptance"}:
                value = None if value is None else _members(value)
            elif item.name == "problems":
                value = [
                    {
                        "kind": problem.kind,
                        "subjects": [_members(subject) for subject in problem.subjects],
                        "at": problem.at,
                    }
                    for problem in value
                ]
            elif item.name in {"processes", "refused_counts"}:
                value = [_members(entry) for entry in value]
            document[item.name] = plain(value)
        return document

    @property
    def terminal(self):
        """``system health --watch`` repeats until everything is working."""
        return not self.problems

    @property
    def runbooks(self):
        """The runbook links the page shows (not part of the document)."""
        return RUNBOOKS

    @property
    def senders(self):
        """The mail consumers' lines."""
        return tuple(line for line in self.processes if line.sender)

    @property
    def waiting(self):
        """Whether anything is holding Family email back right now."""
        return bool(
            self.delivery_paused
            or self.planning_held
            or self.retry_waiting
            or any(
                not line.running or line.sender_state != "running"
                for line in self.senders
            )
            or "mail-dispatch" in self.missing_services
        )

    @property
    def accept_state(self):
        """Whether the refusal shown can be accepted once, or why not.

        ``none`` while no refusal is shown; ``accepted`` while its acceptance
        is live and ``lapsed`` once that ended unused (a refusal is accepted
        at most once, so **Refresh now** gives a new one to accept);
        ``empty`` for a load with no Families or no Members, which is
        refused at any setting and so can never be accepted; otherwise
        ``open``. Only a hint for the page: the service function and the
        acceptance's SQL guard decide.
        """
        return accept_state(self.refused_counts, self.drop_acceptance)

    @property
    def attention(self):
        """The problems that need the Administrator, which the page counts.

        Every problem, except a refused large change already accepted once
        (#530): the Administrator has handled it and it waits for the next
        full refresh, so the page lists it, saying so, without counting it.
        """
        if self.accept_state != "accepted":
            return self.problems
        return tuple(
            problem
            for problem in self.problems
            if problem.kind != "source_destructive_change"
        )

    @property
    def debug_lines(self):
        """The running processes with debug logging in effect."""
        return tuple(
            line for line in self.processes if line.running and line.debug_logging
        )

    @property
    def announcement(self):
        """The short sentence screen readers hear when the problems change."""
        from django.utils.translation import gettext, ngettext

        attention = self.attention
        if not self.problems:
            return gettext("Everything is working.")
        if not attention:
            return gettext("Nothing needs attention.")
        # The short names make the sentence change whenever the set of
        # problems does, not only when their number does, so a problem that
        # ends as another begins is still announced.
        return ngettext(
            "%(count)d problem needs attention: %(names)s.",
            "%(count)d problems need attention: %(names)s.",
            len(attention),
        ) % {
            "count": len(attention),
            "names": "; ".join(problem.summary for problem in attention),
        }


def _members(entry):
    """A dataclass entry's fields as a mapping, for a document."""
    return {part.name: getattr(entry, part.name) for part in fields(entry)}


def _refresh_document(status):
    """The refresh outcome's states and times, as ``admin_reads.Status`` has them."""
    connection = status.connection
    return {
        "full_succeeded_at": status.succeeded_at,
        "full_failed_at": status.failed_at,
        "full_running": status.running,
        "delta_succeeded_at": status.delta_succeeded_at,
        "full_started_at": status.full_started_at,
        "data_as_of": status.data_as_of,
        "connection": None if connection is None else connection.state,
        "connection_at": None if connection is None else connection.at,
        "overdue_full_at": status.overdue_at,
        "out_of_date": status.out_of_date,
        "held_for_send": status.held_for_send,
        "resume_at": status.resume_at,
        "next_full_at": status.next_full_at,
    }


@cache
def _image_migrations():
    """Every (app, name) migration this image ships, read once per process."""
    from .upgrade_check import disk_migrations

    return frozenset(disk_migrations())


# The status records, the open incidents the page names, the applied
# migrations and when the newest backup finished, as one derived row: Home
# has a fixed query budget, so ``refresh_status.full_refresh_status`` joins
# this row into the statement Home already runs, and the page runs it alone.
# Every column is read whatever the data, so Home's query count never
# depends on which problems are open. Times travel as JSON and are parsed.
HEALTH_SQL = (
    "SELECT (SELECT coalesce(json_agg(json_build_object("
    "'service',service,'process',process,'target',target,"
    "'started_at',started_at,'reported_at',reported_at,"
    "'application_version',application_version,'debug_logging',debug_logging,"
    "'sender_state',sender_state,'sender_since',sender_since,"
    "'sender_until',sender_until)),'[]'::json) "
    "FROM public.stewardship_service_status),"
    "(SELECT coalesce(json_agg(json_build_array(kind,first_seen)),'[]'::json) "
    "FROM public.stewardship_ops_incident "
    "WHERE resolved_at IS NULL AND kind=ANY(%(health_incidents)s)),"
    "(SELECT array_agg(app||' '||name) FROM public.django_migrations),"
    "(SELECT completed_at FROM public.stewardship_backup_run "
    "ORDER BY completed_at DESC,id DESC LIMIT 1)"
)
HEALTH_COLUMNS = 4
_TIMES = ("started_at", "reported_at", "sender_since", "sender_until")


def health_params():
    """The named parameters ``HEALTH_SQL`` takes."""
    return {"health_incidents": list(INCIDENTS)}


@dataclass(frozen=True)
class HealthFacts:
    """What ``HEALTH_SQL`` read, parsed.

    ``rows`` are mappings with the ``stewardship_service_status`` columns;
    ``incidents`` maps each open kind the page names to when it began;
    ``schema_current`` is whether the applied migrations are exactly the
    ones this version ships; ``backup_at`` is when the newest backup
    finished.
    """

    rows: list
    incidents: dict
    schema_current: bool
    backup_at: datetime | None


def _instant(value):
    """A JSON timestamp from PostgreSQL as an aware datetime (None stays None)."""
    return None if value is None else datetime.fromisoformat(value)


def health_facts(columns):
    """Parse the ``HEALTH_COLUMNS`` columns ``HEALTH_SQL`` selects.

    psycopg decodes the JSON columns already.
    """
    rows, incidents, applied, backup_at = columns
    for row in rows:
        for name in _TIMES:
            row[name] = _instant(row[name])
    applied = frozenset(tuple(item.split(" ", 1)) for item in applied or ())
    return HealthFacts(
        rows,
        {kind: _instant(since) for kind, since in incidents},
        applied == _image_migrations(),
        backup_at,
    )


def _facts():
    """Run ``HEALTH_SQL`` alone (the page's read) and parse it."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(HEALTH_SQL, health_params())
        return health_facts(cursor.fetchone())


def _retries():
    """How many Family emails wait to retry, and when the next is due."""
    from django.db.models import Count, Min

    from .jobs.outbox_models import OutboxMessage

    found = OutboxMessage.objects.filter(
        state="retry_wait", purpose__in=FAMILY_PURPOSES
    ).aggregate(count=Count("id"), due=Min("not_before"))
    return found["count"], found["due"]


def _refused(succeeded_at):
    """The newest refused load's time, counts and attempt, unless a full
    refresh followed: ``(None, (), None)`` then.

    A full refresh that promoted after the refusal settles it, so the panel
    no longer shows it.
    """
    from .source.drop_models import DROP_MEASURES, SourceDropCount

    newest = (
        SourceDropCount.objects.order_by("-created_at", "-id")
        .values("attempt_id", "created_at")
        .first()
    )
    if newest is None or (
        succeeded_at is not None and succeeded_at >= newest["created_at"]
    ):
        return None, (), None
    rows = SourceDropCount.objects.filter(attempt_id=newest["attempt_id"]).values(
        "measure", "before", "after", "limit_percent", "failed"
    )
    counts = sorted(
        (DropCount(**row) for row in rows),
        key=lambda count: DROP_MEASURES.index(count.measure),
    )
    return newest["created_at"], tuple(counts), newest["attempt_id"]


@dataclass(frozen=True)
class DropAcceptanceStatus:
    """The one-time acceptance of the refusal System health shows (ADM-13 PR
    5b, #530): its identity, when it was given, when it expires, and whether
    it is still live (``stewardship_source_drop_acceptance_active_v1()``).

    Who gave it is not part of the read model's document: the page shows the
    Administrator's address from ``read_health``'s page values.
    """

    id: object
    created_at: datetime
    expires_at: datetime
    live: bool


def _drop_acceptance(attempt_id):
    """The acceptance of ``attempt_id``'s refusal and who gave it.

    ``(None, None)`` when the refusal has none. The address is read only
    while the acceptance is live, the only time the page names who gave it.
    Whether it is live is the SQL lifetime check's answer, the one the guard
    and the worker use, so the page cannot disagree with them.
    """
    from django.db import connection

    from .accounts.policy_models import PortalUser
    from .source.drop_models import SourceDropAcceptance

    if attempt_id is None:
        return None, None
    row = (
        SourceDropAcceptance.objects.filter(attempt_id=attempt_id)
        .values("id", "created_at", "expires_at", "actor_id")
        .first()
    )
    if row is None:
        return None, None
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_source_drop_acceptance_active_v1()")
        (live,) = cursor.fetchone()
    actor_id = row.pop("actor_id")
    status = DropAcceptanceStatus(**row, live=live == row["id"])
    if not status.live:
        return status, None
    actor = (
        PortalUser.objects.filter(pk=actor_id).values_list("email", flat=True).first()
    )
    return status, actor


def _backup(configuration):
    """The newest backup: when it finished, whether it used the configured key,
    its size in bytes (database and files) and the version that took it."""
    from parishkit.config import ConfigError

    from .accounts.backup_key import configured_key
    from .backup_sealing import SealError, parse_public_key
    from .jobs.backup_models import BackupRun

    newest = (
        BackupRun.objects.order_by("-completed_at", "-id")
        .values_list(
            "completed_at",
            "recipient_fingerprint",
            "database_bytes",
            "files_bytes",
            "application_version",
        )
        .first()
    )
    if newest is None:
        return None, None, None, None
    size, version = newest[2] + newest[3], newest[4]
    record = configured_key(configuration)
    if record is None:
        return newest[0], None, size, version
    try:
        configured = parse_public_key(record["values"]["settings"]["public_key"])
    except (SealError, KeyError, TypeError):
        # The applied configuration was validated when it was saved, so a key
        # that cannot be read is a damaged configuration: the page answers
        # 503 (a ConfigError), never 400, which would blame the request.
        raise ConfigError("The configured backup key cannot be read.") from None
    return newest[0], configured.fingerprint == newest[1], size, version


@dataclass(frozen=True)
class BackupRequestStatus:
    """The newest Take a backup now request, and how the page words it.

    ``status`` is ``waiting``, ``held`` (a bulk Family send holds it),
    ``not_picked_up`` (it waited 30 minutes without being claimed),
    ``running``, ``did_not_finish`` (running for more than two hours, or
    settled so), ``finished``, ``failed`` or ``restored`` (expired by a
    restore): the stored state, read with the lapse rules request mode
    applies, so the page is right even before the next poll records them.
    """

    state: str
    status: str
    created_at: datetime
    held_at: datetime | None
    claimed_at: datetime | None
    finished_at: datetime | None
    failure_kind: str | None


def request_status(row, now):
    """Word a request row's state as the page shows it, at ``now``."""
    state = row["state"]
    if state == "waiting":
        since = max(row["created_at"], row["held_at"] or row["created_at"])
        if now - since > timedelta(minutes=30):
            return "not_picked_up"
        return "held" if row["held_at"] else "waiting"
    if state == "running" and now - row["claimed_at"] > timedelta(hours=2):
        return "did_not_finish"
    if state == "failed" and row["failure_kind"] == "did_not_finish":
        return "did_not_finish"
    if state == "expired":
        # A restore marks requests expired as of their own creation (the
        # backup runbook's restore step); request mode expires one only once
        # it has waited 30 minutes, which the page words as not picked up.
        if row["finished_at"] <= row["created_at"]:
            return "restored"
        return "not_picked_up"
    return state


def _backup_request(now):
    """The newest backup request's status, or None when there is none."""
    from .jobs.backup_models import BackupRequest

    row = (
        BackupRequest.objects.order_by("-created_at", "-id")
        .values(
            "state",
            "created_at",
            "held_at",
            "claimed_at",
            "finished_at",
            "failure_kind",
        )
        .first()
    )
    if row is None:
        return None
    return BackupRequestStatus(status=request_status(row, now), **row)


@dataclass(frozen=True)
class DebugSwitchStatus:
    """The newest debug-off switch (ADM-13 PR 6, #530): when it was set, and
    when and by whom (``operator`` or ``administrator``) it was cleared.

    Who set it is not part of the read model's document: the page shows the
    Administrator's address from ``read_health``'s page values.
    """

    set_at: datetime
    cleared_at: datetime | None
    cleared_by: str | None

    @property
    def active(self):
        """Whether the switch is set now (not yet cleared)."""
        return self.cleared_at is None


def _debug_switch():
    """The newest debug-off switch and the address of who set it.

    ``(None, None)`` when no switch was ever set. The address is read only
    while the switch is set, the only time the page names who set it.
    """
    from .accounts.policy_models import PortalUser
    from .jobs.debug_switch_models import DebugOffSwitch

    row = (
        DebugOffSwitch.objects.order_by("-set_at", "-id")
        .values("set_at", "cleared_at", "cleared_by", "actor_id")
        .first()
    )
    if row is None:
        return None, None
    actor_id = row.pop("actor_id")
    status = DebugSwitchStatus(**row)
    if not status.active:
        return status, None
    actor = (
        PortalUser.objects.filter(pk=actor_id).values_list("email", flat=True).first()
    )
    return status, actor


def _offsite(configuration):
    """The off-site copy status Home shows, or None when copies are not set up."""
    from .accounts.backup_destination import offsite_status

    kinds = {
        record["values"]["kind"]
        for record in configuration.active_configuration.canonical_document[
            "sections"
        ].get("integrations", [])
    }
    return offsite_status() if "backup" in kinds else None


def _pause(campaign):
    """Who paused live delivery and why, for the page only.

    The reason is what an Administrator typed and the actor is an email
    address, so neither is part of the read model's document.
    """
    from .accounts.policy_models import PortalUser

    return {
        "reason": campaign.pause_reason,
        "actor": PortalUser.objects.filter(pk=campaign.pause_actor_id)
        .values_list("email", flat=True)
        .first(),
    }


def _workgroup_missing(campaign):
    """The campaign's Reminder WorkGroup name when ParishSoft has none by it.

    None when the campaign names no WorkGroup, when the newest refresh found
    it, or when no refresh has read the current name yet (#861).
    """
    from .source.workgroups import current_evidence

    if campaign is None:
        return None
    name, evidence = current_evidence(campaign.active_configuration.values)
    return name if evidence is not None and not evidence["found"] else None


def _processes(rows, now):
    """The process lines, and the core services that have not reported."""
    processes = group_processes(rows, now)
    reported = {line.service for line in processes}
    return processes, tuple(
        service for service in CORE_SERVICES if service not in reported
    )


def home_problems(configuration, now, refresh):
    """The System health problems, for the lines on Home (ADM-13 PR 2b).

    ``refresh`` is the ``FullRefreshStatus`` Home read with ``health=True``,
    which carries ``HEALTH_SQL``'s facts from the same statement, so the
    lines cost Home no query of their own. Returns the same ``Problem``
    values the page lists, in its order.
    """
    facts = refresh.health
    processes, missing = _processes(facts.rows, now)
    return find_problems(
        mode=configuration.mode,
        processes=processes,
        missing=missing,
        schema_current=facts.schema_current,
        incidents=facts.incidents,
        refresh=refresh,
        backup_at=facts.backup_at,
    )


def read_health(store):
    """Read the System health model in one read-only snapshot.

    Returns ``(model, page)``, where ``page`` holds what only the page shows
    (the current campaign's identifier for its links, who paused delivery
    and why, who set the debug-off switch and who accepted the refused
    change), or None while a restore is
    under review, when the page is withheld. The caller authorizes the
    reader before and after (the page's views).
    """
    from .accounts.configuration_installation import coherent_configuration
    from .accounts.sessions import database_now
    from .campaigns.models import Campaign
    from .campaigns.work_locks import read_transaction
    from .jobs.family_mail_dispatch import (
        DAILY_SEND_LIMIT,
        RESERVED_SENDS,
        daily_resume,
        daily_sends,
    )
    from .jobs.send_reads import read_progress
    from .source.refresh_status import full_refresh_status, refresh_schedule

    with read_transaction():
        configuration = coherent_configuration(store)
        if configuration.restore_review_required:
            return None
        campaign = None
        if configuration.current_campaign_id is not None:
            # refresh_schedule reads the campaign's zone from its settings.
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=configuration.current_campaign_id
            )
            configuration.current_campaign = campaign
        now = database_now()
        facts = _facts()
        incidents, schema_current = facts.incidents, facts.schema_current
        processes, missing = _processes(facts.rows, now)
        refresh = full_refresh_status(refresh_schedule(configuration), now)
        refused_at, refused_counts, refused_attempt = _refused(refresh.succeeded_at)
        drop_acceptance, accept_actor = _drop_acceptance(refused_attempt)
        backup_at, key_matches, backup_bytes, backup_version = _backup(configuration)
        backup_request = _backup_request(now)
        retry_waiting, next_retry_at = _retries()
        daily_sent = daily_sends()
        daily_resume_at = daily_resume()
        progress = read_progress()
        send = None if progress is None else progress["send"]
        production = configuration.mode == "production"
        paused = bool(production and campaign and campaign.delivery_paused)
        offsite = _offsite(configuration)
        debug_switch, debug_actor = _debug_switch()
        page = {
            "campaign_id": None if campaign is None else campaign.pk,
            "pause": _pause(campaign) if paused else None,
            "workgroup_missing": _workgroup_missing(campaign),
            "debug_actor": debug_actor,
            "accept_actor": accept_actor,
        }
    model = SystemHealth(
        checked_at=now,
        mode=configuration.mode,
        problems=find_problems(
            mode=configuration.mode,
            processes=processes,
            missing=missing,
            schema_current=schema_current,
            incidents=incidents,
            refresh=refresh,
            backup_at=backup_at,
        ),
        processes=processes,
        missing_services=missing,
        versions=tuple(sorted({line.version for line in processes if line.running})),
        schema_current=schema_current,
        delivery_paused=paused,
        paused_at=campaign.paused_at if paused else None,
        send_active=bool(send and send.active),
        planning_held=bool(send and send.active and send.counts.held),
        retry_waiting=retry_waiting,
        next_retry_at=next_retry_at,
        daily_sent=daily_sent,
        # The share Family invitations, reminders and tests may use: the daily
        # limit less the sends kept for receipts, reports and alerts, as
        # family_mail_dispatch.over_daily_limit compares it.
        daily_limit=DAILY_SEND_LIMIT - RESERVED_SENDS,
        daily_resume_at=daily_resume_at,
        incidents=incidents,
        refresh=refresh,
        refused_at=refused_at,
        refused_counts=refused_counts,
        backup_at=backup_at,
        backup_key_matches=key_matches,
        backup_bytes=backup_bytes,
        backup_version=backup_version,
        backup_request=backup_request,
        offsite=offsite,
        debug_switch=debug_switch,
        drop_acceptance=drop_acceptance,
    )
    return model, page


# ------------------------------------------------------- take a backup now

# A request in these page statuses is live: another cannot be made.
LIVE_REQUEST = frozenset({"waiting", "held", "running"})


@dataclass(frozen=True)
class BackupPreview:
    """What Take a backup now's preview states before the Administrator confirms.

    ``key`` becomes the request's identity, so a repeated confirmation
    returns the first request instead of making a second. ``backup_at`` and
    ``backup_bytes`` describe the newest backup, ``offsite`` whether
    off-site copies are set up, and ``held`` whether a bulk Family send is
    in progress, so the backup would wait for it.
    """

    key: object
    backup_at: datetime | None
    backup_bytes: int | None
    offsite: bool
    held: bool


def _admit_action(caller, store):
    """An enabled Administrator who may view System health and configure.

    Returns ``(principal, signed-in instant)``; the instant must be within
    the fresh sign-in window (``sessions.require_fresh``), or the caller
    gets the "Confirm with Google" step-up.
    """
    from .accounts.sessions import require_fresh

    return _admit_requester(caller, store), require_fresh(caller)


def _refusal(now):
    """Why a backup cannot be requested now, as a stored request, or None."""
    request = _backup_request(now)
    if request is not None and request.status in LIVE_REQUEST:
        return request
    return None


def preview_backup(caller, store):
    """Take a backup now's preview, for a freshly signed-in Administrator.

    Refused (``StaleRecordError``) while a request is waiting or running.
    Reads only what the page reads; changes nothing.
    """
    from uuid import uuid4

    from .accounts.configuration_installation import coherent_configuration
    from .accounts.sessions import database_now
    from .campaigns.work_locks import read_transaction
    from .source.send_hold import family_send_active
    from .storage import StaleRecordError

    _admit_action(caller, store)
    with read_transaction():
        configuration = coherent_configuration(store)
        if configuration.restore_review_required:
            raise StaleRecordError("No backup request during a restore review.")
        now = database_now()
        if _refusal(now) is not None:
            raise StaleRecordError("A backup request is already waiting or running.")
        backup_at, _, backup_bytes, _ = _backup(configuration)
        return BackupPreview(
            key=uuid4(),
            backup_at=backup_at,
            backup_bytes=backup_bytes,
            offsite=_offsite(configuration) is not None,
            held=family_send_active(),
        )


class ActionRefused(PermissionError):
    """The database refused a System health action for a reason other than a
    stale sign-in (a missing grant, another login): the reader is told
    plainly, and the System logs record why. The step-up would not help."""


class BackupRequestRefused(ActionRefused):
    """The database refused a backup request (see ``ActionRefused``)."""


class DebugOffRefused(ActionRefused):
    """The database refused to set the debug-off switch (see ``ActionRefused``)."""


# The guards' words for a sign-in outside the fresh window (0062, 0078): the
# only permission refusal a "Confirm it's you" step-up can resolve.
FRESH_REFUSAL = "requires fresh Administrator authentication"


def _admit_requester(caller, store):
    """An enabled Administrator who may view System health and configure."""
    from .accounts.policy import Capability, allows
    from .accounts.sessions import authenticated_admin

    actor = authenticated_admin(caller, store=store, activity=True)
    if not (
        allows(actor, Capability.SYSTEM_LOGS) and allows(actor, Capability.CONFIGURE)
    ):
        raise PermissionError("System health's actions require an Administrator.")
    return actor


@dataclass(frozen=True)
class _FreshRow:
    """What differs between the fresh-gated actions ``_record_fresh`` records:
    the row's model, its audit events (the page's and the command's) and
    the words of each refusal."""

    model: object
    action: object
    command_action: object
    other_actor: str
    busy: str
    refused: type


def _record_fresh(caller, store, kind, *, key, command, values=None, then=None):
    """Record one fresh-gated action row; return ``(row, created)``.

    Shared by Take a backup now, Accept this change once and Turn off debug
    logging, whose rows' SQL guards make the same checks. The fresh sign-in
    check runs inside the row's transaction, so an automation session's gate
    (``sessions.require_fresh``, which locks the session row and records its
    gate event) commits or rolls back with the row. The guard checks again,
    in the same transaction, that the sign-in is fresh (or a live full-scope
    automation session's) and its own one-at-a-time rule. A repeated
    confirmation with the same ``key`` returns the first row (``created``
    false). ``kind.action`` is recorded with the row as its subject, and
    from the command line (``command``) also ``kind.command_action`` naming
    the automation session. ``values`` are the row's own further columns
    (an acceptance's refusal and reviewed counts). ``then(row, actor)``,
    when given, runs after those events in the same transaction, only for a
    new row, so whatever it records commits or rolls back with the row (an
    acceptance acknowledges its refusal's critical problem).
    """
    from django.db import DatabaseError, IntegrityError, transaction

    from .accounts.sessions import FreshAuthenticationRequired, require_fresh
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action
    from .observability import emit_failure
    from .storage import StaleRecordError

    actor = _admit_requester(caller, store)
    session = caller.portal_session

    def first():
        """The row already recorded with this key, or None."""
        previous = kind.model.objects.filter(pk=key).first()
        if previous is not None and previous.actor_id != actor.identity:
            raise PermissionError(kind.other_actor)
        return previous

    try:
        with transaction.atomic():
            previous = first()
            if previous is not None:
                return previous, False
            fresh = require_fresh(caller)
            row = kind.model.objects.create(
                id=key,
                actor_id=actor.identity,
                session_id=session.pk,
                authenticated_at=fresh,
                **(values or {}),
            )
            record_action(
                kind.action,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                subject_id=row.pk,
                context={"outcome": Outcome.SUCCEEDED},
            )
            if command:
                record_action(
                    kind.command_action,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
            if then is not None:
                then(row, actor)
            return row, True
    except IntegrityError as error:
        # A simultaneous second click with the same key lost the race: it
        # returns the first row. Otherwise the guard's one-at-a-time (or,
        # for a backup, restore-review) refusal.
        previous = first()
        if previous is not None:
            return previous, False
        raise StaleRecordError(kind.busy) from error
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "42501":
            raise
        if FRESH_REFUSAL in str(error):
            # The sign-in lapsed since the check above: the step-up helps.
            raise FreshAuthenticationRequired(
                "Please authenticate with Google again."
            ) from error
        # Any other permission refusal (a missing grant, the wrong login)
        # is not the reader's to fix by signing in again: log it and say so.
        emit_failure(error)
        raise kind.refused(
            "The server refused this action; the System logs say why."
        ) from error


def _backup_kind():
    """Take a backup now's row, events and refusals, for ``_record_fresh``."""
    from .audit.schemas import Action
    from .jobs.backup_models import BackupRequest

    return _FreshRow(
        model=BackupRequest,
        action=Action.BACKUP_REQUESTED,
        command_action=Action.ADMIN_CMD_SYSTEM_BACKUP_NOW,
        other_actor="This backup request was made by another Administrator.",
        busy="A backup request is already waiting or running.",
        refused=BackupRequestRefused,
    )


def request_backup(caller, store, *, key, command=False):
    """Record one Take a backup now request; return ``(row, created)``.

    ``_record_fresh`` checks the fresh sign-in in the request's transaction
    and records ``backup_requested`` (and, from the command line
    (``command``), ``admin_cmd_system_backup_now``). The request's guard
    also refuses one while another is live or a restore is under review.
    """
    return _record_fresh(caller, store, _backup_kind(), key=key, command=command)


@dataclass(frozen=True)
class BackupNow(ReadModel):
    """``system backup-now``'s result: the request it recorded (or the one
    an earlier run with the same ``--request-key`` recorded), as System
    health's Backups panel words it.

    ``status`` is the panel's word for it (``waiting`` until the server's
    five-minute check picks it up); ``created`` is false for a repeat.
    """

    request_id: object
    created: bool
    status: str
    created_at: str | None


def backup_preview_lines(store, *, key=None):
    """What Take a backup now's preview says, as plain lines for the
    command line's confirmation prompt. Reads only what the page reads and
    refuses (``StaleRecordError``) as the page's preview does. A repeat (a
    request already recorded with ``key``) needs no preview: it returns
    None, and ``request_backup`` returns that first request."""
    from django.template.defaultfilters import filesizeformat

    from .accounts.configuration_installation import coherent_configuration
    from .accounts.sessions import database_now
    from .campaigns.work_locks import read_transaction
    from .jobs.backup_models import BackupRequest
    from .source.send_hold import family_send_active
    from .storage import StaleRecordError

    with read_transaction():
        if key is not None and BackupRequest.objects.filter(pk=key).exists():
            return None
        configuration = coherent_configuration(store)
        if configuration.restore_review_required:
            raise StaleRecordError("No backup request during a restore review.")
        if _refusal(database_now()) is not None:
            raise StaleRecordError("A backup request is already waiting or running.")
        backup_at, _, backup_bytes, _ = _backup(configuration)
        offsite = _offsite(configuration) is not None
        held = family_send_active()
    lines = [
        "Ask the server to take a backup now, of the same kind as the scheduled ones."
    ]
    if backup_at is None:
        lines.append("No backup has been recorded yet.")
    else:
        size = (
            "" if backup_bytes is None else f" and was {filesizeformat(backup_bytes)}"
        )
        lines.append(f"The last backup finished at {plain(backup_at)}{size}.")
    lines.append(
        "It will also be copied off-site."
        if offsite
        else "Off-site copies are not set up, so it stays on the server only."
    )
    lines.append(
        "It follows the usual retention rules and adds to the server's disk use."
    )
    if held:
        lines.append(
            "A Family email send is in progress, so the backup waits until it finishes."
        )
    return tuple(lines)


def backup_now(caller, service, *, key, context):
    """``system backup-now``: Take a backup now, as System health's button.

    The command has already shown ``backup_preview_lines`` and had them
    confirmed. ``request_backup`` checks the fresh sign-in (a full-scope
    automation session's, which the request's guard accepts too) inside the
    request's transaction and records ``backup_requested`` with the
    command's own ``admin_cmd_system_backup_now``. ``key`` (the command's
    ``--request-key``) is the request's identity, so a repeat returns the
    first request. ``context["committed"]`` is set once it may have
    committed (exit 6 then). Returns the request as the panel words it.
    """
    from django.db import DatabaseError

    from .accounts.sessions import database_now
    from .observability import _guard_refusal

    try:
        row, created = request_backup(caller, service.store, key=key, command=True)
    except DatabaseError as error:
        # A failure at the commit itself may have committed.
        if not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    status = request_status(
        {
            "state": row.state,
            "created_at": row.created_at,
            "held_at": row.held_at,
            "claimed_at": row.claimed_at,
            "finished_at": row.finished_at,
            "failure_kind": row.failure_kind,
        },
        database_now(),
    )
    return BackupNow(
        request_id=row.pk,
        created=created,
        status=status,
        created_at=plain(row.created_at),
    )


# ------------------------------------------------- turn off debug logging


@dataclass(frozen=True)
class DebugOffPreview:
    """What Turn off debug logging's preview states before the Administrator
    confirms (ADM-13 PR 6b).

    ``key`` becomes the switch's identity, so a repeated confirmation
    returns the first switch instead of being refused as already set.
    ``processes`` names the running processes with debug logging in effect.
    """

    key: object
    processes: tuple


def _debug_processes():
    """The running processes with debug logging in effect, in page order."""
    from .accounts.sessions import database_now

    processes, _ = _processes(_facts().rows, database_now())
    return tuple(line for line in processes if line.running and line.debug_logging)


def _switch_set():
    """Whether a debug-off switch is set now."""
    from .jobs.debug_switch_models import DebugOffSwitch

    return DebugOffSwitch.objects.filter(cleared_at__isnull=True).exists()


# The words of the refusal while a switch is set, from the page and the
# command line alike.
SWITCH_SET = "The debug-off switch is already set."


def preview_debug_off(caller, store):
    """Turn off debug logging's preview, for a freshly signed-in Administrator.

    Refused (``StaleRecordError``) while the switch is set. Reads only what
    the page reads; changes nothing. Offered in Testing and Production
    alike (System health decision 3).
    """
    from uuid import uuid4

    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    _admit_action(caller, store)
    with read_transaction():
        if _switch_set():
            raise StaleRecordError(SWITCH_SET)
        return DebugOffPreview(
            key=uuid4(), processes=tuple(line.label for line in _debug_processes())
        )


def _debug_kind():
    """Turn off debug logging's row, events and refusals, for ``_record_fresh``."""
    from .audit.schemas import Action
    from .jobs.debug_switch_models import DebugOffSwitch

    return _FreshRow(
        model=DebugOffSwitch,
        action=Action.DEBUG_LOGGING_TURNED_OFF,
        command_action=Action.ADMIN_CMD_SYSTEM_DEBUG_OFF,
        other_actor="This debug-off switch was set by another Administrator.",
        busy=SWITCH_SET,
        refused=DebugOffRefused,
    )


def turn_off_debug(caller, store, *, key, command=False):
    """Set the debug-off switch; return ``(row, created)``.

    ``_record_fresh`` checks the fresh sign-in in the switch's transaction
    and records ``debug_logging_turned_off`` (and, from the command line
    (``command``), ``admin_cmd_system_debug_off``). The switch's guard
    (0078) also refuses one while another is set. Every process that
    reaches the database then turns its own debug logging off
    (``debug_switch``) on its next connection or status record.
    """
    return _record_fresh(caller, store, _debug_kind(), key=key, command=command)


@dataclass(frozen=True)
class DebugOff(ReadModel):
    """``system debug-off``'s result: the switch it set (or the one an
    earlier run with the same ``--request-key`` set).

    ``created`` is false for a repeat; ``active`` is whether that switch is
    still set (a repeat after the server operator cleared it says false).
    """

    switch_id: object
    created: bool
    set_at: str | None
    active: bool


def debug_off_preview_lines(store, *, key=None):
    """What Turn off debug logging's preview says, as plain lines for the
    command line's confirmation prompt. Refuses (``StaleRecordError``) as
    the page's preview does. A repeat (a switch already set with ``key``)
    needs no preview: it returns None, and ``turn_off_debug`` returns that
    first switch."""
    from .campaigns.work_locks import read_transaction
    from .jobs.debug_switch_models import DebugOffSwitch
    from .storage import StaleRecordError

    with read_transaction():
        if key is not None and DebugOffSwitch.objects.filter(pk=key).exists():
            return None
        if _switch_set():
            raise StaleRecordError(SWITCH_SET)
        processes = _debug_processes()
    lines = [
        "Turn debug logging off in every service now, until the server "
        "operator clears the debug-off switch."
    ]
    if processes:
        names = ", ".join(line.label for line in processes)
        lines.append(f"Debug logging is on in: {names}.")
    else:
        lines.append("No service reports debug logging right now.")
    lines += [
        "Lines already written stay in the logs, and so do lines a service "
        "writes when it starts, before it first reads the switch.",
        "The setting on the server still says on until the server operator "
        "recreates the services without it.",
        "Only the server operator can clear the switch, offline, with "
        "pk-stewardship debug-off-clear.",
    ]
    return tuple(lines)


def debug_off(caller, service, *, key, context):
    """``system debug-off``: Turn off debug logging, as System health's button.

    The command has already shown ``debug_off_preview_lines`` and had them
    confirmed. ``turn_off_debug`` checks the fresh sign-in (a full-scope
    automation session's, which the switch's guard accepts too) inside the
    switch's transaction and records ``debug_logging_turned_off`` with the
    command's own ``admin_cmd_system_debug_off``. ``key`` (the command's
    ``--request-key``) is the switch's identity, so a repeat returns the
    first switch. ``context["committed"]`` is set once it may have
    committed (exit 6 then).
    """
    from django.db import DatabaseError

    from .observability import _guard_refusal

    try:
        row, created = turn_off_debug(caller, service.store, key=key, command=True)
    except DatabaseError as error:
        # A failure at the commit itself may have committed.
        if not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return DebugOff(
        switch_id=row.pk,
        created=created,
        set_at=plain(row.set_at),
        active=row.cleared_at is None,
    )


# ------------------------------------------------- accept this change once

# The signed preview binding's salt and lifetime. The binding names the
# refusal and its counts as reviewed, so a confirmation can only accept
# what the Administrator saw; an older one is reviewed again. Its lifetime
# is the shared preview lifetime (admin-portal spec, "Preview lifetimes"):
# web.refusals.PREVIEW_MAX_AGE, not imported here because this module must
# stay importable before Django is set up; a test pins the two together.
ACCEPT_SALT = "parishkit.stewardship.system_health.accept"
ACCEPT_MAX_AGE = 15 * 60
# What a step answers while the refusal shown cannot be accepted any more
# (another refusal arrived, a full refresh promoted, it was accepted): the
# page says "This changed since you looked; review it again".
ACCEPT_CHANGED = "The refusal changed since it was reviewed."


@dataclass(frozen=True)
class AcceptPreview:
    """What Accept this change once's preview states before the Administrator
    ticks the box and confirms (ADM-13 PR 5b).

    ``token`` is the signed binding the confirmation posts: the new
    acceptance's key, the refused attempt and every recorded count as
    reviewed. ``counts`` are the refusal's ``DropCount`` values, in the
    panel's order; counts only, no parish data.
    """

    token: str
    attempt_id: object
    refused_at: datetime
    counts: tuple

    @property
    def failed(self):
        """The counts that fell too far."""
        return tuple(count for count in self.counts if count.failed)


class AcceptanceRefused(ActionRefused):
    """The database refused an acceptance (see ``ActionRefused``)."""


def _open_refusal():
    """The newest refusal, when it can be accepted now: ``(attempt, at,
    counts)``. Raises ``StaleRecordError`` (``ACCEPT_CHANGED``) when there
    is none, or it was accepted, or its acceptance lapsed, or it was an
    empty load. Runs in the caller's read snapshot; the guard checks again.

    "The newest refusal" is ``_refused``'s, as the page's panel shows it:
    the newest recorded drop count's attempt, unless a full refresh was
    promoted at or after it. The page passes the refresh status's last full
    success, which is that same promotion; here it is read directly
    (``_last_full_promotion``), so the preview needs no refresh status. The
    SQL lifetime check and guard use the same rule.
    """
    from .storage import StaleRecordError

    refused_at, counts, attempt = _refused(_last_full_promotion())
    acceptance, _ = _drop_acceptance(attempt)
    if accept_state(counts, acceptance) != "open":
        raise StaleRecordError(ACCEPT_CHANGED)
    return attempt, refused_at, counts


def _last_full_promotion():
    """When the newest full refresh was promoted, or None."""
    from django.db.models import Max

    from .source.snapshot_models import SourceSnapshot

    return SourceSnapshot.objects.filter(kind="full", state="promoted").aggregate(
        at=Max("promoted_at")
    )["at"]


def accept_state(counts, acceptance):
    """Whether a refusal with these recorded ``counts`` and ``acceptance``
    (a ``DropAcceptanceStatus`` or None) can be accepted once, or why not;
    see ``SystemHealth.accept_state``. The page's button and the preview's
    check share it."""
    if not any(count.failed for count in counts):
        return "none"
    if acceptance is not None:
        return "accepted" if acceptance.live else "lapsed"
    if any(
        count.measure in {"family", "member"} and count.after == 0 for count in counts
    ):
        return "empty"
    return "open"


def _sign(actor, key, attempt, counts):
    """The preview binding for ``actor``'s acceptance ``key`` of ``attempt``."""
    from django.core import signing

    from .source.drop_acceptance import reviewed_counts

    return signing.dumps(
        {
            "key": str(key),
            "actor": str(actor.identity),
            "attempt": str(attempt),
            "counts": reviewed_counts(counts),
        },
        salt=ACCEPT_SALT,
    )


def _signer(caller, store):
    """Who is confirming, read only to check the binding is theirs; the
    action's own admission (``_record_fresh``) checks the Administrator."""
    from .accounts.sessions import authenticated_admin

    return authenticated_admin(caller, store=store, read_only=True)


def _unsign(actor, token):
    """The binding's ``(key, attempt, counts)``, for ``actor`` only.

    An expired binding is ``StaleRecordError`` (review it again); an altered
    one, or another Administrator's, is ``ValueError``.
    """
    from uuid import UUID

    from django.core import signing

    from .storage import StaleRecordError

    try:
        value = signing.loads(token, salt=ACCEPT_SALT, max_age=ACCEPT_MAX_AGE)
    except signing.SignatureExpired:
        raise StaleRecordError(ACCEPT_CHANGED) from None
    except (signing.BadSignature, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    try:
        key, attempt = UUID(value["key"]), UUID(value["attempt"])
        if value["actor"] != str(actor.identity) or type(value["counts"]) is not dict:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    return key, attempt, value["counts"]


def _accept_preview(actor):
    """The preview of accepting the newest refusal, in one read snapshot."""
    from uuid import uuid4

    from .campaigns.work_locks import read_transaction

    with read_transaction():
        attempt, refused_at, counts = _open_refusal()
    return AcceptPreview(
        token=_sign(actor, uuid4(), attempt, counts),
        attempt_id=attempt,
        refused_at=refused_at,
        counts=counts,
    )


def preview_acceptance(caller, store):
    """Accept this change once's preview, for a freshly signed-in
    Administrator (the page's step). Changes nothing.

    Refused (``StaleRecordError``) while the newest refusal cannot be
    accepted: none is shown, it was accepted already (live or lapsed), or
    it was a load with no Families or no Members.
    """
    actor, _ = _admit_action(caller, store)
    return _accept_preview(actor)


def _accept_kind():
    """Accept this change once's row, events and refusals, for ``_record_fresh``."""
    from .audit.schemas import Action
    from .source.drop_models import SourceDropAcceptance

    return _FreshRow(
        model=SourceDropAcceptance,
        action=Action.SOURCE_DROP_ACCEPTED,
        command_action=Action.ADMIN_CMD_SYSTEM_REFRESH_ACCEPT,
        other_actor="This change was accepted by another Administrator.",
        # Every guard refusal that is not about the sign-in (the refusal is
        # no longer the newest, a full refresh promoted, the counts differ,
        # another acceptance is live, this refusal was accepted before).
        busy=ACCEPT_CHANGED,
        refused=AcceptanceRefused,
    )


def _request_refresh(caller, service, key):
    """Ask for a full refresh after an acceptance, as **Refresh now** does.

    The command is keyed by the acceptance's own key, so a repeated
    confirmation joins the same request. Returns the run's root task, or
    None when the request could not be recorded now, for any reason (the
    ParishSoft settings, the Administrator's access, the database): it runs
    only after the acceptance committed, which stands either way, and the
    next full refresh, scheduled or asked for, uses it. So every failure is
    logged and absorbed here, and the page and the command report the
    acceptance instead of a refusal that would read as "nothing accepted".
    """
    from .accounts.refresh_views import request_manual_refresh
    from .accounts.sessions import authenticated_admin
    from .observability import emit_failure

    try:
        actor = authenticated_admin(caller, store=service.store, read_only=True)
        receipt = request_manual_refresh(caller, service, actor, key)
    except Exception as error:  # noqa: BLE001 - the acceptance already stands
        emit_failure(error, level=logging.WARNING, name_class=True)
        return None
    return receipt.task_root_id


def _refusal_events(attempt_id):
    """The CRITICAL ``source_destructive_change`` log entries the refusal of
    ``attempt_id`` recorded: the worker writes one, naming the refused
    task, in the refusal's own transaction (``source.failures``). Ids only;
    whether each is still inside the banner's window and unacknowledged is
    ``critical_events.acknowledge``'s to check."""
    from .audit.models import OperationalLog
    from .observability import Event
    from .source.refresh_models import SourceRefreshAttempt

    task = (
        SourceRefreshAttempt.objects.filter(pk=attempt_id)
        .values_list("task_id", flat=True)
        .first()
    )
    if task is None:
        return []
    return list(
        OperationalLog.objects.filter(
            level="CRITICAL",
            event=Event.SOURCE_DESTRUCTIVE_CHANGE.value,
            context__task_id=str(task),
        ).values_list("id", flat=True)
    )


def _acknowledge_refusal(service, actor, row):
    """Acknowledge, for ``actor``, the critical problem the accepted refusal
    raised, inside the acceptance's transaction (``_record_fresh``'s
    ``then``).

    Accepting the change once is the Administrator's answer to that
    problem, so the critical-problems banner should not ask for it again.
    Only that refusal's own entries are acknowledged, through the banner's
    shared ``acknowledge_now`` with its ``critical_events_acknowledged``
    event, whose subject is the acceptance; any other critical problem,
    an earlier refusal's included, stays until it is acknowledged itself.

    The acknowledgement is a courtesy to the banner, never a condition of
    the acceptance: a configuration refusal (a restore under review) or a
    permission refusal (SQLSTATE 42501, a missing grant) is logged and the
    acceptance stands. ``acknowledge_now`` runs in its own savepoint, so
    the failure leaves the acceptance's transaction usable; the banner
    keeps the entry for an Administrator to acknowledge by hand.
    """
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.critical_event_views import acknowledge_now
    from .observability import emit_failure

    ids = _refusal_events(row.attempt_id)
    if not ids:
        return
    try:
        acknowledge_now(service, actor, ids, subject_id=row.pk)
    except ConfigError as error:
        emit_failure(error, level=logging.WARNING, name_class=True)
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "42501":
            raise
        emit_failure(error, level=logging.WARNING, name_class=True)


def accept_change(caller, service, *, token, command=False, recorded=None):
    """Record one acceptance of the reviewed refusal and ask for a full
    refresh; return ``(row, created, refresh)``.

    ``token`` is the preview's signed binding (``_unsign``): its key becomes
    the acceptance's identity, so a repeated confirmation returns the first
    acceptance (``created`` false) and joins the same refresh. The counts
    copied are the ones the Administrator reviewed; the acceptance's guard
    (0080) refuses them unless they still equal the recorded ones and the
    refusal is still the newest with nothing accepted, which this answers
    as ``StaleRecordError`` (``ACCEPT_CHANGED``). ``_record_fresh`` checks
    the fresh sign-in in the row's transaction and records
    ``source_drop_accepted`` (and from the command line ``command``,
    ``admin_cmd_system_refresh_accept``). ``refresh`` is the requested full
    refresh's root task, or None (``_request_refresh``). ``recorded`` is
    called once the acceptance has committed, before the refresh is asked
    for (the command line marks its outcome committed there); nothing after
    that point raises. A new acceptance also acknowledges its refusal's
    critical problem in its own transaction (``_acknowledge_refusal``); a
    repeat, or a refused or expired confirmation, acknowledges nothing.
    """
    key, attempt, counts = _unsign(_signer(caller, service.store), token)
    row, created = _record_fresh(
        caller,
        service.store,
        _accept_kind(),
        key=key,
        command=command,
        values={"attempt_id": attempt, "counts": counts},
        then=lambda row, actor: _acknowledge_refusal(service, actor, row),
    )
    if recorded is not None:
        recorded()
    return row, created, _request_refresh(caller, service, key)


@dataclass(frozen=True)
class RefreshAcceptPreview(ReadModel):
    """``system refresh-accept-preview``: the page's preview of Accept this
    change once, for the newest refusal.

    ``counts`` are every recorded count (``measure``, ``before``, ``after``,
    ``limit_percent``, ``failed``), counts only; ``preview.token`` is the
    signed binding ``system refresh-accept --token`` takes, valid for 15
    minutes and for this Administrator only.
    """

    attempt_id: object
    refused_at: str
    counts: list
    preview: dict


def refresh_accept_preview(caller, service):
    """``system refresh-accept-preview``: Accept this change once's preview.

    Admits an Administrator as the page does, without the page's fresh
    sign-in: the preview changes nothing, and ``system refresh-accept``,
    which does, is fresh-gated. Refused (``stale_version``) while the newest
    refusal cannot be accepted, as the page's preview is.
    """
    actor = _admit_requester(caller, service.store)
    preview = _accept_preview(actor)
    return RefreshAcceptPreview(
        attempt_id=preview.attempt_id,
        refused_at=plain(preview.refused_at),
        counts=[_members(count) for count in preview.counts],
        preview={"token": preview.token},
    )


def accept_prompt_lines(service, token, caller):
    """What ``system refresh-accept`` asks the Administrator to confirm: the
    counts that fell too far, as the page's preview lists them, and the
    page's tick box. Checks the token first, so a bad one is refused before
    the prompt."""
    _, _, counts = _unsign(_signer(caller, service.store), token)
    lines = ["Accept this large ParishSoft change once:"]
    for measure, value in sorted(counts.items()):
        if value.get("failed"):
            lines.append(
                f"{measure}: {value.get('before')} before, {value.get('after')} after"
            )
    lines += [
        "Records missing from ParishSoft leave the campaign's data after the "
        "next full refresh.",
        "I checked this in ParishSoft, and the change is real.",
    ]
    return tuple(lines)


@dataclass(frozen=True)
class RefreshAccept(ReadModel):
    """``system refresh-accept``'s result: the acceptance it recorded (or the
    one an earlier run with the same token recorded) and the full refresh
    it asked for.

    ``created`` is false for a repeat; ``live`` is whether that acceptance
    is still in effect; ``task_root_id`` is the requested full refresh's
    root task, or null when the request could not be recorded (the next
    full refresh uses the acceptance anyway).
    """

    acceptance_id: object
    created: bool
    created_at: str
    expires_at: str
    live: bool
    task_root_id: object


def refresh_accept(caller, service, *, token, context):
    """``system refresh-accept``: Accept this change once, as the page's
    confirmation (``accept_change``), with ``admin_cmd_system_refresh_accept``.

    ``context["committed"]`` is set once the acceptance may have committed
    (exit 6 then): as soon as ``_record_fresh`` returns, or on a database
    failure that may have been the commit itself.
    """
    from django.db import DatabaseError, connection

    from .observability import _guard_refusal

    def recorded():
        """The acceptance committed: any later error is outcome unknown."""
        context["committed"] = True

    try:
        row, created, refresh = accept_change(
            caller, service, token=token, command=True, recorded=recorded
        )
    except DatabaseError as error:
        # A failure at the commit itself may have committed.
        if not _guard_refusal(error):
            context["committed"] = True
        raise
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_source_drop_acceptance_active_v1()")
        (live,) = cursor.fetchone()
    return RefreshAccept(
        acceptance_id=row.pk,
        created=created,
        created_at=plain(row.created_at),
        expires_at=plain(row.expires_at),
        live=live == row.pk,
        task_root_id=refresh,
    )
