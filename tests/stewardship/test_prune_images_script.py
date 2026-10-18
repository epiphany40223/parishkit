"""Pruning old release images after a scripted upgrade (#1661).

`tools/stewardship-prune-images.sh` runs on the host against Docker, so
these tests run it with a stand-in `docker` (a small Python program over a
JSON state file) that answers the few commands the script uses and records
every call. They pin which images are kept (any container's, the protected
release and rollback target, the newest N, anything of another repository),
that a dry run removes nothing, that a failure is reported without
stopping the rest, and that a failed lookup of what to keep stops it. The
local half of `tools/stewardship-upgrade.sh` is run with a recording `ssh`
to pin that it prunes only after a successful run, protects the replaced
release, and never fails because pruning did.
"""

import json
import os
import subprocess
import sys
import textwrap

from tests.stewardship.test_upgrade_script import (
    OFFICIAL_REPO,
    PRUNE,
    SCRIPT,
    UUID,
    executable_stub,
)

REPO = "ghcr.io/example/stewardship"
OTHER = "docker.io/library/postgres"

FAKE_DOCKER = textwrap.dedent(
    '''
    """A stand-in docker CLI for the prune script's tests."""
    import json, os, sys

    state_path = os.environ["FAKE_DOCKER_STATE"]
    with open(state_path) as f:
        state = json.load(f)
    with open(os.environ["FAKE_DOCKER_CALLS"], "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
    args = sys.argv[1:]

    def refs(image):
        """Every reference an image carries, tags then digests."""
        return image["tags"] + image["digests"]

    def name(ref):
        """The repository part of a tag or digest reference."""
        if "@" in ref:
            return ref.split("@")[0]
        head, _, tail = ref.rpartition(":")
        return head if head and "/" not in tail else ref

    def find(ref):
        """The image a reference or ID names, or None."""
        for image in state["images"]:
            if ref == image["id"] or ref in refs(image):
                return image
        return None

    if args[:2] == ["info", "--format"]:
        print(state["root"])
    elif args[:1] == ["ps"]:
        if state.get("ps_fail"):
            sys.exit(1)
        for container in state["containers"]:
            print(container["id"])
    elif args[:2] == ["inspect", "--format"]:
        by_id = {c["id"]: c["image"] for c in state["containers"]}
        for cid in args[3:]:
            print(args[2].replace("{{.Image}}", by_id[cid]))
    elif args[:3] == ["image", "inspect", "--format"]:
        if args[4] in state.get("inspect_fail", []):
            print("Error response from daemon: i/o timeout", file=sys.stderr)
            sys.exit(1)
        image = find(args[4])
        if image is None:
            print("Error: No such image: " + args[4], file=sys.stderr)
            sys.exit(1)
        if image is None:
            print("Error: No such image: " + args[4], file=sys.stderr)
            sys.exit(1)
        if args[3] == "{{.Id}}":
            print(image["id"])
        else:
            print(
                image["created"], image["id"], image["size"],
                ",".join(image["tags"]) + "," + ",".join(image["digests"]),
            )
    elif args[:2] == ["image", "ls"]:
        wanted = args[args.index("--filter") + 1].removeprefix("reference=")
        for image in state["images"]:
            if any(name(ref) == wanted for ref in refs(image)):
                print(image["id"])
    elif args[:2] == ["image", "rm"]:
        image = find(args[2])
        used = any(c["image"] == args[2] for c in state["containers"])
        if image is None or used or args[2] in state.get("rm_fail", []):
            print("Error response from daemon: conflict: unable to remove",
                  file=sys.stderr)
            sys.exit(1)
        state["images"].remove(image)
        with open(state_path, "w") as f:
            json.dump(state, f)
        print("Deleted: " + args[2])
    elif args[:3] == ["builder", "prune", "--force"]:
        if state.get("builder_fail"):
            print("Error: builder unavailable", file=sys.stderr)
            sys.exit(1)
        print("Total reclaimed space: 3.1GB")
    else:
        print("fake docker: unexpected " + " ".join(args), file=sys.stderr)
        sys.exit(99)
    '''
)


def image(n, created, repo=REPO, tags=(), size=380 * 1048576):
    """One fake image: ID n, its creation time and its references."""
    image_id = "sha256:" + f"{n:x}" * 64
    return {
        "id": image_id[:71],
        "created": created,
        "size": size,
        "tags": list(tags),
        "digests": [f"{repo}@sha256:" + f"{n:x}" * 64],
    }


def ids(images):
    """The IDs of the given fake images."""
    return {i["id"] for i in images}


