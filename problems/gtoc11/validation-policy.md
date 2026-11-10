# GTOC11 validation policy: official binary, B=1

Revision: **gtoc11-official-b1-v1**. The trusted executable is the unmodified
`GTOC11Check_Linux` supplied by the organizers in the user-provided package.
Its SHA-256 is `6beb6456ee3959034ef3510ea646edf1633d37cb1fd61e41687dee3d94b4eabb`.
It requires Linux x86-64, glibc and libstdc++. The Windows executable is retained
only in host-side references; the benchmark uses the Linux version consistently.

## Acceptance and score

The adapter first checks the entire ASCII envelope, counts, indices, finite
numbers, chronology, duplicate visits/transfers and basic structure. This
prevents malformed input from exploiting unchecked C++ extraction, accepting a
truncated prefix or hiding trailing records. A numeric magnitude limit of 1e30
is an execution-safety bound, far beyond legitimate problem values.

Every structurally acceptable candidate is copied into a fresh private directory
as `Solution.txt` and paired with the exact received `Candidate Asteroids.txt`.
The program is invoked without a shell or command-line input, with stdin closed.
This also satisfies its final interactive `getchar()` without user interaction.
The binary and catalogue are never rewritten.

Acceptance requires **all** of the following:

- Process exit code 0 and exactly one `Status:0` and one finite `Score:` marker.
- A complete `Result.txt`, agreeing with stdout on status, score, mass and count.
- Published mass and asteroid count matching the completely parsed candidate.
- The printed score matching an independent recomputation of the full merit
  function at B=1, allowing only the C++ stream's output rounding (8 significant
  digits in stdout and score; 6 significant digits for mass in `Result.txt`).
- No unexpected stderr output.

The awarded score is the official executable's printed score. The unrounded
independent score, twelve station masses, ten Delta-V costs and B=1 are returned
under `score_breakdown`. Earth-departure excess velocity is not charged; all ten
ship slots remain in the denominator, including unused ones.

`Status:-1` is a rejection even though native errors also print `Score:0`.
The adapter returns `status: invalid`, `score: null` and the original textual
cause, with its original line number. A legitimate accepted zero-score candidate
has `Status:0` and returns `status: valid`, `score: 0`.
Missing/duplicated success markers, inconsistent reports, crashes, signals and
timeouts return `validator_error` with no score, never an assumed acceptance.

## Official numerical behavior

All trajectory integration, flyby tests, ATD control interpolation, mass checks,
arrival geometry and construction scheduling use the received executable. No
replacement physics model, looser trajectory tolerance or alternative validator
is used. Both control types 0 and 1 are supported by that program.

The final statement's mass-flow coefficient is **6e-9 s^-1**. An early PDF
dated 10 October 2021 specifies 1e-9 and is superseded. The original final
statement is dated 16 October; the reference here uses its corrected constants.
The supplied example also repeats **arrival mass** in every control row, and
some of its steps exceed one day by less than 0.001 day. These are accepted by
the official program. The wrapper does not introduce conflicting checks on
these fields or treat printed nominal tolerances as an independent numerical
implementation. The program's internal numerical tolerances cannot be fully
audited without source code.

The historical time bonus is omitted. The standalone executable receives no
submission date and its score is checked against B=1 for every accepted file.
Host-side historical references do not participate in validation.

## Execution isolation

During a benchmark run, UID 1000 executes agent tools and UID 1001 executes the
checker; the checker directory is inaccessible to the agent. The image is
read-only, data are pinned with SHA-256 and each attempt receives a separate
temporary directory. Files use at most 100 MB of the existing 128 MiB private
judge tmpfs; the catalogue is a read-only symlink. The native checker is limited
to 600 CPU/wall seconds, 1 GiB address space, 1 MiB per output file, and no core
dump. Supervisor, transport and client timeouts are 615, 630 and 660 seconds.
The timeout is a resource limit, not evidence of an invalid trajectory.

This integration preserves the behavior of the provided official executable,
with input/output guards. It does not assert that every historical server build
was identical or that the closed-source executable has no numerical or logical
defects. Reference tests exercise its actual success and rejection paths.
