"""Bounded intake checks, distinct from installer ancestry/corruption verification."""

from django.db import connection

from parishkit.config import ConfigError

from .configuration_models import AppliedConfigurationVersion
from .configuration_snapshots import verified_snapshot_version


def intake_base(digest):
    """Load only the selected document/projections and its direct predecessor digest."""
    snapshot = (
        AppliedConfigurationVersion.objects.select_related("parish")
        .prefetch_related("integrations")
        .filter(digest=digest)
        .first()
    )
    try:
        if snapshot is not None:
            predecessor = None
            if snapshot.predecessor_id is not None:
                predecessor = AppliedConfigurationVersion.objects.values_list(
                    "digest", flat=True
                ).get(pk=snapshot.predecessor_id)
            return snapshot, verified_snapshot_version(
                snapshot, predecessor_digest=predecessor
            )
    except ConfigError:
        pass
    raise ConfigError("A complete prepared base configuration is required.")


def check_historical_additions(base_id, patch):
    """Reject retired kind/ID reuse without loading historical JSON into intake.

    Only additions need an ancestry identity check: updates cannot change kind
    or ID, and removals introduce no binding. The recursive query traverses UUID
    links and checks matching projections, returning at most one conflict, not
    every canonical document. Full ancestry validation remains mandatory when
    the installer prepares the candidate; this is not a readiness certificate.
    """
    additions = [
        item
        for item in patch
        if item["section"] == "integrations" and item["operation"] == "add"
    ]
    if not additions:
        return
    predicates, parameters = [], [base_id]
    for item in additions:
        kind, identifier = item["values"]["kind"], item["id"]
        predicates.append(
            "((i.kind = %s AND i.record_id <> %s) OR "
            "(i.record_id = %s AND i.kind <> %s))"
        )
        parameters.extend([kind, identifier, identifier, kind])
    with connection.cursor() as cursor:
        cursor.execute(
            """WITH RECURSIVE chain(id, predecessor_id) AS (
                SELECT id, predecessor_id FROM stewardship_configuration_version
                WHERE id = %s
                UNION
                SELECT p.id, p.predecessor_id FROM stewardship_configuration_version p
                JOIN chain c ON p.id = c.predecessor_id
            ) SELECT 1 FROM chain c JOIN stewardship_applied_integration i
              ON i.configuration_id = c.id WHERE """
            + " OR ".join(predicates)
            + " LIMIT 1",
            parameters,
        )
        if cursor.fetchone() is not None:
            raise ConfigError(
                "Integration identities must remain stable across history."
            )
