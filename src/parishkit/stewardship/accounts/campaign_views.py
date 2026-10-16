"""Admin structural editing of the current campaign through exact YAML requests.

Creating another campaign is retired until the single-campaign change (#145):
the one campaign is created by Create the campaign (campaign_create_views,
#142; admin-portal spec, decision 11), the old New campaign address only
redirects here, and ``_target`` refuses a new draft for every caller.
"""

import hashlib
import json
from copy import copy
from urllib.parse import urlencode
from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import MINISTRY_LEADER_ROLES
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.domain import CampaignState
from parishkit.stewardship.campaigns.leader_roles import effective_roles, role_choices
from parishkit.stewardship.campaigns.lifecycle import structural_edit_admitted
from parishkit.stewardship.campaigns.live_end_date import LIVE_STATES
from parishkit.stewardship.campaigns.live_ministries import live_ministries_editable
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
from parishkit.stewardship.campaigns.single_campaign import creation_refused
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.source.catalog_names import (
    fund_display_name,
    ministry_display_name,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotFund, SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
    stale_page,
    unexpected_fields,
    with_default_code,
)

from . import admin_navigation, field_tips, setup_help
from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
    summary_errors,
)
from .authentication import runtime
from .campaign_family_test import chosen_family_test_url
from .campaign_forms import CampaignSettingsForm, initial_fields
from .campaign_preview import describe_changes
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-campaign-structure-preview-v1"
# The review's note when the change turns financial stewardship off.
REMOVES_SHARE_OPTIONS = _(
    "Disabling financial stewardship removes this draft's sharing options. "
    "Re-enabling it starts with the default options, not your customized "
    "labels. The previous configuration remains in retained history."
)
MULTIPLE_FIELDS = frozenset(
    {"ministry_duids", "fund_duids", "comparison_fund_duids", "ministry_leader_roles"}
)
# The settings a live campaign may still change (#1119): its end date (#912),
# Ministry leader roles (#922) and Ministry selections (#342). Every other
# setting is drawn disabled in place, and Django's disabled fields take their
# saved values whatever is posted, so the server alone decides what a Review
# can change.
LIVE_FIELDS = ("end_date", "ministry_leader_roles", "ministry_duids")
# Why a setting is locked. Each locked field carries its reason in its "i"
# tip (UX conventions, Conditional fields), never in a line of its own.
LOCKED_LIVE = _(
    "Locked: this campaign is scheduled or live in Production, so this setting "
    "can no longer change."
)
LOCKED_CLOSED = _(
    "Locked: this campaign has closed, so its settings can no longer change."
)
LOCKED_HELD = _(
    "Locked while background work or going live is under way. Background "
    "work usually finishes in minutes; going live holds it until it completes "
    "or is cancelled. Reload this page then."
)
LOCKED_OTHER = _(
    "Locked: these settings change only on the current Testing-mode draft, "
    "before it has ever been live."
)
END_HELD = _(
    "The end date can change once the background work now running finishes, "
    "usually within minutes. Reload this page then."
)
MINISTRIES_HELD = _(
    "The Ministries can change once the background work now running "
    "finishes, usually within minutes. Reload this page then."
)
# A live Ministry change is reviewed on its own. SQL restores a refused live
# Ministry addition (one that a ParishSoft refresh dropped after the review)
# only for a request whose one update changes the Ministry selections alone
# (#342), so a combined request could never be refused cleanly.
MINISTRIES_ALONE = _(
    "Change the Ministries on their own: their review shows what the change "
    "touches, and it is applied by itself."
)
MINISTRIES_ALONE_FIX = _(
    "Put the end date and leader roles back as they were and review the "
    "Ministries; once that is applied, change the rest."
)
# A live campaign's end date: the help that replaces the draft's, since the
# date may move either way (#912).
LIVE_END_HELP = _(
    "The last day Families can respond, in the campaign's time zone. Choose a "
    "later day to extend the campaign or an earlier one to end it sooner. "
    "Family codes and links keep working until the end date you choose."
)
# The refusal of a Review that changes nothing. The page's change gate keeps
# Review unavailable then, so only a request that bypasses the page script
# meets it, in the review dialog (#750).
UNCHANGED = _("No settings have changed.")


