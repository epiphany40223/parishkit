# ParishKit Sync — Specification

ParishKit Sync is a small web portal that runs ParishKit's periodic
integrations: ParishSoft to Google Groups, ParishSoft to Constant Contact,
ministry roster spreadsheets, and Google Calendar reservation checks. It
replaces the command-line tools specified under `docs/specs/pk-*/`. Once each
module reaches parity with its tool, that tool is deleted; there is no
command-line backward compatibility.

This file is the overview. Subsystem specs hold the detail and link to each
other rather than repeating it:

| Spec | Covers |
|---|---|
| [core](core/spec.md) | Django app, sign-in, administrators, first-run setup, settings |
| [credentials](credential-store/spec.md) | Encrypted credential store, master key, OAuth refresh |
| [jobs](jobs/spec.md) | Scheduler, dataset graph, child processes, progress, job control |
| [modules](modules/spec.md) | Module manifest, config schema, generated config UI, durable references |
| [providers](providers/spec.md) | Identity, file upload, mail and notification interfaces |
| [logging](logging/spec.md) | Structured log, audit trail, search, export, retention |
| [deployment](deployment/spec.md) | Image, release, installer, updater, verification, disk budget |

Module specs: [ParishSoft download](parishsoft-download/spec.md),
[Google Groups](google-groups/spec.md),
[Constant Contact](constant-contact/spec.md),
[ministry rosters](ministry-rosters/spec.md) and
[calendar reservations](calendar-reservations/spec.md).

General UX rules come from the
[Stewardship UX conventions](../stewardship/ui-conventions/spec.md), which
Sync follows by link. Sync-specific UX lives in the subsystem specs.

