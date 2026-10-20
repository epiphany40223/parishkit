"""What Publish to ParishSoft shows: what waits, and the newest check (#528).

ADM-09's page (reports spec, "Publish to ParishSoft") drives the census
publication pipeline (``responses.publication_tasks``). This module is its
read side, with no state of its own:

- ``waiting`` counts the approved Automatic Member changes a check would
  plan, and for how many Members;
- ``check_gate`` says why Check ParishSoft is unavailable, if it is;
- ``newest_plan`` and ``plan_view`` read the current campaign's newest plan
  and word its field rows for the plan table.

A plan holds no values (only ids, versions, outcomes and hashes), so the
values come from the changes themselves, read and worded as Census changes
words them (``census_changes.census_rows``). A Conflict or Cannot check row
shows no ParishSoft value, since the plan did not keep the one it read.
"""

from dataclasses import dataclass

from django.conf import settings
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.responses.accepted_base import with_accepted_base
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.responses.publication import Outcome
from parishkit.stewardship.responses.publication_models import (
    PublicationField,
    PublicationPlan,
)
from parishkit.stewardship.responses.publication_plans import (
    confirmation_counts,
    unlisted_changes,
)
from parishkit.stewardship.responses.publication_tasks import (
    approved_changes,
    publication_running,
)
from parishkit.stewardship.web.refusals import UserFacingUnavailable
from parishkit.stewardship.web.tables import Sorting

from .census_changes import census_rows
from .export_services import admit_campaign

# A write on a Member the plan blocks (a Conflict or Cannot check beside it)
# is held back: a Member is written whole or not at all.
HELD = "held"
# Each shown outcome's words, in the order the Outcome column sorts them.
OUTCOMES = {
    Outcome.WRITE.value: _("Will be written"),
    HELD: _("Held back"),
    Outcome.ALREADY_IN_PARISHSOFT.value: _("Already in ParishSoft"),
    Outcome.CONFLICT.value: _("Conflict"),
    Outcome.CANNOT_CHECK.value: _("Cannot check"),
}
# What ParishSoft at the check says where the plan kept no value.
NOT_KEPT = {
    Outcome.CONFLICT.value: _("Changed since the Family answered"),
    Outcome.CANNOT_CHECK.value: _("Could not be read"),
}


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# Every column sorts in memory; rows start grouped by Family DUID, Member
# DUID and field, so the default (Family) keeps each Member's rows together.
SORTING = Sorting.by_column(
    {
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "duid": lambda row: row["family_duid"],
        "who": lambda row: _text(row["who"]),
        "member_duid": lambda row: row["member_duid"] or 0,
        "what": lambda row: _text(row["label"]),
        "outcome": lambda row: list(OUTCOMES).index(row["outcome"]),
    },
    default="family",
)


@dataclass(frozen=True)
class Gate:
    """Why Check ParishSoft is unavailable: its reason code and its words."""

    reason: str
    message: str

    def refusal(self):
        """The refusal a post meets for this reason, as the page words it."""
        return UserFacingUnavailable(self.message, reason=self.reason)


OFF = Gate(
    "publication_off",
    _(
        "Publishing to ParishSoft is switched off. Enter Automatic changes "
        "by hand from Census changes."
    ),
)
NOTHING_APPROVED = Gate(
    "publication_nothing_approved",
    _("No approved changes to publish. Approve changes on Census changes first."),
)
RUNNING = Gate(
    "publication_running",
    _("Publishing is running. Wait for it to finish."),
)
HELD_CAMPAIGN = Gate(
    "publication_campaign_held",
    _(
        "Publishing waits until other campaign work (a restore review, or a "
        "cleanup, purge or go-live step) finishes."
    ),
)


def enabled():
    """Whether this web deployment's ``parishsoft_publication`` is on."""
    return getattr(settings, "STEWARDSHIP_PARISHSOFT_PUBLICATION", False) is True


def waiting(campaign_id):
    """The approved Automatic changes not yet published, and their Members.

    Exactly the changes a preflight would plan: a change whose Member is no
    longer an active, living Member of its Family is kept open (rule 4,
    #1738) but left unplanned (``unlisted_changes``), so it is not counted
    as waiting and cannot make Check ParishSoft available on its own.
    """
    rows = list(approved_changes(campaign_id).values_list("pk", "entity_key"))
    unlisted = unlisted_changes(pk for pk, _ in rows)
    kept = [key for pk, key in rows if pk not in unlisted]
    return {"changes": len(kept), "members": len(set(kept))}


def check_gate(campaign_id, *, enabled, waiting_changes):
    """Why Check ParishSoft is unavailable now, or None when it is available.

    The switch first, then nothing to check, a publish run unfinished, and
    other campaign work holding the campaign (the preflight request refuses
    that one itself too).
    """
    if not enabled:
        return OFF
    if not waiting_changes:
        return NOTHING_APPROVED
    if publication_running(campaign_id):
        return RUNNING
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return HELD_CAMPAIGN
    return None


def newest_plan(campaign_id):
    """The campaign's newest plan, whoever made it, or None."""
    return (
        PublicationPlan.objects.filter(campaign_id=campaign_id)
        .order_by("-created_at", "-pk")
        .first()
    )


