# Tappet — Arm Kinematics, Workspace and Accuracy

This document explains the arm geometry, shows that the default workspace covers the target screen,
and derives the tap accuracy budget against the **2 mm** requirement (an iPhone's smallest common
hit target is ~44 pt ≈ 7 mm; 2 mm leaves margin for keyboard keys, which are ~5.5 mm wide).

The equations themselves are normative in [SPEC.md §3](SPEC.md#3-arm-kinematics-identical-in-firmware-c-python-kotlin);
all numbers come from [`config/robot.json`](../config/robot.json). Implementations:
`firmware/` (C++), `host/tappet/kinematics.py` (Python), `android/` (Kotlin). All three are checked
against `config/kinematics_vectors.json`.

## 1. Geometry

```
 side view (one arm, radial plane)                 top view
                                                         +y (toward user / tray)
          elbow                                          ▲
           ●──────── L2 = 110 ────────● wrist             │      ┌──────────┐
          /  θ2 (absolute)            │                   │      │  target  │  tray centre (0,160)
   L1=100/                            │ tool_dz = 35      │      │  phone   │
        /  θ1                         │                   │      └──────────┘
 ──────● shoulder (H = 70, r0 = 12)   ▼ stylus tip        │   L ●           ● R   bases (∓85, 40)
       ║                         ←tool_dx = 15→           │          ◉ neck (0,-10)
  yaw turret ψ (30T, 1.5:1)                               └──────────────────► +x
```

| Parameter | Value | Note |
|---|---|---|
| Base L / R | (−85, 40) / (85, 40) mm | yaw axis on desk |
| Shoulder height `H` | 70 mm | |
| Radial offset `r0` | 12 mm | shoulder pivot ahead of yaw axis |
| `L1` upper arm | 100 mm | |
| `L2` forearm | 110 mm | |
| Tool offset `tool_dx`, `tool_dz` | 15, 35 mm | wrist pivot → stylus tip |
| Linkage limit | 20° ≤ θ1−θ2 ≤ 160° | parallel-linkage interference |
| Default screen surface | z = 13.8 mm | tray floor 6 + iPhone 15 thickness 7.8 |

The elbow servo sits at the shoulder and drives the forearm through a parallel (MeArm-style) link,
so θ2 is an **absolute** angle. That keeps both heavy servos at the base, which cuts the moving mass
and decouples the two joints: moving the shoulder does not change the forearm angle. A second
parallelogram at the wrist keeps the stylus vertical in all poses, so `tool_dx/dz` are constant.

## 2. Workspace coverage

Check (left arm, the same holds for the right by symmetry): a 21×21 grid over the whole default
screen (65.1 × 141.1 mm centred on (0, 160)), at three heights per point: surface
(z = 13.8), hover (+8 mm, `approach_mm`) and press (−2.5 mm, `press_mm`).

| Result | Value |
|---|---|
| Poses checked | 1,323 |
| Unreachable | 0 |
| Joint-limit or linkage violations | 0 |
| Yaw range used over the screen | −67.2° … −15.4° (limits −93° … 23°) |

**Either arm alone can reach the entire default screen.** `tap arm:"auto"` normally picks the arm
on the same side as the target. Being able to fall back to the other arm matters when one arm is
parked or being recalibrated.

Representative poses (left arm, at the screen surface):

| Point (x, y) mm | Horizontal reach from turret | ψ, θ1, θ2 (°) | θ1−θ2 |
|---|---|---|---|
| Centre (0, 160) | 147 | −35.3, 48.4, −60.8 | 109.2 |
| Near-left (−32.5, 89.5) | 72 | −46.7, 62.6, −90.4 | 153.1 |
| Near-right (32.5, 89.5) | 128 | −67.2, 53.8, −67.9 | 121.7 |
| Far-left (−32.5, 230.5) | 198 | −15.4, 29.9, −40.3 | 70.2 |
| Far-right (32.5, 230.5) | 224 | −31.7, 14.3, −24.7 | 39.0 |

The tightest margin is near-left (θ1−θ2 = 153° vs. the 160° linkage limit), which still leaves 7°.

## 3. Sensitivity: tip motion per servo degree

Tip motion in the screen plane (xy) for **one servo degree** (yaw already divided by the 1.5:1 gear):

| Point | Yaw | Shoulder | Elbow |
|---|---|---|---|
| Centre (147 mm) | **1.71 mm** | 1.32 mm | 1.67 mm |
| Near-left (72 mm) | 0.84 | 1.56 | 1.92 |
| Near-right (128 mm) | 1.48 | 1.42 | 1.77 |
| Far-left (198 mm) | 2.30 | 0.88 | 1.23 |
| Far-right (224 mm) | **2.60** | 0.45 | 0.79 |

Yaw dominates at long reach because its lever arm is the full horizontal reach. **Without the
1.5:1 yaw gearing, every yaw figure would be 1.5× worse** (e.g. 3.9 mm/° at the far corner). That is
why the yaw servo drives a 30T turret from a 20T gear ([ADR-0002](decisions/0002-positional-servos-yaw-gearing.md)).

## 4. Command resolution (PCA9685)

The PCA9685 runs at 50 Hz with 12-bit resolution: 20 ms / 4096 = **4.88 µs per count**. The servo
mapping is 2000 µs / 180° = 11.1 µs per degree, so one count is **≈ 0.44° of servo rotation**.
The worst-case rounding error is half a count, **±0.22°** on every joint (±0.15° at the yaw output
after gearing). This is a fixed floor, so it appears in the budget below as an uncorrectable term.

## 5. XY error budget (target: 2 mm)

The terms are independent, so they are combined as root-sum-square (RSS). The worst-case sum (every
term at its extreme in the same direction) is shown for reference.

Assumptions per term:

| Term | Raw assumption | Mitigated assumption | Mitigation |
|---|---|---|---|
| Servo deadband, yaw (SG90) | ±0.45° (≈ 5 µs deadband band) | ±0.15° | Same-direction approach |
| Servo deadband, shoulder/elbow (MG90S) | ±0.23° | ±0.10° | Same-direction approach |
| PCA9685 quantization | ±0.22° per joint | unchanged | (floor) |
| Yaw gear backlash | ±0.25° at turret | ±0.06° | Same-direction approach + gear mesh shim |
| Linkage pin clearance | ±0.30 mm | ±0.15 mm | M3 shoulder screws |
| Structural flex | ±0.20 mm | unchanged | |
| Calibration residual | ±0.50 mm | unchanged | Touch-probe calibration achieves this |
| Stylus tip compliance / centroid | ±0.30 mm | unchanged | |
| Touch controller centroiding | ±0.30 mm | unchanged | |

Results (left arm):

| Point | Raw RSS | Mitigated RSS | Raw worst-case sum |
|---|---|---|---|
| Centre (0, 160) | **1.47 mm** | **0.99 mm** | 4.73 mm |
| Far-left (−32.5, 230.5) | 1.69 mm | 1.02 mm | 4.95 mm |
| Far edge, mid (0, 230.5) | **1.74 mm** | **1.03 mm** | 4.95 mm |

Itemized contributions at the screen centre (mm):

| Term | Raw | Mitigated |
|---|---|---|
| Yaw deadband | 0.77 | 0.26 |
| Shoulder deadband | 0.30 | 0.13 |
| Elbow deadband | 0.38 | 0.17 |
| Quantization yaw / shoulder / elbow | 0.38 / 0.29 / 0.37 | same |
| Yaw gear backlash | 0.64 | 0.15 |
| Pin clearance | 0.30 | 0.15 |
| Flex | 0.20 | 0.20 |
| Calibration residual | 0.50 | 0.50 |
| Stylus | 0.30 | 0.30 |
| Touch centroiding | 0.30 | 0.30 |
| **RSS** | **1.47** | **0.99** |

### Interpretation

* **Raw**, the design meets 2 mm on a statistical (RSS) basis everywhere on the screen, but the
  worst-case sum (~4.9 mm) does not. Occasional misses on small targets would be expected.
* **With mitigations, the error is ≈ 1.0 mm RSS.** The two mitigations are:
  1. **Same-direction approach.** The firmware always reaches the final joint target from the same
     side (overshoot by ~2° and come back). Deadband and gear backlash then show up as a
     *constant offset*, not a random error.
  2. **Touch-probe calibration** ([calibration.md](calibration.md)) measures where taps actually
     land and fits a homography, which absorbs that constant offset plus tray/phone placement error.
* What remains is dominated by the quantization floor, the calibration residual and the stylus/touch
  terms. Further gains would need a higher-resolution PWM source or encoders, which aren't
  justified at this price point.

## 6. Z (height) budget

The vertical error comes from the same shoulder/elbow terms (z sensitivity is up to 1.75 mm per
servo degree at the far corner) plus tray and phone thickness tolerance.

* Worst-case z error: **±1.9 mm**.
* The tap descends to `surface_z − press_mm` = 2.5 mm below the nominal surface. Even at +1.9 mm
  error the tip is still 0.6 mm into the glass, so **contact is guaranteed**.
* At −1.9 mm the stylus spring is compressed 2.5 + 1.9 = **4.4 mm worst case, which is less than the
  6 mm stylus travel**, so the stylus never bottoms out and the press force stays ≤ 0.15 N/mm × 4.4 mm
  ≈ 0.66 N.
* The 8 mm approach height clears the glass by ≥ 6.1 mm in the worst case.

## 7. Reference poses

| Pose | Joints L (ψ, θ1, θ2) | Tool position L (x, y, z) |
|---|---|---|
| Home | −35, 100, −20 | (−20.2, 132.6, 95.9) |
| Park | 20, 110, −10 | (−119.6, 135.0, 109.9), outside the screen area |
| Neutral (servos at 90°) | −35, 60, −30 | (13.8, 181.1, 66.6) |

## 8. Reproducing these numbers

Everything here can be regenerated from `config/robot.json` with `host/tappet/kinematics.py`
(`Kinematics(cfg).ik(arm, x, y, z)` / `.fk(arm, q)` / `.limit_violation(arm, q)`).
`host/tappet/workspace.py` (`screen_coverage`) produces the coverage map as SVG/PNG in `host/out/`.
Section 2 corresponds to a grid sweep with joint-limit checks. Section 3 comes from perturbing one joint by 1 servo degree and
measuring the FK difference. Section 5 multiplies those sensitivities by the per-term angles and
combines them as RSS.
