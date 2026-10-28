"""Stand-ins for host tools the scripts' tests cannot rely on everywhere.

The host scripts run on Linux with GNU coreutils; the tests also run on a
developer's macOS, whose BSD ``stat`` has no ``-c``.
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


def gnu_stat_missing():
    """Whether this host's ``stat`` lacks GNU's ``-c`` format option."""
    result = subprocess.run(
        ["stat", "-c", "%i", "/"], capture_output=True, text=True, check=False
    )
    return result.returncode != 0


def install_gnu_stat(bin_dir):
    """Put a GNU-compatible ``stat`` on ``bin_dir`` where the host has none."""
    if not gnu_stat_missing():
        return
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "stat"
    path.write_text(f"#!{sys.executable} -I\n{GNU_STAT}")
    path.chmod(0o755)
