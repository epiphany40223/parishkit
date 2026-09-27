"""Plain-language help for every initial-setup field, in one reviewable place.

The expected reader is a mid-level IT administrator, not an expert in these
back-end systems, so each entry says what the value is, where to find it, an
example, and how the system uses it. Django renders ``help_text`` under the
field and links it to the control with ``aria-describedby``. Its default
templates mark help text safe, so these strings must never contain markup or
anything derived from input.
"""

from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from .campaign_forms import MULTI_SELECT_HELP

PARISH = {
    "name": _(
        "The parish name as Families should see it, for example “St. Mary "
        "Catholic Church”. It appears on Family pages, in emails and on reports."
    ),
    "website": _(
        "The parish's public website, for example https://www.yourparish.org/. "
        "Family pages and emails link to it. Use the plain address, without a "
        "user name, “?” or “#” part."
    ),
    "timezone": _(
        "The parish's local time zone, for example America/New_York. Campaign "
        "dates and scheduled email times use it. Starting the parish data load "
        "fixes it for this setup attempt."
    ),
    "phone": _(
        "The parish office's main telephone number, shown to Families who need "
        "help. Use +1 followed by the ten-digit US number, for example "
        "+12125551234."
    ),
    "online_giving_url": _(
        "Optional. The web page where Families can give online, for example "
        "https://www.yourparish.org/give. It must start with https://. Pages "
        "and emails, such as the submission receipt, can link to it. Leave "
        "empty if the parish has no online giving page."
    ),
}

ACCESS = {
    "staff_domains": _(
        "Google Workspace domains whose accounts may sign in as Staff, one per "
        "line, for example yourparish.org. Staff can use reports and follow-up "
        "work but cannot change settings. Personal email domains such as "
        "gmail.com are not allowed. Leave empty to grant Staff access only to "
        "the individual addresses below."
    ),
    "ministry_domains": _(
        "Domains whose accounts may sign in as Ministry leaders, one per line. "
        "A Ministry leader sees only the Ministries they are assigned to, so the "
        "domain alone shows no Ministry information."
    ),
    "staff_addresses": _(
        "Individual Google account addresses that get Staff access, one per "
        "line, for example secretary@yourparish.org. Use this for people outside "
        "your domains or instead of granting a whole domain."
    ),
    "ministry_addresses": _(
        "Individual addresses that may sign in as Ministry leaders, one per line. "
        "Each person still needs a Ministry assignment before seeing anything."
    ),
    "admin_addresses": _(
        "People who get full Administrator access, one per line. Administrators "
        "can change every setting, including who has access. Your own access as "
        "the original Administrator is kept automatically. Administrator access "
        "is granted only to individual addresses, never to a whole domain."
    ),
}

MAIL = {
    "delegated_email": _(
        "A real, licensed Google Workspace user account that sends the campaign "
        "email, for example stewardship@yourparish.org, not the service "
        "account's own address. The Google Workspace service account acts as "
        "this mailbox through domain-wide delegation (scope "
        "https://mail.google.com/) and sends through Gmail. Copies of sent mail "
        "appear in this mailbox's Sent folder, and Google's limit of about 2,000 "
        "messages per day for one user applies."
    ),
    "sender": _(
        "The From address Families see. It must be the delegated mailbox itself "
        "or a “Send mail as” alias already verified in that mailbox's Gmail "
        "settings, for example stewardship@yourparish.org."
    ),
    "sender_name": _(
        "The name mail programs show next to the From address, for example "
        "St. Example Stewardship. Leave empty to use the Parish name."
    ),
    "reply_to": _(
        "Where Families' replies go. Use an address someone reads regularly, "
        "such as the parish office, for example office@yourparish.org."
    ),
}

SLACK = {
    "enabled": _(
        "Turn on to post alerts about problems that need an administrator, such "
        "as failed background work, to a Slack channel. Leave off if your parish "
        "does not use Slack."
    ),
    "channel_id": _(
        "The ID (not the name) of the Slack channel for these alerts, for example "
        "C0123456789. In Slack, open the channel, click its name, and copy the "
        "Channel ID shown at the bottom of the About tab. Invite your Slack app's "
        "bot to that channel. Leave empty when Slack is off."
    ),
}

TESTING = {
    "testing_recipient": _(
        "While the system is in Testing mode, every email it sends goes to this "
        "one address instead of to Families. That includes test emails for a "
        "chosen real Family, which contain that Family's real information. Use a "
        "staff mailbox that only trusted people can read, for example "
        "stewardship-testing@yourparish.org."
    ),
}

LOGO = {
    "logo": _(
        "A PNG, JPEG or WebP image of the parish logo, up to 5 MB. It appears in "
        "the header of Family pages and as the browser tab icon; the needed "
        "display sizes are generated automatically. A simple logo on a plain or "
        "transparent background works best."
    ),
}

