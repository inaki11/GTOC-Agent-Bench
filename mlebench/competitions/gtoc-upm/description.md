# The Problem of the 12th Global Trajectory Optimisation Competition
## Sustainable Asteroid Mining

Hexi Baoyin, Fanghua Jiang, Zhong Zhang, Nan Zhang, Xiang Guo, Di Wu, Xuan Xie, Jia Yang
*Laboratory of Astrodynamics, School of Aerospace Engineering, Tsinghua University*

Haiyang Li, Yuming Peng, Wei Wang
*Shanghai Institute of Satellite Engineering*

Released: 19 June 2023
\*

## 1. The Scenario

In about 2035, as commercially sustainable asteroid mining may be put into action\*, humanity will plan to mine asteroids and transport their resources back to the Earth. The asteroid mining technology will be greatly developed and integrated into Sustainable Asteroid Miners to automatically extract and refine valuable minerals. A successful mining to an asteroid requires two successive rendezvous to the asteroid by the same or two different Mining Ships propelled by low-thrust electric propulsion. The first rendezvous is to set up a miner and the second is to retrieve the collected mass. Each asteroid can be mined once at most in a submitted solution (i.e., each asteroid can be rendezvoused twice at most). The maximal collected mass mined from an asteroid is proportional to the duration of stay between two moments of rendezvous with this asteroid. The task in GTOC-12 is to transport as many asteroid resources back to the Earth as possible in 15 years.

## 2. The Problem

All mission events must take place between January 1, 2035, 00:00:00 TT (64328 MJD) and January 1, 2050, 00:00:00 TT (69807 MJD) for possible launches, asteroid rendezvous, and planets flybys. Each team can use a limited number of Mining Ships. All Mining Ships must be launched from the Earth, rendezvous with targets selected in the sixty thousand asteroids to set up a miner or to retrieve the collected mass, and fly by the Earth to unload the resources. They can be performed possible gravity assists (GAs) as needed at three terrestrial planets Venus, Earth, and Mars. Each Mining Ship is equipped with an electric propulsion thruster with a specific impulse $I_{sp} = 4000 \text{ s}$ and a maximum thrust magnitude $T_{max} = 0.6 \text{ N}$, while the direction of thrust is not constrained. The Mining Ship's mass changes continuously when the thruster is on, and it must be no smaller than the sum of its dry mass and collected mass throughout the mission. The collected mass unloaded on the Earth is counted in the merit function (see Section 3) and constrains the number of Mining Ships (see Section 4.e)