Tracking: epic [#2205](https://github.com/epiphany40223/parishkit/issues/2205)
and its sub-issues.

## Goals

1. **Unattended, periodic and loud on failure**, like the command-line tools
   it replaces (see the [system spec](../intro/spec.md#design-intent-and-goals)).
2. **Small.** One parish per VM. A 1 GB VM is the comfortable target; a
   512 MB VM is supported (see [Footprint](#footprint)).
3. **Configured in a web UI**, not YAML. Modules declare what they need; the
   core renders the UI, stores the config and keeps the history.
4. **Holds no upstream data** between runs (see
   [No cached upstream data](#no-cached-upstream-data)).
5. **Modular.** Sign-in, uploads, mail and notifications are provider
   interfaces with pluggable back-ends; functionality comes from
   self-contained modules that declare their dependencies.
6. **Secure updates.** It updates itself by default, and every image is
   verified before it runs.
7. **Resilient to renames.** Config binds durable upstream IDs, not names.

## Product boundaries

- **Name and identifiers.** Product name *ParishKit Sync*; Python package
  `parishkit.sync`; image `ghcr.io/epiphany40223/parishkit-sync`; release tags
  `sync-vX.Y.Z`. Stewardship's tags become `stewardship-vX.Y.Z` in
  [#2203](https://github.com/epiphany40223/parishkit/issues/2203).
- **Separate from Stewardship.** Sync and Stewardship are separate portals,
  each with its own image and release. Neither imports the other. Code that
  both use lives in `src/parishkit/` outside `sync/` and `stewardship/`, and
  an import-boundary test enforces this (see [CI and release](#ci-and-release)).
- **No mass move.** Shared modules stay where they are today. New Sync code
  goes in `src/parishkit/sync/` and its tests in `tests/sync/`.
- **One parish per VM.** One ParishSoft organization per installation.
- **All modules in one VM.** Every module runs in the same container on the
  same VM. VM size only changes how many module runs may run *at the same
  time* (see [jobs](jobs/spec.md#concurrency)).

## Architecture

```text
              Browser (administrators)
                       │  HTTPS (parish DNS name)
                       ▼
       Caddy container: TLS, automatic certificates
                       │  HTTP on a private Podman network
┌──────────────────────┼─────────────────────────────────┐
│ Sync container       ▼                                  │
│  gunicorn (1 worker) — Django web app ─── SQLite (WAL)  │
│        │                                  config,       │
│        │ scheduler thread                 credentials,  │
│        ▼                                  logs, audit   │
│  child process per module run ──► per-run tmpfs         │
│  (ParishSoft, Google, Constant Contact)   datasets      │
└─────────────────────────────────────────────────────────┘
        ▲ verified by digest
 Host: Podman + systemd, host-side updater, master-key keyring
```

- One Sync container, run by Podman under systemd. Podman is the only
  supported runtime.
- A Caddy container in front terminates HTTPS (see
  [deployment](deployment/spec.md#https)).
- Django and SQLite. No PostgreSQL, Celery or Valkey.
- The web process hosts the scheduler; each module run is a child process
  (see [jobs](jobs/spec.md)).
- The updater runs on the host, not in the container (see
  [deployment](deployment/spec.md#updater)).

## No cached upstream data

Sync keeps only configuration, encrypted credentials, administrator records,
logs, audit records and small per-module state. It does not keep ParishSoft,
Google or Constant Contact data between runs:

- A run's datasets (for example the ParishSoft download) live in a per-run
  temporary directory that is deleted when the run ends (see
  [jobs](jobs/spec.md#datasets)).
- Lookup results for config pickers may be held in memory for a few minutes
  at most, never on disk.
- **Exception: the current-names table.** For each entity that the config
  references, Sync stores the entity's latest name so the UI can show it.
  It holds names only, never members or contact data (see
  [modules](modules/spec.md#current-names-table)).
- Logs can contain parishioner names and email addresses; retention and
  redaction bound them (see [logging](logging/spec.md#retention)).

## Footprint

Measured on 2026-10-25 on a 512 MB DigitalOcean droplet (1 vCPU, 458 MB
usable, stock Ubuntu 24.04, Podman 4.9.3, no swap) with a production-sized
parish (about 1,300 families and 2,900 members)
([#2211](https://github.com/epiphany40223/parishkit/issues/2211)):

| Measurement | MB |
|---|---|
| Host "used" after boot settled, before Podman | 174 |
| Podman installed, idle (no daemon) | +2 |
| Child: Python / + ParishSoft client / + Google client libraries | 12 / 31 / 50 |
| Child after the full ParishSoft load (about 77 s, network-bound) | 106 |
| Child peak while serializing the dataset | 137 |
| Serialized ParishSoft dataset | 7.4 |
| Child after releasing the data (not returned to the OS) | 103 |
| Stock Django + gunicorn (1 worker) + SQLite, after 20 requests | 77 |
| **Web plus one child: host used peak** | **341 (116 available)** |

Consequences:

- A 512 MB VM runs one module child at a time. The real portal should add
  about 20–40 MB over stock Django, which still fits.
- A 1 GB VM can run two or three children at once.
- Children are required: released memory stays in the process, so module
  work must not run in the long-lived web process.
- Datasets are small; write them in a streaming format rather than one large
  in-memory serialization (which cost a 30 MB spike).
- The installer disables `multipathd` (27 MB, useless on a single-disk VM)
  and adds a 1 GB swapfile as a safety net only.

Re-measure once the core app exists
([#2212](https://github.com/epiphany40223/parishkit/issues/2212)).

## Language and stack

Python, matching the rest of ParishKit. Go helpers only where a concrete need
appears (for example, if the host updater outgrows a short script).

## CI and release

- **Per-product CI.** A classifier (`tools/ci/affected.py`) maps changed
  paths to products (stewardship, sync, shared, infrastructure) and runs only
  the job groups a change can affect. Stewardship's existing decisions
  (`parishkit.stewardship.quality_paths`) must stay identical. Sync owns the
  classifier ([#2207](https://github.com/epiphany40223/parishkit/issues/2207)).
- **Import boundary.** A test fails if `parishkit.sync` imports
  `parishkit.stewardship` or the reverse, or if shared code imports either.
- **Per-product test settings.** `tests/sync` does not run under Stewardship's
  Django settings.
- **Release evidence.** A release of product P at commit T is satisfied by a
  green full P run at a main commit C when the classifier says nothing in
  C..T can affect P's jobs; otherwise the release runs a full P run at T.
  Stewardship owns this change
  ([#2208](https://github.com/epiphany40223/parishkit/issues/2208)), building
  on [#2203](https://github.com/epiphany40223/parishkit/issues/2203).
- **Sync releases** are described in
  [deployment](deployment/spec.md#release-and-image).
- **Priority.** Stewardship comes first. Sync work that uses CI or touches
  shared code is scheduled in low-impact windows agreed with the Stewardship
  work.

## Retiring the command-line tools

When a module reaches parity with its tool (as defined by that tool's spec),
the same PR that completes the module deletes the tool's entry point,
`scripts/pk-*` wrapper and CLI-only tests. Shared logic and its tests stay in
shared code or move to `tests/sync`. After the last module, `pk-cron-runner`,
the CLI-only shared helpers and the CLI specs are retired, and the project
conventions in `CLAUDE.md` (scripts/ wrappers, YAML config, the
`/opt/parishkit` layout) are updated
([#2227](https://github.com/epiphany40223/parishkit/issues/2227)).
Stewardship's uses of shared helpers must keep working.

The two lookup tools, `pk-query-ps-memfam` and `pk-print-ps-ministries`, are
dropped with no Sync equivalent; the ministry picker covers browsing
ministry names.

## Legacy configuration import

A one-time back-end command imports existing YAML configs; it is not an
administrator UI feature (see [modules](modules/spec.md#legacy-yaml-import)).

## Rejected approaches

- **Rust or Go rewrite.** Memory is dominated by the OS, the container
  runtime and the data, not the language. A rewrite would discard tested
  integration code, and Rust lacks official Google and Microsoft Graph SDKs.
- **Reusing Stewardship's runtime** (PostgreSQL, Valkey, Celery, many
  services): suited to Stewardship's data and threat model, far too heavy for
  a stateless 512 MB VM.
- **Running modules in the web process.** Python rarely returns memory to the
  OS, threads cannot be killed safely, and one bad module could take down the
  UI.
- **Wrapping the CLI tools with fork/exec.** No CLI compatibility is needed,
  and module functions make dataset sharing natural.
- **Moving shared code into `src/parishkit/common/`.** It would conflict with
  in-flight Stewardship work and risk stored dotted paths, for little gain.
- **Separate VMs per module.** Unnecessary; one VM runs every module.

## Glossary

- **Module** — a self-contained unit of functionality with a manifest.
- **Job** — one schedulable entry point of a module; a module may have
  several.
- **Run** — one scheduler tick or manual trigger; contains steps.
- **Step** — one job execution inside a run, as one child process.
- **Dataset** — named data a step produces for other steps in the same run.
- **Reference** — a config value that points at an upstream entity by its
  durable ID.
- **Preview** — a dry run that shows planned changes without writing.
