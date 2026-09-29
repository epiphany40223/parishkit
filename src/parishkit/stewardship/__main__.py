"""Run ``pk-stewardship`` as ``python -m parishkit.stewardship``.

The worker process starts its source-queue sibling this way (#336), so the
sibling runs under the same interpreter without relying on a script path.
"""

import sys

from .cli import main

sys.exit(main())
