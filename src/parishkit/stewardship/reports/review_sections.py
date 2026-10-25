"""One review wizard page's section, from the Family hub's own code (#2123).

Review pipeline spec, "Wizard shared component" and "Wizard pages". The
wizard adds pages, not review logic: a page's section is the Family hub's
section for the same stream, read and decorated by the hub's own functions
(``hub_census``, ``hub_streams`` and ``hub_actions``, which read each
stream through its own worklist read under the reader's principal) and
drawn by the same ``components/review-*-section.html`` templates the hub's
panels include. Only two things are the wizard's own:

- **The page shows the run's matched items, live.** A subject's stored
  item ids (``ReviewRun.subjects``) are first followed to their live
  replacements (a census change or Ministry request a resubmission
  superseded, an Additional information item it replaced; a pledge is the
  Family's current one), then kept only while the stream's scoped read
  still lists them for this reader. The subject's other items that read
  lists are counted for the page's filtered-out line.
- **The subject is named only while it is listed.** ``Section.listed`` is
  whether any of the subject's items is still in the reader's scoped read.
  The page draws the subject's name, DUID and Timeline link only then, so
  a Ministry leader who lost a Ministry mid-run never sees the name of a
  Member stored in a run started earlier; nor does anyone see the name of
  a subject whose items all left the stream.

This slice draws the Family pages' sections (Pledges, Additional
information, the Census changes Family page and the Limitations Family
page); a Member page's section comes with the next slice, but whether its
subject is listed is read here already, for its heading.
"""

from dataclasses import dataclass, replace
from uuid import UUID

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.responses.limitation_review import answer_id

from .hub_actions import (
    census_subject,
    census_worklist,
    financial_tab,
    limitations_worklist,
    ministry_worklist,
    mutable,
    other_tab,
    shown,
)
from .hub_census import read_hub_census
from .hub_streams import read_hub_financial, read_hub_other
from .review_wizard import FAMILY

# How many replacements a stored id is followed through: each resubmission
# adds one, so a few cover any real Family; the bound only stops a cycle.
MAX_HOPS = 20
# A Limitations run's Cannot contribute financially item (its Pledges item,
# ``limitation_worklist.answer_rows``).
PLEDGE_ITEM = "pledge-"


@dataclass(frozen=True)
class Part:
    """One shared section template's input on a wizard page.

    ``kind`` names the template: "census" (``review-census-section.html``,
    ``subject`` a ``hub_census.Subject``), "pledge"
    (``review-pledge-section.html``, ``financial`` a ``HubFinancial``) or
    "answers" (``review-answers-section.html``, ``rows`` and
    ``actions_column``). ``prefix`` is what every id it draws carries.
    """

    kind: str
    prefix: str
    subject: object = None
    financial: object = None
    rows: tuple = ()
    actions_column: bool = False


@dataclass(frozen=True)
class Section:
    """A wizard page's section; see the module docstring.

    ``listed`` says the subject is still in the reader's scoped read of the
    stream; ``parts`` are the shared templates' inputs, in page order, and
    ``dialogs`` the stream worklists whose row dialogs the page draws.
    ``filtered_out`` counts the subject's other items in the stream, which
    the run's filters left out. ``census`` is the hub's census read, when
    one was made, which the page's heading reuses for a Member's name.
    """

    listed: bool = False
    parts: tuple = ()
    dialogs: tuple = ()
    filtered_out: int = 0
    census: object = None

    @property
    def count(self):
        """How many items the page shows, for its view's audit."""
        found = 0
        for part in self.parts:
            if part.kind == "census":
                found += len(part.subject.changed)
            elif part.kind == "answers":
                found += len(part.rows)
            else:
                found += 1
        return found


def _uuids(ids):
    """The ids that are UUIDs (record ids), as UUIDs; the rest are skipped."""
    found = []
    for value in ids:
        try:
            found.append(UUID(str(value)))
        except ValueError:
            continue
    return found


def follow(model, link, ids):
    """``ids`` with each replaced record followed to its latest replacement.

    ``link`` is the model's field naming the record that replaced it
    (``superseded_by`` or ``replacement``). One read per generation of
    replacements; an id that is not a record of ``model`` is kept as it is.
    Returns a set of id strings.
    """
    current = {str(value) for value in ids}
    for _hop in range(MAX_HOPS):
        replaced = dict(
            model.objects.filter(
                pk__in=_uuids(current), **{f"{link}__isnull": False}
            ).values_list("pk", f"{link}_id")
        )
        if not replaced:
            break
        current = (current - {str(pk) for pk in replaced}) | {
            str(pk) for pk in replaced.values()
        }
    return current


