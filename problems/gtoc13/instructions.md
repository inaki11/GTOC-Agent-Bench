# GTOC13: exploration of the Altaira system

Design a trajectory that maximizes the scientific return in `/problem/statement.md`.
Read `/problem/submission-format.md` and `/problem/validation-policy.md` before
generating a submission. The complete original competition PDFs, including their
figures, are in `/problem/documents/`. The three original ephemeris catalogues
are in `/problem/data/`: `gtoc13_planets.csv`, `gtoc13_asteroids.csv` and
`gtoc13_comets.csv`. The planet file uses Windows-1252 for one planet name.
Use the constants in the statement, including its precise astronomical unit.

The objective is `J = b * sum(w*S*F)`, in points. The historical submission-time
bonus is fixed to `c = 1` for every candidate, so scores are comparable across
runs. The grand tour bonus and seasonal and velocity penalties still apply.
Only verified trajectories receive a score. The benchmark compares the full
numeric score; the historical three-decimal tie-break rules are not applied.
Science flyby count and initial Vx are recorded as additional metrics.

This exercise uses an independent validator with reconstruction of the sail's
interior attitude. For each positive sail interval, the judge starts with a
normalized linear interpolation of the submitted endpoint normals. When needed,
it fits a normalized quadratic curve whose endpoint normals remain unchanged.
The fitted curve is then fixed and checked with a single RK4 step and independent
DOP853 and Radau propagation. The published 1e-4 relative tolerance applies
separately to position and velocity for each output interval, starting from its
submitted initial state. The sail's interior cone angle, distance to Altaira and
normal normalization are also checked continuously. All other physical rules
still apply. If a verifiable control cannot be reconstructed, no score is awarded.

Duplicate rows at control changes are supported. Numerical defects of accepted
sail intervals and reconstruction statistics are reported. There is no additional
100 m / 0.1 mm/s closure requirement for an entire sail arc; those are the stated
conic tolerances. Read the validation policy for the precise numerical contract
and the remaining differences from the proprietary JPL competition checker.

Work in `/workspace`. You may write code and use the assigned CPUs and GPUs.
Use `/opt/conda/bin/python` for the scientific Python environment. NumPy, SciPy,
PyKEP, PyTorch, CasADi, cyipopt, NLopt, Numba and native compilers are available.
Run `gtoc tools` for versions and paths and `gtoc packages` for the approved
package and documentation catalogue. Install permitted packages with
`/opt/conda/bin/python -m pip install --user PACKAGE`; installation time counts.

Evaluate and preserve a candidate with:

```sh
gtoc evaluate /workspace/solution.txt --json
```

The file must contain exactly 12 finite numeric fields per data row, in the
competition format. Comments and blank lines are allowed; a final newline is
allowed. Limits are 100,000,000 bytes and 500,000 data rows. Only one evaluation
may be pending. The judge has a 600-second limit and the request allows up to
660 seconds including overhead; use a tool timeout of at least 660 seconds.
Validation consumes the run's time budget. Submit candidates throughout the run
and leave enough time to receive the verdict, especially for long sail trajectories.

The JSON verdict contains `status`, `score`, `diagnostics` and, for rule rejections,
`errors` with a stable code, source line and explanation. `invalid` means a rule
of this exercise was violated; `unverified` means numerical uncertainty or a
resource limit prevented acceptance. Neither receives a score. A checker failure
is `validator_error`. Valid results include the score breakdown and physical
residuals. Exit code 0 means valid; 2 means not accepted. Operational failures
use exit code 1. `evaluation_busy` is retryable after the pending check finishes.

Use `gtoc status --json` to inspect the remaining time and token budget. Time
includes inference, computation, compilation, validation and provider waits.
Keep improving and evaluating solutions until the controller stops the run or
the configured token cutoff is reached. Do not end the run after a baseline.
Save and evaluate throughout the run: a final response does not register a file.
Only candidates admitted before the cutoff count. Every admitted file and verdict
is retained, even when the score does not improve.

Internet access is limited to approved package installation and documentation
through the gateway. Use `gtoc docs URL` for approved documentation. Do not search
for, download or reuse participants' GTOC code, solvers or solutions. Do not move
optimization to external machines, consult other runs, modify the protected
judge or bypass these restrictions. Choose your own optimization method.
