> Benchmark note: this is a text transcription for convenient reading. The original
> [problem statement PDF](documents/gtoc13_problem_statement.pdf) contains all figures
> and is the authority for the historical competition. Read [validation-policy.md](validation-policy.md)
> for the explicit GTOC-Bench differences, including time bonus c = 1.

> Read together with `validation-policy.md` for the benchmark conventions.
> The original PDFs in `documents/` are the authoritative competition documents.



# Problem Description of the 13th Global Trajectory Optimisation Competition

# GTOC13: Humanity’s First Robotic Exploration of a Hypothetical Exoplanetary System

Gregory Whiffen, Mark Wallace, Damon Landau, Etienne Pellegrini, Gregory Lantoine, Anastassios Petropoulos, Sungmoon Choi, Brian Anderson, Zubin Olikara, Jon Sims

*Jet Propulsion Laboratory, California Institute of Technology*

*Release Date: 20 October 2025*

## 1. Background

The Altaira system represents one of the most intriguing and complex exoplanetary systems in our stellar neighborhood. Decades of remote sensing and astronomical observations from Earth and its vicinity have only deepened our curiosity, revealing a diverse collection of worlds unlike anything in our own solar system. Among these are multiple planets located within the habitable zone, as well as other planets that do not have solar system analogues, such as the massive planet **Vulcan** that orbits the star in a very close orbit – a so called “*Hot Jupiter*”. At the heart of this system lies **Altaira**, a star slightly brighter and more massive than our Sun.

Fortunately, long ago, in a bold act of interstellar foresight, a small robotic spacecraft was placed on a many-year transit trajectory to explore this system in depth. Now, as the spacecraft nears its destination, your task begins: to design a **multi-decade tour design** to maximize our understanding of the system. Much of the enormous interstellar relative velocity will be removed before the final approach when our problem begins, however the spacecraft may still arrive with a high incoming velocity far from the target system. As a result, the first problem is to achieve capture into orbit around Altaira. The larger the incoming velocity the faster the spacecraft can start the tour but the harder it is to capture. The spacecraft end-of-life depends on battery decay and is therefore a fixed date. Due to the extraordinary cost of interstellar transport, virtually no chemical propellant remains upon arrival. This means that the tour must rely entirely on ballistic, propellant-less gravity assists, and, optionally, solar-sail maneuvers. Slower flybys allow for greater science return and are therefore more highly valued. Furthermore, repeated flybys during different seasons best complete our understanding of each body.

All in all, your challenge is to design this unprecedented, long-term robotic exploration campaign and unveil the secrets of the Altaira system with a careful choreography of planetary flybys.

*See the corresponding figure in the original problem statement PDF.*

## 2. Exo-Solar System Description

The central star of the simulated exo-solar system, Altaira, is a G1v-type main-sequence star, slightly larger and more luminous than our Sun. It is accompanied by 10 major planets, 1 dwarf planet, 257 asteroids, and 42 comets. They are described below.

### *Major Planets*

The major planets move in Keplerian orbits with initial states given in the separate **gtoc13\_planets.csv** file (available on the submission website). The names are intended to be humorous and were useful during problem formulation for the organizers to keep the planets' relative locations and sizes straight. The planets are listed here in order of increasing orbital period.

Altaira – A G1v class star, about 1.05 solar masses.

Vulcan – A “Hot Jupiter” in a very close orbit. Its orbit plane defines the ecliptic of the exoplanetary solar system

Yavin – Lies near the inner edge of the habitable zone

Eden – Earth-sized planet near the middle of the habitable zone

Hoth – Venus-sized planet, relatively highly inclined and just below the inner edge of the main asteroid belt

Yandi – A dwarf planet embedded in the main asteroid belt. Unlike the other planets, it is treated as a massless body.

Beyoncé – Ringed Saturn-sized planet; resonances with this planet define the main asteroid belt

Bespin – A Super-Jovian

Jotunn – An Ice Giant, similar in size to Neptune and Uranus.

Wakonyingo – An ice giant stripped of its atmosphere, leaving a super-Earth terrestrial planet

Rogue1 – Captured Jovian exoplanet in a retrograde orbit in a 2:1 resonance with PlanetX

PlanetX – Highly eccentric, highly inclined, and in a 1:2 resonance with Rogue1

### *Main-Belt Asteroids*

