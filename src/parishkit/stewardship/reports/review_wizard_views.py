"""Review one at a time: the review wizard's start page and run pages (#2123).

Review pipeline spec, "Review wizard". Two addresses, both under Review
work and both for the current campaign:

- ``reports/review/`` (``start``): the start page. A GET draws the
  choices: the stream (only the streams the reader's menu offers), Basic
  or Advanced filters (the stream table's own), the sort and Start. A POST
  starts a run: the stream's items are read as its table reads them, fixed
  as the run's sequence of subjects, stored, and page 1 opens (303). A
  review table's Review these one at a time posts a start too, with the
  table's view (``table``, ``review_wizard_tables``). Unfinished runs are
  never listed or offered (R52 Part W): starting a run deletes the
  reader's earlier open runs in the campaign, so a run lives only while
  its pages are in use.
- ``reports/review/<position>/?run=<id>`` (``page``): one page of a run.
  A GET draws the subject's heading and the two navigation rows; a POST is
  Previous or Next (the address names the target position), which records
  the position in its own short transaction and answers 303 with the
  page's address, or Finish, which marks the run finished and opens My
  review work. A run that was finished, or replaced by a newer start,
  says "This review is no longer open." with a link to the start page,
  never an error page.

Every request re-checks the stream page's own access (its ``_principal``),
so a role change mid-run takes effect on the next page; a run's start and
each page are audited as that stream page's view, with the count read.

A page's section is the Family hub's section for its stream, read live
through the stream's own scoped read (``review_sections``): only the run's
matched items the read still lists for this reader are shown, and the
subject is named only while one is, so a reader who lost access to a
subject's items mid-run (a Ministry leader no longer leading a Ministry)
never sees a name stored with the run (#2123).
"""

from urllib.parse import urlencode
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.functions import Now
from django.http import HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.role_refusal import signed_out
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Outcome
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.refusals import with_default_code
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tabs import PARAMETER as TAB

from .export_views import SAFE_FAILURES
from .ministries import unscoped
from .read_admission import admit_report_read
from .review_run_models import ReviewRun
from .review_streams import BY_KEY, EVERY, STREAMS
from .review_wizard import FAMILY, SORTS, WAITING, TooManyPages, progress, sequence

START_TEMPLATE = "stewardship/review-wizard-start.html"
PAGE_TEMPLATE = "stewardship/review-wizard-page.html"
# The refusals and notes the start page says in place of starting a run.
NOTHING = "Nothing matches these filters."
TOO_MANY = (
    "This selection has more than 5,000 pages. Narrow the filters, then start again."
)
# Which navigation control a move came from, so the page it opens puts focus
# on the same control (the address's ``from``, a closed word).
FROM = frozenset({"previous-top", "next-top", "previous-bottom", "next-bottom"})
MODES = ("basic", "advanced")


def _reader(request, store, *, read_only=False):
    """The signed-in Admin principal, before any stream's own check."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if principal is None:
        raise signed_out()
    return principal


def _offered(principal):
    """The streams the reader's menu offers, each with its menu item, in order.

    A stream is offered while its Review work page is in the reader's menu
    (``admin_navigation.menu``); its ``item.url`` is None while the menu
    greys it, with the menu's reason (``item.reason``).
    """
    from parishkit.stewardship.accounts.admin_context import (
        _current_campaign,
        _navigation_items,
    )

    configuration = SystemConfiguration.objects.select_related(
        "active_configuration__parish", "current_campaign__active_configuration"
    ).get()
    items = _navigation_items(
        principal,
        allows(principal, Capability.CONFIGURE),
        _current_campaign(configuration),
        configuration,
    )
    by_name = {item.name: item for item in items}
    return [
        (stream, by_name[stream.entry]) for stream in STREAMS if stream.entry in by_name
    ]


def start_url():
    """The start page's address."""
    return reverse("admin:review_wizard")


def page_url(run_id, position, came_from=None):
    """A run page's address: its position, the run and, after a move, its control."""
    url = f"{reverse('admin:review_wizard_page', args=[position])}?run={run_id}"
    return f"{url}&from={came_from}" if came_from else url


