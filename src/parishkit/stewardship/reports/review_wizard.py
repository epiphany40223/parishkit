"""The review wizard's pure rules (#2123; review pipeline spec, "Review wizard").

The wizard shows one subject (a Family or a Member) per page, for one
stream, with Previous and Next. Its views (``review_wizard_views``) read
the stream's items through ``review_streams`` and call this module, which
holds no database access, so its rules are unit-tested from plain values:

- ``sequence`` turns the matching items into the run's subjects, in the
  run's sort: Name A–Z (the tables' shared default order,
  ``web.tables.name_order``), Oldest or Newest submitted first. In the
  grouped streams (Census changes, Limitations) the sort orders Families,
  and each Family's pages are adjacent: its Family page first, when the
  Family itself has a matching item, then one page per Member with a
  matching item, in the Family hub's Member tab order.
- ``progress`` words a page's place ("Family 12 of 87, Member 2 of 3").
- ``waiting`` is Basic's stage set: every stage at which a person still has
  something to do (a Ministry leader: To review).
"""

from dataclasses import dataclass, field
from datetime import datetime

from django.utils.translation import gettext as _

from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.web.tables import name_order

from .review_run_models import MAX_SUBJECTS

# Basic's Stage, and the Advanced Stage filter's first choice.
WAITING = "waiting"
# "Waiting" (review pipeline spec, "Wizard filters and sort"): the stages
# at which a reviewer still has something to do, among those a stream
# reaches. Ready to update and Updating belong to Update ParishSoft.
WAITING_STAGES = frozenset(
    stage.value
    for stage in (
        Stage.TO_REVIEW,
        Stage.CONFLICT,
        Stage.TO_ENTER_BY_HAND,
        Stage.TO_HANDLE,
        Stage.ENTERED_INCORRECTLY,
    )
)
# A Ministry leader has no update phase: only To review waits on them.
LEADER_WAITING = frozenset({Stage.TO_REVIEW.value})
# The run's sorts, in the start page's order, with their words.
SORTS = (
    ("name", "Name A–Z"),
    ("oldest", "Oldest submitted first"),
    ("newest", "Newest submitted first"),
)
FAMILY = "family"


class TooManyPages(ValueError):
    """The selection has more pages than one run holds (``MAX_SUBJECTS``)."""


@dataclass(frozen=True, slots=True)
class Item:
    """One matching item of a stream, as the sequence needs it.

    ``family`` is the Family's campaign record id (the hub's address key),
    ``kind`` and ``key`` the subject's entity as its change rows name it:
    ``("family", "")`` for the Family itself, ``("member", DUID)`` or
    ``("proposed_member", local id)`` for a Member. ``name`` and ``duid``
    are the Family's (for a Member subject in a Member stream, the
    Member's); ``member_name`` names a Member page inside a grouped
    stream. ``stage`` is the item's Stage value.
    """

    id: str
    family: str
    kind: str
    key: str
    name: str | None
    duid: int | None
    stage: str
    submitted_at: datetime | None = None
    member_name: str | None = None


@dataclass(slots=True)
class Subject:
    """One page of a run: its entity and the matched items' ids."""

    family: str
    kind: str
    key: str
    items: list = field(default_factory=list)
    name: str | None = None
    duid: int | None = None
    member_name: str | None = None
    submitted_at: datetime | None = None

    def stored(self):
        """The run row's form of this subject (``ReviewRun.subjects``)."""
        return {
            "family": self.family,
            "kind": self.kind,
            "key": self.key,
            "items": self.items,
        }


def waiting(stage, *, leader=False):
    """Whether ``stage`` (a Stage value) is still waiting on the reader."""
    return stage in (LEADER_WAITING if leader else WAITING_STAGES)


