"""Keep specification headings, work packages, and scenario ownership in sync."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from parishkit.config import load_yaml_config

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "docs/specs/stewardship"
PLANS = ROOT / "docs/plans/stewardship"


def collect_test_nodes(root):
    """Resolve exact pytest IDs without executing tests or inheriting selections."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COVERAGE_", "COV_CORE_"))
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "--collect-only",
            "--collection-manifest",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    prefix = "PARISHKIT_TEST_NODEIDS="
    manifests = [
        json.loads(line[len(prefix) :])
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    ]
    assert len(manifests) == 1 and manifests[0], "Missing test collection evidence"
    assert isinstance(manifests[0], list)
    assert all(isinstance(node, str) for node in manifests[0])
    return set(manifests[0])


def test_references_use_exact_collected_parameter_and_class_ids(tmp_path):
    """Fixtures, nested closures, and stale parameter IDs cannot satisfy a ref."""
    tests = tmp_path / "tests"
    tests.mkdir()
    shutil.copyfile(ROOT / "tests/conftest.py", tests / "conftest.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tests / "test_sample.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', ['one', 'two'])\n"
        "def test_top(value):\n"
        "    def test_renamed():\n"
        "        pass\n"
        "    raise AssertionError('collection must not execute tests')\n"
        "@pytest.fixture\n"
        "def test_fixture():\n"
        "    pass\n"
        "class TestGroup:\n"
        "    def test_method(self):\n"
        "        raise AssertionError('collection must not execute tests')\n"
    )
    assert collect_test_nodes(tmp_path) == {
        "tests/test_sample.py::test_top[one]",
        "tests/test_sample.py::test_top[two]",
        "tests/test_sample.py::TestGroup::test_method",
    }


def specification_headings(path):
    """Extract section headings outside fenced examples, retaining nested scope."""
    headings = []
    fence = None
    for line in path.read_text().splitlines():
        if line.startswith(("```", "~~~")):
            marker = line[:3]
            fence = None if fence == marker else marker if fence is None else fence
        elif fence is None and re.match(r"^#{2,6} ", line):
            headings.append(line.split(" ", 1)[1])
    return headings


def test_every_spec_section_package_and_scenario_has_an_owner():
    """New requirements cannot silently bypass the maintained acceptance map."""
    manifest = load_yaml_config(
        ROOT / "docs/development/stewardship-acceptance.yaml",
        required=True,
        reject_duplicate_keys=True,
    )
    assert manifest["schema_version"] == 1
    packages = set()
    for plan in PLANS.glob("*.md"):
        packages.update(
            re.findall(r"^### ([A-Z]+-\d{2}):", plan.read_text(), re.MULTILINE)
        )
    assert len(packages) == 69
    specs = {
        path.relative_to(SPECS).as_posix(): path for path in SPECS.rglob("spec.md")
    }
    assert set(manifest["sections"]) == specs.keys()
    owned = set()
    for name, path in specs.items():
        headings = specification_headings(path)
        assert len(headings) == len(set(headings)), (
            "disambiguate duplicate section titles"
        )
        mappings = manifest["sections"][name]
        assert mappings.keys() == set(headings), name
        for owners in mappings.values():
            assert owners and len(owners) == len(set(owners))
            assert set(owners) <= packages
            owned.update(owners)
    assert owned == packages
    operations = (SPECS / "operations/spec.md").read_text()
    scenarios = operations.split("## Acceptance scenarios\n", 1)[1].split("\n## ", 1)[0]
    numbers = {
        int(value) for value in re.findall(r"^(\d+)\. ", scenarios, re.MULTILINE)
    }
    assert manifest["scenarios"].keys() == numbers
    referenced_nodes = set()
    for scenario in manifest["scenarios"].values():
        assert scenario["owners"] and set(scenario["owners"]) <= packages
        assert scenario["status"] in {"planned", "partial", "complete"}
        tests = scenario["tests"]
        assert isinstance(tests, list) and len(tests) == len(set(tests))
        assert bool(tests) == (scenario["status"] != "planned")
        assert all(isinstance(node, str) for node in tests)
        referenced_nodes.update(tests)
    if referenced_nodes:
        missing = referenced_nodes - collect_test_nodes(ROOT)
        assert not missing, f"Uncollected scenario test references: {sorted(missing)}"
