"""Chosen-Family Testing sends: Admin intake of tickets, never rendering or keys.

An Administrator names up to ten real Families for one invitation or reminder
template. Each Family gets a ticket; the general worker later renders that
Family's real message with its own Testing code and link, routed only to the
Testing recipient. Web reads eligibility and writes tickets; it never sees a
code, a link, rendered content or a recipient address.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from django.core import signing
from django.urls import reverse

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.models import (
    Campaign,
    CampaignWorkGate,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_metadata import family_duid
from parishkit.stewardship.jobs.family_mail_models import (
    FAMILY_TEST_LIMIT,
    FamilyMailTest,
)
from parishkit.stewardship.jobs.family_mail_test_tasks import TASK_TYPE
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .content_models import ContentVersion
from .sessions import require_fresh

SALT = "stewardship-family-mail-test-v1"
BINDING_KEYS = frozenset(
    {"actor", "key", "campaign", "template", "digest", "epoch", "recipient", "families"}
)


@dataclass(frozen=True)
class FamilyChoice:
    """One requested DUID and whether it names a currently deliverable Family."""

    duid: int
    family_id: UUID | None
    reason: str

    @property
    def eligible(self):
        return self.reason == "eligible"


@dataclass(frozen=True)
class FamilyTestPreview:
    """The exact public inputs the Admin reviewed; confirmation must match them."""

    campaign: Campaign
    template: ContentVersion
    configuration: object
    request_key: UUID
    actor_id: UUID
    epoch_id: UUID | None
    families: tuple[FamilyChoice, ...]
    in_progress: int
    testing_recipient: str
    # A temporary gate (restore review, unreleased campaign work) refuses
    # confirmation for now without invalidating the review itself.
    held: bool = False

    @property
    def available(self):
        """How many more tests this campaign may have in progress right now."""
        return max(0, FAMILY_TEST_LIMIT - self.in_progress)

    def binding(self):
        """A changed configuration, epoch, recipient or Family list needs review."""
        return {
            "actor": str(self.actor_id),
            "key": str(self.request_key),
            "campaign": str(self.campaign.pk),
            "template": str(self.template.record_id),
            "digest": f"{self.configuration.digest}:{self.campaign.readiness_revision}",
            "epoch": str(self.epoch_id) if self.epoch_id is not None else "",
            "recipient": self.testing_recipient,
            "families": [str(choice.duid) for choice in self.families],
        }


def parse_family_duids(value):
    """Accept at most ten distinct exact DUIDs, whitespace-separated; never names."""
    if type(value) is not str or len(value) > 400:
        raise ValueError("Enter up to ten Family DUIDs.")
    duids = [family_duid(token) for token in value.split()]
    if len(duids) > FAMILY_TEST_LIMIT or len(set(duids)) != len(duids):
        raise ValueError("Enter up to ten distinct Family DUIDs.")
    return tuple(duids)


def _choice(duid, row, population, current):
    """Explain why a DUID cannot be tested without exposing anything private."""
    if row is None:
        return FamilyChoice(duid, None, "unknown")
    if not row.active or not row.portal_eligible:
        return FamilyChoice(duid, row.pk, "ineligible")
    if not row.email_eligible or not row.email_deliverable:
        return FamilyChoice(duid, row.pk, "undeliverable")
    if (
        population is None
        or population.population_dirty
        or current is None
        or population.source_snapshot_id != current.snapshot_id
        or population.source_generation != current.generation
        or row.source_generation != current.generation
    ):
        return FamilyChoice(duid, row.pk, "stale_source")
    return FamilyChoice(duid, row.pk, "eligible")


def _template_in_schedule(campaign_id, revision_id):
    """Only a template a current invitation/reminder schedule really uses."""
    return ScheduleDefinition.objects.filter(
        campaign_id=campaign_id,
        kind__in=("initial", "reminder"),
        current_revision__values__template_version=str(revision_id),
    ).exists()


def families_link(runtime, campaign, revision_id):
    """Where the fictional-sample page offers chosen-Family tests, if at all."""
    if (
        runtime.mode != "testing"
        or campaign.state != "draft"
        or runtime.current_campaign_id != campaign.pk
        or not _template_in_schedule(campaign.pk, revision_id)
    ):
        return None
    return reverse("admin:campaign_mail_families", args=[campaign.pk, revision_id])


def in_progress_count(campaign_id):
    """Queued tickets plus prepared messages not yet settled by the provider."""
    return FamilyMailTest.objects.filter(
        campaign_id=campaign_id, state="queued"
    ).count() + (
        OutboxMessage.objects.filter(campaign_id=campaign_id, purpose="family_test")
        .exclude(state__in=("delivered", "permanent_failure", "cancelled"))
        .count()
    )


def prepare(request, service, campaign_id, revision_id, duids=(), *, request_key=None):
    """Resolve the chosen DUIDs against current eligibility; no send, no content.

    The preview is only intent. Confirmation repeats every check here under the
    work lock, and the SQL ticket guard repeats them once more.
    """
    if any(not isinstance(value, UUID) for value in (campaign_id, revision_id)):
        raise ValueError("An exact campaign and email revision are required.")
    key = request_key if request_key is not None else uuid4()
    if not isinstance(key, UUID):
        raise ValueError("An exact request identity is required.")
    with work_transaction():
        actor = principal(request, service, passive=True)
        runtime = editable_configuration(service)
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=campaign_id
        )
        version = runtime.active_configuration
        template = ContentVersion.objects.get(
            configuration=version,
            campaign_id=campaign_id,
            record_id=revision_id,
            kind="email",
        )
        if (
            runtime.mode != "testing"
            or campaign.state != "draft"
            or runtime.current_campaign_id != campaign_id
        ):
            raise PermissionError("Family tests belong to the current Testing draft.")
        if not _template_in_schedule(campaign_id, revision_id):
            raise LookupError("This email is not used by a current schedule.")
        population = CampaignCredentialState.objects.filter(campaign=campaign).first()
        epoch = None
        if population is not None and not population.go_live_gate:
            epoch = RehearsalEpoch.objects.filter(
                pk=population.rehearsal_epoch_id, campaign=campaign, state="active"
            ).first()
        current = SourceCurrent.objects.filter(singleton=True).first()
        held = (
            runtime.restore_review_required
            or CampaignWorkGate.objects.filter(campaign=campaign)
            .exclude(state="released")
            .exists()
        )
        rows = {
            row.family_duid: row
            for row in FamilyCampaign.objects.filter(
                campaign=campaign, family_duid__in=duids
            )
        }
        return FamilyTestPreview(
            campaign=campaign,
            template=template,
            configuration=version,
            request_key=key,
            actor_id=actor.identity,
            epoch_id=None if epoch is None else epoch.pk,
            families=tuple(
                _choice(duid, rows.get(duid), population, current) for duid in duids
            ),
            in_progress=in_progress_count(campaign_id),
            testing_recipient=runtime.testing_recipient,
            held=held,
        )


def _binding(preview_token):
    """Reject a malformed or expired signed preview before touching the database."""
    if type(preview_token) is not str or len(preview_token) > 4096:
        raise ValueError("Invalid Family test command.")
    binding = signing.loads(preview_token, salt=SALT, max_age=900)
    if (
        type(binding) is not dict
        or set(binding) != BINDING_KEYS
        or any(type(binding[key]) is not str for key in BINDING_KEYS - {"families"})
        or type(binding["families"]) is not list
        or not binding["families"]
        or any(type(value) is not str for value in binding["families"])
    ):
        raise ValueError("Invalid Family test preview.")
    return binding


def _enqueue(actor_id, ticket_id):
    """Allocate the worker task whose exact identity the SQL ticket guard checks."""

    def admit(action, status):
        return (
            action == "enqueue"
            and status.task_type == TASK_TYPE
            and status.domain_request_id == ticket_id
        )

    return enqueue(
        task_type=TASK_TYPE,
        domain_request_id=ticket_id,
        actor_id=actor_id,
        correlation_id=current_correlation(),
        admit=admit,
        idempotency_key=ticket_id,
    )


def request_tests(
    request, service, campaign_id, revision_id, *, preview_token, acknowledge
):
    """Persist one reviewed request as one ticket per Family, or replay it.

    Confirmation needs a fresh Google sign-in and an explicit acknowledgement
    that real Family data goes to the Testing recipient. Retries within the
    preview lifetime return the original tickets; nothing is sent twice.
    """
    if acknowledge is not True:
        raise ValueError(
            "Acknowledge that real Family data goes to the Testing recipient."
        )
    binding = _binding(preview_token)
    with work_transaction():
        actor = principal(request, service)
        if binding["actor"] != str(actor.identity) or binding["campaign"] != str(
            campaign_id
        ):
            raise PermissionError("Family test preview belongs to another scope.")
        if binding["template"] != str(revision_id):
            raise PermissionError("Family test preview names another email.")
        key = UUID(binding["key"])
        previous = list(
            FamilyMailTest.objects.filter(
                requested_by_id=actor.identity, request_key=key
            ).order_by("sequence")
        )
        if previous:
            if any(
                row.campaign_id != campaign_id or row.template.record_id != revision_id
                for row in previous
            ):
                raise PermissionError("Family test replay scope differs.")
            return previous
        preview = prepare(
            request,
            service,
            campaign_id,
            revision_id,
            parse_family_duids(" ".join(binding["families"])),
            request_key=key,
        )
        if preview.binding() != binding:
            raise StaleRecordError("Review a fresh Family test preview.")
        if preview.epoch_id is None:
            raise StaleRecordError("Testing credentials are not ready yet.")
        if preview.held:
            raise StaleRecordError("Campaign work is in progress; try again later.")
        if any(not choice.eligible for choice in preview.families):
            raise StaleRecordError("A chosen Family can no longer be tested.")
        if len(preview.families) > preview.available:
            raise StaleRecordError("Too many Family tests are in progress.")
        reauthenticated_at = require_fresh(request)
        rows = []
        for sequence, choice in enumerate(preview.families, start=1):
            ticket_id = uuid5(
                campaign_id,
                f"family-mail-test:{actor.identity}:{key}:{sequence}",
            )
            task = _enqueue(actor.identity, ticket_id)
            row = FamilyMailTest(
                id=ticket_id,
                campaign=preview.campaign,
                configuration=preview.configuration,
                template=preview.template,
                requested_by_id=actor.identity,
                request_key=key,
                sequence=sequence,
                reauthenticated_at=reauthenticated_at,
                rehearsal_epoch_id=preview.epoch_id,
                task_id=task.run_id,
                family_id=choice.family_id,
                actor_id=actor.identity,
            )
            row.save(force_insert=True)
            rows.append(row)
        return rows


def recent_tickets(campaign_id, *, limit=25):
    """Status rows for the page: sequence, ticket state, message state and DUID.

    A queued ticket still names its Family; a prepared one is followed through
    its outbox message, which keeps the Family until Testing cleanup.
    """
    rows = list(
        FamilyMailTest.objects.filter(campaign_id=campaign_id)
        .order_by("-created_at", "-sequence", "-id")
        .only(
            "id",
            "created_at",
            "sequence",
            "state",
            "family_id",
            "outbox_id",
            "request_key",
        )[:limit]
    )
    messages = {
        message["id"]: message
        for message in OutboxMessage.objects.filter(
            pk__in=[row.outbox_id for row in rows if row.outbox_id is not None]
        ).values("id", "state", "family_id")
    }
    duids = dict(
        FamilyCampaign.objects.filter(
            pk__in={
                *(row.family_id for row in rows if row.family_id is not None),
                *(message["family_id"] for message in messages.values()),
            }
        ).values_list("id", "family_duid")
    )
    items = []
    for row in rows:
        message = messages.get(row.outbox_id)
        family_id = row.family_id if message is None else message["family_id"]
        items.append(
            {
                "id": row.pk,
                "created_at": row.created_at,
                "request_key": row.request_key,
                "sequence": row.sequence,
                "state": row.state,
                "message_state": None if message is None else message["state"],
                "duid": duids.get(family_id),
            }
        )
    return items
