# GTOC13 with reconstructed interior controls, revision v2

Revision: `gtoc13-reconstructed-controls-v2`. This independently implemented judge
uses the supplied JPL statement and submission-format PDFs. It is not the JPL
executable and does not claim identical decisions for every possible submission.
Unlike v1, it reconstructs interior sail controls and accepts the complete
user-supplied Antipodes submitted trajectory after physical checking. A matching
score alone is not used to accept a trajectory.

## Acceptance and score

All structural and physical checks precede scoring. A rejected or unverifiable
candidate has `score: null`; it cannot improve the benchmark result. All rows,
including non-scientific encounters and intermediate arcs, are checked.

The published equations for body weights, velocity factor, seasonal history and
grand tour bonus are used unchanged. Only scientific flybys enter seasonal
history. At most 13 scientific encounters of each body are permitted. The grand
tour requires all ten planets, Yandi, and at least 13 **distinct** asteroids/comets.
The historic calendar bonus is fixed to 1. Scores are stored without rounding;
rounding to one or two decimal places is suitable for display, not validation.

## Physical model

* All 310 bodies use the supplied Keplerian elements, reference epoch and units.
  Massive flybys use patched conics. Yandi, asteroids and comets are massless.
* Initial plane and transverse velocity, the full 200-year time window,
  encounter positions and declared v-infinity vectors are checked.
* Incoming/outgoing planetary v-infinity magnitudes must match and imply a
  flyby altitude of 0.1–100 body radii above the surface. Massless flybys cannot
  change velocity. The final singleton flyby allowed by the format is supported.
* Successive flybys of the same body require one third of its orbital period.
  Asteroid/comet science needs a previously verified stellar perihelion. Yandi
  is treated separately, following the statement's distinction of the dwarf planet.
* Perihelion constraints apply inside flown arcs, not just at reported points,
  and not to an unflown extension of an osculating orbit. At most one passage
  below 0.05 AU is allowed, never below 0.01 AU, with the stated 1 km tolerance.

## Numerical checks and explicit differences

Ballistic propagation retains the analytic universal-variable Kepler solution,
independent DOP853/Radau checks and conic endpoint tolerances. Consecutive ballistic
arcs retain the propagated state. Planetary and massless encounter rules are unchanged.

For each positive sail interval, let s be normalized time in [0,1], and n0/n1
the submitted endpoint normals. Start with normalized linear interpolation. If
its RK4 residual is not below one fifth of the published tolerance, minimize
position and velocity residuals over this family:

```text
n_raw(s) = (1-s)*n0 + s*n1 + 4*s*(1-s)*(a*p + b*q)
n(s)     = n_raw(s) / norm(n_raw(s))
```

The orthonormal tangent basis p,q is perpendicular to normalize(n0+n1), constructed
deterministically using its least-aligned Cartesian axis. The two parameters are
bounded to [-1,1]. The correction vanishes at both endpoints. Input rows, times,
states and endpoint attitudes are never edited. Antipodal normals require an
intermediate row. The optimizer uses `max_nfev=48` (SciPy's counter excludes
finite-difference Jacobian probes); every probe and final comparison also checks
the overall judge time budget. The one-fifth threshold is a reconstruction trigger,
not a relaxed acceptance limit.

Once chosen, the control is fixed. Exactly one RK4 step and independent DOP853
and Radau propagation must verify that control. A fit alone cannot award a score.
The published condition is applied separately to position and velocity:

```text
norm(X_predicted - X_f) / norm(X_f - X_0) < 1e-4
```

Each sail interval starts from its submitted X_0. Its verified numerical endpoint
X_f becomes the next node for subsequent arcs and encounter checks. This follows
the local numerical contract in the statement. The extra v1 requirement that an
entire sail arc close within the conic 100 m / 0.1 mm/s limits has been removed,
including the indirect application of those limits to sail defects at encounters.
The reported flyby position still has to match the body within 100 m, and all
v-infinity and gravity-assist conditions still apply.

