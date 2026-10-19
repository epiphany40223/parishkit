"""``census update-*``: Update ParishSoft from the command line (#1755).

The Update ParishSoft page (``admin:update_parishsoft``,
``reports.census_publication_views``; #528, #1688) has four steps, and each
is one command, through the page's own pieces so the two cannot drift:

- ``census update-check`` is Check ParishSoft: the page's gate
  (``census_publication.check_gate``), then ``request_preflight``;
- ``census update-plan`` is the page's read of the newest plan (or a named
  one, ``census_publication.plan_view``), reduced to ids, versions, counts,
  outcomes and times;
- ``census update-confirm`` is Update's Apply: the shared newest-plan check
  (``census_publication.newest_plan_check``), what the page offers
  (``census_publication.offer``), the counts it asked about, then
  ``confirm_and_publish`` with the command session's sign-in;
- ``census update-rerun`` is Run again: the same checks, then
  ``rerun_publication``.

Every command checks the page's admission (``census_publication_views.
_principal``: the Administrator's ``PUBLISH_CENSUS``) and the page's switch
(``census_publication.enabled``: the deployment's ``parishsoft_publication``,
which the command process reads as the web does). The writes run in the
page's command scope (``jobs.task_retries.command_scope``) and record their
own ``admin_cmd_census_update_*`` event in the same transaction, once per
request key, as ``refresh start`` does. A refusal the page would show is
the page's own ``UserFacingUnavailable``, marked with the command's error
code and reason (``_refused``), so the refusal log records the page's
reason and the document carries it as ``error.reason``.

This module is imported before Django is set up (its read models give the
catalog's result fields), so everything else is imported where it is used.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, _held

# The page's reasons, by the command line's error code (admin automation
# spec, "Update ParishSoft commands"): a plan that moved on is stale, a
# request with nothing to act on is invalid, and anything else (the switch,
# Testing mode, a held campaign, a run or a check under way) is not
# available now.
STALE_REASONS = frozenset(
    {
        "publication_plan_changed",
        "publication_plan_expired",
        "publication_plan_superseded",
    }
)
INVALID_REASONS = frozenset(
    {
        "publication_nothing_approved",
        "publication_nothing_to_write",
        "publication_finished",
    }
)


def _refused(error):
    """Mark one of the page's refusals with the command line's code and reason.

    ``error`` is the page's ``UserFacingUnavailable`` (a ``Gate``'s
    ``refusal()``), kept as it is so the refusal log reads its closed reason
    as the page's log does; ``admin_cli.classify`` reads ``command_code``
    and the document ``command_reason``. Returns ``error``.
    """
    reason = error.reason
    if reason in STALE_REASONS:
        error.command_code = "stale_version"
    elif reason in INVALID_REASONS:
        error.command_code = "invalid"
    else:
        error.command_code = "not_available"
    error.command_reason = reason
    return error


def _refusing(step):
    """Run ``step()``, turning the page's refusals into the command's."""
    from .web.refusals import UserFacingUnavailable

    try:
        return step()
    except UserFacingUnavailable as error:
        raise _refused(error) from None


def _admit_update(caller, store, *, final=False):
    """The page's admission of its posts, or refuse.

    The page's own ``_principal`` (an Administrator who may update),
    recording activity as its posts do. ``command_scope`` repeats it
    (``final``) inside the transaction as a read-only recheck that writes
    nothing. A session that ended is exit 5 (``session_ended``), not the
    page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin
    from .reports.census_publication_views import _principal

    try:
        return _principal(caller, store, read_only=final)
    except PermissionError:
        if authenticated_admin(caller, store=store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _switched_on():
    """Refuse every step while the deployment's switch is off, as the page."""
    from .reports import census_publication as publication

    if not publication.enabled():
        raise publication.OFF.refusal()


def _campaign():
    """The current campaign, admitted as the page admits its reads.

    No current campaign is ``not_available``; a campaign the reports cannot
    read now is the page's own refusal.
    """
    from .admin_report_reads import _current
    from .reports.read_admission import admit_report_read

    campaign_id = _current()
    admit_report_read(campaign_id)
    return campaign_id


def _task(status):
    """A task's id and state, as the command prints it."""
    return {"id": status.run_id, "state": status.state}


def _command_event(caller, actor, action):
    """Record the command's own event: the automation session as subject."""
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor.identity,
        subject_id=caller.automation_session_id,
        context={"outcome": Outcome.SUCCEEDED},
    )


