"""Execute hardened stock Caddy against only the fixture's synthetic local CA."""

import json
import subprocess
import time


def inspect_service(file, project, service, run):
    """Inspect only the exact container resolved by this UUID Compose project."""
    identifier = run(file, project, "ps", "-q", service).stdout.strip()
    result = subprocess.run(
        ["docker", "inspect", identifier],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return json.loads(result.stdout)[0]


def check_ingress(file, project, configuration, run):
    """Prove live route/privacy/network/filesystem and persistent certificate state."""
    run(file, project, "up", "--detach", "caddy")
    probe = (
        "import http.client, json, socket, ssl, sys\n"
        "context = ssl._create_unverified_context()\n"
        "connection = context.wrap_socket(socket.create_connection((sys.argv[1], 8443),"
        " timeout=3), server_hostname='parish.example')\n"
        "connection.sendall(('GET ' + sys.argv[2] + ' HTTP/1.1\\r\\n'"
        " 'Host: parish.example\\r\\nAuthorization: Bearer private-header-canary\\r\\n'"
        " 'X-Forwarded-For: 192.0.2.99, 198.51.100.99\\r\\n'"
        " 'Cookie: private-cookie-canary\\r\\nConnection: close\\r\\n\\r\\n')"
        ".encode())\n"
        "response = http.client.HTTPResponse(connection)\nresponse.begin()\n"
        "print(json.dumps({'status': response.status, "
        "'body': response.read().decode()}))\n"
    )

    def request(path, *, check=True):
        """Connect from web's namespace using the synthetic hostname and SNI."""
        # Only the disposable locally issued certificate uses an unverified client;
        # production trust/TLS configuration is unchanged by this fixture.
        return run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            probe,
            configuration.runtime_network.caddy,
            path,
            check=check,
        )

    deadline = time.monotonic() + 30
    while True:
        result = request("/health/live", check=False)
        if result.returncode == 0:
            break
        if time.monotonic() >= deadline:
            logs = run(file, project, "logs", "caddy")
            raise AssertionError(
                "Synthetic ingress failed: " + logs.stdout + logs.stderr
            )
        time.sleep(0.5)
    for path in ("/health/live", "/health/ready", "/metrics", "/metrics/private"):
        assert json.loads(request(path).stdout)["status"] == 404
    response = json.loads(request("/admin/login?private-query-canary").stdout)
    assert response["status"] in {200, 302}
    assert json.loads(request("/private-path-canary").stdout)["status"] == 404
    assert json.loads(request("/static/synthetic.txt").stdout) == {
        "status": 200,
        "body": "Synthetic static fixture",
    }
    inspected = inspect_service(file, project, "caddy", run)
    assert inspected["Config"]["User"] == "10001:10001"
    host = inspected["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert host["Init"] is True
    assert host["CapDrop"] == ["ALL"]
    assert [value.removeprefix("CAP_") for value in host["CapAdd"]] == [
        "NET_BIND_SERVICE"
    ]
    assert "no-new-privileges:true" in host["SecurityOpt"]
    redirect = run(
        file,
        project,
        "exec",
        "-T",
        "web",
        "python",
        "-c",
        "import http.client, json, sys\n"
        "client=http.client.HTTPConnection(sys.argv[1],8080,timeout=3)\n"
        "client.request('GET','/admin/login',headers={'Host':'parish.example'})\n"
        "response=client.getresponse()\n"
        "print(json.dumps({'status':response.status,"
        "'location':response.getheader('Location')}))\n",
        configuration.runtime_network.caddy,
    )
    assert json.loads(redirect.stdout) == {
        "status": 308,
        "location": "https://parish.example/admin/login",
    }
    boundaries = run(
        file,
        project,
        "exec",
        "-T",
        "caddy",
        "sh",
        "-c",
        "test $(id -u) = 10001 && test $(id -g) = 10001 && "
        "touch /data/write-probe /config/write-probe && "
        "! touch /etc/forbidden-probe && ! touch /srv/static/forbidden-probe && "
        "awk '/^NoNewPrivs:/ {if ($2 != 1) exit 1; found=1} "
        "END {if (!found) exit 1}' /proc/self/status",
    )
    assert boundaries.returncode == 0
    # Prove the connectivity tool and flags work before treating a nonzero exit
    # as network denial; an unavailable command is not isolation evidence.
    run(
        file,
        project,
        "exec",
        "-T",
        "caddy",
        "nc",
        "-z",
        "-w",
        "1",
        configuration.runtime_network.web(0),
        "8000",
    )
    for service, port in (("postgres", "5432"), ("valkey", "6379")):
        target = inspect_service(file, project, service, run)
        addresses = target["NetworkSettings"]["Networks"]
        assert len(addresses) == 1
        address = next(iter(addresses.values()))["IPAddress"]
        denied = run(
            file,
            project,
            "exec",
            "-T",
            "caddy",
            "nc",
            "-z",
            "-w",
            "1",
            address,
            port,
            check=False,
        )
        assert denied.returncode != 0
        assert denied.returncode != 127
    run(file, project, "stop", "--timeout", "10", "web")
    assert (
        json.loads(request_from_caddy_host(file, project, configuration, run).stdout)[
            "status"
        ]
        == 502
    )
    run(file, project, "up", "--detach", "web")
    certificate = run(
        file,
        project,
        "exec",
        "-T",
        "caddy",
        "sha256sum",
        "/data/caddy/pki/authorities/local/root.crt",
    ).stdout
    original_logs = run(file, project, "logs", "caddy")
    run(file, project, "up", "--detach", "--force-recreate", "caddy")
    assert (
        run(
            file,
            project,
            "exec",
            "-T",
            "caddy",
            "sha256sum",
            "/data/caddy/pki/authorities/local/root.crt",
        ).stdout
        == certificate
    )
    for directory in ("data", "config"):
        run(
            file,
            project,
            "exec",
            "-T",
            "caddy",
            "test",
            "-f",
            f"/{directory}/write-probe",
        )
    logs = run(file, project, "logs", "caddy")
    for marker in ("private-header", "private-cookie", "private-query", "private-path"):
        assert marker not in (
            original_logs.stdout + original_logs.stderr + logs.stdout + logs.stderr
        )


def request_from_caddy_host(file, project, configuration, run):
    """Observe a real proxy error from a one-off web-network client, not the app."""
    probe = (
        "import http.client,json,socket,ssl,sys\n"
        "context=ssl._create_unverified_context()\n"
        "connection=context.wrap_socket(socket.create_connection((sys.argv[1],8443),"
        "timeout=10),server_hostname='parish.example')\n"
        "connection.sendall(b'GET /private-path-canary?private-query-canary "
        "HTTP/1.1\\r\\n'"
        "b'Host: parish.example\\r\\nCookie: private-cookie-canary\\r\\n'"
        "b'Connection: close\\r\\n\\r\\n')\n"
        "response=http.client.HTTPResponse(connection)\nresponse.begin()\n"
        "print(json.dumps({'status':response.status}))\n"
    )
    return run(
        file,
        project,
        "run",
        "--rm",
        "--entrypoint",
        "python",
        "web",
        "-c",
        probe,
        configuration.runtime_network.caddy,
    )
