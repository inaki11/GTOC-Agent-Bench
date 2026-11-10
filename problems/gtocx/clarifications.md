# Organizer clarifications incorporated in GTOC-Bench

These are paraphrases of the organizer's public answers preserved in ESA's
archive of the original JPL website. The linked threads remain the primary source.

* [Final angles](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/1/index.html):
  stellar positions and velocities use the listed rounded orbital elements.
  Scoring uses the separately supplied theta_f column, computed with higher
  precision by the organizer. It must not be regenerated from the rounded elements.
* [Delta-V accounting](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/33/index.html):
  only ships and pods actually used to settle stars contribute their tabulated
  maximum total Delta-V. Potential unused ships/pods contribute nothing. Do not
  reduce a productive vessel's tabulated capacity based on the number/timing of
  impulses it happens to use. The temporal bonus used the checking server's time
  to the second, which could lag the upload time.
* [Mother-ship retirement](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/43/index.html):
  after the last pod is released, the mother's dynamics are ignored. It need not
  stay in the galactic range bounds until 90 Myr. Do not list later impulses.
* [Launching children](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/44/index.html):
  several children can launch simultaneously; the condition is the at-least-2-Myr
  colony wait, not a launch spacing constraint between distinct Settler Ships.
* [Pod/mother spacing](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/92/index.html):
  a pod's impulse must be at least 1 Myr from earlier **and later** mother impulses
  and other pod impulses of the same mother.
* [Zero-impulse pod](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/17/index.html):
  if the mother rendezvouses with a star it still needs to release a pod to settle
  it. A pod with no impulse adds no maneuver-spacing event; the mother's own
  arrival and later departure maneuvers still need the normal separation.
* [Blank lines](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/113/index.html):
  blank data lines are forbidden, including trailing empty records; only the
  discarded header may be blank.
* [Bonus origin](https://electra.estec.esa.int/jpl.gtocx/gtocx/discussion/12/index.html):
  the opening of submissions was postponed to 21 May 2019, 20:00 UTC. This is
  t_start for the original B formula, not the 15 May problem-release date.
  The benchmark sets B=1 regardless of any submission date.