def run_prune(tmp_path, images, *args, containers=(), **extra):
    """Run the prune script with the stand-in docker.

    Returns (result, docker calls, the image IDs left on the fake host).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    fake = tmp_path / "fake_docker.py"
    fake.write_text(FAKE_DOCKER)
    executable_stub(bin_dir, "docker", f'exec "{sys.executable}" -I "{fake}" "$@"')
    state = tmp_path / "state.json"
    calls = tmp_path / "docker.calls"
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "images": images,
                "containers": [
                    {"id": f"c{n}" * 8, "image": image_id}
                    for n, image_id in enumerate(containers)
                ],
                **extra,
            }
        )
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state),
        "FAKE_DOCKER_CALLS": str(calls),
        "STEWARDSHIP_LOG_DIR": str(logs),
    }
    result = subprocess.run(
        ["bash", str(PRUNE), "--repo", REPO, *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    left = {i["id"] for i in json.loads(state.read_text())["images"]}
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, recorded, left


def releases():
    """Eight release images, oldest first, created a day apart."""
    return [image(n, f"2026-10-{10 + n:02d}T03:00:00.{n}Z") for n in range(1, 9)]


def test_keeps_running_stopped_protected_newest_and_other_repositories(tmp_path):
    """Only unprotected older release images of the repository are removed."""
    rel = releases()
    running, stopped, rollback = rel[7], rel[0], rel[1]
    other = image(9, "2020-01-01T00:00:00Z", repo=OTHER, tags=[f"{OTHER}:16"])
    shared = image(10, "2020-01-02T00:00:00Z", tags=[f"{OTHER}:mirror"])
    result, calls, left = run_prune(
        tmp_path,
        [*rel, other, shared],
        "--keep",
        "2",
        "--protect",
        rel[1]["digests"][0],
        "--protect",
        f"{REPO}@sha256:" + "f" * 64,
        containers=[running["id"], stopped["id"]],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Kept: the running and the stopped container's images, the protected
    # rollback target, the two newest others (6 and 7), the other
    # repository's image and the one shared with another repository.
    assert left == ids([running, stopped, rollback, rel[6], rel[5], other, shared])
    removed = [c.split()[2] for c in calls if c.startswith("image rm")]
    assert sorted(removed) == sorted(ids(rel[2:5]))
    assert "builder prune --force" in calls
    # Nothing but per-image removal and the build cache: no blanket prune,
    # no volume, no other repository listed.
    assert not any(
        "volume" in c or ("prune" in c and not c.startswith("builder")) for c in calls
    )
    assert [c for c in calls if c.startswith("image ls")] == [
        f"image ls --no-trunc --quiet --filter reference={REPO}"
    ]
    out = result.stdout
    assert "(used by a container)" in out
    assert f"(protected ({rel[1]['digests'][0]}))" in out
    assert "(one of the 2 newest)" in out
    assert f"also named outside {REPO}" in out
    assert "protected image not on this host" in out
    assert "build cache cleared: Total reclaimed space: 3.1GB" in out
    assert "==> Removed 3 image(s); freed" in out
    log = next((tmp_path / "logs").glob("stewardship-prune-*.log")).read_text()
    assert "removed" in log and "==> Removed 3 image(s)" in log


def test_the_default_keeps_four_newest_besides_the_running_release(tmp_path):
    """Without --keep, the running release plus the four newest others stay."""
    rel = releases()
    result, _, left = run_prune(tmp_path, rel, containers=[rel[7]["id"]])
    assert result.returncode == 0, result.stdout + result.stderr
    assert left == ids(rel[3:])


def test_a_dry_run_removes_nothing(tmp_path):
    """A dry run lists what would go and removes no image and no cache."""
    rel = releases()
    result, calls, left = run_prune(
        tmp_path, rel, "--dry-run", "--keep", "1", containers=[rel[7]["id"]]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert left == ids(rel)
    assert not any(c.startswith(("image rm", "builder")) for c in calls)
    assert result.stdout.count("would remove") == 6
    assert "would clear the build cache" in result.stdout
    assert "==> Removed 0 image(s)" in result.stdout


def test_the_build_cache_can_be_kept(tmp_path):
    """--keep-build-cache leaves the build cache alone."""
    result, calls, _ = run_prune(tmp_path, releases(), "--keep-build-cache")
    assert result.returncode == 0
    assert not any(c.startswith("builder") for c in calls)
    assert "build cache kept" in result.stdout


def test_a_failed_removal_is_reported_and_the_rest_continues(tmp_path):
    """One refused removal and a failed cache prune: exit 1, others removed."""
    rel = releases()
    result, _, left = run_prune(
        tmp_path,
        rel,
        "--keep",
        "1",
        containers=[rel[7]["id"]],
        rm_fail=[rel[3]["id"]],
        builder_fail=True,
    )
    assert result.returncode == 1
    assert left == ids([rel[7], rel[6], rel[3]])
    assert "WARNING: could not remove" in result.stdout
    assert "WARNING: could not clear the build cache" in result.stdout


def test_a_failed_lookup_removes_nothing(tmp_path):
    """When the containers cannot be listed, nothing is removed at all."""
    rel = releases()
    result, calls, left = run_prune(tmp_path, rel, "--keep", "1", ps_fail=True)
    assert result.returncode == 1
    assert left == ids(rel)
    assert not any(c.startswith(("image rm", "builder")) for c in calls)
    assert "WARNING: pruning stopped" in result.stdout


def test_a_failed_protect_lookup_removes_nothing(tmp_path):
    """Only "No such image" lets a protected image be skipped.

    A daemon error while resolving a protected image would silently drop
    that protection, so the run stops before any removal.
    """
    rel = releases()
    result, calls, left = run_prune(
        tmp_path,
        rel,
        "--keep",
        "1",
        "--protect",
        rel[1]["digests"][0],
        containers=[rel[7]["id"]],
        inspect_fail=[rel[1]["digests"][0]],
    )
    assert result.returncode == 1
    assert left == ids(rel)
    assert not any(c.startswith(("image rm", "image ls", "builder")) for c in calls)
    assert (
        f"WARNING: could not look up protected image {rel[1]['digests'][0]}: "
        "Error response from daemon: i/o timeout"
    ) in result.stdout
    assert "WARNING: pruning stopped" in result.stdout


def test_bad_options_are_usage_errors(tmp_path):
    """A keep count outside 1-99 or an unknown option touches nothing."""
    for n, args in enumerate((["--keep", "0"], ["--keep", "x"], ["--bogus"])):
        result, calls, _ = run_prune(tmp_path / str(n), releases(), *args)
        assert result.returncode == 2, args
        assert calls == [], args


# ---------------------------------------------------------------------------
# The local half: when and how it prunes.

REPLACED = f"{OFFICIAL_REPO}@sha256:" + "a" * 64
IMAGE = f"{OFFICIAL_REPO}@sha256:" + "d" * 64


def run_upgrade(tmp_path, *args, host_rc=0, prune_rc=0, env=None):
    """Run the local half with an ssh that answers as a finished host half.

    The first ssh call ends as a successful host half does, naming the
    replaced release; the second (the pruning) exits with prune_rc.
    """
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "ssh.calls"
    executable_stub(
        bin_dir,
        "ssh",
        f"""echo "ssh $*" >>"{calls}"; cat >/dev/null