def _write(caller, service, actor, step, *, request_key, context, plan=None):
    """Run one write ``step()`` in the page's command scope, as the page.

    ``step`` returns ``(model, recorded)``, ``recorded`` true when it
    recorded the command's event (a new key), so a database error after it
    may have committed (``context["committed"]``). A service refusing what
    the page's checks passed a moment before (a ``PermissionError``) is
    worded as the page words it (``census_publication_views._raced``) for
    ``plan()``, the plan the step checked, once the Administrator is
    admitted again (a lost role is still ``denied``).
    """
    from django.db import DatabaseError

    from .jobs.storage import TaskRetryConflict
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .reports.census_publication_views import _raced

    context["request_id"] = str(request_key)
    written = []

    def scoped():
        """The step inside the page's command scope."""
        # A step repeated after an activating change starts over.
        written.clear()
        with command_scope(caller, service, actor, admit=_admit_update):
            model, recorded = _refusing(step)
            if recorded:
                written.append(True)
            return model

    try:
        model = _held(scoped)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except (PermissionError, TaskRetryConflict):
        _admit_update(caller, service.store, final=True)
        if plan is None or plan() is None:
            raise
        raise _refused(_raced(caller, service, plan())) from None
    context["committed"] = True
    return model


@dataclass(frozen=True)
class UpdateCheck(ReadModel):
    """The check a ``census update-check`` queued, or found for its key.

    ``task`` is the preflight task's ``id`` and ``state``; follow it with
    ``task show TASK_ID --watch``, then read the plan with ``census
    update-plan``.
    """

    created: bool
    request_key: UUID
    task: dict


def update_check(caller, service, *, request_key, context):
    """``census update-check``: Check ParishSoft (#1755).

    As the page's post: the switch, the current campaign, the page's gate
    for a new key (something Ready to update, no run unfinished or stopped,
    the campaign free), then ``request_preflight`` under the key, which a
    repeat by the same Administrator returns without a new event. The
    preflight service's own admission refusing what the gate passed (other
    campaign work took the campaign meanwhile) is the page's
    ``publication_campaign_held``. A key another Administrator used is
    ``invalid``.
    """
    from .audit.schemas import Action
    from .jobs.models import TaskRun
    from .observability import current_correlation
    from .reports import census_publication as publication
    from .responses.publication_tasks import PREFLIGHT_TASK_TYPE, request_preflight

    actor = _admit_update(caller, service.store)

    def step():
        """The page's gate and request, with the command's event."""
        _switched_on()
        campaign_id = _campaign()
        # The work-order lock the scope holds serializes concurrent repeats.
        repeat = TaskRun.objects.filter(
            task_type=PREFLIGHT_TASK_TYPE,
            idempotency_key=str(request_key),
            initiated_by_id=actor.identity,
        ).exists()
        if not repeat:
            gate = publication.check_gate(
                campaign_id,
                enabled=True,
                waiting_changes=publication.waiting(campaign_id)["changes"],
            )
            if gate is not None:
                raise gate.refusal()
        try:
            task = request_preflight(
                service.store,
                actor.identity,
                request_key=request_key,
                correlation_id=current_correlation(),
                enabled=publication.enabled(),
            )
        except PermissionError:
            # As the page: the gate passed, but other campaign work took
            # the campaign first. A lost role is found by the scope's own
            # admission, which runs again as the error leaves it.
            _admit_update(caller, service.store, final=True)
            raise publication.HELD_CAMPAIGN.refusal() from None
        if not repeat:
            _command_event(caller, actor, Action.ADMIN_CMD_CENSUS_UPDATE_CHECK)
        model = UpdateCheck(
            created=not repeat, request_key=request_key, task=_task(task)
        )
        return model, not repeat

    return _write(
        caller, service, actor, step, request_key=request_key, context=context
    )


