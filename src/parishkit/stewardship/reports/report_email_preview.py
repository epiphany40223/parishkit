"""Preview of a report email: drawn from current data, never sent (#1786, #2035).

The report-emails spec's "Preview": the Report emails row action, the
editor's Preview of the unsaved form and ``pk-admin report-email render``
compile the report with the one compiler a send uses
(``report_email_capture.compile_report_email``), from what a send would
read now:

- the as-of moment and report day follow from the report's as-of choice
  and the database clock, derived as a Send now records them
  (``report_email_inputs.send_inputs`` with no covered slot);
- the open requests and the funnel come from the readers a send uses;
- the fact blocks read one ready fact generation, never built or pinned
  here (``select_facts``): the one a send would build now when it is ready,
  else the newest ready one, cut at the report day and marked Updating.

Nothing is written but the caller's audit event: no occurrence, outbox
row, snapshot or fact pin. The page's content policy admits no inline
image data, so the dialog's chart images are drawn again by
``preview_image`` from a short-lived signed recipe (``image_token``) that
names the same generation, as-of moment and block options.

A preview has no saved copy, so its "Open protected report" button goes
to the Report emails page and its request links to the live requests
(spec, "Shared email layout").
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import connection
from django.urls import reverse

from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.jobs.campaign_mail_values import (
    campaign_values,
    document_parish,
)
from parishkit.stewardship.web.digest_content import CHART_ID, COMPARISON_CHART_IDS

from .digest_funnel import digest_funnel
from .documents import participation_document
from .facts import FactBusy, FactUnavailable, read_fact_set
from .inputs import FactInputs
from .models import CampaignDailyFactSet
from .report_blocks.compiler import compile_report
from .report_document import ReportDocument
from .report_email_capture import (
    OBSERVE,
    comparison_for,
    compile_report_email,
    open_requests,
)
from .report_email_cohort import frozen_cohort
from .report_email_inputs import report_days, send_inputs

# The population a report's fact blocks count (the preparations' scope).
SCOPE = "historical"
# The blocks whose drawing carries an inline image the dialog redraws.
IMAGE_BLOCKS = ("participation_charts", "compared_with_past_campaigns")
# How long a preview's image addresses stay valid: a dialog left open
# longer shows its images' alt text instead.
IMAGE_SECONDS = 15 * 60
# The content ids a preview's images may have: anything else is refused
# before the recipe is read or a block is drawn.
IMAGE_IDS = (CHART_ID, *COMPARISON_CHART_IDS)
# The report name the redrawn images' document carries: the recipe holds
# no free text, and the images never draw the name.
IMAGE_NAME = "Preview"


class NoSource(FactUnavailable):
    """No ParishSoft data has been promoted yet, so nothing can be drawn."""


@dataclass(frozen=True, repr=False)
class Preview:
    """One report drawn now: its email, who it would go to, and its facts.

    ``recipients`` is the cohort a send would freeze now (``[{address,
    kind, ref}]``); ``updating`` says the figures are not the generation a
    send would build now (or there is none); ``fact_set_id`` names the
    generation drawn (None without one); ``observed_at`` is the database
    clock the as-of moment was derived from, and ``watermark`` the live
    submission watermark observed with it, which holds the report end
    (#2298).
    """

    subject: str
    html: str
    text: str
    images: tuple
    recipients: list
    as_of: str
    as_of_at: datetime
    updating: bool
    fact_set_id: UUID | None
    observed_at: datetime
    covered_dates: tuple
    mode: str
    watermark: int | None = None


def _observe(campaign_id):
    """The database clock, the promoted source, and the live and review watermarks."""
    with connection.cursor() as cursor:
        cursor.execute(OBSERVE, {"campaign": campaign_id})
        observed = cursor.fetchone()
    if observed is None:
        raise NoSource("A preview needs a promoted ParishSoft source.")
    instant, source_id, watermark, reviews = observed
    return instant.astimezone(UTC), source_id, watermark, reviews


def select_facts(campaign_id, expected):
    """The ready generation a preview draws, and whether it is Updating.

    ``expected`` are the inputs a send would record now (``FactInputs``).
    Their exact ready generation is current; otherwise the newest ready
    generation of the campaign's scope (by source, then responses, then
    last day) is drawn, Updating. Returns ``(fact_set_id, updating)``,
    ``(None, True)`` when no generation is ready.
    """
    ready = CampaignDailyFactSet.objects.filter(
        campaign_id=campaign_id, population_scope=SCOPE, state="ready"
    )
    exact = (
        ready.filter(
            source_id=expected.source_id,
            submission_watermark=expected.submission_watermark,
            review_watermark=expected.review_watermark,
            timezone_configuration_id=expected.timezone_configuration_id,
            through_date=expected.through_date,
        )
        .values_list("id", flat=True)
        .first()
    )
    if exact is not None:
        return exact, False
    newest = (
        ready.order_by(
            "-source_generation",
            "-submission_watermark",
            "-review_watermark",
            "-through_date",
        )
        .values_list("id", flat=True)
        .first()
    )
    return newest, True


def _cut(document, last):
    """The participation document's days through ``last`` (None if none)."""
    days = tuple(day for day in document.days if day.local_date <= last)
    if not days:
        return None
    return replace(document, days=days, last_date=days[-1].local_date)


