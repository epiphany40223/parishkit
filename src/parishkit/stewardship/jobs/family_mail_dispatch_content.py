"""Dispatch-only current rendering and in-memory reusable-token resolution."""

from html import escape

from django.db import connection

from parishkit.stewardship.accounts.cryptography import token_digest
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    DeploymentCredentialState,
    FamilyAccessTokenGeneration,
    FamilyCampaign,
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
    open_family_credentials,
    seal_family_credentials,
)
from .family_mail_inputs import load_family_mail_source
from .family_mail_rendering import current_render
from .outbox_storage import _status
from .outbox_validation import RenderInput, SealedSubstitutions


def retained_render(message):
    """Recover the exact immutable rendering used to authenticate substitutions."""
    row = message.render
    return RenderInput(
        **{field: getattr(row, field) for field in RenderInput.__dataclass_fields__}
    )


def claimed_token(message, token_id, claim):
    """The ciphertext and digest of the message's own token, under ``claim``.

    Mail dispatch cannot read token ciphertext (#389 L1); the definer
    answers only for an unsent Family message whose delivery task ``claim``
    holds under a live lease, and only that message's Family, campaign and
    token generation (Production) or rehearsal epoch (Testing). No row is
    refused like a token that does not match the message.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT ciphertext, digest FROM "
            "stewardship_dispatch_family_token_v1(%s, %s, %s, %s, %s)",
            [message.pk, token_id, claim.run_id, claim.fence, claim.worker_id],
        )
        row = cursor.fetchone()
    if row is None:
        raise PermissionError("Family delivery token is not under a live claim.")
    return row


def current_content(
    message, template_record_id, scope, *, claim, private, public_origin
):
    """Refresh unsent content without general-key mounts or cross-epoch fallback.

    Source, settings, template and routed recipients are pinned before returning.
    The caller journals this rendering and commits submission under the same work
    lock, holding ``claim``, the message's live delivery task claim. A
    generation change remains held for its restore/reopen owner. The
    template record comes from the occurrence's schedule revision, or from the
    Admin ticket for a chosen-Family test.
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
            context = rehearsal_context(reference.token_id)
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
            context = token_context(reference.token_id)
        ciphertext, digest = claimed_token(message, reference.token_id, claim)
        token = private.decrypt(ciphertext, context=context).decode("ascii")
        if token_digest(token, message.campaign_id) != digest:
            raise PermissionError("Family delivery token fingerprint differs.")
        family = FamilyCampaign.objects.only(
            "id", "campaign_id", "family_duid", "source_generation"
        ).get(pk=message.family_id)
        source = load_family_mail_source(family)
        render = current_render(
            identity,
            template_record_id,
            scope,
            source,
            public_origin=public_origin,
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
            banner_origin=public_origin,
        )
        return render, sealed, mail
