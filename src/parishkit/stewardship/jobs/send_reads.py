"""Family email send reads, moved out of their Admin pages' views.

Family email progress (``send_progress_views``) and its history
(``send_history_views``) read the current campaign's sends through these
functions, and so does the Admin automation command line (ADM-11), so the
page and the command count the same way. Each read
runs in one ``read_transaction`` snapshot and takes no lock, in particular
not the global work-order lock the sending workers take, so watching a send
never slows it down. Nothing here takes a request or checks authority.
"""

from dataclasses import replace

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.tables import WINDOW_SIZES, paginate

from .ownership import database_now
from .send_history import count_row, list_sends, mark_live
from .send_progress import progress, read_send, upcoming

# Sends per history page. A campaign has a handful (the invitation and each
# reminder, in Testing and in Production), so one page usually holds them
# all. Every shown send is counted, so there is no "All": at most 100 per
# page.
PAGE_SIZE = 25
SIZES = tuple(str(size) for size in WINDOW_SIZES)


def _configuration():
    """The system configuration with its current campaign, or None if unavailable.

    Unavailable means no configuration yet, or a restore still under review.
    """
    configuration = SystemConfiguration.objects.select_related(
        "current_campaign"
    ).first()
    if configuration is None or configuration.restore_review_required:
        return None
    return configuration


def read_progress():
    """Read the current send in one read-only snapshot, without any lock.

    Returns ``campaign``, ``testing``, ``paused``, ``send`` (a
    ``send_progress.SendProgress`` or None) and ``upcoming`` (a Production
    send about to start), or None when the system is unavailable.
    """
    with read_transaction():
        configuration = _configuration()
        if configuration is None:
            return None
        campaign = configuration.current_campaign
        production = configuration.mode == "production"
        # Only Production mail can be paused; use the pause control itself,
        # since messages are held one by one as workers reach them.
        paused = bool(production and campaign and campaign.delivery_paused)
        sent = soon = None
        if campaign is not None:
            cycle = campaign.production_cycle if production else 0
            now = database_now()
            counts = read_send(campaign.pk, configuration.mode, cycle, now)
            sent = None if counts is None else progress(counts, paused=paused)
            if sent is None or not sent.active:
                soon = upcoming(campaign, configuration.mode, cycle, now)
        return {
            "campaign": campaign,
            "testing": not production,
            "paused": paused,
            "send": sent,
            "upcoming": bool(soon),
        }


def audited_count(sent):
    """The emails an audited progress view showed: the total, else those counted."""
    if sent is None:
        return 0
    return sent.done + sent.counts.remaining if sent.total is None else sent.total


def read_history(parameters):
    """List the sends and count the shown page in one lock-free snapshot.

    ``parameters`` holds the already-filtered ``page`` and ``size``. Returns
    ``campaign`` and ``table`` (a ``web.tables.TablePage`` of
    ``send_history.SendRow``), or None when the system is unavailable.
    """
    with read_transaction():
        configuration = _configuration()
        if configuration is None:
            return None
        campaign = configuration.current_campaign
        if parameters.get("size", str(PAGE_SIZE)) not in SIZES:
            raise ValueError("Unsupported table page size.")
        table = replace(
            paginate(
                list_sends(campaign.pk) if campaign else [],
                parameters,
                default=PAGE_SIZE,
            ),
            sizes=WINDOW_SIZES,
            allow_all=False,
        )
        if campaign is not None and table.rows:
            mode = configuration.mode
            # Only Production mail can be paused (see read_progress).
            paused = mode == "production" and campaign.delivery_paused
            now = database_now()
            rows = [
                count_row(campaign, sent, mode=mode, paused=paused, now=now)
                for sent in table.rows
            ]
            # Only the send the progress panel shows links to it, so read
            # the panel's choice when any shown send is in progress.
            if any(row.current and row.send.active for row in rows):
                cycle = campaign.production_cycle if mode == "production" else 0
                rows = mark_live(rows, read_send(campaign.pk, mode, cycle, now))
            table = replace(table, rows=rows)
        return {"campaign": campaign, "table": table}
