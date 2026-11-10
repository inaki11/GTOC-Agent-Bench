# Complete GTOC11 solution format

An ASCII text file contains ten ship blocks, one ring row, one control-type
row, and zero or more asteroid-transfer blocks. Separate fields with spaces
or tabs. Use decimal integers for IDs and counts. Floating-point fields may
use decimal or scientific notation. LF and CRLF line endings are supported.
Do not include titles, comments, column headings, commas, NaN or infinity.

## Ten mother ship blocks

List IDs 1 through 10 in order, including empty unused slots:

```text
ship_ID  impulse_row_count  visited_asteroid_count
epoch_MJD  x  y  z  vx  vy  vz  dvx  dvy  dvz  target_ID
... exactly impulse_row_count + visited_asteroid_count event rows ...
```

The initial Earth departure is the first event, has `target_ID=-1`, and is
included in `impulse_row_count`. Its velocity is the **post-departure
heliocentric velocity (Earth velocity plus excess velocity)**;
`dvx,dvy,dvz` specify the departure excess velocity. A later
impulse row uses `target_ID=0`, the **incoming** velocity and the impulse to
apply after that row. A flyby row uses the catalogue asteroid ID and
`dvx=dvy=dvz=0`. Events are chronological; co-timed impulse/flyby events
must still represent the required incoming/outgoing states consistently.

An unused ship is represented by `ship_ID 0 0` without event rows. Do not omit
its header. The same asteroid ID must not appear in two flyby rows.

## Ring and thrust type

After ship 10, provide exactly these two rows:

```text
a_Dyson_AU  inclination_deg  RAAN_deg  first_station_phase_deg
continuous_thrust_type
```

The elements refer to **95739 MJD**, not the first delivery epoch. Type is:

- **0:** acceleration direction is fixed within each step; the original
  checker verifies each consecutive pair of states.
- **1:** direction changes continuously; the original checker interpolates
  acceleration components with its Lagrange scheme and verifies the complete
  asteroid trajectory. Its algorithm is retained unchanged.

## Asteroid-transfer blocks

Repeat until end of file:

```text
asteroid_ID  destination_station_ID  following_row_count
epoch_MJD  x  y  z  vx  vy  vz  ax  ay  az  mf
... exactly following_row_count state/control rows ...
```

Station ID is 1..12 and identifies phase `phi_1+30*(ID-1)` at 95739 MJD.
The asteroid must have a preceding mother-ship flyby in the fleet. It can have
only one transfer block. The first row starts ATD activation and the last row
is station rendezvous. Asteroid epochs must increase; the nominal step limit
is one day. Acceleration components are in **m/s^2**, unlike the km/s^2
units normally used with km Cartesian states.

`mf` is the remaining-mass column. **The organizer's accepted example repeats
the arrival mass in every row of a block**, and the checker validates arrival
mass against the original catalogue mass and total thrust duration. Do not
assume that the first `mf` is the initial catalogue mass. The score adapter
uses the final row of each transfer for delivered mass, after official
acceptance. No requirement to interpolate the printed mass column is added.

All declared rows must exist and every nonblank row must belong to a block.
Trailing whitespace is allowed. A partial file, unexplained extra row, wrong
column count, noninteger ID or nonfinite value is rejected before the native
checker. Limits are 100,000,000 bytes, 1,000,000 physical rows and 4096 bytes
per row. The submitted filename is arbitrary; the evaluator privately stages
it as `Solution.txt`.
