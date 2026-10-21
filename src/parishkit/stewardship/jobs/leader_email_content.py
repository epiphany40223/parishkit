"""The fixed content of a Ministry leader email (#1592).

One email tells one Ministry leader user that they can sign in to the Admin
portal, with which address and Google Account, and how many requests are
open in each Ministry they lead (admin-portal specification, "Email Ministry
leaders"). It is compiled once, by the preparation Task, from the facts its
request's database guard recorded; the recipient row keeps the result, and
every attempt sends exactly that.

The wording is fixed: Pages and emails does not edit it. It names no
parishioner and carries no contact or request detail (email is not an
audited channel). The Google Account guidance is the shared wording of
``accounts.google_account_help`` (#1593), never restated here. Ministry names
are ParishSoft's, repaired for display as everywhere else
(``ministry_display_name``). The markup is the Admin report emails' closed
set (``web.report_markup``), so the private mail helper's weekly-report
validator admits it unchanged.
"""

from dataclasses import dataclass
from types import MappingProxyType

from parishkit.stewardship.accounts.google_account_help import (
    HELP_URL,
    LINK_LABEL,
    NO_ACCOUNT,
    STILL_STUCK,
    WHICH_ADDRESS,
)
from parishkit.stewardship.reports.links import report_url
from parishkit.stewardship.source.catalog_names import ministry_display_name
from parishkit.stewardship.web.content import text_html
from parishkit.stewardship.web.report_markup import STYLES, button

# The Admin portal pages the email links: its sign-in page and the Ministry
# requests page (admin-portal specification, "Address map").
SIGN_IN_PATH = "/admin/login"
REQUESTS_PATH = "/admin/reports/ministries/"
# Room for the "[TEST] " prefix within the 254-character subject limit.
MAX_SUBJECT = 247


@dataclass(frozen=True)
class LeaderMinistry:
    """One led Ministry and its open requests, as the request recorded them."""

    duid: int
    name: str
    join: int
    leave: int

    @classmethod
    def from_fact(cls, value):
        """Read one recorded Ministry, refusing anything but its closed shape."""
        if (
            type(value) is not dict
            or set(value) != {"duid", "name", "join", "leave"}
            or any(type(value[key]) is not int for key in ("duid", "join", "leave"))
            or not isinstance(value["name"], str | None)
            or value["join"] < 0
            or value["leave"] < 0
        ):
            raise ValueError("Invalid Ministry leader email fact.")
        return cls(
            value["duid"],
            ministry_display_name(value["duid"], value["name"]),
            value["join"],
            value["leave"],
        )


@dataclass(frozen=True)
class LeaderEmailFacts:
    """Everything one request's emails say, apart from each leader's address.

    ``ministries`` maps each leader address to its Ministries in the order
    the request recorded them. ``testing_recipient`` is set exactly in
    Testing, where every email goes to it instead.
    """

    parish: str
    campaign: str
    public_origin: str
    mode: str
    testing_recipient: str | None
    ministries: MappingProxyType

    def __post_init__(self):
        """Refuse a mode and Testing recipient that disagree."""
        if self.mode not in ("testing", "production") or (
            self.testing_recipient is None
        ) != (self.mode == "production"):
            raise ValueError("Invalid Ministry leader email routing.")


def recorded_ministries(recipients):
    """Each address's Ministries from a request's recorded ``recipients``."""
    if type(recipients) is not list:
        raise ValueError("Invalid Ministry leader email facts.")
    result = {}
    for item in recipients:
        if (
            type(item) is not dict
            or set(item) != {"email", "ministries"}
            or type(item["email"]) is not str
            or type(item["ministries"]) is not list
            or item["email"] in result
        ):
            raise ValueError("Invalid Ministry leader email facts.")
        result[item["email"]] = tuple(
            LeaderMinistry.from_fact(value) for value in item["ministries"]
        )
    return MappingProxyType(result)


