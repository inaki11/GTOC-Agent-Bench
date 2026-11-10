# Independent GTOCX validation policy, revision v1

The implementation follows the original problem/format PDFs and the organizer's
public clarifications. It is independent software and does not claim identical
decisions to the proprietary competition host. The complete tree is validated
before any score is awarded. Non-scored/unused branches cannot bypass physical
checks or inflate Delta-V capacity. Invalid, uncertain and failed checks all
receive `score: null`.

## Physical checks

* The full catalogue, original constants and galactic central force are used.
  Stars follow the published analytic circular ephemerides.
* Counts and impulse limits apply per vehicle, with the published tolerances.
  Each listed mother must contribute actual pods and have an explicit departure
  impulse. Unproductive mother records are rejected rather than credited as fleet
  capacity. A physically zero departure can be written as a zero vector.
* Every target is distinct and is not Sol. The entire graph must be connected
  to the initial fleet. At most three children can leave each settled star, and
  all departures respect the colony wait. Cycles, duplicates and orphans fail.
* Mother impulses and nonzero pod impulses share a 1 Myr separation constraint,
  checked both before and after releases. Zero-impulse pods follow the organizer's
  exception. At a simultaneous mother burn they can detach from the matching
  pre-burn or post-burn state; an actual capture must still be verified.
* Mother propagation ends at the last pod release, not at 90 Myr. Extra later
  impulses fail. Settler/Fast trajectories end at their actual arrivals.
* Each trajectory is propagated continuously across its impulses; there are no
  submitted intermediate states to reset to or trust. Independent DOP853 and
  Radau chains retain their own propagated states throughout each vessel's flight.
* Continuous minimum and maximum radius are checked inside every segment as
  well as at endpoints. Both dense numerical interpolants are converted to their
  polynomial representations, and the real extrema of squared radius are examined.
  Flown arcs must remain in the stated range including its 0.01 kpc tolerance.

## Arrival impulses and score

The published arrival tolerance is explicitly an error in **magnitude** of the
arrival impulse, 0.01 km/s. Several official final files use the opposite sign
for the arrival vector. The judge derives the physical rendezvous impulse as
`v_star - v_incoming` and checks its magnitude against the submitted magnitude.
Departure and intermediate impulses always use all submitted vector components.
There is no free incoming velocity choice: it comes from propagation. The actual
required arrival impulse must also satisfy the single/total Delta-V budgets.

The supplied arrival vector's direction therefore does not control any subsequent
flight. A settled star follows its own ephemeris; future children start from it.
The metrics distinguish arrival **magnitude error** from the informational vector
difference, which is not the host's published acceptance test.

Scoring uses the verified distinct settlements, the catalogue-supplied theta_f,
the exact finite-domain kernels and endpoint corrections, and the declared
impulse magnitudes. Maximum available Delta-V counts only productive vessels.
The calendar factor is always **B=1**. No submission date changes benchmark
scores. Raw scores are compared; N and Delta-V are retained as metrics without
the original five-significant-figure tie-breaking procedure.

## Numerical conservatism and format compatibility

DOP853 and Radau are compared at extrema and interior/end points. Their discrepancy
consumes tolerance instead of increasing it. Non-finite arithmetic, ambiguous
extrema, integration failures, excessive disagreement and numerical timeouts
prevent acceptance. This is engineering verification with independent methods;
it is not an interval-arithmetic certificate or a measured false-acceptance rate.

Pure serialization differences observed in the organizer's public final files
are accepted and recorded in `format_notes`: two Fast Ship rows may repeat the
-11 label, and child rows/groups may be out of canonical order. The total fleet,
branch counts, exact dependencies, all epochs and physics are still checked
globally. Duplicate targets and extra ships remain forbidden. A single trailing
comma is supported; internal missing cells, non-integer identifiers, NaN and
infinity are rejected. The first line is always the discarded header; blank data
rows remain errors.

The 100 MB, 100,100-row and 600-second limits are benchmark resource limits.
Use pinned NumPy 2.2.6 and SciPy 1.16.2. Each result records candidate and judge
hashes and revision. Material/code hashes are verified by the controller and
protected judge. Public submissions and leaderboards are host-only references
and are excluded from Docker entirely.
