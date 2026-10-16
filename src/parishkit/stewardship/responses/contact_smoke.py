"""The human-run Member contact smoke check that confirms the contact read (#528).

Census publication (BG-09) may write a Member only after reading all fifteen
fields the v2 Member contact PUT replaces, so that a PUT can never clear a
field nobody read. Which ParishSoft GET returns those fifteen fields is not
yet confirmed, so ``ParishSoftContactGateway.read_member_contact`` still
refuses (``read_unconfirmed``). This check settles it, run by a human inside
the deployed worker against one chosen test Member
(docs/guides/stewardship-smoke-tools.md#member-contact-round-trip):

1. **Probe** (always): read ``GET members/{id}`` and report, for each PUT
   field, whether the record carries it, under which spelling, its JSON type
   and, for dates, phones and gender, its character shape (digits as ``9``,
   letters as ``a``); whether the publication encoder reproduces the value
   as read (``encoder_matches_read``); whether the value as read satisfies
   the recorded PUT schema; which encoding fields were blank, so are still
   unsettled; and the record's top-level key names. No values.
2. **Round trip** (only when asked, and only when the probe found all
   fifteen fields, each sendable as read): PUT the payload exactly as read
   with one field set to a fixed marker, read back, then always PUT the
   original payload back, retrying with backoff until a read matches.

Every read compares the whole record, not just the fifteen fields, so a PUT
that also changes something outside the request model is reported (by key
name) and fails confirmation. The result carries no values, names or ids.
CI drives this module with a fake gateway only.
"""

import re
import signal
import threading
import time
from contextlib import contextmanager
from decimal import Decimal

from parishkit import __version__
from parishkit.config import ConfigError
from parishkit.parishsoft_transport import SourceTransportDrainFailure

from .capabilities import MEMBER_CAPABILITIES, Handling
from .publication import (
    MEMBER_CONTACT_FIELDS,
    decode_member_value,
    encode_member_value,
)
from .publication_gateway import ContactGatewayError

# Other spellings a Member record may use for a PUT field. The household
# reader and the source corpus already read these from ParishSoft Member
# records (parishsoft_households, source.corpus).
READ_ALIASES = {
    "dateOfBirth": ("birthdate",),
    "gender": ("sex",),
    "cellPhone": ("mobilePhone",),
}
# The fields the round trip may change, each with a fixed marker in the
# Family-display phone form (not the canonical digits), so a provider that
# normalizes a written phone fails confirmation. Both fields are rarely used
# and reach no Family-facing census text (nickName would, so it is not here).
MARKERS = {
    "pager": "(555) 555-0199",
    "fax": "(555) 555-0198",
}
DEFAULT_FIELD = "pager"
# The recorded MemberContactUpdateRequestModel constraints (a test pins them
# to tests/stewardship/fixtures/parishsoft_contact_capabilities.json): every
# field is a nullable string with these (minimum, maximum) lengths; the two
# dates are ``format: date``.
PUT_SCHEMA = {
    "firstName": (0, 30),
    "nickName": (0, 30),
    "middleName": (0, 30),
    "lastName": (0, 50),
    "maidenName": (0, 50),
    "dateOfBirth": (0, None),
    "dateOfDeath": (0, None),
    "language": (0, 50),
    "emailAddress": (0, 150),
    "homePhone": (10, 25),
    "cellPhone": (10, 25),
    "workPhone": (10, 25),
    "pager": (10, 25),
    "fax": (10, 25),
    "gender": (0, 1),
}
DATE_FIELDS = frozenset({"dateOfBirth", "dateOfDeath"})
# Fields whose encoding is still provisional in the publication encoder: a
# blank one on the test Member leaves its encoding unsettled.
ENCODING_FIELDS = (
    "cellPhone",
    "dateOfBirth",
    "dateOfDeath",
    "emailAddress",
    "fax",
    "gender",
    "homePhone",
    "pager",
    "workPhone",
)
SHAPED = frozenset(ENCODING_FIELDS) - {"emailAddress"}
# Record keys ParishSoft may change on every write (audit stamps). They are
# reported apart and never fail confirmation; any other key that changes does.
VOLATILE_KEYS = frozenset(
    {
        "dateModified",
        "modifiedDate",
        "lastModifiedDate",
        "modifiedBy",
        "lastModifiedBy",
    }
)
# The census field each PUT field is published from, for the encoder check.
CENSUS_FIELDS = {
    capability.provider_fields[0]: name
    for name, capability in MEMBER_CAPABILITIES.items()
    if capability.handling is Handling.API
}
TIMEOUT = 30
# Seconds to wait before a restore read when the marker PUT was not
# accepted (a timed-out PUT may land late), and between restore attempts.
SETTLE_SECONDS = 15
RESTORE_BACKOFF = (5, 15, 30)
_ADDRESS = re.compile(r"[^\s,;]+@[^\s,;]+")


