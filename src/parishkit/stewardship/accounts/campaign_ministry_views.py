"""Changing a live campaign's Ministries through a reviewed configuration request.

A live campaign's structural settings are locked, except its Ministry
selections (#342, ``campaigns/live_ministries``). This editor offers only that
one setting while the campaign is scheduled or active. Its review counts what a
change touches, and confirming it records an ordinary configuration request
plus an audit event naming the Administrator, the selections before and after,
and the Ministries added and removed. Nothing here deletes or rewrites an
answer: a removed Ministry's requests stay in reports and follow-up, marked as
no longer in the campaign, and show unmarked again if it is added back.

Review, apply and the change's status happen on the page itself (#532,
#750), as on Campaign settings: the form is ``data-table-sync``, so an
answer fills the review region under it while the form keeps the reader's
selections and takes only its hidden version. A refusal keeps the version
those selections came from (the one posted, or the one a refused Apply was
reviewed at), so a page changed elsewhere is refused again until it is
reloaded and can never quietly propose undoing that change.
"""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.live_ministries import live_change_admitted
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.source.version_models import SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page
from parishkit.stewardship.workflows.models import MinistryRequest

from . import admin_navigation
from .admin_editing import (
    confirm,
    error_response,
    form_action,
    principal,
    receipt_base,
    requested_status,
    review_region,
    reviewed_base,
    sign_preview,
)
from .authentication import runtime
from .campaign_forms import MULTI_SELECT_HELP, SourceChoices
from .campaign_views import _catalog, _scope, _state
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-live-ministries-preview-v1"
# Request states that still need Staff follow-up, as in Ministry follow-up.
OPEN_REQUEST_STATES = ("new", "assigned", "in_progress")


class LiveMinistriesForm(forms.Form):
    """The one live-editable structural setting, against the applied digest."""

    ministry_duids = SourceChoices(
        label=_("Included Ministries"), required=False, help_text=MULTI_SELECT_HELP
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, *args, ministries=(), **kwargs):
        """Offer only server-built choices: current selections and addable ones."""
        super().__init__(*args, **kwargs)
        self.fields["ministry_duids"].choices = ministries


def _addable(choices, source, selected):
    """Keep current selections plus Ministries ParishSoft still lists.

    The settings catalog offers every locally active snapshot Ministry, even
    one marked "Not in ParishSoft data". The installer and SQL refuse to add
    such a Ministry, so the live editor never offers it.
    """
    present = (
        set()
        if source is None or source.snapshot_id is None
        else {
            str(int(row.source_key))
            for row in SnapshotMinistry.objects.filter(
                snapshot_id=source.snapshot_id
            ).select_related("payload")
            if row.payload.payload.get("catalog_present") is True
        }
    )
    keep = present | {str(duid) for duid in selected}
    return [row for row in choices if row[0] in keep]


