"""Approved default text for every named Family page and email template slot.

These defaults are parish-neutral: every parish fact comes from a placeholder
that is filled in from the configured Parish profile, campaign and mail
settings. They are only a starting point. The setup wizard can copy them into
empty slots in one step, and each editor can start an empty slot from its
default. Either way the text goes through the same ``ContentForm`` validation
and sanitization as text an Admin types, so a default can never bypass a
content rule.

Every email has an HTML body. Its plain-text alternative is derived from that
HTML; the shared extraction writes each link as "label: URL", which keeps the
required ``{{ family_url }}`` in both alternatives of invitations and
reminders.
"""

from dataclasses import dataclass

from parishkit.stewardship.web.content import prepare_content

# Contact sentence shared by several pages; kept identical on purpose.
_CONTACT = "the parish office at {{ parish_phone }} or email {{ parish_email }}"
# The Family form works in portrait on phones, so the help line gives no
# orientation advice.
_HELP = (
    "<p>If you need help, or your link isn’t working, contact the parish "
    "office at {{ parish_phone }} or {{ parish_email }}.</p>"
)
_ALTERNATE_ACCESS = (
    "<p>If the link above doesn’t work, go to {{ generic_family_url }} and enter "
    "your household’s code: <strong>{{ family_code }}</strong></p>"
)
_SIGNATURE = "<p>In gratitude,<br>The Stewardship Team</p>"