The 257 main-belt asteroids of interest lie between the orbits of Hoth and Beyoncé. The asteroids are all treated as massless bodies and move in purely Keplerian orbits with initial states given in the separate **gtoc13\_asteroids.csv** file (available on the submission website).

### *Comets*

The 42 comets of interest can be found throughout the exo-solar system. Like the planets and main-belt asteroids, they move in purely Keplerian orbits. Their initial states are given in the separate **gtoc13\_comets.csv** file (available on the submission website).

## 3. Objective Function

The objective is to maximize science return from a tour of the exoplanetary system. The following cost function is to be maximized:

$$J = bc \sum_{k \in ID} w_k \sum_{i=1}^{N_k} (S(\hat{\mathbf{r}}_{k,i}) \times F(V_{\infty,k,i}))$$

where

$b$  is a grand tour bonus term (see section 3.1.)

$c$  is a time bonus term that decreases during the competition time frame (see section 3.2.)

$k$  index is the body ID:  $k \in [1 \dots 10, 1000 \dots 1257, 2001 \dots 2042]$

$i$  index refers to  $i$ th scientific flyby (in chronological order) of body  $k$

$N_k$  is the total number of scientific flybys of body  $k$ :  $N_k \leq 13$ , i.e. up to 13 flybys per body can be designed as scientific flybys and can count in the score. A flag accompanying each flyby in the submitted solution specifies whether the flyby is for science purposes and should be counted in the objective function (see separate solution format file, **gtoc13\_submission\_format.pdf**). Additional non-scientific flybys of each body are permitted but will not count in the score.

$w_k$  is the constant scoring weight of body  $k$ , reflecting its perceived scientific merit. The scientific weights of each body are given in each csv data ephemeris file and reproduced in Table 1 for clarity purposes.

$\hat{\mathbf{r}}_{k,i}$  is the unit heliocentric position vector of body  $k$  at its  $i$ th scientific flyby

$V_{\infty,k,i}$  is the hyperbolic excess velocity magnitude of the spacecraft relative to body  $k$  at its  $i$ th scientific flyby. See Appendix 1 for a definition of the hyperbolic excess velocity.

$S$  is the seasonal penalty term (see section 3.3.) to encourage seasonal diversity.

$F$  is the flyby velocity penalty term (see section 3.4.)

For example, if a tour is submitted at the start of the competition with only one flyby of PlanetX and a relative flyby velocity of 10 km/s, then:

- $b = 1$  (no grand tour bonus, see section 3.1.)
- $c = 1.13$  (full time bonus, see section 3.2.)
- $w_{10} = 50$ ,  $N_{10} = 1$ ,  $S = 1$  (see section 3.3.),  $F = 0.663369$  (see section 3.4.)
- All in all,  $J = 37.480$

Table 1 – Body scientific weights

| Body ID   | Body Name  | Weight w |
|-----------|------------|----------|
| 1         | Vulcan     | 0.1      |
| 2         | Yavin      | 1        |
| 3         | Eden       | 2        |
| 4         | Hoth       | 3        |
| 1000      | Yandi      | 5        |
| 5         | Beyonce    | 7        |
| 6         | Bespin     | 10       |
| 7         | Jotunn     | 15       |
| 8         | Wakonyingo | 20       |
| 9         | Rogue1     | 35       |
| 10        | PlanetX    | 50       |
| 1001-1257 | Asteroids  | 1        |
| 2001-2042 | Comets     | 3        |

Should there be a tie to three decimal places in  $J$ , the solution with larger total number of scientific flybys will win. Should there still be a tie, the solution with larger initial velocity will win.

### 3.1. Grand tour bonus $b$

The grand tour bonus term  $b$  is equal to 1.2 if the submitted solution has a scientific flyby of all the planets, the dwarf planet Yandi and at least 13 asteroids or comets. Otherwise,  $b$  is equal to 1.

### 3.2. Time bonus $c$

The time bonus term  $c$  is first constant then decreases linearly until the end of the 4-week competition, and is computed as follows

$$c = \begin{cases} 1.13 & \text{if } t \leq 7 \\ -0.005t + 1.165 & \text{if } t > 7 \end{cases}$$

where  $t$  is the time elapsed, measured in days, from the competition start time to the solution submission time. The time bonus term  $c$  is plotted in Figure 2 as a function of time.

*See the corresponding figure in the original problem statement PDF.*

### 3.3. Seasonal penalty term $S$

To encourage seasonal diversity when doing multiple flybys of the same body, the objective function includes a seasonal penalty term  $S$ :

