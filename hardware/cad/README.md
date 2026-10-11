# Tappet — mechanical CAD (build123d)

> **Just want to print it?** Use the six ready-made plates in [`plates/`](plates/README.md): every part count is included, laid out for a 220 × 220 bed (Ender 3 V3 SE), with one Cura profile per plate.

Parametric, fully scripted CAD for the Tappet desktop robot: a Luxo-lamp-style column carrying the
Android "head" on a pan/tilt neck, two 3-DOF parallel-linkage arms with compliant capacitive styluses,
and a fixed tray for the target phone. All kinematic geometry is read from
[`config/robot.json`](../../config/robot.json) (SPEC §3). Nothing kinematic is hard-coded here.

![assembly](renders/assembly_iso.png)

| | |
|---|---|
| ![front](renders/assembly_front.png) | ![side](renders/assembly_side.png) |
| ![top](renders/assembly_top.png) | ![exploded](renders/assembly_exploded.png) |

## Regenerate

```sh
cd hardware/cad
uv run --python 3.12 --with build123d --with trimesh --with matplotlib --with numpy python build.py
#   --no-render         skip PNGs (about 40 s faster)
#   --no-interference   skip the boolean interference checks
#   --no-export         checks only, do not write STL/STEP
```

Outputs:

* `stl/<part>.stl`, `step/<part>.step`: every printable part, **already in print orientation** (on the bed
  at z = 0, centred). Handed parts are exported as `_L` / `_R`.
* `step/assembly.step`: full robot at the **home pose** in the world frame (labelled, coloured
  components, including servo, horn, bearing, stylus-tip and phone mock-ups). `step/assembly_exploded.step`
  is the exploded variant.
* `renders/*.png`: assembly (iso, rear 3/4, front, side, top, exploded, left-arm close-up) and one
  render per printable part.
* `build_report.json`: raw results of every check. The tables below are regenerated into this README.

The process exits non-zero if any check fails.

## Layout and design

World frame (robot.json `frames.world`): origin on the desk under the neck column, +y toward the
user and the tray, +z up, +x the robot's right.

| subsystem | geometry (from robot.json unless noted) |
|---|---|
| Base | 210 × 120 × 30 mm tub (deck top z = 30) with an inset bottom lid. Two 608 bearings at the arm bases (±85, 40); SG90 yaw servos hang under the deck, 25 mm inboard |
| Yaw drive | 20T (SG90 spline) → 30T (turret) module-1 involute spur gears = **1.5 : 1** (`joints.*_YAW.gear`). Centre distance 25 mm. The pinion phase is meshed in the assembly |
| Turret | Bolted to the 30T gear (3× M3). The 8 mm gear axle runs in the 608 and is retained by an M3 screw and a fender washer from below. Cheeks hold the shoulder MG90S (−v side) and elbow MG90S (+v side) **coaxially on the shoulder axis** at r0 = 12, H = 70 |
| Arm | Upper arm L1 = 100 is keyed to the shoulder horn. A 25 mm elbow crank is keyed to the elbow horn, and a linkage rod (length L1) runs to the forearm's 25 mm rear arm. Together they form a parallelogram, so the forearm angle is **absolute θ2** (MeArm/uArm style). Forearm L2 = 110 |
| Wrist levelling | Two chained parallelograms. Turret ground post → `wrist_link_a` (L1) → `elbow_triangle` (free on the elbow pin) → `wrist_link_b` (L2) → `stylus_holder`. The holder keeps its turret-relative orientation, so the **stylus stays vertical** |
| Stylus | Sleeve axis at tool_dx = 15 ahead of the wrist pin. The tip point is tool_dz = 35 below the wrist pin. The plunger has 6 mm travel (`tool.stylus_travel`) set by an M2 screw in a slot, with a light spring above. The 6 mm conductive tip has a 1.6 mm ground-lead channel through the plunger and the cap |
| Neck | Pan SG90 in the column head housing, shaft on the pan axis (0, −10). The column top is at 210 (`neck.column_top_z`). The tilt SG90 sits in the pan mount with its shaft on the tilt axis z = 232 (`neck.tilt_axis_z`). The tilt bracket is a U-yoke (horn + M3 idler) |
| Head clamp | Spring-loaded jaw on a dovetail rail. Phones 64–82 mm wide and 7–11 mm thick, held landscape with the screen facing +y |
| Tray | Pocket 80 × 165 centred at (0, 160), floor z = 6 (`tray.*`). Two dovetail tongues drop into sockets in the base front and are locked by M3 screws from the deck, so the pocket is fixed in the world frame. Default iPhone 6.1" surface = 6 + 7.8 = **13.8** = `default_device.surface_z` |

