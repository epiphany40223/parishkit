"""The Family hub's other streams: Ministry, Financial and the last tab (#1678).

Slices 3 and 4 of the Family information hub (admin-portal spec, "Member
tabs' Ministry joins and leaves", "Hub Financial tab" and "Hub Cannot
participate and Additional information tab"). Each tab shows the Family's
answers for its stream beside their Baseline, with each item's
review-pipeline stage from the stream's own mapping in
``parishkit.stewardship.pipeline``, so the hub holds no second comparison
that could drift from the review pages.

Only the chosen tab reads its stream (the view decides), so each read here
costs nothing on the other tabs. Every ``read_*`` function runs its
statements under the page's read guard; every ``*_rows`` / ``*_tab``
function is pure, so a unit test drives it from plain values.
"""

import json
import logging
from dataclasses import dataclass, replace

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.leader_import import ministry_names
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import (
    Stage,
    cannot_participate_placement,
    financial_placement,
    information_placement,
    ministry_placement,
)
from parishkit.stewardship.source.source_status import (
    NAME_NOT_RECORDED,
    source_status_label,
)

from .financial import FinancialQuery, giving_proof, shape_result
from .financial_documents import period_years
from .hub_census import FieldRow, Subject
from .ministries import outcome_label

# --- Member tabs: Ministry joins and leaves --------------------------------

# Each Ministry's newest request for one Member among the Family's live
# responses (``responses.ministry_requests.request_index``'s rule, restated
# because that function takes the response work lock), with its latest
# Entered in ParishSoft tick (the tick ``workflows.roster.roster_marks``
# reads: the entry with the highest sequence), and its Member's standing in
# ParishSoft as Ministry requests reads it (#1217,
# stewardship_ministry_request_member_status_v1: NULL for an added Member).
# Takes %(family)s, %(campaign)s, %(kind)s and %(key)s.
MINISTRY_REQUESTS = """
SELECT DISTINCT ON (r.ministry_duid) r.ministry_duid, r.action, r.state,
    r.outcome, r.resolution_source_id IS NOT NULL,
    coalesce((SELECT e.entered FROM stewardship_ministry_roster_entry e
        WHERE e.request_id=r.id ORDER BY e.sequence DESC LIMIT 1), false),
    stewardship_ministry_request_member_status_v1(r.id)
FROM stewardship_ministry_request r
JOIN stewardship_submission s ON s.id=r.submission_id
WHERE s.family_id=%(family)s AND s.campaign_id=%(campaign)s AND s.mode='live'
    AND r.entity_kind=%(kind)s AND r.entity_key=%(key)s
ORDER BY r.ministry_duid, s.family_version DESC
"""
REQUEST_COLUMNS = (
    "ministry",
    "action",
    "state",
    "outcome",
    "resolved",
    "entered",
    "member_status",
)
# Where Ministry requests lists a request whose Member ParishSoft no longer
# lists as current in the Family (#1217): only under that Status choice.
NOT_CURRENT = _(
    "%(status)s: Ministry requests lists this request only with Status set "
    "to Members no longer in ParishSoft"
)
# The roster the Family answered from, implied by the request: a join is
# asked by a Member not in the Ministry, a leave by one in it.
BASELINES = {"join": _("Not in this Ministry"), "leave": _("In this Ministry")}
ANSWERS = {"join": _("Join"), "leave": _("Leave")}


def read_member_ministry(campaign_id, family_id, entity):
    """The chosen Member tab's Ministry rows: one statement, three with any.

    ``entity`` is the Member's ``(entity_kind, entity_key)``, as its
    requests name it. The Ministry names come from the current catalog,
    read only when there is a request to name.
    """
    kind, key = entity
    with connection.cursor() as cursor:
        cursor.execute(
            MINISTRY_REQUESTS,
            {"family": family_id, "campaign": campaign_id, "kind": kind, "key": key},
        )
        rows = [dict(zip(REQUEST_COLUMNS, values, strict=True)) for values in cursor]
    names = ministry_names([row["ministry"] for row in rows]) if rows else {}
    return ministry_rows(rows, names)


def ministry_rows(rows, names):
    """One ``FieldRow`` per request in force, by Ministry name.

    ``rows`` are ``MINISTRY_REQUESTS`` rows and ``names`` each Ministry
    DUID's display name. A superseded or cancelled request is history, not
    shown; a decided one carries its outcome's words as Ministry requests
    shows them. A request whose Member ParishSoft no longer lists as current
    (#1217) keeps its stage but says where Ministry requests lists it, since
    its default list leaves it out.
    """
    shown = []
    for row in rows:
        placement = _placed(
            ministry_placement,
            "hub_ministry_stage",
            state=row["state"],
            outcome=row["outcome"],
            source_resolved=row["resolved"],
            roster_entered=row["entered"],
        )
        if placement is not None and placement.stage is Stage.HISTORY:
            continue
        shown.append(
            FieldRow(
                label=names.get(row["ministry"], ""),
                answer=str(ANSWERS.get(row["action"], row["action"])),
                changed=True,
                baseline=str(BASELINES.get(row["action"], "")),
                placement=placement,
                note=member_note(row) or outcome_label(row["outcome"], row["action"]),
            )
        )
    return tuple(sorted(shown, key=lambda row: row.label.casefold()))


