"""Conservative Family delta replacement, preserving coherent full-only inputs."""

from datetime import date

from parishkit.config import ConfigError
from parishkit.parishsoft import (
    ParishSoftData,
    load_family_groups,
    ministry_membership_is_current,
)
from parishkit.parishsoft_changes import ChangeFeedIncomplete, load_family_changes
from parishkit.parishsoft_households import load_family_slice
from parishkit.parishsoft_source import CoherentParishSoftClient

from .canonical import InvalidSourcePayload
from .corpus import KINDS, normalize_core
from .cursors import delta_dates
from .loading import SourceLoad, validate_count_trend
from .windows import RefreshWindow


def _slice_data(organization_id, households, groups):
    """Use the same core normalizer as full loads without inventing missing DTOs."""
    return ParishSoftData(
        organization_id=organization_id,
        families={key: household.family for key, household in households.items()},
        members={
            key: row
            for household in households.values()
            for key, row in household.members.items()
        },
        member_contactinfos={
            key: row
            for household in households.values()
            for key, row in household.contacts.items()
        },
        family_groups=groups,
        family_workgroups={},
        family_workgroup_memberships={},
        member_workgroups={},
        member_workgroup_memberships={},
        ministry_types={},
        ministry_type_memberships={},
        funds={},
        pledges={},
        contributions={},
    )


def _composition(base, households):
    """Moves/removals can affect other Families and require a full refresh."""
    owners = {key: member["family_key"] for key, member in base["member"].items()}
    previous = {}
    for key, owner in owners.items():
        previous.setdefault(owner, set()).add(key)
    observed = set()
    for identifier, household in households.items():
        family_key = str(identifier)
        before = previous.get(family_key, set())
        after = {str(key) for key in household.members}
        if observed & after:
            raise ChangeFeedIncomplete("Source Member appears in multiple households.")
        observed.update(after)
        if family_key in base["family"] and before != after:
            raise ChangeFeedIncomplete(
                "Changed household composition requires full refresh."
            )
        if any(key in owners and owners[key] != family_key for key in after):
            raise ChangeFeedIncomplete("Moved source Members require full refresh.")


def _replace(base, updated, households, as_of):
    """Only exact affected Family/Member/contact/address sets are replaced."""
    result = {kind: dict(rows) for kind, rows in base.items()}
    family_ids = {str(key) for key in households}
    member_ids = {
        str(key) for household in households.values() for key in household.members
    }
    for kind in ("contact", "address"):
        result[kind] = {
            key: row
            for key, row in result[kind].items()
            if not (
                (row["owner_kind"] == "family" and row["owner_key"] in family_ids)
                or (row["owner_kind"] == "member" and row["owner_key"] in member_ids)
            )
        }
    for kind in ("family", "member", "contact", "address"):
        result[kind].update(updated[kind])
    # This is a new evaluation of retained dated facts, not a partial roster
    # reload. All actual Ministry/fund/giving source rows remain from full truth.
    result["roster"] = {
        key: {**row, "current": ministry_membership_is_current(row, today=as_of)}
        for key, row in result["roster"].items()
    }
    return result


def load_delta_source(
    client,
    *,
    base,
    base_cursor,
    window,
    started_at,
    as_of,
    previous_full_counts,
    maximum_drop_percent=25,
):
    """Return a whole replacement corpus or require a full refresh; never save SQL.

    The Family feed is only an indication stream. Missing/discontinuous coverage,
    changed composition or global group definitions cannot authorize a partial
    replacement. Network/transient provider failures retain normal retry policy;
    an ambiguous scope raises ChangeFeedIncomplete for the durable full producer.
    """
    if (
        not isinstance(client, CoherentParishSoftClient)
        or not isinstance(window, RefreshWindow)
        or type(as_of) is not date
    ):
        raise InvalidSourcePayload("A delta requires its coherent client/window/date.")
    start, end = delta_dates(
        base_cursor, started_at=started_at, window_digest=window.digest
    )
    if (
        type(base) is not dict
        or set(base) != set(KINDS)
        or any(type(rows) is not dict for rows in base.values())
    ):
        raise ChangeFeedIncomplete("Delta requires a reconstructable complete base.")
    try:
        retained = base_cursor["load"]
        giving_day = date.fromisoformat(retained["giving_as_of_date"])
        if (
            giving_day.isoformat() != retained["giving_as_of_date"]
            or giving_day > as_of
        ):
            raise ValueError("Invalid giving observation day.")
        for key in ("anonymous_pledges", "anonymous_contributions"):
            if type(retained[key]) is not int or retained[key] < 0:
                raise ValueError("Invalid source giving count.")
    except (KeyError, TypeError, ValueError):
        raise ChangeFeedIncomplete(
            "Delta giving coverage requires a full refresh."
        ) from None
    indications = load_family_changes(
        client,
        organization_id=client.expected_organization_id,
        start_date=start,
        end_date=end,
    )
    try:
        households = {
            identifier: load_family_slice(client, family_id=identifier)
            for identifier in indications.family_ids
        }
        _composition(base, households)
        groups = load_family_groups(client) if households else {}
        if households:
            for family in base["family"].values():
                if family.get("family_group") != groups.get(family.get("famGroupID")):
                    raise ChangeFeedIncomplete(
                        "Changed Family group definitions require full refresh."
                    )
        updated = normalize_core(
            _slice_data(client.expected_organization_id, households, groups),
            as_of=as_of,
        )
        corpus = _replace(base, updated, households, as_of)
        counts = {kind: len(rows) for kind, rows in corpus.items()}
        validate_count_trend(
            counts,
            previous_full_counts=previous_full_counts,
            maximum_drop_percent=maximum_drop_percent,
        )
    except (ConfigError, KeyError, TypeError, ValueError, OverflowError):
        raise ChangeFeedIncomplete(
            "The Family delta cannot be safely scoped."
        ) from None
    return SourceLoad(
        corpus,
        counts,
        {
            "schema": "source-load-v1",
            "window_digest": window.digest,
            "maximum_drop_percent": maximum_drop_percent,
            "indications": indications.indication_count,
            "families_reloaded": len(households),
            "requests": client.request_count,
            "response_bytes": client.response_bytes,
            "as_of_date": as_of.isoformat(),
            "giving_as_of_date": retained["giving_as_of_date"],
            "anonymous_pledges": retained["anonymous_pledges"],
            "anonymous_contributions": retained["anonymous_contributions"],
        },
    )
