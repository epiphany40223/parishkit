"""One current, credential-redacted rendering path for preparation and dispatch."""

from uuid import UUID

from parishkit.stewardship.accounts.branding_context import banner_for_email
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.hosted_file_content import links_for
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.web.content import email_banner

from .campaign_mail_values import document_parish
from .family_mail_content import FamilyMailTemplate, render_family_mail
from .family_mail_inputs import public_values


def current_render(identity, template_record_id, scope, source, *, public_origin):
    """Use the caller's locked source and the currently selected public settings.

    ``template_record_id`` names the stable email template record; scheduled
    mail takes it from the occurrence's schedule revision and a chosen-Family
    test from its Admin ticket. The copy in the active configuration is used.
    This helper knows no credential keys and grants no sending authority. Each
    owning service separately validates lifecycle and its claim/Admin command.
    """
    require_work_order()
    return build_render(
        identity,
        template_record_id,
        scope.runtime,
        scope.campaign,
        source,
        public_origin=public_origin,
    )


def build_render(
    identity, template_record_id, runtime, campaign, source, *, public_origin
):
    """``current_render`` without the work-order lock, for bulk builds (BG-12).

    ``runtime`` is the system configuration row and ``campaign`` the campaign,
    each with its active configuration. A bulk build calls this inside its own
    REPEATABLE READ snapshot, outside the lock, and the item later compares
    the build's fingerprint under the lock before writing what it rendered;
    the render guard also checks every render against current scope. Given
    the same inputs it renders exactly what ``current_render`` does.
    """
    if not isinstance(template_record_id, UUID):
        raise TypeError("Rendering requires a canonical template record identity.")
    version = runtime.active_configuration
    template = ContentVersion.objects.get(
        configuration=version,
        campaign_id=identity.campaign_id,
        kind="email",
        record_id=template_record_id,
    )
    email = AppliedIntegration.objects.get(configuration=version, kind="email")
    return render_family_mail(
        identity=identity,
        configuration_id=version.pk,
        template_id=template.pk,
        template=FamilyMailTemplate(template.subject, template.html, template.text),
        values=public_values(
            source,
            parish=document_parish(version.canonical_document),
            campaign=campaign.active_configuration.values,
            public_origin=public_origin,
        ),
        sender=email.settings["sender"],
        reply_to=email.settings["reply_to"],
        intended_recipients=source.recipients.deliverable,
        testing_recipient=runtime.testing_recipient
        if identity.mode == "testing"
        else None,
        banner=email_banner(
            banner_for_email(
                campaign.active_configuration.values,
                template.slot,
                origin=public_origin,
            ),
            campaign.active_configuration.values["name"],
        ),
        files=links_for(public_origin, template.html, template.text),
    )
