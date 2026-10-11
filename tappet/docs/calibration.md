# Tappet — Calibration

Calibration turns a pile of ±5° hobby servos and a hand-placed phone into ~1 mm taps. It has four
stages. Each stage is independent, so you only redo the one whose inputs changed.

| Stage | What it finds | Stored in | Redo when |
|---|---|---|---|
| 1. Servo trim | `s_zero` per joint (servo horn offset) | ESP32 NVS (`calib_set save:true`) | A servo or horn is replaced |
| 2. Geometry check | Confirms `arms.*` in `robot.json` match the print | ESP32 NVS (`geom_set`) | Arm parts reprinted or rebuilt |
| 3. Screen calibration (touch probe) | Homography `H_sw` from screen (u,v) to world (x,y), plus `surface_z` | Android app (`/api/calibration`) | Phone re-seated, new phone, shims changed |
| 4. Camera calibration | Homography `H_ic` from camera pixel to (u,v) | Android app | Head phone re-clamped, neck re-trimmed |

Why these numbers matter is explained in [kinematics.md §5](kinematics.md#5-xy-error-budget-target-2-mm):
uncalibrated, the tray/horn errors alone are several mm; after stages 1 and 3 the residual is
≈ 0.5 mm and the total RSS error is ≈ 1.0 mm.

## Tools you need

* The printed **trim gauges** (part of the printed parts set): an L-shaped 90° gauge for the
  shoulder and a 0°/−30° wedge for the elbow, plus a yaw pointer that snaps on the turret.
* The robot plugged into a USB-C PD charger, and either the Android head docked or a PC on the
  DevKit UART port (115200 baud).
* For stage 3, the target phone in the tray with the touchpad page open.

## 1. Servo trim (`s_zero`)

Each joint maps to its servo as `s = s_zero + dir · gear · (q − neutral)`. A spline horn can only
be seated in ~ 7–15° steps, so `s_zero` absorbs the remainder.

1. `enable`, then `home`. Disconnect the target phone (arms will move).
2. For each joint, command its **neutral** angle (`joints q:{L_SHOULDER:60}` etc.;
   neutral values are in `robot.json.joints`).
3. Fit the gauge. Jog the joint with `servo_raw` in 5 µs steps (≈ 0.45°) until the gauge
   seats with no light gap. Always finish the jog **in the same direction** (increasing µs). That
   is the same rule the motion planner uses, so trim and runtime see the same backlash side.
4. Convert: `s_zero = (us − 500) · 180 / 2000`. Write with
   `calib_set joints:{L_SHOULDER:{s_zero:…}} save:true`.
5. Repeat for all 8 joints. The neck joints use the column/bracket faces as references.

Acceptance: after `home`, every joint sits within 1 servo step of its gauge. Servo trim is done
with body-protocol commands, either over the DevKit UART port from a PC (`host/tappet/client.py`)
or through `POST /api/cmd` on the head. The remote UI does not have a trim page yet
([roadmap](roadmap.md)).

`reset_calib` restores the compiled defaults (all `s_zero = 90`) and erases NVS.

## 2. Geometry check

Print tolerance on the 100/110 mm links is normally < 0.3 mm, which falls inside the calibration
residual, so most builds skip this stage. To verify:

1. Place the printed **reach gauge** (a flat card with crosshairs at (±60, 120) and (±60, 200) mm
   in world coordinates, located by the base's front edge).
2. `move` each arm to each crosshair at z = 10 mm and look down the stylus.
3. If the tip misses consistently by > 1.5 mm in the radial direction, measure L1/L2 and update with
   `geom_set arms:{L:{L1:…}} save:true`.

## 3. Screen calibration (touch-probe, automatic)

The robot can't see exactly where the phone sits in the tray, but the phone can report where it was
touched. The probe uses that.

1. On the **target phone**, open `http://<head-ip>:8080/touchpad.html` and switch to full screen
   (Safari: Share → Add to Home Screen gives a full-screen web app; that is the most reliable
   option). The page shows a dot grid and reports every touch to the head
   (`/api/touchpad/event`, same LAN, no token).
2. In the remote UI open the **Calibrate** tab, pick the arm (Auto / L / R) and press **Run
   auto-calibration** (or `POST /api/calibrate/auto`).
3. The robot:
   1. starts from the default homography built from the tray geometry and device profile
      (`default_device` in `robot.json`, or a profile you select);
   2. taps a 3×3 grid at u, v ∈ {0.15, 0.5, 0.85}, a few mm inside the screen edges;
   3. matches each tap to the touch event the page reports (by time window);
   4. fits `H_sw` by least squares (normalized DLT) from the 9 commanded world points to the 9
      reported (u,v), and estimates `surface_z` by stepping the press depth down on the centre
      point until the first touch registers.
4. The result reports per-point residuals. **Accept if the RMS residual is ≤ 0.6 mm and the max is
   ≤ 1.2 mm.** Otherwise the probe repeats once with `n = 4` (16 points). If it still fails, the
   phone is probably loose in the tray. Add a shim and retry.

The same procedure runs for each arm with `{arm:"L"}` / `{arm:"R"}`. If the per-arm homographies
differ by more than 1 mm anywhere on the screen, the app stores both and uses the one for the
arm that performs each tap.

Manual fallback (no touchpad page, e.g. a non-browser target device): jog the stylus with
`/api/jog` onto the four screen corners and save. Expect ~1.5 mm accuracy.

## 4. Camera calibration (`H_ic`)

1. `neck` to `look_at_tray` (pan 0, tilt −50).
2. Remote UI **Calibrate** tab → *Camera screen corners*: grab a raw snapshot and tap the four
   screen corners in order TL, TR, BR, BL (as the screen reads), then **Save corners**.
3. The app fits `H_ic` (4-point homography). `GET /api/camera.jpg?rectified=1` should now show
   the target screen squared up. The agent's `look_at_device()` returns this image.

The camera homography only affects what the agent *sees*, never where it taps. A sloppy corner
click makes the agent's reasoning less precise but does not make taps less accurate.

## 5. Verifying: the tap test

`POST /api/taptest {n:100}` taps 100 random (u,v) points on the touchpad page and compares them
with the reported touches.

| Metric | Pass |
|---|---|
| Registered taps | ≥ 99 / 100 |
| Median error | ≤ 1.0 mm |
| 95th percentile error | ≤ 2.0 mm |
| Max error | ≤ 3.0 mm |

The host tool `host/tappet/taptest.py` writes a JSON + SVG report (error vectors over the screen).
A consistent arrow direction across the screen means the phone has shifted, so redo stage 3. A
radial pattern growing toward the far edge points to yaw backlash or trim, so redo stage 1 for that
yaw joint.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Taps don't register at all | Stylus not grounded; tip worn | Check ground lead continuity tip → GND (< 1 kΩ); replace tip |
| Registers only near the robot | `surface_z` too high | Re-run stage 3 (it re-probes z) |
| Double taps / drags | Press too deep or hold too long | Lower `press_mm` to 2.0; `hold_ms` 40–60 |
| Error grows over a session | Phone sliding in tray | Silicone pads, shims; re-run stage 3 |
| One arm worse than the other | Trim on that arm's yaw | Stage 1 for that yaw, then stage 3 for that arm |
