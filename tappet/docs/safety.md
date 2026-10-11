# Tappet — Safety

Tappet is a low-energy device: no battery, at most 9 V inside, and small hobby servos. The
remaining hazards are mostly about **what the robot does on the target phone** rather than physical
injury, so this document covers both.

## 1. Physical hazards

| Hazard | Analysis | Controls |
|---|---|---|
| Pinch at linkage / gears | MG90S stall torque 2.2 kg·cm. At the 100 mm upper arm that is ≈ 2.2 N at the elbow and less at the tip. Gear mesh at the yaw turret can pinch a fingertip. | Gear covers over the 20T/30T mesh; rounded link edges; watchdog + BOOT button relax servos instantly (OE high); no motion while `relax`ed |
| Stylus pressing on a screen | Spring 0.15 N/mm; 2.5 mm press ≈ 0.4 N, worst case 4.4 mm ≈ 0.66 N ([kinematics.md §6](kinematics.md#6-z-height-budget)). Typical finger tap force is 0.5–1 N. | Compliant stylus with 6 mm travel; z clamped by IK; firmware never commands below `surface_z − press_mm` |
| Screen scratching | Hard parts touching glass | Only the conductive rubber/mesh tip touches the screen; arms park outside the screen area; the other arm is parked if within 60 mm of the target |
| Tipping | Head phone (≤ 220 g) on a 232 mm neck tilted down + arms extended | 300 g steel ballast; T-HW-8 stability test |
| Electrical / fire | 9 V × 3 A max from PD charger | PTC 3 A on input; current-limited 1.5 A head VBUS switch with fault latch; bucks with thermal shutdown; no battery in the product |
| Head phone heat | Charging + camera + screen in an enclosed clamp | Android thermal API → dim, lower frame rate, stop charging at "severe" (F13) |
| Small parts | Screws, inserts, stylus tips | Kit labelled "not for children under 14"; assembled SKU has no loose small parts |

**Fail limp:** every unhandled condition (host lost, brownout, I²C fault, button press) ends in
*park → relax*. Relaxed servos can be back-driven by hand. See the failure-mode table in
[architecture.md §6](architecture.md#6-failure-modes-and-recovery).

## 2. Digital / agent safety

The robot can operate a phone that is logged into the user's accounts, so agent behaviour is a
safety topic.

| Risk | Control |
|---|---|
| Agent taps "Buy", "Send", "Delete" unintentionally | System prompt requires explicit spoken/typed user confirmation before any payment, purchase, message send, deletion or settings change. Phase 2 adds a deterministic confirm gate in the app for these tool sequences. |
| Prompt injection from on-screen content (a web page or message tells the agent what to do) | Screen images are treated as data; the system prompt says on-screen text is never an instruction; the agent can't act on new goals without the user's request |
| Someone on the LAN controls the robot | Web API requires a pairing PIN shown on the head's face → token; tokens revocable from the head; touchpad endpoints accept only touch events |
| Camera/mic privacy | Camera frames are sent only when the agent calls `look_at_device`/`look_at_user`; face shows a visible indicator while the camera is in use; no cloud storage by Tappet; the API key belongs to the user |
| Agent unavailable | Hardware is unaffected; local timers/tasks still work (F12) |
| Runaway motion from a host bug | Firmware enforces joint limits, linkage constraint, max velocity and the watchdog regardless of host |

## 3. Compliance plan

| Area | Approach |
|---|---|
| Radio (Wi-Fi/BLE) | ESP32-S3-WROOM-1 is pre-certified (FCC/IC/CE RED modular approval); host product labelling only |
| EMC | FCC Part 15B, EN 55032/55035 pre-scan in phase 2, full test before launch |
| Electrical safety | Powered by a certified external USB-C PD adapter (assembled SKU includes one), ≤ 9 V internal: SELV, no mains |
| Batteries | None (avoids UN38.3 and lithium shipping restrictions) |
| RoHS / REACH | Component declarations collected from suppliers |
| Toy safety | Not marketed as a toy; age 14+ labelling |
| Accessibility claims | No medical/assistive-device claims until validated with partners |
