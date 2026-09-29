"""One complete shared-client load, ready for fenced staging but not promotion."""

import logging
import os
from dataclasses import dataclass
from datetime import date

from parishkit.config import ConfigError
from parishkit.parishsoft import load_families_and_members, load_funds
from parishkit.parishsoft_pagination import ShiftedSourceScan
from parishkit.parishsoft_source import (
    CoherentParishSoftClient,
    SourceOrganizationMismatch,
)
from parishkit.stewardship.observability import debug_logging_enabled

from .canonical import InvalidSourcePayload
from .corpus import KINDS, normalize_core
from .giving import load_giving
from .windows import RefreshWindow

TREND_COLLECTIONS = ("family", "member", "ministry", "roster", "fund")
# Counts derived from the records rather than of them. ParishSoft can keep
# every row while dropping or nulling the fields eligibility is derived from
# (organization, status, member type, email); promotion would then make those
# Families ineligible at once (#320). The same loss threshold applies to these.
DERIVED_COUNTS = (
    "portal_eligible_families",
    "email_eligible_families",
    "active_head_families",
    "valid_email_contacts",
)
# The closed names a refusal may report: a record collection, a derived count,
# or "empty" for a load with no Families or Members.
LOSS_MEASURES = frozenset({*TREND_COLLECTIONS, *DERIVED_COUNTS, "empty"})

DEFAULT_MAXIMUM_DROP_PERCENT = 25
# Operator override for one refresh that must accept a known large change,
# such as a parish inactivating many Families at once. It is read from the
# worker's environment (see the deployment runbook) rather than the deployment
# YAML, which is fixed once provisioned. 100 accepts any drop; only a load
# with no Families or Members is still refused.
DROP_OVERRIDE_VARIABLE = "PARISHKIT_SOURCE_MAX_DROP_PERCENT"


def maximum_drop_percent():
    """The loss threshold in effect: the override when valid, else the default.

    An unset, blank or malformed override keeps the default, so a typo can
    never switch the guard off.
    """
    value = os.environ.get(DROP_OVERRIDE_VARIABLE, "").strip()
    if value.isascii() and value.isdigit() and 0 <= int(value) <= 100:
        return int(value)
    return DEFAULT_MAXIMUM_DROP_PERCENT


class DestructiveSourceChange(InvalidSourcePayload):
    """Complete-looking source data unexpectedly removes core parish records.

    ``loss`` names which count fell, from ``LOSS_MEASURES``, with its before
    and after values. These are counts, never record values, so the refusal
    can be logged in ordinary output for the operator.
    """

    def __init__(self, message, *, measure=None, before=None, after=None):
        """Keep the closed measure name and counts alongside the fixed message."""
        super().__init__(message)
        self.loss = (measure, before, after) if measure in LOSS_MEASURES else None


@dataclass(frozen=True)
class SourceLoad:
    """Validated collections and nonprivate completeness evidence for one attempt."""

    corpus: dict
    counts: dict
    evidence: dict


def validate_count_trend(counts, *, previous_full_counts, maximum_drop_percent=25):
    """Unexpected empty/large-loss core data cannot replace the last full baseline.

    Giving is excluded: its scope intentionally changes with campaign windows,
    and records can legitimately decrease after adjustments. Contact/address
    edits likewise do not imply lost core identities. This is a validation
    threshold, never permission to truncate a collection or bypass completeness.
    """
    if type(maximum_drop_percent) is not int or not 0 <= maximum_drop_percent <= 100:
        raise ValueError("The source loss threshold must be between 0 and 100 percent.")
    for values in (counts, previous_full_counts):
        if values is not None and (
            type(values) is not dict
            or set(values) != set(KINDS)
            or any(type(value) is not int or value < 0 for value in values.values())
        ):
            raise InvalidSourcePayload("Source count evidence is incomplete.")
    if counts is None or counts["family"] == 0 or counts["member"] == 0:
        raise DestructiveSourceChange(
            "Source Family/Member corpus is unexpectedly empty.",
            measure="empty",
            before=None,
            after=0,
        )
    if previous_full_counts is None:
        return
    for kind in TREND_COLLECTIONS:
        _check_drop(
            kind,
            previous_full_counts[kind],
            counts[kind],
            maximum_drop_percent,
            "Source corpus exceeds the permitted count loss.",
        )


def _check_drop(measure, before, after, maximum_drop_percent, message):
    """Refuse a nonzero count that fell to zero or by more than the percent.

    A threshold of 100 accepts every drop. Below that, even a count of one or
    two falling to zero is refused: on a tiny parish that is still a loss of
    every such record, which the operator must accept explicitly.
    """
    if maximum_drop_percent < 100 and (
        before and (not after or (before - after) * 100 > before * maximum_drop_percent)
    ):
        raise DestructiveSourceChange(
            message, measure=measure, before=before, after=after
        )


def derived_counts(corpus):
    """Count eligibility-bearing facts of one corpus (``DERIVED_COUNTS``).

    Portal-eligible, email-eligible and active-head Families come from the
    normalized Family rows; valid-email contacts from every contact row. The
    same function counts a new load and the current promoted snapshot, so the
    two sides of the comparison cannot be defined differently.
    """
    families = corpus["family"].values()
    return {
        "portal_eligible_families": sum(
            1 for row in families if row.get("portal_eligible") is True
        ),
        "email_eligible_families": sum(
            1 for row in families if row.get("email_eligible") is True
        ),
        "active_head_families": sum(
            1 for row in families if row.get("active_head_duids")
        ),
        "valid_email_contacts": sum(
            1
            for row in corpus["contact"].values()
            if any(email.get("valid") is True for email in row.get("emails", ()))
        ),
    }


