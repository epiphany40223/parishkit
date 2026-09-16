"""Closed expected preparation failures, without provider or private values."""

from parishkit.stewardship.storage import StorageInvariantError


class CatchUpPreparationHeld(StorageInvariantError):
    """An unresolved group keeps the demand held and requires owning recovery."""
