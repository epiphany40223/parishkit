"""Campaign settings' in-place review of a live campaign's end date (#912).

A live campaign's settings are locked, but its end date may still move
(``campaigns.live_end_date``). Campaign settings offers that one field, with
the page's in-place Review and Apply (#532). The review is the Dates and mail
schedules review of the same change (``schedule_changes.build_preview``),
built from the campaign's saved schedules left as they are, so both pages
(and ``pk-admin schedule preview``) validate the same candidate and sign the
same preview. When the new date would leave an invitation or Reminder outside
the campaign, the review is refused with a link to that page's combined
date-change review, where each such mailing is rescheduled or removed in the
same change.
"""

from django import forms
from django.core import signing
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.presentation import parish_date
from parishkit.stewardship.web.refusals import Refusal, UserFacingStale, stale_page

from .schedule_changes import build_preview, preview_salt
from .schedule_forms import OUTSIDE_CAMPAIGN

# What the review says about Family access and Family-facing dates.
FAMILY_ACCESS = _(
    "Family codes and links already sent keep working until the new end date."
)
CAMPAIGN_END_TEXT = _(
    "Pages and emails that show the end date use the new date from now on; "
    "emails already sent keep the date they showed."
)
BLOCKING = _(
    "Email from an affected schedule is being sent now, or its result is "
    "unknown. Review again once it has finished."
)


class LiveEndDateForm(forms.Form):
    """The one date a live campaign may still change, against the applied digest."""

    end_date = forms.DateField(
        label=_("Campaign end date"),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_(
            "The last day Families can respond, in the campaign's time zone. "
            "Choose a later day to extend the campaign or an earlier one to "
            "end it sooner."
        ),
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )


def signed_for(token, campaign_id):
    """Whether ``token`` is a schedule preview signed for this campaign.

    Campaign settings confirms two kinds of review: a draft's settings and a
    live campaign's end date, which is signed as a schedule change. This
    only picks the salt; ``confirm_intent`` checks the token in full.
    """
    try:
        signing.loads(token, salt=preview_salt(campaign_id))
    except signing.BadSignature:
        return False
    return True


def end_review(service, actor, state, campaign, form, live_at):
    """Review the posted end date; return ``admin_editing.review_region`` kwargs.

    ``state`` is ``campaign_views._state``'s and ``live_at`` the instant
    ``schedule_reads.live_end_at`` found the change open at. Returns
    ``{"review": ...}`` with the signed preview, or ``{"refusal": ...}``
    (with a ``link`` when mail must be resolved first), or ``{}`` when the
    form itself is invalid (its errors are the region's summary).
    """
    from parishkit.stewardship.admin_changes import form_data

    from .content_views import _records
    from .schedule_forms import Schedules, ScheduleWindow
    from .schedule_reads import campaign_schedules

    configuration = state[0]
    digest = configuration.active_configuration.digest
    if not form.is_valid():
        return {}
    if form.cleaned_data["base_digest"] != digest:
        raise stale_page()
    end_date = form.cleaned_data["end_date"].isoformat()
    previous = campaign.active_configuration.values
    saved = campaign_schedules(configuration, campaign.pk)
    # The schedules exactly as saved, as the Dates and mail schedules page
    # would post them with only the end date changed.
    data, _rows = form_data(
        {"window": {"end_date": end_date}, "schedules": []},
        saved,
        previous,
        editable=False,
        base_digest=digest,
        end_only=True,
    )
    window = ScheduleWindow(
        data, prefix="window", previous=previous, editable=False, live_at=live_at
    )
    schedules = Schedules(
        data,
        prefix="schedules",
        templates=_records(configuration, campaign.pk),
        campaign_id=campaign.pk,
        campaign=previous,
        previous=saved,
    )
    context = build_preview(
        service,
        actor,
        state,
        campaign,
        window,
        schedules,
        base_digest=digest,
        salt=preview_salt(campaign.pk),
    )
    if context is None:
        if stranded(schedules):
            # The date itself is fine, but saved mail no longer fits: an
            # invitation or Reminder after a shortened end. Each one is
            # resolved in the combined review, which applies them with the
            # date (the schedule forms' own errors say which).
            url = (
                reverse("admin:schedule_settings")
                + "?"
                + urlencode({"end_date": end_date})
            )
            return {
                "refusal": Refusal(
                    _(
                        "Some scheduled emails would no longer fit the "
                        "campaign, such as invitations or Reminders after the "
                        "new end date."
                    ),
                    fix=_(
                        "Change the end date together with those mailings on "
                        "Dates and mail schedules: reschedule or delete each "
                        "one there."
                    ),
                ),
                "link": {
                    "url": url,
                    "label": _("Change the end date and its mailings"),
                },
            }
        # Any other problem, with the date or a saved schedule, in its own
        # words (the window's, then each schedule's).
        return {"refusal": Refusal(" ".join(form_messages(window, schedules)))}
    if context["blocking"]:
        return {"refusal": Refusal(BLOCKING)}
    replanned = [
        {
            "label": change["label"],
            "before": _("As scheduled"),
            "after": _("Re-planned for the new dates"),
        }
        for change in context["changes"]
    ]
    return {
        "review": {
            "changes": [
                {
                    "label": _("Campaign end date"),
                    "before": parish_date(form.initial["end_date"]),
                    "after": parish_date(form.cleaned_data["end_date"]),
                },
                *replanned,
            ],
            "notes": [FAMILY_ACCESS, CAMPAIGN_END_TEXT],
            "preview": context["preview"],
        }
    }


def stranded(schedules):
    """Whether a saved mailing is refused only for falling outside the campaign."""
    return any(
        form.has_error("date", code=OUTSIDE_CAMPAIGN) for form in schedules.forms
    )


def form_messages(window, schedules):
    """Every error of the bound window and schedule forms, in their own words."""
    return [
        str(item)
        for errors in (
            *window.errors.values(),
            schedules.non_form_errors(),
            *(errors for form in schedules.forms for errors in form.errors.values()),
        )
        for item in errors
    ]


# The live check's answer once the end date can no longer change at all.
END_LOCKED = _(
    "The end date can't change now: the campaign closed or background work "
    "started. Reload this page."
)


def end_check(service, actor, state, campaign, form, live_at):
    """What keeps a typed end date from being reviewed, for the live check (#944).

    Campaign settings checks the end date as it is typed, through a no-save
    request (``campaign_views.campaign_end_date_check``), so Review changes
    stays unavailable while the date cannot be reviewed. The answer is the
    Review's own (``end_review``) with nothing recorded: ``{}`` when Review
    may go ahead, else ``{"message": ..., "link": ...}``, the words shown at
    the field and, for mail stranded outside the new window, the link to the
    combined review (whose words then stand in for the refusal's fix).
    """
    try:
        region = end_review(service, actor, state, campaign, form, live_at)
    except UserFacingStale as error:
        region = {"refusal": error.refusal}
    if "review" in region:
        return {}
    refusal, link = region.get("refusal"), region.get("link")
    if refusal is None:
        # The form itself refused the date (empty or unreadable).
        texts = [item for errors in form.errors.values() for item in errors]
    else:
        texts = [refusal.message] if link else [refusal.message, refusal.fix]
    return {"message": " ".join(str(text) for text in texts if text), "link": link}
