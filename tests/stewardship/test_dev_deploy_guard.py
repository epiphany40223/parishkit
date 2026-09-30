"""The pre-launch dev deploy refuses a deployment already in Production (#326).

The script has no harness that could run it against a host, so this pins
the guard's query and its place: before the build, the push and the first
service stop, and exiting on anything but a clear "not activated".
"""

import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "stewardship-dev-deploy.sh"


def test_the_production_guard_runs_before_anything_changes():
    """The activation check precedes every step that builds or stops."""
    text = SCRIPT.read_text()
    guard = text.index(
        "SELECT EXISTS (SELECT 1 FROM stewardship_production_request "
        "WHERE activated_at IS NOT NULL)"
    )
    refusal = text.index('if [ "$activated" != f ]; then', guard)
    assert "exit 1" in text[refusal : text.index("\nfi\n", refusal)]
    for step in ("docker build", "docker push", '"${dc[@]}" stop'):
        assert guard < text.index(step), step
    # The refusal names where a Production upgrade is described, but only
    # when the deployment is in Production: an unreadable answer (usually a
    # stopped database) says to start the database instead.
    production = text.index('if [ "$activated" = t ]; then', refusal)
    unreadable = text.index("    else\n", production)
    assert "stewardship-deployment-runbook.md#upgrade" in text[production:unreadable]
    rest = text[unreadable : text.index("\nfi\n", refusal)]
    assert "#upgrade" not in rest and "up --detach --wait postgres" in rest


def test_the_writer_guard_check_runs_before_anything_changes():
    """A host without the #293 writer guard is refused before any step."""
    text = SCRIPT.read_text()
    check = text.index("tgname='stewardship_operational_log_writer_v1'")
    refusal = text.index('if [ "$guard" != 1 ]; then', check)
    assert "exit 1" in text[refusal : text.index("\nfi\n", refusal)]
    for step in ("docker build", "docker push", '"${dc[@]}" stop'):
        assert check < text.index(step), step


def test_the_family_login_check_runs_before_anything_changes():
    """A host without the #306 Family login SQL is refused before any step."""
    text = SCRIPT.read_text()
    check = text.index("to_regprocedure('public.stewardship_family_login_v1(")
    assert "NOT has_table_privilege('pk_stewardship_web'" in text[check:]
    refusal = text.index('if [ "$family_login" != t ]; then', check)
    assert "exit 1" in text[refusal : text.index("\nfi\n", refusal)]
    for step in ("docker build", "docker push", '"${dc[@]}" stop'):
        assert check < text.index(step), step


# A stand-in for docker (and for the root-only `install -o`) that records
# every call and answers as a healthy, set-up deployment would. FAKE_NOOP is
# what the upgrade-check query answers; FAKE_RENDER_FAIL makes rendering
# it fail, FAKE_WEB_FAIL makes web never turn healthy, and FAKE_STOP_FAIL
# makes every `compose stop` fail.
FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
if [ -n "$FAKE_STOP_FAIL" ] && [[ "$*" == *" stop "* ]]; then
    echo "stop refused by the stand-in" >&2
    exit 1
fi
if [ -n "$FAKE_RENDER_FAIL" ] && [[ "$*" == *" upgrade-check "* ]]; then
    exit 2
fi
if [ -n "$FAKE_WEB_FAIL" ]; then
    case "$*" in
        *"up --detach --wait web" | *"exec -T web pk-stewardship health"*) exit 1 ;;
    esac
fi
case "$*" in
    *"stewardship_production_request"*) echo f ;;
    *"pg_trigger WHERE tgname"*) echo 1 ;;
    *"stewardship_family_login_v1"*) echo t ;;
    *"stewardship_setup_completion"*) echo t ;;
    inspect\ --format*) echo "$FAKE_REPO@sha256:0123" ;;
    inspect\ -f*) echo "caddy run" ;;
    "compose ls"*) echo '[{"Name":"stewardship","ConfigFiles":"'"$FAKE_COMPOSE"'"}]' ;;
    *" config --services" | *"ps --services --status running")
        printf '%s\n' postgres valkey web worker scheduler caddy ;;
    *"ps -q caddy") echo caddy-id ;;
    *" upgrade-check "*) echo "SELECT true;" ;;
    *"PGOPTIONS"*) cat >/dev/null; echo "$FAKE_NOOP" ;;
    *"exec -T caddy sha256sum"*) sha256sum "$FAKE_CADDYFILE" ;;
    *"ps --all --format json")
        for s in web worker scheduler caddy; do
            health=healthy
            [ -n "$FAKE_WEB_FAIL" ] && [ "$s" = web ] && health=unhealthy
            echo '{"Service":"'$s'","State":"running","Health":"'$health'"}'
        done ;;
