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
        reference = _opened_reference(message, identity, scope.campaign, private)
        token = _claimed_plaintext(message, reference, claim, private)
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
        sealed = _resealed(message, identity, render, reference, private)
        return render, sealed, _mail(message, render, reference, token, public_origin)


def built_content(message, identity, campaign, render, *, private):
    """``current_content``'s decryption and sealing without the work-order lock.

    For a bulk send build (BG-12, ``family_mail_send_builds``): the caller
    has rendered ``render`` outside the lock and owns a short transaction.
    This takes the credential key-set lock there (shared and non-waiting,
    with its inventory check, held to the end of that transaction), opens
    the message's own sealed reference, makes the same generation and epoch
    check, and seals the reference again for ``render``. Returns the opened
    reference (the plaintext code, in memory only) and the sealed
    substitutions. It reads no link token: that needs the delivery claim,
    which only the commit under the lock holds (``prebuilt_content``).
    Nothing is written, so no credential changes.
    """
    with key_set_lock(private, private.public()):
        reference = _opened_reference(message, identity, campaign, private)
        return reference, _resealed(message, identity, render, reference, private)


def prebuilt_matches(message, prebuilt, *, private, current):
    """Whether a send build still matches its message, under the lock (BG-12).

    Runs under the work-order lock, inside the commit half's transaction,
    before the message's pause hold (if any) is released: the release
    moves only the message's version and command columns, never a content
    input, but the version is in the fingerprint, so comparing after it
    would rebuild every message held during a pause. It takes the key-set
    lock as ``current_content`` does (shared, non-waiting, with its
    inventory check); that lock is transaction-scoped, so it stays held
    from this comparison to the commit and no rotation can land in
    between, and a busy or stale key set raises exactly as there.
    ``current()`` is the message's fingerprint now. False when it differs
    from the build's, or the build is another message's; the caller then
    renders under the lock as before.
    """
    require_work_order()
    identity = _status(message).identity
    if prebuilt.message_id != message.pk or prebuilt.identity != identity:
        return False
    with key_set_lock(private, private.public()):
        return current() == prebuilt.fingerprint


def prebuilt_content(message, prebuilt, scope, *, claim, private, public_origin):
    """``current_content`` from a send build ``prebuilt_matches`` accepted.

    Runs under the work-order lock at the point where ``current_content``
    would, in the transaction that made the comparison, so the key-set lock
    is still held (it is taken again here, with its inventory check). The
    generation check is made again and the link token, which needs
    ``claim``, is resolved here as ``current_content`` resolves it; the
    build's render and sealed substitutions are returned with the assembled
    mail.
    """
    require_work_order()
    with key_set_lock(private, private.public()):
        _check_generation(message, scope.campaign)
        token = _claimed_plaintext(message, prebuilt.reference, claim, private)
        return (
            prebuilt.render,
            prebuilt.sealed,
            _mail(message, prebuilt.render, prebuilt.reference, token, public_origin),
        )


def _opened_reference(message, identity, campaign, private):
    """Open the message's sealed code and token reference, then check it is current.

    The caller holds the key-set lock. Testing needs no generation check;
    Production refuses a reference whose token generation or credential
    epoch is no longer the campaign's active one (``_check_generation``).
    """
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
    _check_generation(message, campaign)
    return reference


def _check_generation(message, campaign):
    """Refuse a Production message whose token generation or epoch has moved."""
    if message.mode == "testing":
        return
    deployment = DeploymentCredentialState.objects.get()
    if (
        message.token_generation_id != campaign.active_token_generation_id
        or message.credential_epoch_id != deployment.family_link_epoch
        or not FamilyAccessTokenGeneration.objects.filter(
            pk=message.token_generation_id,
            campaign_id=message.campaign_id,
            state="active",
            credential_epoch=deployment.family_link_epoch,
        ).exists()
    ):
        raise PermissionError("Family delivery credential generation is held.")


def _claimed_plaintext(message, reference, claim, private):
    """Decrypt the message's own link token under ``claim`` and check its digest."""
    if message.mode == "testing":
        context = rehearsal_context(reference.token_id)
    else:
        context = token_context(reference.token_id)
    ciphertext, digest = claimed_token(message, reference.token_id, claim)
    token = private.decrypt(ciphertext, context=context).decode("ascii")
    if token_digest(token, message.campaign_id) != digest:
        raise PermissionError("Family delivery token fingerprint differs.")
    return token


def _resealed(message, identity, render, reference, private):
    """Seal the opened reference again for ``render``, keeping its generation."""
    return seal_family_credentials(
        identity=identity,
        render=render,
        public=private.public(),
        code=reference.code,
        token_id=reference.token_id,
        token_generation_id=message.token_generation_id,
        credential_epoch_id=message.credential_epoch_id,
    )


def _mail(message, render, reference, token, public_origin):
    """The provider mail: the render with the code and link substituted."""
    url = public_origin + "/access/" + token
    return FamilyDeliveryMail(
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
