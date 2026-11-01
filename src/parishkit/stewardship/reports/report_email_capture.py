"""A report send's inputs, facts, document and snapshot (#1786).

The report pipeline's ``inputs`` and ``capture`` phases (report-emails spec,
"Report pipeline", "As of", "Report snapshot"):

1. ``record_inputs`` records, once, what the send reports on: its covered
   slots, its capture instant, the as-of moment and report day derived from
   them (``report_email_inputs``), the promoted source (pinned to the
   preparation) and the submission watermark.
2. ``begin_report_facts`` builds or reuses the exact fact generation those
   inputs name, when the report has a fact block and a report day: the
   current one (today's campaign Families, #2331) its snapshot pins, and,
   for the Compared with past campaigns block, a historical one at the same
   inputs, since that block stays like for like with the archives.
3. ``load_report_document`` reads everything the blocks draw, in one owned
   effect: the facts, the funnel at the as-of moment, the comparison
   section and the open requests.
4. ``compile_report_email`` draws the email outside any lock, and
   ``retain_snapshot`` commits the snapshot with its fact pin and the next
   phase: fanout, or complete when there is nothing to send.
"""

import base64
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.db import connection

from parishkit.stewardship.campaigns.models import (
    CampaignConfiguration,
    ScheduleRevision,
)
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.campaign_mail_values import (
    campaign_values,
    document_parish,
)
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.content import (
    render_template,
    validate_admin_digest_content,
)

