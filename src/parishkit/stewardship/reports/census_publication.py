"""What Update ParishSoft shows: what waits, the newest check and its run.

ADM-09's page (reports spec, "Update ParishSoft"; renamed from Publish to
ParishSoft by #1688) drives the census publication pipeline
(``responses.publication_tasks``). This module is its read side, with no
state of its own:

- ``waiting`` counts the review pipeline's Ready to update changes a check
  would plan, and for how many Members, and those it leaves out because
  their Member left the Family (rule 4, #1738);
- ``check_gate`` says why Check ParishSoft is unavailable, if it is;
- ``newest_plan`` and ``plan_view`` read the current campaign's newest plan,
  word its field rows for the plan table and, once it is confirmed, each
  written change's result from its Member's recorded attempts;
- ``offer`` says which of Update and Run again the plan's region offers,
  and why it is unavailable, if it is; ``newest_plan_check`` and
  ``confirm_refusal`` are the checks Update's and Run again's posts share
  (and the command line will, #1755).

A plan holds no values (only ids, versions, outcomes and hashes), so the
values come from the changes themselves, read and worded as Census changes
words them (``census_changes.census_rows``). A Conflict or Cannot check row
shows no ParishSoft value, since the plan did not keep the one it read.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.responses.accepted_base import with_accepted_base
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.responses.publication import Outcome
from parishkit.stewardship.responses.publication_models import (
    PublicationField,
    PublicationOutcome,
    PublicationPlan,
)
from parishkit.stewardship.responses.publication_plans import (
    confirmation_counts,
    unlisted_changes,
)
from parishkit.stewardship.responses.publication_tasks import (
    PUBLICATION_TASK_TYPE,
    approved_changes,
    final_refresh_command,
    publication_running,
)
from parishkit.stewardship.responses.publication_worker import unfinished
from parishkit.stewardship.source.refresh_models import SourceRefreshCommand
from parishkit.stewardship.web.refusals import UserFacingUnavailable
from parishkit.stewardship.web.tables import Sorting

from .census_changes import census_rows
from .export_services import admit_campaign

# The executions the review pipeline places an accepted Automatic change at
# Ready to update (pipeline.census_placement): pending, or failed by an
# earlier update. An accepted change in conflict is a Conflict, not Ready,
# though the check still considers it, so its Member is held back.
READY_EXECUTIONS = ("pending", "failed")
# How long a plan may be confirmed after its check; the confirmation's SQL
# guard (migration 0114) refuses an older one by the database clock.
PLAN_LIFETIME = timedelta(hours=1)
# A write on a Member the plan blocks (a Conflict or Cannot check beside it)
# is held back: a Member is written whole or not at all.
HELD = "held"
# A written change's result once the plan is confirmed, from its Member's
# recorded attempts (reports spec, "Confirming and running").
UPDATED, CONFIRMED, NOT_WRITTEN, FAILED, NOT_FINISHED = (
    "updated",
    "confirmed",
    "not_written",
    "failed",
    "not_finished",
)
# Each shown outcome's words, in the order the Outcome column sorts them.
OUTCOMES = {
    Outcome.WRITE.value: _("Will be written"),
    HELD: _("Held back"),
    Outcome.ALREADY_IN_PARISHSOFT.value: _("Already in ParishSoft"),
    Outcome.CONFLICT.value: _("Conflict"),
    Outcome.CANNOT_CHECK.value: _("Cannot check"),
    UPDATED: _("Updated in ParishSoft"),
    CONFIRMED: _("Confirmed"),
    NOT_WRITTEN: _("Not written: changed since the check"),
    FAILED: _("Failed"),
    NOT_FINISHED: _("Not finished"),
}
# A failed attempt's sanitized word (publication_gateway.FAILURE_KINDS and
# the worker's own verify_*), as the Failed outcome words it.
FAILURE_REASONS = {
    "read_unavailable": _("ParishSoft did not answer"),
    "write_unavailable": _("ParishSoft did not answer"),
    "verify_unavailable": _("ParishSoft did not answer"),
    "write_rejected": _("ParishSoft refused the change"),
    "verify_mismatch": _("The details read back differently"),
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
    """Why a control is unavailable: its reason code and its words."""

    reason: str
    message: str

    def refusal(self):
        """The refusal a post meets for this reason, as the page words it."""
        return UserFacingUnavailable(self.message, reason=self.reason)


OFF = Gate(
    "publication_off",
    _(
        "Updating ParishSoft is switched off, so nothing is read or written "
        "here. Ready to update changes wait on Census changes."
    ),
)
NOTHING_APPROVED = Gate(
    "publication_nothing_approved",
    _("No changes are Ready to update. Accept changes on Census changes first."),
)
RUNNING = Gate(
    "publication_running",
    _("An update is running. Wait for it to finish."),
)
HELD_CAMPAIGN = Gate(
    "publication_campaign_held",
    _(
        "Updating waits until other campaign work (a restore review, or a "
        "cleanup, purge or go-live step) finishes."
    ),
)
TESTING = Gate(
    "publication_testing",
    _("Testing mode: nothing is written to ParishSoft."),
)
NOTHING_TO_WRITE = Gate(
    "publication_nothing_to_write",
    _("This check found nothing to write. Check ParishSoft again."),
)
EXPIRED = Gate(
    "publication_plan_expired",
    _("This check is more than an hour old. Check ParishSoft again."),
)
CHANGED = Gate(
    "publication_plan_changed",
    _("Something changed since the check. Check ParishSoft again."),
)
SUPERSEDED = Gate(
    "publication_plan_superseded",
    _("A newer check exists. Review it first."),
)
OVERLAP = Gate(
    "publication_overlap",
    _("Some of these changes are still being written. Wait for that update to finish."),
)
FINISHED = Gate(
    "publication_finished",
    _("Every Member in this check is finished."),
)


def enabled():
    """Whether this web deployment's ``parishsoft_publication`` is on."""
    return getattr(settings, "STEWARDSHIP_PARISHSOFT_PUBLICATION", False) is True


