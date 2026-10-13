"""Exact daily fact ownership and atomic compiled-content/recipient handoff."""

from dataclasses import replace

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.data_age import data_age_at
from parishkit.stewardship.web.digest_content import validate_digest_body

from .daily_digest import DailyDigestContent, DailyDigestDocument
from .digest_capture import retained_dates, retained_statistics
from .digest_funnel import digest_funnel, report_day_end
from .digest_models import DailyDigestReady, DailyDigestSnapshot
from .digest_ownership import (
    TASK_TYPE,
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .documents import participation_document
from .facts import FactUnavailable, begin_fact_set, fact_inputs
from .models import CampaignDailyFactSet
from .recovery import recover_fact_set
from .retention import pin_facts


def admit_daily_facts(claim, action, inputs):
    """Recheck the same protected observation at every calculation chunk boundary."""
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.get(preparation=preparation)
    return preparation.phase == "facts" and inputs == fact_inputs(snapshot)


def begin_daily_facts(claim):
    """Reuse exact ready data; incomplete foreign roots retain their own recovery."""
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.get(preparation=preparation)

    def admit(action, inputs):
        """Bind all calculation primitives to this owned snapshot and live fence."""
        return admit_daily_facts(claim, action, inputs)

    facts = begin_fact_set(fact_inputs(snapshot), claim, admit=admit)
    if facts.state == "ready":
        return facts
    owner = TaskRun.objects.get(pk=facts.task_id)
    if owner.root_id != preparation.task_id and (
        owner.task_type not in {TASK_TYPE, "report_exact_export"}
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


def load_daily_document(claim, fact_set_id):
    """Detach data in a guarded effect; expensive chart rendering happens after it.

    When the schedule revision this preparation runs has the Compared with
    past campaigns section on (#1018 slice 7), the section is computed here,
    in the same owned transaction, from the digest's own pinned facts and
    the pinned campaign configuration; ``comparison_section`` documents it.
    """
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.select_related(
        "configuration__parish", "timezone_configuration"
    ).get(preparation=preparation)
    facts = CampaignDailyFactSet.objects.get(pk=fact_set_id, state="ready")
    if preparation.phase != "facts" or fact_inputs(snapshot) != fact_inputs(facts):
        raise FactUnavailable("Daily report facts do not match their retained input.")
    document = retained_daily_document(snapshot, facts)
    if comparison_enabled(preparation):
        document = replace(document, comparison=comparison_section(document, snapshot))
    return document


# The SQLSTATE of the comparison function's refusal of a disagreeing fact row.
CHECK_VIOLATION = "23514"


def comparison_enabled(preparation):
    """Whether the preparation's schedule revision includes the section."""
    values = ScheduleRevision.objects.values_list("values", flat=True).get(
        pk=preparation.revision_id
    )
    return values.get("comparison") is True


def comparison_section(document, snapshot):
    """The protected section on the digest's report day, or None.

    The comparison's current figures come from the digest's fact set and its
    comparison day is the report day; None leaves the section out (no past
    campaign imported). A disagreement between the comparison's figures and
    their facts raises ``FactUnavailable``, failing this compile visibly and
    retryably, as a fact problem does, rather than sending a digest without
    the section it was set to carry.
    """
    from django.db import DatabaseError

    from parishkit.stewardship.storage import StorageInvariantError

    from .comparison_reads import digest_comparison
    from .digest_comparison import section

    try:
        found = digest_comparison(
            document.participation, snapshot.timezone_configuration
        )
    except StorageInvariantError:
        raise FactUnavailable("Comparison figures disagree with their facts.") from None
    except DatabaseError as error:
        # The definer function refuses with SQLSTATE 23514 when its Families
        # responded differ from the fact row's (Django raises IntegrityError);
        # that is the same disagreement. Any other database error is not.
        if getattr(error.__cause__, "sqlstate", None) != CHECK_VIOLATION:
            raise
        raise FactUnavailable("Comparison figures disagree with their facts.") from None
    return section(found)


def retained_daily_document(snapshot, facts, comparison=None):
    """Load the same pinned observation for compilation and authorized web reads.

    ``comparison`` is the retained section the saved page shows (the ready
    row's), never computed again here.
    """
    if facts.state != "ready" or fact_inputs(snapshot) != fact_inputs(facts):
        raise FactUnavailable("Daily report facts do not match their retained input.")
    document = participation_document(
        facts,
        parish_name=snapshot.configuration.parish.name,
        browser_timezone=snapshot.timezone_configuration.timezone,
        requested_at=snapshot.observed_at,
    )
    # The ParishSoft data age and connection as known at the observation
    # (#510), for the email and the saved report page alike. The compiled
    # email body is retained, so later refreshes never rewrite a sent digest.
    covered = retained_dates(snapshot)
    mode = snapshot.preparation.mode
    # The response funnel at the end of the report day (#477), counted from
    # durable timestamps here and again on the saved page, so both agree.
    funnel = digest_funnel(
        snapshot.campaign_id,
        mode,
        report_day_end(covered[-1], document.campaign_timezone, snapshot.observed_at),
    )
    return DailyDigestDocument(
        snapshot.pk,
        document,
        retained_statistics(snapshot),
        covered,
        snapshot.configuration.parish.date_format,
        source_age=data_age_at(snapshot.observed_at),
        funnel=funnel,
        mode=mode,
        comparison=comparison,
    )


def section_charts(section, images):
    """(Families PNG, pledges PNG) for the ready row's columns, each or None.

    The images are matched to the section's charts by content id, so a
    section without a pledges chart (no Financial stewardship) leaves that
    column null.
    """
    from parishkit.stewardship.web.digest_content import COMPARISON_CHART_IDS

    found = dict(images)
    if section is None:
        return None, None
    return tuple(found.get(name) for name in COMPARISON_CHART_IDS)


def retain_daily_content(claim, document, content):
    """Pin exact facts and compiled bytes before any per-Admin outbox allocation."""
    if not isinstance(document, DailyDigestDocument) or not isinstance(
        content, DailyDigestContent
    ):
        raise TypeError("Daily publication requires compiled exact report content.")
    validate_digest_body(content.html, content.text, content.chart.data, content.images)
    # Each section's server-drawn charts are compiled with it, unless the
    # last attempt withheld every such chart (#477), when the retained
    # comparison section has none either (``digest_comparison.withheld``).
    section_charts_expected = bool(
        document.comparison and document.comparison["charts"]
    )
    if (
        bool(content.comparison) != section_charts_expected
        or bool(content.funnel)
        != (document.funnel is not None and not content.charts_withheld)
        or (content.charts_withheld and section_charts_expected)
    ):
        raise ValueError("A digest's section and its charts are compiled together.")
    preparation = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(preparation)
    snapshot = DailyDigestSnapshot.objects.get(preparation=preparation)
    if snapshot.pk != document.snapshot_id or preparation.phase != "facts":
        raise PermissionError("Daily publication differs from its owned observation.")
    facts = CampaignDailyFactSet.objects.select_for_update().get(
        pk=document.participation.fact_set_id, state="ready"
    )
    if fact_inputs(facts) != fact_inputs(snapshot):
        raise FactUnavailable("Daily publication requires the exact retained facts.")
    recipients = list(
        AddressRule.objects.filter(
            configuration_id=scope.runtime.active_configuration_id,
            roles__contains=["administrator"],
        )
        .order_by("email")
        .values_list("email", flat=True)
    )
    ready = DailyDigestReady.objects.create(
        snapshot=snapshot,
        fact_set=facts,
        recipient_configuration_id=scope.runtime.active_configuration_id,
        recipients=recipients,
        subject=content.subject,
        html=content.html,
        text=content.text,
        chart=content.chart.data,
        comparison=document.comparison,
        **dict(
            zip(
                ("comparison_families_chart", "comparison_pledges_chart"),
                section_charts(document.comparison, content.comparison_images),
                strict=True,
            )
        ),
        # The Response funnel section's two charts (#477 slice 2), or none.
        **dict(
            zip(
                ("funnel_chart", "activity_chart"),
                (image.data for image in content.funnel)
                if content.funnel
                else (None, None),
                strict=True,
            )
        ),
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=preparation.pk,
    )
    pin_facts(
        facts.pk,
        parent_kind="digest",
        parent_id=snapshot.pk,
        admit=lambda action, inputs: admit_daily_facts(claim, action, inputs),
    )
    checkpoint_preparation(claim, phase="fanout")
    return ready