from .digest_comparison import section
from .digest_funnel import digest_funnel
from .documents import participation_document
from .facts import FactUnavailable, begin_fact_set, fact_inputs
from .models import CampaignDailyFactSet
from .recovery import recover_fact_set
from .report_blocks.compiler import capture_report, compile_report, has_content
from .report_document import OpenRequest, ReportDocument, report_subject
from .report_email_cohort import frozen_cohort
from .report_email_inputs import report_days, send_inputs
from .report_email_models import ReportEmailSnapshot
from .report_email_ownership import (
    TASK_TYPE,
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .retention import pin_facts

# The other owners whose exact fact generation a report send may reuse.
FACT_OWNERS = frozenset({TASK_TYPE, "daily_digest_prepare", "report_exact_export"})
# The source and fact pins of a report send name its preparation, under the
# digests' parent kind (frozen file 0156's retention triggers).
PIN_KIND = "digest"
# The population the Compared with past campaigns block counts: the
# historical cohort, like the past campaigns' archives and the Campaign
# comparison page (#2324 decision 4), whatever the email's own figures
# count (``ReportEmailPreparation.population_scope``, #2331).
COMPARISON_SCOPE = "historical"
COMPARISON_BLOCK = "compared_with_past_campaigns"

OBSERVE = """SELECT stewardship_campaign_now_v1(), c.snapshot_id,
    coalesce((SELECT max(campaign_sequence) FROM stewardship_submission
        WHERE campaign_id=%(campaign)s AND mode='live'),0),
    public.stewardship_pledge_review_watermark_v1(%(campaign)s)
    FROM stewardship_source_current c JOIN stewardship_source_snapshot s
        ON s.id=c.snapshot_id AND s.state='promoted' AND s.compacted_at IS NULL"""


def _bound(claim, phase):
    """Require one live ordered effect and its exact persisted phase."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(row)
    if row.phase != phase or row.occurrence_id is None:
        raise StorageInvariantError("Report preparation is in a different phase.")
    return row, scope


def _values(row):
    """The report's values as of the preparation's revision."""
    return ScheduleRevision.objects.values_list("values", flat=True).get(
        pk=row.revision_id
    )


def record_inputs(claim):
    """Record the send's inputs once and pin their source (inputs to capture).

    The capture instant is the database clock in this transaction, so the
    guard can check that it is this observation; the watermark is the last
    live submission committed by then, and the review watermark the pledge
    review rows by then (#2202).
    """
    row, scope = _bound(claim, "inputs")
    configuration = scope.campaign.active_configuration
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_report_email_slots_v1(%s,%s)::text",
            [row.occurrence_id, row.mode],
        )
        covered = json.loads(cursor.fetchone()[0])
        cursor.execute(OBSERVE, {"campaign": row.campaign_id})
        observed = cursor.fetchone()
    if observed is None:
        raise FactUnavailable("Report inputs need a promoted ParishSoft source.")
    captured_at, source_id, watermark, reviews = observed
    values = _values(row)
    inputs = send_inputs(
        values,
        covered,
        captured_at.astimezone(UTC),
        configuration,
        campaign_id=row.campaign_id,
        watermark=watermark,
    )

    def admit(action, source):
        """A source row lock is not permission to protect an unrelated input."""
        lock_task_claim(claim)
        current_preparation(row)
        return action == "pin" and source.pk == source_id

    pin_snapshot(source_id, parent_kind=PIN_KIND, parent_id=row.pk, admit=admit)
    return checkpoint_preparation(
        claim,
        phase="capture",
        covered_slots=covered,
        as_of_kind=values["as_of"],
        as_of=inputs.as_of,
        captured_at=captured_at,
        report_day=inputs.report_day,
        source_id=source_id,
        submission_watermark=watermark,
        review_watermark=reviews,
        timezone_configuration_id=configuration.pk,
        through_date=inputs.through_date,
    )


def report_fact_inputs(row):
    """The fact inputs a send may build: its own, and its comparison's.

    The first is the generation the snapshot pins (``fact_inputs``, today's
    campaign Families since #2331); the second the same inputs in the
    historical scope, which only the Compared with past campaigns block
    reads.
    """
    own = fact_inputs(row)
    return own, replace(own, population_scope=COMPARISON_SCOPE)


def admit_report_facts(claim, action, inputs):
    """Recheck the same recorded inputs at every calculation chunk boundary."""
    row = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(row)
    return row.phase == "capture" and inputs in report_fact_inputs(row)


def compares(row):
    """Whether the send's report draws the Compared with past campaigns block."""
    return any(entry["block"] == COMPARISON_BLOCK for entry in _values(row)["blocks"])


def begin_report_facts(claim, *, comparison=False):
    """The send's exact fact generation, built or reused; None without one.

    ``comparison`` asks for the historical generation the Compared with
    past campaigns block reads instead of the one the snapshot pins; it is
    None too when the report has no such block. An incomplete generation
    another live owner is building is left to it.
    """
    row, _scope = _bound(claim, "capture")
    if row.through_date is None or (comparison and not compares(row)):
        return None
    inputs = report_fact_inputs(row)[1 if comparison else 0]

    def admit(action, inputs):
        """Bind every calculation primitive to this send's recorded inputs."""
        return admit_report_facts(claim, action, inputs)

    facts = begin_fact_set(inputs, claim, admit=admit)
    if facts.state == "ready":
        return facts
    owner = TaskRun.objects.get(pk=facts.task_id)
    if owner.root_id != row.task_id and (
        owner.task_type not in FACT_OWNERS
        or TaskRun.objects.filter(
            root_id=owner.root_id, state__in=NONTERMINAL_STATES
        ).exists()
    ):
        raise FactUnavailable("Another report owner retains these exact inputs.")
    if (facts.task_id, facts.task_fence, facts.worker_id) != (
        claim.run_id,
        claim.fence,
        claim.worker_id,
    ):
        facts = recover_fact_set(facts.pk, claim, admit=admit)
    return facts


def open_requests(campaign_id, source_id, as_of):
    """The requests to review or to handle at ``as_of``, oldest first.

    Ignored requests are left out and each carries its stage (frozen
    function v2, #2331), as the Additional information page places it.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_report_email_requests_v2(%s,%s,%s)::text",
            [campaign_id, source_id, as_of],
        )
        rows = json.loads(cursor.fetchone()[0])
    return tuple(
        OpenRequest(
            UUID(identifier),
            duid,
            name,
            datetime.fromisoformat(submitted).astimezone(UTC),
            text,
            stage=stage,
        )
        for identifier, duid, name, submitted, text, stage in rows
    )


def comparison_for(document, projection, values, as_of_day):
    """The Compared with past campaigns section, or None (spec, its block).

    Its comparison day is the report day; for a report as of when the email
    is made, the last complete campaign day (``as_of_day`` less one), held
    at the campaign's last day. None leaves the block out.
    """
    from parishkit.stewardship.archive.imports import ArchiveRefusal, year_from_values

    from .comparison_reads import compare, digest_comparison

    if values["as_of"] == "previous_day_end":
        found = digest_comparison(document, projection)
        return None if found is None else section(found)
    last = min(as_of_day - timedelta(days=1), projection.end_date)
    if last < projection.start_date or last < document.first_date:
        return None
    try:
        year = year_from_values(projection.values or {})
    except ArchiveRefusal:
        year = None
    latest = ((last - projection.start_date).days + 1, last)
    found = compare(
        document, projection, latest, latest[0], status="current", current_year=year
    )
    return section(found) if found.imported else None


def load_report_document(claim, fact_set, comparison_set=None):
    """Read everything the report's blocks draw, from the recorded inputs only.

    Runs in one owned effect. ``fact_set`` is the ready generation from
    ``begin_report_facts`` (None without one); ``comparison_set`` the
    historical one its ``comparison`` call gave, which only the Compared
    with past campaigns block reads (None without that block).
    """
    row, scope = _bound(claim, "capture")
    values = _values(row)
    keys = {entry["block"] for entry in values["blocks"]}
    projection = CampaignConfiguration.objects.get(pk=row.timezone_configuration_id)
    version = scope.runtime.active_configuration
    participation = None
    if row.through_date is not None:
        facts = CampaignDailyFactSet.objects.get(pk=fact_set.pk, state="ready")
        if fact_inputs(facts) != fact_inputs(row):
            raise FactUnavailable("Report facts do not match their recorded inputs.")
        participation = participation_document(
            facts,
            parish_name=version.parish.name,
            browser_timezone=projection.timezone,
            requested_at=row.captured_at,
        )
    occurrence = ScheduleOccurrence.objects.get(pk=row.occurrence_id)
    inputs = send_inputs(
        values,
        row.covered_slots,
        row.captured_at,
        projection,
        campaign_id=row.campaign_id,
        watermark=row.submission_watermark,
    )
    document = ReportDocument(
        send_id=row.pk,
        name=values["name"],
        as_of=values["as_of"],
        as_of_at=row.as_of,
        campaign_timezone=projection.timezone,
        mode=row.mode,
        # With or without facts: a recovery send names its range.
        covered_dates=report_days(
            values, row.covered_slots, inputs, projection.start_date
        ),
        participation=participation,
        funnel=digest_funnel(row.campaign_id, row.mode, row.as_of)
        if "response_funnel" in keys
        else None,
        information=open_requests(row.campaign_id, row.source_id, row.as_of)
        if "additional_information" in keys
        else (),
        manual=occurrence.slot.startswith("manual:"),
        recovery=occurrence.slot.startswith("recovery:") or len(row.covered_slots) > 1,
        held=inputs.held,
        date_format=version.parish.date_format,
    )
    if COMPARISON_BLOCK in keys and comparison_set is not None:
        historical = CampaignDailyFactSet.objects.get(
            pk=comparison_set.pk, state="ready"
        )
        if fact_inputs(historical) != report_fact_inputs(row)[1]:
            raise FactUnavailable("Comparison facts differ from their recorded inputs.")
        cohort = participation_document(
            historical,
            parish_name=version.parish.name,
            browser_timezone=projection.timezone,
            requested_at=row.captured_at,
        )
        local_day = row.as_of.astimezone(ZoneInfo(projection.timezone)).date()
        document = replace(
            document,
            comparison=comparison_for(cohort, projection, values, local_day),
        )
    return document


@dataclass(frozen=True, repr=False)
class CompiledEmail:
    """A send's email as every recipient gets it, and its captured blocks."""

    subject: str
    html: str
    text: str
    images: tuple
    blocks: list
    has_content: bool


def compile_report_email(
    document, values, *, public_origin, names, copy_path=None, requests_path=None
):
    """Draw the email: introduction, blocks and subject (spec, "Compiling a report").

    ``names`` are the public placeholder values (``placeholder_values``).
    ``copy_path`` and ``requests_path`` replace the send's saved copy as
    the closing button's and the request links' targets (a Preview has no
    saved copy); by default both are the send's saved copy.
    The Administrator's subject and introduction accept only public
    placeholders, checked together before the compiler's markup is
    appended, as the digests' are.
    """
    entries = values["blocks"]
    keys = [entry["block"] for entry in entries]
    options = {entry["block"]: entry["options"] for entry in entries}
    captured = capture_report(document, entries)
    report = compile_report(
        document,
        keys,
        public_origin=public_origin,
        copy_path=copy_path or document.report_path,
        requests_path=requests_path,
        options=options,
    )
    subject = report_subject(
        render_template(values["subject"], names, subject=True),
        document,
        campaign=" ".join(names.get("campaign_name", "").split()),
        parish=" ".join(names.get("parish_name", "").split()),
    )
    intro_html = render_template(values["intro_html"], names, html=True)
    intro_text = render_template(values["intro_text"], names)
    validate_admin_digest_content(subject, intro_html, intro_text)
    return CompiledEmail(
        subject,
        intro_html + report.html,
        intro_text + ("\n\n" if intro_text else "") + report.text,
        report.images,
        captured,
        has_content(captured),
    )


def placeholder_values(scope):
    """The public campaign and parish values a report's subject and intro may use."""
    version = scope.runtime.active_configuration
    return campaign_values(
        parish=document_parish(version.canonical_document),
        campaign=scope.campaign.active_configuration.values,
    )


def retain_snapshot(claim, document, compiled, fact_set):
    """Commit the snapshot, its fact pin and the next phase together.

    The cohort is resolved now, under the active configuration. The send
    moves to fanout, or completes at once when it has nobody to send to or,
    with "Send only when there is something to report" on, nothing to
    report.
    """
    if not isinstance(document, ReportDocument) or not isinstance(
        compiled, CompiledEmail
    ):
        raise TypeError("A report snapshot requires its compiled document.")
    row, scope = _bound(claim, "capture")
    if document.send_id != row.pk:
        raise PermissionError("Report snapshot differs from its preparation.")
    values = _values(row)
    # The requests were read in an earlier effect; one handled or replaced
    # since then is drawn again on a retry rather than refused at the guard.
    listed = any(
        entry["block"] == "additional_information" for entry in values["blocks"]
    )
    if listed and document.information != open_requests(
        row.campaign_id, row.source_id, row.as_of
    ):
        raise FactUnavailable("The open requests changed while the report was drawn.")
    configuration_id = scope.runtime.active_configuration_id
    recipients = frozen_cohort(values, configuration_id)
    if (fact_set is None) != (row.through_date is None):
        raise FactUnavailable("Report snapshot requires its exact recorded facts.")
    snapshot = ReportEmailSnapshot.objects.create(
        id=uuid4(),
        preparation_id=row.pk,
        campaign_id=row.campaign_id,
        configuration_id=configuration_id,
        fact_set_id=None if fact_set is None else fact_set.pk,
        values=values,
        blocks=compiled.blocks,
        has_content=compiled.has_content,
        recipients=recipients,
        subject=compiled.subject,
        html=compiled.html,
        text=compiled.text,
        images=[
            [image.content_id, base64.b64encode(image.data).decode("ascii")]
            for image in compiled.images
        ],
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=row.pk,
    )
    if fact_set is not None:
        pin_facts(
            fact_set.pk,
            parent_kind=PIN_KIND,
            parent_id=row.pk,
            admit=lambda action, inputs: admit_report_facts(claim, action, inputs),
        )
    # Send now always sends (spec, "Sending only when there is content").
    manual = document.manual
    nothing = not recipients or (
        not compiled.has_content and values["content_required"] and not manual
    )
    checkpoint_preparation(claim, phase="complete" if nothing else "fanout")
    return snapshot