def _participation(fact_set_id, report_day, *, parish_name, requested_at):
    """The generation's participation document, cut at the report day.

    None when the generation is gone or has no day by the report day. The
    generation is read under its shared read protection and detached, so
    the compiler draws from memory. A generation locked just now
    (``FactBusy``) is raised, not drawn as missing, so the caller can say
    to try again.
    """

    def admit(action, inputs):
        """Only a read of this campaign's report scope."""
        return action == "read" and inputs.population_scope == SCOPE

    try:
        with read_fact_set(fact_set_id, admit=admit) as facts:
            projection = CampaignConfiguration.objects.get(
                pk=facts.timezone_configuration_id
            )
            document = participation_document(
                facts,
                parish_name=parish_name,
                browser_timezone=projection.timezone,
                requested_at=requested_at,
            )
    except FactBusy:
        raise
    except FactUnavailable:
        return None
    if not document.days:
        return None
    return _cut(document, report_day)


def build_document(
    values,
    campaign,
    version,
    *,
    mode,
    observed_at,
    source_id=None,
    fact_set_id=None,
    lists=True,
    watermark=None,
):
    """The report's source as a send now would draw it (``ReportDocument``).

    ``campaign`` is the campaign row (its active configuration gives the
    dates and zone), ``version`` the applied configuration (the parish's
    name and date format). ``fact_set_id`` is the generation to draw (None:
    no facts). ``lists`` False leaves out the funnel and the open requests,
    which only the dialog's email draws, never its redrawn images.
    ``watermark`` is the observed submission watermark the report end is
    counted under (#2298), the Preview's own for its redrawn images; None
    counts every live response.
    """
    projection = campaign.active_configuration
    inputs = send_inputs(
        values,
        (),
        observed_at,
        projection,
        campaign_id=campaign.pk,
        watermark=watermark,
    )
    covered = report_days(values, (), inputs, projection.start_date)
    participation = None
    if inputs.through_date is not None and fact_set_id is not None:
        participation = _participation(
            fact_set_id,
            inputs.through_date,
            parish_name=version.parish.name,
            requested_at=observed_at,
        )
    if participation is not None and participation.last_date != covered[-1]:
        # An older generation ends before the report day: its own last day
        # is the one reported, which the Updating notice explains.
        covered = (participation.last_date,)
    keys = {entry["block"] for entry in values["blocks"]}
    document = ReportDocument(
        # A preview is no send: its id is never linked, because the
        # preview's compile points the links elsewhere (``preview_report``).
        send_id=uuid4(),
        name=values["name"],
        as_of=values["as_of"],
        as_of_at=inputs.as_of,
        campaign_timezone=projection.timezone,
        mode=mode,
        covered_dates=covered,
        participation=participation,
        funnel=digest_funnel(campaign.pk, mode, inputs.as_of)
        if lists and "response_funnel" in keys
        else None,
        information=open_requests(campaign.pk, source_id, inputs.as_of)
        if lists and "additional_information" in keys
        else (),
        held=inputs.held,
        date_format=version.parish.date_format,
    )
    if "compared_with_past_campaigns" in keys and participation is not None:
        local_day = inputs.as_of.astimezone(ZoneInfo(projection.timezone)).date()
        document = replace(
            document,
            comparison=comparison_for(participation, projection, values, local_day),
        )
    return document


def placeholders(version, campaign):
    """The public campaign and parish values a subject and introduction may use."""
    return campaign_values(
        parish=document_parish(version.canonical_document),
        campaign=campaign.active_configuration.values,
    )