n=$(wc -l <"{calls}")
if [ "$n" -eq 1 ]; then
    echo "    Record in the operators' notes. Replaced release: {REPLACED}"
    exit {host_rc}
fi
echo "pruned"; exit {prune_rc}""",
    )
    base = {k: v for k, v in os.environ.items() if not k.startswith("STEWARDSHIP_")}
    full = {
        **base,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STEWARDSHIP_HOST": "host.invalid",
        "STEWARDSHIP_UUID": UUID,
        "STEWARDSHIP_IMAGE": IMAGE,
        **(env or {}),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=full,
        text=True,
        capture_output=True,
        timeout=30,
    )
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, recorded


def test_a_successful_upgrade_prunes_keeping_both_releases(tmp_path):
    """The pruning protects the started and the replaced release."""
    for n, mode in enumerate(((), ("--rollback",))):
        result, calls = run_upgrade(tmp_path / str(n), *mode)
        assert result.returncode == 0, result.stderr
        assert len(calls) == 2
        assert (
            f'bash "$f" --repo {OFFICIAL_REPO} --keep 4 --protect {IMAGE} '
            f"--protect {REPLACED} ; rc="
        ) in calls[1]
        assert "Replaced release" in result.stdout and "pruned" in result.stdout


def test_the_pruning_settings_reach_the_host(tmp_path):
    """Keep count, dry run and the build cache switch become options."""
    result, calls = run_upgrade(
        tmp_path,
        env={
            "STEWARDSHIP_PRUNE": "dry-run",
            "STEWARDSHIP_KEEP_RELEASES": "7",
            "STEWARDSHIP_KEEP_BUILD_CACHE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert (
        f"--keep 7 --protect {IMAGE} --protect {REPLACED} --dry-run "
        "--keep-build-cache ; rc="
    ) in calls[1]
    result, calls = run_upgrade(tmp_path / "off", env={"STEWARDSHIP_PRUNE": "off"})
    assert result.returncode == 0 and len(calls) == 1


def test_a_failed_pruning_never_fails_the_upgrade(tmp_path):
    """The upgrade succeeded, so a pruning failure is only a warning."""
    result, calls = run_upgrade(tmp_path, prune_rc=1)
    assert result.returncode == 0
    assert len(calls) == 2
    assert "WARNING: pruning old release images failed" in result.stderr


def test_a_failed_upgrade_does_not_prune(tmp_path):
    """The host half's failure is the run's exit status, with no pruning."""
    result, calls = run_upgrade(tmp_path, host_rc=3)
    assert result.returncode == 3
    assert len(calls) == 1


def test_bad_pruning_settings_are_refused_before_ssh(tmp_path):
    """An unknown mode or keep count never reaches a host."""
    for n, env in enumerate(
        (
            {"STEWARDSHIP_PRUNE": "yes"},
            {"STEWARDSHIP_KEEP_RELEASES": "0"},
            {"STEWARDSHIP_KEEP_RELEASES": "100"},
        )
    ):
        result, calls = run_upgrade(tmp_path / str(n), env=env)
        assert result.returncode == 1, env
        assert calls == [], env
