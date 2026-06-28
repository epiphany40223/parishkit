# pk-query-ps-memfam

Look up and print ParishSoft family and member records.

This wrapper delegates to the installed `pk-query-ps-memfam` command.

## Usage

```sh
pk-query-ps-memfam --config example-config.yaml --member-duid 12345
pk-query-ps-memfam --config example-config.yaml --family-duid 67890
pk-query-ps-memfam --config example-config.yaml --name "Jane Smith"
pk-query-ps-memfam --config example-config.yaml --member-duid 12345 \
  --load-contributions 2026-01-01
pk-query-ps-memfam --config example-config.yaml --member-duid 12345 \
  --no-load-contributions
```

`--load-contributions` without a date loads the default contribution window.
`--no-load-contributions` disables contribution loading even when the config
enables it. Output is a bounded summary rather than a raw ParishSoft record
dump.

Use runtime credential paths in local config. Do not store API keys in this
directory.