Lateral (v) layout of one arm, from the shoulder servo side (L arm; R is the mirror image):

| v (mm) | member |
|---|---|
| −47 … −14.5 | shoulder MG90S (body, tabs on the turret cheek at −28.6), horn face at −14.5 |
| −16 … −11 | upper arm (horn pocket 1.7 deep) |
| −10 … −6 | elbow triangle / turret ground post |
| −5 … −1 | wrist_link_a, wrist_link_b |
| 0 … 4 | forearm |
| 5 … 10 | linkage rod / stylus-holder plate (stylus axis itself at v = 0, as FK requires) |
| 11 … 16 | elbow drive crank |
| 14.5 … 47 | elbow MG90S |

There is a 1 mm gap between planes. Fill each gap with a 0.5–1 mm M3 nylon washer at every pin.

### Notable decisions

* **Passive links are solved, not placed.** `assembly.py` closes every parallelogram by circle
  intersection from the pin distances. A wrong hole spacing would therefore show up as a stylus
  position or tilt error.
* The R arm is the exact mirror of the L arm about the world YZ plane, with ψ_L = −ψ_R. This
  reproduces SPEC FK for base_x = +85 and matches the `dir: -1` on R_SHOULDER and R_ELBOW. Only the
  turret and the stylus holder are chiral (`_L` / `_R`). Every flat link is the same part for both arms
  (flip it over).
* The 30T gear is a separate part so that both the turret and the gear print support-free with crisp teeth.
* The yaw bearing housing supports the 608 outer race on a lip, and the turret gear rides on the
  inner race through a 1 mm shoulder ring. The gear face clears the deck by 1 mm.
* The tray shims register the phone **centred in the pocket**, which is what the default screen
  homography assumes. Walls thinner than 0.8 mm are left out. The 16 Pro Max (77.6 × 163) fits the bare
  pocket. For the iPhone SE (7.3 mm), set `surface_z` = 13.3 in the device profile.
* Cable routing: each turret has two servo-lead pass-throughs, the deck has a slot behind each turret,
  and the column is hollow from the pan servo pocket down into the base. The tray has cable notches
  on both sides at the port end.
* Electronics in the lid. Positions are kept in `tappet_cad/parts/base.py` and are read by
  `hardware/electronics/gen_drawings.py`.
  * The ESP32-S3-DevKitC-1 stands on its pin headers in a cradle. Its **UART** port is exposed as **PC**
    at x = −16. Its native USB port is deliberately *not* exposed, because GPIO19/20 go to the HEAD board.
  * **HEAD** USB-C cut-out at x = −80 for J2 on the 30 × 20 mm HEAD board (cradle at x −95…−65,
    y −47…−27). The board's rear-left corner needs a ~2 mm 45° clip to clear the base's inner corner
    radius. The cradle pads are already trimmed to suit.
  * PB power board, 40 × 17 mm, in a cradle at x −7…33, y −47…−30.
  * CH224K PD trigger cradle, with **POWER IN** at x = +40.
  * PCA9685 on standoffs.
  * **Buck A XL4015** (≈51 × 26 mm, servo rail) in a cradle at (72, −1).
  * **Buck B Mini560** (22 × 17 mm, logic) in a cradle at (62, −36).
  * The buck cradles work with any hole pattern: corner rest pads, L-locators and a zip-tie slot pair.
    The lid is vented under both bucks, and the rear and side walls are vented.
  * The rear-left lid screw boss is at (−93, −18), out of the HEAD board's way.

## Parts, mass and print settings

Defaults are PLA, 0.4 mm nozzle, 0.2 mm layers unless the table says otherwise. Use PETG for the cable clips. Mass = solid volume
× 1.24 g/cm³ × 0.30 effective fill. This is a rough estimate, and thin links come out heavier in practice.

