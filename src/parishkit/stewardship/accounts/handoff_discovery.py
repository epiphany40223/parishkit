"""Installer-owned public-key publication; web only obtains an encryption type."""

import logging

from django.db import transaction

from parishkit.config import ConfigError
from parishkit.stewardship.observability import Event, emit

from .credential_database import admit_installer_database
from .credential_handoff import PrivateHandoff, PublicHandoff
from .cryptography import Key
from .handoff_models import PublicCredentialHandoff
from .secret_models import SECRET_TARGETS


def publish_handoff(private):
    """Derive public bytes from the admitted installer's actual private handoff.

    Publication is immutable and idempotent; a mismatch cannot strand already
    sealed requests by silently replacing their advertised encryption key.
    The public table never receives private material or a private serialization.
    """
    if not isinstance(private, PrivateHandoff):
        raise TypeError("An admitted target-specific private handoff is required.")
    admit_installer_database(private.target)
    public = private.public()
    with transaction.atomic():
        row, _ = PublicCredentialHandoff.objects.get_or_create(
            target=public.target,
            defaults={"key_id": public.key.id, "public_key": public.key.material},
        )
        if (row.key_id, bytes(row.public_key)) != (public.key.id, public.key.material):
            emit(Event.HANDOFF_KEY_MISMATCH, level=logging.ERROR)
            raise ConfigError(
                "Published credential handoff differs from this installer."
            )
    return public.key.fingerprint


def public_handoff(target):
    """Resolve only published public material, never an installer mount or secret."""
    if type(target) is not str or target not in SECRET_TARGETS:
        raise ValueError("Unknown credential target.")
    row = PublicCredentialHandoff.objects.filter(target=target).first()
    if row is None:
        raise ConfigError("The target installer has not published its public handoff.")
    return PublicHandoff(target, Key(row.key_id, "active", bytes(row.public_key)))