def _state(service):
    """Pin current source, campaign lifecycle and all work gates beside applied YAML.

    A later lifecycle/source change invalidates the preview even when YAML has
    not changed. The caller owns the common work lock so the read is coherent
    with source promotion, lifecycle commands, and configuration installation.
    """
    configuration = editable_configuration(service)
    campaigns = list(
        Campaign.objects.select_related("active_configuration").order_by("id")
    )
    source = SourceCurrent.objects.first()
    gates = list(
        CampaignWorkGate.objects.filter(state__in=("preparing", "running"))
        .order_by("id")
        .values_list("id", "version")
    )
    cleanup = CampaignCredentialState.objects.filter(go_live_gate=True).exists()
    scope = {
        "runtime": configuration.version,
        "campaigns": [(str(row.pk), row.version) for row in campaigns],
        "source": str(source.snapshot_id) if source else None,
        "gates": [(str(key), version) for key, version in gates],
        "cleanup": cleanup,
    }
    fingerprint = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return configuration, campaigns, source, bool(gates) or cleanup, fingerprint


def _scope(service):
    """Confirmation rechecks the same domain version under intake's owning lock."""
    configuration, _, _, _, fingerprint = _state(service)
    return configuration, fingerprint


def _catalog(configuration, source, previous):
    """Expose Ministry/fund names only from the configured tenant's current corpus."""
    selected = set(previous.get("ministry_duids", []))
    financial = previous.get("financial") or {}
    retained_funds = set(financial.get("fund_duids", [])) | set(
        financial.get("comparison_fund_duids", [])
    )

    def missing_choices(identifiers, kind, organization_id=None):
        """Retain saved IDs, labelled by name and never by DUID (#1123).

        With the configured tenant's source promoted (``organization_id``),
        a saved Ministry or fund that its current data no longer has shows
        the name it had in the latest promoted snapshot that still held it.
        That is read through the snapshot, not the version rows: versions
        are content-addressed and reused, so after a rename A, B, then A the
        newest version row would say B. Without a promoted source, no name
        is borrowed from an unavailable tenant, so the label only says it is
        unavailable.
        """
        model, display = (
            (SnapshotMinistry, ministry_display_name)
            if kind == "ministry"
            else (SnapshotFund, fund_display_name)
        )
        known = {}
        if organization_id is not None and identifiers:
            # Earliest promotion first, so each DUID keeps its latest name.
            for key, text in (
                model.objects.filter(
                    snapshot__organization_id=organization_id,
                    snapshot__promoted_at__isnull=False,
                    source_key__in=[str(duid) for duid in identifiers],
                )
                .order_by("snapshot__promoted_at")
                .values_list("source_key", "payload__canonical")
            ):
                known[int(key)] = json.loads(text).get("name")
        choices = []
        for duid in sorted(identifiers):
            if duid in known:
                label = _("%(name)s (no longer in ParishSoft; retained selection)") % {
                    "name": display(duid, known[duid])
                }
            elif kind == "ministry":
                label = _("Unavailable Ministry (retained selection)")
            else:
                label = _("Unavailable fund (retained selection)")
            choices.append((str(duid), label))
        return choices

    integrations = configuration.active_configuration.canonical_document[
        "sections"
    ].get("integrations", [])
    organization = next(
        (
            row["values"]["settings"]["organization_id"]
            for row in integrations
            if row["values"]["kind"] == "parishsoft"
        ),
        None,
    )
    if (
        source is None
        or source.snapshot_id is None
        or str(source.organization_id) != organization
    ):
        return missing_choices(selected, "ministry"), missing_choices(
            retained_funds, "fund"
        )
    catalogs = []
    for model in (SnapshotMinistry, SnapshotFund):
        choices = []
        rows = list(
            model.objects.filter(snapshot_id=source.snapshot_id).select_related(
                "payload"
            )
        )
        for row in rows:
            duid, payload = int(row.source_key), row.payload.payload
            # Every Ministry in this snapshot can be chosen: upstream Ministry
            # activity is not reliable and there is no local override (#1267).
            # A fund keeps ParishSoft's own active flag.
            active = (
                model is SnapshotMinistry or payload.get("active", True) is not False
            )
            retained = selected if model is SnapshotMinistry else retained_funds
            if active or duid in retained:
                # A blank, null or odd ParishSoft name must not break
                # campaign settings (#341).
                name = (
                    ministry_display_name
                    if model is SnapshotMinistry
                    else fund_display_name
                )(duid, payload.get("name"))
                if not active:
                    name = _("%(name)s (inactive; retained selection)") % {"name": name}
                choices.append((str(duid), name))
        retained = selected if model is SnapshotMinistry else retained_funds
        choices.extend(
            missing_choices(
                retained - {int(row.source_key) for row in rows},
                "ministry" if model is SnapshotMinistry else "fund",
                source.organization_id,
            )
        )
        choices.sort(key=lambda row: (row[1].casefold(), int(row[0])))
        catalogs.append(choices)
    return catalogs


