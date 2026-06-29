# pk-query-ps-memfam

Look up and print a ParishSoft family or member record from the command line.
This is a read-only spot-check tool — handy for confirming what ParishSoft holds
for a person before or after a sync. It never changes any data.

## What you need first

- A working ParishSoft API key, referenced from your config.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-query-ps-memfam.yaml
```

The config mainly supplies your ParishSoft credentials and cache settings. Keep
real credential paths in your config under `/opt/parishkit/config/`; do not store
API keys in this directory.

## Run it

Look someone up by member ID, family ID, or name:

```sh
pk-query-ps-memfam --config /opt/parishkit/config/pk-query-ps-memfam.yaml --member-duid 12345
pk-query-ps-memfam --config /opt/parishkit/config/pk-query-ps-memfam.yaml --family-duid 67890
pk-query-ps-memfam --config /opt/parishkit/config/pk-query-ps-memfam.yaml --name "Jane Smith"
```

Optionally include contribution history:

```sh
pk-query-ps-memfam --config /opt/parishkit/config/pk-query-ps-memfam.yaml --member-duid 12345 \
  --load-contributions 2026-01-01
pk-query-ps-memfam --config /opt/parishkit/config/pk-query-ps-memfam.yaml --member-duid 12345 \
  --no-load-contributions
```

`--load-contributions` without a date loads the default contribution window;
`--no-load-contributions` disables contribution loading even if your config
enables it. The output is a bounded, readable summary, not a raw ParishSoft
record dump.