def parse_start(parameters, offered):
    """The posted start: ``(stream, mode, sort, values, stored filters)``.

    ``values`` are the stream table's own filter values (the search
    included) for its read; the stored filters keep the closed ones and the
    mode, and only whether a search was used. Anything malformed, or a
    stream the reader's menu does not offer as a link now, is a ValueError.
    """
    values = filters(
        parameters,
        allowed={"csrfmiddlewaretoken", "start", "stream", "mode", "sort", "stage"}
        | {"search", "table"}
        | {name for stream in STREAMS for name, _default in stream.closed},
    )
    for name in ("csrfmiddlewaretoken", "start", "table"):
        values.pop(name, None)
    available = {stream.key: stream for stream, item in offered if item.url}
    stream = available.get(values.pop("stream", ""))
    mode = values.pop("mode", "basic")
    sort = values.pop("sort", "name")
    if stream is None or mode not in MODES or sort not in dict(SORTS):
        raise ValueError("Invalid review wizard start.")
    allowed = {"search"}
    if mode == "advanced":
        allowed |= {"stage", *(name for name, _default in stream.closed)}
    if set(values) - allowed:
        raise ValueError("Invalid review wizard filters.")
    values.setdefault("stage", WAITING)
    if values["stage"] == Stage.HISTORY.value:
        # The wizard offers no History (R52 Part W): a History item is never
        # a page.
        raise ValueError("The review wizard has no History stage.")
    stored = {
        "mode": mode,
        "stage": values["stage"],
        **{name: values[name] for name, _default in stream.closed if name in values},
        "search_used": bool(values.get("search")),
    }
    return stream, mode, sort, values, stored


def _start_context(principal, campaign_id, offered, *, chosen=None, mode="basic"):
    """The start page's context: the streams' choices and filters.

    ``chosen`` is ``(stream key, values, sort)`` of a refused start, drawn
    again as it was posted; else the page opens on the reader's last
    stream, else the first one offered, in Basic with the default sort.
    """
    last = (
        ReviewRun.objects.filter(user_id=principal.identity, campaign_id=campaign_id)
        .order_by("-updated_at")
        .values_list("stream", flat=True)
        .first()
    )
    key, values, sort = chosen or (None, {}, "name")
    linked = [stream.key for stream, item in offered if item.url]
    if key is None:
        key = last if last in linked else (linked[0] if linked else None)
    streams = []
    for stream, item in offered:
        mine = values if stream.key == key else {}
        streams.append(
            {
                "stream": stream,
                "label": stream.label,
                "reason": item.reason,
                "available": bool(item.url),
                "stages": [
                    (WAITING, _("Waiting")),
                    *stream.stages(principal),
                    (EVERY, _("Every stage except History")),
                ],
                "stage": mine.get("stage", WAITING),
                "search": mine.get("search", ""),
                # Where the deferred items are, while that stream is offered.
                "note": stream.basic_note
                if not stream.defers_to or stream.defers_to in linked
                else "",
                # The table's own filter template, with its own context.
                "context": stream.context(principal, campaign_id, mine)
                if item.url
                else {},
            }
        )
    return {
        "streams": streams,
        "chosen": key,
        "single": len(streams) == 1,
        "mode": mode,
        "sorts": SORTS,
        "sort": sort,
        "start_url": start_url(),
    }


def _render(
    request,
    campaign_id,
    principal_check,
    context,
    *,
    template,
    status=200,
    on_close=None,
):
    """Draw ``template`` under the campaign read guard, with a fresh access check.

    ``context()`` builds the template's context inside the guard. ``status``
    is the answer's status (a refused start is a 400). ``on_close(completed)``
    runs once the guard has closed (the page view's audit).
    """

    def authorize(guard):
        """A role change while the page is produced ends it."""
        principal_check()
        admit_report_read(campaign_id)

    def content():
        """Read and render inside the guard."""
        html = render_to_string(template, context(), request=request)
        return iter((html.encode(),))

    response = campaign_response(
        request,
        [campaign_id],
        authorize=authorize,
        open_content=content,
        on_close=on_close,
    )
    if response.status_code == 503 and not response.streaming:
        return report_unavailable()
    if status != 200 and response.status_code == 200:
        # The refusal is this page with its words, safe to show as it is
        # (not the generic page the middleware draws for other 400s), with
        # the refusal log's code for its status.
        response.status_code = status
        response.stewardship_safe_error = True
        with_default_code(response)
    return response