def _target(configuration, campaigns, held, campaign_id):
    """Structural mutation uses the canonical current-campaign guards.

    ``campaign_id`` None asks for a new draft (Copy campaign passes it), which
    is refused until the single-campaign change (#145; navigation rule 10).
    """
    if campaign_id is None:
        raise creation_refused()
    campaign = next((row for row in campaigns if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    editable = (
        not held
        and campaign.pk == configuration.current_campaign_id
        and configuration.mode == "testing"
        and structural_edit_admitted(
            CampaignState(campaign.state),
            ever_active=campaign.ever_active,
            locked=campaign.structural_locked,
        )
    )
    return campaign, editable


def _leaders_editable(configuration, campaign, editable):
    """Whether a live campaign's Ministry leader roles stay open (#922).

    True for a locked current campaign that is not archived and asks about
    Ministries. A draft changes the roles with its other settings instead.
    """
    return (
        not editable
        and campaign.pk == configuration.current_campaign_id
        and campaign.state != "archived"
        and "ministry" in campaign.active_configuration.values["modules"]
    )


def _with_leader_roles(values):
    """A campaign's values with its effective leader roles filled in (#922).

    A campaign without the key uses the default roles, so a review compares
    a change with the roles really in effect, never with "Not set".
    """
    if "ministry" not in values["modules"] or MINISTRY_LEADER_ROLES in values:
        return values
    return {**values, MINISTRY_LEADER_ROLES: effective_roles(values)}


def _lock_reason(campaign, held):
    """Why this campaign's settings are locked, for each locked field's tip."""
    if campaign.structural_locked or campaign.ever_active:
        return LOCKED_LIVE if campaign.state in LIVE_STATES else LOCKED_CLOSED
    return LOCKED_HELD if held else LOCKED_OTHER


def _openness(state, campaign, editable):
    """Which settings can change now, and why some held ones are locked.

    Returns ``(open_fields, live_at, reasons)``: the names of the fields the
    form leaves enabled (every one on an editable draft; on a live campaign
    its end date while it may move, #912, its leader roles, #922, and its
    Ministry selections, #342), the instant a live end date was found open
    at (``schedule_reads.live_end_at``, else None), and the locked fields
    whose tip gives a reason of their own: a live end date or Ministry list
    that background work alone holds says it can change once that work
    finishes (#944).
    """
    from .schedule_reads import live_end_at, live_end_held

    if editable:
        return (
            frozenset(CampaignSettingsForm.base_fields) - {"base_digest"},
            None,
            {},
        )
    configuration, held = state[0], state[3]
    live_at = live_end_at(state, campaign)
    open_fields, reasons = set(), {}
    if live_at is not None:
        open_fields.add("end_date")
    elif live_end_held(state, campaign):
        reasons["end_date"] = END_HELD
    if _leaders_editable(configuration, campaign, editable):
        open_fields.add("ministry_leader_roles")
    if live_ministries_editable(campaign, configuration.current_campaign_id):
        if held:
            reasons["ministry_duids"] = MINISTRIES_HELD
        else:
            open_fields.add("ministry_duids")
    return frozenset(open_fields), live_at, reasons


def _lock(field, reason):
    """Draw ``field`` disabled where it stands, with ``reason`` in its tip.

    A disabled control is not sent, and Django's disabled field takes its
    saved value whatever is posted, so a locked setting can never change
    through this form. A field whose long help already moved into a tip
    (``field_tips``) gets the reason first in that tip; any other gets a tip
    of its own, keeping its short help visible. A field drawn by its own
    template (the overlap confirmation, shown only while the dates overlap)
    is only disabled.
    """
    field.disabled = True
    if field.template_name not in (None, field_tips.TIP_TEMPLATE):
        return
    tip = getattr(field, "tip", None)
    field_tips.add(
        field, format_lazy("{} {}", reason, tip) if tip else reason, field.help_text
    )


def _settings_form(
    configuration,
    campaign,
    data=None,
    *,
    source=None,
    open_fields=None,
    reason=None,
    reasons=None,
    live_end=False,
):
    """The campaign's settings form, drawn from its applied values.

    ``source`` is the promoted source for the Ministry and fund names.
    ``open_fields`` names the fields that can change now (all of them when
    None); every other one is locked in place with ``reason`` in its tip, or
    with its own reason from ``reasons`` (a live end date or Ministry list
    that background work holds). ``live_end`` says a live campaign's end
    date is open, whose help then says it may move either way. A live
    campaign's open Ministry list offers only what may be added now.
    """
    previous = campaign.active_configuration.values
    ministries, funds = _catalog(configuration, source, previous)
    # A draft's settings are all open; a live campaign's few are judged by
    # their own reviews (the end date's, #912).
    whole = open_fields is None or "name" in open_fields
    if not whole and "ministry_duids" in open_fields:
        # A live campaign may add only Ministries ParishSoft still lists; the
        # installer and SQL check it again (#342).
        from .campaign_ministry_changes import addable

        ministries = addable(ministries, source, previous["ministry_duids"])
    initial = initial_fields(previous, digest=configuration.active_configuration.digest)
    leader_roles = (
        effective_roles(previous) if "ministry" in previous["modules"] else []
    )
    # A draft without Ministry stewardship still offers the default roles,
    # ticked, for when it is turned on.
    shown = leader_roles or effective_roles({})
    initial["ministry_leader_roles"] = shown
    form = CampaignSettingsForm(
        data,
        initial=initial,
        previous=previous,
        ministries=ministries,
        funds=funds,
        leader_choices=role_choices(shown),
        leader_roles=shown,
        whole=whole,
    )
    # Same plain-language field help as the setup wizard (setup_help.py).
    setup_help.apply(form, setup_help.ADMIN_CAMPAIGN, replace=True)
    if open_fields is not None:
        lock_settings(
            form,
            open_fields=open_fields,
            reason=reason,
            reasons=reasons,
            live_end=live_end,
        )
    return form


def lock_settings(form, *, open_fields, reason, reasons=None, live_end=False):
    """Lock every field of ``form`` but ``open_fields``, each where it stands.

    Each locked field takes ``reason`` in its tip, or its own reason from
    ``reasons``. ``live_end`` gives a live campaign's open end date the help
    that says it may move either way (#912). The page's fixtures (browser
    tests) lock their forms the same way.
    """
    if live_end:
        field_tips.add(form.fields["end_date"], LIVE_END_HELP)
    for name, field in form.fields.items():
        if name != "base_digest" and name not in open_fields:
            _lock(field, (reasons or {}).get(name, reason))
    return form


def locked_refusal(name, reason, reasons):
    """The refusal of a change to ``name``, a setting locked now.

    ``reason`` is the locked fields' ``_lock_reason`` and ``reasons`` the
    fields' own (``_openness``). ``_refuse_locked_changes`` raises it for
    the page's Review, and ``pk-admin campaign preview`` for a document
    naming a locked setting (#463).
    """
    from .campaign_end_date import END_LOCKED

    why = (reasons or {}).get(name) or (END_LOCKED if name == "end_date" else reason)
    return UserFacingStale(
        _("Some settings you changed can no longer change now."),
        # A reason that already says to reload is not told twice.
        fix=why
        if any(
            why is said for said in (END_HELD, END_LOCKED, LOCKED_HELD, MINISTRIES_HELD)
        )
        else format_lazy("{} {}", why, _("Reload this page to start again.")),
    )


def _refuse_locked_changes(form, open_fields, reason, reasons):
    """Refuse a Review that changes a setting locked since the page loaded.

    The page is drawn with the settings open then; background work, going
    live or the campaign's closing instant can lock some of them before
    Review, without changing the configuration's version. A locked field is
    disabled, so Django would quietly keep its saved value and drop the
    reader's change. Instead, the Review is refused (409, in the dialog)
    with that setting's reason: any posted locked field whose value differs
    from the saved one, and any post from a draft's whole form (it sends the
    name, which only an open draft sends) once the draft is locked, since
    unticked boxes are not sent and their change could not be seen.
    """

    def refuse(name):
        """The refusal naming why ``name`` is locked now."""
        return locked_refusal(name, reason, reasons)

    if "name" in form.data and "name" not in open_fields:
        raise refuse("name")
    for name, field in form.fields.items():
        if name == "base_digest" or name in open_fields or name not in form.data:
            continue
        key = form.add_prefix(name)
        posted = field.widget.value_from_datadict(form.data, form.files, key)
        # A disabled field never reports a change, so a copy that is not
        # disabled compares the posted value with the saved one.
        probe = copy(field)
        probe.disabled = False
        if probe.has_changed(form.get_initial_for_field(field, name), posted):
            raise refuse(name)


def _page(
    request,
    configuration,
    campaign,
    form,
    *,
    editable,
    open_fields,
    live_at,
    status=200,
):
    """Campaign settings: every setting once, open or locked where it stands.

    ``editable`` says the whole draft can change; ``open_fields`` names the
    fields that can change now and ``live_at`` is set while a live
    campaign's end date may move, whose field is then checked as it is
    typed (#944). A Review refused for the values posted (``status`` 400)
    is this page with the form's errors and their summary, which the page
    script marks on the form it shows (UX conventions, Review dialog).
    """
    if open_fields:
        admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/campaign-settings.html",
        {
            "configuration": configuration,
            "campaign": campaign,
            "form": form,
            "editable": editable,
            "open": bool(open_fields),
            # The end-date live check compares the typed date with this
            # saved ISO date (ui-v1.js); templates never format dates.
            "end_saved": None
            if live_at is None
            else campaign.active_configuration.values["end_date"],
            "end_check": None if live_at is None else end_check_words(),
            # A live campaign's open Ministry list says what a change does.
            "live_ministries": not editable and "ministry_duids" in open_fields,
            "family_test_url": chosen_family_test_url(configuration, campaign),
            # Review posts here, and the review dialog's Apply too.
            "page_url": request.path,
            # Once a change is applied, the dialog redraws the form's region
            # from this page's own address.
            "refresh_url": request.path,
            "errors": summary_errors(form) if status == 400 else [],
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def end_check_words():
    """What the page script says about a live end date's check (#944), and
    the longest words its reserved line keeps room for (#1119)."""
    from .campaign_end_date import CHECK_FAILED, CHECKING, SIGNED_OUT, longest_answer

    return {
        "checking": CHECKING,
        "failed": CHECK_FAILED,
        "signed_out": SIGNED_OUT,
        "size": longest_answer(),
    }


# Settings whose words are a long list (every Ministry or fund named, every
# share option's label): a change of one keeps the full review's table even
# alone, since a "Field: old → new" line of two such lists is no shorter.
LONG_FIELDS = frozenset({"ministry_duids", "financial", "share_options"})


def _compact(changed):
    """Whether a change's review is a compact one (#1356, class A2).

    A change of one or two short settings (a name, the modules, a date)
    repeats only the form behind the dialog, plus each old value, so it is
    one "Field: old → new" line each. More settings, or a long one, keep the
    full review.
    """
    return len(changed) <= 2 and not LONG_FIELDS.intersection(changed)


def _review(request, review):
    """The signed review for the review dialog: a fragment, never a page.

    ``review`` holds each change (label, current and proposed), the change's
    notes and its signed ``preview``, which the dialog's Apply posts here;
    ``compact`` (``_compact``) draws the changes as one line each.
    """
    return render(
        request, "stewardship/campaign-settings-review.html", {"review": review}
    )


def _signed(actor, configuration, patch, fingerprint):
    """Sign a Campaign settings change (not a live end date) for Apply."""
    return sign_preview(
        actor=actor,
        configuration=configuration,
        patch=patch,
        salt=SALT,
        snapshot=fingerprint,
    )


def review_settings(service, actor, state, campaign, editable, opened, form, reason):
    """The page's Review of a bound settings form, without a request (#463).

    Campaign settings' Review post and ``pk-admin campaign preview`` both
    call this, so the two cannot drift. ``opened`` is ``_openness``'s
    ``(open_fields, live_at, reasons)`` and ``reason`` the locked fields'
    ``_lock_reason``. A change to a setting locked since the page loaded,
    or any change while nothing is open, is refused (``UserFacingStale``).
    Returns the review: ``kind`` (``settings``, ``end_date`` or
    ``ministries``, which confirmation it takes), ``values`` (each changed
    stored value as ``(before, after)``), the dialog's ``changes`` and
    ``notes``, a Ministries review's ``ministries``, an end date's
    ``replanned`` and the signed ``preview``. Returns None when the form now
    holds the refusal (the page answers 400 with it), or ``{"reconcile":
    values}`` when only a draft's dates change while it has mail schedules,
    which Dates and mail schedules reviews together. Nothing is requested.
    """
    open_fields, live_at, reasons = opened
    _refuse_locked_changes(form, open_fields, reason, reasons)
    if not open_fields:
        raise UserFacingStale(
            _("These campaign settings are locked."),
            fix=_(
                "They can be changed only for the current draft "
                "campaign in Testing mode, before it has ever been "
                "active, and while no background work is running. "
                "A live campaign's end date, Ministry leader roles "
                "and Ministries "
                "can still change here, while no background work is "
                "running."
            ),
        )
    if editable:
        return _draft_review(service, actor, state, campaign, form)
    return _live_review(service, actor, state, campaign, form, (open_fields, live_at))


def _answer(request, review, page):
    """Answer a Review post with ``review_settings``' result.

    ``page(status)`` draws this page again: a refusal held by the form is
    status 400. A draft's dates-only change with schedules leads to Dates
    and mail schedules' combined review, where every mailing is reconciled
    with the new dates; any other review is the dialog's fragment.
    """
    if review is None:
        return page(400)
    if "reconcile" in review:
        return HttpResponseRedirect(
            reverse("admin:schedule_settings") + "?" + urlencode(review["reconcile"])
        )
    return _review(request, review)


def _draft_review(service, actor, state, campaign, form):
    """Review a draft's change: its review, or why it is refused.

    None means the form holds the refusal. A change of only the campaign's
    dates, while it has schedules, is ``{"reconcile": ...}``: Dates and
    mail schedules' combined review, where every mailing is reconciled with
    the new dates.
    """
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return None
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    values = form.values()
    previous = _with_leader_roles(campaign.active_configuration.values)
    changed = {
        key: value for key, value in values.items() if previous.get(key) != value
    }
    if not changed:
        raise UserFacingError(UNCHANGED, fix=_("Nothing was changed."))
    window_fields = {"start_date", "end_date", "timezone"}
    if window_fields.intersection(changed) and any(
        row["values"]["campaign_id"] == str(campaign.pk)
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("schedules", [])
    ):
        if set(changed) - window_fields:
            form.add_error(
                None,
                ValidationError(
                    _(
                        "No settings have been saved. Reconcile dates and mail "
                        "schedules first using the schedule editor, then save other "
                        "draft settings."
                    ),
                    code="schedules_first",
                ),
            )
            return None
        return {"reconcile": {name: values[name] for name in sorted(window_fields)}}
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": changed,
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    try:
        build_candidate(base, patch, candidate_id=uuid4())
    except ConfigError:
        form.add_error(
            None,
            ValidationError(
                _(
                    "Check the campaign name and dates. Existing invitation and "
                    "reminder times must remain within the campaign."
                ),
                code="candidate_refused",
            ),
        )
        return None
    removes_share_options = (
        bool(previous.get("share_options")) and "financial" not in values["modules"]
    )
    return {
        "kind": "settings",
        "values": _pairs(previous, changed),
        "changes": describe_changes(
            previous,
            values,
            ministries=form.fields["ministry_duids"].choices,
            funds=form.fields["fund_duids"].choices,
        ),
        "notes": [REMOVES_SHARE_OPTIONS] if removes_share_options else [],
        "compact": _compact(changed),
        "preview": _signed(actor, configuration, patch, fingerprint),
    }


def _pairs(previous, changed):
    """Each changed stored value as ``(before, after)``, in the change's order."""
    return {key: (previous.get(key), value) for key, value in changed.items()}


def _live_review(service, actor, state, campaign, form, opened):
    """Review a live campaign's change of the settings still open (#1119).

    ``opened`` is ``(open_fields, live_at)`` from ``_openness``. The locked
    fields are disabled, so the bound form holds their saved values, and only
    the open ones are compared. A change that moves the end date is reviewed
    as the Dates and mail schedules change it is (``end_review``), with a
    leader-role change in the same campaign update, so one Apply applies
    both or neither. A leader-role change alone is one campaign update of
    ``ministry_leader_roles``, which the installer and the SQL structural
    lock both admit while live. A refusal for the date itself (passed, before
    the start, mail it would strand) is the end date's field error. A change
    of the Ministry selections is reviewed on its own
    (``_ministries_review``; ``MINISTRIES_ALONE`` says why). None means the
    form holds the refusal.
    """
    from datetime import date

    from .campaign_end_date import BLOCKING, LiveEndDateForm, end_review

    configuration, fingerprint = state[0], state[-1]
    open_fields, live_at = opened
    if not form.is_valid():
        return None
    digest = form.cleaned_data["base_digest"]
    if digest != configuration.active_configuration.digest:
        raise stale_page()
    previous = _with_leader_roles(campaign.active_configuration.values)
    values = _with_leader_roles(form.values())
    changed = {
        key: values[key]
        for key in LIVE_FIELDS
        if key in open_fields and key in values and values[key] != previous.get(key)
    }
    if not changed:
        raise UserFacingError(UNCHANGED, fix=_("Nothing was changed."))
    if "ministry_duids" in changed:
        if len(changed) > 1:
            raise UserFacingError(MINISTRIES_ALONE, fix=MINISTRIES_ALONE_FIX)
        return _ministries_review(service, actor, state, campaign, form, changed)
    if "end_date" in changed:
        saved = date.fromisoformat(previous["end_date"])
        end_form = LiveEndDateForm(
            {"end_date": changed["end_date"], "base_digest": digest},
            initial={"end_date": saved, "base_digest": digest},
        )
        region = end_review(
            service,
            actor,
            state,
            campaign,
            end_form,
            live_at,
            extra={key: value for key, value in changed.items() if key != "end_date"},
        )
        if "review" in region:
            return region["review"] | {
                "kind": "end_date",
                "values": _pairs(previous, changed),
            }
        refusal = region.get("refusal")
        if refusal is not None and refusal.message == BLOCKING:
            # Not about the date: mail is being sent now. Shown in the dialog.
            raise UserFacingError(BLOCKING)
        # The date itself is refused; its words go at the field. Stranded
        # mail names where to resolve it, which the live check links.
        texts = (
            [refusal.message, refusal.fix]
            if refusal is not None
            else [item for errors in end_form.errors.values() for item in errors]
        )
        form.add_error(
            "end_date",
            ValidationError(
                " ".join(str(text) for text in texts if text), code="end_date_refused"
            ),
        )
        return None
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": changed,
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    return {
        "kind": "settings",
        "values": _pairs(previous, changed),
        "changes": describe_changes(previous, changed),
        "notes": [],
        "compact": _compact(changed),
        "preview": _signed(actor, configuration, patch, fingerprint),
    }


def _ministries_review(service, actor, state, campaign, form, changed):
    """Review a live campaign's Ministry change and its impact (#342, #1119).

    The change is one campaign update of ``ministry_duids`` alone. Its
    choices offer only the current selections and the Ministries
    ParishSoft still lists (``campaign_ministry_changes.addable``), so the
    form has already refused any other addition; the installer and SQL check
    it again. The review lists the Ministries to add and to remove, with what
    each touches, and the Families with a form open now; the signed preview
    carries the audit companion that confirming records.
    """
    from . import campaign_ministry_changes

    configuration, fingerprint = state[0], state[-1]
    before = sorted(campaign.active_configuration.values["ministry_duids"])
    after = changed["ministry_duids"]
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
    ministries, extra = campaign_ministry_changes.review(
        campaign, dict(form.fields["ministry_duids"].choices), before, after
    )
    return {
        "kind": "ministries",
        "values": {"ministry_duids": (before, after)},
        "changes": [],
        "ministries": ministries,
        "notes": [],
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=campaign_ministry_changes.SALT,
            snapshot=fingerprint,
            extra=extra,
        ),
    }


def _confirm(request, service, actor, campaign_id):
    """Apply a reviewed change: record it and answer with its Change status.

    The preview's salt says which change it is and how it is confirmed: a
    live end date (#912) is signed as the Dates and mail schedules change it
    is, so it is confirmed as that page's own are, refusing an archived
    campaign (#1258) so that no Mail schedules review signed before
    archiving is confirmed here instead; a live Ministry change (#342)
    records its audit companion with the request; anything else is an
    ordinary Campaign settings change. The review dialog follows the
    answer's status, then redraws this page in place. A refusal (an
    out-of-date preview, settings changed elsewhere) is raised and shown
    inside the dialog.
    """
    route = confirm_route(request.POST.get("preview", ""), campaign_id)
    return confirm(request, service, actor, **route)


def confirm_route(token, campaign_id):
    """How Apply confirms ``token``: ``confirm_intent``'s salt, scope, companion.

    Campaign settings' Apply and ``pk-admin campaign confirm`` both route
    through this (#463), so a token from either channel is confirmed the
    same way. Only the salt is tried here; ``confirm_intent`` checks the
    token in full. Returns ``salt``, ``current_scope`` and ``attach`` (the
    Ministry change's audit companion, else None).
    """
    from . import campaign_ministry_changes
    from .campaign_end_date import signed_for
    from .schedule_changes import confirm_scope, preview_salt

    if signed_for(token, campaign_id):
        return {
            "salt": preview_salt(campaign_id),
            "current_scope": lambda service: confirm_scope(
                service, campaign_id, page=True
            ),
            "attach": None,
        }
    if _signed_with(token, campaign_ministry_changes.SALT):
        return {
            "salt": campaign_ministry_changes.SALT,
            "current_scope": _scope,
            "attach": campaign_ministry_changes.audit,
        }
    return {"salt": SALT, "current_scope": _scope, "attach": None}


def _signed_with(token, salt):
    """Whether ``token`` is signed with ``salt``; ``confirm`` checks it in full."""
    try:
        signing.loads(token, salt=salt)
    except signing.BadSignature:
        return False
    return True


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_settings(request, campaign_id):
    """Campaign settings: one editor, one Review changes (#1119).

    Every setting is drawn once, in the same place in every campaign state:
    open where it can change now, otherwise disabled with the reason in its
    tip. Review posts the form without a page load and answers the shared
    review dialog's fragment (UX conventions, Review dialog, #750); Apply
    posts its signed preview here and is answered with the change's status.
    No configuration is written directly.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        # The page takes no query: its address is all there is (no legacy
        # addresses, navigation rule 10).
        filters(request.GET, allowed=set())
        action = None
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields=set(CampaignSettingsForm.base_fields),
                multiple_fields=MULTIPLE_FIELDS,
            )
            if action == "confirm":
                response = _confirm(request, service, actor, campaign_id)
                response["Cache-Control"] = "no-store"
                return response
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state = _state(service)
            configuration, campaigns, source, held = state[:4]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            open_fields, live_at, reasons = _openness(state, campaign, editable)
            form = _settings_form(
                configuration,
                campaign,
                request.POST if action == "preview" else None,
                source=source,
                open_fields=open_fields,
                reason=(reason := _lock_reason(campaign, held)),
                reasons=reasons,
                live_end=live_at is not None,
            )

            def page(status=200):
                """This page, with the form as it is now."""
                return _page(
                    request,
                    configuration,
                    campaign,
                    form,
                    editable=editable,
                    open_fields=open_fields,
                    live_at=live_at,
                    status=status,
                )

            if action is None:
                response = page()
            else:
                review = review_settings(
                    service,
                    actor,
                    state,
                    campaign,
                    editable,
                    (open_fields, live_at, reasons),
                    form,
                    reason,
                )
                response = _answer(request, review, page)
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


@require_http_methods(["POST"])
def campaign_end_date_check(request, campaign_id):
    """A live campaign's end-date live check (#944): what blocks its Review.

    Campaign settings posts its form here each time a live campaign's end
    date changes (ui-v1.js, ``form[data-live-check]``). Only the end date
    and the page's version are read; the date is checked exactly as a
    Review would check it (``campaign_end_date.end_check``), including mail
    the new date would strand, and the answer is the message the page shows
    at the field, or none. It saves nothing, records no request or audit
    entry and is never cached; it needs the page's own Configure capability,
    and only the page's own fields may be posted. The page's Review is still
    checked in full on its own post.
    """
    from datetime import date

    from .campaign_end_date import END_LOCKED, LiveEndDateForm, end_check
    from .schedule_reads import live_end_at

    try:
        service = runtime()
        actor = principal(request, service)
        allowed = {"action", "csrfmiddlewaretoken", *CampaignSettingsForm.base_fields}
        if (
            request.GET
            or not set(request.POST) <= allowed
            or any(
                len(values) != 1 and name not in MULTIPLE_FIELDS
                for name, values in request.POST.lists()
            )
        ):
            raise unexpected_fields(
                request.GET, request.POST, allowed=allowed, multiple=MULTIPLE_FIELDS
            )
        with read_transaction():
            state = _state(service)
            configuration, campaigns, _source, held = state[:4]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            live_at = None if editable else live_end_at(state, campaign)
            if live_at is None:
                answer = {"message": END_LOCKED}
            else:
                end_form = LiveEndDateForm(
                    {
                        "end_date": request.POST.get("end_date", ""),
                        "base_digest": request.POST.get("base_digest", ""),
                    },
                    initial={
                        "end_date": date.fromisoformat(
                            campaign.active_configuration.values["end_date"]
                        ),
                    },
                )
                answer = end_check(service, actor, state, campaign, end_form, live_at)
        response = render(request, "stewardship/campaign-end-check.html", answer)
        # As the page does: a revocation while checking is not answered.
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
    ) as error:
        return error_response(error)
