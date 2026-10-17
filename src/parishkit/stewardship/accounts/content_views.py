"""Admin-only content catalog, sanitized previews and exact revision requests."""

from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import BANNER_EMAILS
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.jobs.campaign_mail_values import document_parish
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import (
    PLACEHOLDERS,
    prepare_content,
    removed_markup,
    sanitize_html,
)
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
    stale_page,
    with_default_code,
)

from . import admin_navigation, campaign_wizard, schedule_local, setup_help
from .admin_editing import (
    confirm,
    direct_key,
    error_response,
    form_action,
    principal,
    sign_preview,
    summary_errors,
)
from .artwork_views import artwork_patch
from .authentication import runtime
from .campaign_views import _scope, _state
from .campaign_wizard_models import CampaignSetupReview, item_key
from .content_defaults import default_initial
from .content_forms import (
    EMAIL_LABELS,
    ContentForm,
    matches_default,
    page_slots,
    revision_patch,
    sample_banner,
    sample_render,
    stale_markup,
    text_is_generated,
)
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .receipt_note import fold, legacy_note
from .request_patch import build_candidate
from .schedule_forms import KINDS, email_usage, excerpt, schedule_order
from .sessions import authenticated_admin


def _content_campaign(state, campaign_id):
    """The campaign whose pages and emails may be edited now (``_campaign``).

    An archived current campaign is refused first with the menu entry's own
    reason (``admin_navigation.refuse_greyed``; #1255): it is closed
    history, so the refusal is permanent and must not say to try again
    later, as the held or non-current message does. It reads the same
    snapshot (or work lock) as the rest of the request, so archiving cannot
    slip in between the check and a save.
    """
    configuration, campaigns = state[0], state[1]
    campaign = next((row for row in campaigns if row.pk == campaign_id), None)
    admin_navigation.refuse_greyed("content_catalog", configuration, campaign)
    return _campaign(state, campaign_id)


