"""The closed context of a refused Admin input (#1403; operations spec,
Admin input refusals).

A refusal's operational entry and its process-log line hold the same
allow-listed keys and nothing else. ``clean`` builds them: anything not on
the list, or not of its key's shape, is dropped rather than redacted, so a
refusal never records a submitted value. The database's
``stewardship_safe_context_v1`` checks the same shapes for schema
``refusal``; Python holds the closed sets (reasons, methods, statuses,
modes), as it does for ``failure_kind``, so a new reason needs no migration.

This module imports nothing from the rest of the package, so the process-log
formatter (``observability``) can re-check a line with it.
"""

import re

# Route, method, form, field, code, reason and exception-class values: a
# resolved page name such as ``admin:campaign_create``, a formset field such
# as ``slots-0-time``, a Django error code such as ``invalid_choice``.
IDENTIFIER = re.compile(r"[a-z][a-z0-9_:-]{0,63}")
# A pk-admin catalog name, such as ``export create``: the shape SQL checks
# for every ``command`` key.
COMMAND = re.compile(r"[a-z][a-z-]*( [a-z][a-z-]*){0,3}")
COMMAND_LIMIT = 64
# An expected or actual setting value: enum words, IANA zone names, counts,
# sizes, content types and versions; never an ``@``.
SETTING = re.compile(r"[A-Za-z0-9_+./-]{1,64}")
# At most this many fields and codes per entry.
LIST_LIMIT = 10
# The largest unexpected-field count SQL admits (four digits).
UNEXPECTED_LIMIT = 9999

METHODS = frozenset({"get", "head", "post", "put", "patch", "delete"})
STATUSES = frozenset({400, 403, 404, 409, 410, 422})
MODES = frozenset({"testing", "production"})

# Every reason code a refusal can carry. The shared refusals fix the first
# group; ``unclassified`` is a refusal with no reason of its own (its class
# name goes in ``exception_class``). The last three are the command line's
# exit-2 refusals, which reach the process log only (admin-automation spec,
# Refusal entry). A new kind of refusal adds its reason here.
REASONS = frozenset(
    {
        "stale_page",
        "unexpected_fields",
        "expired_preview",
        "bad_signature",
        "fresh_authentication_required",
        "csrf_failed",
        "denied",
        # A retired action or another campaign's address (UserFacingGone,
        # HTTP 410): INFO, although it is a PermissionError.
        "gone",
        # The Administrator's 2026-10-15 review cases (#1403, PR 5): a
        # cloned campaign's zone that is not the parish's, a logo or image
        # refused for its size or its file, and the Off-site backups page's
        # Test access refusals.
        "campaign_zone_not_parish",
        "image_too_large",
        "image_unsupported",
        "drive_not_in_local",
        "workspace_mail_missing",
        "drive_folder_missing",
        "drive_folder_link_invalid",
        # Scheduled email's Send test: a test still on its way (INFO), or one
        # whose result the mail provider left uncertain (WARNING: the
        # provider's state, not the input).
        "test_pending",
        "test_result_uncertain",
        # Automation access approval: an unknown code, or another's pairing
        # (one answer for both, drawn in place). INFO: the code typed is the
        # input's own fault, and repeated probes are bounded by the pairing
        # limiter and the cap.
        "pairing_code_refused",
        # Restore review and Held emails: a release or a resend the
        # restored site's state refuses (the step's own key is the code).
        "release_refused",
        "resend_refused",
        # System health: allowing debug logging again in Production, which
        # only the server operator may do.
        "production_only",
        "unclassified",
        "usage",
        "configuration",
        "credential_mismatch",
    }
)
# Reasons recorded at WARNING: denials, CSRF failures and causes that are
# server state or configuration rather than the input. Every other reason,
# a step-up request included, is INFO; ``unclassified`` is WARNING only for
# a PermissionError (see ``audit.refusals.level_of``).
WARNING_REASONS = frozenset(
    {
        "denied",
        "csrf_failed",
        "configuration",
        "credential_mismatch",
        # Server state or configuration, not the input.
        "campaign_zone_not_parish",
        "drive_not_in_local",
        "workspace_mail_missing",
        "test_result_uncertain",
        "release_refused",
        "resend_refused",
        "production_only",
    }
)
# Reasons whose ``expected`` and ``actual`` are allow-listed settings. This
# set is the only gate on which reasons carry values: the database checks
# only their shape, which a token or a phone number can also fit. Each
# reason here says which setting its values are:
# - ``campaign_zone_not_parish``: the parish's IANA zone and the zone chosen
#   (both from the server's zone catalog);
# - ``image_too_large``: the limit and the file's size, in bytes.
SETTING_REASONS = frozenset({"campaign_zone_not_parish", "image_too_large"})

