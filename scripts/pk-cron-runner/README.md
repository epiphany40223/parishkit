# pk-cron-runner

`pk-cron-runner` is the recommended way to run ParishKit tools on a schedule. You
point `cron` at the runner, and the runner runs your configured jobs with
safety features that bare cron does not give you:

- **Lock files**, so two runs cannot overlap and step on each other.
- **Stale-lock recovery**, so a crashed run does not block future runs forever.
- **Per-job timeouts**, so a hung job is cleaned up instead of running forever.
- **Captured logs** in JSON Lines format.
- **Optional Slack summaries** on failure (or on success, if you want them).

## Configure it

Copy the example config and edit it. The `jobs` list defines what runs and in
what order; `lock`, `logging`, `slack`, and `runner` control the safety and
notification behavior. The comments in `example-config.yaml` explain every
field.

```sh
cp example-config.yaml /opt/parishkit/config/runner.yaml
```

Each job's `command` is an argument list (no shell expansion). Use absolute paths
so scheduled runs do not depend on the environment. A typical job invokes one of
the ParishKit commands with its own config:

```yaml
jobs:
  - name: pk-sync-ps-to-ggroup
    command:
      - /opt/parishkit/bin/pk-sync-ps-to-ggroup
      - --config
      - /opt/parishkit/config/pk-sync-ps-to-ggroup.yaml
```

## Run it

Run all configured jobs:

```sh
pk-cron-runner --config /opt/parishkit/config/runner.yaml
```

Run only selected jobs by name:

```sh
pk-cron-runner --config /opt/parishkit/config/runner.yaml pk-sync-ps-to-ggroup
```

Run a single command without a runner config (handy for testing):

```sh
pk-cron-runner --lock-file /opt/parishkit/run/manual.lock --command echo ok
```

Disabled jobs (`enabled: false`) are skipped even when named explicitly; use
`--include-disabled` only for deliberate manual testing. If `PARISHKIT_ROOT` is
set, the default runner config and lock paths move under it; explicit `--config`,
`--lock-file`, and YAML paths are always used as written.

## Schedule it with cron

Add a cron entry that calls the runner on the interval you want. Use absolute
paths, since cron runs with a minimal environment:

```cron
*/15 * * * * /opt/parishkit/bin/pk-cron-runner --config /opt/parishkit/config/runner.yaml
```

## Manual smoke tests

These checks exercise the runner's behavior without touching production locks or
logs. Use a temporary directory:

```sh
tmpdir="$(mktemp -d)"
config="$tmpdir/runner.yaml"
```

Basic success and cron-style logging:

```sh
cat > "$config" <<EOF
lock:
  path: $tmpdir/runner.lock
logging:
  log_file: $tmpdir/runner.log
jobs:
  - name: ok
    command: [python3, -c, "print('ok')"]
EOF
pk-cron-runner --config "$config"
test "$?" -eq 0
test -s "$tmpdir/runner.log"
```

The runner log file is JSON Lines. Terminal output and Slack notifications stay
human-readable text.

Lock contention should return exit code 3:

```sh
cat > "$tmpdir/runner.lock" <<EOF
{"start_time":"2099-01-01T00:00:00+00:00","token":"manual-active"}
EOF
pk-cron-runner --config "$config"
test "$?" -eq 3
rm -f "$tmpdir/runner.lock"
```

Stale-lock recovery should remove the stale lock and continue:

```sh
cat > "$tmpdir/runner.lock" <<EOF
{"start_time":"2000-01-01T00:00:00+00:00","token":"manual-stale"}
EOF
pk-cron-runner \
  --config "$config" \
  --stale-after 1s \
  --stale-action remove-and-continue
test "$?" -eq 0
```

Fail-closed stale handling should return exit code 3:

```sh
cat > "$tmpdir/runner.lock" <<EOF
{"start_time":"2000-01-01T00:00:00+00:00","token":"manual-stale"}
EOF
pk-cron-runner \
  --config "$config" \
  --stale-after 1s \
  --stale-action fail-closed
test "$?" -eq 3
rm -f "$tmpdir/runner.lock"
```

Timeout child cleanup should return exit code 4:

```sh
cat > "$config" <<EOF
lock:
  path: $tmpdir/runner.lock
jobs:
  - name: slow
    command: [python3, -c, "import time; time.sleep(30)"]
    timeout: 1s
EOF
pk-cron-runner --config "$config"
test "$?" -eq 4
```

Slack notification smoke tests require human-provided credentials at runtime. Do
not commit the token file:

```sh
printf '%s' "$SLACK_BOT_TOKEN" > "$tmpdir/slack-token.txt"
pk-cron-runner \
  --config "$config" \
  --slack-token-file "$tmpdir/slack-token.txt" \
  --slack-channel "$SLACK_CHANNEL" \
  --slack-log-level CRITICAL
```