def member_note(row):
    """The note for a request whose Member is not current in ParishSoft, or ""."""
    status = source_status_label(row.get("member_status"))
    return str(NOT_CURRENT % {"status": status}) if status else ""


def _placed(mapping, shaping, **state):
    """An item's ``Placement`` from its stream's mapping, or None.

    A mapping refuses a state the SQL guards never store. One such row must
    not take the whole Family page down, so it is logged and shown with no
    stage, as on the census tabs.
    """
    try:
        return mapping(**state)
    except ValueError as error:
        emit_failure(
            error,
            event=Event.REPORT_SHAPING_FAILED,
            level=logging.WARNING,
            shaping=shaping,
        )
        return None


# --- Financial tab ----------------------------------------------------------


@dataclass(frozen=True)
class HubFinancial:
    """The Financial tab: the Family's current pledge, if it has one.

    ``pledge`` is the pledge item (a ``Subject`` whose ``addition`` row
    carries the item's one stage and whose rows are its fields), None when
    the Family's current response has no financial answers. ``available``
    is False when the report's inputs cannot be read now. ``contributions``
    is the ParishSoft Contributed sum (a ``MoneyAmount``), ``years`` the
    comparison period's years and ``through`` the giving data's last day.
    """

    pledge: Subject | None = None
    available: bool = True
    contributions: object = None
    years: str = ""
    through: object = None


def read_hub_financial(campaign, family_duid, *, parish_name):
    """The Financial tab, read as Financial stewardship detail reads it.

    The completeness proof (two statements), then the report's own
    selection, unpaged and narrowed by the Family's DUID (its search also
    matches longer DUIDs and names, so only the Family's own row is kept),
    then the configurations the answers were given under, which word the
    share methods.
    """
    proof = giving_proof(campaign)
    filters = replace(FinancialQuery(), search=str(family_duid)).form_values()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_financial_report_v1(%s,%s::jsonb,NULL,NULL)::text",
            [campaign.pk, json.dumps({"filters": filters, "proof": proof})],
        )
        value = cursor.fetchone()
    result = json.loads(value[0]) if value and value[0] else {"unavailable": True}
    if result.get("disabled") or result.get("unavailable"):
        return HubFinancial(available=False)
    result["rows"] = [
        row for row in result["rows"] if row["family_duid"] == family_duid
    ]
    if not result["rows"]:
        # Nothing to word: skip the configurations' read.
        return HubFinancial()
    result = shape_result(
        result,
        campaign_id=campaign.pk,
        parish_name=parish_name,
        configuration=campaign.active_configuration.values,
    )
    return financial_tab(result["rows"][0], result["metadata"])


def financial_tab(row, metadata):
    """The pledge item from one shaped Financial stewardship row.

    ``row`` is a ``shape_result`` row and ``metadata`` its result's. The
    pledge is one item with one stage (``financial_placement``); only the
    annual amount has a ParishSoft Baseline.
    """
    years = period_years(metadata["comparison_start"], metadata["comparison_end"])
    if row["cannot_give"]:
        amount = str(_("Cannot contribute financially"))
    else:
        amount = row["annual"].display
    if row["installment"].available:
        frequency = str(
            _("%(frequency)s (%(installment)s each)")
            % {
                "frequency": row["frequency_label"],
                "installment": row["installment"].display,
            }
        )
    else:
        frequency = str(row["frequency_label"])
    shares = "; ".join(
        f"{share['label']}: {share['text']}" if share["text"] else share["label"]
        for share in row["shares"]
    ) or str(_("None chosen"))
    if years:
        pledge_label = _("Annual pledge (ParishSoft %(years)s)") % {"years": years}
    else:
        pledge_label = _("Annual pledge")
    rows = (
        FieldRow(
            str(pledge_label), amount, True, baseline=row["source_pledge"].display
        ),
        FieldRow(str(_("Frequency")), frequency, True, baseline_read=False),
        FieldRow(str(_("Share methods")), shares, True, baseline_read=False),
    )
    stage = FieldRow(str(_("Pledge")), "", True, placement=financial_placement())
    return HubFinancial(
        Subject("financial", str(_("Financial")), rows, addition=stage),
        contributions=row["source_contributions"],
        years=years,
        through=metadata["giving_through"],
    )


# --- Cannot participate and Additional information tab ----------------------

