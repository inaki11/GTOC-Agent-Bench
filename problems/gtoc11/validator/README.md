# Official GTOC11 checker

`GTOC11Check_Linux` is the unmodified ELF x86-64 executable received from the
organizers. It expects `Solution.txt` and `Candidate Asteroids.txt` in its
working directory, writes `Result.txt`, and also prints `Extra Info`, `Score`
and `Status`. The adapter in `gtoc_bench.gtoc11` handles this contract and
preserves original rejection messages.

The organizer's invocation instructions are retained in host-side provenance.
The isolated benchmark runs this program as the judge user. Do not invoke it
with the agent user's files in place of the protected evaluator.
