# GTOCX submission format

The complete original [format PDF](documents/gtocX_submission_format.pdf) is supplied.
Files contain ASCII numeric text with commas and/or whitespace as separators.
The **first physical line is always a discarded header**, and may be blank.
Data rows contain no comments or blank lines. A single final newline is allowed.
Times are Myr after Year Zero and impulse components are km/s. IDs and counts
are integers. Every floating point number must be finite.

Sections occur in this order: Mother Ships and their pods, Fast Ships, then
Settler Ships. Omit an unused initial-ship section; at least one initial ship
must be present.

## Mother Ships and Settlement Pods

Mother IDs are -1, -2, -3. A Mother Ship row is followed immediately by exactly
Nsp pod rows. Its own row contains `4+4*M` fields:

```text
mother_ID 0 Nsp M T1 ... TM dV1x dV1y dV1z ... dVMx dVMy dVMz
```

For this exercise, listed mothers must be productive: `1 <= Nsp <= 10`, and
`1 <= M <= 3`. A zero-valued departure impulse can be explicitly recorded.
Each pod has seven fields and a sequential pod number starting at 1:

```text
mother_ID pod_number target_star_ID release_time arrival_dVx arrival_dVy arrival_dVz
```

The pod starts with the mother's instantaneous state. Its arrival impulse is
applied immediately at release, where it settles its target. A mother itself
does not establish a colony. No mother maneuvers may follow its final pod release.

## Fast Ships

There are at most two Fast Ship rows, conventionally labelled -11 and -12.
Each row has exactly ten fields:

```text
fast_ID target_star_ID departure_time arrival_time dV1x dV1y dV1z dV2x dV2y dV2z
```

The departure state is Sol's state plus the first vector; arrival must be a
rendezvous at the named star, using the permitted final-impulse magnitude.

## Settler Ships

Each row has `3+4*M` fields, with `2 <= M <= 5`:

```text
source_star_ID target_star_ID M T1 ... TM dV1x dV1y dV1z ... dVMx dVMy dVMz
```

Both IDs are in 1–100000. The source must already have been settled by another
verified vessel at least 2 Myr before T1. At most three children may leave a
given source. Arrival establishes one distinct new colony; cycles and duplicate
settlements are forbidden.

The canonical competition serialization groups all children of a source on
consecutive rows and orders them by departure time. This independent judge also
accepts physically equivalent row permutations found in the official final
files. It verifies counts, complete graph connectivity and dates independently
of their textual ordering. It reports this as a format note, not as a physics
exception. Similarly, the public Citadel file repeats the -11 label; each row
still counts as a separate Fast Ship, with a strict limit of two. A single
trailing comma is accepted for compatibility with the official Aerospace file.

## Verification output

Run `gtoc evaluate /workspace/solution.txt --json` in a benchmark run. The verdict
contains a status, score and diagnostics. Rule rejections have an error code,
the source line and an explanation. Only `valid` receives a score. Numerical
uncertainty or exhausted resources return `unverified`, and checker failures
return `validator_error`; both have `score: null`.
