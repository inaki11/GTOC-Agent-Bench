# GTOC-Bench execution rules

Solve the problem described in `/problem/statement.md`. The asteroid catalogue is
`/problem/data/GTOC12_Asteroids_Data.txt`. The objective is the total mass returned
to Earth, in kg, with all activity coefficients fixed to 1. The physical and
fleet constraints in the statement still apply.

You may execute code and use the assigned CPUs and GPUs. Work in `/workspace`.
The initial prompt includes your actual GPU allocation (device names, VRAM and
CUDA support); `gtoc status --json` also exposes `gpu_allocation`. All assigned
GPUs are available for your optimization code. Use `/opt/conda/bin/python` for
PyTorch/CUDA; assigning a GPU does not move CPU computations onto it automatically.
PyTorch, PyKEP, NumPy and SciPy are preinstalled in the scientific Python
environment, together with CasADi, cyipopt (IPOPT's Python interface), NLopt,
Numba/llvmlite and pybind11. GCC/G++, CMake and pkg-config are available.
Run `gtoc tools` for installed versions, executable paths and IPOPT libraries.
IPOPT is in `/opt/conda/lib`, its headers in `/opt/conda/include/coin-or`, and
`PKG_CONFIG_PATH=/opt/conda/lib/pkgconfig` is set for compilation.
Run scientific scripts with `/opt/conda/bin/python`; plain `python`
may select a different interpreter in the agent's shell. Install additional
approved packages with `/opt/conda/bin/python -m pip install --user PACKAGE`;
installation time counts.
Run `gtoc packages` to see the permitted package and documentation catalog.
An installation error saying "No matching distribution" may mean that the
package is not approved; check this catalog before retrying.
Zig 0.17.0 is preinstalled as `zig`, `python-zig` and
`/opt/conda/bin/python -m ziglang`; it also compiles C/C++ via `zig cc`/`zig c++`.
Its language reference is available with
`gtoc docs https://ziglang.org/documentation/0.17.0/`.

Use `gtoc status --json` to inspect the remaining time and token budget. Time
includes inference, computation, compilation, validation and provider waits.
The token budget, when configured, is approximate. A budget cutoff terminates
all your processes; do not rely on a final response to save your work.

Keep working on improving and validating solutions until the time limit expires
and the controller stops the run, or until an earlier configured token cutoff.
Do not stop after obtaining a baseline or a valid solution, and do not give up
because further improvement seems difficult. Use the remaining budget to keep
trying to improve your best result. Do not ask for permission to continue.
Do not send a final response while budget remains: it ends the run and terminates
any remaining background work. Save and evaluate candidates throughout the run
so that your best validated result is preserved when the controller stops you.

Evaluate and save a candidate with:

```sh
gtoc evaluate /workspace/solution.txt --json
```

This copies and registers the file before running the deterministic validator.
As a planning estimate, allow a few seconds for small candidates and roughly
10-30 seconds for larger fleets on the exploratory emulated environment.
Actual time depends on solution size, hardware and CPU load; it is not a guarantee.
Complex candidates can take longer: the native validator has a 120-second limit,
and the complete evaluation request allows up to 180 seconds including overhead.
Use a shell/tool timeout of at least 180 seconds when waiting for an evaluation.
Validation consumes the run's time budget. Submit improvements throughout the run
and leave enough time for feedback; do not wait until the final seconds.
Every accepted candidate and verdict is preserved, including results that do
not improve your best score. Only candidates accepted before the cutoff count.
Keep submitting candidates during the run. Results computed privately by your
code are not recorded until you call this command.

Only one evaluation can be pending at a time. With `--json`, operational errors
are also JSON on stdout, with `status: "error"` and exit code 1. An overlapping
evaluation returns `code: "evaluation_busy"` and `retryable: true`; wait for the
pending evaluation to finish and check the remaining budget before retrying.
The CLI does not retry automatically. Exit code 0 means a valid candidate;
exit code 2 means an invalid candidate or a validator error.
A native timeout, supervisor timeout and controller transport timeout are
reported separately. Code snapshots are asynchronous observations after
validation, so copying the workspace does not precede or block the validator.

The inherited native validator treats a newline after the final event as an
extra empty line. End the solution at the final number, without a trailing
newline or blank line. The benchmark preserves and evaluates your exact bytes.

Internet access is limited to approved library documentation and package
installation. Use `gtoc docs URL` to read approved documentation. Do not search
for, download, or reuse other participants' GTOC code, solvers or solutions.
Do not move optimization to external machines, consult other runs, alter the
validator or try to bypass these restrictions. Direct Internet connections
are unavailable; the local gateway provides the permitted operations.

The controller does not supply an optimization strategy. Choose your own
methods and use your time and model calls accordingly.