esac
exit 0
"""


def run_remote(tmp_path, noop, *, status=0, compose=None, **fake):
    """Run the host-side half against the stand-in; return the docker calls.

    `fake` sets the stand-in's FAKE_* switches; `status` is the exit status
    the script must end with; `compose` is the rendered compose.json text.
    """
    import os
    import shutil
    import subprocess

    import pytest

    bash = shutil.which("bash")
    probe = subprocess.run([bash, "-c", "type mapfile"], capture_output=True)
    if probe.returncode != 0 or shutil.which("jq") is None:
        pytest.skip("the host-side script needs bash 4 or newer and jq")
    text = SCRIPT.read_text()
    begin = text.index("<<'REMOTE'\n") + len("<<'REMOTE'\n")
    remote = text[begin : text.rindex("REMOTE")]
    root = tmp_path / "root"
    services = root / "config" / "services"
    services.mkdir(parents=True)
    (services / "Caddyfile").write_text("caddy\n")
    if compose is not None:
        (services / "compose.json").write_text(compose)
    (root / "cache" / "static").mkdir(parents=True)
    (root / "cache" / "static" / "old.js").write_text("old")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "docker": FAKE_DOCKER,
        "install": '#!/usr/bin/env bash\nmkdir -p "${@: -1}"\n',
        # The health loop's retries need no real waiting here.
        "sleep": "#!/usr/bin/env bash\nexit 0\n",
    }.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "docker.log"
    script = tmp_path / "remote.sh"
    script.write_text(remote)
    result = subprocess.run(
        [
            bash,
            str(script),
            str(tmp_path / "build"),
            "ghcr.io/example/stewardship",
            "dev-tag",
            str(root),
            "stewardship",
            str(tmp_path / "deployment.yaml"),
            "00000000-0000-4000-8000-000000000000",
            "0",
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_NOOP": noop,
            "FAKE_REPO": "ghcr.io/example/stewardship",
            "FAKE_COMPOSE": str(services / "compose.json"),
            "FAKE_CADDYFILE": str(services / "Caddyfile"),
            **{"FAKE_" + name.upper(): "1" for name in fake},
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == status, result.stderr
    return log.read_text().splitlines(), result.stdout + result.stderr


def first(calls, fragment):
    """Index of the first recorded docker call containing the fragment."""
    return next(i for i, call in enumerate(calls) if fragment in call)


def test_downtime_holds_only_what_needs_web_stopped(tmp_path):
    """Image-only work precedes the stops, and web stops last and starts first."""
    calls, output = run_remote(tmp_path, "t")
    stop_background = first(calls, " stop worker scheduler")
    stop_web = first(calls, " stop web")
    for early in (" upgrade-check ", " collect-static "):
        assert first(calls, early) < stop_background, early
    # The backup comes after the drain, while web still serves, so a
    # database-restore rollback loses only what web accepted meanwhile.
    backup = first(calls, "backup-worker")
    assert stop_background < backup < stop_web < first(calls, " retarget-image ")
    # caddy keeps serving the maintenance page; it is never stopped.
    assert not any(" stop " in call and "caddy" in call for call in calls)
    # The no-op query runs only once everything online is stopped.
    assert stop_web < first(calls, "PGOPTIONS")
    assert not any(" migration" in call for call in calls)
    assert not any("database-grants" in call for call in calls)
    start_web = first(calls, "up --detach --wait web")
    start_rest = first(calls, "up --detach --wait worker scheduler")
    assert first(calls, " retarget-image ") < start_web < start_rest
    assert output.index("Web was down for") < output.index("Starting the background")
    assert "skipped: the upgrade check answered t" in output
    static = tmp_path / "root" / "cache"
    assert not (static / "static.next").exists()
    assert (static / "static.previous" / "old.js").exists()


def test_migration_and_grants_run_without_a_clear_no_op(tmp_path):
    """Anything but "t" from the check runs both commands, after retarget."""
    calls, _ = run_remote(tmp_path, "f")
    retarget = first(calls, " retarget-image ")
    migration = first(calls, "run --rm -T migration")
    assert retarget < migration < first(calls, "database-grants")
    assert migration < first(calls, "up --detach --wait web")


def test_the_check_answer_is_shown_when_both_commands_run(tmp_path):
    """The deploy log says why migration and grants ran."""
    _, output = run_remote(tmp_path, "f")
    assert "running both: the upgrade check answered: f" in output


def test_a_failed_render_runs_both_commands(tmp_path):
    """Without a rendered query there is no proof, so both commands run."""
    calls, output = run_remote(tmp_path, "t", render_fail=True)
    assert not any("PGOPTIONS" in call for call in calls)
    assert first(calls, "run --rm -T migration") < first(calls, "database-grants")
    assert "upgrade check could not be rendered" in output
    assert "answered: no query (the render failed)" in output


def test_web_that_never_turns_healthy_fails_the_deploy(tmp_path):
    """The background services still start, and the run fails naming web."""
    calls, output = run_remote(tmp_path, "t", status=1, web_fail=True)
    assert first(calls, "up --detach --wait web") < first(
        calls, "up --detach --wait worker scheduler"
    )
    assert "Web is still not healthy after" in output
    assert "Web was down for" not in output
    assert "web health check still failing" in output
    assert "web: running unhealthy" in output


def test_the_replaced_image_is_named_before_retargeting(tmp_path):
    """A failed deploy can be rolled back to the digest the log names (#309)."""
    old = "ghcr.io/example/stewardship@sha256:" + "a" * 64
    compose = json.dumps({"services": {"web": {"image": old}}})
    _, output = run_remote(tmp_path, "t", compose=compose)
    assert f"replacing {old}" in output
    assert output.index(f"replacing {old}") < output.index("Migration and grants")


def test_a_failed_stop_ends_the_deploy_and_says_why(tmp_path):
    """A stop error is shown, and nothing is retargeted after it (#309)."""
    calls, output = run_remote(tmp_path, "t", status=1, stop_fail=True)
    assert "stop refused by the stand-in" in output
    assert not any(" retarget-image " in call for call in calls)
