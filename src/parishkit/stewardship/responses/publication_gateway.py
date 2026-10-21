"""ParishSoft Member contact reads and writes for census publication (#528).

BG-09's publish worker (``responses.publication_worker``) reads one Member's
full contact record right before each PUT and again after it, and writes it
with ParishSoft's v2 ``PUT /api/v2/members/{id}/contact``. It talks to a
``ContactGateway``: ``read_member_contact`` returns the record keyed by the
request schema's fields (``publication.MEMBER_CONTACT_FIELDS``), leaving out
any field the read lacks, or None when the Member is not there;
``write_member_contact`` sends one PUT body holding only the fields to
write (the PUT is a partial update, #528/#1600). Both raise
``ContactGatewayError`` with a fixed word, never provider text.

CI uses an in-memory fake only. ``ParishSoftContactGateway`` is the real
one. Its write is implemented, through ``BoundedPublicationSession``, the
one transport whose helper admits that PUT. Its read is not: which
ParishSoft GET returns all fifteen Member contact fields is unconfirmed, so
it refuses with ``read_unconfirmed``. The worker reads before every write,
so the real gateway can never reach its PUT until a confirmed read replaces
that refusal and the human-run smoke test has written one test Member and
read it back (docs/guides/stewardship-member-requests.md#parishsoft-publication).

The one exception is the local fake ParishSoft (#1884), whose record shape
this package defines (``stewardship.local.fake_contact``): for the fake's
base URL, which only a LOCAL deployment can choose, the read is
``GET members/{id}`` mapped onto the request schema's names, so pk-local
runs the real worker, session and helper end to end against the fake. The
real API's read stays refused.

That smoke test (``responses.contact_smoke``) is the one other user of this
gateway. It opens it with ``open_operator`` (no task owns a human's run) and
reads the candidate record with ``probe_member_record``, which returns the
raw ``GET members/{id}`` answer; the worker never calls it.
"""

from types import SimpleNamespace
from typing import Protocol

from django.db import connection

from parishkit.parishsoft_http_worker import LOCAL_SOURCE_BASE_URL, admitted_base_url
from parishkit.parishsoft_transport import (
    BoundedPublicationSession,
    SourceTransportError,
)
from parishkit.stewardship.storage import StorageInvariantError

# The sanitized failure words a gateway may raise; each is also a valid
# publication outcome failure_kind.
FAILURE_KINDS = frozenset(
    {
        "read_unavailable",
        "read_unconfirmed",
        "write_unavailable",
        "write_rejected",
    }
)


class ContactGatewayError(Exception):
    """One gateway failure, named by a fixed word (never provider text)."""

    def __init__(self, kind):
        """Keep only a reviewed failure word."""
        if kind not in FAILURE_KINDS:
            raise ValueError("Unknown contact gateway failure.")
        super().__init__(kind)
        self.kind = kind


class ContactGateway(Protocol):
    """What the publish worker needs from ParishSoft, and nothing more."""

    def read_member_contact(self, member_id, *, timeout):
        """One Member's uncached contact record, or None when not found."""

    def write_member_contact(self, member_id, payload, *, timeout):
        """Write the fields in ``payload``; ParishSoft leaves every other one."""


def _member_id(value):
    """A ParishSoft Member id as a positive integer (refuses anything else)."""
    text = str(value)
    if not text.isdigit() or not 0 < int(text) < 10**18:
        raise ValueError("Invalid ParishSoft Member id.")
    return int(text)