CREDENTIALS = {
    "parishsoft": {
        "candidate": _(
            "The API key ParishSoft issued to your parish, usually one line of "
            "36 letters, digits and dashes; ask ParishSoft support if you do not "
            "have one. Paste it exactly. It is stored encrypted when you save and "
            "is never displayed again; enter a new one here at any time before "
            "setup finishes to replace it. The system only reads from ParishSoft."
        ),
        "organization_id": _(
            "The number ParishSoft uses to identify your parish (its "
            "organization ID), for example 1234; ParishSoft support can tell you "
            "this number. The data load first checks that the API key belongs "
            "to exactly this organization and stops if it does not, so another "
            "parish's data can never be loaded."
        ),
    },
    "google_workspace": {
        "candidate": _(
            "Paste the entire JSON key file you downloaded for the service "
            "account in Google Cloud, unmodified, from the opening { to the "
            'closing }. It must be a service-account key ("type": '
            '"service_account") that uses Google\'s standard token address '
            "https://oauth2.googleapis.com/token. Create the service account "
            "with no IAM roles. Then, in the Google Workspace Admin console "
            "under Security, Access and data control, API controls, Manage "
            "Domain Wide Delegation, authorize the service account's Client ID "
            "for the scope https://mail.google.com/. The system uses it only to "
            "send mail as the delegated mailbox you entered. It is stored "
            "encrypted and never displayed again; paste a new key here to "
            "replace it."
        ),
    },
    "slack": {
        "candidate": _(
            "Your Slack app's Bot User OAuth Token, which starts with xoxb-. Find "
            "it in the app's settings at api.slack.com under OAuth and "
            "Permissions. The app needs the chat:write permission and must be "
            "invited to the alert channel. It is stored encrypted and never "
            "displayed again; enter a new token here to replace it."
        ),
    },
}

CAMPAIGN = {
    "name": _(
        "A name Families and staff will see, for example “2027 Stewardship Renewal”."
    ),
    "year_label": _(
        "Optional. Pages and emails show this where they mention the campaign "
        "year. Leave blank to use the upcoming financial period's year (or the "
        "campaign start year if there's no financial period); fill in to "
        "override, for example 2027."
    ),
    "timezone": _("The first campaign always uses the parish time zone."),
    "start_date": _(
        "The first day Families can respond. Invitations and reminders must be "
        "scheduled between the start and end dates."
    ),
    "end_date": _("The last day Families can respond."),
    "census": _(
        "Ask Families to review and correct their household and Member details, "
        "such as addresses, telephone numbers and email addresses."
    ),
    "ministry": _(
        "Ask Members which of the Ministries included below they want to join or leave."
    ),
    "financial_enabled": _(
        "Ask Families for a pledge for the upcoming financial period, using the "
        "share options you set up on a later step."
    ),
    "ministry_duids": _(
        "The Ministries Families can choose from. They come from the parish data "
        "you loaded from ParishSoft."
    ),
    "financial_start": _(
        "The first day of the one-year period the pledge is for, for example "
        "July 1, 2026 for a July-to-June fiscal year."
    ),
    "financial_end": _(
        "The last day of that period: the day before the first anniversary of "
        "the start, for example June 30, 2027."
    ),
    "fund_duids": _(
        "The ParishSoft funds whose giving counts toward this pledge, for "
        "example the weekly offertory fund."
    ),
    "comparison_start": _(
        "The first day of an earlier one-year period to compare against, "
        "usually the year just before the upcoming period."
    ),
    "comparison_end": _(
        "The last day of the comparison period, the day before its first anniversary."
    ),
    "comparison_fund_duids": _(
        "The funds whose giving in the comparison period is shown for "
        "comparison, usually the same funds as above."
    ),
    "overlap_confirmed": _(
        "Needed only when the campaign dates and the upcoming financial period "
        "overlap. Check it to confirm that is intended."
    ),
    "additional_information": _(
        "Add a final question where Families can write anything else for the "
        "parish. Each new answer creates a follow-up item for Staff."
    ),
}

# The shared campaign form's own help for its multi-select lists says how to
# choose several items. Setup replaces that help with the text above, so the
# instructions are repeated after it.
for name in ("ministry_duids", "fund_duids", "comparison_fund_duids"):
    CAMPAIGN[name] = format_lazy("{} {}", CAMPAIGN[name], MULTI_SELECT_HELP)

WINDOW = {
    "timezone": CAMPAIGN["timezone"],
    "start_date": CAMPAIGN["start_date"],
    "end_date": CAMPAIGN["end_date"],
    "overlap_confirmed": CAMPAIGN["overlap_confirmed"],
}

SHARE = {
    "label": _(
        "The text of one choice Families see for how they will give, for "
        "example “Weekly through online giving”."
    ),
    "free_text": _("Also show a text box so the Family can add details."),
}

CONTENT = {
    "subject": _("The subject line of this email."),
    "html": _(
        "The formatted body. Use the visual editor or edit the HTML directly. "
        "Placeholders in double braces are replaced for each recipient."
    ),
    "generate_text": _(
        "Create the plain-text version automatically from the formatted body. "
        "Email programs that cannot show formatting use the plain text."
    ),
    "text": _("The plain-text version of the body."),
    "clear": _("Clear this page text or email template back to empty."),
}


def apply(form, texts, *, replace=False):
    """Set help text on the form's fields that do not already explain themselves.

    An existing, more specific help text (for example a slot-specific content
    rule) is kept unless ``replace`` says the generic shared text is superseded.
    """
    for name, text in texts.items():
        field = form.fields.get(name)
        if field is not None and (replace or not field.help_text):
            field.help_text = text
    return form
