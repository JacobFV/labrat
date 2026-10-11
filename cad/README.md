# Servo pipette mount for the 3018 CNC

The model is `pipette_mount.py` (build123d). Every dimension is a parameter at the top of that file. To rebuild:

```
cd cad && uv run --python 3.12 --with build123d --with trimesh --with matplotlib --with numpy --with networkx --with lxml python pipette_mount.py
```

It writes `stl/*.stl`, `plate_pipette.3mf` (all 3 parts on one 220x220 plate) and `renders/*.png`. It also prints the cam table below.

## Parts (3, all print without supports, in the orientation they are exported)
| part | what | vol |
|---|---|---|
| `mount_body.stl` | Split clamp ring for the motor (ID 44.8, takes 2x M3) + arm + split pipette collar (ID 20.8, takes 1x M3 pinch bolt) + vertical servo plate. The plate has 2x M3 slots, 20 mm apart, for the alternative X-carriage mount. | 31 cm3 |
| `cam.stl` | Eccentric disc, R16, eccentricity 8.5, 6 mm thick. A pocket fits the stock SG90/MG90S single-arm horn. | 4.4 cm3 |
| `plunger_cap.stl` | Flat follower disc, D20 x 3, with an 8.4 mm recess that sits on the plunger button. | 0.9 cm3 |

For the 52 mm spindle collar instead of the 44.5 mm 775 body, set `MOTOR_D = 52`. Then also set `PIPETTE_X` to about 49.

## Print settings (Ender 3 V3 SE)
- 0.24 mm layers, PLA or PETG, 3 walls, 15% gyroid infill, no supports, no brim.
- The parts total about 37 cm3 of solid volume, which is roughly 22-25 g as sliced.
- Estimated time is about 40-45 min for the whole plate at the stock "fast" profile. The mount body takes about 35 min of that, and the cam and cap about 8 min. **Check this in the slicer.** If it runs over budget, use 2 walls and 10% infill, or set `RING_WALL=4`.

## Hardware
- 3x M3x16 bolts with nuts: 2 for the motor ring, 1 for the pipette pinch.
- 2x M3x10 plus nuts or washers, only if you use the X-carriage slots.
- 2x servo tab screws (from the servo bag).
- 2x tiny horn screws (from the servo bag) to fix the horn to the cam. The center horn screw goes through the 6 mm access hole in the cam.

## Assembly
1. Push the servo through the plate cutout from the **front** (the cam side, +Y), so the body ends up behind the plate. Screw the tabs on. The shaft goes on the end nearest the pipette, so it sits directly above the pipette axis.
2. Power the servo and send `rest_us` (667 us = servo 15 deg). Screw the single-arm horn into the cam pocket, pocket side toward the servo. Press the horn onto the spline so the **eccentric (big side of the disc) points straight UP**. Fit the horn screw.
3. Slide the pipette up through the collar until its body top is **4 mm above the collar top**. Tighten the pinch bolt.
4. Set the plunger cap on the button, recess down. A dab of hot glue or tape holds it in place. At rest there is about 0.5 mm of gap between the cam and the cap.
5. Clamp the ring around the motor body, arm pointing toward the machine **front** (away from the carriage plate, so it clears the spindle holder block). Leave the ring about 20 mm or more above the holder so the collet and bit area stays clear. Tighten the 2x M3.
   - **Alternative mount:** cut or snap off the ring. Bolt the flat back of the servo plate to the X-carriage plate (or to an angle bracket on it) through the 2x M3 slots, which are 20 mm apart and allow 10 mm of vertical adjustment.
6. Sweep the servo slowly from rest to blowout, then find the first stop by feel (see the table below).

## Offsets (software must add these)
Local frame: origin is the spindle axis at the **bottom face of the clamp ring**. +X points along the arm.
- **Pipette axis XY offset: 45.0 mm from the spindle axis, along the arm direction.** If the arm points to the machine front (-Y on most 3018s), use `tip_offset = (dx, dy) = (0, -45)` from the spindle XY. Measure the real direction on the machine and fix the sign.
- **Z:**
  - The pipette body top is +20 mm above the ring bottom.
  - The body bottom (tip cone start) is -75 mm.
  - The tip end is about -125 mm with a 50 mm disposable tip (`TIP_LEN`).
  - The tip end relative to the spindle bit/collet depends on where you clamp the ring. **Calibrate by jogging the tip to touch the bed and recording Z.** Do the same with the bit.
- Servo shaft height is Z = 44.8. The plate top is at Z = 56.9.

## Cam angle to plunger travel
The flat follower makes this exact: `travel = 8.5*(1-cos(theta)) - 0.5` (the 0.5 is the rest gap). Servo angle = theta + 15 deg. The pulse is `us = 500 + servo_deg/180*2000`. For a mirrored install, use `us = 2500 - ...` instead.

| cam deg | servo deg | us | plunger travel mm |
|---|---|---|---|
| 0 | 15 | 667 | 0.0 (**rest_us**) |
| 30 | 45 | 1000 | 0.6 |
| 60 | 75 | 1333 | 3.8 |
| 90 | 105 | 1667 | 8.0 |
| 110 | 125 | 1889 | 10.9 |
| **118** | 133 | **1979** | **12.0 (first_stop_us)** |
| 130 | 145 | 2111 | 13.5 |
| **145** | 160 | **2283** | **15.0 (blowout_us)** |
| 150 | 165 | 2333 | 15.4 |
| 165 | 180 | 2500 | 16.2 (hard limit, cam nearly touches body top) |

Starting values: `rest_us=667`, `first_stop_us=1979`, `blowout_us=2283`. Calibrate first_stop by feel; it is a soft detent on real pipettes. Do not command a cam angle past 150 deg.

Force: the lever arm is at most 8.5 mm. An SG90 (1.8 kgcm) gives about 20 N and an MG90S (2.2 kgcm) about 25 N. Use the **MG90S**. The blowout spring may stall an SG90.

## Known limitations
- The cap recess is only 1.2 mm deep. With the default 15 mm protrusion and 15 mm stroke, the cap meets the body top at blowout, so there is no margin. If your pipette's button sticks out less than its stroke plus 1 mm, lower `STROKE_TOTAL` or sand the cap thinner.
- The renders use matplotlib painter's sorting, so the servo plate sometimes draws in front of the cam. The geometry itself was checked numerically.

## FALLBACK (no time to print)
1. **Pipette to spindle.** Lay the pipette alongside the motor body, tip down. Put a 15-20 mm spacer between them: a wooden block, a stack of popsicle sticks or a bottle cap, so the pipette clears the spindle holder. Fix it with 3-4 heavy zip ties around motor + spacer + pipette. Hot glue the spacer to both. Keep the plunger button free and pointing up.
2. **Servo.** Hot glue the servo body to the top of the spacer or motor with the shaft horizontal. The shaft should sit about 8-10 mm above the plunger button top and directly over the pipette axis. Add a zip tie through the servo tabs around the motor.
3. **Actuator.** Use the stock **single-arm horn** as the pusher. At rest, point it horizontal toward the pipette, with its tip resting just above the button. Rotating it about 90 deg pushes the button down by about horn length x sin(angle). The ~16 mm horn gives about 16 mm max, but the force tilts sideways near the end. Hot glue a coin or flat washer on the button so the horn tip can't slip off.
4. **Calibration.** Find rest/first_stop/blowout by stepping `us` in 20 us increments and watching the plunger. Expect roughly rest 1500, first stop about 1500 + 600, blowout about 1500 + 800, but the direction depends on which side you glued the servo. Record the measured XY offset with a ruler, tip to spindle axis.
