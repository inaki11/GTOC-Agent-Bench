# GTOC 12 materials

The statement, asteroid catalogue, Linux x86-64 validator and `ScoreData.txt`
were copied byte for byte from the legacy repository at commit
`4d95712de66973fee36ddc34f3ae20d6852b4b6e`. Paths and SHA-256 hashes are in
`provenance.json`. No legacy Python harness, grading wrapper, agent, Kaggle
integration or participant solution is imported.

The statement already defines the static objective, the sum of returned mass.
The supplied `ScoreData.txt` contains zero historical entries. GTOC-Bench
extracts the reported total resource mass and does not reconstruct competitive
activity or dynamic coefficients.

The binary is provided as an existing research artifact, not as newly licensed
GTOC-Bench source. Its upstream source code and a separate redistribution grant
were not present in the imported materials. Confirm the rights to these assets
before publishing a distributable release. Hashes establish provenance, not a
license grant.

The new wrapper checks finite input, native errors, the success marker and a
finite non-negative mass. The zero-resource fixture in the tests is newly
constructed from the statement's Earth ephemeris, not a competition solution.
