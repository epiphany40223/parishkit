"""Browser tests must remain collectable with only baseline dependencies."""

import os
import subprocess
import sys
from pathlib import Path


def test_browser_collection_without_playwright():
    """Simulate an absent optional dependency even in a full developer environment."""
    script = """
import sys
import pytest
sys.modules['playwright'] = None
sys.modules['playwright.sync_api'] = None
raise SystemExit(pytest.main([
    '--ds=parishkit.stewardship.settings.test', '--collect-only', '-q',
    'tests/stewardship/browser',
]))
"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COVERAGE_", "COV_CORE_"))
    }
    environment.pop("PARISHKIT_RUN_BROWSER_TESTS", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_family_response.py::" in result.stdout
