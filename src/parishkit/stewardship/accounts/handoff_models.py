"""Public-only discovery for target-specific credential encryption."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

from .secret_models import SECRET_TARGETS


class PublicCredentialHandoff(ImmutableRecord):
    """One immutable bootstrap handoff key per target, published by its installer.

    These bytes cannot decrypt anything. Private key rotation/replacement is
    deliberately not an implicit side effect of restarting a service; a
    mismatching startup fails closed pending an explicit future offline owner.
    """

    target = models.CharField(max_length=32, unique=True)
    key_id = models.CharField(max_length=48)
    public_key = models.BinaryField(max_length=32, unique=True)

    class Meta:
        db_table = "stewardship_public_credential_handoff"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target__in=SECRET_TARGETS),
                name="handoff_public_known_target",
            ),
            models.CheckConstraint(
                condition=models.Q(key_id__regex=r"^[A-Za-z0-9_-]{1,48}$"),
                name="handoff_public_key_label",
            ),
        ]
