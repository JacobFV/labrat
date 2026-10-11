# ADR-0007: Power from a USB-C PD charger at 9 V (no battery)

* Status: Accepted
* Date: 2026-10-11

## Context

Eight servos can draw several amps in bursts (MG90S stall ≈ 0.7–0.8 A each), and the robot must also
charge the head phone (up to 1.5 A at 5 V). A 5 V/3 A USB supply (15 W) can't cover servo bursts
plus phone charging without the servo rail dropping out.

## Decision

* Input is **USB-C PD**: a CH224K trigger requests **9 V**. ≥ 20 W chargers are required, and the
  assembled SKU includes a 20 W charger.
* PTC 3 A on the input, then two bucks: **Buck A 5 V/5 A** for the servo rail (1000 µF bulk cap),
  **Buck B 5 V/3 A** for logic and the head VBUS (via a 1.5 A current-limited switch).
* Firmware monitors VIN and VSERVO. VIN < ~8 V at boot (5 V-only charger) refuses `enable`;
  VSERVO < 4.3 V raises `fault{brownout}` and pauses motion.
* **No battery** in the product.

## Alternatives considered

* **5 V USB only:** cheapest, but brownouts under load would cause servo resets and missed taps.
* **Barrel-jack 6–12 V adapter:** an extra region-specific adapter to ship, and not the cable users
  already have.
* **Internal Li-ion:** portability isn't needed on a desk, and it adds cost, UN38.3 shipping
  rules and fire risk.

## Consequences

* Separate rails keep servo noise away from the ESP32 and the phone.
* Users with only 5 V chargers get a clear fault (F4 in [architecture.md](../architecture.md)) and
  not intermittent behaviour.
* Simple safety story: ≤ 9 V internally, SELV ([safety.md](../safety.md)).
