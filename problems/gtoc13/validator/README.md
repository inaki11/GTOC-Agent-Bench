# Validator entry points

Protected benchmark invocation: `python -m gtoc_bench.validator gtoc13`, with
the exact candidate bytes on stdin and one JSON verdict on stdout.

Standalone human-readable invocation from the repository:

```sh
uv run python -m gtoc_bench.gtoc13 solution.txt
# Equivalent executable wrapper:
uv run problems/gtoc13/validator/GTOC13_Verify solution.txt
```

Add `--json` for the full report. `-` reads stdin. This entry point uses the same
physical checks. Use `gtoc validate --problem gtoc13 --local solution.txt` to
also verify the pinned material hashes. These commands are offline checks and
do not register a benchmark submission. In a run, use `gtoc evaluate`.

Source: `src/gtoc_bench/gtoc13/`. Policy: `../validation-policy.md`.