$$S(\hat{\mathbf{r}}_{k,i}) = 0.1 + \frac{0.9}{1 + 10 \sum_{j=1}^{i-1} \exp \left( -\frac{[\text{acosd}(\hat{\mathbf{r}}_{k,i} \cdot \hat{\mathbf{r}}_{k,j})]^2}{50} \right)}$$

where

$\hat{\mathbf{r}}_{k,i}$  is the unit heliocentric position vector of body  $k$  at its  $i$ th flyby.

$\text{acosd}$  is the arccosine function with output expressed in degrees between  $0^\circ$  and  $180^\circ$ .

$$S(\hat{\mathbf{r}}_{k,1}) = 1.$$

This term reduces the contribution of the  $i$ th scientific flyby of body  $k$  if its heliocentric direction  $\hat{\mathbf{r}}_{k,i}$  is too similar to those of previous scientific flybys of that same body. Flybys of the same body clustered near the same solar phase angle are penalized, while those distributed across a broad range of viewing geometries are unaffected. This reflects the scientific value of observing a body under varying illumination conditions around Altaira.

For example, let's hypothetically assume 2 flybys of body  $k$ , with  $\hat{\mathbf{r}}_{k,1} = \begin{bmatrix} 0 \\ 1 \\ 0 \end{bmatrix}$  and  $\hat{\mathbf{r}}_{k,2}$  parametrized by an angle  $\theta \in [0, 360^\circ]$ , i.e.  $\hat{\mathbf{r}}_{k,2} = \begin{bmatrix} \cos \theta \\ \sin \theta \\ 0 \end{bmatrix}$ . Then Figure 3 plots the function  $S$  as a function of  $\theta$ . In particular, if  $\theta = 90^\circ$ , then  $\hat{\mathbf{r}}_{k,2} = \hat{\mathbf{r}}_{k,1}$  and  $S = 2/11$ .

*See the corresponding figure in the original problem statement PDF.*

### 3.4. Flyby Velocity penalty term $F$

$$F(V_\infty) = 0.2 + \frac{\exp(-V_\infty/13)}{1 + \exp(-5(V_\infty - 1.5))}$$

where  $V_\infty$  is the flyby hyperbolic excess velocity magnitude (expressed in km/s).

This term penalizes flybys with large hyperbolic excess velocity  $V_\infty$ , which would correspond to shorter observation times with the target body. In addition, this term penalizes rendezvous-like encounters due to radiation risks and other environmental uncertainties near each body. The Flyby Velocity penalty term  $F$  is plotted in Figure 4 as a function of  $V_\infty$ .

*See the corresponding figure in the original problem statement PDF.*

## 4. Coordinate Frame and Initial State of the Spacecraft

The ecliptic plane is the orbital plane of Vulcan. Fortunately, the initial heliocentric velocity of the spacecraft, at a distance of 200 AU from Altaira, is parallel to the ecliptic plane. Thus, the  $x$  direction is taken as lying along this velocity direction, positive towards Altaira. The  $z$  direction is taken as perpendicular to the ecliptic, positive in the direction of Vulcan's orbital angular momentum. The  $y$ -axis is then defined as  $\hat{y} = \hat{z} \times \hat{x}$ . The orbital elements listed in the ephemeris files, as well as the initial spacecraft state listed in Table 2, are all expressed in this coordinated frame. The solution files must also use this coordinate frame. For reference, the conversion between orbital elements and cartesian elements is given in Appendix 1.

The initial state of the spacecraft is defined in Table 2 and illustrated in Figure 5. It corresponds to an incoming interstellar asymptote nearly aligned with the  $+x$  direction. Initial position components are free in the  $yz$  plane perpendicular to the  $x$  axis. This initial state is defined at initial time  $0 \leq t_0 \leq 200$  years, i.e. the initial time is not fixed but must be selected between 0 and 200 years. It is recommended (but not mandatory) to start with a positive  $V_x$  value.

The Mean Anomaly listed in the ephemeris files is for reference epoch time  $t=0$ .

Table 2 – Initial spacecraft state

| Initial States | Value   |
|----------------|---------|
| x              | -200 AU |
| y              | Free    |
| z              | Free    |
| Vx             | Free    |
| Vy             | 0       |
| Vz             | 0       |

*See the corresponding figure in the original problem statement PDF.*

## 5. Dynamics and Solar Sail Model

