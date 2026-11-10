# GTOC11 materials and original checker

The user received four files directly from the GTOC11 organizers and provided
`/Users/inaki/Downloads/Check Program/` for this integration on 3 November 2026.
The original folder remains unchanged. The complete received set is retained,
without byte modifications, in these locations:

| Received file | Benchmark location | Role |
| --- | --- | --- |
| Candidate Asteroids.txt | data/Candidate Asteroids.txt | Agent problem data and trusted catalogue |
| GTOC11Check_Linux | validator/GTOC11Check_Linux | Official Linux x86-64 checker |
| GTOC11Check_Windows.exe | references/gtoc11/received/GTOC11Check_Windows.exe (host) | Original Windows build, not used for scoring |
| Solution.txt | references/gtoc11/received/Solution.txt (host) | Organizer-provided example, not an agent input |

File permissions on the **copied** Linux executable are changed to make it
executable; its bytes are unchanged. No source, patch, decompilation or rebuilt
substitute is used as the trajectory checker. `provenance.json` pins problem
materials and adapter source; the host reference manifest pins the other two
received files, reference sources and received invocation instructions.

The organizers are Ya-Zhong Luo and Hong-Xin Shen (NUDT / Xi'an Satellite
Control Center), as identified by the [official ESA portal](https://sophia.estec.esa.int/gtoc_portal/?page_id=782)
and their [problem/results paper](https://doi.org/10.1016/j.actaastro.2022.08.040).
TsinghuaLAD&509 won this edition; it was not the organizer.

The final [problem statement](https://sophia.estec.esa.int/gtoc_portal/wp-content/uploads/2021/12/gtoc11_problem_stmt.pdf)
is dated 16 October 2021. Direct retrieval failed on the integration date
(certificate mismatch / HTTP 503), as did access to the former competition
site. Its indexed final text confirms alpha=6e-9 s^-1, the state tolerances,
constants and format. A full historical PDF dated 10 October was recovered
from [jondavis847/gtoc_11](https://github.com/jondavis847/gtoc_11/blob/48e0bca768f3686921b8453c135a17d5393dfae7/gtoc11_problem_stmt.pdf),
with SHA-256 `e5882026b35feb2bd5757eac399051af156077c2f9e8e49e4a53de10365968ea`.
That PDF has the superseded alpha=1e-9 and is retained only in host references.
The working mathematical statement and format here are newly written references
using the corrected final constant and the received executable's contract.

No time bonus is applied. The adapter independently recomputes J at B=1 after
official acceptance and checks the printed score. This verifies the standalone
program's score without assuming a historical submission date.

The official 25-team leaderboard and a separately labelled approximation with
B=1 are host-side comparison data under `references/leaderboards/gtoc11/`.
Neither those tables, the received example nor the Windows executable is copied
into Docker or the initial agent workspace. Reference information is never an
input to the judge. This is a local research integration; receiving the binary
does not establish an open-source license or independent organizer endorsement
of the benchmark wrapper.
