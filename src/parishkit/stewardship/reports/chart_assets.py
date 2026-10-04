"""Pins for the vendored chart engine: the browser bundles and vl-convert (#477).

Every Admin chart is one Vega-Lite specification (``chart_specs``), rendered
interactively by the browser (``chart-v1.js``) and statically by the server
(``chart_rendering``) from the same JSON, so the two cannot disagree. Both
sides therefore run the same Vega: vl-convert-python bundles Vega, Vega-Lite
and vega-embed inside its own JavaScript runtime, and the browser loads the
same releases from the files vendored under
``accounts/static/stewardship/vendor/``. ``tests/stewardship/test_chart_assets.py``
asserts that each vendored file's SHA-256 is the one recorded here, that the
versions equal the ones the locked vl-convert reports, and that the template
which loads them names exactly these files.

Provenance: each file is the unmodified ``build/*.min.js`` that the package
published to npm, fetched through cdn.jsdelivr.net at the URL recorded with
it; the digests below were checked against jsdelivr's own file hashes when the
files were vendored. The three packages are BSD-3-Clause; the bundles also
include third-party ISC, MIT, BSD-3-Clause and Unlicense packages (d3, semver,
fast-json-patch and others), and ``vendor/LICENSES.txt`` carries every notice.
vl-convert reports its Vega and vega-embed releases exactly and its Vega-Lite
only by minor version (1.9.0.post1 embeds vega-lite@6.4.1, the vendored
release, as its binary shows; no API exposes the patch). Upgrading means
replacing the three files together with a vl-convert release that bundles the
same versions, regenerating the third-party notices from the new bundles, and
updating these pins and ``components/chart-scripts.html``.

Why vega-embed rather than vega-interpreter and vega-tooltip on their own: the
Content Security Policy (``web/security.py``) forbids eval, so Vega must run in
its interpreter mode, and forbids inline scripts, so an import map cannot
resolve bare module specifiers. In the Vega 6 line the interpreter and
vega-tooltip are published only as ES modules that import ``vega-util`` by
bare specifier; the one distribution of both as a classic script is
vega-embed's UMD build, which bundles them. ``chart-v1.js`` uses it with the
interpreter, the SVG renderer, no actions menu and no injected styles, and
``chart-v1.css`` serves the tooltip's stylesheet instead.
"""

from dataclasses import dataclass
from pathlib import Path

VENDOR_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "accounts/static/stewardship/vendor"
)


@dataclass(frozen=True)
class VendoredAsset:
    """One vendored browser bundle: where it came from and what it must hash to."""

    package: str
    version: str
    filename: str
    url: str
    sha256: str

    @property
    def static_name(self):
        """The name ``{% static %}`` and the collected tree know the file by."""
        return f"stewardship/vendor/{self.filename}"

    @property
    def path(self):
        """The vendored file in the package source tree."""
        return VENDOR_DIRECTORY / self.filename


VEGA = VendoredAsset(
    package="vega",
    version="6.2.0",
    filename="vega-6.2.0.min.js",
    url="https://cdn.jsdelivr.net/npm/vega@6.2.0/build/vega.min.js",
    sha256="ea6a5936381d06f912466354e2b6a20eb66c4d41f1ca9dff1e0236dfee6ee392",
)
VEGA_LITE = VendoredAsset(
    package="vega-lite",
    version="6.4.1",
    filename="vega-lite-6.4.1.min.js",
    url="https://cdn.jsdelivr.net/npm/vega-lite@6.4.1/build/vega-lite.min.js",
    sha256="6d5035fdd429b4bc6f91f3754426c3f516f3dbd8e08b105cfc2496bed4ebd254",
)
VEGA_EMBED = VendoredAsset(
    package="vega-embed",
    version="7.0.2",
    filename="vega-embed-7.0.2.min.js",
    url="https://cdn.jsdelivr.net/npm/vega-embed@7.0.2/build/vega-embed.min.js",
    sha256="c7111a41190080938b14c1de00074d9bda16ef2199e2919dd038cddf97d87354",
)
# In load order: Vega-Lite and vega-embed attach to the ``vega`` global.
VENDORED = (VEGA, VEGA_LITE, VEGA_EMBED)
# The Vega-Lite schema every chart declares; vl-convert renders a v6 spec with
# the newest 6.x it bundles, which is the vendored release's minor version.
VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json"