PAGES = {
    "welcome": (
        "<h2>Welcome to {{ parish_name }}'s {{ campaign_year }} stewardship "
        "renewal</h2>"
        "<p>Time is a limited resource. People often say that they “don’t have "
        "enough time” – but God gives us all the time we need. We must ask "
        "ourselves: “How do I choose to use my time?” Are <strong>worship"
        "</strong> and <strong>prayer</strong> part of my priorities? Please "
        "consider these opportunities and commit to them in {{ campaign_year }}:</p>"
        "<h3>Worship</h3>"
        "<ul><li>Receive the Eucharist</li>"
        "<li>Attend Mass on Sundays</li>"
        "<li>Attend Mass on Holy Days</li>"
        "<li>Watch Mass by livestream if you are homebound</li>"
        "<li>Attend daily Mass</li>"
        "<li>Go to Reconciliation</li></ul>"
        "<h3>Personal prayer</h3>"
        "<ul><li>Spend time in personal prayer</li>"
        "<li>Say grace before meals</li>"
        "<li>Study Scripture (read the Bible)</li>"
        "<li>Attend Eucharistic Adoration</li>"
        "<li>Pray the Rosary</li>"
        "<li>Pray for vocations</li>"
        "<li>Pray the Stations of the Cross</li>"
        "<li>Walk a labyrinth</li></ul>"
        "<h3>Group prayer</h3>"
        "<ul><li>Participate in a Bible study</li>"
        "<li>Pray with loved ones</li>"
        "<li>Organize or join a small prayer group</li></ul>"
    ),
    # Kept to one sentence: the sign-in page is only the code entry. Contact
    # details are on the access-denied page instead.
    "login_help": (
        "<p>Enter the Family code from your invitation email, or use the personal "
        "link in that email.</p>"
    ),
    "pre_start": (
        "<p>{{ parish_name }}’s {{ campaign_year }} stewardship renewal opens on "
        "{{ campaign_start }}. Your household will receive an email invitation "
        "with a personal link when it opens.</p>"
        f"<p>Questions? Contact {_CONTACT}.</p>"
    ),
    "post_end": (
        "<p>{{ parish_name }}’s {{ campaign_year }} stewardship renewal closed on "
        "{{ campaign_end }}. Thank you to every household that took part.</p>"
        "<p>If you still need to update your ministry involvement or financial "
        f"commitment, please contact {_CONTACT}.</p>"
    ),
    "census": (
        "<p>Please check your household’s information below for accuracy and "
        "correct anything that has changed. The parish office does its best to "
        "keep this information up to date, so please understand if you see "
        "errors.</p>"
    ),
    "member_census": (
        "<p>Please check each household member’s information below for accuracy "
        "and correct anything that has changed. The parish office does its best "
        "to keep this information up to date, so please understand if you see "
        "errors.</p>"
    ),
    # The bold labels match the Family form's Ministry controls exactly
    # (static family-v1.js): the "Current ministries" heading, each current
    # ministry's "Continuing" / "Stop participating" choice, and the "Tap (or
    # Click) here to join more ministries" disclosure, whose verb follows the
    # device. Leaving is always honored.
    "ministry": (
        "<p>Every skill, talent, and ability is a unique gift from God. Our call "
        "to serve is an invitation to discern <em>where</em> and <em>how</em> the "
        "Spirit is calling us. As our talents develop and circumstances change, "
        "we listen to how God is calling us to use our gifts. If you haven’t yet "
        "explored your spiritual gifts, consider a spiritual gifts assessment.</p>"
        "<p>Please check the information below for accuracy. The parish office "
        "does its best to keep it up to date, so please understand if you see "
        "errors.</p>"
        "<ul><li>Ministries each person takes part in are listed under "
        "<strong>Current ministries</strong>, marked <strong>Continuing</strong>. "
        "To continue in one, you don’t need to change anything.</li>"
        "<li>To stop participating in a ministry, choose "
        "<strong>Stop participating</strong>.</li>"
        "<li>To begin a new ministry, or to learn more about one, tap or click "
        "<strong>here to join more ministries</strong>, search for it, and check "
        "its box. Someone from that ministry will contact you.</li></ul>"
    ),
    "financial": (
        "<p>Treasure is the word most often associated with stewardship. Many "
        "reduce this beautiful spirituality to a “nice way to ask for money” when "
        "it is much more. Stewardship encompasses sharing all of our blessings, "
        "so our finances are included. It is not so much the gift, but our "
        "intention to give, that is important.</p>"
        "<p>Your stewardship contributions support the ministries, programs, "
        "staff, and other operating expenses of {{ parish_name }}. Making an "
        "annual commitment helps the parish plan its budget.</p>"
        "<ul><li>If you did not make a pledge last year, please consider doing so "
        "this year. A pledge is made in good faith and may be increased or "
        "decreased if your circumstances change.</li>"
        "<li>If you give cash in the collection, please consider envelopes or "
        "online giving so we can record your gifts.</li>"
        "<li>If you already give regularly, thank you! If you are able, please "
        "consider increasing your pledge by 1%, 3%, or 5%.</li></ul>"
        "<p><strong>Other ways to give:</strong> ask your employer about matching "
        "gifts. If you are 70½ or older, ask your tax advisor about a Qualified "
        "Charitable Distribution from your IRA directly to {{ parish_name }}.</p>"
        "<p><strong>Please note:</strong> the commitment you make here covers "
        "{{ financial_start }} through {{ financial_end }}.</p>"
        "<p>Thank you for your generosity!</p>"
    ),
    # Optional page between Financial and Additional information. Removing
    # this content (or leaving it empty) removes the page from the Family form.
    "closing": (
        "<h2>Protect the earth</h2>"
        "<p>The Church’s teaching on stewardship of the earth sees its goods as "
        "gifts from God, intended for the benefit of everyone. We are called to "
        "<strong>protect</strong> these gifts as stewards and trustees, not as "
        "mere consumers and users. Please consider:</p>"
        "<h3>At home and at work</h3>"
        "<ul><li>Avoid printing emails and making photocopies</li>"
        "<li>Reduce your use of electricity; turn off lights in rooms you are not "
        "using</li>"
        "<li>Reduce water use</li>"
        "<li>Recycle what you can</li>"
        "<li>Set your thermostat a little higher in summer and lower in winter</li>"
        "<li>Wash and dry full loads, and use the right water level on your "
        "washer</li>"
        "<li>Caulk and weather-strip doors and windows</li></ul>"
        "<h3>In your yard</h3>"
        "<ul><li>Leave grass clippings on the lawn</li>"
        "<li>Water early or late in the day</li></ul>"
        "<h3>On the road</h3>"
        "<ul><li>Walk, carpool, or take public transportation</li>"
        "<li>Check tire pressure and keep your car well maintained</li></ul>"
    ),
    "additional": (
        "<p>If you have any additional information that you wish to share with "
        "{{ parish_name }} that was not covered by any previous question, please "
        "enter it here.</p>"
    ),
    "review": (
        "<p>Please review your household’s answers below. You can go back to any "
        "section to make changes.</p>"
        "<p><strong>Nothing is sent to {{ parish_name }} until you select the "
        "Submit button at the bottom of this page.</strong></p>"
    ),
    "thank_you": (
        "<p>Thank you for taking the time to complete {{ parish_name }}’s "
        "electronic stewardship renewal. We appreciate your reflection in "
        "completing this for your household.</p>"
        "<h2>Thank You!</h2>"
        "<p>(You may now close this page.)</p>"
        "<p>Within a few minutes, you will receive an email confirming the "
        "successful submission of your renewal (please check your spam "
        "folder!).</p>"
    ),
    "access_denied": (
        "<p>We couldn’t open your household’s renewal with that code or link. "
        "Please check that you entered the Family code exactly as it appears in "
        "your invitation email, or use the personal link in that email.</p>"
        "<p>If you still can’t get in, contact the {{ parish_name }} parish "
        "office at {{ parish_phone }} or email {{ parish_email }}, and we’ll "
        "help.</p>"
    ),
    # Appended to every receipt email after the "confirmation" email body,
    # which already thanks the Family, so this block adds only what follows.
    "submission_confirmation": (
        f"<p>If anything needs to change, please contact {_CONTACT}.</p>"
    ),
}