def waiting(campaign_id):
    """The Ready to update changes a check would plan, and their Members.

    Exactly the changes the pipeline places at Ready to update that a
    preflight would plan: one whose Member is no longer an active, living
    Member of its Family is kept open (rule 4, #1738) but left unplanned
    (``unlisted_changes``), so it is counted apart (``away``), never as
    waiting, and cannot make Check ParishSoft available on its own.
    """
    rows = list(
        approved_changes(campaign_id)
        .filter(execution__in=READY_EXECUTIONS)
        .values_list("pk", "entity_key")
    )
    unlisted = unlisted_changes(pk for pk, _ in rows)
    kept = [key for pk, key in rows if pk not in unlisted]
    return {
        "changes": len(kept),
        "members": len(set(kept)),
        "away": len(rows) - len(kept),
    }


def _held(campaign_id):
    """Whether other campaign work holds the campaign now."""
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return True
    return False


def check_gate(campaign_id, *, enabled, waiting_changes):
    """Why Check ParishSoft is unavailable now, or None when it is available.

    The switch first, then nothing to check, an update run unfinished, and
    other campaign work holding the campaign (the preflight request refuses
    that one itself too).
    """
    if not enabled:
        return OFF
    if not waiting_changes:
        return NOTHING_APPROVED
    if publication_running(campaign_id):
        return RUNNING
    if _held(campaign_id):
        return HELD_CAMPAIGN
    return None


def newest_plan(campaign_id):
    """The campaign's newest plan, whoever made it, or None."""
    return (
        PublicationPlan.objects.filter(campaign_id=campaign_id)
        .order_by("-created_at", "-pk")
        .first()
    )


def newest_plan_check(campaign_id, plan_id):
    """The campaign's newest plan, refusing any other one as superseded.

    No SQL guard or service checks this: a newer plan made in another tab or
    by another Administrator makes Update and Run again refuse the older one,
    so an Administrator always confirms the newest check they have seen
    (reports spec, "Confirming and running"). The page's posts and the
    command line (#1755) share it.
    """
    plan = newest_plan(campaign_id)
    if plan is None or plan.pk != plan_id:
        raise SUPERSEDED.refusal()
    return plan


def latest_run(plan):
    """The plan's latest run task (its retries share one root), or None."""
    return (
        TaskRun.objects.filter(
            task_type=PUBLICATION_TASK_TYPE, domain_request_id=plan.pk
        )
        .order_by("-retry_sequence", "-created_at")
        .first()
    )


def production():
    """Whether the system is in Production mode now."""
    return SystemConfiguration.objects.filter(mode="production").exists()


def _expired(plan):
    """Whether the plan's hour to be confirmed in has passed."""
    return plan.created_at <= timezone.now() - PLAN_LIFETIME


