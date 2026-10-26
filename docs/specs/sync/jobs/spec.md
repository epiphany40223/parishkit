# ParishKit Sync — Jobs, Runs and Progress

How Sync schedules module work, runs it in child processes, shares data
between modules and reports progress. Overview: [Sync spec](../spec.md).
Module manifests: [modules](../modules/spec.md#manifest).

## Model

- A **run** is one scheduler tick or one manual trigger.
- A run contains **steps**, one per module job, ordered by the dataset graph.
- Each step runs as **one child process**.

Step states: `queued`, `running`, `succeeded`, `failed`, `cancelled`,
`timed_out`, `stalled`, `skipped` (a dependency failed) and `interrupted`
(the portal restarted mid-run).

## Scheduling

- Each job has a schedule (a default from the manifest, adjustable by the
  administrator) and can be disabled.
- On each tick the scheduler collects the jobs that are due, then adds the
  producer jobs whose datasets they consume.
- A job never overlaps itself; a due job whose previous run is still active
  is skipped and logged. This rule covers scheduled and Run now runs only.
  [Preview runs](#preview-runs) neither hold nor check it: they write
  nothing upstream and use their own datasets, so a Preview never causes a
  scheduled run to be skipped (it can only make one wait for a concurrency
  slot).

## Datasets

- Manifests declare datasets they **produce** and **consume** (for example
  `parishsoft.core`).
- A producer runs **once per run** for all due consumers. A consumer whose
  producer failed, or whose producer was skipped because a previous run of
  it was still active, is also `skipped`, and the log names the reason. The
  consumer runs again on its next tick. A job skipped on three consecutive
  ticks raises an alert, so a slow producer can never silently starve its
  consumers.
- Datasets are written to a per-run temporary directory (tmpfs) in a
  streaming format and deleted when the run ends. Nothing persists between
  runs (see [No cached upstream data](../spec.md#no-cached-upstream-data)).
- Two consumers with different schedules share a producer only on ticks where
  both are due.

## Concurrency

- Setting: **maximum concurrent module runs**, default **1**.
- With 1, steps queue and run one after another, as `pk-cron-runner` does
  today. This is required on a 512 MB VM (see
  [Footprint](../spec.md#footprint)).
- On a 1 GB VM it can be raised to 2–3, so independent consumers run in
  parallel once their producer finishes.
- The setting never means one module per VM; every module runs on the same
  VM.

## Child processes

- Started by the scheduler as `python -m parishkit.sync.worker <step-id>`
  (spawn semantics, not fork).
- The child reads its step context from the database: module, job, config
  revision (or preview document, below), credential bindings, input dataset
  paths, Preview flag.
- The child runs in its own process group so the whole tree can be killed.
  The scheduler records each step's process identity in the database: the
  process-group ID, the group leader's start time (from `/proc/<pid>/stat`),
  and a **container instance ID**. A bare process ID is never enough,
  because IDs restart in a new container.
- The container instance ID is generated **once, when the container
  starts**, by its entrypoint (outside gunicorn), and written to a tmpfs file
  that every web worker reads. It survives gunicorn worker replacements
  within the same container and changes only when the container itself
  restarts, which is exactly when old children are guaranteed gone.

### Preview runs

- **Preview this change** stores the unsaved edits as a transient **preview
  document** (never a revision) and starts a run whose step reads it instead
  of a saved revision. The document is deleted when the run ends.
- A Preview run includes the producers its job consumes (for example the
  ParishSoft download, about 77 s), like any run, and always produces its
  own datasets. It never borrows another run's datasets, which are deleted
  when that run ends.
- A Preview run counts toward the maximum concurrent module runs and queues
  like any other run; the UI shows its place in the queue.
- Rationale: memory returns to the OS when the child exits, timeouts can kill
  hung calls, and a crashing module cannot take down the web UI.

### Module API

Modules receive a context object:

- `ctx.config` — the typed config (see [modules](../modules/spec.md)).
- `ctx.credentials` — resolved credential slots.
- `ctx.datasets` — readers for consumed datasets and writers for produced
  ones.
- `ctx.state` — the module's key-value state store.
- `ctx.mail` — the core send-email service, routed to the configured Mail
  back-end (see [providers](../providers/spec.md#mail)); in a Preview it
  records what would be sent instead of sending.
- `ctx.preview` — true for a dry run; modules must not write upstream.
- `ctx.log` — the structured logger.
- `ctx.phase(name, total=..., unit=...)` — a progress phase with
  `advance(n)`.

Shared libraries report progress automatically where they can (for example,
ParishSoft pagination knows its page counts).

## Progress protocol

The child writes newline-delimited JSON to a dedicated pipe (not stdout):

| Event | Fields |
|---|---|
| `progress` | `phase`, `done`, `total`, `unit`, `message` |
| `log` | `level`, `msg`, `fields` |
| `counts` | `added`, `removed`, `updated`, `unchanged`, `errors` |
| `heartbeat` | — |
| `result` | `status`, summary |

The parent:

- writes `log` events to the log table, tagged with run and step (see
  [logging](../logging/spec.md));
- keeps the latest progress in memory and saves it at most every couple of
  seconds;
- samples each child's RSS and CPU from `/proc`, shows them live and records
  peak memory per step;
- captures child stdout and stderr as log records.

## Job control

- **Run now** and **Preview** for any enabled job.
- **Cancel:** SIGTERM to the process group, a grace period, then SIGKILL.
- **Timeouts:** a total timeout and a separate **stall timeout** (no heartbeat
  or progress) per job, with manifest defaults the administrator can change.
- Every kill logs what was killed, the limit and the elapsed time.
- **Fencing on restart:** on portal start (or when a web worker is
  replaced), before scheduling anything, the scheduler finds steps still
  marked `running`. A step recorded by a **different container instance** is
  marked `interrupted` without sending any signal: its processes died with
  that container, and its numeric IDs may now belong to unrelated processes.
  For a step from the **current** instance, the scheduler signals the group
  only if its leader still exists with the recorded start time; it sends
  SIGTERM, then SIGKILL after the grace period, and confirms no member of
  the group is still alive. Only then are the steps marked `interrupted` and
  their jobs released. A group that cannot be confirmed gone keeps its job locked and
  raises an alert, so a surviving child can never overlap a new run of the
  same job.
- The process-tree timeout handling comes from `pk_cron_runner.py`.

## Visibility

- A live view per run shows each step's state, phase, progress bar, counts,
  RSS and CPU, refreshed in place about every 2 seconds.
- Run history shows duration, outcome, counts and peak memory; each run links
  to its logs filtered by run and step.
- Failures and timeouts send a notification (see
  [providers](../providers/spec.md#notify)).

## Rejected approaches

- **Threads or async tasks in the web process** (see the
  [overview](../spec.md#rejected-approaches)).
- **Celery or another broker:** too heavy for the footprint.
- **Server-sent events or websockets for the live view:** they hold
  connections open on a single worker; in-place polling is enough.
