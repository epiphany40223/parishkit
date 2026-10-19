"""``report roster``: the roster changes to enter, as counts (ADM-11).

The list (``reports.ministry_roster_views``) is the resolved Ministry joins
and leaves staff still have to enter in ParishSoft by hand (#528), on the
page Ministry requests' To enter by hand Stage since the Roster changes to
enter page was folded into it (R39 finding 2). The command reads the list
through its own pieces: its admission
(``ministry_roster_views._principal``: Administrators and Staff, never a
Ministry leader), the shared report read (``admin_report_reads._report``:
the current campaign, ``admit_report_read`` and the campaign read guard)
and its row selection (``roster_rows``), with the page's Show
(``--show todo|all``) and Ministry (``--ministry DUID``) choices.

Each row names a Member and a Family, so the command prints counts only:
the list's size, its joins and leaves, how many are already marked
Entered in ParishSoft and how many a later response replaced, overall and
per Ministry (its DUID and name, as ``report ministry`` prints them). The
rows themselves stay on the page and its downloads (a later ``export
roster``). It records the page's ``roster_changes_viewed`` with the page's
context members; as the other report reads, the event's ``count`` is the
rows the command displayed, which is none.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import ReadModel, _recheck


@dataclass(frozen=True)
class RosterReport(ReadModel):
    """The Roster changes to enter list of the current campaign, as counts.

    ``show`` is the page's Show choice (``todo``: not yet entered, the
    default; ``all``: entered ones too) and ``ministry`` its Ministry filter
    (a DUID, or None). ``matching`` is the list's row count; ``joins`` and
    ``leaves`` split it by request; ``entered`` counts rows already marked
    Entered in ParishSoft (none under ``todo``) and ``replaced`` rows a
    later response from the Family replaced. ``ministries`` repeats those
    counts per Ministry, by DUID (``ministry``) with its ``name``.
    """

    campaign_id: UUID
    show: str
    ministry: int | None
    matching: int
    joins: int
    leaves: int
    entered: int
    replaced: int
    ministries: list


def _counts(rows):
    """One group's join, leave, entered and replaced counts."""
    return {
        "matching": len(rows),
        "joins": sum(row["action"] == "join" for row in rows),
        "leaves": sum(row["action"] != "join" for row in rows),
        "entered": sum(bool(row["roster_entered"]) for row in rows),
        # The page's "Replaced by a later response": the Family answered
        # again after this change was resolved.
        "replaced": sum(row.get("latest") is False for row in rows),
    }


def roster_model(campaign_id, show, ministry, rows):
    """``roster_rows``'s rows as ``report roster`` prints them: counts only.

    No Member's or Family's name or DUID, and no resolver or tick author,
    reaches the document; Ministries are listed by DUID, in DUID order.
    """
    groups = {}
    for row in rows:
        groups.setdefault(row["ministry_duid"], []).append(row)
    return RosterReport(
        campaign_id=campaign_id,
        show=show,
        ministry=ministry,
        **_counts(rows),
        ministries=[
            {"ministry": duid, "name": group[0]["ministry_name"], **_counts(group)}
            for duid, group in sorted(groups.items())
        ],
    )


def read_roster(caller, service, *, show, ministry):
    """``report roster``: the Roster changes to enter list, counted.

    ``show`` is ``todo`` or ``all`` (the command line's choices); a
    ``ministry`` outside the page's DUID range (1 through 2**31 - 1) is the
    page's refusal (``invalid``), reported after the session recheck so an
    ended session is exit 5. Admission, the campaign check and the guarded
    read are the shared report read's (``_report``), so no current campaign
    is ``not_available`` and a Ministry leader or a reader without the page
    is ``denied``. Records ``roster_changes_viewed`` (succeeded or failed,
    with ``count`` 0) as the page records its view.
    """
    from .admin_report_reads import _report
    from .reports.ministry_followup import valid_ministry
    from .reports.ministry_roster_views import _audit, _principal, roster_rows

    filter_text = "" if ministry is None else str(ministry)

    def admit(actor, campaign_id):
        """The page's choice check, before anything is recorded."""
        if filter_text and not valid_ministry(filter_text):
            _recheck(caller, service.store, actor)
            raise ValueError("A Ministry DUID is 1 through 2147483647.")

    def read(guard, actor, campaign_id):
        """The page's selection, reduced to its counts."""
        rows, _ = roster_rows(campaign_id, actor, show, filter_text)
        return roster_model(campaign_id, show, ministry, rows), None

    def audit(actor, campaign_id, outcome, values):
        """The page's view event: the command displayed no row."""
        from .audit.schemas import Action

        _audit(Action.ROSTER_CHANGES_VIEWED, actor, campaign_id, outcome, 0)

    return _report(
        caller, service, principal=_principal, read=read, audit=audit, admit=admit
    )
