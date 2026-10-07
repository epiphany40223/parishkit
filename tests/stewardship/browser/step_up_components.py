"""Render every page's fresh-authentication prompt in LOCAL and elsewhere (#619).

Each page puts its own sentences around the shared step-up component
(``components/reauthenticate.html``). LOCAL has no Google sign-in, so there
neither the sentences nor the component may mention Google; every other
profile keeps the Production wording unchanged. The template tests and the
browser test share these pages so both check the same renders.
"""

from html.parser import HTMLParser
from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

NEXT = "/admin/step-up-return"
# Page name -> (template, its stale-sign-in context, Production sentences).
# The sentences are the exact Production wording the prompt must keep outside
# LOCAL; LOCAL renders none of them.
PAGES = {
    "error": (
        "stewardship/error.html",
        {
            "admin": True,
            "title": "Confirm it's you",
            "reauthenticate": True,
            "next": NEXT,
            "submitted": True,
            "inputs_kept": True,
            "home": "/admin/",
            "home_label": "Administration home",
        },
        (
            "For your security, confirm it's you with Google before this action.",
            "Google may ask you to choose your account or sign in. You will then"
            " return to this page.",
        ),
    ),
    "delivery-control": (
        "stewardship/delivery-control.html",
        {
            "available": True,
            "inventory": {"queued": 0, "held": 0, "submitting": 0, "unknown": 0},
            "health": {"ready": False},
            "next_due": {},
        },
        (
            "For your security, confirm it's you with Google before confirming a"
            " pause, resume or resolution.",
        ),
    ),
    "production-withdrawal": (
        "stewardship/production-withdrawal.html",
        {"available": True},
        (
            "Cancelling go-live needs a Google sign-in from the last five minutes."
            " Confirm it's you with Google; you will come back here, then preview"
            " the cancellation again.",
        ),
    ),
    "production-confirmation": (
        "stewardship/production-confirmation.html",
        {
            "state": Value(target_state="active"),
            "here": NEXT,
            "transition": Value(pk=UUID(int=620)),
        },
        (
            "Confirm it's you with Google",
            "Going live needs a Google sign-in from the last five minutes. Confirm"
            " it's you with Google; you will come back here, then check the impact"
            " below.",
        ),
    ),
    "family-tests": (
        "stewardship/campaign-mail-families.html",
        {
            "subject": "Welcome",
            "testing_recipient": "testing@example.org",
            "epoch_ready": True,
            "available": 3,
            "families": [{"name": "Sample Family", "duid": 1234, "label": "Ready"}],
            "confirm": True,
            "sendable": True,
            "signed_in_minutes": 7,
            "next": NEXT,
        },
        (
            "Sending tests to real Families requires confirming your sign-in with"
            " Google (you signed in 7 minutes ago).",
        ),
    ),
    "setup-finishing": (
        "stewardship/setup-cancel.html",
        {
            "attempt": Value(state="frozen", attempt_id=UUID(int=621)),
            "reauthenticate": True,
            "next": NEXT,
            "status_url": "/setup-status.json",
            "elapsed_minutes": 2,
        },
        (
            "Your Google sign-in is too old for installing credentials. Sign in"
            " again with the same account to continue; your setup is kept.",
        ),
    ),
    "backup-key": (
        "stewardship/backup-key.html",
        {
            "label": "Backup encryption key",
            "current": Value(source="configured", fingerprint="0123456789abcdef"),
            "proved": True,
            "next": NEXT,
        },
        ("For security, confirm your Google sign-in before reviewing the change.",),
    ),
    # The automation pages (#582) were already worded without Google; they
    # are here so a later edit cannot add it back.
    "automation-access": (
        "stewardship/automation-access.html",
        {"sessions": [], "live": [], "approval_url": "/admin/automation/approve"},
        (
            "Approving a session first asks you to confirm your sign-in, then"
            " takes you to the approval page.",
        ),
    ),
    "automation-approval": (
        "stewardship/automation-approval.html",
        {
            "state": "enter",
            "next": NEXT,
            "back": "/admin/automation",
            "back_label": "Back to Automation access",
        },
        ("For security, confirm your sign-in before approving a session.",),
    ),
}
# Page name -> the sentences LOCAL shows instead of the Production ones.
LOCAL_WORDING = {
    "error": ("For your security, confirm your sign-in before this action.",),
    "delivery-control": (
        "For your security, confirm your sign-in before you pause, resume or"
        " resolve delivery. You need to have signed in within the last five"
        " minutes.",
    ),
    "production-withdrawal": (
        "Cancelling go-live needs a sign-in from the last five minutes. Confirm"
        " your sign-in, then preview the cancellation again.",
    ),
    "production-confirmation": (
        "Confirm your sign-in",
        "Going live needs a sign-in from the last five minutes. Confirm your"
        " sign-in, then check the impact below.",
    ),
    "family-tests": (
        "Sending tests to real Families requires confirming your sign-in (you"
        " signed in 7 minutes ago).",
    ),
    "setup-finishing": (
        "Your sign-in is too old for installing credentials. Sign in again with"
        " the same email address to continue; your setup is kept.",
    ),
    "backup-key": (
        "For security, confirm your sign-in before reviewing the change. The"
        " code does not need to be typed again.",
    ),
    "automation-access": PAGES["automation-access"][2],
    "automation-approval": PAGES["automation-approval"][2],
}