class RoundTripAborted(Exception):
    """The round trip stopped after a write; ``result`` holds what is known."""

    def __init__(self, result):
        """Keep the value-free partial result for the console."""
        super().__init__("The contact round trip was interrupted.")
        self.result = result


def _type(value):
    """The JSON type name of one value read."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float | Decimal):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(value), "other")


def _shape(value):
    """A string with every digit as ``9`` and every letter as ``a``."""
    return "".join(
        "9" if char.isdigit() else "a" if char.isalpha() else char for char in value
    )


def _describe(value):
    """A value's type and, for a string, its shape: never the value."""
    report = {"type": _type(value)}
    if isinstance(value, str):
        report["shape"] = _shape(value)
    return report


def _encoder_matches(name, value):
    """Whether publication's encoder gives back ``value`` exactly as read.

    True or False, ``"unreadable"`` when the census decoder refuses it, or
    None for a field publication never writes (pager, fax).
    """
    census = CENSUS_FIELDS.get(name)
    if census is None:
        return None
    try:
        return encode_member_value(census, decode_member_value(census, value)) == value
    except ValueError:
        return "unreadable"


def sendable(name, value):
    """Whether one value as read satisfies the recorded PUT schema.

    An Administrator's edited census value is held to the same check
    (``census_review.edited_value``), so the two never disagree.
    """
    if value is None:
        return True
    if type(value) is not str:
        return False
    if name in DATE_FIELDS:
        return re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is not None
    minimum, maximum = PUT_SCHEMA[name]
    return minimum <= len(value) and (maximum is None or len(value) <= maximum)


def map_record(record):
    """The PUT payload one Member record yields, and a per-field report.

    Returns ``(payload, fields)``. ``payload`` holds each PUT field the record
    carries, under its own name or one alias. Two spellings carrying
    different values are ambiguous: the field is left out (so an unclear read
    never counts as complete) and each spelling's type and shape is reported
    for the human to choose. ``fields`` maps every PUT field to its
    ``present``, ``source``, ``type``, ``shape`` (encoding fields),
    ``encoder_matches_read`` and ``sendable``, plus the address count and
    separators of the email field; never a value.
    """
    payload, fields = {}, {}
    for name in MEMBER_CONTACT_FIELDS:
        found = [key for key in (name, *READ_ALIASES.get(name, ())) if key in record]
        if len({repr(record[key]) for key in found}) > 1:
            fields[name] = {
                "present": False,
                "ambiguous": True,
                "sources": {key: _describe(record[key]) for key in found},
            }
            continue
        if not found:
            fields[name] = {"present": False, "ambiguous": False}
            continue
        value = record[found[0]]
        payload[name] = value
        report = {"present": True, "source": found[0], "type": _type(value)}
        if isinstance(value, str) and name in SHAPED:
            report["shape"] = _shape(value)
        if isinstance(value, str) and name == "emailAddress":
            report["addresses"] = len(_ADDRESS.findall(value))
            report["separators"] = sorted(
                {_shape(part) for part in _ADDRESS.split(value)[1:-1]}
            )
        if value not in (None, ""):
            report["encoder_matches_read"] = _encoder_matches(name, value)
        report["sendable"] = sendable(name, value)
        fields[name] = report
    return payload, fields


def _read(gateway, member_id, *, timeout):
    """Read the one chosen Member's record, refusing any other identity."""
    record = gateway.probe_member_record(member_id, timeout=timeout)
    if record is None:
        raise ConfigError("ParishSoft has no Member with that id.")
    if str(record.get("memberDUID")) != str(member_id):
        raise ConfigError("ParishSoft answered with another Member.")
    return record


def _changed_keys(before, after, *, ignore=()):
    """Sorted non-volatile record key names whose values differ (names only)."""
    return sorted(
        key
        for key in set(before) | set(after)
        if key not in VOLATILE_KEYS
        and key not in ignore
        and (key not in before or key not in after or before[key] != after[key])
    )


def _volatile_changed(before, after):
    """The volatile key names that changed, reported for information only."""
    return sorted(key for key in VOLATILE_KEYS if before.get(key) != after.get(key))


def _write(gateway, member_id, payload, *, timeout):
    """One PUT; its outcome as a word (``accepted`` or the gateway's failure).

    A helper whose kill the OS could not confirm (``drain_failed``) is an
    outcome here, not an abort. The worker stops its consumer on it, to keep
    its fencing promises; this run has no fence to protect, and leaving the
    marker in place would be worse. The next call starts a fresh helper, the
    restore sends only the original payload, and the settle wait and the
    full-record read catch a marker the stranded helper lands late.
    """
    try:
        gateway.write_member_contact(member_id, payload, timeout=timeout)
    except ContactGatewayError as error:
        return error.kind
    except SourceTransportDrainFailure:
        return "drain_failed"
    return "accepted"


