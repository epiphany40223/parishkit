"""Real pinned Valkey ACL and Kombu transport, using only disposable synthetic data."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError, NoPermissionError
from redis.retry import Retry

from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
from parishkit.stewardship.jobs.broker import build_broker, publish_hint
from parishkit.stewardship.jobs.queues import BROKER_PREFIX, WorkQueue
from parishkit.stewardship.jobs.scanning import ExecutionHint
from parishkit.stewardship.runtime_topology import VALKEY_IMAGE
from parishkit.stewardship.runtime_valkey import server_acl

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)
PASSWORDS = {
    role: f"synthetic-{role.value}-password".encode()
    for role in (
        ServiceRole.WEB,
        ServiceRole.SCHEDULER,
        ServiceRole.WORKER,
    )
}


@pytest.fixture(scope="module")
def valkey_endpoint(tmp_path_factory):
    """Launch one fresh named server; never modify an existing deployment or ACL."""
    directory = tmp_path_factory.mktemp("broker-valkey")
    acl = directory / "server.acl"
    acl.write_bytes(server_acl(PASSWORDS))
    acl.chmod(0o600)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--pull=never",
            "--name",
            "parishkit-broker-test-" + uuid4().hex,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/data:rw,nosuid,nodev,noexec,mode=1777",
            "--publish",
            "127.0.0.1::6379",
            "--mount",
            f"type=bind,source={acl},target=/fixture.acl,readonly",
            VALKEY_IMAGE,
            "valkey-server",
            "--aclfile",
            "/fixture.acl",
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "The disposable pinned Valkey container could not start."
    )
    identifier = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", identifier)
    try:
        result = subprocess.run(
            ["docker", "port", identifier, "6379/tcp"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        port = int(result.stdout.strip().rsplit(":", 1)[1])
        endpoint = ValkeyConfiguration("127.0.0.1", port, 0, None)
        client = connection(endpoint, ServiceRole.WORKER)
        deadline = time.monotonic() + 10
        try:
            while True:
                try:
                    assert client.ping()
                    break
                except ConnectionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        finally:
            client.close()
        yield endpoint
    finally:
        # The exact successful docker-run ID belongs solely to this fixture.
        subprocess.run(
            ["docker", "rm", "--force", identifier],
            capture_output=True,
            timeout=30,
            check=True,
        )


def connection(endpoint, role):
    """A finite, per-service host client with no credential-bearing URL."""
    return Redis(
        host=endpoint.host,
        port=endpoint.port,
        username=role.value,
        password=PASSWORDS[role],
        socket_connect_timeout=1,
        socket_timeout=1,
        retry=Retry(NoBackoff(), 0),
    )


def test_real_transport_can_publish_consume_and_ack_one_uuid(valkey_endpoint):
    """The narrowed ACL supports actual Kombu operations, not just a guessed list."""
    producer = build_broker(
        endpoint=valkey_endpoint,
        password=PASSWORDS[ServiceRole.SCHEDULER].decode(),
        service=ServiceRole.SCHEDULER,
        handlers={},
    )
    consumer = build_broker(
        endpoint=valkey_endpoint,
        password=PASSWORDS[ServiceRole.WORKER].decode(),
        service=ServiceRole.WORKER,
        handlers={},
    )
    hint = ExecutionHint(uuid4(), WorkQueue.GENERAL)
    try:
        publish_hint(producer, hint)
        with consumer.app.connection_for_read() as channel_connection:
            queue = consumer.app.amqp.queues[WorkQueue.GENERAL.value](
                channel_connection
            )
            message = queue.get(no_ack=False)
            assert message is not None and message.payload[0] == [str(hint.run_id)]
            assert message.payload[1] == {}
            # Exercise the mutex/Lua and watch/transaction restoration paths as
            # well as ordinary ACK; both must remain within the worker's keys.
            message.channel.qos.restore_visible(interval=1)
            message.reject(requeue=True)
            repeated = queue.get(no_ack=False)
            assert repeated.payload[0] == [str(hint.run_id)]
            repeated.ack()
    finally:
        producer.app.close()
        consumer.app.close()


@pytest.mark.parametrize(
    "role,command",
    [
        (ServiceRole.WORKER, ("GET", "stewardship:auth:v1:synthetic")),
        (ServiceRole.WORKER, ("RPOP", BROKER_PREFIX + "mail-dispatch")),
        (ServiceRole.WORKER, ("FLUSHDB",)),
        (ServiceRole.SCHEDULER, ("RPOP", BROKER_PREFIX + "general")),
        (ServiceRole.SCHEDULER, ("DEL", BROKER_PREFIX + "general")),
        (ServiceRole.WEB, ("GET", BROKER_PREFIX + "general")),
    ],
)
def test_real_acl_denies_cross_service_commands(valkey_endpoint, role, command):
    """Server authorization remains effective even when client routing is bypassed."""
    client = connection(valkey_endpoint, role)
    try:
        with pytest.raises(NoPermissionError):
            client.execute_command(*command)
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["consume", "idle"])
def test_actual_worker_uses_closed_queues_and_drains_after_work_or_sigterm(
    valkey_endpoint, mode
):
    """Exercise Celery startup/consumption/shutdown against the real narrowed ACL."""
    identifier = uuid4()
    if mode == "consume":
        producer = build_broker(
            endpoint=valkey_endpoint,
            password=PASSWORDS[ServiceRole.SCHEDULER].decode(),
            service=ServiceRole.SCHEDULER,
            handlers={},
        )
        try:
            publish_hint(producer, ExecutionHint(identifier, WorkQueue.GENERAL))
        finally:
            producer.app.close()
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("runtime_broker_probe.py"))],
        input=json.dumps(
            {"port": valkey_endpoint.port, "expected": str(identifier), "mode": mode}
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": 0,
        "ready": True,
        "seen": [str(identifier)] if mode == "consume" else [],
    }