The bodies in the solar system move on conic orbits (Keplerian motion) around Altaira. The spacecraft is also on a conic orbit, unless it deploys its solar sail. Flybys are modelled using the patched conic method. A detailed description, including the effect of the acceleration from the solar sail, is given in Appendix 1.

The solar sail is available on the spacecraft to maneuver during any time intervals of choice after the start of the trajectory. Note that using the solar sail is optional and ballistic Keplerian propagation is allowed on any time interval. In particular, it is possible to have alternating solar-sail-powered arcs and ballistic Keplerian arcs. Refer to the solution file format ([gtoc13\\_submission\\_format.pdf](#), available on the submission website) for more details on the corresponding solution file implementation.

An ideal sail model is assumed, where the solar radiation pressure is perfectly reflected from the sail surface (i.e. perfect mirror). Eclipses are ignored. The acceleration of the sail is given below:

$$\mathbf{a}_{sail} = -\frac{2CA}{m} \left(\frac{r_0}{r}\right)^2 (\hat{\mathbf{u}}_n \cdot \hat{\mathbf{u}}_r)^2 \hat{\mathbf{u}}_n$$

where  $C$  is the Altaira flux at 1 AU (in N/m<sup>2</sup>),  $A$  is the sail area (in m<sup>2</sup>),  $m$  is the spacecraft mass (in kg),  $\hat{\mathbf{u}}_n$  is the unit vector in the direction of the sail normal,  $\hat{\mathbf{u}}_r$  is the unit vector from spacecraft pointing to Altaira,  $r$  is the corresponding spacecraft distance to Altaira's center (in km),  $r_0$  is the reference distance equal to 1 AU, expressed in km. Figure 6 conceptually illustrates the ideal sail model. The cone angle  $\alpha$  is defined as the angle between the sail unit normal  $\hat{\mathbf{u}}_n$  and the sun-pointing unit vector  $\hat{\mathbf{u}}_r$ :

$$\cos \alpha = \hat{\mathbf{u}}_n \cdot \hat{\mathbf{u}}_r$$

Because a solar sail cannot generate an acceleration with a component in the direction of Altaira, the normal vector  $\hat{\mathbf{u}}_n$  must be chosen to point “inwards” towards Altaira, such that  $\mathbf{a}_{sail}$  has no radially inward component, and the cone angle is defined in the range  $\alpha \in [0^\circ, 90^\circ]$ .

*See Figure 6 in the original problem statement PDF.*

Parameters of the ideal solar sail model are given in Table 3. Figure 7 plots the corresponding sail acceleration at 13 AU. For example, at 13 AU, when facing the Sun ( $\alpha = 0^\circ$ ),  $\mathbf{a}_{sail} = 0.001918 \text{ mm/s}^2$  (about 5% of the local gravitational acceleration due to Altaira).

Table 3 – Solar sail model parameters

| Parameter                | Value                                |
|--------------------------|--------------------------------------|
| Altaira flux $C$ at 1 AU | $5.4026 \cdot 10^{-6} \text{ N/m}^2$ |
| Reference distance $r_0$ | 149597870.691 km                     |
| Sail area $A$            | 15,000 $\text{m}^2$                  |
| Spacecraft mass $m$      | 500 kg                               |

*See the corresponding figure in the original problem statement PDF.*

## 6. Constants

Apart from the solar sail parameters already given in Table 3, the values of the other constant parameters of the GTOC13 problem are provided in Table 4.

Table 4 – GTOC13 constants

| Constant             | Value                                     |
|----------------------|-------------------------------------------|
| AU                   | 149597870.691 km                          |
| Altaira GM ( $\mu$ ) | 139348062043.343 $\text{km}^3/\text{s}^2$ |
| Day                  | 86400 s                                   |
| Year                 | 365.25 days                               |

## 7. Constraints, Tolerances and Solution Checking

1. All the trajectory events (including trajectory start & flybys) must occur within a fixed time window of 200 years measured from the reference epoch  $t = 0$ . In particular, the initial time  $t_0$  when the initial conditions in Table 2 are defined must satisfy  $0 \leq t_0 \leq 200$  years. Similarly, the last time of the trajectory solution should lie between 0 and 200 years.
2. All close approaches to Altaira except for one must be at or above a range of 0.05 AU. The spacecraft is equipped with a thermal protection system which will allow a single perihelion passage as low as 0.01 AU. This single, lower passage can be applied at any perihelion, i.e. it does not need to be the first perihelion passage.
3. If any two successive flybys (scientific or non-scientific) are of the same body, then the time interval between these flybys must be no less than 1/3 of the body orbital period around Altaira, in order to allow enough time for navigation of each flyby.
4. The heliocentric position vector of the spacecraft at the time of a flyby of a body must be equal to the heliocentric position vector of the body at that time (subject to conic position tolerance given below).
5. Planetary flybys (body ID between 1 and 10) are modeled using the gravity assist patched conics model described in Appendix 1. The incoming and outgoing spacecraft hyperbolic excess velocities relative to the planet must have equal magnitude. Each flyby must occur at an altitude between 0.1 and 100 body radii from the surface of the flyby body. The flyby altitude is computed from the patched conics model described in Appendix 1.
6. The dwarf planet Yandi, asteroids and comets (body ID between 1000 and 1257) are treated as massless. Therefore, the incoming and outgoing spacecraft hyperbolic excess velocities relative to these bodies must have equal magnitude and direction (i.e. be continuous) at the flyby of these bodies.
7. No encounter of asteroids and comets can count in the score until the first perihelion (i.e. first close approach of Altaira).
8. When solar sail is used, sail cone angle should always be between 0 deg and 90 deg, inclusive. If a cone angle of 90 deg is used for an extended period, it would be preferable to list that as a ballistic Keplerian arc in the solution file (see Submission Format document for details).

Upon submission via the competition website, solutions will be checked against our independent propagations generated from the submitted data. Solutions will have to meet the following tolerances to be considered valid:

- Conic Position & Velocity tolerance: 100 m and 0.1 mm/s
- Initial (trajectory start) Position & Velocity tolerance: 100 m and 0.1 mm/s
- Vinfinity equality tolerance: 0.1 mm/s
- Time, Position & Velocity continuity tolerance: to all reported digits of accuracy
- Relative tolerance for numerical integration (applies to both position and velocity over 1 propagated segment, see paragraph below):  $10^{-4}$
- Reporting interval for numerical integration: >60 s (note that intervals much longer than 60s are generally expected)
- Flyby-altitude tolerance: 100 m
- Perihelion altitude tolerance: 1 km

For numerically integrated arcs, the step size between rows in the solution file should be compatible with one 4<sup>th</sup>-order Runge Kutta RK4 integration step using a relative error tolerance of  $10^{-4}$ . That is,  $\|X_{RK4} - X_f\| / \|X_f - X_0\| < 10^{-4}$ , where  $X_{RK4}$  is the state (position or velocity vector) propagated using RK4,  $X_f$  is the state at the end of the segment from the solution file, and  $X_0$  is the state at the beginning of the segment. If the solution passes this first check, then we replace  $X_{RK4}$  with the state derived from a higher-order collocation method to ensure that  $X_f$  also matches a ‘truth’ solution to  $10^{-4}$ . We will use the control from the solution file at the beginning and end of each segment, and compute ourselves the sail control along the segment interior that minimizes integration error. It is assumed that the control is continuous between timesteps. If there is an abrupt/discontinuous change in sail direction, then two rows with the same time and state but different controls should be used in the solution file. For example, piecewise constant control segments would require (at least) two lines per constant sail direction. More than two lines are necessary for piecewise constant control segments when more than one integration step is required to meet the relative error tolerance.

Note that we do not prescribe an integration method used to design the trajectory, just the time resolution of the solution output should pass the required tolerance using a common RK4 scheme. A solution obtained using RK4 propagation with error control is expected to pass. However, propagation without error control is not expected to pass, and a solution obtained using a higher-order integrator would require a finer step size for the solution file.

## 8. Submission Process

Solutions are to be submitted via the competition website, <https://gtoc.jpl.net/>, by the registered user(s) for each team. The solution will be automatically verified immediately after upload. Upon successful verification, the submission epoch, score, number of scientific flybys, initial velocity and time of flight will be displayed in the *Leaderboard* on the website. The maximum file size that a team is allowed to submit is 100 Mb, although it is expected that much smaller file sizes will be sufficient for even intricate solutions. Teams can optionally submit a solution for verification and scoring only (a “trial” solution), without having it posted to the *Leaderboard*. Teams can submit up to 10 submissions per sliding 24-hour window, with any submission that is verified successfully (including successful trial submissions) counting towards the limit. In the event of technical difficulties with the website, teams may also submit solutions by email to [gtoc13@jpl.nasa.gov](mailto:gtoc13@jpl.nasa.gov) for manual verification and scoring.

The file format for the submissions and more detailed information on the submission process are defined in a separate file, [gtoc13\\_submission\\_format.pdf](#), available on the submission website.

## 9. Acknowledgements

For the selection and formulation of this year's problem, the authors wish to thank the many GTOC enthusiasts at JPL for their discussions on this and other candidate problems. The work presented in this paper was carried out in part at the Jet Propulsion Laboratory, California Institute of Technology, under a contract with the National Aeronautics and Space Administration.

## Appendix I: Dynamics and conversions between elements

The motion of the all the planets, asteroids and comets around the central star, Altaira, is governed by these equations, expressed in a Cartesian coordinate frame centered on Altaira:

$$\ddot{x} + \mu \frac{x}{r^3} = 0, \quad \ddot{y} + \mu \frac{y}{r^3} = 0, \quad \ddot{z} + \mu \frac{z}{r^3} = 0$$

where

$$r = \sqrt{x^2 + y^2 + z^2} = \frac{a(1 - e^2)}{1 + e \cos \theta},$$

with  $\mu$  being the gravitational parameter of Altaira,  $a$  and  $e$  being constants for each body (semimajor axis and eccentricity, respectively), given in the ephemeris files, and  $\theta$  being the true anomaly as described below. The motion of the spacecraft around Altaira is governed by the same formulae but with the addition of the  $x, y, z$  components of the sail acceleration, as well as the ability to introduce discontinuities in the spacecraft velocity by means of an impulse from flybys of the massive planets:

$$\ddot{x} + \mu \frac{x}{r^3} = a_x, \quad \ddot{y} + \mu \frac{y}{r^3} = a_y, \quad \ddot{z} + \mu \frac{z}{r^3} = a_z,$$

The sail acceleration vector has a direction and magnitude defined in Section 5. The permitted discontinuities in the spacecraft velocity due to the flybys are described further below.

Conversion from orbit elements to Cartesian quantities is as follows:

$$\begin{aligned} x &= r[\cos(\theta + \omega) \cos \Omega - \sin(\theta + \omega) \cos i \sin \Omega] \\ y &= r[\cos(\theta + \omega) \sin \Omega + \sin(\theta + \omega) \cos i \cos \Omega] \\ z &= r[\sin(\theta + \omega) \sin i] \\ v_x &= v[-\sin(\theta + \omega - \gamma) \cos \Omega - \cos(\theta + \omega - \gamma) \cos i \sin \Omega] \\ v_y &= v[-\sin(\theta + \omega - \gamma) \sin \Omega + \cos(\theta + \omega - \gamma) \cos i \cos \Omega] \\ v_z &= v[\cos(\theta + \omega - \gamma) \sin i] \end{aligned}$$

where

$a, e, i, \Omega, \omega$  are the semimajor axis, eccentricity, inclination, longitude of the ascending node, and argument of periapsis, respectively, as given in the ephemeris files,

the velocity  $v$  is

$$v = \sqrt{\frac{2\mu}{r} - \frac{\mu}{a}},$$

the flight path angle,  $\gamma$ , is obtained from

$$\tan \gamma = \frac{e \sin \theta}{1 + e \cos \theta},$$

the true anomaly,  $\theta$ , is related to the eccentric anomaly,  $E$ , by

$$\tan \frac{E}{2} = \sqrt{\frac{1 - e}{1 + e}} \tan \frac{\theta}{2},$$

the eccentric anomaly is related to the mean anomaly,  $M$ , by Kepler's equation,

$$M = E - e \sin E,$$

and the mean anomaly is related to time,  $t$ , and the initial mean anomaly by

$$M - M_0 = \sqrt{\frac{\mu}{a^3}}(t - t_0).$$

The initial mean anomaly,  $M_0$ , is given for each body in the ephemeris files for the initial time, which is taken as  $t_0 = 0$ . The gravitational parameter,  $\mu$ , for Altaira is given in Table 4. Thus, the Cartesian positions and velocities of the bodies in the Altaira system may be computed as a function of time with only the minor nuisance of having to solve Kepler's equation for  $E$  by some iterative procedure. In other words, the bodies follow Keplerian motion. That is, for the bodies and for a coasting spacecraft (sail not deployed), the equations of motion do not need to be numerically integrated to find position and velocity at some given time. Self-consistent units must of course be used in the equations.

The orbit elements may also be computed from the Cartesian state by inverting the equations.

### Mathematical modelling of “patched-conic” flybys

Flybys of the ten massive planets are modelled using the patched-conic approximation and neglecting the time spent inside the planet’s sphere of influence. The gravity assist (flyby) occurs at time  $t_G$  when the spacecraft heliocentric position,  $\vec{x}$ , propagated using the equations above, equals the planet’s heliocentric position,  $\vec{x}_P$ , at time  $t_G$ ; the spacecraft heliocentric velocity then undergoes a discontinuous change in such a way that the outgoing and incoming hyperbolic excess velocity vectors,  $\vec{v}_\infty$ , relative to the planet have the same magnitude and are separated by the turn angle  $\delta_t$ . Specifically,

$$\begin{aligned}\vec{x}(t_{G-}) &= \vec{x}_P(t_{G-}) \\ \vec{x}(t_{G+}) &= \vec{x}_P(t_{G+}) \\ \vec{x}(t_{G+}) &= \vec{x}(t_{G-}) \\ \vec{v}_{\infty G-} &= \vec{v}(t_{G-}) - \vec{v}_P(t_{G-}) \\ \vec{v}_{\infty G+} &= \vec{v}(t_{G+}) - \vec{v}_P(t_{G+}) \\ |\vec{v}_{\infty G+}| &= |\vec{v}_{\infty G-}| = v_\infty \\ \vec{v}_{\infty G+} \cdot \vec{v}_{\infty G-} &= v_\infty^2 \cos \delta_t \\ \sin(\delta_t/2) &= \frac{\mu_P/(R_P + h_{pP})}{v_\infty^2 + \mu_P/(R_P + h_{pP})}\end{aligned}$$

subject to the timing and altitude constraints

$$t_{G+} = t_{G-}, \quad 0.1R_P \leq h_{pP} \leq 100R_P$$

with  $\mu_P$  the gravitational parameter (GM) of the planet, and  $R_P$  the radius of the planet, as given in the ephemeris file.

For computational purposes, the equality condition on the flyby position is considered satisfied if the quantity  $|\vec{x}(t_G) - \vec{x}_P(t_G)|$  is at or below the position tolerance specified in Section 7. Similarly, for the flyby velocity condition, the quantity  $(|\vec{v}_{\infty G+}| - |\vec{v}_{\infty G-}|)$  must be within the velocity tolerance specified in Section 7. The timing and altitude tolerances are also described in Section 7.

This patched-conic method is the same as the one used in previous GTOC competitions.

#### Patched conics

For a full understanding of the patched-conic method and its relation to real trajectories, the reader is referred for example to Richard Battin’s textbook, “An Introduction to the Mathematics and Methods of Astrodynamics,” AIAA, Reston, Virginia, 1999. Here, we say only a few words in an attempt to make the concept a little less foreign, and provide some context for the mathematics and terminology of this Appendix.

For a real-world, planetary flyby trajectory, which a spacecraft could actually fly assuming perfect knowledge and perfect execution, there are three distinct conceptual parts: The trajectory before the flyby (where the spacecraft is predominantly affected by the sun), an almost hyperbolic flyby (where the spacecraft is predominantly affected by the planet and flies on an arc that is approximately part of a hyperbola with focus at the planet’s centre), and the trajectory after the flyby (where the spacecraft is again mostly affected by the sun). In the patched-conic approximation, the trajectory parts before and after the flyby are modelled as being continuous in position and passing through the centre of the planet, without sensing the gravity of the planet, but with a velocity discontinuity at the time when the spacecraft position matches the position of the planet’s centre. Given the patched-conic approximation for the pre- and post-flyby trajectories, the hyperbola which approximates the almost-hyperbolic part can be computed using the equations of this appendix. Specifically, what is termed the “flyby altitude” in the patched-conic method is the altitude computed based on this hyperbola; it has no relation to the fact that the pre- and post-flyby trajectories of the patched-conic approximation pass through the centre of the planet. The flyby periapsis vector is similarly based on this hyperbola. There is, of course, also a timing error in the patched-conic approximation, because in the real world the spacecraft velocity will be noticeably altered by the planet’s gravity (unless the flyby altitude is very high), an effect which is not modelled in the approximation. In practice, the position, velocity, and timing errors of the patched-conic approximation are frequently small enough to allow the method to be used for preliminary design.