"""Closed, intentional source-read failures, distinct from arbitrary OS errors."""

from functools import wraps

from parishkit.stewardship.accounts.authority import AuthorityChanging
from parishkit.stewardship.storage import StorageInvariantError


def local_read_admission(function):
    """Keep local preflight/cleanup errors out of upstream DTO classification.

    Shared loaders intentionally catch parser ValueErrors (including ConfigError).
    Local callbacks execute inside those loaders but never parse provider data;
    preserve that boundary without translating lost fences or typed scope changes.
    A configuration activation in progress (#429) becomes its own typed hold.
    """

    @wraps(function)
    def checked(*args, **kwargs):
        """Convert only local data/configuration failures, without private text."""
        try:
            return function(*args, **kwargs)
        except AuthorityChanging:
            raise SourceAuthorityChanging(
                "Configuration is activating; the read holds."
            ) from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StorageInvariantError(
                "Local source admission is unavailable."
            ) from None

    return checked


class SourceScopeChanged(PermissionError):
    """Previously admitted source input changed while this read was in flight."""


class SourceAuthorityChanging(SourceScopeChanged):
    """A configuration change was activating when this read rechecked admission.

    The carrier of AuthorityChanging out of a shared loader, which treats a
    ValueError as invalid provider data (#429).
    """


class SourceCredentialChanged(PermissionError):
    """Loaded source bytes no longer match their explicitly bound fingerprint."""


class SourceOrganizationChanged(PermissionError):
    """Applied tenant configuration conflicts with retained source truth."""