def read_section(stream, campaign, family, subject, principal, *, parish_name):
    """The section of one wizard page: its stream's items for its subject.

    ``stream`` is the run's ``ReviewStream`` key, ``family`` the
    ``FamilyCampaign`` and ``subject`` the run's stored subject (its
    ``family``, ``kind``, ``key`` and ``items``). The stream page's own
    access was checked by the caller; each read here is the stream's own,
    under ``principal``.
    """
    stored = {str(item) for item in subject.get("items", ())}
    if subject["kind"] != FAMILY:
        return _member(stream, campaign, family, subject, stored, principal)
    reads = {
        "census": _census_family,
        "pledges": _pledges,
        "information": _information,
        "limitations": _limitations_family,
    }
    if stream not in reads:
        return Section()
    return reads[stream](campaign, family, stored, principal, parish_name=parish_name)


def _census_family(campaign, family, stored, principal, *, parish_name):
    """The Census changes Family page: the household changes that matched."""
    from parishkit.stewardship.responses.models import ProposedChange

    census = read_hub_census(campaign.pk, family.pk, census=True)
    subject = census.family
    worklist = census_worklist(
        campaign.pk,
        family.pk,
        principal,
        prefix="review-family-census",
        writable=mutable(campaign.pk),
    )
    if subject is None or worklist is None:
        return Section(census=census)
    acted = census_subject(subject, worklist)
    keep = follow(ProposedChange, "superseded_by", stored)
    # The changes the stream's read lists for this reader: only those can
    # be shown, and those not matched are the filtered-out line's.
    listed = [row for row in acted.changed if row.item]
    matched = {row.item_id for row in listed if row.item_id in keep}
    if not matched:
        return Section(census=census)
    # The unchanged fields stay, for the Show unchanged disclosure.
    rows = tuple(row for row in acted.rows if not row.changed or row.item_id in matched)
    narrowed = replace(acted, rows=rows, actions=shown(worklist, rows))
    return Section(
        listed=True,
        parts=(Part("census", "review-family", subject=narrowed),),
        dialogs=(narrowed.actions,) if narrowed.acting else (),
        filtered_out=len(listed) - len(matched),
        census=census,
    )


def _pledge(campaign, family, principal, *, parish_name):
    """The Family's pledge as Pledges lists it for this reader, or None.

    One pledge per Family: a pledge a resubmission replaced is shown by the
    Family's current one. None when the campaign has no Financial
    stewardship, the reader may not see pledges, the answers cannot be read
    now, or Pledges does not list the Family's pledge for them: the page
    then names no pledge (nor, with nothing else listed, the Family), since
    Pledges' own listing could not confirm it.
    """
    modules = (campaign.active_configuration.values or {}).get("modules", ())
    if "financial" not in modules or not allows(principal, Capability.FINANCIAL_DETAIL):
        return None
    financial = financial_tab(
        campaign.pk,
        family.family_duid,
        read_hub_financial(campaign, family.family_duid, parish_name=parish_name),
        principal,
    )
    pledge = financial.pledge
    if pledge is None or pledge.addition is None or pledge.addition.item is None:
        return None
    return financial


def _pledge_part(financial):
    """A pledge's part and its dialogs, as the Financial tab draws them."""
    pledge = financial.pledge
    dialogs = (pledge.actions,) if pledge and pledge.actions.acting else ()
    return Part("pledge", "review-pledge", financial=financial), dialogs


def _pledges(campaign, family, stored, principal, *, parish_name):
    """The Pledges page: the Family's pledge, live."""
    financial = _pledge(campaign, family, principal, parish_name=parish_name)
    if financial is None or not stored:
        return Section()
    part, dialogs = _pledge_part(financial)
    return Section(listed=True, parts=(part,), dialogs=dialogs)


