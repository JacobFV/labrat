# Tappet — Bill of Materials

The machine-readable BOM is [bom.csv](bom.csv) (32 lines). This page summarizes it and explains the
cost assumptions. Column `sku` says which product a line ships in: `both`, `kit` or `assembled`.

## Totals

| | Prototype (qty 1, retail parts) | 1k units, before freight | Landed at 1k (+10% freight/duty) |
|---|---|---|---|
| **Kit** (sells at $99) | **$78.86** | **$39.22** | **$43.14** |
| **Assembled** (sells at $129) | $84.86 (+ founder time) | **$46.32** | **$50.95** |

Assembled = kit + A01 final assembly/calibration/test at the CM ($4.50) + A02 20 W PD charger ($2.60).

## By category (lines common to both SKUs)

| Category | Prototype | 1k | Main items |
|---|---|---|---|
| Electronics | $27.20 | $11.02 | ESP32-S3 DevKitC (→ WROOM module on v1 PCB), PCA9685, CH224K PD trigger, 2 bucks, VBUS switch, carrier |
| Servos | $23.50 | $10.30 | 3× SG90 (yaws, pan), 5× MG90S (shoulders, elbows, tilt) |
| Mechanical | $17.66 | $14.77 | ~330 g printed parts, 3× 608ZZ, fasteners, inserts, stylus parts, steel ballast |
| Cables | $4.00 | $1.15 | 0.3 m head cable, 1 m power cable |
| Packaging | $6.50 | $1.98 | Mailer box + pulp insert, quick-start card |
| **Total (kit)** | **$78.86** | **$39.22** | |

## Notes and assumptions

* **Printed parts are the only line that gets more expensive at scale** ($7.00 → $11.50). The
  prototype figure is filament only; the 1k figure is a print-farm quote including machine time and
  QC (~$0.035/g). Moving the base, turrets and arms to injection moulding is the largest single
  cost lever on the [roadmap](roadmap.md) (tooling ~$15–25k; part cost drops to ~$2.50).
* **Electronics drop ~60%** at volume, mostly because v1 replaces the breakouts with a single 2-layer
  carrier PCB holding the ESP32-S3-WROOM-1 module, PCA9685, CH224K and bucks.
* **Servos** include a 10% QC overage at 1k. Servos are tested on arrival (sweep + current) because
  SG90 quality varies a lot by lot.
* **Not included:** the Android head phone and the target phone (user-supplied), the charger
  in the kit SKU (users must supply ≥ 20 W USB-C PD with 9 V), and freight (added as +10% for
  landed cost).
* **No battery** in the product. This avoids lithium shipping restrictions and the extra
  certification work (see [safety.md](safety.md)).

## Sourcing risks

| Part | Risk | Mitigation |
|---|---|---|
| SG90 / MG90S | Lot-to-lot deadband and centring variation | Incoming sweep test; calibration absorbs offsets |
| ESP32-S3 module | Single source (Espressif), but high availability | Pin-compatible N8R2/N16R8 variants |
| CH224K | Single source (WCH) | IP2721 as alternate PD sink (board footprint) |
| Printed parts | Print-farm capacity at launch | Two farms qualified; moulding at > 3k units |
