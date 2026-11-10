# GTOC X — Settlers of the Galaxy

Problem by Anastassios E. Petropoulos, Eric Gustafson, Gregory Whiffen and Brian
Anderson, Jet Propulsion Laboratory, California Institute of Technology.
Released 15 May 2019. Original copyright: © 2019 California Institute of
Technology; U.S. Government sponsorship acknowledged.

This is a mathematical reference transcription for GTOC-Bench. The complete
[original statement](documents/gtocX_problem_stmt.pdf), including its figure,
is also supplied. Read [clarifications.md](clarifications.md) and
[validation-policy.md](validation-policy.md) for the organizer's clarifications
and the explicit independent-judge policy. The benchmark fixes the historical
calendar bonus to **B=1**.

## Scenario and fleet

Settle as many of the 100,000 candidate stars as possible, with a useful spatial
distribution and low propulsive cost, during 90 Myr after Year Zero. The supplied
`data/stars.txt` contains 100,001 rows after one header: Sol has ID 0, and targets
have IDs 1–100000. A star may be settled only once; Sol is not counted in N.

Up to three Mother Ships and two Fast Ships may depart Sol. Each initial departure
must occur between Year Zero and 10 Myr. A Mother Ship carries up to ten
Settlement Pods. At a stellar flyby it releases a pod with its instantaneous
position and velocity. The pod immediately performs one rendezvous impulse to
match the star's velocity and settles it. The Mother Ship receives no gravity
assist or velocity change from the encounter.

Each Fast Ship rendezvouses with and settles one star. After a star has been
settled for at least 2 Myr, it can launch up to three Settler Ships. Each Settler
Ship rendezvouses with and settles one new star. All three children can depart
simultaneously. Every part of the settlement tree must be causally connected
to a Mother Ship/Pod or Fast Ship from Sol. Every settlement is by 90 Myr.

## Stellar ephemerides and ship dynamics

The catalogue columns are `ID, R(kpc), i(deg), Omega(deg), phi(deg), theta_f(deg)`.
Stars follow circular orbits under the same central force as ships. Angles in
the following equations are radians. Define

```text
P(R) = k8*R^8 + k7*R^7 + ... + k1*R + k0
vc(R) = 1/P(R)                        [km/s]
u = (1,000,000 * 31557600) / 30856775814671900
v(R) = u*vc(R)                        [kpc/Myr]
n = v(R)/R                           [rad/Myr]
a = n*t + phi
p = (cos(Omega), sin(Omega), 0)
q = (-sin(Omega)*cos(i), cos(Omega)*cos(i), sin(i))
r_star(t) = R*(cos(a)*p + sin(a)*q)  [kpc]
v_star(t) = v(R)*(-sin(a)*p + cos(a)*q) [kpc/Myr]
```

These reproduce equations 6–12 of the original statement. Inclinations are near
180 degrees, giving the clockwise galactic convention. Do not substitute a
Newtonian inverse-square point-mass model. Between impulses, every ship obeys

```text
r = sqrt(x*x+y*y+z*z)
d(position)/dt = velocity
d(velocity)/dt = -(u*vc(r))^2 * position/r^2
```

The polynomial coefficients, in descending order, are:

| Coefficient | Value |
| --- | ---: |
| k8 | -1.94316e-12 |
| k7 | 3.7516e-10 |
| k6 | -2.70559e-8 |
| k5 | 9.70521e-7 |
| k4 | -1.88428e-5 |
| k3 | 0.000198502 |
| k2 | -0.0010625 |
| k1 | 0.0023821 |
| k0 | 0.00287729 |

Radii in that polynomial are in kpc, and its reciprocal is in km/s. One kpc is
**30856775814671900 km**; one year is **31557600 s**; one Myr is 1,000,000 years.

## Impulses, timing and tolerances

All impulses instantaneously change velocity; submitted vectors use km/s.

| Vessel | Maximum impulses | Maximum single impulse (km/s) | Maximum total ΔV (km/s) |
| --- | ---: | ---: | ---: |
| Mother Ship | 3 | 200 | 500 |
| Settlement Pod | 1 | 300 | 300 |
| Fast Ship | 2 | 1500 | 1500 |
| Settler Ship | 5 | 175 | 400 |

Consecutive maneuvers of a given ship must be at least 1 Myr apart. Each nonzero
pod impulse must also be at least 1 Myr from every previous **and subsequent**
mother-ship impulse or other pod impulse of the same mother. See the zero-impulse
pod exception in the organizer clarifications. The Mother Ship's trajectory ends
at its final pod release; later motion is ignored and later maneuvers must be omitted.

Every flown arc must remain within 2–32 kpc of the galactic center, including
between impulses. A rendezvous must match the star's position, and its required
arrival impulse is determined by the propagated incoming velocity and stellar velocity.

| Check | Published tolerance |
| --- | ---: |
| Individual and total ΔV limits | 0.01 km/s |
| Arrival impulse magnitude error | 0.01 km/s |
| Maneuver timing and 90 Myr limit | 1 year = 0.000001 Myr |
| Arrival position error | 0.0001 kpc |
| Galactic range boundary violation | 0.01 kpc |

The judge reconstructs the physical arrival-impulse direction and checks its
magnitude against the submitted magnitude, as specified by the original tolerance.
The full vectors of departure and intermediate impulses determine the flight.

## Merit function

The original objective is `J = B * D * (ΔVmax/ΔVused)`; maximize it. In this benchmark
**B = 1**. The remaining equations and constants are retained.

For N distinct settled stars, use each star's catalogue radius `ri` and its
**catalogue-supplied theta_f**, converted to radians. Do not recompute theta_f
from the rounded orbital elements. Define the triangular kernel

```text
f(x; mu, s) = max(0, 1/s - abs(x-mu)/s^2)
fr(r) = sum_i f(r; ri, 1 kpc) / N
ft(theta) = sum_i f(theta; theta_f_i, 2*pi/32) / N
Rk = k+2,                    k=0..30
Theta_k = -pi+2*pi*k/32,      k=0..32
gr(r) = alpha(r)*2*r/(32^2-2^2)
gt(theta) = beta(theta)/(2*pi)
alpha(2)=0.5833; alpha(32)=0.4948; alpha(other)=1
beta(-pi)=beta(pi)=0.5; beta(other)=1
Er = sum_k (fr(Rk)/gr(Rk)-1)^2
Et = sum_k (ft(Theta_k)/gt(Theta_k)-1)^2
D = N/(1+0.0001*N*(Er+Et))
```

The angular kernel is evaluated on this finite interval **without periodic
wrapping**; the endpoint goal factors already account for the boundary.
`ΔVused` sums the magnitudes of all submitted impulses in the verified tree.
`ΔVmax` sums the last column of the vessel table for the vessels actually used
to settle stars: used mothers, used pods, Fast Ships and Settler Ships. Hypothetical
additional children, unused mother ships and unused pods add no capacity.

The historical calendar factor was

```text
B = 1 + ((t_end - t_check)/(t_end - t_start))^4
t_start = 2019-05-21 20:00 UTC
t_end   = 2019-06-12 20:00 UTC
```

It is shown only to document the competition: GTOC-Bench always uses B=1.
The historic leaderboard broke ties at five significant figures using larger N,
then smaller ΔVused. The benchmark maximizes the full numeric score and records
N and ΔVused as metrics without reproducing historical leaderboard tie-breaking.
