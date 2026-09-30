"""Current credential-free receipt configuration, selected under the work lock."""

from parishkit.stewardship.accounts.branding_context import banner_for_email
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.hosted_file_content import links_for
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.content import SafeContent, email_banner

from .campaign_mail_values import document_parish
from .family_mail_inputs import public_values
from .receipt_content import ReceiptTemplate, render_receipt


def current_receipt_render(
    identity, submission, *, runtime, campaign, source, public_origin
):
    """Select optional singleton content; fixed receipt facts cannot be removed.

    The submission supplies its immutable instant and campaign identity, not its
    answers. Current source addresses and applied public settings govern sending.
    No template uses a Family credential and no private key is loaded.

    The submitted stamp uses the date format of the configuration this render
    pins (its ``configuration_id``), never a separately resolved active one.
    """
    require_work_order()
    version = runtime.active_configuration
    parish = document_parish(version.canonical_document)
    selected = ContentVersion.objects.filter(
        configuration=version, campaign_id=campaign.pk
    )
    try:
        email = selected.get(kind="email", slot="confirmation")
    except ContentVersion.DoesNotExist:
        email = None
    try:
        block = selected.get(kind="page", slot="submission_confirmation")
    except ContentVersion.DoesNotExist:
        block = None
    integration = AppliedIntegration.objects.get(configuration=version, kind="email")
    return render_receipt(
        identity=identity,
        configuration_id=version.pk,
        template_id=email.pk if email else None,
        template=ReceiptTemplate(email.subject, email.html, email.text)
        if email
        else ReceiptTemplate(),
        block=SafeContent(block.html, block.text) if block else SafeContent("", ""),
        values=public_values(
            source,
            parish=parish,
            campaign=campaign.active_configuration.values,
            public_origin=public_origin,
        ),
        submitted_at=submission.submitted_at,
        campaign_timezone=campaign.active_configuration.timezone,
        date_format=dates.normalized(parish.get("date_format")),
        sender=integration.settings["sender"],
        reply_to=integration.settings["reply_to"],
        intended_recipients=source.recipients.deliverable,
        testing_recipient=runtime.testing_recipient
        if identity.mode == "testing"
        else None,
        banner=email_banner(
            banner_for_email(
                campaign.active_configuration.values,
                "confirmation",
                origin=public_origin,
            ),
            campaign.active_configuration.values["name"],
        ),
        files=links_for(
            public_origin,
            *(part for row in (email, block) if row for part in (row.html, row.text)),
        ),
    )