@dataclass(frozen=True)
class UpdatePlan(ReadModel):
    """Update ParishSoft as ``census update-plan`` prints it: no value or name.

    ``waiting`` is what a check would plan (``changes``, ``members`` and
    ``away``, Ready changes whose Member left the Family); ``check`` whether
    Check ParishSoft is ``available`` and, if not, its ``reason``;
    ``checking`` whether a check is queued or running. ``plan`` is null
    before the first check, else the plan's ``id``, ``checked_by`` (the
    checking Administrator's user id), ``created_at``, ``testing``,
    ``expires_at`` and ``expired`` (its hour to be confirmed in),
    ``counts``, ``confirmed``, ``run`` (its latest run task's ``id`` and
    ``state``, or null), ``refreshing`` and ``offer`` (``kind``:
    ``update``, ``rerun`` or null; ``available``; ``reason``;
    ``unfinished``). ``rows`` are the page's table rows: ``proposal_id``,
    ``proposal_version``, ``field``, ``member_duid`` and ``outcome``;
    ``others`` the plan's rows the table leaves out.
    """

    campaign_id: UUID
    waiting: dict
    check: dict
    checking: bool
    plan: dict | None
    rows: list
    others: int


def _plan_document(view):
    """The plan's own facts from the page's ``plan_view``, ids and counts only."""
    from .reports.census_publication import PLAN_LIFETIME, _expired

    plan, offer, run = view["plan"], view["offer"], view["run"]
    return {
        "id": plan.pk,
        "checked_by": plan.actor_id,
        "created_at": plan.created_at,
        "testing": plan.testing,
        "expires_at": plan.created_at + PLAN_LIFETIME,
        "expired": _expired(plan),
        "counts": view["counts"],
        # A confirmation queues its run in the same transaction, so a plan
        # with a run is confirmed (census_publication.offer).
        "confirmed": run is not None,
        "run": None if run is None else {"id": run.pk, "state": run.state},
        "refreshing": view["refreshing"],
        "offer": {
            "kind": offer.kind,
            "available": offer.kind is not None and offer.gate is None,
            "reason": None if offer.gate is None else offer.gate.reason,
            "unfinished": offer.unfinished,
        },
    }


def _plan_rows(view):
    """The table's rows as the command prints them: ids, field and outcome.

    The page's rows carry each change's proposal id, Member DUID and
    outcome (the run's result once confirmed); the version the plan
    recorded and the field come from the plan's own field rows.
    """
    from .responses.publication_models import PublicationField

    planned = {
        str(proposal_id): (version, field)
        for proposal_id, version, field in PublicationField.objects.filter(
            entity__plan=view["plan"]
        ).values_list("proposal_id", "proposal_version", "field")
    }
    return [
        {
            "proposal_id": row["id"],
            "proposal_version": planned[str(row["id"])][0],
            "field": planned[str(row["id"])][1],
            "member_duid": row["member_duid"],
            "outcome": row["outcome"],
        }
        for row in view["rows"]
    ]