<!-- PARTS_TABLE_START -->
| part (STL) | qty | material | bbox as printed (mm) | volume cm3 | est. mass g (each) | orientation / settings |
|---|---|---|---|---|---|---|
| `base` | 1 | PLA | 210 x 120 x 30 | 135.86 | 50.5 | 0.2 mm, 3 walls, 15% infill. Print upside down (deck on the bed). Supports only under the 3 rear USB-C cut-outs (or rely on 7 mm bridges). 3 walls, 15% infill. |
| `base_lid` | 1 | PLA | 204.2 x 114.2 x 17.6 | 66.54 | 24.8 | 0.2 mm, 3 walls, 15% infill. Print flat, standoffs up. 3 walls, 15% infill. |
| `column` | 1 | PLA | 46 x 54.5 x 180 | 66.21 | 24.6 | 0.2 mm, 3 walls, 20% infill. Print upright on the foot. 3 walls, 20% infill; support only under the knuckle caps (or none - 45 deg chamfered). Brim recommended. |
| `neck_pan_mount` | 1 | PLA | 39.6 x 36 x 32.5 | 9.74 | 3.6 | 0.2 mm, 4 walls, 40% infill. Print plate-down. Servo window bridges ~13 mm. 4 walls, 40% infill. |
| `neck_tilt_bracket` | 1 | PLA | 32 x 34 x 26.5 | 6.67 | 2.5 | 0.2 mm, 4 walls, 40% infill. Print on the bridge front face (cheeks standing up, no supports). 4 walls, 40% infill. |
| `phone_clamp` | 1 | PLA | 72 x 106.5 x 20 | 38.95 | 14.5 | 0.2 mm, 4 walls, 30% infill. Print back-plate down (rail, ribs, jaws up; no supports). 4 walls, 30% infill. |
| `phone_clamp_jaw` | 1 | PLA | 26 x 15.7 x 12 | 3.17 | 1.2 | 0.2 mm, 3 walls, 100% infill. Print upside down (top face on the bed, lip up). 100% infill. |
| `turret_L` | 1 | PLA | 47.5 x 57.2 x 45.5 | 21.02 | 7.8 | 0.2 mm, 4 walls, 30% infill. Print platform-down, no supports (servo windows bridge 12 mm). 4 walls, 30% gyroid. Export turret_L and turret_R (mirror). |
| `turret_R` | 1 | PLA | 47.5 x 57.2 x 45.5 | 21.02 | 7.8 | 0.2 mm, 4 walls, 30% infill. Print platform-down, no supports (servo windows bridge 12 mm). 4 walls, 30% gyroid. Export turret_L and turret_R (mirror). |
| `yaw_gear_servo` | 2 | PLA | 22 x 22 x 5.5 | 1.6 | 0.6 | 0.12 mm, 3 walls, 100% infill. Print spline-bore down, 0.12 mm layers, 100% infill. Bore is a light press on the SG90 spline. |
| `yaw_gear_turret` | 2 | PLA | 32 x 31.9 x 15.1 | 5.18 | 1.9 | 0.12 mm, 3 walls, 100% infill. Print upside down (hub face on bed, axle up). 0.12 mm layers, 100% infill for crisp teeth. |
| `upper_arm` | 2 | PLA | 116 x 18 x 5 | 5.85 | 2.2 | 0.2 mm, 4 walls, 40% infill. Print flat (horn pocket face up). 4 walls, 40% infill for stiffness. Same part for L and R (flip over). |
| `forearm` | 2 | PLA | 147 x 15 x 4 | 5.08 | 1.9 | 0.2 mm, 4 walls, 40% infill. Print flat. 4 walls, 40% infill. Same part for L and R. |
| `elbow_drive_link` | 2 | PLA | 39.5 x 18 x 5 | 2.11 | 0.8 | 0.2 mm, 3 walls, 100% infill. Print flat, horn pocket up. 100% infill (small, torque-carrying). |
| `linkage_rod` | 2 | PLA | 110 x 10 x 5 | 3.39 | 1.3 | 0.2 mm, 4 walls, 30% infill. Print flat. 4 walls. |
| `wrist_link_a` | 2 | PLA | 109.2 x 9.2 x 4 | 3.03 | 1.1 | 0.2 mm, 4 walls, 30% infill. Print flat. 4 walls. |
| `wrist_link_b` | 2 | PLA | 119.2 x 9.2 x 4 | 3.31 | 1.2 | 0.2 mm, 4 walls, 30% infill. Print flat. 4 walls. |
| `elbow_triangle` | 2 | PLA | 47 x 26.7 x 4 | 2.63 | 1.0 | 0.2 mm, 3 walls, 100% infill. Print flat. 100% infill. |
| `stylus_holder_L` | 1 | PLA | 27.8 x 16.8 x 46.7 | 5.43 | 2.0 | 0.2 mm, 4 walls, 60% infill. Print upright on the sleeve flare (round bore), tree supports under the pin plate only; ream bore with a 7 mm drill. L and R are mirror images (export _L/_R). |
| `stylus_holder_R` | 1 | PLA | 27.8 x 16.8 x 46.7 | 5.43 | 2.0 | 0.2 mm, 4 walls, 60% infill. Print upright on the sleeve flare (round bore), tree supports under the pin plate only; ream bore with a 7 mm drill. L and R are mirror images (export _L/_R). |
| `stylus_plunger` | 2 | PLA | 6.6 x 6.6 x 24.5 | 0.66 | 0.2 | 0.12 mm, 3 walls, 100% infill. Print upright, 0.12 mm layers, 100% infill; sand lightly to slide freely. |
| `tray` | 1 | PLA | 100 x 187.8 x 12 | 103.07 | 38.3 | 0.2 mm, 3 walls, 20% infill. Print flat (bottom on the bed). 3 walls, 20% infill. 100 x 180 mm footprint. |
| `tray_shim_iphone_61` | 1 | PLA | 79.7 x 164.7 x 5 | 10.81 | 4.0 | 0.2 mm, 3 walls, 100% infill. For iPhone 15 / 15 Pro / 16 / 16 Pro (6.1in). Print flat, 100% infill (thin walls). |
| `tray_shim_iphone_67` | 1 | PLA | 79.7 x 164.7 x 5 | 2.97 | 1.1 | 0.2 mm, 3 walls, 100% infill. For iPhone 15/16 Plus, 15 Pro Max (6.7in); 16 Pro Max needs no shim. Print flat, 100% infill (thin walls). |
| `tray_shim_iphone_se` | 1 | PLA | 79.7 x 164.7 x 5 | 15.69 | 5.8 | 0.2 mm, 3 walls, 100% infill. For iPhone SE 2nd/3rd gen. Print flat, 100% infill (thin walls). |
| `cable_clip` | 12 | PETG | 16.4 x 16.2 x 6 | 0.5 | 0.2 | 0.2 mm, 3 walls, 100% infill. Print on its side (C profile flat on the bed) in PETG for springiness, 100% infill. |

