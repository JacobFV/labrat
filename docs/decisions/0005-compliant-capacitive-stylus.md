# ADR-0005: Spring-compliant, grounded capacitive stylus tips

* Status: Accepted
* Date: 2026-10-11

## Context

A capacitive screen registers a touch when a conductive object coupled to a large capacitance
(normally a human body) contacts it. The arm's z position is uncertain by **±1.9 mm** worst case
([kinematics.md §6](../kinematics.md#6-z-height-budget)). A rigid tip would miss taps (too high) or
press hard on the glass (too low).

## Decision

* A 6 mm conductive rubber/fiber-mesh tip on a 4 mm brass tube, sliding in the holder against a
  **~0.15 N/mm spring with 6 mm travel**.
* Taps descend to `surface_z − 2.5 mm` (`press_mm`). The spring absorbs the overtravel.
* The tube is **wired to circuit ground** (30 AWG lead), which gives enough capacitance to register
  reliably.
* A wrist parallelogram keeps the stylus vertical.

## Analysis

* At +1.9 mm z error the tip is still 0.6 mm into the glass, so contact is guaranteed.
* At −1.9 mm the spring compresses 4.4 mm (< 6 mm travel), so the stylus never bottoms out. Force
  is ≤ ~0.66 N, about a light finger tap. The nominal force is 0.4 N.

## Alternatives considered

* **Rigid tip + precise z:** would need sub-mm z accuracy, which these servos can't deliver.
* **Force sensor / switch in the tip:** closes the loop on z but adds wiring and cost. The
  touch-probe calibration already measures `surface_z`.
* **Ungrounded tip:** registers on some phones but not reliably.

## Consequences

The tip is a wear item (replaceable, $0.22 at 1k). Ground continuity is part of QC (< 1 kΩ).
