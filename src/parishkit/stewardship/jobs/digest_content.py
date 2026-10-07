"""Individually routed Administrator digests with mandatory compiled facts."""

from dataclasses import dataclass

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.reports.daily_digest import DailyDigestContent
from parishkit.stewardship.reports.weekly_digest import WeeklyDigestContent
from parishkit.stewardship.web.content import (
    ADMIN_DIGEST_PLACEHOLDERS,
    SafeContent,
    bounded_text,
    prepare_content,
    render_template,
    text_html,
    validate_admin_digest_content,
)
from parishkit.stewardship.web.weekly_digest_content import validate_weekly_body

from .outbox_validation import DeliveryIdentity, RenderInput, WeeklyRenderInput


@dataclass(frozen=True, repr=False)
class DigestTemplate:
    """Parish-authored introduction cannot replace the required compiled report."""

    # The envelope appends the report kind, its date and the parish (#720).
    subject: str = "{{ campaign_name }}"
    html: str = ""
    text: str = ""

    def __post_init__(self):
        """Only canonical authored HTML and public substitutions are admitted."""
        validate_admin_digest_content(self.subject, self.html, self.text)
        if not self.subject.strip() or prepare_content(
            self.html, text=self.text
        ) != SafeContent(self.html, self.text):
            raise ValueError("Admin digest template requires canonical safe content.")


# Room for the "[TEST] " prefix within the 254-character subject limit.
MAX_SUBJECT = 247


# Words that show an authored subject already names the kind of report.
REPORT_WORDS = ("report", "digest", "summary")


def report_label(title):
    """The short report name of a compiled title: "Daily campaign digest" →
    "daily report", "Manual weekly information digest" → "manual weekly report".
    """
    words = title.split()
    kind = [
        word.lower()
        for word in words
        if word.lower() not in {"campaign", "information", "digest"}
    ]
    return " ".join(kind + ["report"])


def identified_subject(authored, report, *, campaign, parish):
    """Make the subject name the report and its day, the campaign and parish once.

    The report emails open straight into their content (#720), so the subject
    is the only place that names them. ``report`` is the compiled title and
    date, for example "Weekly information digest — October 5, 2026". The
    Administrator's subject is kept and only what it lacks is appended:

    - the report's kind ("daily report"), unless the subject already names a
      report, digest or summary;
    - the date;
    - "manual" or "recovery", and the campaign and parish names, in
      parentheses when missing.

    For example "Annual campaign — daily report, November 2, 2026 (Example
    Parish)".
    """
    title, _, when = report.rpartition(" — ")
    subject = " ".join(authored.split())
    folded = subject.casefold()
    names_report = any(word in folded for word in REPORT_WORDS)
    label = report_label(title)
    if not names_report:
        subject = f"{subject} — {label}" if subject else label[:1].upper() + label[1:]
    if when and when.casefold() not in subject.casefold():
        subject += f", {when}"
    notes = []
    if names_report:
        notes += [
            word
            for word in ("manual", "recovery")
            if word in label.split() and word not in folded
        ]
    notes += [
        name
        for name in (campaign, parish)
        if name and name.casefold() not in subject.casefold()
    ]
    if notes:
        subject += f" ({', '.join(notes)})"
    return subject if len(subject) <= MAX_SUBJECT else subject[: MAX_SUBJECT - 1] + "…"


def render_digest_envelope(
    *,
    identity,
    configuration_id,
    template_id,
    template,
    content,
    values,
    sender,
    recipient,
    testing_recipient=None,
    reply_to=None,
):
    """Route exactly one intended Admin; current authorization belongs to the owner.

    The same intended recipient remains visible in Testing while the actual
    envelope contains only the designated test mailbox. Authored substitutions
    remain public campaign values; private report values arrive only in the
    separately compiled content, never as executable template expressions.
    Daily chart bytes stay with the snapshot owner and typed MIME adapter.
    """
    if (
        not isinstance(identity, DeliveryIdentity)
        or identity.purpose not in {"daily_digest", "weekly_digest"}
        or identity.credential_namespace != "none"
        or not isinstance(template, DigestTemplate)
        or not isinstance(
            content,
            DailyDigestContent
            if identity.purpose == "daily_digest"
            else WeeklyDigestContent,
        )
    ):
        raise TypeError(
            "An exact Admin digest identity and typed content are required."
        )
    if type(values) is not dict or not values.keys() <= ADMIN_DIGEST_PLACEHOLDERS:
        raise ValueError("Admin digest requires public campaign substitutions.")
    for value in values.values():
        bounded_text(value)
    if normalized_email(recipient) != recipient:
        raise ValueError("Admin digest requires one canonical intended recipient.")
    subject = identified_subject(
        render_template(template.subject, values, subject=True),
        content.subject,
        campaign=" ".join(values.get("campaign_name", "").split()),
        parish=" ".join(values.get("parish_name", "").split()),
    )
    html = render_template(template.html, values, html=True)
    text = render_template(template.text, values)
    # Validate authored substitutions together before appending compiler-owned
    # markup, whose report values are not interpreted as template expressions.
    validate_admin_digest_content(subject, html, text)
    html += content.html
    text += ("\n\n" if text else "") + content.text
    routed = recipient
    if identity.mode == "testing":
        if normalized_email(testing_recipient) != testing_recipient:
            raise ValueError("Testing requires one canonical override recipient.")
        routed = testing_recipient
        description = f"TEST — sent to {routed} instead of Administrator {recipient}."
        subject = "[TEST] " + (subject if len(subject) <= 247 else subject[:246] + "…")
        # text_html matches sanitizer output, so the delivery check passes.
        html = "<h2>TEST</h2><p>" + text_html(description) + "</p>" + html
        text = description + "\n\n" + text
    elif testing_recipient is not None:
        raise ValueError("Production mail cannot have a Testing override.")
    if identity.purpose == "weekly_digest":
        validate_weekly_body(html, text)
    render_type = (
        WeeklyRenderInput if identity.purpose == "weekly_digest" else RenderInput
    )
    return render_type(
        configuration_id=configuration_id,
        template_id=template_id,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=(recipient,),
        routed_recipients=(routed,),
        subject=subject,
        html=html,
        text=text,
    )
