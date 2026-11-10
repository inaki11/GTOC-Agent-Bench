# GTOCX — Settlers of the Galaxy

Solve `/problem/statement.md`. The original complete PDFs are in
`/problem/documents/`, and the full catalogue of 100,001 stars including Sol is
`/problem/data/stars.txt`. Read `/problem/submission-format.md`,
`/problem/clarifications.md` and `/problem/validation-policy.md` before submitting.

Maximize the published merit formula with the calendar bonus **B fixed to 1**.
All galactic dynamics, distribution terms, fleet constraints and Delta-V ratios
remain active. Only fully verified trees receive a score, in points. The benchmark
compares raw numeric scores and records the number of settlements and Delta-V
as metrics. It does not reproduce historical leaderboard tie-breaking.

Work in `/workspace`. Use `/opt/conda/bin/python` for the scientific environment.
NumPy, SciPy, PyKEP, PyTorch, CasADi, cyipopt, NLopt, Numba and native compilers
are available. `gtoc tools` lists installed versions and paths; `gtoc packages`
lists approved packages and documentation. Permitted additional packages can be
installed with `/opt/conda/bin/python -m pip install --user PACKAGE`.

Submit and preserve a candidate with:

```sh
gtoc evaluate /workspace/solution.txt --json
```

The first physical line of the file is always a discarded header, even if blank.
Remaining rows must follow the numeric competition format. No NaN, infinity or
blank data rows are allowed. A final newline is accepted. The independent judge
handles the harmless numbering and row-order variants present in historical
files, while checking the complete graph and physical constraints.

The judge propagates every ship and every mother-ship leg between releases and
maneuvers, using independent numerical schemes and continuous range checks.
The original arrival tolerance compares impulse magnitudes: the physical arrival
direction is reconstructed from the incoming state and the target stellar state.
All departure and intermediate impulse directions are applied exactly as given.
Use the catalogue's theta_f values for scoring, not values recomputed from the
rounded orbital elements. The velocity conversion and galactic force are not
the ordinary Newtonian two-body model.

Only one evaluation may be pending. Limits are 100,000,000 bytes, 100,100 data
rows and 600 seconds for numerical checking; the complete request permits up
to 660 seconds including overhead. Set a tool timeout of at least 660 seconds.
Large settlement trees may take substantially longer than small files. Validation
counts against the run's time budget, so evaluate improvements throughout the run
and leave time to receive feedback.

JSON results include `status`, `score`, `diagnostics` and, for rule failures,
`errors` with a code, source line and explanation. `invalid` means a violated
rule; `unverified` means uncertainty/resource limits prevented verification.
Both have `score: null`. An internal checker failure is `validator_error`.
Valid results include the score breakdown, fleet counts and numerical residuals.
Exit code 0 means valid, 2 means not accepted, and 1 means an operational error.
An overlapping request returns `evaluation_busy`; wait for it to finish before
trying again. No automatic retries are made.

Use `gtoc status --json` for remaining time and tokens. The budget includes
inference, computation, compilation, validation and provider waits. Keep improving
and validating candidates until the controller stops the run or the configured
token cutoff is reached. A final response ends the run and does not register a
solution file. Preserve candidates during the run; only candidates admitted before
the cutoff count. Every admitted file and verdict is retained.

Internet access is limited to approved package installation and documentation
through `gtoc docs URL`. Do not search for, download or reuse other participants'
GTOC code or solutions. Do not consult other runs or the host's reference files,
move optimization to another machine, alter the protected judge or bypass these
restrictions. Choose your own optimization method.
