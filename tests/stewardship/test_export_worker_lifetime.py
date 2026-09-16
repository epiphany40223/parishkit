"""A stalled background renderer must stop, not outlive its campaign barrier."""

import os
import subprocess
import sys


def test_render_abort_terminates_only_isolated_worker_process():
    """Exercise the real hard-stop in a child; never kill the pytest owner."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django; django.setup(); "
            "from parishkit.stewardship.reports.export_tasks "
            "import _abort_render_worker; _abort_render_worker(); "
            "raise RuntimeError('unreachable')",
        ],
        env=os.environ
        | {"DJANGO_SETTINGS_MODULE": "parishkit.stewardship.settings.test"},
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 70, result.stderr.decode()
    assert result.stdout == b""