def preview_report(values, system, campaign, *, public_origin):
    """Draw the report ``values`` from current data (spec, "Preview").

    ``system`` is the configuration row (its active version and mode),
    ``campaign`` the current campaign. Run inside one read snapshot; the
    caller records the audit event. Raises ``NoSource`` before any
    ParishSoft data is promoted, ``FactBusy`` (or another
    ``FactUnavailable``) when the figures are locked or replaced while
    they are read, and the chart renderer's error when a chart cannot be
    drawn.
    """
    observed_at, source_id, watermark, reviews = _observe(campaign.pk)
    projection = campaign.active_configuration
    inputs = send_inputs(
        values,
        (),
        observed_at,
        projection,
        campaign_id=campaign.pk,
        watermark=watermark,
    )
    fact_set_id, updating = None, False
    if inputs.through_date is not None:
        expected = FactInputs(
            campaign.pk,
            SCOPE,
            source_id,
            watermark,
            projection.pk,
            inputs.through_date,
            review_watermark=reviews,
        )
        fact_set_id, updating = select_facts(campaign.pk, expected)
    version = system.active_configuration
    document = build_document(
        values,
        campaign,
        version,
        mode=system.mode,
        observed_at=observed_at,
        source_id=source_id,
        fact_set_id=fact_set_id,
        watermark=watermark,
    )
    if document.participation is None and fact_set_id is not None:
        # The generation vanished or ends before the campaign's first day.
        fact_set_id, updating = None, True
    compiled = compile_report_email(
        document,
        values,
        public_origin=public_origin,
        names=placeholders(version, campaign),
        # No saved copy: the button goes to the Report emails page and each
        # request link to the live request (spec, "Shared email layout").
        copy_path=reverse("admin:report_emails"),
        requests_path=reverse("admin:information_queue"),
    )
    return Preview(
        subject=compiled.subject,
        html=compiled.html,
        text=compiled.text,
        images=compiled.images,
        recipients=frozen_cohort(values, system.active_configuration_id),
        as_of=values["as_of"],
        as_of_at=document.as_of_at,
        updating=updating,
        fact_set_id=fact_set_id,
        observed_at=observed_at,
        covered_dates=document.covered_dates,
        mode=system.mode,
        watermark=watermark,
    )


def _salt(actor_id):
    """The image recipe's signing salt, bound to one Administrator."""
    return f"stewardship.report-email-preview-image:{actor_id}"


def image_token(preview, values, campaign_id, actor_id):
    """A short-lived signed recipe for the preview's chart images, or None.

    It names the generation, the observed clock and submission watermark
    (so a response entered after the close between the Preview and the
    image's fetch cannot move its report day, #2298) and the image-drawing
    blocks with their options, so ``preview_image`` draws the same images
    again; they hold only aggregate figures. The token is signed, not
    encrypted, and travels in the image's path, so it holds no free text
    (not even the report's name). None for a preview with no generation or
    no such block.
    """
    entries = [item for item in values["blocks"] if item["block"] in IMAGE_BLOCKS]
    if preview.fact_set_id is None or not entries:
        return None
    return signing.dumps(
        {
            "c": str(campaign_id),
            "f": str(preview.fact_set_id),
            "t": preview.observed_at.isoformat(),
            "w": preview.watermark,
            "a": values["as_of"],
            "b": entries,
        },
        salt=_salt(actor_id),
        compress=True,
    )


def preview_image(token, actor_id, system, campaign, name):
    """Draw one chart image of a preview again from its signed recipe.

    ``name`` is the image's content id: the participation chart's, or one
    of the comparison block's. Only the block that draws it is compiled.
    Raises ``signing.BadSignature`` (an expired or altered recipe, or
    another Administrator's) and ``LookupError`` when the recipe draws no
    such image, for example a generation compacted since. An unknown
    ``name`` is refused before the recipe is read or anything is drawn.
    """
    if name not in IMAGE_IDS:
        raise LookupError("No such preview image.")
    recipe = signing.loads(token, salt=_salt(actor_id), max_age=IMAGE_SECONDS)
    if recipe["c"] != str(campaign.pk):
        raise LookupError("The preview image belongs to another campaign.")
    owner = "participation_charts" if name == CHART_ID else IMAGE_BLOCKS[1]
    entry = next((item for item in recipe["b"] if item["block"] == owner), None)
    if entry is None:
        raise LookupError("The preview draws no such image.")
    values = {"name": IMAGE_NAME, "as_of": recipe["a"], "blocks": [entry]}
    document = build_document(
        values,
        campaign,
        system.active_configuration,
        mode=system.mode,
        observed_at=datetime.fromisoformat(recipe["t"]),
        fact_set_id=UUID(recipe["f"]),
        lists=False,
        watermark=recipe.get("w"),
    )
    if document.participation is None:
        raise LookupError("The preview's facts are no longer kept.")
    report = compile_report(
        document,
        [owner],
        # Only the images are kept; the drawn links are thrown away.
        public_origin="https://preview.invalid",
        options={owner: entry.get("options", {})},
    )
    for image in report.images:
        if image.content_id == name:
            return image.data
    raise LookupError("No such preview image.")
