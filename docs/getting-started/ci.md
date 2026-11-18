# Use it in CI

`testudo replay` returns the verdict at the end of the bag as its exit code:

| Exit code | Verdict |
|---|---|
| `0` | OK |
| `1` | WARN |
| `2` | ERROR or STALE, or the bag could not be read |

To fail a pipeline on any problem:

```bash
testudo replay -c config.yaml run_bag/
```

To allow warnings and fail only on errors:

```bash
testudo replay -c config.yaml run_bag/ || [ $? -eq 1 ]
```

The exit code reflects the **end** of the bag. A fault that cleared before the end is listed in the report, but doesn't fail the run.

Tune known conditions in the config you pass with `-c`, so a clean run ends OK.
