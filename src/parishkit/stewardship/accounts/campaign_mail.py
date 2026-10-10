"""Current-preview test-mail intake, independently journalled from live schedules."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from django.db import connection
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.campaign_mail_values import document_parish
from parishkit.stewardship.jobs.receipt_preview import confirmation_block
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.readiness_mail import ReadinessMail
from parishkit.stewardship.sender_name import resolved_sender_name
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import UserFacingMissing, load_preview

from .admin_editing import editable_configuration, principal
from .campaign_mail_models import CampaignMailTest
from .configuration_models import AppliedIntegration
from .content_forms import sample_banner, sample_render
from .content_models import ContentVersion

TASK_TYPE = "campaign_mail_test"
SALT = "stewardship-campaign-mail-test-v1"
# The Production campaign states whose test mail is admitted while live
# delivery is paused, as in stewardship_campaign_mail_live_v1.
PAUSED_STATES = frozenset({"scheduled", "active", "closed"})


@dataclass(frozen=True)
class MailPreview:
    """Server-built fictional message and the exact public inputs the Admin reviewed."""

    row: CampaignMailTest
    sample: ReadinessMail
    digest: str
    # Where chosen-Family tests of this template are offered, if anywhere.
    families_url: str | None = None

    def binding(self):
        """A changed configuration, recipient or key invalidates an unsent preview.

        The token is signed, not encrypted: anyone holding it can read it. So
        it binds the Testing recipient by ``recipient_digest``, never the
        address itself (ADM-11 PR 6b prints the token on the command line).
        """
        row = self.row
        return {
            "actor": str(row.requested_by_id),
            "key": str(row.request_key),
            "configuration": str(row.configuration_id),
            "digest": self.digest,
            "campaign": str(row.campaign_id),
            "template": str(row.template_id),
            "fingerprint": row.fingerprint,
            "recipient": recipient_digest(self.sample.recipient),
        }


def recipient_digest(address):
    """The SHA-256 of a normalized address, so a token never carries it.

    Normalized as the address is compared elsewhere: surrounding spaces
    removed and lower case, so the same configured recipient always binds
    the same digest.
    """
    return hashlib.sha256((address or "").strip().lower().encode()).hexdigest()


def admits_test_mail(mode, campaign):
    """Whether the campaign's mode and state admit test mail at all (#923).

    This is the lifecycle part of the SQL admission
    (``stewardship_campaign_mail_live_v1``): a Testing draft, or a scheduled,
    active or closed Production campaign whose live delivery is paused. The
    SQL stays the authority and also checks the work gates, the installed
    credential and the requesting Administrator; this only lets pages offer a
    test when one can work, and say why when it cannot, instead of the
    generic "This information changed".
    """
    if mode == "testing":
        return campaign.state == "draft"
    return (
        mode == "production"
        and campaign.delivery_paused
        and campaign.state in PAUSED_STATES
    )


# Shown on Dates and mail schedules in place of the test link, and as the
# test page's refusal, while admits_test_mail is false.
NOT_OPEN = _(
    "Test emails can be sent only while the campaign is being tested, or while "
    "live email delivery is paused."
)


def not_open_refusal():
    """The test page's refusal while the campaign admits no test mail (#923)."""
    return UserFacingMissing(
        NOT_OPEN,
        fix=_(
            "While a live campaign's email delivery runs, its emails go out only "
            "as scheduled. To send a test now, pause live email delivery first."
        ),
        link=reverse("admin:delivery_control"),
        link_label=_("Pause and resume mail"),
    )


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
            # A campaign whose mode and state admit no test mail at all gets a
            # refusal that says so; reloading would never help (#923).
            if not admits_test_mail(runtime.mode, campaign):
                raise not_open_refusal()
            raise StaleRecordError("Campaign test preview is not currently available.")
        sample = ReadinessMail(
            delivery_id=row.pk,
            sender=email.settings["sender"],
            reply_to=email.settings["reply_to"],
            recipient=runtime.testing_recipient,
            sender_name=resolved_sender_name(
                email.settings.get("sender_name"),
                document_parish(version.canonical_document)["name"],
            ),
            **sample_render(
                {key: getattr(template, key) for key in ("subject", "html", "text")},
                parish=document_parish(version.canonical_document),
                campaign=campaign.active_configuration.values,
                confirmation=template.slot == "confirmation",
                receipt_block=confirmation_block(
                    version.canonical_document, campaign_id
                ),
                banner=sample_banner(
                    campaign.active_configuration.values, template.slot
                ),
            ),
            banner_origin=_public_origin(),
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


def recent_tests(campaign_id, row):
    """The page's test list: pending and unknown flags and the 25 newest tests.

    ``row`` is the preview's ``CampaignMailTest``; a test is ``current`` when
    it used the same configuration and template. The Preview and test email
    page and ``pk-admin test sample-preview`` (ADM-11 PR 6b) both read this.
    """
    rows = CampaignMailTest.objects.filter(campaign_id=campaign_id)
    newest = rows.order_by("-created_at", "-id").only(
        "id", "state", "created_at", "configuration_id", "template_id"
    )[:25]
    return {
        "pending": rows.filter(state__in=["queued", "submitting"]).exists(),
        "unknown": rows.filter(state="delivery_unknown").exists(),
        "items": [
            {
                "id": test.pk,
                "state": test.state,
                "created_at": test.created_at,
                "current": test.configuration_id == row.configuration_id
                and test.template_id == row.template_id,
            }
            for test in newest
        ],
    }


def _public_origin():
    """This deployment's public origin, the only host a sample banner may use."""
    from django.conf import settings

    return getattr(settings, "STEWARDSHIP_PUBLIC_ORIGIN", "") or ""


def _families_link(runtime, campaign, revision_id):
    """Offer chosen-Family tests within the same snapshot; never fail the sample.

    The link is a convenience read inside the sample's work transaction. Its
    own savepoint keeps a failed query from aborting the caller's transaction,
    and only database/configuration failures are absorbed.
    """
    from django.db import DatabaseError, transaction

    from parishkit.config import ConfigError

    from .campaign_family_test import families_link

    try:
        with transaction.atomic():
            return families_link(runtime, campaign, revision_id)
    except (DatabaseError, ConfigError):
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
    binding = load_preview(preview_token, salt=SALT)
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