def _start_run(request, campaign_id, store, offered):
    """Start a run from the posted choices; see the module docstring.

    The stream page's own check admits the reader; the stream's items are
    read and fixed as the sequence in one transaction with the run's insert,
    which first deletes the reader's earlier open runs in the campaign
    (unfinished runs are never offered again, R52 Part W; an open tab on
    one says it is no longer open). A selection with nothing in it, or with
    more than 5,000 pages, draws the start page again with its words and
    starts nothing, leaving the earlier runs as they are.
    """
    stream, mode, sort, values, stored = parse_start(request.POST, offered)
    principal = stream.principal(request, store)
    admit_report_read(campaign_id)
    note, status, count = None, 200, 0
    # A stream that defers items to another (Pledges' Cannot contribute
    # Families, to Limitations) leaves them out only while that one is
    # offered to this reader as a link.
    options = {}
    if stream.defers_to:
        linked = {offer.key for offer, item in offered if item.url}
        options["defer"] = stream.defers_to in linked
    with transaction.atomic():
        items = stream.read(campaign_id, values, principal, **options)
        count = len(items)
        try:
            subjects = sequence(items, grouped=stream.grouped, sort=sort)
        except TooManyPages:
            subjects, note, status = None, TOO_MANY, 400
        if subjects == []:
            note = NOTHING
        if subjects:
            ReviewRun.objects.filter(
                user_id=principal.identity, campaign_id=campaign_id, finished_at=None
            ).delete()
            run = ReviewRun.objects.create(
                user_id=principal.identity,
                campaign_id=campaign_id,
                stream=stream.key,
                filters=stored,
                sort=sort,
                subjects=[subject.stored() for subject in subjects],
            )
    _audit(stream, principal, campaign_id, count, stored)
    if note is None:
        return HttpResponseRedirect(page_url(run.pk, 1), status=303)
    return _render(
        request,
        campaign_id,
        lambda: stream.principal(request, store, read_only=True),
        lambda: (
            _start_context(
                principal,
                campaign_id,
                offered,
                chosen=(stream.key, values, sort),
                mode=mode,
            )
            | {"notice": _(note), "refused": status != 200}
        ),
        template=START_TEMPLATE,
        status=status,
    )


def _audit(stream, principal, campaign_id, count, stored, *, outcome=Outcome.SUCCEEDED):
    """Audit a read as the stream page's view; a failed audit is logged."""
    try:
        stream.audit(principal, campaign_id, outcome, count, stored)
    except (DatabaseError, StorageInvariantError) as error:
        emit_failure(error, event=Event.REPORT_AUDIT_FAILED)


def _invalid():
    """A malformed request: a 400 naming nothing it was sent."""
    return validation_response([FieldError(ErrorCode.INVALID)], status=400)


