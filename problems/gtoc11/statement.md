# GTOC11: Dyson Sphere Building

This is a working mathematical reference for the final GTOC11 problem,
organized in 2021 by the National University of Defense Technology and the
Xi'an Satellite Control Center. The original Linux checker and asteroid
catalogue were provided directly by the organizers and are used unchanged.
Sources and the distinction between the initial and final statement are
recorded in `PROVENANCE.md` and `documents/README.md`.

## Objective and scenario

Use at most ten mother ships to visit candidate asteroids and deploy asteroid
transfer devices (ATDs). Transfer selected visited asteroids to twelve stations
on a circular Dyson ring. Choose the ring orbit, the station phases, ship
trajectories, ATD controls and arrival schedule to maximize the merit function.
Not every visited asteroid has to be transferred.

Every mission event must occur in **[95739, 103044] MJD**, inclusive: 1 January
2121 to 1 January 2141. Coordinates are J2000 heliocentric ecliptic Cartesian
coordinates. Use km for position, km/s for velocity and impulses, m/s^2 for
submitted ATD acceleration, kg for mass, and days for MJD epochs.

## Dynamics and catalogue

With physical time in seconds, heliocentric motion obeys

```text
dr/dt = v
dv/dt = -mu * r / |r|^3 + a_ATD
mu = 1.32712440018e11 km^3/s^2
```

The ATD term is zero for mother ships, inactive asteroids and stations. Mother
ship maneuvers change velocity instantaneously: `v_after = v_before + dv`.
An active asteroid's acceleration has constant magnitude **1e-4 m/s^2**, or
**1e-7 km/s^2**; its direction is selectable. Once activated, the ATD must
remain active until rendezvous with its destination station.

`data/Candidate Asteroids.txt` contains 83,453 rows without a header. Columns are:

```text
ID  epoch_MJD  a_AU  e  i_deg  RAAN_deg  argument_of_perihelion_deg  mean_anomaly_deg  initial_mass_kg
```

The elements describe unforced Keplerian motion at the catalogue epoch.
Use the supplied IDs exactly. Earth has these elements at **59396 MJD**:

| Element | Value |
| --- | ---: |
| a (AU) | 9.998012770769207e-1 |
| e | 1.693309475505424e-2 |
| i (degrees) | 3.049485258137714e-3 |
| RAAN (degrees) | 1.662869706216879e2 |
| argument of perihelion (degrees) | 2.978214889887391e2 |
| mean anomaly (degrees) | 1.757352290983351e2 |

One AU is **1.49597870691e8 km**, one day **86400 seconds**, and one year
**365.25 days**. Do not replace these with a library's slightly different AU.

## Mother ships and flybys

Each mother ship starts at Earth with a hyperbolic excess velocity of at most
**6 km/s**, in any direction. This departure excess velocity is **excluded**
from the total Delta-V used in the merit function. Subsequent impulse magnitudes
are summed for that ship. There is no individual impulse magnitude cap stated
for those maneuvers.

At a counted asteroid flyby, ship and asteroid must be within **1 km**, with
relative speed at most **2 km/s**. The ATD rendezvous impulse and its propellant
cost are outside this problem's accounting. The flyby itself does not change
the mother ship velocity; if a ship maneuver is needed there, provide a separate
impulse row. No asteroid can be visited twice in a submitted fleet.

A ship may use at most **four impulses per transfer leg**, including the leg
from Earth to its first asteroid and each leg between consecutive asteroid
flybys. The departure row counts among the impulse rows in the file format,
even though its Delta-V is not charged in the objective.

## Asteroid transfers and the ring

An ATD must wait at least **30 days** after the mother ship flyby before
activation. Its acceleration then has fixed magnitude until station arrival.
There are two official control representations: constant direction over each
step (type 0), or continuously varying direction (type 1). See the submission
format and the numerical policy for their distinct checker behavior. Consecutive
reported asteroid states are nominally at most **one day apart**.

Mass decreases **linearly with elapsed thrust time**, in proportion to the
asteroid's **original** mass, not its instantaneous remaining mass:

```text
alpha = 6e-9 s^-1
m_final = m_initial * (1 - alpha * (t_arrival_MJD - t_activation_MJD) * 86400)
```

Remaining mass cannot be negative. The initial 10 October statement's alpha
of 1e-9 s^-1 was revised; **6e-9 is the final value and the value used by the
received official checker**. The ATD can be used on only one asteroid, and a
given asteroid can be transferred only once.

All stations share a circular orbit. Design four elements at **95739 MJD**:
`a_Dyson` in AU, inclination in degrees, RAAN in degrees, and the first station's
argument of latitude `phi_1` in degrees. The radius must satisfy
**a_Dyson >= 0.65 AU**; eccentricity is zero. Station j starts with phase
`phi_1 + 30*(j-1)` degrees for j=1..12 and advances at the Keplerian circular
mean motion. Both position and velocity must match at arrival.

Construct stations sequentially in any chosen order. All arrivals to one
station form a group; the last arrival to one station and first arrival to the
next station must be separated by at least **90 days**. Stations cannot be
built concurrently. Station IDs identify geometric phase, not construction
order. The heliocentric distance of every mother ship and asteroid must remain
at least **0.4 AU** throughout flight. Gravity assists are not allowed.

The final statement lists state tolerances of **10 km**, **0.01 m/s** and
**1 kg** for position, velocity and mass. The supplied executable implements
the official numerical checks and tolerances; the wrapper does not replace its
propagator or control interpolation. `validation-policy.md` records observed
serialization and numerical details of the supplied example.

## Merit function used by this benchmark

Let `M_j` be the sum of final delivered masses at station j and
`M_min = min(M_1, ..., M_12)`. Empty stations have mass zero. For each of the
ten ship slots let `DV_k` be the sum of its post-departure impulse magnitudes,
in km/s; unused slots have `DV_k=0` and still contribute 1 to the sum.

```text
J = (1e-10 * M_min) / (a_Dyson^2 * sum_{k=1..10} (1 + DV_k / 50)^2)
```

Maximize J in points. The historical competition multiplied this expression
by a submission-time factor:

```text
B = 2 - sqrt(1 - (1 - (t_submission-t_start)/(t_end-t_start))^3)
```

**The benchmark always uses B=1**. No run date, upload time, system clock or
calendar bonus enters scoring. The received standalone checker already returns
the B=1 score; the adapter independently recomputes the merit from the complete
accepted file to check this agreement. The official printed precision is
retained in the awarded score.
