"""ParishSoft Member contact reads and writes for census publication (#528).

BG-09's publish worker (``responses.publication_worker``) reads one Member's
full contact record right before each PUT and again after it, and writes it
with ParishSoft's v2 ``PUT /api/v2/members/{id}/contact``. It talks to a
``ContactGateway``: ``read_member_contact`` returns the record keyed by the
request schema's fields (``publication.MEMBER_CONTACT_FIELDS``), leaving out
any field the read lacks, or None when the Member is not there;
``write_member_contact`` sends one full payload. Both raise
``ContactGatewayError`` with a fixed word, never provider text.

CI uses an in-memory fake only. ``ParishSoftContactGateway`` is the real
one. Its write is implemented, through ``BoundedPublicationSession``, the
one transport whose helper admits that PUT. Its read is not: which
ParishSoft GET returns all fifteen Member contact fields is unconfirmed, so
it refuses with ``read_unconfirmed``. The worker reads before every write,
so the real gateway can never reach its PUT until a confirmed read replaces
that refusal and the human-run smoke test has written one test Member and
read it back (docs/guides/stewardship-member-requests.md#parishsoft-publication).

That smoke test (``responses.contact_smoke``) is the one other user of this
gateway. It opens it with ``open_operator`` (no task owns a human's run) and
reads the candidate record with ``probe_member_record``, which returns the
raw ``GET members/{id}`` answer; the worker never calls it.
"""

from types import SimpleNamespace
from typing import Protocol

from django.db import connection

from parishkit.parishsoft_http_worker import admitted_base_url
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
        """Replace one Member's contact record with ``payload``."""


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
        """Bind one publication session and the profile's one base URL."""
        if not isinstance(session, BoundedPublicationSession):
            raise TypeError("ParishSoft publication requires its own session.")
        self.session = session
        self.base_url = admitted_base_url(profile)

    @classmethod
    def open(cls, execution, credential, *, profile, on_timeout):
        """Build the gateway for one running publish task.

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
            on_timeout=on_timeout,
        )
        session.headers["x-api-key"] = credential.api_key
        return cls(session, profile=profile)

    @classmethod
    def open_operator(cls, credential, *, profile, on_timeout):
        """Build the gateway for the human-run contact smoke check.

        No task owns that run, so there is no ownership to check; every call
        is still refused inside a transaction, and a timeout recorder is
        still required.
        """
        return cls.open(
            SimpleNamespace(check=lambda: None),
            credential,
            profile=profile,
            on_timeout=on_timeout,
        )

    def read_member_contact(self, member_id, *, timeout):
        """Refused until a GET returning all fifteen contact fields is confirmed."""
        _member_id(member_id)
        raise ContactGatewayError("read_unconfirmed")

    def probe_member_record(self, member_id, *, timeout):
        """One Member's raw ``GET members/{id}`` record, or None when not found.

        Only the contact smoke check calls this, to find out whether that
        record carries the fifteen PUT fields; it is not the publication
        read. Failures are fixed words, never provider text.
        """
        url = f"{self.base_url}/members/{_member_id(member_id)}"
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