@require_http_methods(["GET", "POST"])
def start(request, campaign_id):
    """The start page; a POST starts a run (module docstring)."""
    try:
        service = runtime()
        principal = _reader(request, service.store)
        admit_report_read(campaign_id)
        offered = _offered(principal)
        if not offered:
            # No stream page in the reader's menu: nothing to review here. A
            # Ministry leader who leads no Ministry gets the same plain
            # sentence as the Ministry reports (#939, #2165), not the
            # generic sign-in denial.
            raise unscoped(principal, "Review one at a time is unavailable.")
        if request.method == "POST":
            return _start_run(request, campaign_id, service.store, offered)
        if request.GET:
            raise ValueError("The start page takes no query.")
        return _render(
            request,
            campaign_id,
            lambda: _reader(request, service.store, read_only=True),
            lambda: _start_context(principal, campaign_id, offered),
            template=START_TEMPLATE,
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except ValueError:
        return _invalid()


def _run(request, principal, campaign_id):
    """``(run, control, run id)`` the address names; the run is None once
    it is not open.

    A run of another reader or campaign reads as one that is not open, so a
    page never tells whose runs exist.
    """
    values = filters(request.GET, allowed={"run", "from"})
    if values.get("from") not in (None, *FROM):
        raise ValueError("Invalid review wizard control.")
    run_id = UUID(values.get("run", ""))
    return (
        ReviewRun.objects.filter(
            pk=run_id,
            user_id=principal.identity,
            campaign_id=campaign_id,
            finished_at=None,
        ).first(),
        values.get("from"),
        run_id,
    )


def envelope_shown(facts):
    """The envelope number the header shows: the Family's ``FamilyFacts``
    envelope when it is set and not 0, else None (ParishSoft's "no
    envelope" is often 0 rather than blank; R52 Part W)."""
    return (facts.envelope or None) if facts else None


def heading(family, subject, census, *, census_on):
    """The page's header: the Family's line and, on a Member page, the Member's.

    "Family: *name* (DUID *n*, envelope *e*)", the envelope only when the
    Family's ParishSoft record has one other than 0, and on a Member page
    "Member: *name* (DUID *n*)" (R52 Part W). ``family`` is the subject's
    ``FamilyCampaign`` and ``census`` the hub's census read
    (``hub_census.read_hub_census``) the section made, or None (then it is
    read here, with census answers when ``census_on``). A Member is named
    as the Family hub's tab names them, a New Member with the hub's badge
    and no DUID; ``tab`` is that Member's hub tab key, for the Member's
    link, or None when the hub has no tab for them. Drawn only while the
    subject is listed for the reader (``review_sections.Section.listed``).
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.snapshot_names import snapshot_family_facts
    from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED

    from .hub_census import read_hub_census

    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    facts = snapshot_family_facts(snapshot, [family.family_duid], NAME_NOT_RECORDED)
    found = facts.get(family.family_duid)
    words = {
        "family_id": family.pk,
        "family_name": found.name if found else None,
        "family_duid": family.family_duid,
        "envelope": envelope_shown(found),
        "member": None,
    }
    if subject["kind"] == FAMILY:
        return words
    entity = (subject["kind"], str(subject["key"]))
    hub = census or read_hub_census(family.campaign_id, family.pk, census=census_on)
    member = next((m for m in hub.members if tuple(m.entity) == entity), None)
    words["member"] = {
        "name": member.label if member else _("This Member"),
        "duid": int(subject["key"]) if subject["kind"] == "member" else None,
        "new": subject["kind"] == "proposed_member",
        "tab": member.key if member else None,
    }
    return words


def _page_context(request, run, position, came_from, principal, campaign_id):
    """A run page's context: the heading, the progress, the rows and the section.

    The section is read live through the stream's scoped read
    (``review_sections.read_section``); the subject's header (names, DUIDs
    and hub links) is drawn only while it lists one of the subject's items.
    The page has no footer links (R52 Part W).
    """
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.responses.census_review import campaign_today
    from parishkit.stewardship.responses.financial import FREQUENCIES

    from .financial import FREQUENCY_LABELS
    from .review_sections import read_section

    stream = BY_KEY[run.stream]
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    modules = (campaign.active_configuration.values or {}).get("modules", ())
    subject = run.subjects[position - 1]
    words = progress(
        run.subjects, position, grouped=stream.grouped, member_stream=stream.member
    )
    size = run.size
    family = FamilyCampaign.objects.filter(
        pk=subject["family"], campaign_id=campaign_id
    ).first()
    section = None
    if family is not None:
        configuration = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        section = read_section(
            stream.key,
            campaign,
            family,
            subject,
            principal,
            parish_name=configuration.active_configuration.parish.name,
            filters=run.filters,
        )
    listed = bool(section and section.listed)
    timeline = None
    if listed and allows(principal, Capability.CAMPAIGN_REPORT):
        from .timeline_views import timeline_url

        timeline = timeline_url(subject["family"])
    here = page_url(run.pk, position)
    header = (
        heading(family, subject, section.census, census_on="census" in modules)
        if listed
        else None
    )
    member_url = None
    if timeline and header and header["member"] and header["member"]["tab"]:
        # The Member's name opens their tab of the Family hub (the Timeline).
        member_url = f"{timeline}?{urlencode([(TAB, header['member']['tab'])])}"
    return {
        "run": run,
        "stream": stream,
        "subject": subject,
        "listed": listed,
        "member_page": subject["kind"] != FAMILY,
        "heading": header,
        "member_url": member_url,
        "section": section,
        "progress": words,
        # The last crumb names the stream and progress, as the browser title
        # does; its parent crumb already says Review one at a time.
        "breadcrumb_label": f"{stream.label}, {words}",
        "position": position,
        "size": size,
        "previous_url": page_url(run.pk, position - 1) if position > 1 else None,
        "next_url": page_url(run.pk, position + 1) if position < size else None,
        "here_url": here,
        # The control a move came from takes focus on the page it opened.
        "focus": {control.replace("-", "_"): control == came_from for control in FROM},
        "start_url": start_url(),
        # What the shared row dialogs' editors read, as on the hub: the
        # latest date a census Edit may give, and a pledge Edit's choices.
        "edit_today": campaign_today(campaign).isoformat(),
        "frequencies": [(key, FREQUENCY_LABELS[key]) for key in FREQUENCIES],
    }


def _move(request, run_id, position, principal, campaign_id):
    """Record Previous, Next or Finish in its own short transaction.

    Returns the run as saved, or None when it is no longer open (finished, or
    replaced by a newer start, meanwhile). Two tabs on one run are last-write-wins.
    """
    values = filters(request.POST, allowed={"csrfmiddlewaretoken", "finish", "from"})
    with transaction.atomic():
        run = (
            ReviewRun.objects.select_for_update()
            .filter(
                pk=run_id,
                user_id=principal.identity,
                campaign_id=campaign_id,
                finished_at=None,
            )
            .first()
        )
        if run is None:
            return None, False
        finish = values.get("finish") == "1"
        if not 1 <= position <= run.size:
            raise ValueError("Invalid review wizard move.")
        # Finish from whichever page posts it (one reached by the browser's
        # Back, or another tab on the same run): last write wins.
        run.position = position
        if finish:
            run.finished_at = Now()
        run.updated_at = Now()
        run.save(update_fields=["finished_at", "position", "updated_at"])
    return run, finish


@require_http_methods(["GET", "POST"])
def page(request, campaign_id, position):
    """One page of a run; a POST moves to it or finishes the run (module docstring)."""
    try:
        service = runtime()
        reader = _reader(request, service.store)
        admit_report_read(campaign_id)
        run, came_from, run_id = _run(request, reader, campaign_id)
        if run is None and request.method == "POST":
            # A move on a run closed meanwhile answers as every move does,
            # with the page's address, which then says it is not open.
            return HttpResponseRedirect(page_url(run_id, position), status=303)
        if run is None:
            return _render(
                request,
                campaign_id,
                lambda: _reader(request, service.store, read_only=True),
                lambda: {
                    "closed": True,
                    "start_url": start_url(),
                    # Not a repeat of the parent crumb, Review one at a time.
                    "breadcrumb_label": _("Closed review"),
                },
                template=PAGE_TEMPLATE,
            )
        stream = BY_KEY[run.stream]
        # The stream page's own access, re-checked on every page and move.
        principal = stream.principal(request, service.store, read_only=True)
        if request.method == "POST":
            moved, finished = _move(request, run.pk, position, principal, campaign_id)
            if finished:
                return HttpResponseRedirect(reverse("admin:index"), status=303)
            if moved is None:
                return HttpResponseRedirect(page_url(run.pk, position), status=303)
            control = request.POST.get("from")
            return HttpResponseRedirect(
                page_url(run.pk, position, control if control in FROM else None),
                status=303,
            )
        if not 1 <= position <= run.size:
            raise ValueError("No such page in this run.")
        audited = False
        # The items the page drew, set while it is drawn, for its audit.
        drawn = {"count": 0}

        def context():
            """The page's context, noting how many items it shows."""
            found = _page_context(
                request, run, position, came_from, principal, campaign_id
            )
            section = found["section"]
            drawn["count"] = section.count if found["listed"] else 0
            return found

        def finish(completed):
            """Audit the page once, as its stream page's view of the items
            it shows."""
            nonlocal audited
            if not audited:
                audited = True
                _audit(
                    stream,
                    principal,
                    campaign_id,
                    drawn["count"],
                    run.filters,
                    outcome=Outcome.SUCCEEDED if completed else Outcome.FAILED,
                )

        return _render(
            request,
            campaign_id,
            lambda: stream.principal(request, service.store, read_only=True),
            context,
            template=PAGE_TEMPLATE,
            on_close=finish,
        )
    except IntegrityError:
        # The run's guard refused the change (a position outside the run).
        return _invalid()
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except ValueError:
        return _invalid()