The candidate asteroids are listed in the file `GTOC12_Asteroids_Data.txt`. They are characterized by identification numbers (asteroid ID from 1 to 60,000), epochs (the mission's initial time 64328 MJD), and corresponding orbital elements (semi-major axis, eccentricity, inclination, longitude of ascending node, argument of perihelion, and mean anomaly).

## 3. Merit Function

The task of the sustainable asteroid mining in this competition is to maximize the total resource value defined by
$$ J = \sum_{i=1}^{60,000} M_i $$
where $M_i$, in kilograms, is the collected mass of the $i$-th asteroid, which is, of course, zero if the collected mass is not transported back to the Earth. 

## 4. Constraints

The scenario of the sustainable asteroid mining mission is set with the following constraints.
- All the mission events (including the launches, thrust on/off, rendezvous, planetary flybys, etc.) must take place between 64328 MJD and 69807 MJD.
- The position of each Mining Ship at the moment of launch, rendezvous, and planetary flybys must be equal to the position of the Earth, asteroid, and planet at the same time, respectively. The hyperbolic excess velocity of each Mining Ship relative to the Earth must be no larger than 6.0 km/s at the moment of launch and unloading resources. The velocity of each Mining Ship at the moment of rendezvous must be equal to the velocity of target asteroid.
- The time interval between two successive rendezvous of the same asteroid must be no less than 1 year: $t_{2i} - t_{1i} \ge 1 \text{ yr}$, where $t_{2i}$ and $t_{1i}$ denote the moments of the second and first rendezvous to the $i$-th asteroid, respectively. The collected mass $M_i$ on the $i$-th asteroid is constrained by $M_i \le k(t_{2i} - t_{1i})$ where the rate of mining resources is $k = 10.0 \text{ kg/yr}$.
- The initial mass of each Mining Ship is constrained by $m_0 = m_d + m_p + I m_s \le 3000 \text{ kg}$, where $m_d = 500 \text{ kg}$ is the constant dry mass, $m_p$ is the propellant mass, $I (\le 20)$ is the number of miners, and $m_s = 40 \text{ kg}$ is the mass of a miner. The final mass of each Mining Ship must be no smaller than the sum of the dry mass 500 kg and collected mass on the ship.
- The number of Mining Ships used in each solution, $N$, is constrained by
   $$ N \le \min \left[ 100, 2 \exp(\rho \bar{M}) \right], \text{ where } \rho = 0.004 \text{ kg}^{-1}, \text{ and } \bar{M} = \frac{1}{N} \sum_{i=1}^{60000} M_i \text{, in kilograms, is the} $$
   average collected mass transported back to the Earth per Mining Ship in this solution. Illustrative examples for the maximum number of Mining Ships are given in Table 1.

   **Table 1 Maximum number of Mining Ships as a function of the average collected mass**
   | $\bar{M}$ (kg) | 100 | 300 | 500 | 700 | 900 | 1000 |
   |----------------|-----|-----|-----|-----|-----|------|
   | $N_{max}$      | 2   | 6   | 14  | 32  | 73  | 100  |

- Gravity assists at the planets Venus, Earth, and Mars are allowed. The GA impulse model is given in Section 6.2. If the Mining Ship flies by the Earth with a hyperbolic excess velocity no larger than 6 km/s, all resources carried by this ship will be instantaneously unloaded on the Earth.
g. The distance from the Sun to each Mining Ship must be no less than 0.3 AU: $r(t) \ge 0.3 \text{ AU}$.
h. Tolerances in the position, velocity, and mass of each Mining Ship at all events are 1,000 km, 1.0 m/s, and 0.001 kg, respectively.

## 5. Appendices

### 5.1 Dynamic Model

In the Sun's central gravitational field, the motions of planets, asteroids, and un-propelled Mining Ships are all governed by
$$ \dot{\mathbf{r}} = \mathbf{v} $$
$$ \dot{\mathbf{v}} = -\frac{\mu}{r^3}\mathbf{r} $$
where $\mathbf{r}$ (with $r = ||\mathbf{r}||$) and $\mathbf{v}$ are the position and velocity in the J2000 heliocentric ecliptic frame, respectively, and $\mu$ is the gravitational parameter of the Sun.
The initial states of planets and asteroids are described by their initial mean anomaly $M_0$ along with their other five constant orbital elements: the semi-major axis $a$, eccentricity $e$, inclination $i$, longitude of ascending node $\Omega$, and argument of perihelion $\omega$. The mean anomaly at time $t$, $M_t$, is computed by
$$ M_t = M_0 + \sqrt{\frac{\mu}{a^3}}(t-t_0) $$
The orbital eccentric anomaly $E$ at time $t$ is obtained by solving the Kepler's equation:
$$ M_t = E - e \sin E $$
The orbital elements are related to the Cartesian position and velocity according to the expressions
$$ \mathbf{r} = r(\mathbf{P}\cos f + \mathbf{Q} \sin f) $$
$$ \mathbf{v} = \sqrt{\frac{\mu}{a(1-e^2)}} [-\mathbf{P}\sin f + \mathbf{Q}(e+\cos f)] $$
where $r = \frac{a(1-e^2)}{1+e\cos f}$, $\tan \frac{f}{2} = \sqrt{\frac{1+e}{1-e}} \tan \frac{E}{2}$, and $f$ is the true anomaly. The vectors $\mathbf{P}$ and $\mathbf{Q}$ are given by
$$ \mathbf{P} = \begin{pmatrix} \cos\omega\cos\Omega - \sin\omega\sin\Omega\cos i \\ \cos\omega\sin\Omega + \sin\omega\cos\Omega\cos i \\ \sin\omega\sin i \end{pmatrix} $$
$$ \mathbf{Q} = \begin{pmatrix} -\sin\omega\cos\Omega - \cos\omega\sin\Omega\cos i \\ -\sin\omega\sin\Omega + \cos\omega\cos\Omega\cos i \\ \cos\omega\sin i \end{pmatrix} $$
Every Mining Ship is propelled by the electric thrust, and its position, velocity, and mass change continuously when the thruster is on:
$$ \dot{\mathbf{r}} = \mathbf{v} $$
$$ \dot{\mathbf{v}} = -\frac{\mu}{r^3}\mathbf{r} + \frac{T}{m}\mathbf{\alpha} $$
$$ \dot{m} = -\frac{T}{I_{sp}g_0} $$
where $T$ ($0 \le T \le T_{max}$) is the magnitude of thrust, $\mathbf{\alpha}$ is the unit vector of thrust direction, $m$ is the instantaneous mass of the Mining Ship, $I_{sp}$ is the specific impulse, and $g_0$ is the gravitational acceleration at sea level. The Mining Ship mass changes instantaneously at the moment of rendezvous as it releases a miner or collects resources:
$$ m(t_{1i}^+) = m(t_{1i}^-) - m_s $$
$$ m(t_{2i}^+) = m(t_{2i}^-) + M_i $$
where $t_{1i}$ and $t_{2i}$ denote the moments of the first and second rendezvous with the $i$-th asteroid, respectively, $m_s$ is the mass of a miner, $M_i$ is the collected mass on the $i$-th asteroid, and $t^-$ and $t^+$ refer to the states immediately before and after the rendezvous, respectively.

### 5.2 GA impulse model

The GA impulse model (i.e., the same GA model used in the previous GTOCs) is used at the planetary flyby moment $t_g$ when the Mining Ship's positions at the moments immediately before and after GA, $\mathbf{r}(t_g^-)$ and $\mathbf{r}(t_g^+)$, respectively, are both equal to the planet's position $\mathbf{r}_p(t_g)$:
$$ \mathbf{r}(t_g^-) = \mathbf{r}(t_g^+) = \mathbf{r}_p(t_g) $$
The time spent inside the planetary influence is neglected. The hyperbolic excess velocity is calculated by the heliocentric velocity of the Mining Ship relative to that of the planet:
$$ \mathbf{v}_\infty(t_g^-) = \mathbf{v}(t_g^-) - \mathbf{v}_p(t_g) $$
$$ \mathbf{v}_\infty(t_g^+) = \mathbf{v}(t_g^+) - \mathbf{v}_p(t_g) $$
where $\mathbf{v}_p$ is the planet's velocity. The magnitude of hyperbolic excess velocity remains invariant, but its direction has an instantaneous change:
$$ ||\mathbf{v}_\infty(t_g^-)|| = ||\mathbf{v}_\infty(t_g^+)|| = v_\infty $$
$$ \mathbf{v}_\infty(t_g^-) \cdot \mathbf{v}_\infty(t_g^+) = v_\infty^2 \cos\theta $$
where the direction change angle $\theta$ is constrained by the minimum pericenter radius $r_{p,min}$
$$ \sin \frac{\theta}{2} = \frac{\mu_p/r_p}{v_\infty^2 + \mu_p/r_p}, \quad r_p \ge r_{p,min} $$
where $r_p$ is the pericenter radius and $\mu_p$ is the gravitational parameter of the planet.
The Mining Ship mass changes instantaneously at the moment of Earth flyby with a hyperbolic excess velocity no larger than 6.0 km/s:
$$ m(t_g^+) = m(t_g^-) - M $$
where $M$ is equal to the total collected mass carried by this ship.

### 5.3 Constants

The planets' parameters and orbital elements at the mission initial time $t = 64328$ MJD are given in Table 2. The values of some constant parameters are provided in Table 3.

**Table 2 The planets' parameters and orbital elements ($t = 64328$ MJD)**

|                                      | Venus                 | Earth                 | Mars                  |
|--------------------------------------|-----------------------|-----------------------|-----------------------|
| Gravitational parameter, km³/s²      | 3.24858592000e5       | 3.98600435436e5       | 4.28283752140e4       |
| Minimum pericenter radius, km        | 6351.0                | 6678.0                | 3689.0                |
| Semi-major axis, km                  | 1.08208010521e8       | 1.49579151285e8       | 2.27951663551e8       |
| Eccentricity                         | 6.72988099539e-3      | 1.65519129162e-2      | 9.33662184095e-2      |
| Inclination, deg                     | 3.39439096544         | 4.64389155500e-3      | 1.84693231241         |
| Longitude of ascending node, deg     | 7.65796397775e1       | 1.98956406477e2       | 4.94553142513e1       |
| Argument of perihelion, deg          | 5.51107191497e1       | 2.62960364700e2       | 2.86731029267e2       |
| Mean anomaly, deg                    | 1.11218416921e1       | 3.58039899470e2       | 2.38232037154e2       |

**Table 3 Values of some constant parameters**

| Parameter | Value             | Unit    |
|-----------|-------------------|---------|
| $\mu$     | 1.32712440018e11  | km³/s²  |
| $g_0$     | 9.80665           | m/s²    |
| AU        | 1.49597870691e8   | km      |
| Day       | 86400.0           | s       |
| Year      | 365.25            | day     |


## Submission format

The solution file contains all trajectories of Mining Ships. Each trajectory is expressed by its identification number (ship ID from 1 to $N$ ), event ID (from $^ { - 4 }$ to 60,000), epochs (t, MJD), and the histories of position (km), velocity $\left( \mathrm { k m } / \mathrm { s } \right)$ , thrust $( \mathrm { N } )$ , and mass $( \mathrm { k g } )$ . The vectors of position, velocity, and thrust should be projected onto the J2O00 heliocentric ecliptic Cartesian reference frame.The file format is summarized in Table 1 and described below. 

### Table 1 Solution File format

| Line num in file | Data |
|------------------|------|
| Line 1           | 1 &nbsp;&nbsp; 0 &nbsp;&nbsp; *t* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| Line 2           | 1 &nbsp;&nbsp; 0 &nbsp;&nbsp; *t* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| …                | … |
| Line *i*         | –1 &nbsp;&nbsp; *tᵢ* &nbsp;&nbsp; 0.0 &nbsp;&nbsp; 0.0 &nbsp;&nbsp; 0.0 |
| Line *i+1*       | –1 &nbsp;&nbsp; *tᵢ* &nbsp;&nbsp; Tₓ &nbsp;&nbsp; Tᵧ &nbsp;&nbsp; T_z |
| …                | … |
| Line *i+j+1*     | –1 &nbsp;&nbsp; *tᵢ+ⱼ* &nbsp;&nbsp; Tₓ &nbsp;&nbsp; Tᵧ &nbsp;&nbsp; T_z |
| Line *i+j+2*     | –1 &nbsp;&nbsp; *tᵢ+ⱼ* &nbsp;&nbsp; 0.0 &nbsp;&nbsp; 0.0 &nbsp;&nbsp; 0.0 |
| …                | … |
| Line *k*         | –2* &nbsp;&nbsp; *tₖ* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| Line *k+1*       | –2 &nbsp;&nbsp; *tₖ* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| …                | … |
| Line *p*         | 60000† &nbsp;&nbsp; *tₚ* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| Line *p+1*       | 60000 &nbsp;&nbsp; *tₚ* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| …                | … |
| Line *q*         | 2 &nbsp;&nbsp; 0 &nbsp;&nbsp; *t* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| Line *q+1*       | 2 &nbsp;&nbsp; 0 &nbsp;&nbsp; *t* &nbsp;&nbsp; rₓ &nbsp; rᵧ &nbsp; r_z &nbsp; vₓ &nbsp; vᵧ &nbsp; v_z &nbsp; m |
| …                | … |


The solution file must be an ASCI text file, and the format is defined in Data section of Table 1. Each line of the file has the following format: two integers followed by several floating point numbers.

The numbers must be separated by spaces.

The file can be divided into $N$ sections according to the ship ID (the first integer of each line increased from 1 to $N$ ). The first section and the beginning of the second section are shown in Table 1 as an example. Each section lists the events of launch from the Earth, burning arcs,rendezvous,and flybys, which are indicated by the event ID (the second integer of each line) in order of epochs (the first floating point number of each line). The second integer at the first line of each section is 0, indicating that this ship is leaving the Earth at its initial time.The second integer $^ { - 1 }$ represents burning arcs, $^ { - 2 }$ Venus flyby, $^ { - 3 }$ Earth flyby, $^ { - 4 }$ Mars flyby, and an asteroid ID represents rendezvous with this asteroid. Multiple lines with the same epochs are used to represents the events that occur at the same time. There are two types of formats for floating point numbers, depending on the events:

**a. Event $\mathbf { I D } = - \mathbf { 1 }$**

At the moment the thruster is switched either from shutdown to burning or vice versa, two lines must be listed with the same epochs but different thrusts. A burning arc is presented by starting with a line and ending with a line both with a zero thrust vector, and by evenly distributing lines with thrust components between the boundary lines. So, the second and penultimate lines have the same epochs with the first and last lines,respectively, but the thrust vectors are both nonzero. There is no need to provide data for coast arcs. The time interval between two successive lines in the burning arc has to be one day, and a partial-day increment from the antepenultimate line to the last two lines is permitted. Note that, when the solution is verified, the state and mass of each Mining Ship will be propagated by numerically integrating the differential equations of the state and the mass, of which the thrust will be approximated by a third Lagrange interpolating polynomial.

**b. Event $\mathbf { I D } = \mathbf { 0 } , - \mathbf { 2 } , - \mathbf { 3 } , - \mathbf { 4 }$ or asteroid ID**

The events of launches, rendezvous, and flybys are all represented by two lines consisting of the values of epochs, positions, velocities,and masses immediately before and after the events. The two lines corresponding to launch should be listed with the same epochs, positions, and masses,but with different velocities,of which the first one is the heliocentric velocity of the Earth,and the second one is the heliocentric velocity of the Mining Ship. The two lines corresponding to rendezvous should be listed with the same epochs, positions,and velocities, but with different masses as the ship unloads a miner or retrieves resources. The two lines corresponding to planetary flyby should be listed with the same epochs and positions, with the two heliocentric velocities of the Mining Ship satisfying the GA constraints,and with the mass difference for Earth flyby being equal to the mass of resources unloaded on the Earth. The errors in position, velocity, and mass will be evaluated by propagating the state and mass of the Mining Ship between two successive events (event $\mathrm { I D s } \ 0 , - 2 , - 3 , - 4$ or asteroid ID).


### First lines view of **GTOC12_Asteroids_Data.txt**

| ID | epoch (MJD) | a (AU)       | e           | i (deg)    | LAN (deg)   | argperi (deg) | M (deg)     |
|----|-------------|--------------|-------------|------------|-------------|---------------|-------------|
| 1  | 64328       | 3.073000e+00 | 1.177000e-01| 1.745000e+01 | 1.403000e+01 | 1.830000e+00  | 3.053207e+02 |
| 2  | 64328       | 3.193000e+00 | 2.341000e-01| 2.631000e+01 | 2.170900e+02 | 1.312800e+02  | 1.726297e+02 |
| 3  | 64328       | 3.142000e+00 | 5.460000e-02| 5.280000e+00 | 2.139500e+02 | 1.776000e+01  | 1.329012e+02 |

Format: The text file is a plain text table where columns are separated by spaces. The first row contains column headers, and subsequent rows contain numeric values, some in scientific notation. Each row corresponds to one record, with fields such as ID, epoch (MJD), semi-major axis (AU), eccentricity, inclination (deg), longitude of ascending node (deg), argument of periapsis (deg), and mean anomaly (deg).
