"""A stable file lease excludes offline maintenance from every online process.

Compose mounts the same pre-provisioned inode individually. Online services
cannot replace it because they never mount its parent directory. Shared leases
last for the entire service lifetime, not just startup checks. Offline commands
take a nonblocking exclusive lease and never stop another process themselves.
"""

import os
import stat

from parishkit.config import ConfigError

from .runtime_paths import explicit_path

MARKER = b"parishkit-stewardship-startup-v1\n"


class StartupBusy(ConfigError):
    """The requested lifecycle operation conflicts with a running process."""


class StartupLease:
    """Own one validated kernel lease; never create or replace the lock inode."""

    def __init__(self, path, *, offline):
        if type(offline) is not bool:
            raise TypeError("An explicit offline/online lease mode is required.")
        self.path = explicit_path(path)
        self.offline = offline
        self.descriptor = None

    def __enter__(self):
        """Acquire immediately or fail without waiting or changing durable state."""
        import fcntl

        if self.descriptor is not None:
            raise ConfigError("A startup lease cannot be nested.")
        descriptor = None
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or metadata.st_size != len(MARKER)
                or os.read(descriptor, len(MARKER) + 1) != MARKER
            ):
                raise ConfigError("Startup interlock metadata is invalid.")
            try:
                fcntl.flock(
                    descriptor,
                    (fcntl.LOCK_EX if self.offline else fcntl.LOCK_SH) | fcntl.LOCK_NB,
                )
            except BlockingIOError:
                raise StartupBusy(
                    "Offline and online services cannot overlap."
                ) from None
            self.descriptor = descriptor
            self.identity = metadata.st_dev, metadata.st_ino
            self.check()
            return self
        except BaseException as error:
            if descriptor is not None:
                os.close(descriptor)
            self.descriptor = None
            if isinstance(error, OSError):
                raise ConfigError("The startup interlock is unavailable.") from None
            raise

    def check(self):
        """Detect replacement/loss before a subsequent privileged boundary."""
        if self.descriptor is None:
            raise ConfigError("The startup interlock is not held.")
        try:
            actual = os.fstat(self.descriptor)
            selected = self.path.lstat()
        except OSError:
            raise ConfigError("The startup interlock is unavailable.") from None
        if (
            (actual.st_dev, actual.st_ino) != self.identity
            or (selected.st_dev, selected.st_ino) != self.identity
            or not stat.S_ISREG(selected.st_mode)
            or stat.S_IMODE(selected.st_mode) != 0o600
            or selected.st_uid != os.geteuid()
            or actual.st_nlink != 1
        ):
            raise ConfigError("The startup interlock changed during the operation.")

    def inherit(self):
        """Retain this exact lease across exec into the admitted service process."""
        self.check()
        os.set_inheritable(self.descriptor, True)
        return self.descriptor

    def __exit__(self, *exception):
        """Close only our descriptor; inherited children keep their own lease."""
        if self.descriptor is not None:
            # Explicit LOCK_UN would release a child's inherited open-file lease.
            # Closing instead releases it only after the final holder exits.
            os.close(self.descriptor)
            self.descriptor = None
