# Stewardship deployment metadata

This documents the implemented portion of
[ARC-02](../tasks/stewardship/architecture.md#arc-02-shared-cli-configuration-paths-and-app-startup).
The [architecture specification](../specs/stewardship/architecture/spec.md#configuration-and-secrets)
owns the separation of deployment input, authoritative parish YAML, database
runtime state, and credential files.

## Input and precedence

The `deployment` mapping in a shared ParishKit YAML file accepts schema
version 1. The other recognized top-level sections are `calendars`, `common`,
`constant_contact`, `email`, `google`, `jobs`, `lock`, `logging`, `parishsoft`,
`print_member`, `print_ministries`, `rosters`, `runner`, `slack`, and `sync`.
Their contents belong to the other tools/shared helpers; this deployment parser
does not reinterpret or validate their individual fields. Unknown top-level
section names are rejected, including misspellings such as `deploymnet`.
When a tool introduces a new section, update the recognized list in the
[deployment loader](../../src/parishkit/stewardship/deployment.py); regression
tests check compatibility with every tool's example configuration.
An absent deployment document supplies only safe development defaults, not a
configured parish. Unknown deployment fields and unknown environment variables
in the `PARISHKIT_STEWARDSHIP_` namespace are rejected without echoing contents.

Explicit CLI overrides win over environment, then YAML, then built-in defaults.
YAML-relative paths use the YAML file's directory; environment/CLI-relative paths
use the working directory. Paths expand `~` using the existing shared convention.
No parse operation creates a directory or opens a referenced credential file.
Duplicate YAML keys are rejected, including nested/merge overrides. The separate
[configuration-authority contract](stewardship-authority.md) describes versioned
parish YAML and its required database materialization interface.

Use the [example configuration](../../scripts/pk-stewardship/example-config.yaml):

```sh
pk-stewardship --config scripts/pk-stewardship/example-config.yaml validate-deployment
```

Successful output includes `"startup_validated": false`. This is a metadata
diagnostic, not a statement that credentials, mounts, migrations, database/YAML
agreement, or service availability were checked. Production remains disabled in
the scaffold until those checks are implemented and connected.

## Strict YAML loading

Deployment validation, `config-check`, and authority-file reads opt into the
shared strict YAML loader. It limits each input to 8,000,000 UTF-8 bytes, 100,000
parsed node occurrences (including alias references), and 64 nesting levels
(the root is level 1; mapping keys and scalar values also count as nodes).
The reader requests at most the byte ceiling plus one, so file growth after
a filesystem size check cannot cause an unbounded read.

Before constructing values or flattening merged mappings, the loader also
checks the alias-expanded graph against the same node/depth ceilings. Shared
references are counted each time they occur; cycles and excessive expansion
are rejected. Ordinary aliases and unambiguous merges within these limits
remain supported. Limit violations and unexpected recursion failures raise
sanitized `ConfigError` responses; no recursion traceback or input content is
printed by stewardship's diagnostic commands.

The [authority layer](stewardship-authority.md#version-envelope) retains its
additional schema, canonical-size, and structural checks. Legacy non-strict
ParishKit YAML loading keeps its existing behavior and does not inherit these
new limits.

## Schema version 1

All environment suffixes below use `PARISHKIT_STEWARDSHIP_`, except the shared
`PARISHKIT_ROOT` override. The loader's explicit overrides use the same names;
the CLI exposes the profile, role, origin, and root options initially.

| YAML key within deployment | Environment suffix | Default / validation |
| --- | --- | --- |
| `schema_version` | None | Integer `1`; bool and unknown versions rejected |
| `profile` | `PROFILE` | `development`, `test`, or `production`; default development |
| `service_role` | `SERVICE_ROLE` | Default `web`; process identities below |
| `public_origin` | `PUBLIC_ORIGIN` | Local default `http://localhost:8000`; production requires explicit HTTPS origin |
| `trusted_proxy_hops` | `TRUSTED_PROXY_HOPS` | Exactly zero locally and one in production |
| `postgres.host` | `POSTGRES_HOST` | `postgres`; IP or DNS hostname, never a connection URL |
| `postgres.port` | `POSTGRES_PORT` | `5,432`; integer 1–65,535 |
| `postgres.name` | `POSTGRES_NAME` | `stewardship`; nonempty string |
| `postgres.user` | `POSTGRES_USER` | `stewardship`; nonempty string |
| `postgres.password_file` | `POSTGRES_PASSWORD_FILE` | No fallback credential; optional reference during syntax checking |
| `postgres.connect_timeout` | `POSTGRES_CONNECT_TIMEOUT` | Five seconds; integer 1–60 |
| `valkey.host` | `VALKEY_HOST` | `valkey`; IP or DNS hostname |
| `valkey.port` | `VALKEY_PORT` | `6,379`; integer 1–65,535 |
| `valkey.database` | `VALKEY_DATABASE` | Zero; integer 0–15 |
| `valkey.password_file` | `VALKEY_PASSWORD_FILE` | No fallback credential |
| `valkey.password_files.<identity>` | `VALKEY_PASSWORD_FILE_<IDENTITY>` | Individual broker/limiter file override; hyphens become underscores in environment names |
| `credential_target` | `CREDENTIAL_TARGET` | Required only for `credential-installer` |

Public origins cannot include user information, paths other than `/`, queries,
fragments, whitespace, or invalid ports/hosts. Local profiles use loopback HTTP;
production uses HTTPS. Deployment profile does not set Testing/Production
campaign mode; that remains database-authoritative.

Valkey file-map identities are `web`, `worker`, `scheduler`, `mail-dispatch`
and `backup-worker`. The scalar reference belongs only to the input profile's
identity; disagreeing scalar and map references are rejected. Fresh runtime
provisioning defaults these individual paths below `credentials/valkey/`,
creates only the implemented web/worker/scheduler credentials, and puts only
password hashes in the server-only ACL. Each consumer receives only its own
file mount. Independent credentials cannot alias SQL files, writable application
storage, credential-installer targets, each other or the ACL itself. Declaring a
future identity does not provision it or enable its runtime.

Service identities are `web`, `worker`, `scheduler`, `config-installer`,
`credential-installer`, `backup-worker`, `mail-dispatch`, `token-key-rotation`,
`bootstrap`, and `migration`. Parsing a role is not permission to invoke it or
access its secrets. Actual mount/queue/admission checks remain separate tasks.

### Authentication thresholds

`deployment.authentication_limits` accepts the closed count fields in
[`AuthenticationLimits`](../../src/parishkit/stewardship/authentication_policy.py).
Each has an `AUTH_LIMIT_<UPPERCASE_FIELD>` environment/explicit-override suffix;
normal CLI/environment/YAML precedence applies. Values must be integers from
1 through 1,000. Unknown names, booleans and out-of-range values fail validation.
The source dataclass owns the default counts; windows are fixed by the
[authentication policy](../specs/stewardship/architecture/spec.md#identity-and-session-security).
Production parsing warns when a threshold is weaker than its default, naming
only the affected fields. Runtime assembly must pass these validated limits to
the shared limiter; syntactic parsing alone does not enable authentication.

### Paths

`paths.root` defaults through the shared runtime helper to `/opt/parishkit`;
`PARISHKIT_ROOT` or `--runtime-root` overrides it. Each child has a YAML
`paths.<name>` override and a `PATH_<UPPERCASE_NAME>` environment suffix:

- `config`, `credentials`, `cache`, `logs`, `reports`, and `run` default to
  same-named directories immediately under the root.
- `authority` defaults to `config/stewardship` under the resolved config path.
- `persistent_root` defaults to `persistent` under the resolved run path.
- `postgresql`, `valkey`, `caddy`, and `media` each default to a same-named
  directory under the resolved persistent root.

Overrides propagate into derived defaults: moving `paths.run`, for example,
moves persistent stores unless those stores were separately overridden.
Temporary cleanup must never recurse into the persistent root. File ownership,
modes, symlink safety, and Compose mounts are verified by later startup/operations
integration, not inferred from a syntactically valid path.

### Secret references

`secrets.<name>` contains a file path, never credential contents. Each accepts
`SECRET_<UPPERCASE_NAME>` as an environment suffix. Recognized names are
`django_signing`, `general_encryption`, `family_code_mac`, `token_public`,
`token_private`, `google_oauth`, `google_workspace`, `parishsoft`, `slack`,
`backup_target`, `backup_data`, `metrics`, and `handoff_private`.
All except `handoff_private` are valid credential-installer target names.
Do not create a broad shared secret mount from this list: every service receives
only individually authorized files under the operations mount policy.
