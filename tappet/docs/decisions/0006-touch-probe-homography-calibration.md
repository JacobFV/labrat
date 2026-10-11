# ADR-0006: Screen calibration by touch-probe homography

* Status: Accepted
* Date: 2026-10-11

## Context

Placement of the target phone in the tray (±1–2 mm and a small rotation), device-profile errors,
and the constant offsets left by same-direction servo approach together add up to several mm,
which is more than the 2 mm budget. The robot has no sensor that can see exactly where the stylus
touches the glass.

## Decision

The **target phone reports its own touches**. It opens `touchpad.html` (served by the head) in
full screen. The robot taps a 3×3 grid (u,v ∈ {0.15, 0.5, 0.85}), and the page posts where each
touch registered. A **least-squares homography** `H_sw` (normalized DLT) maps screen (u,v) to world
(x,y), and `surface_z` is found by stepping the press depth until the first touch registers.
Acceptance: RMS residual ≤ 0.6 mm, max ≤ 1.2 mm; otherwise retry with 16 points.

A separate 4-point homography `H_ic` (user clicks screen corners in a camera snapshot) is used only
for the agent's rectified view, never for tap positions.

## Alternatives considered

* **Camera-based localisation of the stylus:** the head camera's resolution at the tray (~0.3 mm/px
  at best) and the stylus occluding the screen make this worse and more fragile.
* **Mechanical fixturing only (shims):** reduces placement error but can't remove servo offsets.
* **Per-joint full kinematic calibration:** more parameters to fit and more taps needed. The
  homography absorbs the dominant errors over the small screen area with 9 taps.

## Consequences

* The calibration residual is ≈ 0.5 mm, the largest single term in the mitigated ≈ 1.0 mm budget.
* It needs a browser on the target phone, which works on any iPhone or Android device. For
  devices without one there is a manual jog fallback (~1.5 mm).
* The same touchpad page powers the tap test, so accuracy is measured the same way it is
  calibrated.
