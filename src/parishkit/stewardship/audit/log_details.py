"""Plain-language detail for one operational log entry (#633).

``log_descriptions`` says what an entry type means; this module reads one
entry's stored context and says what happened this time: what failed or was
late, by how much, and what happens next. It reads only the closed fields of
``audit.schemas``, so the sentence can never carry a name, an address or
exception text. An entry whose context it does not recognize (an older entry
or a type with nothing to add) gets no sentence; the page still lists its
recorded fields.
"""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from parishkit.stewardship.jobs.operational_content import (
    AUTOMATION_KINDS,
    RESOLVED_INSTRUCTIONS,
    TITLES,
    IncidentKind,
)

# What failed, for each ``failure`` word (audit.schemas.FAILURES, pinned by a
# test). Each says what went wrong, and where an Administrator can act.
FAILURE_TEXT = {
    "organization_mismatch": gettext_lazy(
        "ParishSoft answered for a different organization than the one configured."
    ),
    "destructive_change": gettext_lazy(
        "The ParishSoft data would have removed an unusually "
        "large share of the current data."
    ),
    "shifted_scan": gettext_lazy(
        "ParishSoft's pages moved while they were being read."
    ),
    "invalid_payload": gettext_lazy(
        "ParishSoft returned a record the system could not accept."
    ),
    "incomplete_collection": gettext_lazy(
        "ParishSoft returned fewer records than it said it had."
    ),
    "invalid_response": gettext_lazy(
        "ParishSoft returned an answer that was not valid data."
    ),
    "lease_unavailable": gettext_lazy(
        "Another ParishSoft refresh or cleanup was already running."
    ),
    "configuration_activating": gettext_lazy(
        "A settings change was still taking effect."
    ),
    "configuration_busy": gettext_lazy(
        "A settings change was being installed at the same time."
    ),
    "scope_changed": gettext_lazy(
        "The ParishSoft settings changed while the data was read."
    ),
    "credential_unreadable": gettext_lazy(
        "The stored ParishSoft key could not be read."
    ),
    "credential_changed": gettext_lazy(
        "The ParishSoft key changed while the data was read."
    ),
    "provider_status": gettext_lazy("ParishSoft answered with an error."),
    "provider_timeout": gettext_lazy("ParishSoft did not answer in time."),
    "provider_unreachable": gettext_lazy("ParishSoft could not be reached."),
    "source_configuration": gettext_lazy(
        "The configured ParishSoft settings were refused; "
        "check the ParishSoft settings."
    ),
    "organization_changed": gettext_lazy(
        "The configured ParishSoft organization no longer "
        "matches the current data; check the ParishSoft settings."
    ),
    "source_health_check": gettext_lazy("The ParishSoft health check could not run."),
    "mail_health_check": gettext_lazy("The email health check could not run."),
    "due_work_health_check": gettext_lazy(
        "The scheduled-work health check could not run."
    ),
    "backup_health_check": gettext_lazy("The backup health check could not run."),
    "export_cleanup": gettext_lazy(
        "Removing an expired report download kept failing; use "
        "Retry export cleanup on its task page after fixing the cause."
    ),
    "fact_verification": gettext_lazy("Checking the report totals could not finish."),
    "source_retention": gettext_lazy(
        "Removing old ParishSoft copies failed this time."
    ),
    "family_engagement": gettext_lazy(
        "A Family's sign-in or form opening could not be "
        "recorded for response reporting."
    ),
    "alert_mail": gettext_lazy("An Administrator alert email could not be sent."),
    "security_mail": gettext_lazy("A security notice email could not be sent."),
    "slack_alert": gettext_lazy("A Slack alert could not be sent."),
    "smtp_systemic": gettext_lazy(
        "The mail provider refused mail for the whole account; "
        "check the email settings and the provider's status."
    ),
    "smtp_unavailable": gettext_lazy(
        "The mail provider was unavailable for several emails in a row."
    ),
}