class ParishSoftContactGateway:
    """The ParishSoft gateway: PUT implemented, contact read not yet confirmed.

    ``session`` is a ``BoundedPublicationSession`` (see ``open``); the worker
    reserves the source lease's request window before each call, so the
    session's own preflight only refuses a call made inside a transaction
    or after the task lost ownership.
    """

    def __init__(self, session, *, profile):
        """Bind one publication session and the one base URL it was opened for."""
        if not isinstance(session, BoundedPublicationSession):
            raise TypeError("ParishSoft publication requires its own session.")
        self.session = session
        self.base_url = admitted_base_url(profile, session.base_url)

    @classmethod
    def open(cls, execution, credential, *, profile, base_url, on_timeout):
        """Build the gateway for one running publish task.

        ``base_url`` is the deployment's chosen ParishSoft base URL; the
        session refuses a LOCAL profile with the real API, which it never
        writes to (#1399); with the local fake it is admitted (#1884).

        ``on_timeout(limit, elapsed)`` records a request killed at its
        deadline (``source.transport.source_timeout_recorder``), so every
        such kill is logged with what, the limit and the elapsed time. It
        is required: a publication request is never killed unlogged.
        """
        if not callable(on_timeout):
            raise TypeError("ParishSoft publication requires a timeout recorder.")

        def before_request(seconds):
            """No provider call inside a transaction or after lost ownership."""
            if connection.in_atomic_block:
                raise StorageInvariantError(
                    "ParishSoft publication cannot call out inside a transaction."
                )
            execution.check()

        session = BoundedPublicationSession(
            before_request=before_request,
            check=execution.check,
            profile=profile,
            base_url=base_url,
            on_timeout=on_timeout,
        )
        session.headers["x-api-key"] = credential.api_key
        return cls(session, profile=profile)

    @classmethod
    def open_operator(cls, credential, *, profile, base_url, on_timeout):
        """Build the gateway for the human-run contact smoke check.

        No task owns that run, so there is no ownership to check; every call
        is still refused inside a transaction, and a timeout recorder is
        still required.
        """
        return cls.open(
            SimpleNamespace(check=lambda: None),
            credential,
            profile=profile,
            base_url=base_url,
            on_timeout=on_timeout,
        )

    def read_member_contact(self, member_id, *, timeout):
        """Refused until a GET returning all fifteen contact fields is confirmed.

        Only the local fake's read is defined (#1884): its ``GET members/{id}``
        Member object mapped onto the request schema's names. The fake's base
        URL is admitted only for a LOCAL profile (checked when the gateway
        was built), so Production always refuses here.
        """
        member_id = _member_id(member_id)
        if self.base_url != LOCAL_SOURCE_BASE_URL:
            raise ContactGatewayError("read_unconfirmed")
        # Lazy: only a LOCAL deployment reading the fake ever needs it.
        from parishkit.stewardship.local.fake_contact import contact_record

        record = self._member_object(member_id, timeout)
        if record is None:
            return None
        try:
            return contact_record(record)
        except ValueError:
            raise ContactGatewayError("read_unavailable") from None

    def probe_member_record(self, member_id, *, timeout):
        """One Member's raw ``GET members/{id}`` record, or None when not found.

        Only the contact smoke check calls this, to find out whether that
        record carries the fifteen PUT fields; it is not the publication
        read. Failures are fixed words, never provider text.
        """
        return self._member_object(_member_id(member_id), timeout)

    def _member_object(self, member_id, timeout):
        """``GET members/{id}`` as a dict, or None when not found.

        Shared by the smoke check's probe and the local fake's contact read;
        a failed or malformed answer is ``read_unavailable``.
        """
        url = f"{self.base_url}/members/{member_id}"
        try:
            response = self.session.get(url, timeout=timeout)
            if response.status_code == 404:
                return None
            body = response.json() if 200 <= response.status_code < 300 else None
        except (SourceTransportError, ValueError):
            raise ContactGatewayError("read_unavailable") from None
        if type(body) is not dict:
            raise ContactGatewayError("read_unavailable")
        return body

    def write_member_contact(self, member_id, payload, *, timeout):
        """Send the one Member contact PUT; any non-2xx answer is a failure."""
        url = f"{self.base_url}/members/{_member_id(member_id)}/contact"
        try:
            response = self.session.put(url, json=payload, timeout=timeout)
        except (SourceTransportError, ValueError):
            raise ContactGatewayError("write_unavailable") from None
        if 200 <= response.status_code < 300:
            return
        # A 4xx says ParishSoft refused this payload; anything else may pass.
        raise ContactGatewayError(
            "write_rejected"
            if 400 <= response.status_code < 500
            else "write_unavailable"
        )

    def close(self):
        """Stop the helper and drop the key."""
        self.session.close()