@dataclass(frozen=True)
class Offer:
    """What the plan's region offers: Update, Run again, or neither.

    ``kind`` is ``"update"``, ``"rerun"`` or None; ``gate`` is why the
    offered button is unavailable, or None; ``unfinished`` is how many of the
    plan's Members a rerun would try.
    """

    kind: str | None
    gate: Gate | None = None
    unfinished: int = 0


def offer(plan, counts, *, run):
    """Which button the plan's region offers, and why it is unavailable.

    A plan from Testing mode offers neither (its line says nothing is
    written). Before its confirmation, Update is offered, unavailable while
    the plan writes nothing, the system is in Testing mode, a run is
    unfinished, other campaign work holds the campaign or the plan is an
    hour old. After it, the run's status shows instead; Run again is offered
    only once the run's task ended without completing (failed or
    cancelled) and a Member has no final outcome. The page never offers
    both, so the region has one primary button.
    """
    if plan.testing:
        return Offer(None)
    # A confirmation queues its run in the same transaction
    # (confirm_and_publish), so a plan with no run is not confirmed.
    if run is None:
        for gate, applies in (
            (NOTHING_TO_WRITE, lambda: not counts["write_entities"]),
            (TESTING, lambda: not production()),
            (RUNNING, lambda: publication_running(plan.campaign_id)),
            (HELD_CAMPAIGN, lambda: _held(plan.campaign_id)),
            (EXPIRED, lambda: _expired(plan)),
        ):
            if applies():
                return Offer("update", gate)
        return Offer("update")
    if run is None or run.state in NONTERMINAL_STATES or run.state == "succeeded":
        return Offer(None)
    left = len(unfinished([plan.pk]))
    if not left:
        return Offer(None)
    gate = HELD_CAMPAIGN if _held(plan.campaign_id) else None
    return Offer("rerun", gate, left)


def confirm_refusal(error):
    """The page's refusal for a database error from confirming a plan.

    The confirmation's SQL guard (migration 0114) words each refusal: a
    sign-in that is no longer live (SQLSTATE 42501, a denial), an
    overlapping unfinished plan (23505), and otherwise a plan that is too
    old or changed since the check (23514). The page has already refused an
    old plan by its own clock, so any other refusal is a change.
    """
    state = getattr(getattr(error, "__cause__", None), "sqlstate", None)
    if state == "42501":
        # The guard found the sign-in no longer live: a denial, as the page's
        # own admission would give it.
        return PermissionError("Update ParishSoft needs a live sign-in.")
    if state == "23505":
        return OVERLAP.refusal()
    return CHANGED.refusal()


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


def _results(plan, *, run):
    """Each entity's run result key and its failure word, by entity id.

    Read from the entities' recorded attempts once the plan is confirmed
    (``run`` is its latest run task, None before): ``published`` is Updated
    in ParishSoft, ``invalidated`` after a failed attempt is Failed with the
    last attempt's word, ``invalidated`` alone is Not written, and an entity
    with neither, once the run's task has ended, is Not finished.
    """
    if run is None:
        return {}
    final, failure = {}, {}
    for entity_id, result, kind in (
        PublicationOutcome.objects.filter(entity__plan=plan)
        .order_by("entity_id", "attempt")
        .values_list("entity_id", "result", "failure_kind")
    ):
        if result == "failed":
            failure[entity_id] = kind
        else:
            final[entity_id] = result
    ended = run.state not in NONTERMINAL_STATES
    results = {}
    for entity_id in PublicationField.objects.filter(entity__plan=plan).values_list(
        "entity_id", flat=True
    ):
        outcome = final.get(entity_id)
        if outcome == "published":
            results[entity_id] = (UPDATED, "")
        elif outcome == "invalidated":
            kind = failure.get(entity_id)
            results[entity_id] = (FAILED, kind) if kind else (NOT_WRITTEN, "")
        elif ended:
            results[entity_id] = (NOT_FINISHED, "")
    return results


