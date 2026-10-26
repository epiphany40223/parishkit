# ParishKit Sync — Deployment and Updates

How Sync images are built, signed and verified, how a VM is installed, and
how it keeps itself updated. Overview: [Sync spec](../spec.md).

## Release and image

- **Trigger:** annotated `sync-vX.Y.Z` tags that are ancestors of `main`.
  Pushing a tag needs a human's approval.
- **Image:** built and published to `ghcr.io/epiphany40223/parishkit-sync`
  from a slim Python base with no build tools, then smoke-tested by digest.
- **Signature and attestations:** the release workflow adds a keyless
  Sigstore signature, a build-provenance attestation and an SBOM attestation,
  all **pushed to the registry**, so a VM can verify without a GitHub token.
- **Identity pinning:** verification pins the certificate identity to this
  repository's release workflow on the **exact** tag being installed
  (`refs/tags/sync-vX.Y.Z`, never a `sync-v*` wildcard) and the issuer
  `https://token.actions.githubusercontent.com`. A validly signed image from
  a different release therefore fails, so a swapped registry tag cannot
  substitute an older, vulnerable release.
- **No downgrades:** the updater never installs a version older than the
  running one, except when restoring the previous version during a
  rollback.
- **Shared tooling:** Sync's release builds on the product-aware release
  tooling from [#2203](https://github.com/epiphany40223/parishkit/issues/2203)
  rather than forking it. Tracking:
  [#2209](https://github.com/epiphany40223/parishkit/issues/2209).

## Verification

- The installer and the updater verify with **`cosign`**, installed at a
  pinned version and checked against its published sha256.
- After verification, they pull and run the image **by digest**, so what
  runs is exactly what was verified. Tags are never run directly.
- Provenance and SBOM attestation checks follow
  [#2209](https://github.com/epiphany40223/parishkit/issues/2209).

Tested on 2026-10-25: `cosign` accepted a GitHub Actions keyless-signed
image with the correct workflow identity and rejected it with a wrong one.
Podman's own `policy.json` `sigstoreSigned` rule could **not** be used on
Ubuntu 24.04: its Podman 4.9.3 is built without Fulcio support ("fulcio
disabled at compile-time"). Runtime policy enforcement is a possible later
hardening step on distributions whose Podman includes it; it is not part of
the baseline.

## Installer

`install.sh` is published as a release asset with a checksum; the
documentation gives a version-pinned URL, never `main`. It:

1. Checks the OS and free disk space, and asks for the portal's DNS name
   (which must already point at the VM; OAuth sign-in needs an HTTPS
   redirect URI on a real name).
2. Installs Podman (the only supported runtime; an existing Docker
   installation is left alone) and a pinned `cosign`.
3. Creates the data volume and generates the credential master key (see
   [credentials](../credential-store/spec.md#master-key)).
4. Adds a 1 GB swapfile and disables `multipathd`.
5. Verifies the image (see [Verification](#verification)), installs the
   systemd units (Podman quadlets for Sync and for Caddy, see
   [HTTPS](#https)) and starts them by digest.
6. Runs a health check through HTTPS, then prints the one-time setup URL and
   token (see [core](../core/spec.md#first-run-setup)).

## HTTPS

- A **Caddy** container in front of Sync terminates TLS. It obtains and
  renews Let's Encrypt certificates automatically for the parish's DNS name,
  redirects HTTP to HTTPS, and adds security headers (HSTS among them).
- Only Caddy publishes ports (80 and 443). Sync's gunicorn listens on a
  private Podman network that only Caddy can reach.
- Certificates and Caddy's state live on their own small volume.
- Each Sync release names the Caddy image **digest** it was tested with; the
  installer and updater run exactly that digest and update it together with
  Sync, so there is one version to track.
- Caddy's memory (estimated 20–30 MB) counts against the footprint and is
  part of the re-measurement on
  [#2212](https://github.com/epiphany40223/parishkit/issues/2212).

Documentation also covers backing up the data volume and the master key, and
recovery. Tracking:
[#2225](https://github.com/epiphany40223/parishkit/issues/2225).

## Updater

A host-side updater runs from a systemd timer. The web app never gets the
container socket.

- **Policy** comes from the portal through a local file or a localhost
  endpoint: enabled (the default; disabling shows a strong warning), the
  deferral window ("only install releases at least N days old") and a
  maintenance window.
- **Jitter:** each install checks at an offset derived from its install ID,
  so installations do not all pull at once.
- **Steps:**
  1. Resolve the newest eligible `sync-v*` release to its **image pair**: the
     Sync digest and the Caddy digest it names (see [HTTPS](#https)). The
     running pair is the rollback target.
  2. Prune first: delete the snapshot kept from the previous update and every
     image except the running Sync and Caddy images. Then check free disk
     space for the new pair and one snapshot; refuse if too low. Pruning
     before pulling keeps the disk peak equal to the steady state in the
     [disk budget](#disk-budget): two image pairs and one snapshot.
  3. Verify the Sync image (exact release identity, see above) and pull both
     images by digest. A release that keeps the same Caddy digest skips the
     Caddy pull and swap.
  4. **Maintenance barrier:** tell the running portal to enter maintenance
     mode. It stops starting new runs, refuses config, credential and
     settings changes (the UI says an update is in progress), and lets
     in-flight steps finish up to a drain limit; steps still running at the
     limit are cancelled and marked `interrupted`. Then stop the old
     Sync container (and the old Caddy container, if its digest changes), so
     nothing writes the database.
  5. Take a consistent snapshot of the SQLite database (SQLite's online
     backup API), then start the new pair, with Sync **in maintenance
     mode**: it runs config upgrades and serves its health check, but
     schedules nothing and accepts no changes.
  6. Health-check the pair **through HTTPS** (Caddy serving a valid
     certificate and proxying to Sync's health endpoint). The health check
     also fails if any module's config upgrade failed (see
     [config upgrades](../modules/spec.md#config-upgrades)). On success, the
     updater lifts maintenance mode and scheduling resumes. On failure, roll
     back: stop the new pair, restore the snapshot, start the previous Sync
     and Caddy digests, lift maintenance mode, then record the rollback in
     the audit trail. Because the barrier
     prevented every run and change between the snapshot and the decision,
     the restore loses only the new version's own startup logs and upgrade
     work; no upstream writes or administrator edits happened in that window.
     The restore is what makes rollback safe: the new version may already
     have upgraded stored config (see
     [config upgrades](../modules/spec.md#config-upgrades)) or changed
     tables.
  7. Keep the previous image pair and the snapshot until the next update, which
     prunes them in step 2.
  8. Report the outcome to the portal's log, audit trail and notifications.
- **About page:** the UI shows the current version, available versions and
  release notes, fetched live from GitHub Releases.

Tracking: [#2226](https://github.com/epiphany40223/parishkit/issues/2226).

## Disk budget

On the smallest supported VM (8.7 GB usable root filesystem), measured on
2026-10-25:

| Item | Size |
|---|---|
| OS and packages | about 2.2 GB |
| Current Sync and Caddy images plus previous (rollback) | about 0.8 GB |
| journald (default: 10% of the filesystem, keeps 15% free) | up to about 0.9 GB |
| Portal SQLite (retention cap; see [logging](../logging/spec.md#retention)) | up to 1 GB |
| Pre-update database snapshot (kept for rollback) | up to 1 GB |
| Swapfile | 1 GB |
| Margin | about 1.8 GB |

These are also the peak during an update, because the updater prunes before
it pulls (see [Updater](#updater) step 2).

OS log files under `/var/log` already rotate weekly, keeping 4. Podman's log
driver is journald, so container stdout falls under journald's cap.

## Rejected approaches

- **gunicorn serving HTTPS with a certbot timer:** custom renewal, restart and
  challenge plumbing, a weaker public-facing TLS endpoint, and a Python
  certbot stack on the host, to save one small always-on process.
- **Watchtower-style updater containers:** they do not verify attestations and
  need the container socket, which is root-equivalent.
- **A self-updating web app with the container socket:** same root-equivalence
  problem.
- **GitHub-only attestation storage:** every VM would need a GitHub token to
  verify.
- **Deploying by tag:** tags are mutable.
- **Relying on Podman's signature policy:** not available on Ubuntu 24.04's
  Podman build.
- **Docker as a supported runtime:** it would double the installer, updater
  and rollback paths and their tests, and its daemon costs about 80–120 MB on
  a 512 MB VM, where Podman measured about 2 MB idle. Support can be added
  later as its own issue if a parish needs it.
