"""Current-preview test-mail intake, independently journalled from live schedules."""

from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from django.core import signing
from django.db import connection

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.receipt_preview import confirmation_block
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.readiness_mail import ReadinessMail
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .campaign_mail_models import CampaignMailTest
from .configuration_models import AppliedIntegration
from .content_forms import sample_render
from .content_models import ContentVersion

TASK_TYPE = "campaign_mail_test"
SALT = "stewardship-campaign-mail-test-v1"


@dataclass(frozen=True)
class MailPreview:
    """Server-built fictional message and the exact public inputs the Admin reviewed."""

    row: CampaignMailTest
    sample: ReadinessMail
    digest: str
    # Where chosen-Family tests of this template are offered, if anywhere.
    families_url: str | None = None

    def binding(self):
        """A changed configuration, recipient or key invalidates an unsent preview."""
        row = self.row
        return {
            "actor": str(row.requested_by_id),
            "key": str(row.request_key),
            "configuration": str(row.configuration_id),
            "digest": self.digest,
            "campaign": str(row.campaign_id),
            "template": str(row.template_id),
            "fingerprint": row.fingerprint,
            "recipient": self.sample.recipient,
        }


def live(row):
    """Repeat current configuration, Admin, draft, credential and work-gate checks."""
    require_work_order()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_campaign_mail_live_v1(%s,%s,%s,%s,%s)",
            [
                row.configuration_id,
                row.campaign_id,
                row.template_id,
                row.fingerprint,
                row.requested_by_id,
            ],
        )
        return cursor.fetchone() == (True,)


def prepare(request, service, campaign_id, revision_id, *, request_key=None):
    """Preview explicit fictional mail for Testing or paused-delivery recovery.

    The shared SQL admission owner also permits the current paused Production
    campaign. Routing remains the configured test recipient, never a Family;
    this does not release schedules, restore holds or the live delivery pause.
    """
    if any(not isinstance(value, UUID) for value in (campaign_id, revision_id)):
        raise ValueError("An exact campaign and email revision are required.")
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
        workspace = AppliedIntegration.objects.get(
            configuration=version, kind="google_workspace"
        )
        email = AppliedIntegration.objects.get(configuration=version, kind="email")
        key = request_key if request_key is not None else uuid4()
        if not isinstance(key, UUID):
            raise ValueError("An exact request identity is required.")
        row = CampaignMailTest(
            id=uuid5(
                campaign_id,
                "campaign-mail-test:" + str(actor.identity) + ":" + str(key),
            ),
            configuration=version,
            campaign=campaign,
            template=template,
            request_key=key,
            requested_by_id=actor.identity,
            actor_id=actor.identity,
            fingerprint=workspace.credential_fingerprint,
        )
        if not live(row):
            raise StaleRecordError("Campaign test preview is not currently available.")
        sample = ReadinessMail(
            delivery_id=row.pk,
            sender=email.settings["sender"],
            reply_to=email.settings["reply_to"],
            recipient=runtime.testing_recipient,
            **sample_render(
                {key: getattr(template, key) for key in ("subject", "html", "text")},
                parish=version.canonical_document["sections"]["parish"][0]["values"],
                campaign=campaign.active_configuration.values,
                confirmation=template.slot == "confirmation",
                receipt_block=confirmation_block(
                    version.canonical_document, campaign_id
                ),
            ),
        )
        row.mail = sample.payload()
        # Structural configuration may be unchanged after withdrawal, but that
        # must not revive an earlier signed test preview for a new go-live cycle.
        return MailPreview(
            row,
            sample,
            f"{version.digest}:{campaign.readiness_revision}",
            _families_link(runtime, campaign, revision_id),
        )


def _families_link(runtime, campaign, revision_id):
    """Offer chosen-Family tests within the same snapshot; never fail the sample."""
    from .campaign_family_test import families_link

    try:
        return families_link(runtime, campaign, revision_id)
    except Exception:
        return None


def request_sample(
    request,
    service,
    campaign_id,
    revision_id,
    *,
    preview_token,
    acknowledge_unknown=False,
):
    """Persist one reviewed send; retries within the preview lifetime reuse its row.

    Expired commands require a fresh preview. They never resend the retained
    journal, whose passive status remains available independently of the token.
    """
    if (
        type(preview_token) is not str
        or len(preview_token) > 4096
        or type(acknowledge_unknown) is not bool
    ):
        raise ValueError("Invalid campaign test command.")
    binding = signing.loads(preview_token, salt=SALT, max_age=900)
    if (
        type(binding) is not dict
        or set(binding)
        != {
            "actor",
            "key",
            "configuration",
            "digest",
            "campaign",
            "template",
            "fingerprint",
            "recipient",
        }
        or any(type(value) is not str for value in binding.values())
    ):
        raise ValueError("Invalid campaign test preview.")
    with work_transaction():
        actor = principal(request, service)
        if binding["actor"] != str(actor.identity) or binding["campaign"] != str(
            campaign_id
        ):
            raise PermissionError("Campaign test preview belongs to another scope.")
        previous = CampaignMailTest.objects.filter(
            requested_by_id=actor.identity, request_key=UUID(binding["key"])
        ).first()
        if previous is not None:
            if (
                previous.campaign_id != campaign_id
                or previous.template.record_id != revision_id
            ):
                raise PermissionError("Campaign test replay scope differs.")
            return previous
        preview = prepare(
            request, service, campaign_id, revision_id, request_key=UUID(binding["key"])
        )
        if preview.binding() != binding:
            raise StaleRecordError("Review a fresh campaign test preview.")
        rows = CampaignMailTest.objects.filter(campaign_id=campaign_id)
        if rows.filter(state__in=["queued", "submitting"]).exists():
            raise StaleRecordError("A campaign test is already pending.")
        if not acknowledge_unknown and rows.filter(state="delivery_unknown").exists():
            raise ValueError("A prior test may have arrived; acknowledge another send.")
        row = preview.row

        def admit(action, status):
            """Intake owns the same configuration/work lock through Task and journal."""
            return (
                action == "enqueue"
                and status.task_type == TASK_TYPE
                and status.domain_request_id == row.pk
                and live(row)
            )

        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=row.pk,
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            admit=admit,
            idempotency_key=row.pk,
        )
        row.task_id = task.run_id
        row.save(force_insert=True)
        return row