# A closed provider result (audit.schemas.REASONS), in words.
REASON_TEXT = {
    "no_deliverable_recipient": gettext_lazy("no address could receive it"),
    "smtp_transient": gettext_lazy("a temporary refusal"),
    "smtp_unavailable": gettext_lazy("the provider was unavailable"),
    "smtp_permanent": gettext_lazy("a permanent refusal"),
    "smtp_systemic": gettext_lazy("a refusal for the whole account"),
    "smtp_delivery_unknown": gettext_lazy("no confirmation either way"),
    "preparation_failed": gettext_lazy("the email could not be prepared"),
    "slack_not_sent": gettext_lazy("Slack did not accept it"),
    "slack_delivery_unknown": gettext_lazy("Slack did not confirm it either way"),
}


def duration(seconds):
    """A whole-second duration as "45 s", "17 min" or "2 h 5 min"."""
    seconds = max(0, int(seconds))
    if seconds < 120:
        return _("%(count)d s") % {"count": seconds}
    minutes = seconds // 60
    if minutes < 120:
        return _("%(count)d min") % {"count": minutes}
    hours, minutes = divmod(minutes, 60)
    if not minutes:
        return _("%(hours)d h") % {"hours": hours}
    return _("%(hours)d h %(minutes)d min") % {"hours": hours, "minutes": minutes}


def _int(context, key):
    """A stored non-negative whole number, or None."""
    value = context.get(key)
    return value if type(value) is int and value >= 0 else None


def _words(identifier):
    """An identifier word as lowercase words, e.g. "outbox delivery"."""
    return identifier.replace("_", " ")


def _late(context):
    """Scheduled work or a campaign boundary that ran late (``due_work``).

    A duration or limit the entry did not record is left out of the
    sentence rather than shown as zero.
    """
    limit = _int(context, "limit_seconds")
    against = (
        " " + _("(the limit is %(limit)s)") % {"limit": duration(limit)}
        if limit is not None
        else ""
    )
    lag = _int(context, "lag_seconds")
    if "occurrence_id" in context and lag is not None:
        return (
            _("A campaign start or close ran %(lag)s after it was due")
            % {"lag": duration(lag)}
            + against
            + "."
        )
    remaining = _int(context, "remaining_count")
    count = _int(context, "count")
    if "definition_id" in context and remaining is not None:
        done = _int(context, "done_count")
        facts = [
            _("%(remaining)d still to send") % {"remaining": remaining}
            if done is None
            else _("%(remaining)d still to send and %(done)d done")
            % {"remaining": remaining, "done": done}
        ]
        stall = _int(context, "stall_seconds")
        if stall is not None:
            facts.append(_("nothing finished for %(time)s") % {"time": duration(stall)})
        elapsed = _int(context, "elapsed_seconds")
        if elapsed is not None:
            facts.append(_("%(time)s after it fell due") % {"time": duration(elapsed)})
        text = (
            _("A bulk Family send fell behind: %(facts)s")
            % {"facts": ", ".join(str(fact) for fact in facts)}
            + against
            + "."
        )
    elif count is not None and type(context.get("task_type")) is str:
        text = ngettext(
            "%(count)d %(type)s task started late",
            "%(count)d %(type)s tasks started late",
            count,
        ) % {"count": count, "type": _words(context["task_type"])}
        if lag is not None:
            text += "; " + (
                ngettext("it waited %(lag)s", "the longest waited %(lag)s", count)
                % {"lag": duration(lag)}
            )
        text += against + "."
    elif limit is not None:
        # The scan recorded only its limit (#633): what was late could not be
        # put into words.
        text = (
            _("Scheduled work was later than its limit; what was late was not recorded")
            + against
            + "."
        )
    else:
        return None
    others = _int(context, "other_late_count")
    if others:
        text += " " + ngettext(
            "%(count)d other task was also late.",
            "%(count)d other tasks were also late.",
            others,
        ) % {"count": others}
    return text + " " + _("System logs records when it is back on time.")


