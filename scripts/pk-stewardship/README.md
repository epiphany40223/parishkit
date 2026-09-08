# Stewardship command

Follow the [locked checkout installation](../../docs/development/stewardship.md#checkout-installation).
Run `pk-stewardship --version` or
`pk-stewardship --config scripts/pk-stewardship/example-config.yaml config-check`.
The wrapper in this directory delegates to the same package entry point.

`config-check` currently checks YAML readability/mapping syntax only. It prints
no keys, values, or secret paths and explicitly reports that deployment has
not been validated. It does not initialize Django or contact a provider.
No bootstrap, publish, or purge command is available yet.

`validate-deployment` checks the typed deployment metadata, including profile,
origin, proxy hops, service role, paths, and connection settings. It prints only
success flags, explicitly distinguishing metadata validation from startup
readiness. `--profile`, `--service-role`, `--public-origin`, and `--runtime-root`
override the corresponding environment/YAML settings. See the
[deployment schema](../../docs/development/stewardship-deployment.md).

`prepare-development --runtime-root PATH` creates the local development runtime
directories and a PostgreSQL password file. This command writes to disk; it is
not a validation-only command. Its parent directory must exist; an existing
target, including a symlink, is refused without changes.

`service` starts the selected service role. Currently only the development web
role is implemented; production and reserved roles refuse startup.
The development server defaults to `127.0.0.1:8000`. Compose explicitly supplies
`--bind-all-interfaces` so the server is reachable inside its container, while
publishing the host port only on localhost. Supplying that flag directly on a
workstation exposes the debug server to its other network interfaces; do not
use it on an untrusted network. The flag requires the development web role.
`healthcheck` probes the development web liveness endpoint and returns a
nonzero status when it is unavailable. See the
[Compose guide](../../docs/development/stewardship-compose.md) for invocation
examples, prerequisites, and unfinished service boundaries.

## Supported options

The scaffold accepts only implemented options. Unsupported shared flags such
as `--dry-run`, logging flags, and provider flags fail with exit status `2`;
they are not silently ignored. In particular, `prepare-development` has no
dry-run mode. Options that belong to another command also fail before any
filesystem, network, or process operation. Long option names must be spelled
in full; abbreviations are not accepted.

| Command | Accepted options |
| --- | --- |
| `config-check` | `--config` (required) |
| `validate-deployment` | `--config`, `--profile`, `--service-role`, `--public-origin`, `--runtime-root` |
| `service` | `--profile`, `--service-role`, `--bind-all-interfaces` (development web only) |
| `healthcheck` | None |
| `prepare-development` | `--runtime-root` (required) |

Options can precede or follow the command. `--version` must be used alone.
The coverage runner (`python -m parishkit.stewardship.quality`) accepts only
`--repository-root` and required `--report`, plus `--help`. Other ParishKit tools
retain their existing shared options.

For the current implementation scope, see the
[architecture tasks](../../docs/tasks/stewardship/architecture.md) and
[dependency and development notes](../../docs/development/stewardship.md).
