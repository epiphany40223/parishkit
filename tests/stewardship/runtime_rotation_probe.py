"""Synthetic-only subprocess probes for the owned disposable Compose fixture.

The test sends this source to the relevant existing container. Each action uses
that service's actual restricted mounts and login. No operator SQL identity or
private handoff key is given to web, and no real provider is contacted.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4


def main():
    """Return only public handoff material, receipts and durable request state."""
    from parishkit.stewardship.deployment import load_deployment
    from parishkit.stewardship.operator_commands import configure_operator_database

    configuration = load_deployment(Path(sys.argv[1]))
    configure_operator_database(configuration)
    from parishkit.stewardship.accounts.credential_handoff import (
        PrivateHandoff,
        PublicHandoff,
    )
    from parishkit.stewardship.accounts.cryptography import Key, decode, encode
    from parishkit.stewardship.accounts.key_files import load_keyring, read_private
    from parishkit.stewardship.accounts.metrics_credentials import MetricsCredential
    from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
    from parishkit.stewardship.accounts.secret_requests import stage_secret_request
    from parishkit.stewardship.accounts.sessions import database_now

    action = sys.argv[2]
    if action == "public":
        ring = load_keyring(configuration.secrets["handoff_private"], "token_private")
        key = PrivateHandoff("metrics", ring.active).public().key
        print(json.dumps({"id": key.id, "key": encode(key.material)}))
    elif action == "stage":
        public = json.loads(sys.argv[3])
        handoff = PublicHandoff(
            "metrics", Key(public["id"], "active", decode(public["key"]))
        )
        prior = MetricsCredential.parse(read_private(configuration.secrets["metrics"]))
        candidate, identifier = MetricsCredential.generate(), uuid4()
        stage_secret_request(
            request_id=identifier,
            target="metrics",
            staging_reference=uuid4(),
            actor_id=uuid4(),
            reauthenticated_at=database_now() - timedelta(seconds=1),
            expires_at=database_now() + timedelta(minutes=5),
            expected_fingerprint=prior.receipt,
            correlation_id=uuid4(),
            required_consumers=("web",),
            sealed_candidate=handoff.seal(identifier, candidate.serialize()),
            candidate_fingerprint=candidate.receipt,
        )
        print(json.dumps({"request_id": str(identifier), "receipt": candidate.receipt}))
    elif action == "state":
        row = SecretReplacementRequest.objects.get(pk=UUID(sys.argv[3]))
        print(json.dumps({"state": row.state}))
    else:
        raise ValueError("Unknown synthetic probe action")


if __name__ == "__main__":
    main()
