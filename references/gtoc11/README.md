# GTOC11 host-only references

The example and the Windows executable were supplied directly by the
competition organizers in the user's `Check Program` folder. The Linux
executable and complete catalogue are preserved unchanged under
`problems/gtoc11/`. All four files and their original instructions are pinned
in `manifest.json`; no original file in Downloads was altered.

The complete `received/Solution.txt` is a valid reference with 148 transferred
asteroids, minimum station mass about 5.4977713e14 kg and official standalone
score **1556.5911** at B=1. Those statistics agree with the final AU-LU row of
the historical leaderboard to its displayed precision. The organizer called
it an example and did not identify its team, so this association is not treated
as a confirmed submission identity or used to replace a leaderboard score.

`sources/gtoc11_problem_stmt_2021-10-10_SUPERSEDED.pdf` is the original initial
statement recovered from a participant repository at an immutable commit. It
was checked visually and has the obsolete alpha=1e-9. The active final value
is 6e-9. The initial PDF is intentionally excluded from agent materials.
`sources/` also preserves the official schedule announcement and captured
extracts of the final statement and final leaderboard. The final PDF itself
could not be downloaded on 3 November 2026; its public URL is documented.

The published schedule HTML removes a frontend API-key literal embedded by the
public website. Announcement text and dates are unchanged. The manifests record
both the sanitized capture hash and the original capture hash for traceability.

The [official and calendar-normalized leaderboards](../leaderboards/gtoc11/official-final.md)
are researcher references. All contents of `references/` are excluded from the
Docker build context and cannot be copied into an agent image by the standard
Dockerfile. They are not read by the protected judge.

`reference-checks.json` records protected Docker checks of the full example,
format and physics mutations, and the derived small zero-score fixture.
These checks establish the observed behavior of the supplied executable; its
closed-source implementation is not claimed to be formally audited.
