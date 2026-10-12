"""``report census``: the Census changes worklist as counts (ADM-11).

The page (``admin:census_changes``, ``reports.census_change_views``) lists
the census changes Families reported, which Admin and Staff carry into
ParishSoft (#528). The command reads the same list through the page's own
pieces, so the two cannot drift: its admission
(``census_change_views._principal``: the census capability, never a
Ministry leader), the shared report read (``admin_report_reads._report``:
the current campaign, ``admit_report_read`` and the campaign read guard),
its filters (``CensusQuery``, without the search) and its read
(``census_changes``), which words and filters the rows as the page does.

Each row names a Family and often a Member, and carries their contact
details, so the command prints counts only: how many rows the filters
keep, split by how they reach ParishSoft and by kind of change, and the
page's summary of every status. The rows themselves stay on the page and
its downloads (a later ``export census``). It records the page's
``census_changes_viewed``; as the other report reads, the event's
``count`` is the rows the command displayed, which is none.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import ReadModel, _recheck


@dataclass(frozen=True)
class CensusReport(ReadModel):
    """The Census changes list of the current campaign, as counts.

    ``filters`` are the page's choices as it re-submits them (``status``,
    ``history``, ``route``, ``kind``, ``start``, ``end`` and ``zone``),
    without the search. ``matching`` is the rows the filters keep;
    ``routes`` splits them into ``automatic`` and ``by_hand`` and ``kinds``
    by kind of change. ``statuses`` is the page's summary: every row of the
    campaign by status key, whatever the other filters, with the history
    statuses (``superseded``, ``cancelled``) only when ``history`` is on.
    """

    campaign_id: UUID
    filters: dict
    matching: int
    routes: dict
    kinds: dict
    statuses: dict


def census_model(campaign_id, query, result):
    """``census_changes``'s result as ``report census`` prints it: counts only.

    No Family's or Member's name, DUID or value, and no resolver or note,
    reaches the document. The page's summary is a list of (wording, count)
    pairs with the zero counts left out; it is printed by status key, every
    status the summary covers, zeros included.
    """
    from .reports.census_changes import HISTORY, KINDS, STATUSES

    rows = result["rows"]
    summary = dict(result["summary"])
    history = bool(query.history)
    return CensusReport(
        campaign_id=campaign_id,
        filters={
            key: value for key, value in query.form_values().items() if key != "search"
        },
        matching=len(rows),
        routes={
            "automatic": sum(bool(row["automatic"]) for row in rows),
            "by_hand": sum(not row["automatic"] for row in rows),
        },
        # The page's Kind filter choices, less its "any" (Every kind).
        kinds={kind: sum(row["kind"] == kind for row in rows) for kind in KINDS[1:]},
        statuses={
            key: summary.get(wording, 0)
            for key, wording in STATUSES.items()
            if history or key not in HISTORY
        },
    )


def read_census(caller, service, filters):
    """``report census``: the Census changes list, counted.

    ``filters`` are the page's non-identifying filters (``status``,
    ``history``, ``route``, ``kind``, ``start``, ``end`` and the ``zone``
    the days fall in, required with them; None when not given), parsed by
    the page's ``CensusQuery`` once the reader is admitted, so a refusal
    (``invalid``) is reported after the session recheck and an ended
    session is exit 5. Admission, the campaign check and the guarded read
    are the shared report read's (``_report``), so no current campaign is
    ``not_available`` and a reader without the census capability (a
    Ministry leader) is ``denied``. Records ``census_changes_viewed``
    (succeeded or failed, with ``count`` 0) as the page records its view.
    """
    from .admin_report_reads import _report
    from .reports.census_change_views import _audit, _principal
    from .reports.census_changes import CensusQuery, census_changes

    given = {key: value for key, value in filters.items() if value is not None}
    holder = {}

    def admit(actor, campaign_id):
        """The page's filter check, before anything is recorded."""
        try:
            holder["query"] = CensusQuery.parse(given)
        except ValueError:
            _recheck(caller, service.store, actor)
            raise

    def read(guard, actor, campaign_id):
        """The page's read, reduced to its counts."""
        result = census_changes(campaign_id, holder["query"], actor)
        return census_model(campaign_id, holder["query"], result), None

    def audit(actor, campaign_id, outcome, values):
        """The page's view event: the command displayed no row."""
        from .audit.schemas import Action

        _audit(Action.CENSUS_CHANGES_VIEWED, actor, campaign_id, outcome, 0)

    return _report(
        caller, service, principal=_principal, read=read, audit=audit, admit=admit
    )