def counts_text(ministry):
    """A Ministry's open requests to join and to leave, in plain words."""
    if not (ministry.join or ministry.leave):
        return "No open requests"
    return f"{ministry.join} asking to join · {ministry.leave} asking to leave"


def portal_urls(public_origin):
    """The sign-in and Ministry requests links on the deployment's public origin.

    ``report_url`` validates the origin (an HTTP origin with no credentials,
    path, query or fragment), so both links share its checks.
    """
    requests = report_url(public_origin, REQUESTS_PATH)
    return requests[: -len(REQUESTS_PATH)] + SIGN_IN_PATH, requests


def _link(url, label):
    """One link in the report emails' closed markup."""
    return (
        f'<a href="{text_html(url)}" style="{STYLES["link"]}" '
        f'rel="noopener noreferrer">{text_html(label)}</a>'
    )


def _paragraph(value):
    """One plain-text paragraph as escaped HTML."""
    return f"<p>{text_html(value)}</p>"


def _heading(value):
    """One section heading, styled as the report emails' headings."""
    return f'<h2 style="{STYLES["heading"]}">{text_html(value)}</h2>'


def render_leader_email(facts, address):
    """Compile one leader's email: ``(subject, html, text, routed)``.

    ``routed`` is the one address the envelope holds: the leader's own in
    Production, the Testing recipient in Testing, where the subject and
    both bodies also say who the email was meant for, as the Administrator
    digests do (background-processing specification, "Mode routing").
    """
    if not isinstance(facts, LeaderEmailFacts) or address not in facts.ministries:
        raise ValueError("Unknown Ministry leader email recipient.")
    ministries = facts.ministries[address]
    sign_in, requests = portal_urls(facts.public_origin)
    subject = (
        f"Sign in to see your Ministry requests — {' '.join(facts.parish.split())}"
    )
    if len(subject) > MAX_SUBJECT:
        subject = subject[: MAX_SUBJECT - 1].rstrip() + "…"
    intro = (
        f"You can now sign in to the {facts.parish} stewardship portal as a "
        "Ministry leader. There you can see the requests parishioners made in "
        f"the stewardship campaign “{facts.campaign}” to join or leave the "
        "Ministries you lead, and follow them up."
    )
    address_line = f"Your sign-in address is {address}, the address this email went to."
    open_line = (
        "Requests from Families to join or leave your Ministries that are "
        "still open (New or In progress) in the current campaign:"
    )
    guidance = (str(WHICH_ADDRESS), str(NO_ACCOUNT))
    html = [
        "<p>Hello,</p>",
        _paragraph(intro),
        button(sign_in, "Sign in"),
        _heading("How to sign in"),
        _paragraph(address_line),
        *(_paragraph(line) for line in guidance),
        f"<p>{_link(HELP_URL, str(LINK_LABEL))}</p>",
        _heading("Waiting in your Ministries"),
        _paragraph(open_line),
        "<ul>"
        + "".join(
            f"<li><strong>{text_html(item.name)}</strong>: "
            f"{text_html(counts_text(item))}</li>"
            for item in ministries
        )
        + "</ul>",
        f"<p>{_link(requests, 'Open Ministry requests')}</p>",
        _paragraph(str(STILL_STUCK)),
    ]
    text = [
        "Hello,",
        intro,
        f"Sign in: {sign_in}",
        "How to sign in",
        address_line,
        *guidance,
        f"{LINK_LABEL}: {HELP_URL}",
        "Waiting in your Ministries",
        open_line
        + "".join(f"\n- {item.name}: {counts_text(item)}" for item in ministries),
        f"Open Ministry requests: {requests}",
        str(STILL_STUCK),
    ]
    routed = address
    if facts.mode == "testing":
        routed = facts.testing_recipient
        description = f"TEST — sent to {routed} instead of Ministry leader {address}."
        subject = "[TEST] " + subject
        html = ["<h2>TEST</h2>", _paragraph(description), *html]
        text = [description, *text]
    return subject, "".join(html), "\n\n".join(text), routed
