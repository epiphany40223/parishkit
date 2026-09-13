"""Durable configured status, distinct from prepared YAML and consumer readiness."""

from .setup_install_models import SetupCompletion


def setup_is_complete():
    """A committed immutable marker survives later campaigns and configurations.

    Normal authentication independently checks current YAML/SQL coherence. Never
    use the historical initial snapshot or configuration as today's authority,
    and never infer completion from a prepared configuration or source catalog.
    """
    return SetupCompletion.objects.exists()
