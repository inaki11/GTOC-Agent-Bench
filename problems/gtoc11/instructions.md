# GTOC11: Dyson Sphere Building

Design trajectories and an asteroid delivery schedule that maximize the score in
`statement.md`. Read `submission-format.md` and `validation-policy.md` before
constructing your solution. The complete catalogue of 83,453 candidate asteroids
is in `/problem/data/Candidate Asteroids.txt`.

The evaluator runs the **original Linux checker supplied by the organizers**.
The benchmark sets the submission-time bonus to **B=1**, retaining every other
term of the GTOC11 merit function. Scores are dimensionless points, maximized.
The active mass-loss coefficient is **alpha = 6e-9 s^-1**; an early version of
the competition statement used a superseded value of 1e-9 s^-1.

Write a complete ASCII solution under `/workspace`, then submit it using:

```sh
gtoc evaluate /workspace/solution.txt
gtoc evaluate /workspace/solution.txt --json
gtoc status
gtoc tools
```

The submitted filename is arbitrary. The protected evaluator stages an immutable
copy as `Solution.txt` beside the catalogue, as required by the official program.
It creates a fresh directory for every attempt. You do not need to rename files
or execute the check program yourself. A rejected solution receives no score;
the response contains the original rejection reason and line when provided by
the checker. A physically valid solution can have score zero when at least one
station has no delivered mass. A crash, timeout or ambiguous result never earns
points. Keep improving and evaluating solutions until your run budget expires.

The environment supplies Python, NumPy, SciPy, PyKEP, compiled-language tools
and numerical optimization libraries; use `gtoc tools` for the actual inventory.
Use only the problem materials and your own work. External solution archives,
participant code, reference solutions and leaderboards are not agent inputs.
The official checker is isolated from your workspace and cannot be modified.
The maximum submission is 100,000,000 bytes and 1,000,000 physical rows. One
evaluation can run at a time, with a 600-second native-checker timeout (660
seconds allowed by the client). Rejections and infrastructure failures are
reported separately. Keep useful source code and the latest complete candidate
in `/workspace` so the recorded run can be inspected and continued.
