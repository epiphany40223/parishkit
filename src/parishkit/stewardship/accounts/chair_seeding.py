"""The selected Member of a confirmed suggestion, from request to retained evidence.

The applied YAML never names a Member. The Administrator's selection travels
beside the confirmation request as an immutable intent per seeded assignment
record, written in the request's own durable transaction; the installer
requires an intent for every seeded assignment the request adds, and records
the seed's retained identity evidence from it inside the activation
transaction, before the Chairperson reconciliation decides the new seed's
overlay. The SQL guards verify every fact this reads.
"""

from django.db import connection

from parishkit.config import ConfigError

from .chair_confirmation import REQUEST_SCHEMA, seeded_additions
from .chair_models import ChairSeedEvidence, ChairSeedIntent


def attach_intents(request, members):
    """Record one intent per seeded assignment the request adds.

    `members` maps each seeded assignment record id in the patch to the
    selected Member; a missing or surplus selection is refused before any row
    is written. The binding trigger then holds each row to this request.
    """
    additions = seeded_additions(request.patch)
    if set(additions) != set(members) or not additions:
        raise ConfigError("Confirmation selections do not match the request.")
    for identifier in additions:
        selected = members[identifier]
        ChairSeedIntent.objects.create(
            request=request,
            assignment_record_id=identifier,
            organization_id=selected["organization_id"],
            member_duid=selected["member_duid"],
            actor_id=request.actor_id,
            correlation_id=request.correlation_id,
        )


def validate_intents(request):
    """A confirmation request must carry one intent per seeded assignment it adds."""
    if request.request_schema != REQUEST_SCHEMA:
        return
    recorded = set(
        ChairSeedIntent.objects.filter(request=request).values_list(
            "assignment_record_id", flat=True
        )
    )
    if {str(value) for value in recorded} != set(seeded_additions(request.patch)):
        raise ConfigError("Confirmation request lacks its Member selections.")


def _relationships(intents, configuration_id):
    """Each intent with its candidate assignment and the current roster keys.

    Read under the activation's own lock, which source promotion also takes,
    so the keys are exactly what the evidence guard will compare.
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .policy_models import MinistryAssignment

    current = SourceCurrent.objects.get(singleton=True)
    rows = []
    for intent in intents:
        assignment = MinistryAssignment.objects.get(
            configuration_id=configuration_id,
            record_id=intent.assignment_record_id,
            source="chair-seed",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT roster_key FROM stewardship_current_chair"
                " WHERE snapshot_id=%s AND organization_id=%s AND member_duid=%s"
                " AND ministry_duid=%s AND email=%s ORDER BY roster_key",
                [
                    current.snapshot_id,
                    intent.organization_id,
                    intent.member_duid,
                    assignment.ministry_duid,
                    assignment.email,
                ],
            )
            keys = [row[0] for row in cursor.fetchall()]
        rows.append((intent, assignment, current, keys))
    return rows


def confirmable(request, configuration_id, document):
    """Whether every seed this request adds is still true of the current source.

    Judged inside the activation transaction, before anything is applied, by
    the same facts the evidence guard demands: the selected Member is a
    current Chairperson of that Ministry at that address in the promoted
    snapshot for that organization, and the Ministry is active under the
    candidate's own activity. A request that adds no seed is confirmable.
    """
    from .ministry_activity import active_ministries

    if request is None or request.request_schema != REQUEST_SCHEMA:
        return True
    intents = list(ChairSeedIntent.objects.filter(request=request))
    if not intents:
        return True
    rows = _relationships(intents, configuration_id)
    for intent, assignment, current, keys in rows:
        if not keys or current.organization_id != intent.organization_id:
            return False
        active = active_ministries(
            document,
            organization_id=intent.organization_id,
            catalog_duids=frozenset({assignment.ministry_duid}),
        )
        if assignment.ministry_duid not in active:
            return False
    return True


def record_seed_evidence(activation, request):
    """Bind each intent of this request to its applied assignment and roster keys.

    A request without intents, an ordinary policy change, records nothing.
    The installer has already judged the seeds confirmable under this same
    lock, so the evidence guard is the backstop, not the decision: a refusal
    here is a defect, never an expected outcome.
    """
    if request is None:
        return 0
    intents = list(ChairSeedIntent.objects.filter(request=request))
    if not intents:
        return 0
    for intent, assignment, current, keys in _relationships(
        intents, activation.configuration_id
    ):
        ChairSeedEvidence.objects.create(
            assignment_record_id=intent.assignment_record_id,
            assignment=assignment,
            snapshot_id=current.snapshot_id,
            organization_id=intent.organization_id,
            member_duid=intent.member_duid,
            roster_keys=keys,
            actor_id=activation.actor_id,
            correlation_id=activation.correlation_id,
        )
    return len(intents)