def render(name, context, admin=None, local=False, **overrides):
    """One page with a stale sign-in, in LOCAL or in any other profile.

    ``overrides`` replace values of the page's own context, for a variant.
    """
    template, values, _ = PAGES[name]
    campaign = Value(
        pk=UUID(int=619),
        delivery_paused=False,
        active_configuration=Value(
            name="Annual campaign",
            starts_at=context["deadline"],
            ends_at=context["absolute_deadline"],
        ),
    )
    page = context | {"admin_chrome": admin, "campaign": campaign, "fresh": False}
    if local:
        page["local_environment"] = True
    return render_to_string(template, page | values | overrides)


def components(context, admin):
    """Each page's prompt at /step-up/<page>, and in LOCAL at .../local."""
    responses = {}
    for name in PAGES:
        for local, suffix in ((False, ""), (True, "/local")):
            responses[f"/step-up/{name}{suffix}"] = (
                "text/html",
                render(name, context, admin, local),
            )
    return responses


class _PromptText(HTMLParser):
    """Collect the text of the element that holds LOCAL's step-up prompt.

    That element (a notice, panel or the error page's section) holds the
    page's own prompt sentences next to the component, and nothing else on
    the page, so a check on it ignores unrelated text such as the setup
    finishing page's hidden Google Drive suggestion.
    """

    # Elements without an end tag never hold text.
    VOID = {"input", "br", "img", "meta", "link", "hr", "source", "wbr"}

    def __init__(self):
        """Start with no open elements and no prompt found."""
        super().__init__()
        self.open = []  # [tag, text pieces, is the prompt's container]
        self.text = None

    def handle_starttag(self, tag, attrs):
        """Open an element; mark its parent if it is the LOCAL step-up."""
        if "data-local-step-up" in dict(attrs) and self.open:
            self.open[-1][2] = True
        if tag not in self.VOID:
            self.open.append([tag, [], False])

    def handle_endtag(self, tag):
        """Close an element, keeping its text if it held the step-up."""
        if self.open and self.open[-1][0] == tag:
            _, pieces, container = self.open.pop()
            if container and self.text is None:
                self.text = " ".join("".join(pieces).split())

    def handle_data(self, data):
        """Text belongs to every element it is inside."""
        for element in self.open:
            element[1].append(data)


def prompt_text(page):
    """The visible-source text of the element holding LOCAL's step-up prompt."""
    parser = _PromptText()
    parser.feed(page)
    assert parser.text is not None, "no LOCAL step-up prompt on the page"
    return parser.text