def _answers(campaign, family, principal, *, information, wanted):
    """The hub's Additional answers ``wanted`` keeps, with their actions.

    ``wanted(row, other)`` picks the rows of the page's stream from the
    ``HubOther`` read (before the row actions are read, so the other
    stream is not read for nothing). Returns the ``HubOther`` with its row
    actions (``hub_actions.other_tab``) and the rows its streams' scoped
    reads list for this reader.
    """
    other = read_hub_other(
        campaign.pk,
        family.pk,
        (),
        ministry=False,
        information=information,
        family_duid=family.family_duid,
    )
    if other.subject is None:
        return other, []
    rows = tuple(
        row for row in other.subject.rows if row.changed and wanted(row, other)
    )
    other = other_tab(
        campaign.pk,
        family.family_duid,
        replace(other, subject=replace(other.subject, rows=rows)),
        principal,
    )
    return other, [row for row in other.subject.rows if row.item]


def _answers_part(other, rows):
    """The answers part for ``rows`` and the dialogs of their worklists."""
    worklists = tuple(shown(worklist, rows) for worklist in other.actions)
    part = Part(
        "answers",
        "review-answers",
        rows=tuple(rows),
        # As the hub's ``Subject.actions_column``, over the rows shown.
        actions_column=any(row.item["icons"] for row in rows),
    )
    return part, tuple(worklist for worklist in worklists if worklist.acting)


def _information(campaign, family, stored, principal, *, parish_name):
    """The Additional information page: the Family's item, live."""
    from parishkit.stewardship.responses.models import AdditionalInformationItem

    keep = follow(AdditionalInformationItem, "replacement", stored)
    other, listed = _answers(
        campaign,
        family,
        principal,
        information=True,
        # The hub's answers hold the Family's current item only.
        wanted=lambda row, other: (
            other.item is not None and row.item_id == str(other.item)
        ),
    )
    matched = [row for row in listed if row.item_id in keep]
    if not matched:
        return Section()
    part, dialogs = _answers_part(other, matched)
    return Section(
        listed=True,
        parts=(part,),
        dialogs=dialogs,
        filtered_out=len(listed) - len(matched),
    )


def _limitations_family(campaign, family, stored, principal, *, parish_name):
    """The Limitations Family page: the Family's own answers that matched.

    Cannot attend Mass or prayer services is drawn by the answers section;
    Cannot contribute financially is the Family's pledge, drawn by the
    pledge section, only for a reader who may see pledges (it is decided on
    Pledges).
    """
    own = answer_id(family.family_duid, FAMILY, "")
    other, listed = _answers(
        campaign,
        family,
        principal,
        information=False,
        wanted=lambda row, other: row.item_id == own,
    )
    parts, dialogs = [], ()
    matched = [row for row in listed if row.item_id in stored]
    filtered = len(listed) - len(matched)
    if matched:
        part, dialogs = _answers_part(other, matched)
        parts.append(part)
    financial = _pledge(campaign, family, principal, parish_name=parish_name)
    if financial is not None and financial.cannot_give:
        if any(item.startswith(PLEDGE_ITEM) for item in stored):
            part, more = _pledge_part(financial)
            parts.append(part)
            dialogs += more
        else:
            filtered += 1
    return Section(
        listed=bool(parts),
        parts=tuple(parts),
        dialogs=dialogs,
        filtered_out=filtered,
    )


def _member(stream, campaign, family, subject, stored, principal):
    """A Member page: whether its Member is still listed for this reader.

    The section itself comes with the wizard's next slice; the heading needs
    to know now whether any of the Member's stored items is still in the
    stream's scoped read (for a Ministry leader, only their Ministries').
    """
    from parishkit.stewardship.responses.models import ProposedChange
    from parishkit.stewardship.workflows.models import MinistryRequest

    census = None
    visible = set()
    if stream == "census":
        census = read_hub_census(campaign.pk, family.pk, census=True)
        stored = follow(ProposedChange, "superseded_by", stored)
        worklist = census_worklist(
            campaign.pk, family.pk, principal, prefix="review-member", writable=False
        )
        visible = {str(row["id"]) for row in worklist.rows} if worklist else set()
    elif stream == "ministry":
        stored = follow(MinistryRequest, "superseded_by", stored)
        worklist = ministry_worklist(
            campaign.pk, principal, stored, prefix="review-member", writable=False
        )
        visible = {str(row["id"]) for row in worklist.rows}
    elif stream == "limitations":
        worklist = limitations_worklist(
            campaign.pk, family.family_duid, principal, writable=False
        )
        visible = {str(row["id"]) for row in worklist.rows}
    return Section(listed=bool(stored & visible), census=census)
