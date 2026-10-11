# Tappet — Roadmap

Each phase ends at a gate. A phase only starts once the previous gate is met.

## Phase 0 — v0 prototype (now)

* Perfboard electronics with breakouts, printed parts, DevKitC.
* Firmware: protocol v1, AOA host, motion queue, watchdog, brownout, NVS calibration.
* Android app: AOA link, face, web UI on :8080, Claude agent with tap/swipe/type/look tools.
* Host tools: kinematics, simulator, bridge, touch-probe calibration, tap test.
* Docs: this set.

**Gate G0:** one robot passes the tap test (p95 ≤ 2 mm) on an iPhone 15 and completes the
"read me the 2FA code" demo end to end, 10 runs in a row.

## Phase 1 — Alpha (10 units)

* Build 10 units from the kit instructions alone (tests [assembly.md](assembly.md)).
* Fill in the Android head compatibility matrix ([test-plan.md §5](test-plan.md)).
* Same-direction approach and touch-probe calibration validated against the
  ≈ 1.0 mm RSS prediction in [kinematics.md](kinematics.md).
* Servo-trim page in the web UI (today it's raw `servo_raw`/`calib_set` commands).
* Endurance test T-HW-7 (10k taps).

**Gate G1:** 8 of 10 alpha builders finish assembly + calibration without help; median tap-test p95 ≤ 2 mm.

## Phase 2 — v1 hardware + beta (100 units)

* v1 carrier PCB: ESP32-S3-WROOM-1, PCA9685, CH224K, bucks and VBUS switch on one 2-layer board
  (BOM electronics $27 → $11 at 1k).
* DFM pass on printed parts; print-farm qualification (two vendors).
* Pre-compliance scan (FCC Part 15B / CE EMC); the radio is covered by the pre-certified module.
* Agent: app-specific routines ("skills"), a confirmation step before any purchase/payment,
  local wake word.
* More device profiles and tray shims.

**Gate G2:** beta return rate < 5%; pre-compliance pass; landed BOM ≤ $45 (kit).

## Phase 3 — Launch (1k units)

* Crowdfunding → first production batch, assembled SKU built by the CM (QC checklist + tap test
  on every unit).
* Docs site, video assembly guide.
* Optional hosted agent plan.

**Gate G3:** contribution per unit within $3 of the plan in [business.md](business.md).

## Later

| Item | Why | Trigger |
|---|---|---|
| Injection-moulded base/turrets/arms | Saves ~$9/unit; payback ~2.2k units | > 3k units committed |
| Higher-resolution servo drive (direct ESP32 LEDC, 14-bit+) | Removes the ±0.22° PCA9685 floor | Accuracy complaints on keyboards |
| Magnetic encoders on yaw | Closes the loop on the dominant error term | Pro / QA tier |
| Third DOF at the wrist (rotate) | Multi-touch gestures (pinch) with both arms already possible; rotation needed for some games | User demand |
| Tablet tray | Larger screens; needs longer links | Pro / QA tier |