# The Family's effective live response (the one the Limitations and
# Additional information pages read): its cannot-attend and per-Member
# cannot-participate answers, and the Family's current Additional
# information item (the newest current, actionable one of its live
# responses). Takes %(family)s and %(campaign)s.
OTHER = """
SELECT s.answers->'cannot_attend' = 'true'::jsonb,
    coalesce(s.answers->'service', '{}'::jsonb)::text,
    a.id, a.text, a.disposition, a.follow_up_needed, a.followed_up_at IS NOT NULL
FROM stewardship_family_campaign i
JOIN stewardship_submission s ON s.id=i.effective_submission_id
    AND s.mode='live' AND s.campaign_id=i.campaign_id
LEFT JOIN LATERAL (
    SELECT a.id, a.text, a.disposition, a.follow_up_needed, a.followed_up_at
    FROM stewardship_additional_information a
    JOIN stewardship_submission t ON t.id=a.submission_id
    WHERE t.family_id=i.id AND t.campaign_id=i.campaign_id AND t.mode='live'
        AND a.disposition='current_actionable'
    ORDER BY t.family_version DESC
    LIMIT 1
) a ON true
WHERE i.id=%(family)s AND i.campaign_id=%(campaign)s
"""
OTHER_COLUMNS = (
    "cannot_attend",
    "service",
    "item",
    "text",
    "disposition",
    "follow_up_needed",
    "followed_up",
)


@dataclass(frozen=True)
class HubOther:
    """The last tab: its fields (a ``Subject``) and the current item's id.

    ``subject`` is None before the Family's first live response. ``item``
    is the current Additional information item's id, None without one.
    ``ministry`` says the campaign has Ministry stewardship, whose
    Limitations page the panel links to, and ``information`` that it has
    Additional information, whose page and words the panel shows only then.
    """

    subject: Subject | None = None
    item: object = None
    ministry: bool = False
    information: bool = False


def read_hub_other(campaign_id, family_id, members, *, ministry, information):
    """The last tab's answers: one statement.

    ``members`` are the hub's Member subjects (their labels and entities,
    from the census read the page already made); ``ministry`` and
    ``information`` say whether those streams are in the campaign.
    """
    with connection.cursor() as cursor:
        cursor.execute(OTHER, {"family": family_id, "campaign": campaign_id})
        found = cursor.fetchone()
    if found is None:
        return HubOther(ministry=ministry, information=information)
    row = dict(zip(OTHER_COLUMNS, found, strict=True))
    row["service"] = json.loads(row["service"])
    return other_tab(row, members, ministry=ministry, information=information)


def other_tab(row, members, *, ministry, information):
    """The last tab's rows from one ``OTHER`` row, in form order.

    A Yes to a cannot answer is an item (``cannot_participate_placement``);
    a No is unchanged. Each Member's answer is labelled as its tab is, in
    tab order; a Member the response answered for without a tab is named
    as an unnamed Member is. The current Additional information item is
    staged by ``information_placement``; without one, Additional
    information is unchanged ("None given").
    """
    rows = [
        cannot_row(_("Cannot attend Mass or prayer services"), row["cannot_attend"])
    ]
    if ministry:
        service = row["service"] or {}
        labels = {subject.entity: subject.label for subject in members}
        answered = [
            (("member" if group == "members" else "proposed_member", key), entry)
            for group in ("members", "proposed_members")
            for key, entry in (service.get(group) or {}).items()
        ]
        # Tab order first, then any Member without a tab.
        order = {subject.entity: place for place, subject in enumerate(members)}
        answered.sort(key=lambda item: (order.get(item[0], len(order)), item[0]))
        for entity, entry in answered:
            name = labels.get(entity) or str(NAME_NOT_RECORDED)
            rows.append(
                cannot_row(
                    _("Cannot participate in ministries: %(name)s") % {"name": name},
                    (entry or {}).get("cannot_serve") is True,
                )
            )
    if information:
        label = str(_("Additional information"))
        if row["item"] is None:
            rows.append(FieldRow(label, str(_("None given"))))
        else:
            placement = _placed(
                information_placement,
                "hub_information_stage",
                disposition=row["disposition"],
                follow_up_needed=row["follow_up_needed"],
                followed_up=row["followed_up"],
            )
            rows.append(
                FieldRow(
                    label, row["text"], True, baseline_read=False, placement=placement
                )
            )
    subject = Subject(
        "other", str(_("Cannot participate and Additional information")), tuple(rows)
    )
    return HubOther(subject, row["item"], ministry, information)


def cannot_row(label, yes):
    """One cannot answer: a Yes is a To review item, a No is unchanged."""
    if yes:
        return FieldRow(
            str(label),
            str(_("Yes")),
            True,
            baseline_read=False,
            placement=cannot_participate_placement(),
        )
    return FieldRow(str(label), str(_("No")))
