# ADR-0002: Positional hobby servos with 1.5:1 yaw gearing and same-direction approach

* Status: Accepted
* Date: 2026-10-11

## Context

Taps must land within **2 mm** of the target over the whole screen. The actuator budget is ~$10 for
8 joints at 1k units. Without position feedback, accuracy depends on how repeatable each actuator
is when given the same command.

## Decision

* **Positional** SG90 (yaws, neck pan) and MG90S (shoulders, elbows, neck tilt) servos.
* Yaw servos drive the turret through a **20T → 30T gear (1.5:1)**.
* The motion planner always **finishes on the same side** of every joint target, so deadband and
  backlash become a constant offset that calibration removes.

## Analysis (from [kinematics.md](../kinematics.md))

* Tip motion per servo degree at the screen centre: yaw 1.71 mm, shoulder 1.32 mm, elbow 1.67 mm;
  yaw at the far corner (224 mm reach) 2.60 mm. **Without gearing, yaw would be 1.5× worse**
  (3.9 mm/° at the far corner).
* RSS error: 1.47 mm (centre) / 1.74 mm (far edge) raw; **≈ 1.0 mm** with same-direction approach
  + touch-probe calibration. The raw worst-case sum is ~4.9 mm, which is why the mitigations are
  required.
* The yaw range used over the screen is −67°…−15° (left arm), so the 1.5:1 gear still fits within
  the servo's 180° travel with margin.

## Alternatives considered

* **Continuous-rotation servos:** they only control speed and drift without encoders, so taps
  can't be repeated. Rejected.
* **Steppers + drivers:** better resolution but ~3–4× cost, more current, homing switches and
  larger mass on the arm.
* **Higher yaw ratio (2:1 or more):** better resolution but less yaw range and more backlash
  stages; 1.5:1 was enough to meet the budget.
* **Encoders now:** deferred to a pro tier ([roadmap](../roadmap.md)).

## Consequences

Calibration (servo trim + touch probe) is mandatory for every unit. A 20T/30T printed gear pair and
turret bearing are added per arm.
