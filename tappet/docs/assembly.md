# Tappet — Assembly Guide (v0 kit)

Expected time: **~2 hours** for a first build (the contract manufacturer target for the assembled SKU is
~25 min including trim and test). Part IDs refer to [bom.csv](bom.csv). The printed parts come from
`hardware/cad/` (build123d → STL). Electrical drawings are in `hardware/electronics/`.

## Tools

Phillips #0 and #1 screwdrivers, 2.5 mm hex key, soldering iron with an insert tip (for heat-set
inserts), side cutters, multimeter, a USB-C PD charger (≥ 20 W with a 9 V profile).

## 0. Before you start

* **Pre-center every servo**: plug each servo into the board, power up, `enable`, and send
  `joints` with every joint at its `neutral` angle from `robot.json` (with factory `s_zero = 90`
  that is exactly 1500 µs, i.e. servo 90°). Do this **before** fitting any horn, gear or arm. Mark each servo with its channel
  number (0–7).
* Servo assignment: **SG90** (S01) → ch0 L_YAW, ch3 R_YAW, ch6 NECK_PAN.
  **MG90S** (S02) → ch1/2 L_SHOULDER/L_ELBOW, ch4/5 R_SHOULDER/R_ELBOW, ch7 NECK_TILT.

## 1. Base

1. Press 16 heat-set inserts (M04) into the base bosses (marked with small rings): 4 for the bay lid,
   4 per yaw turret plate, 4 for the neck column.
2. Bond the steel ballast plate (M09) into its pocket on the underside with the four M3 screws.
   **Don't skip it.** Without the ~300 g ballast, the robot tips forward when the head phone
   tilts down.
3. Stick on the four rubber feet (M10).

## 2. Electronics bay

See `hardware/electronics/` for the wiring diagram. Summary:

1. Mount the carrier board (E11) on the standoffs. On v0 perfboard, the PCA9685 breakout, CH224K
   trigger, both bucks and the VBUS switch sit on the board as in the drawing.
2. **Set the bucks before connecting anything else**: power the CH224K from the charger, check
   VIN ≈ 9 V, then adjust Buck A and Buck B to **5.0–5.1 V** with the multimeter.
3. Connect: I²C SDA → GPIO8, SCL → GPIO9, PCA9685 OE → GPIO10, VBUS_EN → GPIO7,
   VBUS_FLT → GPIO15, VSERVO divider → GPIO4, VIN divider → GPIO5. Solder the 1000 µF cap (E09)
   directly at the PCA9685 V+ terminal, observing polarity.
4. HEAD USB-C port: D−/D+ → ESP32 GPIO19/20 (the native USB pins, **not** the UART bridge port).
   Fit 22 kΩ from each CC pin to 5 V.
5. Smoke test with no servos: `hello` over UART returns `fw:"tappet-fw"`; `status` shows
   `vin ≈ 9.0`, `vservo ≈ 5.0`, `faults:[]`.

## 3. Yaw turrets (×2)

1. Press a 608ZZ bearing (M02) into each turret seat.
2. Fit the SG90 yaw servo under the plate. Press the **20T printed gear** onto the servo spline.
3. Drop the **30T turret** onto the bearing so the gears mesh. Set the turret to its neutral
   heading (−35° left / +35° right; the pointer lines up with the moulded mark) with the servo at
   90°.
4. Check backlash: rocking the turret by hand should move the tip of the pointer < 0.5 mm. If it
   moves more, add the 0.2 mm mesh shim under the servo.

## 4. Arms (×2, mirror images)

1. Mount the shoulder and elbow MG90S side by side on the turret. Both drive from the shoulder
   axis.
2. Upper arm (100 mm) on the shoulder horn at **60° elevation** (the gauge), with the servo at 90°.
3. Elbow drive link on the elbow horn, then the forearm (110 mm) via the parallel drive link, at
   **−30°** absolute (gauge). Use the M3 shoulder screws (M03) for every linkage pivot. Tighten
   to "no play, no drag", then back off 1/8 turn.
4. Wrist parallelogram links: the stylus holder must stay vertical as you move the arm by hand
   (power off). If it tilts, a link is on the wrong hole.
5. Stylus: brass tube (M06) + spring (M07) + conductive tip (M05) into the holder. Solder the ground
   lead (M08) to the tube and route it along the arm to GND on the carrier. Check
   tip→GND < 1 kΩ.

Before powering the arms, also move them by hand through the full range. Nothing should bind, and
the linkage stop must engage before the forearm touches the upper arm.

## 5. Neck and head clamp

1. Fit the neck column into the base with a 608ZZ (pan bearing). The SG90 pan servo drives it
   directly.
2. Fit the MG90S tilt bracket at the top and the phone clamp with silicone pads.
3. Route the 0.3 m head cable (C01) up the column through the clip slots, leaving a service loop at
   the pan joint (±80° rotation).

## 6. Tray

Clip the tray to the front of the base. Insert the shims for your target phone (default: iPhone
15/16 6.1"). The phone lies screen up, **top edge toward the user** (away from the robot).

## 7. First power-up

1. Close the bay lid. Connect the charger to the PD input port.
2. The status LED (GPIO48) lights; `status` over UART should report `faults:[]`.
3. Dock the Android phone in the clamp and connect the head cable. The Tappet app launches
   automatically (accept the "open Tappet when this accessory is connected" prompt and tick
   "always").
4. Run the calibration in [calibration.md](calibration.md): servo trim first, then screen.
5. Finish with the tap test (`/api/taptest {n:100}`). The build is done when it passes.

## QC checklist (also used by the CM)

- [ ] VIN 8.6–9.4 V, VSERVO 4.9–5.2 V idle, ≥ 4.6 V with all servos moving
- [ ] Head charges (Android shows "charging") with app connected
- [ ] All 8 joints reach home within one servo step of the gauge
- [ ] Stylus tip → GND < 1 kΩ, both arms
- [ ] Yaw backlash at the stylus < 0.5 mm
- [ ] Tap test passes (≥ 99/100, p95 ≤ 2 mm)
- [ ] Watchdog: disconnecting the head parks and relaxes the arms within 6 s
