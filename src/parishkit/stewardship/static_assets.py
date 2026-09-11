"""Build public static assets without loading deployment secrets or a database."""

import secrets
from importlib import import_module

from parishkit.config import ConfigError

from .runtime_paths import private_directory


def collect_static(destination):
    """Populate one empty operator-selected private directory, never clear a tree.

    The output is public code-owned assets only. Caddy later mounts it read-only;
    user uploads and authenticated exports must never be copied into this tree.
    A partial failure preserves files for diagnosis instead of deleting a caller's
    directory. Retry with a new empty target after inspecting the failed output.
    """
    import django
    from django.conf import settings
    from django.core.management import call_command

    if settings.configured:
        raise ConfigError("Static collection requires a fresh non-HTTP process.")
    target = private_directory(destination)
    if any(target.iterdir()):
        raise ConfigError("Static collection requires an empty private directory.")
    base = import_module("parishkit.stewardship.settings.base")
    values = {name: getattr(base, name) for name in dir(base) if name.isupper()}
    values.update(
        SECRET_KEY=secrets.token_urlsafe(48),
        STATIC_ROOT=str(target),
        FILE_UPLOAD_PERMISSIONS=0o600,
        FILE_UPLOAD_DIRECTORY_PERMISSIONS=0o700,
    )
    settings.configure(**values)
    django.setup()
    call_command("collectstatic", interactive=False, verbosity=0, clear=False)
    return {"static_assets_collected": True}
