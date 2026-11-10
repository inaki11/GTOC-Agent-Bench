# GTOC13 materials and implementation

GTOC13 was organized by the Jet Propulsion Laboratory, California Institute of
Technology, in 2025. The original documents retain their authorship and copyright
notices. They are reproduced as supplied for this local research benchmark.

* `documents/gtoc13_problem_statement.pdf` and
  `documents/gtoc13_submission_format.pdf`: original competition documents from
  the user-supplied `GTOC 13 jack/documents` directory, copied without alteration.
* `data/gtoc13_planets.csv`, `gtoc13_asteroids.csv`, `gtoc13_comets.csv`: original
  catalogues from `GTOC 13 jack/ephemeris`, copied byte for byte, including their
  original encoding.
* `statement.md` and `submission-format.md`: text transcriptions from the other
  supplied `GTOC 13` directory. Unreliable generated figure descriptions and
  approximate plot tables have been removed from the statement. The PDFs retain
  all the original figures and supersede the transcriptions.
* `instructions.md`, `validation-policy.md`, `problem.yaml` and the Python judge
  in `src/gtoc_bench/gtoc13`: new benchmark implementation. No proprietary JPL
  validation code is included or required. The partial validators in the two
  participant projects were inspected but are not used as the trusted judge.

Official context: [GTOC13](https://gtoc.jpl.net/gtoc13/),
[leaderboard](https://gtoc.jpl.net/gtoc13/leaderboard/) and
[results](https://gtoc.jpl.net/gtoc13/results/).

Historical participant trajectories used for research and test fixtures are not
part of the agent's `/problem` materials or the Docker build. Their provenance
is recorded separately under `tests/fixtures/gtoc13/README.md`. In particular,
reported historic scores with a submission-time bonus are not benchmark scores.

`provenance.json` records hashes of these materials and the judge package. Hashes
establish which local revision was used; they do not certify JPL equivalence.
