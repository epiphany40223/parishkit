"""Dispatch-only current rendering and in-memory reusable-token resolution."""

from html import escape
from uuid import UUID

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.cryptography import token_digest
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyAccessTokenGeneration,
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.link_tokens import token_context
from parishkit.stewardship.campaigns.rehearsals import (
    token_context as rehearsal_context,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.family_delivery import FamilyDeliveryMail

from .family_mail_content import (
    CODE_PLACEHOLDER,
    LINK_PLACEHOLDER,
    FamilyMailTemplate,
    open_family_credentials,
    render_family_mail,
    seal_family_credentials,
)
from .family_mail_inputs import load_family_mail_source, public_values
from .outbox_storage import _status
from .outbox_validation import RenderInput, SealedSubstitutions


def retained_render(message):
    """Recover the exact immutable rendering used to authenticate substitutions."""
    row = message.render
    return RenderInput(
        **{field: getattr(row, field) for field in RenderInput.__dataclass_fields__}
    )


def current_content(message, occurrence, scope, *, private, public_origin):
    """Refresh unsent content without general-key mounts or cross-epoch fallback.

    Source, settings, template and routed recipients are pinned before returning.
    The caller journals this rendering and commits submission under the same work
    lock. A generation change remains held for its restore/reopen owner.
    """
    require_work_order()
    identity = _status(message).identity
    with key_set_lock(private, private.public()):
        reference = open_family_credentials(
            identity=identity,
            render=retained_render(message),
            private=private,
            sealed=SealedSubstitutions(
                message.sealed_substitutions,
                message.token_generation_id,
                message.credential_epoch_id,
            ),
        )
        if message.mode == "testing":
            credential = RehearsalCredential.objects.only(
                "id", "token_ciphertext", "token_digest"
            ).get(
                pk=reference.token_id,
                family_id=message.family_id,
                epoch_id=message.rehearsal_epoch_id,
            )
            ciphertext, context, digest = (
                credential.token_ciphertext,
                rehearsal_context(credential.pk),
                credential.token_digest,
            )
        else:
            deployment = DeploymentCredentialState.objects.get()
            if (
                message.token_generation_id != scope.campaign.active_token_generation_id
                or message.credential_epoch_id != deployment.family_link_epoch
                or not FamilyAccessTokenGeneration.objects.filter(
                    pk=message.token_generation_id,
                    campaign_id=message.campaign_id,
                    state="active",
                    credential_epoch=deployment.family_link_epoch,
                ).exists()
            ):
                raise PermissionError("Family delivery credential generation is held.")
            credential = FamilyAccessToken.objects.only(
                "id", "ciphertext", "digest"
            ).get(
                pk=reference.token_id,
                family_id=message.family_id,
                campaign_id=message.campaign_id,
                generation_id=message.token_generation_id,
                destroyed_at__isnull=True,
            )
            ciphertext, context, digest = (
                credential.ciphertext,
                token_context(credential.pk),
                credential.digest,
            )
        token = private.decrypt(ciphertext, context=context).decode("ascii")
        if token_digest(token, message.campaign_id) != digest:
            raise PermissionError("Family delivery token fingerprint differs.")
        family = FamilyCampaign.objects.only(
            "id", "campaign_id", "family_duid", "source_generation"
        ).get(pk=message.family_id)
        source = load_family_mail_source(family)
        version = scope.runtime.active_configuration
        template = ContentVersion.objects.get(
            configuration=version,
            campaign_id=message.campaign_id,
            kind="email",
            record_id=UUID(occurrence.revision.values["template_version"]),
        )
        email = AppliedIntegration.objects.get(configuration=version, kind="email")
        render = render_family_mail(
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
            if message.mode == "testing"
            else None,
        )
        sealed = seal_family_credentials(
            identity=identity,
            render=render,
            public=private.public(),
            code=reference.code,
            token_id=reference.token_id,
            token_generation_id=message.token_generation_id,
            credential_epoch_id=message.credential_epoch_id,
        )
        url = public_origin + "/access/" + token
        mail = FamilyDeliveryMail(
            message.semantic_key,
            render.sender,
            render.reply_to,
            render.routed_recipients,
            render.subject,
            render.html.replace(CODE_PLACEHOLDER, escape(reference.code)).replace(
                LINK_PLACEHOLDER, escape(url, quote=True)
            ),
            render.text.replace(CODE_PLACEHOLDER, reference.code).replace(
                LINK_PLACEHOLDER, url
            ),
        )
        return render, sealed, mail