def _campaign(state, campaign_id):
    """Allow content edits after structural lock, only for the current campaign."""
    configuration, campaigns, held = state[0], state[1], state[3]
    campaign = next((row for row in campaigns if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    if held or configuration.current_campaign_id != campaign.pk:
        raise UserFacingStale(
            _("This campaign's pages and emails can't be edited right now."),
            fix=_(
                "Only the current campaign's content can be edited, and not "
                "while mail delivery or other background work is running. "
                "Try again later."
            ),
        )
    return campaign


def _records(configuration, campaign_id):
    """Select only this campaign's revisions from the exact applied YAML document."""
    return [
        row
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("content", [])
        if row["values"]["campaign_id"] == str(campaign_id)
    ]


def editor_content(configuration, campaign, kind, slot, revision_id=None):
    """The editor's saved text for one slot, without the request.

    Returns ``(label, previous, note, shown)``: the slot's name, the saved
    record the editor opens (None for an empty slot or a new email), a
    retired receipt closing note (#260; the confirmation email only), and
    the values the editor shows, with that note folded in. An email of a
    mail type other than the confirmation opens only with ``revision_id``;
    without it the editor is a new, empty email. An unknown kind or slot, a
    page slot the campaign does not show, ``revision_id`` on a page, or a
    revision the campaign does not hold raises ``LookupError``. The page
    and ``pk-admin content show`` (#463) both read through here.
    """
    labels = (
        page_slots(campaign.active_configuration.values)
        if kind == "page"
        else EMAIL_LABELS
    )
    if (
        kind not in {"page", "email"}
        or slot not in labels
        or (revision_id and kind != "email")
    ):
        raise LookupError("Content slot is unavailable.")
    records = _records(configuration, campaign.pk)
    previous = next(
        (
            row
            for row in records
            if (row["values"]["kind"], row["values"]["slot"]) == (kind, slot)
            and (
                kind == "page"
                or (slot == "confirmation" and revision_id is None)
                or row["id"] == str(revision_id)
            )
        ),
        None,
    )
    if revision_id and previous is None:
        raise LookupError("Content revision is unavailable.")
    # The confirmation email opens with any retired closing note already
    # folded into its body, as receipts send it (#260).
    note = (
        legacy_note(records, campaign.pk)
        if (kind, slot) == ("email", "confirmation")
        else None
    )
    shown = previous["values"] if previous else None
    if note is not None:
        shown = fold(shown, note["values"], campaign_id=campaign.pk)
    return labels[slot], previous, note, shown


def campaign_email_usage(configuration, campaign_id):
    """The names of the saved schedules that send each email, by email ID.

    Names come from schedule_forms.email_usage, the helper the schedules'
    email lists use, so both pages name a sender the same way (#446).
    """
    # The same records schedule_reads.campaign_schedules reads; that module
    # imports this one, so it is not imported here.
    return email_usage(
        sorted(
            (
                row
                for row in configuration.active_configuration.canonical_document[
                    "sections"
                ].get("schedules", [])
                if row["values"]["campaign_id"] == str(campaign_id)
            ),
            key=schedule_order,
        )
    )


def removal_reason(configuration, campaign, previous, note):
    """Why the editor's "Remove this selected content" can't be used, or None.

    The server would refuse it (#563): with nothing saved in the slot there
    is nothing to remove, and an email a mail schedule still sends can't be
    removed until that schedule sends another (revision_patch). The editor
    shows the box disabled with this reason instead of refusing afterwards.
    A retired receipt closing note (``note``) is saved text the removal
    takes away, so it counts as something to remove.
    """
    if previous is None:
        if note is None:
            return _("Nothing is saved here yet, so there is nothing to remove.")
        return None
    names = campaign_email_usage(configuration, campaign.pk).get(previous["id"])
    if not names:
        return None
    return _(
        "Scheduled mail still sends this email (%(names)s), so it can't be "
        "removed. Choose another email for them under Dates and mail "
        "schedules first."
    ) % {"names": ", ".join(str(name) for name in names)}


def _content_state(record):
    """Catalog status of one revision: "empty", "default" (unmodified) or "edited".

    The page shows them as Empty, Default text and Edited (#1404 PR 6b
    renamed Customized), and ``pk-admin content list`` prints the same words.
    """
    if record is None:
        return "empty"
    return "default" if matches_default(record["values"]) else "edited"


def catalog_entries(configuration, campaign):
    """The Pages and emails list's rows: (pages, emails), without rendering.

    Shared with ``resave_recommended``, so go-live readiness and System
    health flag exactly the rows this list marks (#838).
    """
    records = _records(configuration, campaign.pk)
    # A retired closing note counts as part of the confirmation email (#260).
    note = legacy_note(records, campaign.pk)
    pages = []
    for slot, label in page_slots(campaign.active_configuration.values).items():
        record = next(
            (
                row
                for row in records
                if (row["values"]["kind"], row["values"]["slot"]) == ("page", slot)
            ),
            None,
        )
        pages.append(
            {
                # The slot names the page to ``pk-admin content list`` (#463).
                "slot": slot,
                "label": label,
                "url": reverse("admin:content_edit", args=["page", slot]),
                "state": _content_state(record),
                # The campaign wizard's Kept as is key, and the row's element
                # id its editor returns focus to (#1404 PR 6b).
                "item": item_key("page", slot, record and record["id"]),
                "row_id": f"content-page-{slot}",
                "resave": stale_markup(record and record["values"]),
            }
        )
    # Which saved schedules send each email, named as on Dates and mail
    # schedules (#446), so look-alike emails can be told apart and an unused
    # one can be removed.
    usage = campaign_email_usage(configuration, campaign.pk)
    emails = []
    for slot, label in EMAIL_LABELS.items():
        rows = [
            row
            for row in records
            if (row["values"]["kind"], row["values"]["slot"]) == ("email", slot)
        ]
        subjects = [row["values"]["subject"] for row in rows]
        revisions = [
            {
                "id": row["id"],
                "subject": row["values"]["subject"],
                # The start of the ID tells apart two emails of one type
                # with the same subject, as on the schedules' email list.
                "short_id": short_id,
                # How the row's actions and the Delete dialog name it.
                "name": f"{subject} ({short_id})" if short_id else subject,
                "excerpt": excerpt(values.get("text")),
                "used_by": usage.get(row["id"], []),
                # Only an email no schedule sends can be deleted (#879), and
                # the confirmation email is a single entry, never deleted.
                "deletable": slot in KINDS and not usage.get(row["id"]),
                "state": _content_state({"values": values}),
                "item": item_key("email", slot, row["id"]),
                "row_id": f"content-email-{row['id']}",
                "resave": stale_markup(values),
                "test_url": reverse("admin:campaign_mail", args=[row["id"]]),
                "url": reverse(
                    "admin:content_revision",
                    args=["email", slot, row["id"]],
                ),
            }
            for row in rows
            for subject in [row["values"]["subject"]]
            for short_id in [row["id"][:8] if subjects.count(subject) > 1 else None]
            # The confirmation email is listed with any retired closing note
            # folded in, as receipts send it (#260).
            for values in [
                fold(row["values"], note["values"], campaign_id=campaign.pk)
                if note and slot == "confirmation"
                else row["values"]
            ]
        ]
        editor = reverse("admin:content_edit", args=["email", slot])
        if note and slot == "confirmation" and not revisions:
            # Receipts send the built-in email plus the note; list that pair
            # as the confirmation email the editor opens (#260).
            folded = fold(None, note["values"], campaign_id=campaign.pk)
            revisions.append(
                {
                    "subject": folded["subject"],
                    "name": folded["subject"],
                    "deletable": False,
                    "state": _content_state({"values": folded}),
                    "item": item_key("email", slot),
                    # No saved revision: its editor returns to the heading.
                    "row_id": f"email-{slot}-title",
                    "resave": stale_markup(folded),
                    "test_url": None,
                    "url": editor,
                }
            )
        emails.append(
            {
                "slot": slot,
                "label": label,
                # Element IDs of the table's form, Delete dialog and heading
                # (where focus goes when the row that opened it is gone).
                "form_id": f"email-{slot}-actions",
                "dialog_id": f"email-{slot}-delete",
                "heading_id": f"email-{slot}-title",
                # The table offers selection and Delete selected only when
                # some row can be deleted.
                "deletable": any(revision["deletable"] for revision in revisions),
                "singleton": slot == "confirmation",
                # Only schedulable mail has schedules that send it.
                "scheduled": slot in KINDS,
                "url": editor,
                "revisions": revisions,
            }
        )
    return pages, emails


def resave_recommended(configuration, campaign):
    """The campaign's saved pages and emails that today's sanitizer would change.

    Each entry has the page or email ``label``, the email's ``subject`` (None
    for a page), the editor ``url`` and ``blocked``: an invitation or reminder
    that lost its Family code or link, so sending refuses it (#832, #838).
    """
    pages, emails = catalog_entries(configuration, campaign)
    flagged = [
        {
            "label": page["label"],
            "subject": None,
            "url": page["url"],
            "blocked": page["resave"].blocked,
        }
        for page in pages
        if page["resave"]
    ]
    flagged += [
        {
            "label": email["label"],
            "subject": revision["subject"],
            "url": revision["url"],
            "blocked": revision["resave"].blocked,
        }
        for email in emails
        for revision in email["revisions"]
        if revision["resave"]
    ]
    return flagged


def catalog_items(configuration, campaign):
    """The Kept as is keys of every item the Pages and emails list shows.

    A mark is stored only for one of these (``campaign_wizard
    .record_review``), so an item that was saved again or removed since its
    editor was drawn is refused rather than marked.
    """
    pages, emails = catalog_entries(configuration, campaign)
    return {page["item"] for page in pages} | {
        revision["item"] for email in emails for revision in email["revisions"]
    }


def _kept_items(campaign):
    """The Pages and emails items kept as is in the campaign wizard (#1404 PR 6b).

    Marks exist only for a Testing draft (their table's guard), so any other
    campaign is not read at all; the template shows them only while the
    wizard applies.
    """
    if campaign.state != "draft":
        return frozenset()
    return frozenset(
        CampaignSetupReview.objects.filter(campaign_id=campaign.pk, step="content")
        .exclude(item="")
        .values_list("item", flat=True)
    )


def _catalog(request, configuration, campaign):
    """List named page slots and independent email revisions for per-mail selection.

    The email tables are action tables (#879): their Delete dialogs post the
    applied configuration's digest to ``content_delete``, and the page
    redraws them in place from its own address once the change is applied.
    Each row says whether it was kept as is in the campaign wizard
    (``kept``), which the list shows in place of Default text (#1404 PR 6b).
    """
    pages, emails = catalog_entries(configuration, campaign)
    kept = _kept_items(campaign)
    for row in [
        *pages,
        *(revision for email in emails for revision in email["revisions"]),
    ]:
        row["kept"] = row["item"] in kept
    return render(
        request,
        "stewardship/content-catalog.html",
        {
            "campaign": campaign,
            "pages": pages,
            "emails": emails,
            "base_digest": configuration.active_configuration.digest,
            "delete_url": reverse("admin:content_delete"),
            "refresh_url": request.path,
        },
    )


def _page(
    request,
    form,
    campaign,
    label,
    *,
    status=200,
    default_url=None,
    saved=False,
    started=False,
    refusal=None,
    stale=None,
    folded=False,
    summary=False,
    keep=None,
    save_focus="",
):
    """Never insert rejected user HTML into the visual editor without sanitizing it.

    ``default_url`` offers to start an empty slot (or, when ``saved``, reset a
    configured one) from its default text; ``started`` says the form now
    holds that unsaved default. ``stale`` is the saved revision's
    stale_markup result, shown as a "re-save recommended" notice (#832).
    ``folded`` says the confirmation email shows a retired receipt closing
    note folded in (#260), which previewing the form unchanged saves.
    ``summary`` adds the form's error summary above the heading: a refused
    direct save (#1404 PR 4b) is drawn into the page the reader is on,
    which shows that summary (ui-v1.js showFieldErrors), while a refused
    Preview changes is a page load that shows the errors in the form.
    ``keep`` is the item's Kept as is key and the list row its return
    focuses (``(item, row_id)``), or None when the editor opens no listed
    item (a new email); inside the campaign wizard the action row then
    offers Keep as is and go back while the form holds no change (#1404
    PR 6b), but only on a change-gated form: one that offers saving what is
    shown as it is has something to save. ``save_focus`` is the element
    Pages and emails focuses after Save and go back (the page's row, or the
    email's mail type heading, since a saved email is a new revision with a
    row of its own).
    """
    change_gate = not (form.is_bound or started or folded or stale is not None)
    try:
        visual = sanitize_html(form["html"].value() or "")
    except ValueError:
        visual = ""
    # The editor is the first step of edit, review, apply (#196).
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/content-settings.html",
        {
            "form": form,
            "campaign": campaign,
            "label": label,
            # Names the page or email in the Admin breadcrumb trail.
            "breadcrumb_label": label,
            "visual": visual,
            "placeholders": sorted(PLACEHOLDERS),
            "default_url": default_url,
            "saved": saved,
            "started_from_default": started,
            "refusal": refusal,
            "stale": stale,
            # Preview waits for a change (#1330) unless the page offers saving
            # what is shown as it is: the unsaved default text, a saved text
            # that needs re-saving, a confirmation email with a retired
            # closing note folded in, or a refused Preview drawn with its posts.
            "change_gate": change_gate,
            "keep_item": keep and change_gate and {"item": keep[0], "focus": keep[1]},
            "save_focus": save_focus,
            # Post to the clean path: a "?start=default" GET must not carry its
            # query into the POST, which accepts no query parameters.
            "post_url": request.path,
            "errors": summary_errors(form) if summary else [],
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _keep(kind, slot, previous, note):
    """The editor's listed item as ``_page``'s ``keep``, or None (#1404 PR 6b).

    A page is always listed (empty or not); an email only once it has saved
    text, or for the confirmation email a retired closing note alone, which
    the list shows as that email. A new email has nothing to keep.
    """
    record_id = previous and previous["id"]
    if kind == "page":
        return item_key(kind, slot, record_id), f"content-page-{slot}"
    if previous is not None:
        return item_key(kind, slot, record_id), f"content-email-{record_id}"
    if note is not None:
        return item_key(kind, slot), f"email-{slot}-title"
    return None


def _shows_banner(campaign, kind, slot):
    """Whether a Family email shows the campaign banner (#248), or None.

    None means the choice does not apply: pages and Admin-only emails.
    """
    if kind != "email" or slot not in BANNER_EMAILS:
        return None
    artwork = campaign.active_configuration.values.get("artwork", {})
    return slot not in artwork.get("hide_banner", [])


def _banner_patch(campaign, form, slot):
    """The campaign update for a changed "show the banner" choice, if any."""
    current = _shows_banner(campaign, form.kind, slot)
    if current is None or form.cleaned_data.get("show_banner") == current:
        return []
    artwork = dict(campaign.active_configuration.values.get("artwork", {}))
    hidden = set(artwork.get("hide_banner", []))
    if current:
        hidden.add(slot)
    else:
        hidden.discard(slot)
    artwork["hide_banner"] = [name for name in BANNER_EMAILS if name in hidden]
    return artwork_patch(campaign, artwork)


def _preview(
    request,
    service,
    actor,
    state,
    campaign,
    form,
    label,
    previous,
    slot,
    salt,
    note,
    stale=None,
    key=None,
):
    """Sign sanitized canonical bytes and disclose every affected mail schedule.

    ``note`` is a retired receipt closing note that the confirmation email
    editor showed folded into the body (receipt_note); the same request
    removes it, so the saved email alone carries that text from then on.
    ``stale`` keeps the saved text's re-save notice on a refused preview, so
    the Admin still sees why the re-save is needed (#832). ``key`` is a
    direct save's request key inside the campaign wizard (#1404 PR 4b,
    ``admin_editing.direct_key``): the preview is signed with it and
    returned as the signed token, which ``campaign_wizard.save_step``
    confirms at once, instead of the Review content changes page. A refusal
    is the page (status 400) either way, with its error summary for a
    direct save.
    """
    configuration, fingerprint = state[0], state[-1]
    direct = key is not None
    if not form.is_valid():
        return _page(
            request, form, campaign, label, status=400, stale=stale, summary=direct
        )
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise stale_page()
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    values = form.values(campaign_id=campaign.pk, slot=slot)
    try:
        try:
            patch, affected = revision_patch(
                base.document(), campaign, previous, values
            )
            patch += _banner_patch(campaign, form, slot)
            if note is not None:
                patch.append(
                    {"operation": "remove", "section": "content", "id": note["id"]}
                )
        except UserFacingError as error:
            # A correctable refusal (removing a template that a schedule
            # still sends) is shown beside the form, which keeps its input.
            form.add_error(None, ValidationError(error.refusal.message, code="refused"))
            return _page(
                request,
                form,
                campaign,
                label,
                status=400,
                stale=stale,
                refusal=error.refusal,
                summary=direct,
            )
        if not patch:
            form.add_error(
                None, ValidationError("No content has changed.", code="unchanged")
            )
            return _page(
                request, form, campaign, label, status=400, stale=stale, summary=direct
            )
        build_candidate(base, patch, candidate_id=uuid4())
        if direct:
            # Saved at once: no Review page to draw, so no samples to render.
            token = sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=salt,
                snapshot=fingerprint,
                key=key,
            )
            if len(token) > 256_000:
                raise ValueError("Content preview is too large.")
            return token
        parish = document_parish(base.document())
        from parishkit.stewardship.jobs.receipt_preview import confirmation_block

        confirmation = form.kind == "email" and slot == "confirmation"
        campaign_values = campaign.active_configuration.values
        # "Before" is today's receipt, including any retired closing note;
        # "after" has none, because this request folds it into the email.
        before = sample_render(
            previous["values"] if previous else None,
            parish=parish,
            campaign=campaign_values,
            banner=sample_banner(campaign_values, slot) if form.kind == "email" else "",
            confirmation=confirmation,
            receipt_block=confirmation_block(base.document(), campaign.pk),
        )
        # The proposed sample follows the checkbox being previewed (#248).
        after = sample_render(
            values,
            parish=parish,
            campaign=campaign_values,
            banner=sample_banner(
                campaign_values, slot, show=form.cleaned_data.get("show_banner")
            )
            if form.kind == "email"
            else "",
            confirmation=confirmation,
        )
        token = sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint,
        )
        if len(token) > 256_000:
            raise ValueError("Content preview is too large.")
    except (ConfigError, ValueError):
        form.add_error(
            None,
            ValidationError(
                "Check the content and placeholders. Referenced email templates cannot "
                "be removed. Large content or many affected schedules may need "
                "smaller edits.",
                code="candidate_refused",
            ),
        )
        return _page(
            request, form, campaign, label, status=400, stale=stale, summary=direct
        )
    admin_navigation.place(request, flow="change", step="review")
    return render(
        request,
        "stewardship/content-preview.html",
        {
            "campaign": campaign,
            "label": label,
            "breadcrumb_label": label,
            "before": before,
            "after": after,
            "affected": _affected_rows(affected, campaign),
            "preview": token,
        },
    )


def _affected_rows(affected, campaign):
    """The schedules a content change switches, as the review lists them.

    Each row is the saved schedule with its mail type's label and, for a
    dated mailing, the UTC moment its campaign-local date and time stand
    for, which the page shows in the browser's time zone (#558); a digest
    has no one moment and is listed as recurring.
    """
    zone = campaign.active_configuration.values["timezone"]
    return [
        schedule
        | {
            "label": EMAIL_LABELS.get(schedule["values"]["kind"], ""),
            "due_at": schedule_local.instant(schedule["values"], zone, None)
            if schedule["values"].get("date")
            else None,
        }
        for schedule in affected
    ]


@require_http_methods(["GET", "HEAD", "POST"])
def content_settings(request, campaign_id, kind=None, slot=None, revision_id=None):
    """Browse and edit content without installing YAML or accessing a real Family.

    An editor's POST is Preview changes (``preview``, answered with the
    Review content changes page) or that page's Apply (``confirm``). Inside
    the campaign wizard the editor posts ``save`` instead (#1404 PR 4b): the
    same checks and signed preview, recorded at once by
    ``campaign_wizard.save_step`` once the work transaction has ended, and
    answered with the change's status, which the page follows.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        salt = (
            f"stewardship-content-preview-v1:{campaign_id}:{kind}:{slot}:{revision_id}"
        )
        # Only an editor GET may ask to start from the default text.
        start = (
            request.method != "POST"
            and kind is not None
            and filters(request.GET, allowed={"start"}) == {"start": "default"}
        )
        if (request.GET and not start) or request.FILES:
            raise ValueError("Invalid content parameters.")
        action = None
        if request.method == "POST":
            fields = set(ContentForm.base_fields) - (
                {"subject"} if kind == "page" else set()
            )
            # Only Family emails carry the campaign banner choice (#248).
            if kind == "email" and slot in BANNER_EMAILS:
                fields.add("show_banner")
            action = form_action(request.POST, preview_fields=fields, direct=True)
            if kind is None:
                raise ValueError("The catalog is read-only.")
            if action == "confirm":
                return confirm(request, service, actor, salt=salt, current_scope=_scope)
        # A direct save's key; a repeat of it is answered with its first
        # change's status before the form is checked again (#1486).
        key = direct_key(request.POST) if action == "save" else None
        if key is not None and (
            repeat := campaign_wizard.repeated_save(request, actor, key)
        ):
            repeat["Cache-Control"] = "no-store"
            return repeat
        saved = None
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state = _state(service)
            configuration, campaign = state[0], _content_campaign(state, campaign_id)
            if kind is None:
                response = _catalog(request, configuration, campaign)
            else:
                label, previous, note, shown = editor_content(
                    configuration, campaign, kind, slot, revision_id
                )
                # "?start=default" only pre-fills the form: it starts an empty
                # slot or resets a configured one, and nothing changes until
                # the Admin previews and applies it like any other edit.
                initial = (
                    default_initial(kind, slot)
                    if start
                    else (shown or {}) | {"generate_text": text_is_generated(shown)}
                ) | {"base_digest": configuration.active_configuration.digest}
                form = ContentForm(
                    request.POST if request.method == "POST" else None,
                    kind=kind,
                    slot=slot,
                    initial=initial,
                    banner=_shows_banner(campaign, kind, slot),
                    clear_unavailable=removal_reason(
                        configuration, campaign, previous, note
                    ),
                )
                # The same plain-language field help the setup wizard shows;
                # long help opens from an "i" beside the label.
                setup_help.apply(form, setup_help.CONTENT)
                response = (
                    _preview(
                        request,
                        service,
                        actor,
                        state,
                        campaign,
                        form,
                        label,
                        previous,
                        slot,
                        salt,
                        note,
                        stale_markup(shown),
                        key,
                    )
                    if request.method == "POST"
                    else _page(
                        request,
                        form,
                        campaign,
                        label,
                        default_url=None if start else request.path + "?start=default",
                        # A folded closing note is saved text too, so the
                        # default link offers a reset, not a start.
                        saved=shown is not None,
                        started=start,
                        # Only the saved text, not a default being started.
                        stale=None if start else stale_markup(shown),
                        folded=note is not None,
                        keep=_keep(kind, slot, previous, note),
                        save_focus=f"content-page-{slot}"
                        if kind == "page"
                        else f"email-{slot}-title",
                    )
                )
                if isinstance(response, str):
                    saved, response = response, None
        if saved is not None:
            response = campaign_wizard.save_step(
                request,
                service,
                actor,
                token=saved,
                salt=salt,
                current_scope=_scope,
                campaign_id=campaign_id,
            )
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Content access was revoked.")
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


def plain_text(source):
    """What the server makes of HTML source: cleaned HTML, plain text, removals.

    Request-free, so the editors' live preview (``plain_text_preview``) and
    ``pk-admin content preview --html`` (#463) answer alike: ``html`` is the
    sanitized HTML, ``text`` the plain text generated from it and
    ``removed`` the markup the sanitizer drops, in plain words. Nothing
    saves. Source the sanitizer cannot read raises ``ValueError``.
    """
    prepared = prepare_content(source)
    return {
        "html": prepared.html,
        "text": prepared.text,
        "removed": removed_markup(source),
    }


@require_POST
def plain_text_preview(request):
    """Return what the server makes of posted HTML source; nothing saves.

    One response serves both live previews in the content editors (setup and
    campaign): the sanitized HTML the visual editor redraws from, the plain
    text shown read-only while "Generate plain text from HTML" is checked, and
    a plain-language list of markup the sanitizer removed. The browser never
    renders the raw source itself. Only the HTML field (and the CSRF token)
    is accepted, from a currently authorized Administrator, and the read-only
    authorization never renews the idle session.
    """
    try:
        if (
            set(request.POST) - {"html", "csrfmiddlewaretoken"}
            or len(request.POST.getlist("html")) != 1
        ):
            raise ValueError("Only one HTML value is accepted.")
        service = runtime()
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Content access was revoked.")
        answer = plain_text(request.POST["html"])
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
    ) as error:
        return error_response(error)
    response = JsonResponse(answer)
    response["Cache-Control"] = "no-store"
    return response
