"""Bind finite shared HTTP reads to live task/source fences and closed SQL state."""

from uuid import UUID

from django.db import connection, connections

from parishkit.parishsoft_transport import BoundedSourceSession
from parishkit.stewardship.deployment import recorded_profile
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import verify_refresh_attempt
from .credentials import SourceCredential
from .errors import SourceCredentialChanged, SourceScopeChanged, local_read_admission
from .leases import SourceClaim, reserve_source_request


def runtime_profile():
    """The deployment profile this process was admitted under.

    Source reads use it to choose the ParishSoft base URL (the fake in LOCAL)
    and to label every helper request, so the environment-free helper can
    apply the same rule. It is the one shared reader,
    ``deployment.recorded_profile``, which the mail parents use too.
    """
    return recorded_profile()


def source_session(execution, claim, *, attempt_id, credential):
    """Create a read transport for this exact execution and maintained source lease.

    The caller enters maintain_execution/maintain_source before using the Session.
    Each shared-client retry repeats admission and reserves its own read/drain
    deadline. Connection cleanup is thread-local and leaves the independent
    renewal thread free to heartbeat throughout the provider wait.
    """
    if (
        not isinstance(execution, Execution)
        or not isinstance(claim, SourceClaim)
        or (claim.task_id, claim.task_fence, claim.worker_id)
        != (execution.claim.run_id, execution.claim.fence, execution.claim.worker_id)
    ):
        raise ValueError("Source transport requires its exact owning execution.")
    if not isinstance(attempt_id, UUID) or not isinstance(credential, SourceCredential):
        raise TypeError("Source transport requires a bound attempt and credential.")

    @local_read_admission
    def before_request(seconds):
        """Never close the caller's transaction or perform HTTP inside it."""
        if connection.in_atomic_block:
            raise StorageInvariantError("Source HTTP cannot run inside a transaction.")
        try:
            with execution.effect():
                if (
                    not execution.control.active
                    or execution.control.source_claim != claim
                ):
                    raise StorageInvariantError(
                        "Source HTTP requires maintained task/source ownership."
                    )
                attempt = verify_refresh_attempt(attempt_id, execution, claim)
                if attempt.snapshot.state != "staging":
                    raise SourceScopeChanged("Source HTTP observation is stale.")
                if (
                    attempt.credential_fingerprint != credential.fingerprint
                    or session.headers.get("x-api-key") != credential.api_key
                ):
                    raise SourceCredentialChanged(
                        "The source HTTP credential is stale."
                    )
                reserve_source_request(
                    claim, timeout_seconds=seconds, safety_seconds=15
                )
        finally:
            connections.close_all()

    session = BoundedSourceSession(
        before_request=before_request,
        check=execution.check,
        profile=runtime_profile().value,
        on_timeout=source_timeout_recorder(execution),
    )
    session.headers["x-api-key"] = credential.api_key
    return session


def source_timeout_recorder(execution):
    """Record that a ParishSoft request was stopped at its deadline (#293).

    Called just after the read helper is killed. The entry names the task, the
    request's time limit and how long it ran, on a private connection, so the
    caller's no-SQL-during-provider-reads rule is unaffected.
    """

    def record(limit, elapsed):
        """Write one helper_timed_out entry for this task."""
        from parishkit.stewardship.audit.timeouts import record_timeout
        from parishkit.stewardship.observability import Event

        record_timeout(
            Event.HELPER_TIMED_OUT,
            what="source_helper",
            helper="parishsoft_http_worker",
            task_id=execution.claim.run_id,
            limit_seconds=limit,
            elapsed_seconds=elapsed,
        )

    return record
