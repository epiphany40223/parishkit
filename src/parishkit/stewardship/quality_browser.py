"""Run a browser CI partition only when pytest proves actual completion."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .quality_ci import environment
from .quality_sharding import BROWSER_ENGINES


def validate_receipt(path, engine):
    """A fresh receipt must bind the requested engine to all executed cases."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict or set(value) != {"engine", "selected", "executed"}:
            raise ValueError("Invalid receipt fields")
        selected = value["selected"]
        if (
            value["engine"] != engine
            or type(selected) is not list
            or not selected
            or not all(type(node) is str and node for node in selected)
            or sorted(set(selected)) != selected
            or selected != value["executed"]
        ):
            raise ValueError("Incomplete receipt")
    except (OSError, ValueError) as error:
        raise ValueError(
            "Browser partition did not produce valid completion evidence"
        ) from error
    return len(selected)


def run_engine(root, engine):
    """Keep stdout live; reject early zero exits that never load pytest hooks."""
    if engine not in BROWSER_ENGINES:
        raise ValueError("Unsupported browser engine")
    # Each invocation owns a new private directory outside the checkout; no
    # prior run's receipt can satisfy a --version/--help or other early exit.
    with tempfile.TemporaryDirectory(prefix="parishkit-browser-ci-") as directory:
        receipt = Path(directory) / "completion.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/stewardship/browser",
                f"--ci-browser-engine={engine}",
                f"--ci-browser-evidence={receipt}",
                "--require-no-skips",
                "--ci-progress",
                "--collection-manifest",
                "-o",
                "faulthandler_timeout=120",
                "--durations=10",
                "-q",
            ],
            cwd=root,
            env=environment() | {"PARISHKIT_RUN_BROWSER_TESTS": "1"},
            timeout=840,
            check=True,
        )
        count = validate_receipt(receipt, engine)
        print(f"CI_BROWSER_COMPLETE {engine}: {count:,} executed cases", flush=True)


def main():
    """Expose only the complete engine runner, with no arbitrary pytest options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=BROWSER_ENGINES, required=True)
    args = parser.parse_args()
    try:
        run_engine(Path.cwd(), args.engine)
    except (ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
