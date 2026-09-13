"""Closed, intentional source-read failures, distinct from arbitrary OS errors."""

from functools import wraps

from parishkit.stewardship.storage import StorageInvariantError


def local_read_admission(function):
    """Keep local preflight/cleanup errors out of upstream DTO classification.

    Shared loaders intentionally catch parser ValueErrors (including ConfigError).
    Local callbacks execute inside those loaders but never parse provider data;
    preserve that boundary without translating lost fences or typed scope changes.
    """

    @wraps(function)
    def checked(*args, **kwargs):
        """Convert only local data/configuration failures, without private text."""
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StorageInvariantError(
                "Local source admission is unavailable."
            ) from None

    return checked


class SourceScopeChanged(PermissionError):
    """Previously admitted source input changed while this read was in flight."""


class SourceCredentialChanged(PermissionError):
    """Loaded source bytes no longer match their explicitly bound fingerprint."""
