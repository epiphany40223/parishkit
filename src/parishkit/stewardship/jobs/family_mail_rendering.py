"""One current, credential-redacted rendering path for preparation and dispatch."""

from uuid import UUID

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.campaigns.work_locks import require_work_order

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
    if not isinstance(template_record_id, UUID):
        raise TypeError("Rendering requires a canonical template record identity.")
    version = scope.runtime.active_configuration
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
            parish=version.canonical_document["sections"]["parish"][0]["values"],
            campaign=scope.campaign.active_configuration.values,
            public_origin=public_origin,
        ),
        sender=email.settings["sender"],
        reply_to=email.settings["reply_to"],
        intended_recipients=source.recipients.deliverable,
        testing_recipient=scope.runtime.testing_recipient
        if identity.mode == "testing"
        else None,
    )
