"""Stewardship release tag names (#2203), in one place for the release tools.

Releases up to v1.23.0 were tagged ``vX.Y.Z``; from v1.24.0 they are tagged
``stewardship-vX.Y.Z``, so that another product can release from the same
repository under its own prefix (#2208). Every tool that looks for earlier
releases accepts both prefixes for good: prepare-release.py and
release_evidence.py rank them by version, and the upgrade-parity test takes
the nearest under either. The old tags stay the history that version bumps,
changelog ranges, the release-order guard and rollbacks read.
New tags always use ``TAG_PREFIX``; a release is never tagged twice.

The tools are standalone scripts, so they load this file by path (with
importlib.util.spec_from_file_location) rather than from a package.
"""

import re

# The prefix new release tags get, then the prefixes still recognized,
# preferred in this order when one version carries more than one of them.
TAG_PREFIX = "stewardship-v"
PREFIXES = (TAG_PREFIX, "v")
# Shell globs for git tag --list, git describe --match and git ls-remote.
GLOBS = tuple(f"{prefix}*" for prefix in PREFIXES)
TAG_RE = re.compile(
    r"^(?P<prefix>"
    + "|".join(re.escape(prefix) for prefix in PREFIXES)
    + r")(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)


def tag_for(version):
    """The tag name a new release of the dotted version gets."""
    return f"{TAG_PREFIX}{version}"


def tag_version(tag):
    """A release tag's (major, minor, patch), or None for any other name."""
    match = TAG_RE.match(tag)
    if not match:
        return None
    return tuple(int(match.group(part)) for part in ("major", "minor", "patch"))


def newest_first(tags, one_per_version=True):
    """Release tags from an iterable of names, highest version first.

    Names that are not release tags are dropped. Within one version the
    preferred prefix comes first. When a version has tags under more than one
    prefix (which the release tools never create), one_per_version keeps only
    that first tag, so the version counts once; a caller that filters the
    result further (by ancestry, say) passes False and keeps them all, so a
    tag it rejects cannot hide another tag of the same version.
    """
    ranked = sorted(
        (tag for tag in tags if tag_version(tag) is not None),
        key=lambda tag: (
            tuple(-part for part in tag_version(tag)),
            PREFIXES.index(TAG_RE.match(tag).group("prefix")),
        ),
    )
    if not one_per_version:
        return ranked
    kept = {}
    for tag in ranked:
        kept.setdefault(tag_version(tag), tag)
    return list(kept.values())
