#!/bin/bash
# Install Python requirements in CI, retrying transient index/network faults.
#
# GitHub-hosted runners occasionally fail a pip install with "no matching
# distribution" (ResolutionImpossible) for pins that exist on PyPI, or with
# "Connection broken: IncompleteRead". Those are per-runner index or network
# faults, not dependency conflicts, and a retry succeeds. pip's own --retries
# only covers individual HTTP requests, so the whole install is also retried a
# bounded number of times. A genuine resolution conflict fails the same way on
# every attempt and still fails the job. Arguments are passed to pip install.
set -uo pipefail

attempts=3
backoff=20
for attempt in $(seq 1 "$attempts"); do
    echo "ci-pip-install: attempt ${attempt}/${attempts}: pip install $*"
    if python -m pip install --retries 5 --timeout 60 "$@"; then
        exit 0
    fi
    if [ "$attempt" -lt "$attempts" ]; then
        echo "ci-pip-install: attempt ${attempt} failed; retrying in ${backoff}s"
        sleep "$backoff"
    fi
done
echo "ci-pip-install: pip install failed after ${attempts} attempts" >&2
exit 1