def _subjects(items, *, by_member):
    """The items grouped by subject, each with its newest submitted time.

    ``by_member`` keeps a Member's items on a page of their own; otherwise
    every item of a Family is on its Family's page.
    """
    found = {}
    for item in items:
        if by_member and item.kind != FAMILY:
            key = (item.family, item.kind, item.key)
        else:
            key = (item.family, FAMILY, "")
        subject = found.get(key)
        if subject is None:
            subject = found[key] = Subject(
                *key, name=item.name, duid=item.duid, member_name=item.member_name
            )
        if item.id not in subject.items:
            subject.items.append(item.id)
        if item.submitted_at is not None and (
            subject.submitted_at is None or item.submitted_at > subject.submitted_at
        ):
            subject.submitted_at = item.submitted_at
    return list(found.values())


def _member_order(subject):
    """A Member page's place inside its Family: the hub's Member tab order.

    The Family's own page first; then ParishSoft Members by DUID; then the
    Members the Family added, by name (``hub_census.shape``'s order).
    """
    if subject.kind == FAMILY:
        return (0, 0, "")
    if subject.kind == "member" and str(subject.key).isdigit():
        return (1, int(subject.key), "")
    return (2, 0, (subject.member_name or "").casefold())


def _ordered(subjects, sort):
    """``subjects`` in the run's sort; equal submitted times fall back to Name A–Z.

    A subject with no submitted time sorts after those with one, in either
    direction.
    """
    named = sorted(
        subjects,
        key=lambda subject: name_order(subject.name, subject.duid, subject.family),
    )
    if sort == "name":
        return named
    dated = [subject for subject in named if subject.submitted_at is not None]
    undated = [subject for subject in named if subject.submitted_at is None]
    # sorted() is stable, so subjects with the same time keep the name order.
    dated.sort(key=lambda subject: subject.submitted_at, reverse=sort == "newest")
    return dated + undated


def sequence(items, *, grouped, sort):
    """The run's pages from the stream's matching ``items``; see the module.

    ``grouped`` is True for Census changes and Limitations, whose Families
    are sorted and then opened Family page first, Members after; False for
    a stream with one subject kind (its items' own). Raises
    ``TooManyPages`` beyond ``MAX_SUBJECTS`` pages. Returns ``Subject``
    rows in page order.
    """
    if not grouped:
        found = _ordered(_subjects(items, by_member=True), sort)
    else:
        pages = _subjects(items, by_member=True)
        families = {}
        for subject in pages:
            families.setdefault(subject.family, []).append(subject)
        # Each Family sorts by its Family name and its newest matched item.
        heads = []
        for family, members in families.items():
            first = members[0]
            heads.append(
                Subject(
                    family,
                    FAMILY,
                    "",
                    name=first.name,
                    duid=first.duid,
                    submitted_at=max(
                        (m.submitted_at for m in members if m.submitted_at),
                        default=None,
                    ),
                )
            )
        found = []
        for head in _ordered(heads, sort):
            found.extend(sorted(families[head.family], key=_member_order))
    if len(found) > MAX_SUBJECTS:
        raise TooManyPages(f"More than {MAX_SUBJECTS} pages.")
    return found


def progress(subjects, position, *, grouped, member_stream):
    """The words of page ``position`` (from 1) of a run's stored ``subjects``.

    "Family 12 of 87" in a Family stream, "Member 12 of 87" in Ministry
    requests, and in a grouped stream the Family's place and, on a Member
    page, the Member's place in it: "Family 12 of 87, Member 2 of 3".
    """
    total = len(subjects)
    if not grouped:
        word = _("Member") if member_stream else _("Family")
        return _("%(word)s %(place)s of %(total)s") % {
            "word": word,
            "place": f"{position:,}",
            "total": f"{total:,}",
        }
    families = []
    for subject in subjects:
        if not families or families[-1] != subject["family"]:
            families.append(subject["family"])
    current = subjects[position - 1]
    place = families.index(current["family"]) + 1
    words = _("Family %(place)s of %(total)s") % {
        "place": f"{place:,}",
        "total": f"{len(families):,}",
    }
    if current["kind"] == FAMILY:
        return words
    members = [
        subject
        for subject in subjects
        if subject["family"] == current["family"] and subject["kind"] != FAMILY
    ]
    return _("%(family)s, Member %(place)s of %(total)s") % {
        "family": words,
        "place": f"{members.index(current) + 1:,}",
        "total": f"{len(members):,}",
    }
