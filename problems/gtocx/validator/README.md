# Independent GTOCX judge

The implementation is in `src/gtoc_bench/gtocx/`. From the repository root:

```sh
uv run python -m gtoc_bench.gtocx solution.txt
uv run python -m gtoc_bench.gtocx solution.txt --json
uv run gtoc validate --problem gtocx --local solution.txt
```

The protected benchmark entry point reads exact bytes on stdin and emits JSON:
`python -m gtoc_bench.validator gtocx`. No score is awarded before the whole tree
has passed validation. `B=1` always; reference leaderboards and participant files
are host-only and not part of the agent environment.