def read_update_plan(caller, service, plan_id):
    """``census update-plan``: the page's read, from a session of any scope.

    The shared report read (``admin_report_reads._report``) admits passively
    with the page's ``_principal``, checks the current campaign and reads
    under the campaign read guard, as the page's response does. The switch
    off is ``not_available`` (``publication_off``), as every step is, and a
    named plan of another campaign, or an unknown one, is ``not_available``.
    Records the page's ``census_publication_viewed`` with the number of rows.
    """
    from .admin_report_reads import _report
    from .reports import census_publication as publication
    from .reports.census_publication_views import _audit, _principal
    from .responses.publication_models import PublicationPlan

    def admit(actor, campaign_id):
        """The page's switch, before anything is read or recorded."""
        _refusing(_switched_on)

    def read(guard, actor, campaign_id):
        """The page's read of what waits, the gate and the plan."""
        waiting = publication.waiting(campaign_id)
        gate = publication.check_gate(
            campaign_id, enabled=True, waiting_changes=waiting["changes"]
        )
        if plan_id is None:
            plan = publication.newest_plan(campaign_id)
        else:
            plan = PublicationPlan.objects.filter(
                pk=plan_id, campaign_id=campaign_id
            ).first()
            if plan is None:
                raise NotAvailable("No such plan in the current campaign.")
        view = publication.plan_view(plan) if plan is not None else None
        rows = _plan_rows(view) if view is not None else []
        model = UpdatePlan(
            campaign_id=campaign_id,
            waiting=waiting,
            check={
                "available": gate is None,
                "reason": None if gate is None else gate.reason,
            },
            checking=publication.checking(),
            plan=_plan_document(view) if view is not None else None,
            rows=rows,
            others=view["others"] if view is not None else 0,
        )
        return model, len(rows)

    def audit(actor, campaign_id, outcome, count):
        """The page's view event, with the rows the command printed."""
        _audit(actor, campaign_id, outcome, count or 0)

    return _report(
        caller, service, principal=_principal, read=read, audit=audit, admit=admit
    )


@dataclass(frozen=True)
class UpdateConfirm(ReadModel):
    """The confirmation a ``census update-confirm`` recorded, or found.

    ``confirmation`` is its ``id``, ``created_at`` and the counts it binds;
    ``task`` the run task's ``id`` and ``state``.
    """

    created: bool
    request_key: UUID
    plan_id: UUID
    confirmation: dict
    task: dict


def _offered(plan, counts, *, kind):
    """Refuse a plan whose region does not offer ``kind`` now, as the page.

    ``kind`` is ``"update"`` or ``"rerun"``. Returns the page's ``Offer``.
    """
    from .jobs.models import NONTERMINAL_STATES
    from .reports import census_publication as publication
    from .reports.census_publication_views import _not_offered

    run = publication.latest_run(plan)
    offer = publication.offer(plan, counts, run=run)
    if offer.kind != kind:
        if kind == "update":
            raise _not_offered(run)
        if run is not None and run.state in NONTERMINAL_STATES:
            raise publication.RUNNING.refusal()
        raise publication.FINISHED.refusal()
    if offer.gate is not None:
        raise offer.gate.refusal()
    return offer


def update_preview(caller, service, plan_id, *, request_key):
    """What ``census update-confirm`` asks about: the newest plan and its counts.

    The page's review step: the switch, the current campaign, the shared
    newest-plan check and, unless the key already confirmed this plan (a
    repeat), what the page offers. Returns ``(counts, lines)``: the counts
    the confirmation will bind and the prompt's lines, the page's question
    and sentences. Reads only; the command's admission comes first.
    """
    from .reports import census_publication as publication
    from .responses.publication_models import PublicationConfirmation
    from .responses.publication_plans import confirmation_counts

    actor = _admit_update(caller, service.store)

    def step():
        """The page's checks before its confirmation is drawn."""
        _switched_on()
        campaign_id = _campaign()
        plan = publication.newest_plan_check(campaign_id, plan_id)
        counts = confirmation_counts(plan.pk)
        replay = PublicationConfirmation.objects.filter(
            actor_id=actor.identity, request_key=request_key, plan_id=plan.pk
        ).exists()
        if not replay:
            _offered(plan, counts, kind="update")
        return counts, _update_lines(plan, counts)

    return _held(lambda: _refusing(step))


