"""Authorized response-lifetime selection of complete current or stale reports.

This service never builds synchronously or substitutes a new scope. It exposes
the expected input tuple and the selected document's own as-of metadata so the
HTML owner can conspicuously label Updating without calling stale data current.
Queued exact export/digest requests are a separate owner, not this read path.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
from parishkit.stewardship.schema_primitives import timezone_names

from .documents import participation_document
from .export_services import admit_campaign, authorize
from .facts import FactUnavailable, fact_inputs, read_fact_set
from .inputs import FactInputs
from .models import POPULATION_SCOPES, CampaignDailyFactSet, CampaignFactPointer
from .participation import ParticipationDocument


@dataclass(frozen=True)
class ParticipationSelection:
    """Data and freshness are inseparable; None is unavailable, not an empty chart."""

    selected_at: datetime
    expected: FactInputs | None
    selected: FactInputs | None
    document: ParticipationDocument | None

    @property
    def status(self):
        """Current means equality with the entire captured request-time tuple."""
        if self.document is None:
            return "unavailable"
        return "current" if self.selected == self.expected else "updating"

    @property
    def updating(self):
        """Missing data also needs refresh; never show fabricated zero statistics."""
        return self.status != "current"


def current_inputs(campaign_id, population_scope):
    """Capture configuration/source/live watermark/clock in one SQL snapshot.

    Separate READ COMMITTED queries could combine an old source with a newer
    submission or configuration. No locks or long transactions block writers;
    freshness is explicitly relative to this statement's captured instant.
    The caller owns authorization and the campaign read guard.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.active_configuration_id, p.end_date, p.timezone, "
            "s.snapshot_id, COALESCE((SELECT max(r.campaign_sequence) "
            "FROM stewardship_submission r WHERE r.campaign_id=c.id "
            "AND r.mode='live'),0), stewardship_campaign_now_v1() "
            "FROM stewardship_campaign c "
            "JOIN stewardship_campaign_configuration p "
            "ON p.id=c.active_configuration_id "
            "LEFT JOIN stewardship_source_current s ON s.singleton "
            "WHERE c.id=%s",
            (campaign_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise FactUnavailable("Campaign report inputs are unavailable.")
    configuration, end, zone, source, watermark, instant = row
    if source is None:
        return None, instant
    return FactInputs(
        campaign_id,
        population_scope,
        source,
        watermark,
        configuration,
        min(end, instant.astimezone(ZoneInfo(zone)).date()),
    ), instant


def _candidates(campaign_id, population_scope, expected):
    """Prefer an exact ready build, then only this scope's published fallback."""
    exact = None
    if expected is not None:
        exact = (
            CampaignDailyFactSet.objects.filter(
                campaign_id=campaign_id,
                population_scope=population_scope,
                source_id=expected.source_id,
                submission_watermark=expected.submission_watermark,
                timezone_configuration_id=expected.timezone_configuration_id,
                through_date=expected.through_date,
                state="ready",
            )
            .values_list("id", flat=True)
            .first()
        )
    pointer = (
        CampaignFactPointer.objects.filter(
            campaign_id=campaign_id, population_scope=population_scope
        )
        .values_list("fact_set_id", flat=True)
        .first()
    )
    return tuple(
        dict.fromkeys(value for value in (exact, pointer) if value is not None)
    )


@contextmanager
def participation_report(
    store,
    user_id,
    *,
    campaign_id,
    population_scope="historical",
    browser_timezone,
    abort,
):
    """Keep authorization, generation and campaign protection through rendering.

    The response adapter supplies a real transport-abort hook and must finish
    rendering inside this context. A vanished unpinned candidate is unavailable
    or falls back to the explicit pointer, never to an arbitrary newer report.
    Export request allocation must separately pin the chosen immutable UUID.
    """
    if any(not isinstance(value, UUID) for value in (campaign_id, user_id)):
        raise ValueError("Report identities must be canonical UUIDs.")
    if type(population_scope) is not str or population_scope not in POPULATION_SCOPES:
        raise ValueError("Unknown report population scope.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Report timezone must be an IANA name.")

    def fresh(guard):
        """No saved role or campaign UUID substitutes for current authorization."""
        authorize(store, user_id)
        admit_campaign(campaign_id, mutating=False)

    def admit(action, inputs):
        """A candidate must belong to this exact authorized campaign and scope."""
        return (
            action == "read"
            and inputs.campaign_id == campaign_id
            and inputs.population_scope == population_scope
        )

    with (
        CampaignReadGuard([campaign_id], authorize=fresh, abort=abort) as guard,
        ExitStack() as selected_guard,
    ):
        expected, instant = current_inputs(campaign_id, population_scope)
        facts = None
        for identifier in _candidates(campaign_id, population_scope, expected):
            try:
                facts = selected_guard.enter_context(
                    read_fact_set(identifier, admit=admit)
                )
            except FactUnavailable:
                continue
            break
        document = None
        if facts is not None:
            system = SystemConfiguration.objects.select_related(
                "active_configuration__parish"
            ).get()
            document = participation_document(
                facts,
                parish_name=system.active_configuration.parish.name,
                browser_timezone=browser_timezone,
                requested_at=instant,
            )
        fresh(guard)
        guard.check()
        yield ParticipationSelection(
            instant,
            expected,
            fact_inputs(facts) if facts is not None else None,
            document,
        )
        guard.check()
