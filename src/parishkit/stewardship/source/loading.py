"""One complete shared-client load, ready for fenced staging but not promotion."""

from dataclasses import dataclass
from datetime import date

from parishkit.config import ConfigError
from parishkit.parishsoft import load_families_and_members, load_funds
from parishkit.parishsoft_source import CoherentParishSoftClient

from .canonical import InvalidSourcePayload
from .corpus import KINDS, normalize_core
from .giving import load_giving
from .windows import RefreshWindow

TREND_COLLECTIONS = ("family", "member", "ministry", "roster", "fund")


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
    if type(maximum_drop_percent) is not int or not 0 <= maximum_drop_percent <= 90:
        raise ValueError("The source loss threshold must be between 0 and 90 percent.")
    for values in (counts, previous_full_counts):
        if values is not None and (
            type(values) is not dict
            or set(values) != set(KINDS)
            or any(type(value) is not int or value < 0 for value in values.values())
        ):
            raise InvalidSourcePayload("Source count evidence is incomplete.")
    if counts is None or counts["family"] == 0 or counts["member"] == 0:
        raise InvalidSourcePayload("Source Family/Member corpus is unexpectedly empty.")
    if previous_full_counts is None:
        return
    for kind in TREND_COLLECTIONS:
        before, after = previous_full_counts[kind], counts[kind]
        if before and (
            not after or (before - after) * 100 > before * maximum_drop_percent
        ):
            raise InvalidSourcePayload(
                "Source corpus exceeds the permitted count loss."
            )


def load_full_source(
    client, *, window, as_of, previous_full_counts=None, maximum_drop_percent=25
):
    """Fetch, normalize and validate without SQL or a mutable source pointer.

    The worker begins its manifest before this call, so its durable next delta
    watermark describes the start of network observation, never the end. Its
    bounded Session owns admission/fencing before each request. Callers stage
    this result only after validation succeeds and must still check admission
    and the exact campaign window again at atomic promotion.
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
        )
        # Catalogs are needed by initial campaign preparation even when no
        # giving window exists yet. Shared data is frozen; its collections are not.
        data.funds.update(load_funds(client, data.organization_id))
        corpus = normalize_core(data, as_of=as_of)
        giving = load_giving(client, corpus=corpus, window=window, as_of=as_of)
        corpus.update(pledge=giving.pledges, contribution=giving.contributions)
    except InvalidSourcePayload:
        raise
    except (ConfigError, KeyError, TypeError, ValueError, OverflowError):
        # Ordinary shared tools retain their diagnostics. The app boundary
        # cannot persist raw upstream values embedded in a parser exception.
        raise InvalidSourcePayload(
            "The source load contains invalid provider data."
        ) from None
    counts = {kind: len(rows) for kind, rows in corpus.items()}
    validate_count_trend(
        counts,
        previous_full_counts=previous_full_counts,
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
        },
    )
