"""One complete shared-client load, ready for fenced staging but not promotion."""

import logging
import os
from dataclasses import dataclass
from datetime import date

from parishkit.config import ConfigError
from parishkit.parishsoft import load_families_and_members, load_funds
from parishkit.parishsoft_pagination import (
    ShiftedSourceScan,
    SourceLoadBudgetExceeded,
)
from parishkit.parishsoft_source import (
    CoherentParishSoftClient,
    SourceOrganizationMismatch,
)
from parishkit.stewardship.observability import debug_logging_enabled

from .canonical import InvalidSourcePayload
from .corpus import KINDS, normalize_core
from .count_names import DERIVED_COUNTS, TREND_COLLECTIONS
from .giving import load_giving
from .windows import RefreshWindow

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
# Where a full load's evidence records its Member contact list coverage.
CONTACT_COVERAGE_KEY = "member_contacts"


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
    and after values: the first failing count, or "empty". ``checks`` holds
    every count the load was checked on (``CountCheck``), failing or not, so
    the refusal can record all of them (ADM-13). These are counts, never
    record values, so the refusal can be logged in ordinary output for the
    operator.
    """

    def __init__(self, message, *, measure=None, before=None, after=None, checks=()):
        """Keep the closed measure name and counts alongside the fixed message."""
        super().__init__(message)
        self.loss = (measure, before, after) if measure in LOSS_MEASURES else None
        self.checks = tuple(check for check in checks if isinstance(check, CountCheck))


@dataclass(frozen=True)
class CountCheck:
    """One count a load was checked on (ADM-13, #530).

    ``measure`` is a record collection or derived count name; ``before`` is
    the baseline compared with (``None`` before the first promoted load);
    ``after`` is this load's value; ``limit_percent`` is the loss allowed;
    ``failed`` says whether it fell too far. Counts only, never parish data.
    """

    measure: str
    before: int | None
    after: int
    limit_percent: int
    failed: bool


@dataclass(frozen=True)
class SourceLoad:
    """Validated collections and nonprivate completeness evidence for one attempt."""

    corpus: dict
    counts: dict
    evidence: dict


def _valid_limit(maximum_drop_percent):
    """Refuse a threshold that is not a whole percent from 0 to 100."""
    if type(maximum_drop_percent) is not int or not 0 <= maximum_drop_percent <= 100:
        raise ValueError("The source loss threshold must be between 0 and 100 percent.")


def _fell(before, after, maximum_drop_percent):
    """Whether a nonzero count fell to zero or by more than the percent.

    A threshold of 100 accepts every drop. Below that, even a count of one or
    two falling to zero is refused: on a tiny parish that is still a loss of
    every such record, which the operator must accept explicitly. The SQL
    guard on stewardship_source_drop_count applies the same rule.
    """
    return bool(
        maximum_drop_percent < 100
        and before
        and (not after or (before - after) * 100 > before * maximum_drop_percent)
    )


def _checks(names, before, after, maximum_drop_percent):
    """Check each named count of ``after`` against ``before`` (``None``: none)."""
    return [
        CountCheck(
            name,
            None if before is None else before[name],
            after[name],
            maximum_drop_percent,
            before is not None
            and _fell(before[name], after[name], maximum_drop_percent),
        )
        for name in names
    ]


def count_checks(counts, *, previous_full_counts, maximum_drop_percent=25):
    """Check each record collection against the last full baseline.

    Giving is excluded: its scope intentionally changes with campaign windows,
    and records can legitimately decrease after adjustments. Contact/address
    edits likewise do not imply lost core identities. This is a validation
    threshold, never permission to truncate a collection or bypass completeness.
    Missing counts (``None``) give no checks; the caller refuses them as empty.
    """
    _valid_limit(maximum_drop_percent)
    for values in (counts, previous_full_counts):
        if values is not None and (
            type(values) is not dict
            or set(values) != set(KINDS)
            or any(type(value) is not int or value < 0 for value in values.values())
        ):
            raise InvalidSourcePayload("Source count evidence is incomplete.")
    if counts is None:
        return []
    return _checks(
        TREND_COLLECTIONS, previous_full_counts, counts, maximum_drop_percent
    )


def derived_checks(counts, *, baseline_counts, maximum_drop_percent=25):
    """Check each derived count against the baseline from ``derived_baseline``.

    ``counts`` are this load's ``derived_counts``; ``baseline_counts`` is
    ``None`` before the first promotion.
    """
    _valid_limit(maximum_drop_percent)
    if baseline_counts is not None and not valid_derived_counts(baseline_counts):
        raise InvalidSourcePayload("Source count evidence is incomplete.")
    return _checks(DERIVED_COUNTS, baseline_counts, counts, maximum_drop_percent)


def refuse_drops(checks, *, empty=False):
    """Raise ``DestructiveSourceChange`` carrying every check if the load fails.

    A load with no Families or no Members is refused at any threshold;
    otherwise the first failing count names the refusal, and the message says
    whether a record or an eligibility count fell.
    """
    if empty:
        raise DestructiveSourceChange(
            "Source Family/Member corpus is unexpectedly empty.",
            measure="empty",
            before=None,
            after=0,
            checks=checks,
        )
    failed = next((check for check in checks if check.failed), None)
    if failed is not None:
        raise DestructiveSourceChange(
            "Source corpus exceeds the permitted count loss."
            if failed.measure in TREND_COLLECTIONS
            else "Source eligibility exceeds the permitted count loss.",
            measure=failed.measure,
            before=failed.before,
            after=failed.after,
            checks=checks,
        )


def _empty(counts):
    """Whether a load has no Families or no Members (or no counts at all)."""
    return counts is None or counts["family"] == 0 or counts["member"] == 0


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


# A full load is refused as a shifted scan when the Members its contact list
# left out rise above the last full refresh's count by more than this margin
# (#387 M4): CONTACT_MISSING_MARGIN Members, or CONTACT_MISSING_MARGIN_PERCENT
# of the Members searched, whichever is larger (128 of 6,400). The list is
# read in pages of 500 rows, so a list that lost a whole page is far past the
# margin, while ordinary change between two full refreshes (a handful of
# Members added or inactivated, whom the list may legitimately omit) stays
# well inside it; one Production parish's count held exactly steady over 12
# full loads. Erring wide matters because a refusal blocks every full
# refresh until the count recovers or an operator raises the drop limit,
# while a missed short list costs little: each left-out Member still gets a
# contact row from its own search row.
CONTACT_MISSING_MARGIN = 10
CONTACT_MISSING_MARGIN_PERCENT = 2


def member_contact_coverage(members, contactinfos):
    """How completely the Member contact list covered the Members searched.

    ``members`` and ``contactinfos`` are the shared loader's dictionaries,
    both keyed by Member identifier. Returns counts only, never identifiers:
    ``members`` searched, ``contact_infos`` the contact list returned, and
    ``missing``, the searched Members the contact list left out. A Member
    left out still gets a contact row built from its search row's contact
    fields, which is why the normalized corpus cannot show this.
    """
    return {
        "members": len(members),
        "contact_infos": len(contactinfos),
        "missing": len(members.keys() - contactinfos.keys()),
    }


def contact_missing_allowance(members):
    """How many more left-out Members than the baseline a full load may have."""
    return max(CONTACT_MISSING_MARGIN, members * CONTACT_MISSING_MARGIN_PERCENT // 100)


# The names of a ShortContactList's counts, in the order its ``coverage``
# tuple holds them; the process log reports them under these names.
CONTACT_COVERAGE_FIELDS = (
    "members",
    "contact_infos",
    "missing",
    "baseline_missing",
    "allowance",
)


class ShortContactList(ShiftedSourceScan):
    """A full load's contact list left out too many more Members (#387 M4).

    Retried like any shifted scan. ``coverage`` holds the counts the refusal
    was decided on, in ``CONTACT_COVERAGE_FIELDS`` order, so the operator can
    tell a cut-short list from a real ParishSoft change in ordinary output:
    counts only, never identifiers.
    """

    def __init__(self, message, *, coverage):
        """Keep the refusal's counts alongside the fixed message."""
        super().__init__(message)
        self.coverage = coverage


def check_member_contact_coverage(
    coverage, *, previous_missing, maximum_drop_percent=DEFAULT_MAXIMUM_DROP_PERCENT
):
    """Refuse a full load whose contact list looks cut short (#387 M4).

    ``members/contact/list`` reports no total or row ordinal to check its
    pages against, and ParishSoft legitimately leaves some Members out of it
    (inactive ones, mostly), so neither a total nor "every Member present"
    can be required. Instead the count left out is compared with the last
    promoted full refresh's (``previous_missing``): a rise past
    ``contact_missing_allowance`` is a retryable ``ShiftedSourceScan``.

    With no recorded baseline (``None``: the first full loads after this
    check was added, or setup's first load) the coverage is only recorded.
    An operator who raises the drop limit for one refresh to accept a known
    large change accepts this one too, and that refresh becomes the next
    baseline. The error (``ShortContactList``) carries counts only.
    """
    if previous_missing is None or maximum_drop_percent > DEFAULT_MAXIMUM_DROP_PERCENT:
        return
    missing = coverage["missing"]
    allowance = contact_missing_allowance(coverage["members"])
    if missing > previous_missing + allowance:
        raise ShortContactList(
            f"The Member contact list left out {missing} Members; "
            f"the last full refresh left out {previous_missing}.",
            coverage=(
                coverage["members"],
                coverage["contact_infos"],
                missing,
                previous_missing,
                allowance,
            ),
        )


def recorded_contact_missing(cursor):
    """The ``missing`` count a full snapshot's load recorded, or ``None``."""
    load = cursor.get("load") if type(cursor) is dict else None
    coverage = load.get(CONTACT_COVERAGE_KEY) if type(load) is dict else None
    missing = coverage.get("missing") if type(coverage) is dict else None
    return missing if type(missing) is int and missing >= 0 else None


def check_source_counts(
    counts,
    derived,
    *,
    previous_full_counts,
    previous_derived_counts,
    maximum_drop_percent,
):
    """Check every record and eligibility count, then refuse once with all of them.

    Before ADM-13 the check stopped at the first count that fell; now every
    count is compared first, so a refusal carries all of them (failing or
    not) for the refused attempt to record. The first failing count still
    names the refusal, as before. Returns the checks of an accepted load.
    """
    checks = count_checks(
        counts,
        previous_full_counts=previous_full_counts,
        maximum_drop_percent=maximum_drop_percent,
    )
    if _empty(counts):
        # As before ADM-13, an empty load is refused before the eligibility
        # baseline is examined. Loads pass derived_baseline()'s result,
        # which is always usable or None; the check is defensive, so a
        # malformed baseline can never turn an empty refusal into another
        # error.
        usable = previous_derived_counts is None or valid_derived_counts(
            previous_derived_counts
        )
        refuse_drops(
            checks
            + (
                derived_checks(
                    derived,
                    baseline_counts=previous_derived_counts,
                    maximum_drop_percent=maximum_drop_percent,
                )
                if usable
                else []
            ),
            empty=True,
        )
    checks += derived_checks(
        derived,
        baseline_counts=previous_derived_counts,
        maximum_drop_percent=maximum_drop_percent,
    )
    refuse_drops(checks)
    return checks


def load_full_source(
    client,
    *,
    window,
    as_of,
    previous_full_counts=None,
    previous_derived_counts=None,
    maximum_drop_percent=DEFAULT_MAXIMUM_DROP_PERCENT,
    previous_contact_missing=None,
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
    ``previous_contact_missing`` is the last promoted full refresh's count
    of Members the contact list left out (``check_member_contact_coverage``).
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
        # Checked on the raw collections, before normalization merges each
        # Member's search-row contact fields in and hides who was left out.
        coverage = member_contact_coverage(data.members, data.member_contactinfos)
        check_member_contact_coverage(
            coverage,
            previous_missing=previous_contact_missing,
            maximum_drop_percent=maximum_drop_percent,
        )
        # Catalogs are needed by initial campaign preparation even when no
        # giving window exists yet. Shared data is frozen; its collections are not.
        data.funds.update(load_funds(client, data.organization_id))
        if progress is not None:
            progress("funds", len(data.funds))
        corpus = normalize_core(data, as_of=as_of)
        giving = load_giving(client, corpus=corpus, window=window, as_of=as_of)
        corpus.update(pledge=giving.pledges, contribution=giving.contributions)
    except (
        InvalidSourcePayload,
        SourceOrganizationMismatch,
        ShiftedSourceScan,
        SourceLoadBudgetExceeded,
    ):
        # A shifted scan (including a dangling cross-collection reference)
        # and a load that ran out of time keep their types, so the worker
        # retries them (see failures).
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
    derived = derived_counts(corpus)
    check_source_counts(
        counts,
        derived,
        previous_full_counts=previous_full_counts,
        previous_derived_counts=previous_derived_counts,
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
            # The next full refresh's contact list baseline (#387 M4).
            CONTACT_COVERAGE_KEY: coverage,
        },
    )
