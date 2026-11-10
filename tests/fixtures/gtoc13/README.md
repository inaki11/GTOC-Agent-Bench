# Reference trajectories (not supplied to agents)

* `planetx-conic.txt`: `initial_solution.txt` supplied by the user in
  `/Users/inaki/Downloads/GTOC 13/`. A simple ballistic transfer ending with a
  singleton science encounter at PlanetX. It is a numerical positive test, not
  an assertion that this exact file was accepted by the historical host.
* `kaist-high-score.txt`: public participant output from
  [GunheeYi/gtoc13](https://github.com/GunheeYi/gtoc13/blob/main/solutions-important/high-score.txt),
  fetched on 2026-10-31. Keep the file's declared encounters as a scoring reference,
  and test full trajectory validity separately. A matching score alone is not
  sufficient for physical acceptance.

* `antipodes-submitted.csv`: byte-for-byte copy of the user-supplied
  `GTOC 13 jack/output/antipodes_submitted.csv`. SHA-256:
  `6297881d46e0e1ea238c1cd9a9f612b15ab2e4cb27c880383c41d429a6ad89b8`.
  The accompanying `submission_history.csv` ends with a passing record on
  2025-11-17 12:28 UTC: 133 scientific flybys, Vx 41.582 km/s, TOF 191.177 years,
  score 337.878. The file has matching rounded trajectory metrics and the same
  name as the submitted solution, but the log does not record a file hash.
  This is the expected-positive full sail reference for v2, not an independent
  byte-level certification by JPL. It was rejected by v1's fixed interpolation.

The Antipodes reference is used unchanged: 23,931 positive sail intervals,
252 flybys, 133 scientific. Its c=1 score is 329.3314957879535. The `slow` test
checks the entire file. A second test corrupts its initial velocity and requires
rejection, so acceptance is not based on a filename or a hash allowlist.

These fixtures, including the 5 MB Antipodes file, remain outside the Docker
build context and the agent's problem materials. The supplied research files
retain their original authorship; this benchmark does not claim a new license
for participant artifacts.