def _live_campaign(configuration, campaigns, held, campaign_id):
    """The current campaign, if its Ministries can change now; else refuse plainly."""
    campaign = next((row for row in campaigns if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    if (
        held
        or campaign.pk != configuration.current_campaign_id
        or not live_change_admitted(campaign.state, locked=campaign.structural_locked)
        or "ministry" not in campaign.active_configuration.values["modules"]
    ):
        raise UserFacingStale(
            _("This campaign's Ministries can't be changed here right now."),
            fix=_(
                "This page changes the Ministries of the current campaign while "
                "it is scheduled or open and asks about Ministries. A draft "
                "changes them on Campaign settings; a closed campaign can't "
                "change them. Background work also holds changes until it "
                "finishes, usually within minutes."
            ),
        )
    return campaign


def impact(campaign, duids):
    """What changing these Ministries touches: open forms and retained answers.

    Every open Family form lists every offered Ministry, so the number of
    Families with a form open now is the same for each changed Ministry; each
    of them is asked to review their form before submitting. Per Ministry,
    ``answers`` counts current join and stop requests (not ones a Family later
    replaced or withdrew), and ``open`` those still awaiting Staff follow-up.
    """
    open_forms = (
        FamilyFormBaseline.objects.filter(
            family__campaign_id=campaign.pk,
            mode="live",
            state="open",
            expires_at__gt=timezone.now(),
        )
        .values("family_id")
        .distinct()
        .count()
    )
    requests = MinistryRequest.objects.filter(
        submission__campaign_id=campaign.pk,
        submission__mode="live",
        ministry_duid__in=duids,
    ).exclude(state__in=("cancelled", "superseded"))
    counts = {duid: {"answers": 0, "open": 0} for duid in duids}
    for duid, state in requests.values_list("ministry_duid", "state"):
        counts[duid]["answers"] += 1
        counts[duid]["open"] += state in OPEN_REQUEST_STATES
    return open_forms, counts


def _page(request, configuration, campaign, form, *, status=200, **region):
    """Show the live selection editor with its in-place review region.

    ``region`` is ``review_region``'s ``review``, ``receipt``, ``refusal`` or
    ``message``; the step indicator follows it through edit, review and
    apply (#196), all on this page.
    """
    step = "review" if region.get("review") else "edit"
    if region.get("receipt"):
        step = "apply"
    admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        "stewardship/campaign-ministries.html",
        {
            "configuration": configuration,
            "campaign": campaign,
            "form": form,
            **review_region("campaign_ministries", form, **region),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, state, campaign, form):
    """Review the exact change and its impact; nothing is requested yet.

    A stale page raises ``UserFacingStale``, which the caller shows in the
    review region; other refusals are the page again with their summary.
    """
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return _page(request, configuration, campaign, form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    before = sorted(campaign.active_configuration.values["ministry_duids"])
    after = form.cleaned_data["ministry_duids"]
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    if not added and not removed:
        return _page(
            request,
            configuration,
            campaign,
            form,
            status=400,
            message=_("No Ministries have changed."),
        )
    # The choices offer only the current selections and the active Ministries
    # ParishSoft still lists (_addable), so the form has already
    # refused any other addition; the installer and SQL check it again.
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {"ministry_duids": after},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    names = dict(form.fields["ministry_duids"].choices)
    open_forms, counts = impact(campaign, added + removed)

    def rows(duids):
        """One review row per changed Ministry, with its display name and counts."""
        return [
            {"duid": duid, "name": names.get(str(duid), duid), **counts[duid]}
            for duid in duids
        ]

    review = {
        # Drawn inside settings-review.html, above its Apply.
        "template": "stewardship/campaign-ministries-review.html",
        "notes": [],
        "added": rows(added),
        "removed": rows(removed),
        "open_forms": open_forms,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT,
            snapshot=fingerprint,
            extra={
                "campaign_id": str(campaign.pk),
                "before": before,
                "after": after,
                "added": added,
                "removed": removed,
            },
        ),
    }
    return _page(request, configuration, campaign, form, review=review)


def _audit(request, extra):
    """Record who asked for which change, in the request's own transaction."""
    record_action(
        Action.CAMPAIGN_MINISTRIES_REQUESTED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=request.actor_id,
        subject_id=request.pk,
        campaign_id=extra["campaign_id"],
        current_parish=True,
        context={
            "previous_ministry_duids": extra["before"],
            "ministry_duids": extra["after"],
            "added_ministry_duids": extra["added"],
            "removed_ministry_duids": extra["removed"],
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_ministries(request, campaign_id):
    """Read, review and request a live campaign's Ministry selection change.

    Review, apply and the change's status all happen on this page (#750). A
    stale page or an out-of-date preview is refused in place: the page again
    (status 409) with the explanation in its review region, as is a campaign
    whose Ministries can no longer change here (closed, or held by
    background work) when the answer is in place. The only query is
    ``request``, naming the change Apply just made.
    """
    try:
        service = runtime()
        # Reading a change's status (the page's own quiet refresh once it is
        # applied) is passive, as Change status is: it never renews idle time.
        actor = principal(
            request,
            service,
            passive=request.method != "POST" and "request" in request.GET,
        )
        action, refusal, digest = None, None, None
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields={"ministry_duids", "base_digest"},
                multiple_fields={"ministry_duids"},
            )
            if action == "confirm":
                try:
                    response = confirm(
                        request,
                        service,
                        actor,
                        salt=SALT,
                        current_scope=_scope,
                        attach=_audit,
                        in_place=True,
                    )
                    response["Cache-Control"] = "no-store"
                    return response
                except UserFacingStale as error:
                    # Drawn below with the form kept at the version the
                    # change was reviewed at, so a page changed elsewhere
                    # needs a reload before the next Review
                    # (admin_editing.reviewed_base).
                    refusal = error.refusal
                    digest = reviewed_base(request.POST, SALT)
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            receipt = (
                requested_status(request, service, actor, request.GET)
                if request.method != "POST"
                else None
            )
            if receipt is not None:
                # In place, the form keeps the reader's selections
                # (admin_editing.receipt_base); a full load uses the current
                # version, matching the current selections it draws.
                digest = receipt_base(request, receipt)
            state = _state(service)
            configuration, campaigns, source, held = state[:4]
            try:
                campaign = _live_campaign(configuration, campaigns, held, campaign_id)
            except UserFacingStale as error:
                # A plain load keeps the full refusal page. An in-place
                # answer (a Review, an Apply, the quiet follow-up after
                # Apply) explains it in the review region instead, beside
                # the change's status when there is one, so the page the
                # reader is on stays where it is. The form keeps the
                # version it was drawn at, so nothing can be reviewed.
                if request.method != "POST" and receipt is None:
                    raise
                campaign = next(row for row in campaigns if row.pk == campaign_id)
                refusal = refusal or error.refusal
            values = campaign.active_configuration.values
            ministries, _funds = _catalog(configuration, source, values)
            ministries = _addable(ministries, source, values["ministry_duids"])
            form = LiveMinistriesForm(
                request.POST if action == "preview" else None,
                initial={
                    "ministry_duids": [str(duid) for duid in values["ministry_duids"]],
                    "base_digest": configuration.active_configuration.digest
                    if digest is None
                    else digest,
                },
                ministries=ministries,
            )
            if refusal is not None:
                response = _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    status=409,
                    refusal=refusal,
                    receipt=receipt,
                )
            elif action == "preview":
                try:
                    response = _preview(request, service, actor, state, campaign, form)
                except UserFacingStale as error:
                    # The bound form keeps the version that was posted.
                    response = _page(
                        request,
                        configuration,
                        campaign,
                        form,
                        status=409,
                        refusal=error.refusal,
                    )
            else:
                response = _page(
                    request, configuration, campaign, form, receipt=receipt
                )
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
