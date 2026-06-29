# pk-print-ps-ministries

Print the ministry names defined in ParishSoft, in sorted order. This read-only
helper is useful when configuring the sync tools, because their mappings must use
your ParishSoft ministry names exactly. It never changes any data.

## What you need first

- A working ParishSoft API key, referenced from your config.

## Configure it

Copy the example config and edit it with your parish's values:

```sh
cp example-config.yaml /opt/parishkit/config/pk-print-ps-ministries.yaml
```

By default the tool prints every ministry name. To narrow the list, set
`print_ministries.include_patterns`, `include_names`, or `exclude_patterns` in
your config; the comments in `example-config.yaml` explain each. Keep real
credential paths in your config; do not store API keys in this directory.

## Run it

```sh
pk-print-ps-ministries --config /opt/parishkit/config/pk-print-ps-ministries.yaml
```