@dataclass(frozen=True)
class DefaultEmail:
    """One default email: a one-line subject and its HTML body."""

    subject: str
    html: str


EMAILS = {
    "initial": DefaultEmail(
        "{{ parish_name }} {{ campaign_year }} Stewardship Renewal",
        "<p>Dear {{ family_member_names }}:</p>"
        "<p>Stewardship is acknowledging God as Creator and Giver of all gifts "
        "and living each day in gratitude and with generosity of those gifts. It "
        "is making God’s love visible by imitating Jesus. We do this when we "
        "spend time in worship and prayer, use our talents to serve our parish, "
        "share our treasure, and protect our earth.</p>"
        "<p>Welcome to {{ parish_name }}’s <strong>online</strong> Stewardship "
        "Renewal for {{ campaign_year }}. Will you help us by making stewardship "
        "a way of life?</p>"
        "<p>Even if you already attend Mass, give, and serve in ministries, please "
        "make a {{ campaign_year }} commitment. A stewardship commitment is a "
        "voluntary, annual promise, between you and God, to support our parish’s "
        "mission. It turns giving from an afterthought into an intentional act of "
        "faith, and it helps the parish plan its budget, ministries, and "
        "outreach. Commitments can be adjusted if your circumstances change.</p>"
        "<p>Please look for <strong>new</strong> ministries. They offer new ways "
        "to serve our community.</p>"
        "<p><em>Even if you choose to keep everything the same as last year, "
        "please submit your renewal so we can keep our parish records "
        # Placeholders have no conditionals, and {{ financial_start }} is empty
        # for a campaign without a financial period, so this names the
        # campaign year, which always has a value.
        "accurate.</em> The commitment you make is for {{ campaign_year }}.</p>"
        '<p><strong><a href="{{ family_url }}">Begin your household’s renewal'
        "</a></strong></p>" + _ALTERNATE_ACCESS + _SIGNATURE + _HELP,
    ),
    "reminder": DefaultEmail(
        "Reminder: {{ parish_name }} {{ campaign_year }} Stewardship Renewal",
        "<p>Dear {{ family_member_names }}:</p>"
        "<p><strong>Reminder!</strong> We have not yet received your household’s "
        "{{ campaign_year }} Stewardship Renewal. After {{ campaign_end }}, the "
        "online renewal will no longer be available. Please help us by "
        "committing to stewardship as a way of life!</p>"
        '<p><strong><a href="{{ family_url }}">Complete your household’s '
        "{{ campaign_year }} renewal"
        "</a></strong></p>" + _ALTERNATE_ACCESS + "<p><strong>On behalf of the "
        "Stewardship Team, thank you in advance for completing your "
        "{{ campaign_year }} {{ parish_name }} Stewardship Renewal!</strong></p>"
        + _SIGNATURE
        + _HELP,
    ),
    # {{ online_giving_url }} falls back to the parish website when no giving
    # page is configured, so this link always has a real target.
    "confirmation": DefaultEmail(
        "Thank you: your {{ parish_name }} stewardship renewal was received",
        "<p>Thank you for completing {{ parish_name }}’s {{ campaign_name }} for "
        "the {{ family_name }} household. This email confirms that we received "
        "your submission.</p>"
        "<p>On behalf of the Stewardship Team, we appreciate your commitment to "
        "<strong>devote time</strong> to worship and prayer, <strong>share your "
        "talents</strong>, <strong>give your treasure</strong>, and <strong>protect "
        "the earth</strong>. If you chose to use "
        "{{ parish_name }}’s online service to fulfill your {{ campaign_year }} "
        'pledge, <a href="{{ online_giving_url }}">please click here</a>.</p>'
        "<p>Thank you for your continued support of our community!</p>"
        "<p>Peace in Christ,<br>The Stewardship Team</p>",
    ),
    "daily_digest": DefaultEmail(
        "{{ campaign_name }}: daily progress report",
        "<p>Here is today’s progress report for {{ parish_name }}’s "
        "{{ campaign_name }} ({{ campaign_start }} – {{ campaign_end }}). The "
        "figures below count only submitted Family responses; Testing responses "
        "are never included. Open the protected report for full details.</p>",
    ),
    "weekly_digest": DefaultEmail(
        "{{ campaign_name }}: weekly summary",
        "<p>Here is this week’s summary for {{ parish_name }}’s "
        "{{ campaign_name }}. The figures below count only submitted Family "
        "responses. Use the link to open the protected report with full "
        "details.</p>",
    ),
    "critical_alert": DefaultEmail(
        "Action needed: {{ campaign_name }} stewardship system alert",
        "<p>The {{ parish_name }} stewardship system has detected a problem that "
        "needs attention. Details are below. Please sign in to the "
        "administration portal to review it, or contact your system "
        "administrator.</p>",
    ),
}


def email_text(html):
    """Plain-text alternative that keeps each link's target visible.

    The shared extraction already writes every link as "label: target".
    """
    return prepare_content(html).text


def default_data(kind, slot):
    """Return editor form data for one slot's default, as a browser would post it.

    Every default uses the editor's own generated plain text, which writes
    each link as "label: URL", so emails keep their links in both versions.
    """
    if kind == "page":
        return {"html": PAGES[slot], "generate_text": "on", "text": ""}
    if kind == "email":
        email = EMAILS[slot]
        return {
            "subject": email.subject,
            "html": email.html,
            "generate_text": "on",
            "text": "",
        }
    raise LookupError("Unknown content kind.")


def default_initial(kind, slot):
    """Unsaved initial editor values that start an empty slot from its default."""
    data = default_data(kind, slot)
    return {name: value for name, value in data.items() if name != "generate_text"} | {
        "generate_text": "generate_text" in data
    }
