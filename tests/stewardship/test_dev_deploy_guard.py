"""The pre-launch dev deploy refuses a deployment already in Production (#326).

The script has no harness that could run it against a host, so this pins
the guard's query and its place: before the build, the push and the first
service stop, and exiting on anything but a clear "not activated".
"""

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
    # The refusal names where a Production upgrade is described.
    assert "stewardship-deployment-runbook.md#upgrade" in text[refusal:]


def test_the_writer_guard_check_runs_before_anything_changes():
    """A host without the #293 writer guard is refused before any step."""
    text = SCRIPT.read_text()
    check = text.index("tgname='stewardship_operational_log_writer_v1'")
    refusal = text.index('if [ "$guard" != 1 ]; then', check)
    assert "exit 1" in text[refusal : text.index("\nfi\n", refusal)]
    for step in ("docker build", "docker push", '"${dc[@]}" stop'):
        assert check < text.index(step), step
