# pk-cron-runner

`pk-cron-runner` is a cron-friendly scheduled job runner. It provides lockfile
protection, stale-lock handling, child-process timeouts, captured output, common
logging, and optional Slack reporting.

Run a configured job list:

```sh
pk-cron-runner --config /opt/parishkit/config/runner.yaml
```

If `PARISHKIT_ROOT` is set, the default runner config and lock paths move from
`/opt/parishkit` to that directory. Explicit `--config`, `--lock-file`, and YAML
paths are used as provided.

Run selected configured jobs:

```sh
pk-cron-runner --config /opt/parishkit/config/runner.yaml pk-sync-ps-to-ggroup
```

Run one command without a runner config:

```sh
pk-cron-runner --lock-file /opt/parishkit/run/manual.lock --command echo ok
```

Disabled jobs are skipped by default, even when named explicitly. Use
`--include-disabled` only for deliberate manual testing.

Commands are argument lists by default. Shell interpolation is intentionally not
enabled in this phase; wrap shell behavior in an explicit script when needed.

## Manual Smoke Tests

Use a temporary directory so these checks do not touch production locks or logs:

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

Cron entries should call the wrapper or console script with explicit config and
logs:

```cron
*/15 * * * * /opt/parishkit/bin/pk-cron-runner --config /opt/parishkit/config/runner.yaml
```

Slack notification smoke tests require human-provided credentials at runtime.
Do not commit the token file.

```sh
printf '%s' "$SLACK_BOT_TOKEN" > "$tmpdir/slack-token.txt"
pk-cron-runner \
  --config "$config" \
  --slack-token-file "$tmpdir/slack-token.txt" \
  --slack-channel "$SLACK_CHANNEL" \
  --slack-log-level CRITICAL
```
