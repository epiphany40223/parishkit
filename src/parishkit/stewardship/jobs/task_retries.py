"""Explicit retries of failed background tasks, moved out of the Admin views.

A Background work task page offers **Retry** for a failed Family mail
preparation, daily or weekly digest work, or export file cleanup, once its
cause is fixed. The pages (``delivery_views.preparation_retry`` and
``export_views.retry_cleanup_command``) and the Admin automation command line
(``task retry``, ADM-11 PR 9a) run these same functions, so they admit, scope
and select a retry identically. Nothing here takes a request: the admission
and command scope take an ``AdminCaller`` (a page may still pass its request,
which ``authenticated_admin`` converts), and the retries take the admitted
principal.
"""

from contextlib import contextmanager

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.sessions import authenticated_admin

# The four retries a task page can offer, by the kind of work that failed.
FAMILY_PREPARATION = "family_preparation"
DAILY_DIGEST = "daily_digest"
WEEKLY_DIGEST = "weekly_digest"
EXPORT_CLEANUP = "export_cleanup"


def background_principal(caller, store, *, final=False, activity=False):
    """Admit an Administrator for delivery and background work, or refuse.

    Polling and read views never extend idle expiry (``activity`` false);
    a form records activity. ``final`` is the read-only recheck a command
    makes around its effect.
    """
    actor = authenticated_admin(caller, store=store, activity=activity, read_only=final)
    if not allows(actor, Capability.BACKGROUND_WORK):
        raise PermissionError("Delivery administration is unavailable.")
    return actor


@contextmanager
def command_scope(caller, service, actor, *, admit=background_principal):
    """Lock the live session after the work boundary and retain it through commit.

    Initial form admission is not authority for a later effect. Logout or
    revocation that wins this lock is observed before the command; expiry
    during processing is checked again and rolls back every command-side
    effect. ``admit`` is the admission to repeat (the page passes its own,
    which its tests replace). The lock is on the caller's own session row:
    the browser's, or the command session of an automation caller.
    """
    from parishkit.stewardship.accounts.models import PortalSession
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    try:
        with work_transaction():
            # Lock without rotating cookies or writing session maintenance in a
            # transaction that the domain command may subsequently roll back.
            PortalSession.objects.select_for_update().filter(
                session_id=caller.session.session_key
            ).first()
            current = admit(caller, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Delivery command identity changed.")
            yield
            current = admit(caller, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Delivery command identity changed.")
    except PermissionError:
        # Persist timeout/revocation audit or authority rotation only after the
        # effect rollback. A replacement cookie must name a committed session.
        # If maintenance itself is unavailable, deliberately report 503: no
        # effect committed and current session authority could not be established.
        admit(caller, service.store)
        raise


def retry_kind(task_type):
    """Which retry a task page offers for a task of ``task_type``, or None."""
    from parishkit.stewardship.reports.digest_retry import TASK_TYPES
    from parishkit.stewardship.reports.export_cleanup import (
        TASK_TYPE as CLEANUP_TASK_TYPE,
    )

    from .family_mail_tasks import TASK_TYPE

    if task_type == TASK_TYPE:
        return FAMILY_PREPARATION
    if task_type == CLEANUP_TASK_TYPE:
        return EXPORT_CLEANUP
    if task_type in TASK_TYPES:
        return WEEKLY_DIGEST if task_type.startswith("weekly_") else DAILY_DIGEST
    return None


def retry_preparation_task(store, actor, task_id, *, command_id, kind):
    """Retry one selected failed Family preparation or digest run.

    Runs inside ``command_scope``. ``kind`` is the route's
    (``FAMILY_PREPARATION``, ``DAILY_DIGEST`` or ``WEEKLY_DIGEST``); a task
    of another type is missing (``TaskRun.DoesNotExist``). A Family
    preparation that is no longer the chain's latest run, other than a replay
    of this command, is stale; the digest service checks that itself.
    Returns the retry's ``TaskStatus``.
    """
    from parishkit.stewardship.reports.digest_retry import TASK_TYPES, retry_digest
    from parishkit.stewardship.storage import StaleRecordError

    from .family_mail_tasks import TASK_TYPE, retry_preparation
    from .models import TaskRun

    digest = kind in {DAILY_DIGEST, WEEKLY_DIGEST}
    digest_types = tuple(
        task_type
        for task_type in TASK_TYPES
        if task_type.startswith("weekly_" if kind == WEEKLY_DIGEST else "daily_")
    )
    task = TaskRun.objects.get(
        pk=task_id, task_type__in=digest_types if digest else (TASK_TYPE,)
    )
    if not digest:
        # The digest service binds the selected run, replay and Admin.
        # Family's older service instead takes a preparation identity.
        runs = TaskRun.objects.filter(root_id=task.root_id)
        previous = runs.filter(retry_command_id=command_id).first()
        if (previous and previous.parent_id != task_id) or (
            previous is None and runs.order_by("-retry_sequence").first().pk != task_id
        ):
            raise StaleRecordError("The selected preparation is no longer current.")
    return (retry_digest if digest else retry_preparation)(
        store,
        actor.identity,
        task.pk if digest else task.domain_request_id,
        command_id=command_id,
    )


def retry_export_cleanup_task(store, actor, task_id, *, request_key):
    """Retry one selected failed export file cleanup; returns its ``TaskStatus``."""
    from parishkit.stewardship.reports.export_cleanup import TASK_TYPE, retry_cleanup

    from .models import TaskRun

    task = TaskRun.objects.get(pk=task_id, task_type=TASK_TYPE)
    return retry_cleanup(
        store,
        actor.identity,
        task.domain_request_id,
        request_key=request_key,
        run_id=task_id,
    )
