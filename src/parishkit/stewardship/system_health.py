"""The System health page's read model (ADM-13 PR 2, #530).

One read answers "is the system healthy, and what is running?" for the
Administrator-only System health page (admin-portal spec, "System health"):
the problems list and its six panels (why sends are waiting, mail sender,
ParishSoft refresh, backups, debug logging and version). It reads one
read-only snapshot, takes no lock (in particular not the work-order lock that
sending takes) and reads only small rows and indexed counts: the service
status records, open incidents, the refresh outcomes that Home already reads,
the newest backup and off-site copy, the waiting retries and the applied
migrations. It never calls ParishSoft, Google or Slack.

``SystemHealth`` is the read model; ``to_document()`` is its defined
projection (states, counts, stored enumeration values and instants, never
a name, an address, a reason someone typed or a translated label), which the
``system health`` command will print. The page renders the same model in
words. The actions (take a backup now, clear a halt, accept a large change,
turn off debug logging) come with ADM-13 PR 3 to PR 6, each with its own
service function here.

Pure helpers (``group_processes``, ``find_problems``) take every input as an
argument, so they are unit tested without a database.
"""

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
class Problem:
    """One current problem: its stable kind, and what it names.

    ``service``, ``process`` and ``target`` name the process it is about,
    ``at`` the time the sentence states (when it began, or until when).
    """

    kind: str
    service: str | None = None
    process: str | None = None
    target: str | None = None
    at: datetime | None = None

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
        return words % {"name": self.label} if "%(name)s" in words else words

    @property
    def label(self):
        """The name of the process the problem is about, or None."""
        if self.service is None:
            return None
        return process_label(self.service, self.process, self.target)


@dataclass(frozen=True)
class DropCount:
    """One count a refused ParishSoft load was checked on."""

    measure: str
    before: int | None
    after: int
    limit_percent: int
    failed: bool

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


def _about(kind, line, at=None):
    """A problem about one process line."""
    return Problem(kind, line.service, line.process, line.target, at)


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
    problems = []
    for line in processes:
        if line.sender and line.running and line.sender_state == "halted":
            problems.append(_about("sender_halted", line, line.sender_since))
        elif line.sender and line.running and line.sender_state == "outage_paused":
            problems.append(_about("sender_outage", line, line.sender_until))
    problems.extend(
        Problem(kind, at=incidents[kind])
        for kind in MAIL_INCIDENTS
        if kind in incidents
    )
    for line in processes:
        if not line.running:
            problems.append(_about("not_running", line, line.reported_at))
    problems.extend(Problem("not_reported", service) for service in missing)
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
        problems.extend(
            _about("debug_logging", line)
            for line in processes
            if line.running and line.debug_logging
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
    ``delivery_paused`` is Production's live delivery pause; ``send_active``
    and ``planning_held`` come from Family email progress.
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
            elif item.name == "backup_request":
                value = (
                    None
                    if value is None
                    else {
                        part.name: getattr(value, part.name) for part in fields(value)
                    }
                )
            elif item.name in {"problems", "processes", "refused_counts"}:
                value = [
                    {part.name: getattr(entry, part.name) for part in fields(entry)}
                    for entry in value
                ]
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
    def debug_lines(self):
        """The running processes with debug logging in effect."""
        return tuple(
            line for line in self.processes if line.running and line.debug_logging
        )

    @property
    def announcement(self):
        """The short sentence screen readers hear when the problems change."""
        from django.utils.translation import gettext, ngettext

        if not self.problems:
            return gettext("Everything is working.")
        # The short names make the sentence change whenever the set of
        # problems does, not only when their number does, so a problem that
        # ends as another begins is still announced.
        return ngettext(
            "%(count)d problem needs attention: %(names)s.",
            "%(count)d problems need attention: %(names)s.",
            len(self.problems),
        ) % {
            "count": len(self.problems),
            "names": "; ".join(problem.summary for problem in self.problems),
        }


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
    """The newest refused load's time and counts, unless a full refresh followed.

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
        return None, ()
    rows = SourceDropCount.objects.filter(attempt_id=newest["attempt_id"]).values(
        "measure", "before", "after", "limit_percent", "failed"
    )
    counts = sorted(
        (DropCount(**row) for row in rows),
        key=lambda count: DROP_MEASURES.index(count.measure),
    )
    return newest["created_at"], tuple(counts)


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
    (the current campaign's identifier for its links, and who paused
    delivery and why), or None while a restore is under review, when the
    page is withheld. The caller authorizes the reader before and after
    (the page's views).
    """
    from .accounts.configuration_installation import coherent_configuration
    from .accounts.sessions import database_now
    from .campaigns.models import Campaign
    from .campaigns.work_locks import read_transaction
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
        refused_at, refused_counts = _refused(refresh.succeeded_at)
        backup_at, key_matches, backup_bytes, backup_version = _backup(configuration)
        backup_request = _backup_request(now)
        retry_waiting, next_retry_at = _retries()
        progress = read_progress()
        send = None if progress is None else progress["send"]
        production = configuration.mode == "production"
        paused = bool(production and campaign and campaign.delivery_paused)
        offsite = _offsite(configuration)
        page = {
            "campaign_id": None if campaign is None else campaign.pk,
            "pause": _pause(campaign) if paused else None,
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
    )
    return model, page