def valid_derived_counts(value):
    """Whether ``value`` is complete ``derived_counts`` evidence."""
    return (
        type(value) is dict
        and set(value) == set(DERIVED_COUNTS)
        and all(type(count) is int and count >= 0 for count in value.values())
    )


def derived_baseline(*candidates):
    """Combine the last full and current snapshots' derived counts into one baseline.

    Each count takes the larger of the available values. A drop is refused
    when it exceeds the threshold from either snapshot, and comparing with
    the larger one is exactly that check. The last full snapshot bounds the
    loss accumulated over a series of deltas, as it does for record counts.
    The current snapshot catches a full load that drops below a delta that
    grew. Missing or malformed evidence is skipped, and ``None`` means there
    is nothing to compare with.
    """
    usable = [value for value in candidates if valid_derived_counts(value)]
    if not usable:
        return None
    return {name: max(value[name] for value in usable) for name in DERIVED_COUNTS}


def validate_derived_trend(counts, *, baseline_counts, maximum_drop_percent=25):
    """Refuse a load whose derived counts fell too far below the baseline.

    ``counts`` are this load's ``derived_counts``. ``baseline_counts`` come
    from ``derived_baseline`` (``None`` before the first promotion). The
    threshold and the refusal are the record-count guard's. Call
    ``validate_count_trend`` first, since it validates the threshold.
    """
    if baseline_counts is None:
        return
    if not valid_derived_counts(baseline_counts):
        raise InvalidSourcePayload("Source count evidence is incomplete.")
    for name in DERIVED_COUNTS:
        _check_drop(
            name,
            baseline_counts[name],
            counts[name],
            maximum_drop_percent,
            "Source eligibility exceeds the permitted count loss.",
        )


def load_full_source(
    client,
    *,
    window,
    as_of,
    previous_full_counts=None,
    previous_derived_counts=None,
    maximum_drop_percent=DEFAULT_MAXIMUM_DROP_PERCENT,
    progress=None,
):
    """Fetch, normalize and validate without SQL or a mutable source pointer.

    The worker begins its manifest before this call, so its durable next delta
    watermark describes the start of network observation, never the end. Its
    bounded Session owns admission/fencing before each request. Callers stage
    this result only after validation succeeds and must still check admission
    and the exact campaign window again at atomic promotion. An optional
    ``progress(collection, count)`` observer hears about each downloaded
    collection in the order ``load_progress`` expects.
    """
    if (
        not isinstance(client, CoherentParishSoftClient)
        or not isinstance(window, RefreshWindow)
        or type(as_of) is not date
    ):
        raise InvalidSourcePayload(
            "A coherent client, source window and date are required."
        )
    try:
        data = load_families_and_members(
            client,
            active_only=False,
            parishioners_only=False,
            include_deceased=True,
            retain_empty_families=True,
            load_contributions=False,
            # Stewardship never reads workgroups (the delta load leaves them
            # empty too); fetching them took about seven of fourteen minutes.
            load_workgroups=False,
            progress=progress,
        )
        # Catalogs are needed by initial campaign preparation even when no
        # giving window exists yet. Shared data is frozen; its collections are not.
        data.funds.update(load_funds(client, data.organization_id))
        if progress is not None:
            progress("funds", len(data.funds))
        corpus = normalize_core(data, as_of=as_of)
        giving = load_giving(client, corpus=corpus, window=window, as_of=as_of)
        corpus.update(pledge=giving.pledges, contribution=giving.contributions)
    except (InvalidSourcePayload, SourceOrganizationMismatch, ShiftedSourceScan):
        # A shifted scan keeps its type so the worker retries it (see failures).
        raise
    except (ConfigError, KeyError, TypeError, ValueError, OverflowError) as error:
        # Ordinary shared tools retain their diagnostics. The app boundary
        # cannot persist raw upstream values embedded in a parser exception,
        # except in explicitly enabled pre-launch debug logging.
        if debug_logging_enabled():
            logging.getLogger("parishkit.stewardship.debug").debug(
                "invalid provider data", exc_info=error
            )
        raise InvalidSourcePayload(
            "The source load contains invalid provider data."
        ) from None
    counts = {kind: len(rows) for kind, rows in corpus.items()}
    validate_count_trend(
        counts,
        previous_full_counts=previous_full_counts,
        maximum_drop_percent=maximum_drop_percent,
    )
    derived = derived_counts(corpus)
    validate_derived_trend(
        derived,
        baseline_counts=previous_derived_counts,
        maximum_drop_percent=maximum_drop_percent,
    )
    return SourceLoad(
        corpus,
        counts,
        {
            "schema": "source-load-v1",
            "window_digest": window.digest,
            "maximum_drop_percent": maximum_drop_percent,
            "anonymous_pledges": giving.anonymous_pledges,
            "anonymous_contributions": giving.anonymous_contributions,
            "requests": client.request_count,
            "response_bytes": client.response_bytes,
            "as_of_date": as_of.isoformat(),
            "giving_as_of_date": as_of.isoformat(),
            # Retained in the manifest, so later refreshes can compare with it
            # even after the snapshot's rows are compacted.
            "derived_counts": derived,
        },
    )
