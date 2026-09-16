"""Public-key-only preparation of exact retained Family credential references."""

from parishkit.stewardship.accounts.cryptography import (
    GeneralKeyring,
    TokenPublicKeyring,
)
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyAccessTokenGeneration,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.rehearsals import (
    code_context as rehearsal_code_context,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order

from .family_mail_content import seal_family_credentials


def seal_current_credentials(*, identity, render, campaign, family, general, public):
    """Never load primary token ciphertext, much less decrypt it in this process.

    The owning transaction already admitted campaign/mode/epoch and retains the
    work lock through outbox insertion. The dispatcher will independently repeat
    those checks before resolving the opaque token reference.
    """
    require_work_order()
    if not isinstance(general, GeneralKeyring) or not isinstance(
        public, TokenPublicKeyring
    ):
        raise TypeError("Preparation requires general and public keyrings only.")
    if identity.family_id != family.pk or identity.campaign_id != campaign.pk:
        raise PermissionError("Family credential scope differs.")
    with key_set_lock(general, public):
        generation_id, credential_epoch = None, None
        if identity.mode == "testing":
            credential = RehearsalCredential.objects.only("id", "code_ciphertext").get(
                family=family, epoch_id=identity.rehearsal_epoch_id
            )
            token_id = credential.pk
            ciphertext, context = (
                credential.code_ciphertext,
                rehearsal_code_context(credential.pk),
            )
        else:
            deployment = DeploymentCredentialState.objects.get()
            generation = FamilyAccessTokenGeneration.objects.get(
                pk=campaign.active_token_generation_id,
                campaign=campaign,
                state="active",
                credential_epoch=deployment.family_link_epoch,
            )
            token_id = FamilyAccessToken.objects.values_list("id", flat=True).get(
                family=family,
                campaign=campaign,
                generation=generation,
                destroyed_at__isnull=True,
            )
            ciphertext, context = family.code_ciphertext, code_context(family.pk)
            generation_id, credential_epoch = generation.pk, generation.credential_epoch
        return seal_family_credentials(
            identity=identity,
            render=render,
            public=public,
            code=general.decrypt(ciphertext, context=context).decode("ascii"),
            token_id=token_id,
            token_generation_id=generation_id,
            credential_epoch_id=credential_epoch,
        )
