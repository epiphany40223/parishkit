# Stewardship local laptop environment: developer guide

How to run the production-shaped Stewardship deployment on a laptop with
`tools/stewardship-local.sh`. The
[local-environment specification](../specs/stewardship/local-environment/spec.md)
is the contract (what the environment is, its safety guarantees, the
[operator script](../specs/stewardship/local-environment/spec.md#operator-script)
commands and their rules); this guide is the walk-through. The
[OPS-10 plan](../plans/stewardship/operations.md#ops-10-local-laptop-environment)
lists the pieces that are still landing; where this guide says "not yet", that
plan says which pull request brings it.

**What works today (2026-10-04).** `vm`, `up`, `start`, `down`, `status`,
`snapshot`, `reset` (including `--reinstall` and `--seeded`), `sign-in`,
`wizard`, `seed`, `reseed` and `ca` are complete and were run end to end in
the VM: the site answers at `https://localhost:8443` with the LOCAL banner,
Mailpit catches every message at `http://localhost:8025`, the fake
ParishSoft serves the synthetic parish, an unseeded deployment runs under the
fake clock, and `seed` gives it a campaign in progress in about 17 minutes.
Not yet: `deploy` (OPS-10.10; use `reset --reinstall`).

## What you get

One Lima VM on the laptop runs Docker Engine and, inside it, the same Compose
topology a production host runs, rendered for the `local` profile: Caddy on
`https://localhost:8443` with its own certificate authority, the web, worker,
scheduler, mail-dispatch and installer services, PostgreSQL and Valkey, all
under `/opt/parishkit` with real Linux ownership. The image is built inside the
VM from your checkout, including uncommitted edits to tracked files. Nothing in
the VM can reach the Internet from the application networks, and nothing can
email a real person: mail goes to Mailpit on `http://localhost:8025` and
ParishSoft is a fake service with a synthetic parish.

## Prerequisites

- macOS on Apple silicon with [Lima](https://lima-vm.io/): `brew install lima`.
  The script runs under macOS's own bash 3.2; it needs only Lima, git, tar
  and ssh on the laptop.
- About 5 GiB of memory and 40 GiB of disk for the VM. Quit Docker Desktop
  while the VM runs.
- A checkout of this repository. The script packs the checkout that contains
  the directory you run it from (see [testing a pull request](#testing-a-pull-request)).

If you already have a Lima instance set up by hand rather than with `vm
create`, it needs: Docker Engine with the Compose plugin, `jq`, `rsync`,
`flock` and `curl` (all but Docker and `jq` are in Ubuntu's cloud image),
forwards for guest ports 8443 and 8025 to the laptop's `127.0.0.1`, and no
shared directories. Name it with `PARISHKIT_LOCAL_VM`, for example
`PARISHKIT_LOCAL_VM=pk-local tools/stewardship-local.sh up`.

## Commands

All commands are `tools/stewardship-local.sh COMMAND`; `-h` prints them. The
script talks only to the Lima instance named by `PARISHKIT_LOCAL_VM` (default
`parishkit-local`) and runs everything inside it as root through
`tools/stewardship-local-vm.sh`, which it uploads on every call, so the VM
never needs a copy of the repository.

| Command | What it does |
| --- | --- |
| `vm create` | Create and start the Lima instance from `deploy/stewardship/lima-local.yaml` (Ubuntu 24.04 arm64, Docker Engine, forwards only 8443 and 8025, no shared directories). Takes a few minutes the first time. |
| `vm start` / `vm stop` | Start or stop the VM. |
| `up [--families N]` | First-time install from this checkout (below). `N` is the synthetic parish size, default 100. Refuses if a deployment exists. |
| `start` | Start a stopped deployment's services (after `down` or a VM restart) in the recorded clock mode. |
| `deploy` | Not yet: OPS-10.10 brings the scripted upgrade's local mode. Until then use `reset --reinstall`. |
| `snapshot [--seeded]` | Stop the services, copy the runtime root to `/opt/parishkit-snapshots/post-setup` (or `seeded`) with numeric ownership and modes preserved, start again. |
| `reset [--seeded]` | Stop, restore that snapshot with `rsync --delete`, recreate and start the services. With no post-setup snapshot: type the instance name, and the root is removed and `up` runs again. |
| `reset --reinstall` | Type the instance name; the new image is built from this checkout first, then the root is removed and `up` runs again from that image. Snapshots are kept. The stand-in for `deploy` until OPS-10.10. |
| `seed [--response-scale M]` | Not yet: OPS-10.07 brings the time-travel seeder. The command takes its lock, keeps `~/.parishkit-local/seed.log`, and refuses until the image carries `local-seed`. |
| `reseed` | `reset` to the post-setup snapshot, then `seed`. |
| `status` | The VM, every service's state and health, Docker disk use, VM disk use, snapshots. |
| `down` | Stop the services (90-second grace per container; a container Docker had to kill is named). Data is never removed. |
| `sign-in --email E` | Print a local test sign-in link (OPS-10.08; until then the command inside the image refuses). |
| `ca` | Save Caddy's local root certificate to `~/.parishkit-local/caddy-root.crt` and print the `security add-trusted-cert` command. The script never changes trust itself. |

Environment variables: `PARISHKIT_LOCAL_VM` (instance name),
`PARISHKIT_LOCAL_ADMIN_EMAIL` (the initial Administrator, default
`admin@example.test`), `PARISHKIT_LOCAL_FAMILIES` (default 100),
`PARISHKIT_LOCAL_DEBUG_LOGGING` (1 by default, as pre-launch dev deploys
did; 0 for production-like logging), `PARISHKIT_LOCAL_ROOT` (default
`/opt/parishkit`; must be an absolute path of at least two components) and
`LIMA_HOME` (default `~/.lima`).

## First bring-up

```shell
tools/stewardship-local.sh vm create      # once
tools/stewardship-local.sh up             # one to two minutes: build, install, start
tools/stewardship-local.sh ca             # then run the printed command to trust the CA
```

`up` prints each step with its timing, then a summary: the site and Mailpit
URLs, the Administrator's address, the sign-in command, the image tag, the
deployment UUID, the clock mode and the values the setup wizard asks for. The
VM keeps the complete log under `/var/log/stewardship-local-up-*.log`.

What `up` does, in order (it is the deployment runbook's
[first installation](stewardship-deployment-runbook.md#first-installation)
with the LOCAL inputs):

1. Packs the tracked files of the checkout and builds the image in the VM
   under the tag `parishkit-stewardship-local:<commit>[-dirty]-<epoch>`
   (`-dirty` means tracked files have uncommitted edits).
2. Writes `/etc/parishkit/stewardship-deployment.yaml` (profile `local`,
   origin `https://localhost:8443`, one proxy hop, root `/opt/parishkit`) and
   the deployment record `/etc/parishkit/stewardship-local.env` (UUID,
   Administrator, image, parish size, generator seed, anchor date, debug
   switch), plus an in-progress marker beside it that only a later
   `reset --reinstall` honours if this `up` fails partway.
3. Creates the root owned by `10001:10001`, runs `provision-runtime`, then
   writes the marker `/opt/parishkit/.parishkit-local` that every destructive
   command checks (provisioning needs an empty root, so the marker comes
   after it), and collects the static files.
4. Installs the sentinel OAuth client, writes `run/local/fake-parishsoft.json`
   (seed, families, anchor date 17 days back, `release_at: null`) and the
   clock-mode marker `run/local/clock/mode`.
5. Runs `database-roles`, `bootstrap --phase prepare`, `migrate`,
   `database-grants` and `bootstrap --phase import` under
   `compose-initial.json` with project `parishkit-local`.
6. Starts web, waits for it, starts everything else, and checks the web
   health command and that the application answers the public origin through
   Caddy.

Before the setup wizard has run, `https://localhost:8443/` answers 503 "The
system is not configured yet" from the application, with the LOCAL banner;
`/admin/` redirects to sign-in. That is the expected state after `up`.

If `up` fails partway (the VM's log says where), `reset --reinstall` removes
the half-installed root and installs again; `up` itself refuses to touch it.

### Trusting the certificate

`ca` saves the root certificate and prints the `security add-trusted-cert`
command for the login keychain. Safari and Chrome then trust the site; Firefox
keeps its own store, so import `~/.parishkit-local/caddy-root.crt` under
Settings, Privacy & Security, Certificates, or just accept its warning. Every
reinstall creates a new CA: run `ca` again and trust the new file. To remove a
trusted root later:

```shell
security remove-trusted-cert ~/.parishkit-local/caddy-root.crt
security delete-certificate -c "Caddy Local Authority - 2026 ECC Root" ~/Library/Keychains/login.keychain-db
```

(`openssl x509 -in ~/.parishkit-local/caddy-root.crt -noout -subject` shows
the exact name.)

### Clock mode

An unseeded deployment runs under the
[fake clock](../specs/stewardship/local-environment/spec.md#fake-clock), 17
days behind real time, so that a later seed can start forward of every row:
`up` builds three libfaketime-derived images (application, PostgreSQL,
Valkey), writes `fake` and the offset `-1468800` under `run/local/clock/`,
and runs the install steps and the services with the override
`config/services/compose.faketime.json`. Pages then show dates about 17 days
in the past; browser cookies still work (they carry `Max-Age`). Every command
that starts services reads the marker and applies the override when it says
`fake`; a missing or unknown marker is an error. A seeded deployment runs in
normal mode (offset zero, no preload).

### The setup wizard

The wizard needs an Admin sign-in, which in LOCAL is the local test sign-in
(OPS-10.08): `tools/stewardship-local.sh sign-in --email admin@example.test`
(the bootstrap Administrator, `PARISHKIT_LOCAL_ADMIN_EMAIL`) prints a one-time
link, valid for two minutes, to open in the browser. The wizard's credential
step takes the values `up` printed: the fake ParishSoft API key and
organization (OPS-10.06) and the mail-catcher document as the Workspace
credential (OPS-10.05). Those two credentials are installed by the real
credential installers, exactly as in production, which is part of what the
environment tests.

For an unattended install, `tools/stewardship-local.sh wizard` completes the
wizard through the wizard pages' own service layer with the fake ParishSoft
key, the mail-catcher document, a first campaign with every module, default
pages and emails and a generated logo, then follows the runbook's post-wizard
step (recreating `worker` and `mail-dispatch` from `compose.json` and
acknowledging each credential inside them) until setup is complete. The
browser wizard remains the developer-facing path.

After the wizard completes, take the post-setup snapshot:
`tools/stewardship-local.sh snapshot`. `reset` then returns to that state in
about half a minute. (A snapshot taken before the wizard is allowed and noted
as a pre-wizard snapshot; it is still useful for a fast return to a clean
install.)

### Seeding a campaign

The recipe for a seeded environment, from a checkout, is:

1. `tools/stewardship-local.sh reset --reinstall` (or `up` on a VM with no
   deployment): about two minutes, ends in fake-clock mode.
2. The setup wizard: in the browser (`sign-in --email admin@example.test`,
   then the values the summary printed), or unattended with
   `tools/stewardship-local.sh wizard` (about 90 seconds).
3. `tools/stewardship-local.sh snapshot`: the post-setup snapshot `reseed`
   returns to.
4. `tools/stewardship-local.sh seed`: about 17 minutes at 100 Families.
5. `tools/stewardship-local.sh snapshot --seeded`: the seeded snapshot
   `reset --seeded` restores in about half a minute.

If `seed` fails, run `reseed` (it restores the post-setup snapshot and seeds
again). Until `deploy` exists (OPS-10.10), testing a pull request on seeded
data means reinstalling from that checkout and seeding again, since the
snapshots belong to the image that made them.

`tools/stewardship-local.sh seed [--response-scale M]` gives the deployment a
campaign in progress with realistic Family activity, as the
[specification](../specs/stewardship/local-environment/spec.md#seeded-campaign-and-responses)
describes: it refuses unless the clock-mode marker says `fake` (an unseeded
deployment) and the wizard has completed, clears Mailpit, and runs the
seeder's phases as one-shot containers under the web identity (`prepare`:
the real dates and schedules, readiness and the real go-live; `drive`: every
event before now, with the clock stepped forward and the real scheduler,
worker and mail-dispatch doing the work), the invariant `check` under the
migration profile with the services stopped, the switch to normal clock
mode, and `finish` (the real refresh that promotes the late-added Family).
Every message the seed sent is tagged `seed-history` in Mailpit. Each step
prints its JSON answer, its duration and the fake instant; the whole log is
also kept in `~/.parishkit-local/seed.log`. A seeded deployment runs in
normal clock mode and cannot be seeded again; `reseed [--response-scale M]`
restores the post-setup snapshot (fake-clock mode) and seeds afresh. A seed
that fails before the switch to normal mode leaves the deployment unseeded in
fake-clock mode with its services running; one that fails after it (the
finish phase) leaves seeded data whose late-added Family may not be promoted.
In both cases run `reseed`.

## Testing a pull request

The script builds the checkout that contains the directory it runs from, so
testing a pull request means running it from that pull request's checkout:

1. Check the pull request out, in a worktree or in place: `gh pr checkout N`
   (or `git fetch origin pull/N/head:pr-N && git worktree add ../pk.pr-N pr-N`).
2. If the pull request predates the local environment (no
   `tools/stewardship-local.sh` in it), make a scratch branch and merge
   `origin/main` into it: `git switch -c scratch/pr-N && git merge origin/main`.
3. Only tracked files are packed: `git add` any new file you are testing.
   Uncommitted edits to tracked files are included (the tag ends in
   `-dirty`).
4. From that checkout: `tools/stewardship-local.sh reset --reinstall` (or
   `up` on a VM with no deployment). The output names the checkout, branch
   and commit it is building.
5. `tools/stewardship-local.sh ca`, then trust the new certificate.
6. `tools/stewardship-local.sh sign-in --email admin@example.test` and open
   the link; run the setup wizard with the fake ParishSoft key and
   organization and the mail-catcher document from the summary (or
   `wizard`), then exercise the change. For a campaign in progress, follow
   the [seeding recipe](#seeding-a-campaign): the pull request's image seeds
   its own data, because snapshots belong to the image that made them.

## Day-to-day

- Changed code? `reset --reinstall` builds the new image, then wipes and
  reinstalls, keeping any snapshot (about one to two minutes: the first image
  build takes about a minute, a cached rebuild a few seconds, the install
  about a minute). `reset` with no post-setup snapshot does the same.
- `status` shows every container's health. Service logs are in the VM:
  `limactl shell parishkit-local -- sudo docker compose -p parishkit-local logs --tail 50 web`.
- `down` stops everything and keeps the data; `start` brings it back (also
  after a VM restart). `up` is first-time install only.
- The runbook's inspection commands work inside the VM as on a production
  host: `limactl shell parishkit-local -- sudo bash`, then the
  [deployment runbook](stewardship-deployment-runbook.md#commands-and-generated-paths)'s
  `docker compose -f /opt/parishkit/config/services/compose-initial.json -p parishkit-local ...`.

## The documented VM run

The specification's
[testing requirements](../specs/stewardship/local-environment/spec.md#testing-requirements)
leave the end-to-end run to a developer in the VM before a pull request that
changes the environment. The run is:

1. `up` on a clean root completes with every service healthy and the
   application answering `https://localhost:8443/` through Caddy.
2. From the laptop, `curl -k https://localhost:8443/` answers from gunicorn
   (`via: 1.1 Caddy`), `/health/` and `/metrics` answer 404, and nothing but
   8443 and 8025 is forwarded.
3. No egress: inside the VM,
   `sudo docker compose -p parishkit-local exec caddy wget -T 5 -O- http://1.1.1.1/`
   fails, the `application-egress` network does not exist, web is on
   `backend` and `proxy` only, and `parishkit-local_ingress` carries
   `com.docker.network.bridge.enable_ip_masquerade: false`.
4. Ownership: `/opt/parishkit` is `10001:10001` mode `0700`; credential and
   fake-configuration files are `0600` owned by `10001`.
5. `snapshot`, `down`, `start`, `reset`, `reset --reinstall` and `status`
   round-trip.
6. The seed: `wizard` (or the browser wizard), `snapshot`, `seed` at 20 and
   at 100 Families (`PARISHKIT_LOCAL_FAMILIES` for `reset --reinstall`),
   `snapshot --seeded` and `reset --seeded`, with the
   [seed tests](../specs/stewardship/local-environment/spec.md#seeder-tests)'
   assertions read off the database and Mailpit.

The record of the first such run (2026-10-04) is the
[OPS-10.09 VM run record](https://github.com/epiphany40223/parishkit/issues/476#issuecomment-5976332758)
on issue #476.

## Safety reminders

The script never targets anything but the Lima instance's ssh alias, acts
destructively only on a root carrying `/opt/parishkit/.parishkit-local` (or,
for the wipe behind `reset --reinstall` alone, the in-progress marker of a
failed `up`), never passes `--volumes`, refuses to remove the root while any
container of the project still exists, and never removes `run/persistent`
except as part of a restore or a typed-confirmation wipe. The VM has no shared
directories, so a mistake inside it cannot touch the laptop's files. See the
specification's
[safety guarantees](../specs/stewardship/local-environment/spec.md#safety-guarantees)
for why a LOCAL deployment cannot reach Gmail, ParishSoft, Slack or Google
Drive.