def _next(context):
    """What happens after a failure: retried when, or given up."""
    outcome = context.get("outcome")
    attempt = _int(context, "attempt")
    if outcome == "retry":
        retry = _int(context, "retry_seconds")
        limit = _int(context, "attempt_limit")
        if retry is not None and attempt and limit:
            return _(
                "It will be tried again in %(wait)s (attempt %(attempt)d of %(limit)d "
                "failed)."
            ) % {"wait": duration(retry), "attempt": attempt, "limit": limit}
        if retry is not None:
            return _("It will be tried again in %(wait)s.") % {"wait": duration(retry)}
        return _("It will be tried again.")
    if outcome == "failed":
        if attempt:
            return _(
                "It will not be retried automatically (attempt %(attempt)d failed)."
            ) % {"attempt": attempt}
        return _("It will not be retried automatically.")
    if outcome == "denied":
        return _("The data was not used; it is checked again on the next run.")
    if outcome == "cancelled":
        return _("The work was cancelled.")
    return ""


def _failure(context):
    """What failed (``failure``) and what happens next."""
    failure = context.get("failure")
    text = FAILURE_TEXT.get(failure) if type(failure) is str else None
    if text is None:
        return None
    parts = [str(text)]
    facts = []
    status = _int(context, "status")
    if status is not None:
        facts.append(_("HTTP status %(status)d") % {"status": status})
    reason = context.get("reason")
    reason = REASON_TEXT.get(reason) if type(reason) is str else None
    if reason is not None:
        facts.append(str(reason))
    count = _int(context, "count")
    if count and count > 1:
        facts.append(_("%(count)d failures") % {"count": count})
    kind = context.get("failure_kind")
    if type(kind) is str:
        facts.append(_("category: %(kind)s") % {"kind": _words(kind)})
    if facts:
        parts.append(
            _("Details: %(facts)s.") % {"facts": "; ".join(str(f) for f in facts)}
        )
    parts.append(_next(context))
    return " ".join(str(part) for part in parts if part)


def _recovered(context):
    """An incident that ended (``recovery``).

    A kind whose end needs follow-up (``RESOLVED_INSTRUCTIONS``, such as a
    backup key change, which ends when the change is no longer recent, not
    when anyone checked the key) says "ended" with that instruction, never
    "recovered". Automation notices get no recovery entry (the trigger
    leaves them out); an old one gets no sentence either.
    """
    kind = context.get("incident_kind")
    try:
        kind = IncidentKind(kind) if type(kind) is str else None
    except ValueError:
        kind = None
    if kind is None or kind in AUTOMATION_KINDS:
        return None
    title = _(TITLES[kind])
    instruction = RESOLVED_INSTRUCTIONS.get(kind)
    if instruction is None:
        text = _("Recovered: “%(title)s” has ended") % {"title": title}
    else:
        text = _("Ended: “%(title)s”") % {"title": title}
    elapsed = _int(context, "elapsed_seconds")
    count = _int(context, "count")
    if elapsed is not None:
        text += " " + _("after %(time)s") % {"time": duration(elapsed)}
    if count:
        text += " " + ngettext(
            "(seen %(count)d time)", "(seen %(count)d times)", count
        ) % {"count": count}
    text += "."
    if instruction is not None:
        text += " " + _(instruction)
    if "log_id" in context:
        text += " " + _("Show related entries lists the entry that opened it.")
    return text


def explain(schema, context):
    """The plain-language detail of one operational entry, or None.

    ``schema`` is the stored context schema, which says which fields to read.
    """
    if type(context) is not dict or not context:
        return None
    if schema == "due_work":
        return _late(context)
    if schema == "failure":
        return _failure(context)
    if schema == "recovery":
        return _recovered(context)
    return None