def _update_lines(plan, counts):
    """The prompt's lines: the plan, the page's question and its sentences."""
    from .reports import census_publication as publication

    held = counts["blocked_entities"]
    lines = [
        f"Update ParishSoft from check {plan.pk}.",
        str(publication.update_question(counts)),
        "Each Member's contact record in ParishSoft is read again, and only "
        "these changes are written; nothing else changes.",
    ]
    if held:
        lines.append(f"{held:,} Member(s) are held back.")
    lines.append("This cannot be undone from here.")
    return lines


def update_confirm(caller, service, plan_id, *, counts, request_key, context):
    """``census update-confirm``: Update's Apply, after the prompt (#1755).

    In the page's command scope, the page's checks again (the switch, the
    current campaign, the newest plan, what it offers unless the key already
    confirmed it), then counts equal to those asked about (else
    ``publication_plan_changed``), the command session's sign-in
    (``sign_in_instant``, which also records the automation's fresh gate),
    and ``confirm_and_publish`` with those counts and the key. The command
    records ``admin_cmd_census_update_confirm`` once per key. A database
    refusal is worded as the page words it
    (``census_publication.confirm_refusal``); a key bound to another plan or
    other counts is ``invalid``.
    """
    from django.db import DatabaseError

    from .accounts.sessions import sign_in_instant
    from .audit.schemas import Action
    from .observability import current_correlation, emit_failure
    from .reports import census_publication as publication
    from .responses.publication_models import PublicationConfirmation
    from .responses.publication_plans import confirmation_counts
    from .responses.publication_tasks import confirm_and_publish
    from .responses.publication_worker import PublicationDisabled

    actor = _admit_update(caller, service.store)
    checked = {}

    def step():
        """The page's Apply, with the command's event."""
        _switched_on()
        campaign_id = _campaign()
        plan = checked["plan"] = publication.newest_plan_check(campaign_id, plan_id)
        replay = PublicationConfirmation.objects.filter(
            actor_id=actor.identity, request_key=request_key, plan_id=plan.pk
        ).exists()
        if not replay:
            now = confirmation_counts(plan.pk)
            _offered(plan, now, kind="update")
            if now != counts:
                raise publication.CHANGED.refusal()
        # The automation branch locks the session row and records the
        # automation_fresh_gate event and notice in this transaction.
        caller.campaign_id = campaign_id
        instant = sign_in_instant(caller, record=not replay)
        try:
            record, task = confirm_and_publish(
                service.store,
                actor.identity,
                plan.pk,
                correlation_id=current_correlation(),
                enabled=publication.enabled(),
                session_id=caller.portal_session.pk,
                authenticated_at=instant,
                request_key=request_key,
                **counts,
            )
        except PublicationDisabled:
            # Switched off between the command's check and the service's.
            raise publication.OFF.refusal() from None
        if not replay:
            _command_event(caller, actor, Action.ADMIN_CMD_CENSUS_UPDATE_CONFIRM)
        model = UpdateConfirm(
            created=not replay,
            request_key=request_key,
            plan_id=plan.pk,
            confirmation={
                "id": record.pk,
                "created_at": record.created_at,
                "write_entities": record.write_entities,
                "write_fields": record.write_fields,
                "blocked_entities": record.blocked_entities,
            },
            task=_task(task),
        )
        return model, not replay

    try:
        return _write(
            caller,
            service,
            actor,
            step,
            request_key=request_key,
            context=context,
            plan=lambda: checked.get("plan"),
        )
    except DatabaseError as error:
        if context.get("committed") or "plan" not in checked:
            raise
        # Worded once the transaction has rolled back, as the page words it.
        refusal = publication.confirm_refusal(error, checked["plan"])
        if refusal is error:
            # Not a refusal: an outage, logged as the page logs it.
            emit_failure(error, level=logging.ERROR, name_class=True)
            raise
        if isinstance(refusal, PermissionError):
            raise refusal from None
        raise _refused(refusal) from None