**Total printed mass (all quantities): ~217 g PLA**  
**Rough total print time: ~22 h** (0.2 mm layers on a typical 0.4 mm-nozzle bed-slinger; ~11 g/h effective + plate changes; roughly halve on a CoreXY at 'fast' profiles).
<!-- PARTS_TABLE_END -->

## Hardware (non-printed)

| item | qty | used for |
|---|---|---|
| MG90S metal-gear micro servo | 4 | shoulder + elbow, both arms |
| SG90 micro servo | 4 | 2× arm yaw, neck pan, neck tilt |
| 608-2RS bearing 8×22×7 | 2 | turret yaw axles |
| M3 × 25 socket head + nylock | 2 | elbow pins (upper arm / triangle / forearm) |
| M3 × 20 socket head + nylock | 2 | holder-to-wrist_link_b pins |
| M3 × 16 socket head + nylock | 12 | remaining arm pins (6 per arm), tilt idler + 1 spare |
| M3 nylon washers 0.5–1 mm | ~40 | spacers in the 1 mm inter-plane gaps |
| M3 × 10 socket head | 6 | 30T gear → turret (3 per turret) |
| M3 × 10 + Ø12 fender washer | 2 | yaw axle retainers under the bearings |
| M3 × 10 countersunk | 4 | lid → base bosses |
| M3 × 12 + nut | 4 | column foot → deck |
| M3 × 25 | 2 | tray dovetail locks (from deck top into tongue) |
| M3 × 10 + nut | 2 | phone clamp → tilt bracket |
| M2 × 8 self-tapping | 16 | servo tabs (usually supplied with the servos) |
| M2 × 4 | 2 | stylus plunger retaining screws |
| compression spring Ø5 × 12 mm, 0.3 mm wire | 2 | stylus plunger (light, ~0.3 N/mm) |
| compression spring Ø6 × 32 mm, 0.5 mm wire | 1 | head clamp jaw |
| 6 mm conductive stylus tip (replacement tip, ~2.5 mm stem) | 2 | tool tips |
| 30 AWG silicone wire | 2 × 40 cm | stylus ground leads (to the body GND) |
| Ø10 × 1 mm silicone anti-slip pads | 8 | lid (4) + tray (4), recessed flush |

The electronics (ESP32-S3-DevKitC-1, PCA9685, 2 bucks, CH224K, USB-C) are covered in `hardware/electronics/`.

## Checks (`tappet_cad/checks.py`)

1. **Kinematics vs SPEC §3.** For each pose, the arm is assembled through the real linkage. The driven
   links are posed from (ψ, θ1, θ2), and the passive links are closed by circle intersection. The
   assembled pose then places the stylus-tip solid. The tool point is measured from the placed
   **CAD geometry**: the lowest point of the tip dome, on its axis, taken from the solid's bounding box.
   It is compared with the SPEC FK formula. The check fails if the error exceeds 1 mm, or if the stylus
   tilts by more than 0.05°.
   * Poses: home, park, and 4 IK vectors per arm from `config/kinematics_vectors.json`. These include
     taps on the screen at z = 13.8.