def _checked_by(plan):
    """The sign-in address of whoever asked for the plan's check."""
    email = (
        PortalUser.objects.filter(pk=plan.actor_id)
        .values_list("email", flat=True)
        .first()
    )
    return email or _("a former portal user")


def _accepted(ids):
    """The changes among ``ids`` with an active accepted base (ADM-09.03)."""
    return {
        str(pk)
        for pk in with_accepted_base(ProposedChange.objects.filter(pk__in=ids))
        .filter(conflict_accepted=True)
        .values_list("pk", flat=True)
    }


def plan_row(field, change, *, blocked, accepted):
    """One plan table row: the change as Census changes words it, and its plan.

    ``field`` is the ``PublicationField``, ``change`` the change's worded
    Census changes row, ``blocked`` whether the plan blocks its Member, and
    ``accepted`` whether the change has an active accepted base. A write
    on a blocked Member is Held back. Will write is the edited value, else
    the Family's answer; ParishSoft at the check is the value the change
    was compared with (the Family's baseline, or ParishSoft's value accepted
    by Use the Family's value anyway), the value already there, or, where
    the plan kept none, why not.
    """
    outcome = field.outcome
    will_write = change["edited"] if change["admin_value_set"] else None
    will_write = change["family_answer"] if will_write is None else will_write
    if outcome == Outcome.WRITE.value and blocked:
        outcome = HELD
    if outcome in NOT_KEPT:
        at_check, will_write = NOT_KEPT[outcome], ""
    elif outcome == Outcome.ALREADY_IN_PARISHSOFT.value:
        at_check = will_write
    else:
        at_check = change["parishsoft_now"] if accepted else change["baseline"]
    return {
        "id": change["id"],
        "family_name": change["family_name"],
        "family_duid": change["family_duid"],
        "who": change["who"],
        "member_duid": change["member_duid"],
        "label": change["label"],
        "at_check": at_check,
        "will_write": will_write,
        "outcome": outcome,
        "outcome_label": OUTCOMES[outcome],
    }


def summary(counts):
    """The plan's summary sentences, from the counts the confirmation binds.

    "12 changes for 5 Members will be written. 2 Members are held back. 3
    changes were already in ParishSoft." A count of none leaves its
    sentence out, and a plan that writes nothing says so.
    """
    fields, members = counts["write_fields"], counts["write_entities"]
    if fields:
        written = gettext("%(changes)s for %(members)s will be written.") % {
            "changes": ngettext("%(n)s change", "%(n)s changes", fields)
            % {"n": f"{fields:,}"},
            "members": ngettext("%(n)s Member", "%(n)s Members", members)
            % {"n": f"{members:,}"},
        }
    else:
        written = gettext("Nothing will be written.")
    lines = [written]
    if counts["blocked_entities"]:
        held = counts["blocked_entities"]
        lines.append(
            ngettext("%(n)s Member is held back.", "%(n)s Members are held back.", held)
            % {"n": f"{held:,}"}
        )
    if counts["already"]:
        already = counts["already"]
        lines.append(
            ngettext(
                "%(n)s change was already in ParishSoft.",
                "%(n)s changes were already in ParishSoft.",
                already,
            )
            % {"n": f"{already:,}"}
        )
    return " ".join(lines)


def plan_view(plan):
    """The newest plan as the page shows it: who and when, counts and rows.

    Field rows the plan leaves at ParishSoft's value (not approved, or By
    hand) are counted, not shown. ``rows`` start grouped by Family DUID,
    Member DUID and field.
    """
    fields = list(
        PublicationField.objects.filter(entity__plan=plan).select_related("entity")
    )
    changes = {row["id"]: row for row in census_rows(plan.campaign_id)}
    accepted = _accepted([field.proposal_id for field in fields])
    rows, others = [], 0
    for field in fields:
        change = changes.get(str(field.proposal_id))
        if field.outcome not in OUTCOMES or change is None:
            others += 1
            continue
        rows.append(
            plan_row(
                field,
                change,
                blocked=field.entity.blocked,
                accepted=str(field.proposal_id) in accepted,
            )
        )
    rows.sort(
        key=lambda row: (row["family_duid"], row["member_duid"] or 0, row["label"])
    )
    counts = confirmation_counts(plan.pk) | {
        "already": sum(
            field.outcome == Outcome.ALREADY_IN_PARISHSOFT.value for field in fields
        )
    }
    return {
        "plan": plan,
        "checked_by": _checked_by(plan),
        "rows": rows,
        "others": others,
        "counts": counts,
        "summary": summary(counts),
    }


def read_unconfirmed(task_id):
    """Whether a check stopped because the contact read is not confirmed yet.

    Its preflight task records ``census_publication_read_unconfirmed`` with
    the task as subject before it fails (``publication_tasks._refuse``), so
    the page can say why nothing was planned.
    """
    return AuditEvent.objects.filter(
        event_type=Action.CENSUS_PUBLICATION_READ_UNCONFIRMED, subject_id=task_id
    ).exists()
