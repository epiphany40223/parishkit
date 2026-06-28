# pk-print-ps-ministries

Print ParishSoft ministry names in sorted order.

This wrapper delegates to the installed `pk-print-ps-ministries` command.
It reads ParishSoft ministry types through shared cache and logging options,
then prints unique ministry names in sorted order. By default it prints all
ministry names. Set `print_ministries.include_patterns`, `include_names`, or
`exclude_patterns` in YAML to narrow output for local operational conventions.

## Usage

```sh
pk-print-ps-ministries --config example-config.yaml
```

Use runtime credential paths in local config. Do not store API keys in this
directory.