2. **Pin joints.** The M3 pin-hole centres are extracted from the edges of every placed link. The check
   verifies that all parts meeting at each joint (elbow, wrist, crank pin, forearm rear pin, ground
   pin, triangle pins, holder pin) are concentric within 0.02 mm. It also checks that θ1 − θ2 is
   inside `linkage.rel_min..rel_max`.
3. **Printability.** Every exported part must be a valid single solid that fits a 220 × 220 × 250 bed in
   its print orientation.
4. **Interference.** Every pair of assembly components whose bounding boxes overlap is boolean-intersected.
   Any intersection over 0.5 mm³ is a failure. The components are: base, lid, column, neck, clamp, phones,
   tray, shim, both arms, servos, horns, bearings and gears (the gear teeth are phase-meshed). This runs
   at the home pose, and again with each arm tapping the tray centre or a far corner while the other arm
   is parked.

<!-- CHECKS_START -->
- Kinematics (CAD tip vs SPEC FK, tol 1 mm): **PASS**
- Pin-joint coincidence (hole centres of mating links, tol 0.02 mm): **PASS**
- Printability (valid single solid, fits 220x220x250): **PASS**
- Home-pose interference (99 overlapping-bbox pairs boolean-tested): **PASS**
- Tap-pose interference (each arm tapping tray centre / corners, other arm parked; 412 pairs): **PASS**

| pose | arm | q (yaw, th1, th2) | FK (SPEC §3) | CAD tip | err mm | |
|---|---|---|---|---|---|---|
| home | L | -35.00, 100.00, -20.00 | -20.18, 132.56, 95.86 | -20.18, 132.56, 95.86 | 0.0000 | PASS |
| home | R | 35.00, 100.00, -20.00 | 20.18, 132.56, 95.86 | 20.18, 132.56, 95.86 | 0.0000 | PASS |
| park | L | 20.00, 110.00, -10.00 | -119.59, 135.03, 109.87 | -119.59, 135.03, 109.87 | 0.0000 | PASS |
| park | R | -20.00, 110.00, -10.00 | 119.59, 135.03, 109.87 | 119.59, 135.03, 109.87 | 0.0000 | PASS |
| vector0_L(0, 160, 13.8) | L | -35.31, 48.42, -60.78 | -0.00, 160.00, 13.80 | -0.00, 160.00, 13.80 | 0.0000 | PASS |
| vector1_L(-30, 95, 13.8) | L | -45.00, 62.50, -87.60 | -30.00, 95.00, 13.80 | -30.00, 95.00, 13.80 | 0.0000 | PASS |
| vector2_L(30, 225, 13.8) | L | -31.87, 18.79, -29.05 | 30.00, 225.00, 13.80 | 30.00, 225.00, 13.80 | 0.0000 | PASS |
| vector3_L(-30, 225, 13.8) | L | -16.56, 32.01, -42.42 | -30.00, 225.00, 13.80 | -30.00, 225.00, 13.80 | 0.0000 | PASS |
| vector4_R(0, 160, 13.8) | R | 35.31, 48.42, -60.78 | 0.00, 160.00, 13.80 | 0.00, 160.00, 13.80 | 0.0000 | PASS |
| vector5_R(-30, 95, 13.8) | R | 64.44, 53.81, -67.88 | -30.00, 95.00, 13.80 | -30.00, 95.00, 13.80 | 0.0000 | PASS |
| vector6_R(30, 225, 13.8) | R | 16.56, 32.01, -42.42 | 30.00, 225.00, 13.80 | 30.00, 225.00, 13.80 | 0.0000 | PASS |
| vector7_R(-30, 225, 13.8) | R | 31.87, 18.79, -29.05 | -30.00, 225.00, 13.80 | -30.00, 225.00, 13.80 | 0.0000 | PASS |
<!-- CHECKS_END -->

## Source layout

```
build.py                       entry point (export, assembly, checks, renders, README tables)
tappet_cad/params.py           reads ../../config/robot.json; tolerances (0.2 mm), M2/M3, layout
tappet_cad/lib/                servos (SG90/MG90S + horns + pockets), gears (involute), bearing (608),
                               fasteners, phones (mock-ups), util (solids + 4x4 transforms)
tappet_cad/parts/              one module per part (model frame = assembly frame)
tappet_cad/assembly.py         pose solver + home / exploded assemblies
tappet_cad/checks.py           kinematic, joint, printability, interference checks
tappet_cad/render.py           matplotlib (Agg) Poly3DCollection renderer
```
