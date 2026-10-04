"""The vendored chart engine stays pinned and in step with vl-convert (#477).

The browser and the server must run the same Vega: each vendored bundle is
the file its SHA-256 pin says, the versions equal the ones the locked
vl-convert bundles, the template loads exactly the pinned files, and the
licence notices travel with them.
"""

import hashlib
import importlib.metadata
import re
from pathlib import Path

import pytest
import vl_convert
from django.template.loader import render_to_string

from parishkit.stewardship.reports.chart_assets import (
    VEGA,
    VEGA_EMBED,
    VEGA_LITE,
    VEGA_LITE_SCHEMA,
    VENDOR_DIRECTORY,
    VENDORED,
)

from .test_build import ROOT, locked_requirements

TEMPLATE = (
    ROOT
    / "src/parishkit/stewardship/accounts/templates/stewardship/components"
    / "chart-scripts.html"
)


@pytest.mark.parametrize("asset", VENDORED, ids=lambda asset: asset.package)
def test_vendored_bundle_matches_its_pin(asset):
    """The bytes on disk are the published build the pin names, unmodified."""
    data = asset.path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == asset.sha256
    assert asset.url.endswith(".min.js") and asset.version in asset.url
    assert asset.filename == f"{asset.package}-{asset.version}.min.js"
    # A UMD build: a classic script the CSP can load, not an ES module.
    assert data.startswith(b"!function(") and b"\nimport " not in data[:2000]
    assert asset.version.encode() in data


def test_vendor_directory_holds_only_pinned_files_and_their_licences():
    """Nothing unpinned ships from the vendor directory, and each notice is there."""
    assert sorted(path.name for path in VENDOR_DIRECTORY.iterdir()) == sorted(
        [*(asset.filename for asset in VENDORED), "LICENSES.txt"]
    )
    notices = (VENDOR_DIRECTORY / "LICENSES.txt").read_text()
    for asset in VENDORED:
        assert f"===== {asset.package} {asset.version} =====" in notices
    # The third-party packages the bundles include carry their own notices,
    # one per distinct text, each heading naming the packages that share it.
    bundled, _, third_party = notices.partition(
        "===== Third-party packages bundled in the files above ====="
    )
    assert bundled.count("Redistribution and use in source and binary forms") == 3
    headings = " ".join(re.findall(r"^----- (.+) -----$", third_party, re.M))
    for package in (
        *(f"d3-{name}" for name in ("array", "geo", "scale", "shape", "time")),
        "delaunator",
        "topojson-client",
        "fast-json-patch",
        "json-stringify-pretty-compact",
        "semver",
        "vega-tooltip",
    ):
        assert re.search(rf"(^|[ ,]){package}[ ,]", headings), package
    assert "Permission to use, copy, modify, and/or distribute" in third_party  # ISC
    assert "Permission is hereby granted, free of charge" in third_party  # MIT
    for ignore in (".dockerignore", "deploy/stewardship/Dockerfile.dockerignore"):
        assert (
            "!src/parishkit/stewardship/accounts/static/stewardship/vendor/LICENSES.txt"
            in (ROOT / ignore).read_text()
        )


def test_versions_match_the_locked_vl_convert():
    """vl-convert renders with the very releases the browser loads."""
    installed = importlib.metadata.version("vl-convert-python")
    pin = locked_requirements("stewardship.txt")["vl-convert-python"]
    assert str(pin.specifier) == f"=={installed}"
    assert VEGA.version == vl_convert.get_vega_version()
    assert VEGA_EMBED.version == vl_convert.get_vega_embed_version()
    # vl-convert names its bundled Vega-Lite releases by minor version and
    # renders a v6 schema with the newest 6.x it has; the vendored patch
    # release is checked by the bundle test above.
    newest = vl_convert.get_vegalite_versions()[-1]
    assert VEGA_LITE.version.rsplit(".", 1)[0] == newest
    assert newest.startswith("6.") and VEGA_LITE_SCHEMA.endswith("/vega-lite/v6.json")


def test_template_loads_exactly_the_pinned_bundles_in_order():
    """The head include names the pinned files, Vega first, then the page script."""
    html = render_to_string("stewardship/components/chart-scripts.html")
    scripts = [
        line.split('src="', 1)[1].split('"', 1)[0]
        for line in html.splitlines()
        if "<script" in line
    ]
    assert scripts == [
        *(f"/static/{asset.static_name}" for asset in VENDORED),
        "/static/stewardship/chart-v1.js",
    ]
    assert all(" defer" in line for line in html.splitlines() if "<script" in line)
    assert 'href="/static/stewardship/chart-v1.css"' in html
    # The template text itself carries the versions: a pin change must touch it.
    text = TEMPLATE.read_text()
    assert all(asset.filename in text for asset in VENDORED)


def test_page_script_uses_the_interpreter_without_injected_styles():
    """chart-v1.js keeps to what the CSP allows: no eval, no inline styles."""
    script = Path(
        ROOT / "src/parishkit/stewardship/accounts/static/stewardship/chart-v1.js"
    ).read_text()
    for option in (
        "ast: true",
        'renderer: "svg"',
        "actions: false",
        "defaultStyle: false",
        "disableDefaultStyle: true",
        "loader,",
        "loader.load = refuse",
        "loader.sanitize = refuse",
    ):
        assert option in script
    assert "eval(" not in script and "innerHTML" not in script
