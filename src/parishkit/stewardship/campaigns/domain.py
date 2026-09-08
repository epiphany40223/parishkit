"""Canonical persisted vocabulary and pure values, independent of Django models.

Database-only state machines belong to their owning apps. Import these values
there instead of declaring parallel lifecycle, mode, or role strings.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CampaignState(StrEnum):
    """Campaign lifecycle values; membership does not authorize a transition."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"
    PURGING = "purging"
    PURGE_CLEANUP_FAILED = "purge_cleanup_failed"
    PURGED = "purged"


class SystemMode(StrEnum):
    """Deployment mode, separate from lifecycle and live-delivery pause."""

    TESTING = "testing"
    PRODUCTION = "production"


class CampaignModule(StrEnum):
    """Independently enabled campaign modules."""

    CENSUS = "census"
    MINISTRY = "ministry"
    FINANCIAL = "financial"


class PortalRole(StrEnum):
    """Google portal roles; Family identity is a separate principal type."""

    ADMINISTRATOR = "administrator"
    STAFF = "staff"
    MINISTRY_LEADER = "ministry_leader"


class ValueOrigin(StrEnum):
    """Distinguish upstream, immutable submission, and authorized review values.

    Proposed values can include Admin edits; a repeat Family visit uses the
    immutable FAMILY_SUBMITTED value, never an unresolved review edit.
    """

    BASELINE = "baseline"
    CURRENT = "current"
    FAMILY_SUBMITTED = "family_submitted"
    PROPOSED = "proposed"


@dataclass(frozen=True)
class EnabledModules:
    """Nonempty module set with deterministic YAML/JSON list serialization."""

    values: frozenset[CampaignModule]

    def __post_init__(self) -> None:
        """Reject mutable containers and unvalidated strings at domain boundaries."""
        if not isinstance(self.values, frozenset) or not self.values:
            raise ValueError("at least one campaign module is required")
        if any(not isinstance(value, CampaignModule) for value in self.values):
            raise ValueError("campaign modules must be canonical enum values")

    def to_list(self) -> list[str]:
        """Return stable ordering, independent of input or set iteration order."""
        return sorted(value.value for value in self.values)

    @classmethod
    def from_list(cls, values: list[str]) -> "EnabledModules":
        """Parse a serialized module list, rejecting duplicates and unknown names."""
        if not isinstance(values, list) or any(
            type(value) is not str for value in values
        ):
            raise ValueError("campaign modules must be a list of strings")
        if len(set(values)) != len(values):
            raise ValueError("campaign modules must not repeat")
        return cls(frozenset(CampaignModule(value) for value in values))


@dataclass(frozen=True)
class Money:
    """Exact signed USD cents; pledge nonnegativity is a separate form policy.

    A symmetric bound of $999,999,999.99 also accommodates source adjustments.
    Do not convert money through a float or derive annual totals from rounded
    installments. Persist cents or a Decimal, and serialize as a decimal string.
    """

    cents: int

    def __post_init__(self) -> None:
        """Reject bools, floats, and values outside the shared reporting bound."""
        if type(self.cents) is not int or abs(self.cents) > 99_999_999_999:
            raise ValueError("money must be integer cents within the supported bound")

    def to_string(self) -> str:
        """Return canonical two-place USD without binary or Decimal rounding."""
        sign = "-" if self.cents < 0 else ""
        dollars, cents = divmod(abs(self.cents), 100)
        return f"{sign}{dollars}.{cents:02d}"

    @classmethod
    def from_string(cls, value: str) -> "Money":
        """Parse exact decimal USD, rejecting nonfinite and fractional-cent values."""
        if type(value) is not str:
            raise ValueError("money must be serialized as a decimal string")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("money must be a decimal string") from exc
        if not amount.is_finite() or amount.copy_abs() > Decimal("999999999.99"):
            raise ValueError("money must be finite and within the supported bound")
        # Bounded amounts still may have arbitrarily many fractional digits.
        # Compare against an exact two-place representation before converting;
        # multiplication under the default Decimal context could round them.
        with localcontext(prec=28, rounding=ROUND_HALF_UP):
            rounded = amount.quantize(Decimal("0.01"))
            if amount != rounded:
                raise ValueError("money cannot contain fractional cents")
            return cls(int(rounded * 100))


@dataclass(frozen=True)
class Percentage:
    """Keep exact count inputs; a zero denominator is unavailable, not zero."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        """Reject fractional, boolean, and negative counts before calculation."""
        for value in (self.numerator, self.denominator):
            if type(value) is not int or value < 0:
                raise ValueError("percentage inputs must be nonnegative integer counts")

    @property
    def value(self) -> Decimal | None:
        """Compute the percentage without prematurely rounding report inputs."""
        if self.denominator == 0:
            return None
        with localcontext(prec=28, rounding=ROUND_HALF_UP):
            return Decimal(self.numerator) * 100 / Decimal(self.denominator)

    def to_dict(self) -> dict[str, int]:
        """Serialize exact inputs, not a lossy rounded display percentage."""
        return {"numerator": self.numerator, "denominator": self.denominator}


@dataclass(frozen=True)
class UTCInterval:
    """Half-open UTC interval; an empty interval represents a skipped local day."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Reject naive instants and normalize aware inputs before comparing."""
        for field in ("start", "end"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("interval boundaries must be timezone-aware instants")
            object.__setattr__(self, field, value.astimezone(UTC))
        if self.end < self.start:
            raise ValueError("interval end must not precede its start")

    def contains(self, instant: datetime) -> bool:
        """Use exact half-open boundaries, independent of scheduler state."""
        if not isinstance(instant, datetime) or instant.utcoffset() is None:
            raise ValueError("interval membership requires an aware instant")
        return self.start <= instant.astimezone(UTC) < self.end

    def to_dict(self) -> dict[str, str]:
        """Serialize normalized UTC instants with explicit offsets."""
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class LocalDayInterval:
    """Campaign-local date/timezone plus its resolved UTC interval.

    DOM-02 owns the one canonical DST resolver that constructs this value. This
    record validates types, not a second interpretation of gaps and folds.
    """

    day: date
    timezone: str
    interval: UTCInterval

    def __post_init__(self) -> None:
        """Require a date (not datetime), IANA zone, and typed UTC interval."""
        if type(self.day) is not date or not isinstance(self.interval, UTCInterval):
            raise ValueError("local day requires a date and UTC interval")
        if type(self.timezone) is not str:
            raise ValueError("local day timezone must be an IANA identifier")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("local day timezone must be an IANA identifier") from None

    def to_dict(self) -> dict[str, object]:
        """Retain the immutable campaign timezone alongside normalized instants."""
        return {
            "day": self.day.isoformat(),
            "timezone": self.timezone,
            "interval": self.interval.to_dict(),
        }
