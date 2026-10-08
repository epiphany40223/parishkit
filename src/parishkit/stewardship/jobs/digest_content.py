"""Individually routed Administrator digests with mandatory compiled facts."""

import re
from dataclasses import dataclass

from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.reports.daily_digest import DailyDigestContent
from parishkit.stewardship.reports.weekly_digest import WeeklyDigestContent
from parishkit.stewardship.web.content import (
    ADMIN_DIGEST_PLACEHOLDERS,
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
        """Re-sanitized authored HTML and public substitutions only (#385).

        Today's sanitizer cleans the stored introduction instead of an
        equality check failing on a later sanitizer change.
        """
        if not self.subject.strip():
            raise ValueError("Admin digest template requires a subject.")
        object.__setattr__(
            self, "html", prepare_content(self.html, text=self.text).html
        )
        validate_admin_digest_content(self.subject, self.html, self.text)


# Room for the "[TEST] " prefix within the 254-character subject limit.
MAX_SUBJECT = 247


# Whole words that show an authored subject already names the kind of report
# ("Reporting on …" does not).
REPORT_WORDS = re.compile(r"\b(?:reports?|digests?|summary|summaries)\b", re.I)


def _has_word(text, word):
    """Whether ``text`` contains ``word`` as a whole word, ignoring case."""
    return re.search(rf"\b{re.escape(word)}\b", text, re.I) is not None


def identified_subject(authored, report, *, campaign, parish):
    """Make the subject name the report and its day, the campaign and parish once.

    The report emails open straight into their content (#720), so the subject
    is the only place that names them. ``report`` is the compiled title and
    date, for example "Weekly information digest — October 5, 2026". The
    Administrator's subject is kept and only what it lacks is appended:

    - the report's kind, "daily report" or "weekly report" (just "report"
      when the subject already says daily or weekly), unless the subject
      already names a report, digest or summary as a whole word;
    - the date;
    - in parentheses, "manual" or "recovery" and the campaign and parish
      names, each only when missing.

    For example "Annual campaign — daily report, November 2, 2026 (Example
    Parish)". A subject too long for the limit loses the end of the
    Administrator's text, never the appended date or names.
    """
    title, _, when = report.rpartition(" — ")
    base = " ".join(authored.split())
    kind = "weekly" if _has_word(title, "weekly") else "daily"
    if not REPORT_WORDS.search(base):
        label = "report" if _has_word(base, kind) else f"{kind} report"
        base = f"{base} — {label}" if base else f"{kind.title()} report"
    suffix = f", {when}" if when and when.casefold() not in base.casefold() else ""
    notes = [
        word
        for word in ("manual", "recovery")
        if _has_word(title, word) and not _has_word(base, word)
    ]
    notes += [
        name
        for name in (campaign, parish)
        if name and name.casefold() not in (base + suffix).casefold()
    ]
    if notes:
        suffix += f" ({', '.join(notes)})"
    room = MAX_SUBJECT - len(suffix)
    if len(base) > room:
        # Keep what the report adds; shorten the Administrator's text.
        base = base[: max(room - 1, 0)].rstrip() + "…"
    subject = base + suffix
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