# The keys, in the order an entry lists them.
KEYS = (
    "route",
    "command",
    "method",
    "status",
    "form",
    "fields",
    "codes",
    "reason",
    "exception_class",
    "expected",
    "actual",
    "unexpected_count",
    "mode",
    "capped",
)


def _identifier(value):
    """Whether ``value`` is one identifier-shaped word."""
    return type(value) is str and IDENTIFIER.fullmatch(value) is not None


# ``fields`` and ``codes`` are checked by shape only, so a caller must take
# them from server-owned names (the form's own fields, FieldError ids,
# error codes), never from submitted keys: an unexpected submitted key is
# only counted (``unexpected_count``).
def _names(value):
    """The identifier-shaped names in ``value``, in order, without repeats,
    at most ``LIST_LIMIT``; anything else in it is dropped."""
    if type(value) not in {list, tuple}:
        return []
    names = []
    for item in value:
        if _identifier(item) and item not in names:
            names.append(item)
    return names[:LIST_LIMIT]


def _count(value, limit):
    """Whether ``value`` is a whole number from 0 to ``limit`` (not a bool)."""
    return type(value) is int and 0 <= value <= limit


def clean(values):
    """The allow-listed refusal context in ``values``, everything else dropped.

    A reason outside ``REASONS`` becomes ``unclassified``, so an entry always
    says why. ``exception_class`` stays only with ``unclassified``,
    ``unexpected_count`` only with ``unexpected_fields`` and the expected
    and actual values only with a reason in ``SETTING_REASONS``. When both a
    route and a command are given, both are dropped: the caller is wrong,
    and an entry must name exactly one (``complete``).
    """
    if type(values) is not dict:
        return {}
    reason = values.get("reason")
    if reason not in REASONS:
        reason = "unclassified"
    route, command = values.get("route"), values.get("command")
    found = {
        "route": route if _identifier(route) else None,
        "command": (
            command
            if type(command) is str
            and len(command) <= COMMAND_LIMIT
            and COMMAND.fullmatch(command)
            else None
        ),
        "method": values.get("method") if values.get("method") in METHODS else None,
        "status": (
            values.get("status")
            if type(values.get("status")) is int and values["status"] in STATUSES
            else None
        ),
        "form": values.get("form") if _identifier(values.get("form")) else None,
        "fields": _names(values.get("fields")) or None,
        "codes": _names(values.get("codes")) or None,
        "reason": reason,
        "exception_class": (
            values.get("exception_class")
            if reason == "unclassified" and _identifier(values.get("exception_class"))
            else None
        ),
        "unexpected_count": (
            values.get("unexpected_count")
            if reason == "unexpected_fields"
            and _count(values.get("unexpected_count"), UNEXPECTED_LIMIT)
            else None
        ),
        "mode": values.get("mode") if values.get("mode") in MODES else None,
        "capped": True if values.get("capped") is True else None,
    }
    if reason in SETTING_REASONS:
        for key in ("expected", "actual"):
            value = values.get(key)
            if type(value) is str and SETTING.fullmatch(value):
                found[key] = value
    if found["route"] is not None and found["command"] is not None:
        found["route"] = found["command"] = None
    return {key: found[key] for key in KEYS if found.get(key) is not None}


def complete(context):
    """Whether a cleaned context can be kept durably: it names exactly one
    of a page and a command (the database refuses anything else)."""
    return ("route" in context) != ("command" in context)


def is_clean(values):
    """Whether ``values`` is already exactly its own cleaned context."""
    return type(values) is dict and clean(values) == values