def plan_row(field, change, *, blocked, accepted, result=None):
    """One plan table row: the change as Census changes words it, and its plan.

    ``field`` is the ``PublicationField``, ``change`` the change's worded
    Census changes row, ``blocked`` whether the plan blocks its Member, and
    ``accepted`` whether the change has an active accepted base. A write
    on a blocked Member is Held back. Will write is the reviewer's value,
    else the Family's answer; ParishSoft at the check is the value the
    change was compared with (the Family's baseline, or ParishSoft's value
    accepted over a Conflict), the value already there, or, where the plan
    kept none, why not. ``result`` is the Member's run result (``_results``)
    for a planned write once the plan is confirmed: an Updated change whose
    own row a refresh has since Confirmed says Confirmed, and a Failed one
    says why.
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
    label = None
    if outcome == Outcome.WRITE.value and result is not None:
        outcome, kind = result
        if outcome == UPDATED and change.get("stage") == "confirmed":
            outcome = CONFIRMED
        if outcome == FAILED:
            label = gettext("Failed: %(reason)s") % {
                "reason": FAILURE_REASONS.get(kind, _("the write did not complete"))
            }
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
        "outcome_label": label or OUTCOMES[outcome],
    }


def summary(counts):
    """The plan's summary sentences, from the counts the confirmation binds.

    "12 changes for 5 Members will be written. 2 Members are held back. 3
    changes were already in ParishSoft." A count of none leaves its
    sentence out, and a plan that writes nothing says so.
    """
    if counts["write_fields"]:
        written = gettext("%(what)s will be written.") % {
            "what": _changes_for_members(counts)
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


def _changes_for_members(counts):
    """ "12 changes for 5 Members", the plan's written counts in words."""
    fields, members = counts["write_fields"], counts["write_entities"]
    return gettext("%(changes)s for %(members)s") % {
        "changes": ngettext("%(n)s change", "%(n)s changes", fields)
        % {"n": f"{fields:,}"},
        "members": ngettext("%(n)s Member", "%(n)s Members", members)
        % {"n": f"{members:,}"},
    }


def update_label(counts):
    """Update's button label, with the plan's count: "Update 12 changes"."""
    fields = counts["write_fields"]
    return ngettext("Update %(n)s change", "Update %(n)s changes", fields) % {
        "n": f"{fields:,}"
    }


def update_question(counts):
    """Update's confirmation title: "Write 12 changes for 5 Members to ParishSoft?"."""
    return gettext("Write %(what)s to ParishSoft?") % {
        "what": _changes_for_members(counts)
    }


def rerun_question(count):
    """Run again's confirmation title, naming the Members it would try."""
    return ngettext(
        "Try the %(n)s Member this run did not finish?",
        "Try the %(n)s Members this run did not finish?",
        count,
    ) % {"n": f"{count:,}"}


def refreshing(run_ids):
    """Whether the final refresh an update run requested is still unfinished.

    Each run that wrote a Member requests one full refresh under a command
    id derived from the run (``publication_tasks.final_refresh_command``),
    so the page finds it without any state of its own.
    """
    roots = SourceRefreshCommand.objects.filter(
        pk__in=[final_refresh_command(run_id) for run_id in run_ids]
    ).values("request__task_root_id")
    return TaskRun.objects.filter(
        root_id__in=roots, state__in=NONTERMINAL_STATES
    ).exists()


def plan_view(plan):
    """The newest plan as the page shows it: who and when, counts and rows.

    Field rows the plan leaves at ParishSoft's value (not accepted, or By
    hand) are counted, not shown. ``rows`` start grouped by Family DUID,
    Member DUID and field. Once the plan is confirmed, ``run`` is its latest
    run task, each written change carries its Member's result, ``offer``
    says whether Run again applies, and ``refreshing`` whether the final
    refresh is still unfinished.
    """
    fields = list(
        PublicationField.objects.filter(entity__plan=plan).select_related("entity")
    )
    changes = {row["id"]: row for row in census_rows(plan.campaign_id)}
    accepted = _accepted([field.proposal_id for field in fields])
    run = latest_run(plan)
    results = _results(plan, run=run)
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
                result=results.get(field.entity_id),
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
    runs = (
        list(
            TaskRun.objects.filter(
                task_type=PUBLICATION_TASK_TYPE, domain_request_id=plan.pk
            ).values_list("pk", flat=True)
        )
        if run is not None
        else []
    )
    return {
        "plan": plan,
        "checked_by": _checked_by(plan),
        "rows": rows,
        "others": others,
        "counts": counts,
        "summary": summary(counts),
        "update_label": update_label(counts),
        "run": run,
        "offer": offer(plan, counts, run=run),
        "refreshing": bool(runs) and refreshing(runs),
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
