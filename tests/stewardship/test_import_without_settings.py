"""Commands start before Django settings exist (#633 regression).

``pk-stewardship`` imports the deployment and operational policy modules,
and through them ``jobs.operational_content``, before any settings module is
configured. A module-level call that reads Django settings (such as Django's
``gettext_noop``, which reads ``USE_I18N``) made every command and service
crash at import. A fresh interpreter, with no settings module and this tree
first on its path, must import the command line and print its help.
"""

import os
import subprocess
import sys
from pathlib import Path

import parishkit

SOURCE = Path(parishkit.__file__).resolve().parents[1]


def test_the_command_line_imports_and_runs_without_django_settings():
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DJANGO_SETTINGS_MODULE", "PYTHONPATH"}
    }
    env["PYTHONPATH"] = str(SOURCE)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from parishkit.stewardship.cli import main; "
            "sys.exit(main(['--help']))",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "ImproperlyConfigured" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()
