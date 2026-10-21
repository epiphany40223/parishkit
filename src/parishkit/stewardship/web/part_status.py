"""Parts an audited Admin page could not read, brought in by themselves (#1863).

UX conventions, "Pages that update themselves": a page whose view is
audited never re-reads itself on a timer. When it could not read one of its
parts (the Information request page's Contact details, the Users page's
ParishSoft Ministry leaders), the part's notice sits in a status region
(``components/part-status.html``) that ``live-status-v1.js`` polls. Its
passive status fragment says only whether a re-read would now show the
part. Once it would, the answer carries the hidden ``a[data-reread]``
follow-up link, and ``ui-v1.js`` re-reads the page's own address once,
marked automatic, as a report does for newer figures.

Readiness is keyed on the ParishSoft data generation in use: ``shown`` is
the generation the page's own read saw, and the status is ready once
another one is in use. ``retry`` (Users only) is ready at the first check
that works, for a failure no passive read can foresee; the page drops it
on its automatic re-read, so a part that stays unreadable waits for newer
data instead of being re-read once a minute.
"""

from dataclasses import dataclass

from django.urls import reverse

from parishkit.stewardship.source.snapshot_models import SourceCurrent


@dataclass(frozen=True)
class Part:
    """One page part that may be unreadable, and how its notice is drawn.

    ``name`` is the live-status region's name, ``route`` its passive status
    fragment's URL name, ``region`` the id of the page's in-place region
    the follow-up re-read redraws, ``notice`` the sentence said in place of
    the part (with its element's ``notice_id`` and ``notice_class``), and
    ``interval`` a fixed polling pace in milliseconds (None backs off as
    usual, 2 s to 10 s).
    """

    name: str
    route: str
    region: str
    notice: str
    notice_id: str
    notice_class: str = "notice"
    interval: int | None = None


def current_generation():
    """The ParishSoft data generation in use now (0 before any is promoted)."""
    return SourceCurrent.objects.values_list("generation", flat=True).first() or 0


def part_status(part, *, shown, retry=False, ready=False):
    """The part status region's context (``components/part-status.html``).

    ``part`` is a ``Part``; ``shown`` the generation the page read (or the
    status echoes); ``retry`` asks the status to answer ready at its first
    check that works. The page draws the region pending; its status answers
    it ``ready``, with the follow-up link.
    """
    query = f"?shown={shown}" + ("&retry=1" if retry else "")
    return {
        "name": part.name,
        "url": reverse(f"admin:{part.route}") + query,
        "region": part.region,
        "notice": part.notice,
        "notice_id": part.notice_id,
        "notice_class": part.notice_class,
        "interval": part.interval,
        "ready": ready,
    }


def parse_status(parameters, *, retry=False):
    """``(shown, retry)`` from a status read's query, or ``ValueError``.

    ``shown`` is a non-negative generation, required; ``retry`` is the
    single value ``1``, accepted only where the page sends it. Any other key
    or value is refused, as the pages' own query keys are.
    """
    allowed = {"shown", "retry"} if retry else {"shown"}
    if set(parameters) - allowed or any(
        len(parameters.getlist(name)) != 1 for name in parameters
    ):
        raise ValueError("Invalid part status options.")
    shown = parameters.get("shown", "")
    if not shown.isascii() or not shown.isdigit() or len(shown) > 19:
        raise ValueError("Invalid part status generation.")
    if parameters.get("retry", "1") != "1":
        raise ValueError("Invalid part status retry.")
    return int(shown), "retry" in parameters


def is_ready(shown, retry):
    """Whether a re-read would now show the part (see the module docstring)."""
    current = current_generation()
    return retry or (current > 0 and current != shown)