This is a numerically feasible sequence under those tolerances, not an exact
single globally propagated orbit with a 100 m error bound. Absolute sail defects
can exceed 100 m because the tolerance scales with the state change. The complete
Antipodes reference has a maximum local position defect of about 41.63 km and
velocity defect of 0.181 m/s, both within the local 1e-4 limits. The verdict records
these defects and the maximum RK4/truth relative errors so this is inspectable.
A zero relative-error denominator is never replaced by a permissive floor.

For each adaptive DOP853 step, the checker constructs its dense position polynomial
and searches all real stationary points of squared radius and illumination. With
quadratic raw normals, illumination is degree at most nine. The control norm is
also checked at all stationary points before normalization. Cone and stellar
distance checks cover interior extrema and boundaries, not just a sampling grid.
The same points are compared with Radau. Numerical discrepancies consume the
tolerance rather than enlarging it. Ill-conditioned extrema, non-finite arithmetic,
integration failure, solver disagreement and timeouts prevent acceptance.

The host's exact interior-control optimization/collocation code remains unavailable.
This normalized-quadratic family is a verified reconstruction strategy, not a
claim to recover the original flown control or to fit every possible valid input.
If reconstruction or independent checking cannot establish feasibility, the verdict
is `unverified` with no score. Literal format/physical violations are `invalid`.
For conic endpoint residuals, acceptance still requires the residual plus its
numerical margin to fit within both original limits. A residual whose entire
uncertainty interval exceeds either limit is `invalid`; an interval overlapping
a limit is `unverified`. Neither case receives a score. This classification
correction does not relax acceptance or rewrite historical verdicts; code hashes
distinguish the corrected judge from the earlier v2 implementation.
This is engineering verification, not interval arithmetic or a mathematical
certificate of zero false acceptance.

Duplicate epochs must match exactly. Duplicate states allow only bounded floating
point roundoff: per coordinate, position allowance is
`min(1e-3 km, 1e-7 km + 16*ulp(max_abs_coordinate))` and velocity allowance is
`min(1e-10 km/s, 1e-12 km/s + 16*ulp(max_abs_component))`.
The statement says continuity to all reported digits; this small, bounded allowance
handles ordinary floating point serialization without admitting the 100 m
position jumps allowed for propagated endpoint residuals. Body IDs and flags must
be exact integers, rather than silently truncating fractional input. Sail normals
must have norm within 1e-10 of one. Positive sail steps must be at least 60 seconds,
following the submission-format document; the statement itself writes `>60 s`.

The limits of 500,000 data rows and 600 seconds are benchmark resource limits.
The original 100 Mb file limit is interpreted as 100,000,000 bytes. There is no
historical ten-submissions-per-day restriction in an isolated benchmark run.
The benchmark maximizes raw J with c=1; it records science count and initial Vx
but does not implement the original three-decimal leaderboard tie-break.

## Reproducibility and scope of evidence

Use the pinned NumPy 2.2.6 / SciPy 1.16.2 environment. Each verdict records the
candidate hash, judge revision and code hash. The controller verifies SHA-256
hashes of the exercise materials and judge sources before running the judge.
Tests include the full unchanged Antipodes file, KAIST, independently generated
curved-control trajectories, rule violations, interior perihelia and hostile inputs.
Participant trajectories and historical leaderboards stay outside the Docker build
context and the agent materials. No filename or hash allowlist grants acceptance.
See the repository's `docs/gtoc13-validation.md` for measured results and limits.
Passing these tests is not an empirical false-acceptance rate against the JPL host.

v1 evidence is archived in `docs/gtoc13-validation-v1.md` and
`docs/gtoc13-reference-checks-v1.json`. Judge and material hashes identify v2 as a
different experimental revision. Prior run verdicts are not rewritten; comparisons
across revisions must identify the revision or reevaluate the same candidate bytes.