@dataclass(frozen=True)
class UpdateRerun(ReadModel):
    """The retry a ``census update-rerun`` queued, or found for its key."""

    created: bool
    request_key: UUID
    plan_id: UUID
    task: dict


def _rerun_replay(plan, request_key):
    """Whether ``request_key`` already retried ``plan``'s run (a repeat)."""
    from .jobs.models import TaskRun
    from .reports import census_publication as publication

    run = publication.latest_run(plan)
    return (
        run is not None
        and TaskRun.objects.filter(
            root_id=run.root_id, retry_command_id=request_key
        ).exists()
    )


def rerun_preview(caller, service, plan_id, *, request_key):
    """What ``census update-rerun`` asks about: the Members it would try.

    The page's Run again review: the switch, the current campaign, the
    newest plan and, unless the key already retried it, what the page
    offers. Returns the prompt's lines. Reads only.
    """
    from .reports import census_publication as publication
    from .responses.publication_plans import confirmation_counts

    _admit_update(caller, service.store)

    def step():
        """The page's checks before its confirmation is drawn."""
        _switched_on()
        campaign_id = _campaign()
        plan = publication.newest_plan_check(campaign_id, plan_id)
        if _rerun_replay(plan, request_key):
            return [
                f"Run again for check {plan.pk} was already asked with this "
                "request key; the same retry is returned."
            ]
        offer = _offered(plan, confirmation_counts(plan.pk), kind="rerun")
        lines = [
            f"Run again for check {plan.pk}.",
            str(publication.rerun_question(offer.unfinished)),
        ]
        if publication.past_window(plan):
            lines.append(
                "This update was confirmed more than a day ago, so nothing "
                "more is written: Run again reads these Members back from "
                "ParishSoft and closes them, and a new Check ParishSoft "
                "plans their changes again."
            )
        else:
            lines.append(
                "Each of these Members is read from ParishSoft again before "
                "anything is written, and a write that already landed is "
                "never repeated."
            )
        return lines

    return _held(lambda: _refusing(step))


def update_rerun(caller, service, plan_id, *, request_key, context):
    """``census update-rerun``: Run again's Apply, after the prompt (#1755).

    In the page's command scope, the page's checks again, then
    ``rerun_publication`` with the key as its command id; the command
    records ``admin_cmd_census_update_rerun`` once per key. Another
    Administrator's Run again, or the last Member finishing, between the
    checks and the service is worded as the page words it.
    """
    from .audit.schemas import Action
    from .observability import current_correlation
    from .reports import census_publication as publication
    from .responses.publication_plans import confirmation_counts
    from .responses.publication_tasks import rerun_publication
    from .responses.publication_worker import PublicationDisabled

    actor = _admit_update(caller, service.store)
    checked = {}

    def step():
        """The page's Run again, with the command's event."""
        _switched_on()
        campaign_id = _campaign()
        plan = checked["plan"] = publication.newest_plan_check(campaign_id, plan_id)
        replay = _rerun_replay(plan, request_key)
        if not replay:
            _offered(plan, confirmation_counts(plan.pk), kind="rerun")
        try:
            task = rerun_publication(
                service.store,
                actor.identity,
                plan.pk,
                command_id=request_key,
                correlation_id=current_correlation(),
                enabled=publication.enabled(),
            )
        except PublicationDisabled:
            raise publication.OFF.refusal() from None
        if not replay:
            _command_event(caller, actor, Action.ADMIN_CMD_CENSUS_UPDATE_RERUN)
        model = UpdateRerun(
            created=not replay,
            request_key=request_key,
            plan_id=plan.pk,
            task=_task(task),
        )
        return model, not replay

    return _write(
        caller,
        service,
        actor,
        step,
        request_key=request_key,
        context=context,
        plan=lambda: checked.get("plan"),
    )
