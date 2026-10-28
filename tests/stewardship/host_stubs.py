"""Stand-ins for host tools the scripts' tests cannot rely on everywhere.

The host scripts run on Linux with GNU coreutils; the tests also run on a
developer's macOS, whose BSD ``stat`` has no ``-c`` and whose BSD ``ln``
has no ``-T``.
"""

import subprocess
import sys

# GNU ``stat [-L] -c %d:%i [--] PATH`` for the copy lock's identity check
# (#235): an open descriptor's path (/dev/fd/N) is answered by fstat on the
# descriptor this process inherited, as Linux's /proc does; macOS's own
# /dev/fd reports another device for it.
GNU_STAT = """\
import os
import sys

args = sys.argv[1:]
follow = "-L" in args
args = [arg for arg in args if arg not in ("-L", "--")]
if len(args) != 3 or args[:2] != ["-c", "%d:%i"]:
    sys.exit(2)
path = args[2]
try:
    if path.startswith("/dev/fd/"):
        status = os.fstat(int(path.removeprefix("/dev/fd/")))
    else:
        status = os.stat(path) if follow else os.lstat(path)
except (OSError, ValueError):
    sys.exit(1)
print(f"{status.st_dev}:{status.st_ino}")
"""

# GNU ``ln -T -- SOURCE NAME`` for the copy lock's creation (#2318): a hard
# link that fails when NAME exists (even as a directory) instead of linking
# into it. Any other ``ln`` call runs the host's own.
GNU_LN = """\
import os
import sys

args = sys.argv[1:]
if args[:2] != ["-T", "--"]:
    os.execv("/bin/ln", ["ln", *args])
try:
    os.link(args[2], args[3], follow_symlinks=False)
except (OSError, IndexError):
    sys.exit(1)
"""


def gnu_stat_missing():
    """Whether this host's ``stat`` lacks GNU's ``-c`` format option."""
    result = subprocess.run(
        ["stat", "-c", "%i", "/"], capture_output=True, text=True, check=False
    )
    return result.returncode != 0


def gnu_ln_missing():
    """Whether this host's ``ln`` lacks GNU's ``-T`` option."""
    result = subprocess.run(
        ["ln", "-T", "--help"], capture_output=True, text=True, check=False
    )
    return result.returncode != 0


def install_gnu_tools(bin_dir):
    """Put GNU-compatible ``stat`` and ``ln`` on ``bin_dir`` where the host has none."""
    for name, missing, source in (
        ("stat", gnu_stat_missing, GNU_STAT),
        ("ln", gnu_ln_missing, GNU_LN),
    ):
        if missing():
            bin_dir.mkdir(parents=True, exist_ok=True)
            path = bin_dir / name
            path.write_text(f"#!{sys.executable} -I\n{source}")
            path.chmod(0o755)
