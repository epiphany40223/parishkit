"""Calculate, stage, publish and verify exact retained participation generations.

The compiled task owner supplies campaign/restore/purge admission and a live
task claim. This service performs no scheduling or provider I/O. Verification
callers additionally retain their response-lifetime campaign read guard, just
like other consumers of ``read_fact_set``. Historical reconstruction deliberately
does not read old snapshot memberships, which source retention may compact.
"""

import json
from dataclasses import asdict

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.domain import Money
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.source.snapshot_models import (
    SourceSnapshot,
    SourceSnapshotPin,
)
from parishkit.stewardship.source.version_models import SnapshotFamily

from .calculations import (
    Calculation,
    CohortFamily,
    LiveResponse,
    Promotion,
    calculate_participation,
)
from .facts import (
    FactUnavailable,
    _owned,
    fact_inputs,
    publish_fact_set,
    read_fact_set,
    stage_fact_days,
)
from .inputs import DAY_FIELDS
from .money import MoneyAmount
from .participation import ParticipationDay


def _current_families(record, families):
    """Use the protected generation's permanent source pin, including in READ ONLY.

    Both callers own this generation: construction holds its building lease/row
    and verification holds its read lock. SQL forbids releasing a fact input
    pin while that generation exists, so source compaction cannot remove these
    memberships. A second FOR SHARE source lock would reject read-only guards.
    """
    source_id = record.source_id
    if not SourceSnapshotPin.objects.filter(
        snapshot_id=source_id,
        parent_kind="facts",
        parent_id=record.pk,
        snapshot__state="promoted",
        snapshot__compacted_at__isnull=True,
    ).exists():
        raise FactUnavailable("Fact population input protection is unavailable.")
    by_duid = {row["family_duid"]: row["id"] for row in families}
    selected = set()
    for key, canonical in (
        SnapshotFamily.objects.filter(snapshot_id=source_id)
        .values_list("source_key", "payload__canonical")
        .iterator(chunk_size=500)
    ):
        payload = json.loads(canonical)
        if (
            payload.get("schema_version") != 1
            or type(payload.get("schema_version")) is not int
            or any(
                type(payload.get(field)) is not bool
                for field in ("active", "parishioner", "portal_eligible")
            )
            or payload["portal_eligible"]
            != (payload["active"] and payload["parishioner"])
            or not key.isascii()
            or not key.isdecimal()
            or str(int(key)) != key
            or not 0 < int(key) < 2**31
        ):
            raise FactUnavailable("Fact population source is inconsistent.")
        if payload["portal_eligible"]:
            identity = by_duid.get(int(key))
            if identity is None:
                raise FactUnavailable("Fact population provenance is incomplete.")
            selected.add(identity)
    return frozenset(selected)


def _load_calculation(record):
    """Detach only required input columns while the caller protects this generation.

    Cutoff manifests and first-eligibility fields survive source compaction and
    later inactivation. Never use today's effective-response or eligibility
    pointers. The exact watermark excludes submissions committed during a build.
    """
    inputs = fact_inputs(record)
    projection = CampaignConfiguration.objects.get(pk=inputs.timezone_configuration_id)
    source = SourceSnapshot.objects.only(
        "id", "organization_id", "state", "generation", "promoted_at"
    ).get(pk=inputs.source_id)
    if projection.record_id != inputs.campaign_id or source.state != "promoted":
        raise FactUnavailable("Fact inputs no longer describe a retained campaign.")
    promotions = tuple(
        Promotion(generation, instant)
        for generation, instant in SourceSnapshot.objects.filter(
            state="promoted",
            organization_id=source.organization_id,
            generation__lte=source.generation,
        ).values_list("generation", "promoted_at")
    )
    families = tuple(
        FamilyCampaign.objects.filter(
            campaign_id=inputs.campaign_id, first_eligible_at__isnull=False
        ).values(
            "id", "family_duid", "first_eligible_at", "first_eligible_source_generation"
        )
    )
    current = (
        _current_families(record, families)
        if inputs.population_scope == "current"
        else frozenset()
    )
    responses = tuple(
        LiveResponse(
            family,
            sequence,
            instant,
            MoneyAmount(
                None if amount is None else Money.from_string(str(amount)).cents
            ),
        )
        for family, sequence, instant, amount in Submission.objects.filter(
            campaign_id=inputs.campaign_id,
            mode="live",
            campaign_sequence__lte=inputs.submission_watermark,
        ).values_list("family_id", "campaign_sequence", "submitted_at", "annual_pledge")
    )
    return Calculation(
        inputs.population_scope,
        projection.start_date,
        projection.end_date,
        inputs.through_date,
        projection.timezone,
        projection.values.get("financial") is not None,
        Promotion(source.generation, source.promoted_at),
        inputs.submission_watermark,
        promotions,
        tuple(
            CohortFamily(
                row["id"],
                row["first_eligible_at"],
                row["first_eligible_source_generation"],
            )
            for row in families
        ),
        current,
        responses,
    )


def materialize_fact_set(fact_set_id, claim, *, admit, interactive=False):
    """Build deterministic bounded chunks, rechecking ownership before each write.

    No SQL write locks are held during calculation. The allocated building
    generation protects its required inputs throughout; a lost task fence or
    revoked domain admission prevents any later staging/publication. A recovered
    task may replay identical chunks, but never overwrite a changed stored row.
    """
    if type(interactive) is not bool:
        raise ValueError("Interactive publication must be an explicit boolean.")
    with work_transaction():
        record = _owned(fact_set_id, claim, admit)
        context = _load_calculation(record)
    days = calculate_participation(context)
    for start in range(0, len(days), 250):
        with work_transaction():
            stage_fact_days(
                fact_set_id,
                claim,
                days=[asdict(day) for day in days[start : start + 250]],
                admit=admit,
            )
    with work_transaction():
        return publish_fact_set(
            fact_set_id, claim, admit=admit, interactive=interactive
        )


def verify_fact_set(fact_set_id, *, admit):
    """Return differing local dates while retaining the complete generation lock.

    An empty tuple proves parity, not a missing source or unavailable generation.
    Those conditions raise explicitly; verification never substitutes a newer
    cutoff and never changes facts. Durable drift reporting belongs to its task.
    """
    with read_fact_set(fact_set_id, admit=admit) as record:
        expected = calculate_participation(_load_calculation(record))
        actual = tuple(
            ParticipationDay(**values)
            for values in record.days.order_by("local_date").values(*sorted(DAY_FIELDS))
        )
        expected_by_date = {row.local_date: row for row in expected}
        actual_by_date = {row.local_date: row for row in actual}
        return tuple(
            day
            for day in sorted(expected_by_date.keys() | actual_by_date.keys())
            if expected_by_date.get(day) != actual_by_date.get(day)
        )