def _read_after(gateway, member_id, *, timeout):
    """A read-back record, or None when the read-back itself failed."""
    try:
        return _read(gateway, member_id, timeout=timeout)
    except (ContactGatewayError, ConfigError, SourceTransportDrainFailure):
        return None


@contextmanager
def _interrupts_ignored():
    """Ignore Ctrl-C while the original is being put back (main thread only)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _restore(gateway, member_id, original, record, *, settle, timeout, sleep):
    """Put ``original`` back, retrying with backoff until a read matches.

    ``record`` is the first full read; the restore is done only when a read
    shows every non-volatile key as it was. ``settle`` waits before the first
    read, for a marker PUT that may land late. Returns the result keys.
    """
    attempts, restored = 0, None
    for delay in (*RESTORE_BACKOFF, None):
        attempts += 1
        outcome = _write(gateway, member_id, original, timeout=timeout)
        if settle:
            sleep(SETTLE_SECONDS)
            settle = False
        restored = _read_after(gateway, member_id, timeout=timeout)
        if restored is not None and not _changed_keys(record, restored):
            break
        if delay is not None:
            sleep(delay)
    return {
        "restore_write": outcome,
        "restore_attempts": attempts,
        "restore_mismatch": (
            None if restored is None else _changed_keys(record, restored)
        ),
        "restored": restored is not None and not _changed_keys(record, restored),
    }


def run(
    gateway,
    member_id,
    *,
    send=False,
    field=DEFAULT_FIELD,
    timeout=TIMEOUT,
    sleep=time.sleep,
    report=lambda result: None,
):
    """Probe one Member's contact read and, with ``send``, round-trip one field.

    ``gateway`` is a ``ParishSoftContactGateway`` (a fake in tests). Only
    ``member_id`` is ever read or written: every read must answer with that
    Member's own ``memberDUID``, and every write targets the same id. The
    round trip is refused unless the probe found all fifteen fields, each
    valid for the PUT schema as read, and no field already holds a marker.
    Once the marker PUT has been attempted the original is always put back
    (even after a failed write, a helper that would not drain or an
    unexpected error), with ``report`` told the value-free result first.
    Ctrl-C is ignored from just before the marker PUT until the restore
    ends. An abort after a write raises ``RoundTripAborted`` carrying the
    partial result.
    """
    if field not in MARKERS:
        raise ConfigError("The smoke field must be one of: " + ", ".join(MARKERS))
    record = _read(gateway, member_id, timeout=timeout)
    original, fields = map_record(record)
    complete = set(original) == set(MEMBER_CONTACT_FIELDS)
    result = {
        "parishkit_version": __version__,
        "read": "members/{id}",
        "read_complete": complete,
        "fields": fields,
        "record_keys": sorted(record),
        "unsendable": sorted(
            name for name, item in fields.items() if item.get("sendable") is False
        ),
        "unsettled": sorted(
            name for name in ENCODING_FIELDS if original.get(name) in (None, "")
        ),
        "markers_present": sorted(
            name for name, marker in MARKERS.items() if original.get(name) == marker
        ),
        "field": field,
        "sent": False,
        "confirmed": False,
    }
    if not send:
        return result
    for refusal, reason in (
        (not complete, "refused_incomplete_read"),
        (result["unsendable"], "refused_unsendable"),
        # A marker left behind by an earlier run: restore it by hand first.
        (result["markers_present"], "refused_marker_present"),
    ):
        if refusal:
            result["write"] = reason
            return result
    result["sent"] = True
    aborted = None
    # Ctrl-C is ignored from just before the marker PUT until the restore
    # ends, so no interrupt can fall between the marker and the restore.
    # Every call inside is bounded by its timeout.
    with _interrupts_ignored():
        try:
            result["write"] = _write(
                gateway,
                member_id,
                {**original, field: MARKERS[field]},
                timeout=timeout,
            )
            after = _read_after(gateway, member_id, timeout=timeout)
            source = fields[field]["source"]
            result["marker_read_back"] = (
                after is not None and after.get(source) == MARKERS[field]
            )
            result["also_changed"] = (
                None if after is None else _changed_keys(record, after, ignore={source})
            )
            result["volatile_changed"] = (
                None if after is None else _volatile_changed(record, after)
            )
        except (Exception, KeyboardInterrupt) as error:
            # Never leave without the restore; report what is known after it.
            aborted = error
            result["aborted"] = True
        report(result)
        result |= _restore(
            gateway,
            member_id,
            original,
            record,
            settle=result.get("write") != "accepted",
            timeout=timeout,
            sleep=sleep,
        )
    if aborted is not None:
        raise RoundTripAborted(result) from None
    result["confirmed"] = (
        result["write"] == "accepted"
        and result["marker_read_back"]
        and result["also_changed"] == []
        and result["restored"]
    )
    return result
