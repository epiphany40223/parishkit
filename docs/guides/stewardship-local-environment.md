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
`wizard`, `seed`, `reseed`, `deploy` (with `--schema-change` and
`--rollback`) and `ca` are complete and were run end to end in the VM: the
site answers at `https://localhost:8443` with the LOCAL banner, Mailpit
catches every message at `http://localhost:8025`, the fake ParishSoft serves
the synthetic parish, an unseeded deployment runs under the fake clock, `seed`
gives it a campaign in progress in about 17 minutes, and `deploy` upgrades
the seeded deployment to a pull request's build in about half a minute after
the image build, with web down for about five seconds. The fake-clock
(unseeded) deploy path, with its derived-image build, is covered by the
stand-in tests only; it has not been run in the VM. Likewise `finish-setup`
automates the post-wizard step that was run by hand in the VM, but the command
itself is covered by the stand-in tests only. The BG-12 rehearsal
(`rehearse`, `deploy --bulk` and `deploy --smtp-latency-ms`) is covered by
the stand-in tests and a read-only check of its lock sampler and report in
the VM; its first full run is the baseline run that the
[Family mail dispatch guide](stewardship-family-mail-dispatch.md#bulk-send-rehearsal-baselines)
records.

## What you get

One Lima VM on the laptop runs Docker Engine and, inside it, the same Compose
topology a production host runs, rendered for the `local` profile: Caddy on
`https://localhost:8443` with its own certificate authority, the web, worker,
scheduler, mail-dispatch and installer services, PostgreSQL and Valkey, all
under `/opt/parishkit` with real Linux ownership. The image is built inside the
VM from your checkout, including uncommitted edits to tracked files. The
application services have the same route out to the Internet as on a
production host; Caddy, Mailpit and the fake ParishSoft have none. Code
checks keep mail on Mailpit (`http://localhost:8025`), so nothing can email a
real person, unless you set a mail redirect and install a test mailbox's
key: then every message goes to that one address only
([below](#sending-real-mail-to-one-address)). Admin sign-in is the local
test sign-in, with a stand-in OAuth client, unless you install a real
localhost-only one ([below](#signing-in-with-google)). ParishSoft is a fake
service with a synthetic parish unless you choose the real ParishSoft API
with `deploy --parishsoft real`; pk-local then reads real parish data,
which stays on the laptop and must never be committed, and it never writes
to the real ParishSoft. With the fake, Update ParishSoft writes to the fake
([below](#updating-the-fake-parishsoft)). The other real provider the VM can
reach is Slack, and only with a test token you install yourself.

## Prerequisites

- macOS on Apple silicon with [Lima](https://lima-vm.io/): `brew install lima`.
  The script runs under macOS's own bash 3.2; it needs only Lima, git, tar
  and ssh on the laptop.
- About 5 GiB of memory and 40 GiB of disk for the VM. Quit Docker Desktop
  while the VM runs.
- A checkout of this repository. The script packs the checkout that contains
  the directory you run it from (see [testing a pull request](#testing-a-pull-request-on-seeded-data)).

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
| `deploy [--schema-change]` | Build the image from this checkout, then upgrade the running deployment to it, data and all, by the Production upgrade's steps (`tools/stewardship-upgrade-host.sh` in local mode; [below](#testing-a-pull-request-on-seeded-data)). `--schema-change` is what `STEWARDSHIP_SCHEMA_CHANGE=1` is to Production: without it, a build whose upgrade check expects migration or grant changes is refused before anything stops. Refuses unless a snapshot exists to return to. |
| `deploy --bulk on\|off` / `deploy --smtp-latency-ms N` | The same deploy, rendering the bulk Family send on or off, or the modeled Gmail latency of `N` milliseconds (0–5000; 0 turns it off). Each carries over to later deploys until changed. For [rehearsing a bulk send](#rehearsing-a-bulk-send). |
| `deploy --mail-redirect ADDRESS\|off` | The same deploy, rendering the forced mail redirect: every message, whatever its recipients, routing or campaign mode, goes only to `ADDRESS`. A real Google Workspace key is admitted only while it is set ([below](#sending-real-mail-to-one-address)). It carries over to later deploys until changed; `off` removes it, and a `deploy --rollback` drops it (a real key is then refused). |
| `deploy --parishsoft real\|fake` | The same deploy, reading the real ParishSoft API or the fake (the default). It carries over to later deploys until changed, and a `deploy --rollback` goes back to the fake. With the real API, install a real `parishsoft` key for the real organization (Integrations); the synthetic parish's key and organization belong to the fake. That key can write contact fields; pk-local's code refuses every ParishSoft write, but treat the key as Production's. Use a normal-clock deployment (a seeded one, or after `seed --testing`): under the fake clock the services check ParishSoft's certificate against a date about 17 days ago, and the deploy warns. The key and the real data it reads stay on the laptop, but every `snapshot` copies them, so delete snapshots you no longer need. When you switch back with `--parishsoft fake`, reinstall the fake key and organization `up` printed (Integrations). |
| `deploy --rollback` | The upgrade's image-only rollback to the image the last `deploy` replaced; refused when the schema or a grant changed in between (then `reset --seeded`). |
| `snapshot [--seeded]` | Stop the services, copy the runtime root to `/opt/parishkit-snapshots/post-setup` (or `seeded`) with numeric ownership and modes preserved, start again. Replaces the earlier copy. |
| `snapshot save NAME [--replace]` | The same for any name (1–32 of `a-z`, `0-9`, `-`), with a record of its migrations, mode and campaign state. Refused before anything stops when the Mac would keep less than 20 GiB plus twice the copy, the VM less than 5 GiB, or there are already 8 other snapshots (`PARISHKIT_LOCAL_SNAPSHOT_LIMIT`). An existing name needs `--replace`. A failed save starts the services again and leaves no half copy. |
| `snapshot list` | Each snapshot's time, mode and campaign state, clock mode, commit, what a switch to it would do and size, then the VM's and the Mac's free space. |
| `snapshot delete NAME` | Type the snapshot's name; it is removed and the VM disk is trimmed so the Mac gets the space back. |
| `snapshot restore NAME` / `switch NAME` | Keep the deployed code and switch to `NAME`'s data ([below](#switching-between-testing-and-production-data)). |
| `reset [--seeded \| NAME]` | Stop, restore that snapshot (image and data together) with `rsync --delete`, recreate and start the services. With no post-setup snapshot: type the instance name, and the root is removed and `up` runs again. |
| `reset --reinstall` | Type the instance name; the new image is built from this checkout first, then the root is removed and `up` runs again from that image. Snapshots are kept. |
| `seed [--response-scale M]` | Seed a campaign in progress under the fake clock ([below](#seeding-a-campaign)); refuses on a seeded deployment. Takes a lock and keeps `~/.parishkit-local/seed.log`. |
| `reseed` | `reset` to the post-setup snapshot, then `seed`. |
| `seed --testing` | Switch to the post-setup snapshot on the deployed code and move it to real time: a Testing-mode draft campaign ([below](#switching-between-testing-and-production-data)). |
| `rehearse [--due-in MIN] [--send-only] [--timeout MIN] [--label NAME]` | Add a Reminder due in `MIN` minutes (default 5), measure its send and print the report ([below](#rehearsing-a-bulk-send)). Seeded deployments only. Keeps `~/.parishkit-local/rehearse.log`. |
| `spike [--families N] [--sources S] [--during-send] [--wait MIN] [--label NAME]` | Have `N` Families (default 200) open their emailed links and submit through Caddy from `S` sources (default 8), and print the report ([below](#checking-the-launch-day-spike)). Seeded deployments only. Keeps `~/.parishkit-local/spike.log`. |
| `status` | The VM, every service's state and health, Docker disk use, VM disk use, and the `snapshot list` summary. |
| `down` | Stop the services (90-second grace per container; a container Docker had to kill is named). Data is never removed. |
| `sign-in --email E` | Print a one-time local test sign-in link for an Administrator address. |
| `fake-parishsoft ...` | Change the fake ParishSoft's data, show it, or run a full refresh of it ([below](#changing-parishsoft-data)). Refused after `deploy --parishsoft real`. |
| `ca` | Save Caddy's local root certificate to `~/.parishkit-local/caddy-root.crt` and print the `security add-trusted-cert` command. The script never changes trust itself. |

Environment variables: `PARISHKIT_LOCAL_VM` (instance name),
`PARISHKIT_LOCAL_ADMIN_EMAIL` (the initial Administrator, default
`admin@example.test`), `PARISHKIT_LOCAL_FAMILIES` (default 100),
`PARISHKIT_LOCAL_DEBUG_LOGGING` (1 by default, as pre-launch dev deploys
did; 0 for production-like logging), `PARISHKIT_LOCAL_ROOT` (default
`/opt/parishkit`; must be an absolute path of at least two components),
`PARISHKIT_LOCAL_SNAPSHOT_LIMIT` (default 8) and `LIMA_HOME` (default
`~/.lima`).

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
   (seed, families, anchor date 17 days back, `release_at: null`), installs
   the backup recipient key (the public half as the `backup_data` credential,
   the private key at `run/local/backup-key`, so a local backup can be opened
   for a restore drill) and the clock-mode marker `run/local/clock/mode`.
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
link, valid for 15 minutes, to open in the browser. The wizard's credential
step takes the values `up` printed: the fake ParishSoft API key and
organization (OPS-10.06) and the mail-catcher document as the Workspace
credential (OPS-10.05). Those two credentials are installed by the real
credential installers, exactly as in production, which is part of what the
environment tests.

At Finishing setup the page shows "0 of 1 services acknowledged" for
ParishSoft and for Google Workspace until `worker` and `mail-dispatch` pick up
the installed credentials, within about a minute, and then moves on by
itself, as in Production; after completion those two services restart once
on their own. `tools/stewardship-local.sh finish-setup` waits for all of that
and reports when setup is complete; only if a credential is still
unacknowledged 10 minutes after its installation does it run the runbook's
fallback (recreate `worker` and `mail-dispatch` from `compose.json` and
acknowledge each credential inside them). Running it again is harmless: once
setup is complete it says so and changes nothing. It refuses while the
wizard's pages are still unfinished.

Setup collects no campaign (#142). Once it is complete, open Home and choose
**Create the campaign**, fill in its form (every module, for the seeder),
review and create it, then press **Load the campaign's ParishSoft data** on
Load ParishSoft data, the campaign wizard step it opens next, so the
campaign gets its Families and codes.

For an unattended install, `tools/stewardship-local.sh wizard` completes the
wizard through the wizard pages' own service layer with the fake ParishSoft
key, the mail-catcher document and a generated logo, follows the runbook's
waits while `worker` and `mail-dispatch` acknowledge the credentials
themselves (running the runbook's fallback only if one is overdue) until
setup is complete, and
then runs Create the campaign (every module, with its default pages and
emails) and the campaign's full ParishSoft refresh. The browser wizard and
Create the campaign remain the developer-facing path.

After the campaign has its Families, take the post-setup snapshot:
`tools/stewardship-local.sh snapshot`. `reset` then returns to that state in
about half a minute, and `seed --testing` builds the Testing review state
from it. (A snapshot taken before the wizard is allowed and noted
as a pre-wizard snapshot; it is still useful for a fast return to a clean
install.)

### Seeding a campaign

The recipe for a seeded environment, from a checkout, is:

1. `tools/stewardship-local.sh reset --reinstall` (or `up` on a VM with no
   deployment): about two minutes, ends in fake-clock mode.
2. The setup wizard and the campaign: in the browser (`sign-in --email
   admin@example.test`, then the values the summary printed, then
   `finish-setup` at Finishing setup, then Create the campaign and its load),
   or unattended with `tools/stewardship-local.sh wizard` (about 90 seconds).
3. `tools/stewardship-local.sh snapshot`: the post-setup snapshot `reseed`
   returns to.
4. `tools/stewardship-local.sh seed`: about 17 minutes at 100 Families.
5. `tools/stewardship-local.sh snapshot --seeded`: the seeded snapshot
   `reset --seeded` restores in about half a minute.

If `seed` fails, run `reseed` (it restores the post-setup snapshot and seeds
again). Once the seeded snapshot exists, a pull request is tested on that
data with `deploy`, not by seeding again (see
[testing a pull request on seeded data](#testing-a-pull-request-on-seeded-data)).

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
mode, and `finish` (the real refresh that promotes the late-added Family,
then the import of one Ministry leader user).
Every message the seed sent is tagged `seed-history` in Mailpit. Each step
prints its JSON answer, its duration and the fake instant; the whole log is
also kept in `~/.parishkit-local/seed.log`. A seeded deployment runs in
normal clock mode and cannot be seeded again; `reseed [--response-scale M]`
restores the post-setup snapshot (fake-clock mode) and seeds afresh. A seed
that fails before the switch to normal mode leaves the deployment unseeded in
fake-clock mode with its services running; one that fails after it (the
finish phase) leaves seeded data whose late-added Family may not be promoted.
In both cases run `reseed`.

The synthetic parish's Ministry leaders hold the default leader roles
(`Chairperson` and `Staff`), so they count as Ministry leaders with no
settings change. The seed imports one of them as a Ministry leader user: the
leader of the Ministry with the most open follow-up requests. Its last line
names that user, for example
`Ministry leader user: cla.ward500145@example.test (sign-in --email
cla.ward500145@example.test)`. Sign in as that address to try the Ministry
leader flow. The address changes with the generator seed and the seeded
timeline, so read it from each seed's output (or from the `finish` step's
`leader_user` in `~/.parishkit-local/seed.log`); the
[specification](../specs/stewardship/local-environment/spec.md#seeded-ministry-leader-user)
describes the choice.

## Testing a pull request on seeded data

The script builds the checkout that contains the directory it runs from, so
testing a pull request means running it from that pull request's checkout.
With a seeded deployment running (the [seeding recipe](#seeding-a-campaign),
ending in `snapshot --seeded`), a pull request's build is on the seeded data
in a few minutes:

1. Check the pull request out, in a worktree or in place: `gh pr checkout N`
   (or `git fetch origin pull/N/head:pr-N && git worktree add ../pk.pr-N pr-N`).
2. The deployed image must contain the code the seeded data was made with,
   plus the change. Make a scratch branch and merge the branch the seeded
   deployment was built from into it, normally `origin/main`
   (`git switch -c scratch/pr-N && git merge origin/main`); while the OPS-10
   stack is unmerged, that branch is `pr/stewardship-local-deploy`. `status`
   shows the running image's tag, which carries the commit it was built
   from.
3. Only tracked files are packed: `git add` any new file you are testing.
   Uncommitted edits to tracked files are included (the tag ends in
   `-dirty`).
4. From that checkout: `tools/stewardship-local.sh deploy`. The script may
   be run from another checkout (for example
   `../parishkit/tools/stewardship-local.sh deploy`); it packs the checkout
   that contains the current directory. It builds the image in the VM while
   the deployment still runs (a build failure changes nothing), then runs
   the Production upgrade's steps on the running deployment: the advisory
   upgrade check, background services stopped, the required backup, web
   stopped, `retarget-image`, migration and grants when needed, the static
   refresh, web and then everything else, and the checks. The data, the
   sessions and the snapshots stay; Caddy's CA stays trusted. Each step
   prints its timing. If the pull request changes the schema or a grant,
   the check answers `f` and the deploy refuses before anything stops;
   re-run it as `deploy --schema-change` (Production's
   `STEWARDSHIP_SCHEMA_CHANGE=1`) and step 4 migrates the seeded database.
   For example, #488 (a template change) needs no `--schema-change`; #485
   (a migration) does.
5. Sign in (`sign-in --email admin@example.test`; existing browser sessions
   survive) and exercise the change against the campaign in progress.
6. Back: `tools/stewardship-local.sh reset --seeded` restores the seeded
   snapshot, image and data together, in about half a minute. Always
   `reset --seeded` between pull requests: it is the only way back after
   `--schema-change`, and it keeps one pull request's data out of the next
   one's test. For an image-only return (the Production rollback
   rehearsal), `deploy --rollback` puts the replaced image back and keeps
   whatever data the test wrote; it refuses when the deploy migrated, as
   Production's does.

`deploy` refuses unless a snapshot exists to return to; take `snapshot
--seeded` first if the current data, not the snapshot's, is the return point
you want (it replaces the seeded snapshot). Two side effects to know about:
a deploy records a backup, so more than 24 hours later the deployment raises
the backup-overdue CRITICAL alert (`reset --seeded` clears it, since the
snapshot predates the backup); and because the seeded snapshot was taken
before `up` installed backup keys, each deploy after a reset generates a
fresh backup key pair (harmless: the snapshot restores the backup records
too, so no key change is ever reported). Logs in the VM: the upgrade's own
`/var/log/stewardship-upgrade-*.log` and `stewardship-rollback-*.log`, and
the VM half's `stewardship-local-deploy-*.log` and
`stewardship-local-rollback-*.log` around them.

For a pull request that changes `up` itself (provisioning, the wizard, the
seeder), `reset --reinstall` and the seeding recipe remain the way to test
it, since `deploy` never reinstalls.

### Switching between Testing and Production data

The seeded campaign is live (Production mode), so the Testing-only pages
cannot be reviewed on it: the Testing banner and labels, chosen-Family
Testing sends, Testing sign-in and submissions, the Go live page, Testing
cleanup and the structural edits only Testing allows. A Testing-mode
snapshot holds that state, and `switch` moves between it and the seeded
snapshot without changing the deployed code:

1. Once, with the seeded snapshot saved (`snapshot --seeded`):
   `tools/stewardship-local.sh seed --testing`. It switches to the
   post-setup snapshot (migrating it forward when it was saved on another
   image; one saved before snapshots recorded their migrations is first
   recorded with `reset` and then `snapshot`), checks that it is a
   Testing draft with an `@example.test` Testing recipient, clears Mailpit
   and moves the clock to real time. Add the schedules and make the Testing
   sends you want to review in the Admin portal, then
   `tools/stewardship-local.sh snapshot save testing`.
2. Deploy the pull request as above (`deploy`, with `--schema-change` if it
   migrates).
3. `tools/stewardship-local.sh switch testing` or `switch seeded`: the data
   changes, the code stays. When the snapshot was saved on the deployed
   image, only the data is copied, in about the time of a `reset`. When it
   was saved on another image, the switch starts it on that image and runs
   the Production upgrade's steps to the deployed image, as
   `deploy --schema-change` does (set the `STEWARDSHIP_USERS_*` variables
   for a snapshot from before the #952 conversion). A snapshot from newer
   code is refused before anything stops and the message names its commit.
   So is one saved before snapshots recorded their migrations on another
   image: `reset NAME`, then `snapshot save NAME --replace`, records it.
   After a failure the script names the `reset` that recovers.
4. Sign in again with the link the switch prints (sessions belong to the
   other data); Mailpit shows the snapshot's own mail.

`snapshot list` shows which way each switch would go. The snapshot itself
never changes, so a later switch to it migrates it again; `snapshot save
testing --replace` keeps the migrated copy. `reset NAME` is still the way back
to a snapshot's image and data together. Snapshots are full copies of the
runtime root, a few hundred MB each: `snapshot delete NAME` removes one you no
longer need.

## Changing ParishSoft data

pk-local's ParishSoft is the fake, serving the synthetic parish, so its data
does not change by itself. To exercise what a refresh does when the office
has changed something in ParishSoft (the review pipeline's
[refresh re-evaluation](../specs/stewardship/review-pipeline/spec.md#baseline-refresh-re-evaluation):
Confirmed, Entered incorrectly, Not seen in ParishSoft, Conflict, Not in
ParishSoft), change the fake's data with `fake-parishsoft`, then refresh. The
[specification](../specs/stewardship/local-environment/spec.md#fake-parishsoft-edits)
has the details.

Find the ids first. `show family ID` lists a Family's Members (with their
ids) and pledges, `show member ID` a Member's fields and Ministries, and
`show ministry ID` a roster:

```sh
tools/stewardship-local.sh fake-parishsoft show family 100003
tools/stewardship-local.sh fake-parishsoft show member 500010
```

Then make the change ParishSoft should hold. Each command checks the edit
against the parish (unknown records and fields, and values ParishSoft could
not hold, are refused), records it and restarts the fake in a few seconds.
The late-added Family the seed releases cannot be edited:

```sh
# A Member's census fields (an empty value clears a field; dates YYYY-MM-DD)
tools/stewardship-local.sh fake-parishsoft member 500010 email=new@example.test mobile=555-010-2000 suffix=Jr.
# A death (ParishSoft records both)
tools/stewardship-local.sh fake-parishsoft member 500010 death=2026-10-01 status=Deceased
# A move to another Family
tools/stewardship-local.sh fake-parishsoft member 500010 family=100007
# The Family's email opt-out, or another Family field
tools/stewardship-local.sh fake-parishsoft family 100003 no_mail=true
# Ministry joins and leaves (role=Member, Chairperson or Staff)
tools/stewardship-local.sh fake-parishsoft roster add 9003 500010
tools/stewardship-local.sh fake-parishsoft roster remove 9003 500010
# The upcoming year's pledge (default: the campaign's pledge fund and its
# financial period start; fund=ID and start=YYYY-MM-DD choose another), or
# none to remove it
tools/stewardship-local.sh fake-parishsoft pledge 100003 1800
```

`fake-parishsoft show` lists the recorded edits, `undo` drops the last one and
`clear` all of them. Then run the full refresh, as Refresh from ParishSoft in
the Admin portal would (pressing that button works too):

```sh
tools/stewardship-local.sh fake-parishsoft refresh
```

It waits until the refresh and the work it starts have finished. The next
full refresh sees an edit (every refresh is full since quick updates were
removed in [#1147](https://github.com/epiphany40223/parishkit/issues/1147)).

To try a ParishSoft key that reaches several organizations
([#1554](https://github.com/epiphany40223/parishkit/issues/1554)), make the
fake's key reach one or two more, then refresh:

```sh
tools/stewardship-local.sh fake-parishsoft organizations 2
tools/stewardship-local.sh fake-parishsoft refresh
```

The fake then also answers with a few records of the other organizations,
which the load leaves out. The ParishSoft settings page, System health's
ParishSoft refresh panel say how many organizations
the key reaches and how many records were left out; the parish data is the
same as before. `fake-parishsoft organizations 0` and a refresh go back to a
key for the parish alone.

To produce each refresh outcome for an item a Family submitted (value A in
ParishSoft before, C the answer being carried forward):

- **Confirmed**: mark the item Entered by hand (or update it), set
  ParishSoft to C (the Family's answer, or the reviewer's edited value),
  then `refresh`. An item still To review, Ready to update or To enter by
  hand keeps its stage and says "ParishSoft already holds this value."
  instead ([strict phase order](../specs/stewardship/review-pipeline/spec.md#baseline-refresh-re-evaluation),
  [#1890](https://github.com/epiphany40223/parishkit/issues/1890)).
- **Entered incorrectly**: mark the item Entered by hand, set ParishSoft to a
  third value, then `refresh`.
- **Not seen in ParishSoft**: mark the item Entered by hand and change
  nothing; `refresh` twice (the default number of full refreshes).
- **Conflict**: leave the item To review or reviewed, set ParishSoft to a
  third value, then `refresh`.
- **Not in ParishSoft**: move the Member to another Family or record a death,
  then `refresh`.

Edits live in the fake's configuration file, which snapshots carry: `reset
--seeded` (or a switch) brings back that snapshot's data, edits and all, so
one pull request's edits never leak into the next check.

### Updating the fake ParishSoft

While pk-local reads the fake, census publication is on, so Check ParishSoft
and Update ParishSoft run exactly as in Production and write the approved
changes to the fake
([specification](../specs/stewardship/local-environment/spec.md#fake-parishsoft-writes)).
With `deploy --parishsoft real` publication is off and every write is
refused: pk-local never writes to the real ParishSoft. As in Production,
Testing mode stops a run after Check ParishSoft, so switch to Production
mode to update.

Each write the fake accepts is recorded as one more `member` edit, so
`fake-parishsoft show` lists it and `undo` drops it. A full `refresh` then
reads the written values back and the published changes become Confirmed.
A `fake-parishsoft` edit stops the fake while it runs, so a write sent at
that moment fails as ParishSoft unavailable, as in an outage, and is never
lost after it was answered; run the update again for any change it left
failed.

Writes share the fake's bound of 500 edits with your own edits. Once it is
reached, every further write fails as ParishSoft unavailable, and the fake's
log says so; `fake-parishsoft clear` (or `undo`) or `reset --seeded` frees
edits. A Member of the late-added Family, which the fake serves after
`seed`, cannot be written: a change for that Member fails as refused by
ParishSoft.

### The Member contact smoke check

The [Member contact round trip](stewardship-smoke-tools.md#member-contact-round-trip)
runs the same probe and round-trip steps as Production, against the fake, while pk-local reads the
fake (with `deploy --parishsoft real` it is refused before it reads
anything). Its round trip writes only `pager` or `fax`, which the synthetic
parish leaves unset, so give the test Member one first, then run the probe
and the round trip in the worker (the fake's organization is `1001`):

```sh
tools/stewardship-local.sh fake-parishsoft member 500010 pager=555-010-0100
limactl shell parishkit-local -- sudo docker exec parishkit-local-worker-1 pk-stewardship smoke --config /opt/parishkit/config/services/worker.yaml --target parishsoft_contact --organization-id 1001 --member-id 500010 --confirm-member-id 500010 --send
```

The marker and restore writes are recorded as `member` edits, so
`fake-parishsoft show` lists them; `fake-parishsoft clear` (or `undo`) drops
them with your own edits.

## Rehearsing a bulk send

`rehearse` measures one scheduled Family send, for the
[faster bulk Family send](../plans/stewardship/background-processing.md#bg-12-faster-bulk-family-send)
(BG-12) and its rehearsal protocol. It runs on a seeded deployment, which is
in Production mode like the real one, and changes nothing about how mail is
sent. It schedules one more Reminder, as an Administrator could, so restore
the snapshot (`reset --seeded`) before the next run.

1. Choose what to measure with `deploy`:
   - `--bulk off` for the one-at-a-time path, or `--bulk on` for the bulk path.
   - `--smtp-latency-ms 600`, so each message waits about as long as Gmail
     takes; Mailpit itself answers at once. The setting is admitted only in
     the local profile.

   Both settings carry over to later deploys. `deploy --rollback` does not
   carry the latency into the previous release, which may not know it;
   deploy `--smtp-latency-ms` again afterwards if you need it. Before you
   `deploy` a build older than the rehearsal harness, run
   `deploy --smtp-latency-ms 0` first: an older image refuses the setting,
   and that refusal would come at the retarget, with web already down.
2. Run `tools/stewardship-local.sh rehearse --label bulk-100`. It:
   - adds one Reminder due a whole minute five minutes ahead (`--due-in`),
     through the seeder's `reminder` step;
   - samples the work-order lock once a second (read-only queries with a
     five-second statement limit);
   - waits until every message has settled, giving up `--timeout` minutes
     (default 60) after the due time, plus a two-minute margin (a timed-out
     run is still measured and reported, and fails);
   - runs the seeder's read-only `measure` step under the offline migration
     identity, since the web login may not read the outcome evidence;
   - prints the report, and exits non-zero unless the run passed its
     correctness check within the timeout.

   With `--send-only`, mail-dispatch is stopped until every occurrence has
   its message and no preparation task is left, then started again, which
   gives the send-only rate. If the run fails, its exit trap starts
   mail-dispatch again before it prints anything or stops the sampler, and
   writes its messages to the run's log file in the VM
   (`/var/log/stewardship-local-rehearse-*.log`), since the connection to
   the laptop may be gone.
3. Read the report. Its files stay in the VM under
   `/var/log/stewardship-rehearsal-<label>-<time>/`: `report.txt`,
   `summary.json`, and the inputs `meta.json`, `measure.json`,
   `timings.jsonl` and `locks.tsv`.
4. `reset --seeded`, then the next build or path.

The report shows:

- the time from the due time to the last outcome, and accepted messages a
  minute;
- preparation time (due time to the last message created), and for
  `--send-only`, the send-only time and rate;
- the [mail send report](stewardship-mail-send-report.md)'s phases (`wait_ms`
  through `total_ms`; `submit_ms` includes the modeled latency);
- for the bulk path, each kind of lock transaction (`prepare`, `commit` for
  the "submitting" commit half, `outcome` for each outcome chunk, whose
  items include any outcome it could not record) with its batches, items,
  lock hold and the time per item under the lock;
- `work`, the render, decrypt or seal part of each item: the work BG-12
  moves outside the lock;
- `build`, the time of each build made outside the lock before a
  preparation batch (BG-12 PR 2), used or dropped;
- `prebuilt` and `rebuilt` counts: Production preparation items written from
  a build made outside the lock, and those rebuilt under it because an
  input changed (sending items stay zero until BG-12's PR 3);
- lease renewal waits (from the start of each renewal, so they include
  opening its connection as well as waiting for the lock);
- the lock samples: the share of seconds the lock was held or waited on, its
  holders' states and roles, its waiters' roles, CPU busy share and load;
- correctness: one accepted message per candidate Family (by distinct
  message, and no message accepted twice), no Family with two messages or
  two fulfillments, no `delivery_unknown` (one per occurrence), no failure,
  nothing unfinished, no timeout, the seed's timestamp-ordering invariants,
  and the deadlock (`40P01`) count from `pg_stat_database`.

The seed's full invariant check is not run: besides the ordering checks it
compares counts against the seeded now and the seed's timeline, which stop
holding as soon as time moves on and a rehearsal adds a Reminder.

The timing lines are DEBUG lines, so the deployment needs debug logging,
which is on by default (`PARISHKIT_LOCAL_DEBUG_LOGGING`). Without it, the
report shows no lock holds or renewal waits. The one-at-a-time path logs no
lock holds; there the lock samples and the send statistics are the
measurements.

**Not yet supported: the 1,100-Family invitation.** The protocol's larger
size (plan steps 3 and 4) measures a 1,100-Family campaign's initial
invitation end to end, activated to Production with its Initial due a few
minutes ahead. `rehearse` measures a Reminder on a seeded deployment, and
the seeder only activates a campaign under the fake clock, so that run is
deferred to a later BG-12 pull request, before PR 4's acceptance run. The
Reminder runs work at any parish size `up --families` gave the deployment.

## Checking the launch-day spike

`spike` measures what Families do when the launch email arrives: open the
link, load the form and submit, through Caddy and gunicorn, while mail
dispatch is still sending (#392 M3). The
[specification](../specs/stewardship/local-environment/spec.md#launch-day-spike-check)
is the contract. It writes real submissions, so it runs only on this local
deployment, never against Production.

1. Start from a seeded deployment (`reset --seeded`, or `seed` then
   `snapshot --seeded`) with the parish size you want to check (`up
   --families N`).
2. Run it beside a send, which is the launch-day case:

   ```text
   tools/stewardship-local.sh spike --families 1000 --during-send --wait 60
   ```

   It adds a Reminder due three minutes ahead, and as each Family's email
   lands in Mailpit, a shard container opens it. Without `--during-send` it
   uses the mail already in Mailpit (for example after a `rehearse`).
3. Read the report: each step's p50, p95 and maximum against its target,
   and any Family that was limited, refused or got no answer. It passes only
   when all `N` Families ran (a Family with two emails counts once), every
   shard reported, every Family's every step answered as expected and each
   p95 is within target. Fewer Families than asked before `--wait` fails the
   run; the log names the shard that waited. The files (each shard's document, the run record and the
   report) are in `/var/log/stewardship-spike-LABEL-TIME` in the VM.

Each submission is the Family's form returned unchanged, so the seeded
response pattern changes: take a snapshot first, and `reset --seeded`
afterwards when you want the seeded data back.

`--sources` is how many distinct addresses the Families come from (default
8). The access bucket allows a burst of 30 sign-ins per address and then 2
a second, so with very few sources the check measures the limiter rather
than the server; a launch-day crowd behind one parish Wi-Fi network is that
case.

## Day-to-day

- Changed code? `deploy` builds the new image and upgrades the running
  deployment in place, data kept (about half a minute after the image build,
  web down for about five seconds); `reset --seeded` returns to the snapshot.
  `reset --reinstall` builds the new image, then wipes and reinstalls,
  keeping any snapshot (about one to two minutes: the first image build
  takes about a minute, a cached rebuild a few seconds, the install about a
  minute). `reset` with no post-setup snapshot does the same.
- `status` shows every container's health. Service logs are in the VM:
  `limactl shell parishkit-local -- sudo docker compose -p parishkit-local logs --tail 50 web`.
- `down` stops everything and keeps the data; `start` brings it back (also
  after a VM restart). `up` is first-time install only.
- The runbook's inspection commands work inside the VM as on a production
  host: `limactl shell parishkit-local -- sudo bash`, then the
  [deployment runbook](stewardship-deployment-runbook.md#commands-and-generated-paths)'s
  `docker compose -f /opt/parishkit/config/services/compose-initial.json -p parishkit-local ...`.

## Signing in with Google

`up` installs a stand-in (sentinel) OAuth client, so pk-local starts with
only the test sign-in (`sign-in --email E`). To try the real Google sign-in,
install a Google OAuth client of your own. pk-local signs you in with the
same code as Production: the Google address must be one of the
deployment's Admin users, and step-ups work as there. The test sign-in keeps
working beside it.

1. In a Google Cloud project you use for testing, configure the OAuth
   consent screen (Internal) first, then create a new OAuth client of type
   **Web application**. Its only authorized redirect URI is
   `https://localhost:8443/admin/oauth/callback`. Never use or edit
   Production's client: it has no localhost redirect URI, so Google refuses
   it here, and a localhost URI on it would let a laptop take its sign-ins.
2. Save a file holding only
   `{"client_id": "...", "client_secret": "..."}` from that client, outside
   every git checkout, readable only by you, for example
   `~/.parishkit-local/credentials/google-oauth.json` with the directory at
   mode 0700 and the file at mode 0600 (`chmod 600`).
3. Use a deployment on the normal clock (a seeded one, or after
   `seed --testing`):
   `limactl shell parishkit-local -- sudo cat /opt/parishkit/run/local/clock/mode`
   prints `normal`. Google's tokens and certificates cannot be checked against
   the fake clock's date in the past, so under the fake clock web refuses a
   real client, and `start`, `seed` and the other commands that start
   services under the fake clock refuse before they start anything, with
   "A real Google OAuth client is installed, and this deployment runs under
   the fake clock …". A `deploy` under the fake clock goes on to web's own
   refusal, which web's log names. See [Clock mode](#clock-mode).
4. Install it, streamed into the existing credential file so no other
   copy is left in the VM (the file keeps its owner and mode 0600), then
   check it before restarting web:

   ```sh
   f=~/.parishkit-local/credentials/google-oauth.json
   limactl shell parishkit-local -- sudo tee \
       /opt/parishkit/credentials/google_oauth/credential >/dev/null <"$f"
   limactl shell parishkit-local -- sudo docker exec parishkit-local-web-1 \
       pk-stewardship smoke --target google_oauth \
       --config /opt/parishkit/config/services/web.yaml
   ```

   Writing into the file keeps its inode, so the running web container,
   which mounts that file, already sees the new content and the check reads
   it. A valid client prints one JSON line with `"credential": "valid"` and
   the redirect URI to register. Under the fake clock it exits 2 with
   `ERROR: A real Google OAuth client needs the normal clock: …`; any other
   problem with the file exits 2 with the generic
   `ERROR: smoke check refused or failed; …` line.
5. Restart web:
   `limactl shell parishkit-local -- sudo docker restart parishkit-local-web-1`.
   If web does not come back, its log
   (`… logs --tail 50 web`, under [Day-to-day](#day-to-day)) names the
   refusal. The sign-in page now offers **Sign in with Google** above the
   test sign-in instructions.

To go back, write the sentinel the same way, from a file holding
`{"client_id": "local-environment-sentinel.invalid", "client_secret": "local-environment-sentinel-not-a-secret"}`,
and restart web. Put the sentinel back before seeding: the seeder runs under
the fake clock and refuses a real client. `reseed` and `seed --testing` start
from the post-setup snapshot, which always holds the sentinel. The client
secret stays on the laptop, but every `snapshot` copies it, and `reset` or a
snapshot switch puts back the client that snapshot holds. When you are done,
delete the snapshots taken with the real client, delete the saved file, and
delete the test client in Google Cloud.

## Sending real mail to one address

pk-local normally sends everything to Mailpit. To exercise the real Gmail
path (Family mail, reminders, alerts, samples, `smoke --send-to`) and the
off-site Drive copies, install a key for a test mailbox beside a mail
redirect. The redirect replaces every recipient of every message with one
address and rewrites `To` to it, so nothing can reach anyone else, in
Testing or Production mode, for any kind of mail. With real ParishSoft data
the messages carry real names, gifts, codes and links, which is allowed
only to the one redirect address (Administrator decision, 2026-10-17).

1. In the parish Google Workspace, use a separate test mailbox and a
   separate service account for it with domain-wide delegation of
   `https://mail.google.com/` (and `https://www.googleapis.com/auth/drive`
   for the Drive copies), as the
   [deployment runbook](stewardship-deployment-runbook.md) describes for
   Production. Never use Production's key.
2. Save the JSON key outside every git checkout, readable only by you, for
   example `~/.parishkit-local/credentials/google-workspace.json` with the
   directory at mode 0700 and the file at mode 0600.
3. Use a deployment on the normal clock (`run/local/clock/mode` says
   `normal`; see [Clock mode](#clock-mode)): the Integrations page, smoke
   and the Drive copies refuse a real key under the fake clock, as web
   refuses a real OAuth client.
4. Set the redirect to your Testing recipient:
   `tools/stewardship-local.sh deploy --mail-redirect you@example.org`. The
   deploy prints "every message goes only to the mail redirect …". The
   address can be set only this way, never from the Admin portal.
5. On **Integrations › Google Workspace mail**, change the delegated user,
   sender and reply-to to the test mailbox and paste the key into the key
   field. Saving checks the key with Google before it is applied. Without
   the redirect, or under the fake clock, the key is refused as invalid.
6. Check it from a mail sender:
   `limactl shell parishkit-local -- sudo docker exec parishkit-local-worker-1 pk-stewardship smoke --target google_workspace --config /opt/parishkit/config/services/worker.yaml --delegated-email <test mailbox> --send-to you@example.org`.
   The one fixed smoke message reaches the redirect address.

The modeled latency (`--smtp-latency-ms`) applies only to Mailpit; real
Gmail has its own. With the redirect and the key in place, **Off-site
backups** can also be set to a test Drive folder: the copies are sealed to
the backup recipient key, so the folder holds nothing readable, and they are
tagged with this deployment, so they never mix with another deployment's.
Use a separate test folder. Copies from before a `reset --reinstall` or a
snapshot switch are never pruned; delete old set folders there by hand.

To go back, put the mail-catcher document back on the same page (its
contents are `{"type": "parishkit-mail-catcher"}`), then
`deploy --mail-redirect off`. The key stays on the laptop, but every
`snapshot` copies it, so delete snapshots taken with it when you are done,
delete the saved file, and delete the key in Google Cloud.

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
3. Networks: inside the VM, with `dc="sudo docker compose -p parishkit-local"`:
   - `$dc exec caddy wget -T 5 -O- http://1.1.1.1/` and
     `$dc exec mailpit wget -T 5 -O- http://1.1.1.1/` fail;
   - `$dc exec fake-parishsoft python3 -c "import socket; socket.create_connection(('1.1.1.1', 80), 5)"`
     fails, and the same command run in `web` (or `worker`) succeeds (a
     plain TCP connection, so no certificate is checked against the fake
     clock);
   - `for c in $($dc ps -q); do sudo docker inspect --format '{{.Name}}: {{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}' "$c"; done`
     lists each container's networks: web is on `backend`, `proxy` and
     `application-egress`, and Caddy, Mailpit and `fake-parishsoft` are not
     on `application-egress`;
   - `sudo docker network inspect parishkit-local_ingress --format '{{index .Options "com.docker.network.bridge.enable_ip_masquerade"}}'`
     prints `false`.
4. Ownership: `/opt/parishkit` is `10001:10001` mode `0700`; credential and
   fake-configuration files are `0600` owned by `10001`.
5. `snapshot`, `down`, `start`, `reset`, `reset --reinstall` and `status`
   round-trip.
6. The seed: `wizard` (or the browser wizard), `snapshot`, `seed` at 20 and
   at 100 Families (`PARISHKIT_LOCAL_FAMILIES` for `reset --reinstall`),
   `snapshot --seeded` and `reset --seeded`, with the
   [seed tests](../specs/stewardship/local-environment/spec.md#seeder-tests)'
   assertions read off the database and Mailpit.
7. The deploy, on the seeded deployment: `deploy` of the current checkout
   (the advisory check answers `t`; the per-table row counts are unchanged
   afterwards and every service is healthy), `deploy --rollback` to the
   replaced image, a `deploy` of a schema-changing branch refused without
   `--schema-change` and migrating with it, `deploy --rollback` refused after
   that migration, then `reset --seeded`.

The record of the first such run (2026-10-04) is the
[OPS-10.09 VM run record](https://github.com/epiphany40223/parishkit/issues/476#issuecomment-5976332758)
on issue #476, as is the first `deploy` run (step 7, 2026-10-04): a deploy
of the current checkout took 30 s with web down 5 s (advisory check `t`,
backup recorded with `offsite: not_configured`, 20 online services healthy,
every data table's row count unchanged); `deploy --rollback` took 31 s with
web down 5 s, reusing the kept static tree; a build carrying the migration of
pull request #485 was refused without `--schema-change` before anything
stopped (22 s including the image build) and with it took 30 s, web down 8 s,
migration and grants 3 s; `deploy --rollback` after that migration was
refused before anything stopped; `reset --seeded` restored the seeded
snapshot in 36 s.

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
for why a LOCAL deployment cannot email a real Family, and what each
integration can reach.

Slack is one of the real providers the VM can reach. Install only a separate
test bot token whose Slack app is in a test channel, never the production
token: nothing in the code can tell them apart, and the local environment's
alerts carry no environment label, so with the production token they would
post to the production channel looking much like production's own.
