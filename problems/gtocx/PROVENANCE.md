# GTOCX original materials and independent implementation

GTOC X was organized by JPL in 2019. Its original documents retain California
Institute of Technology authorship/copyright and U.S. Government sponsorship
notices. This local research benchmark supplies the documents and data as released.

The original JPL website is [archived by ESA](https://electra.estec.esa.int/jpl.gtocx/).
The following files were downloaded directly from that archive:

* [Problem statement](https://electra.estec.esa.int/jpl.gtocx/media/gtocX_problem_stmt.pdf)
  → `documents/gtocX_problem_stmt.pdf`.
* [Submission format](https://electra.estec.esa.int/jpl.gtocx/media/gtocX_submission_format.pdf)
  → `documents/gtocX_submission_format.pdf`.
* [Full star catalogue](https://electra.estec.esa.int/jpl.gtocx/media/stars.txt)
  → `data/stars.txt`.

All three match byte-for-byte the historical copies in the participant repository
`jjsouthworth/GTOCX`, commit `ac4c41d95ea48ab2fea5fb25d08c0c114bae150b`.
The original PDF documents are authoritative; the Markdown references are newly
written descriptions of their equations, format and organizer clarifications.

The Python judge in `src/gtoc_bench/gtocx/` is a new implementation. No JPL
proprietary executable/source and no participant optimizer or propagation code
is used by the protected judge. Public C++/Fortran/MATLAB participant resources
were inspected as secondary checks, with original rules and final submissions
used to determine the intended model.

The 12 final submission files linked from the organizer's archive are retained
separately in `references/gtocx/`, with source URLs and SHA-256 hashes. They are
not copied into Docker, `/problem` or the agent workspace. The official and
calendar-normalized leaderboards likewise remain under `references/leaderboards/`.
They are historical comparison data, not a judge input.

`provenance.json` pins the material and implementation hashes. These establish
reproducibility of the local implementation, not endorsement by the organizer.
