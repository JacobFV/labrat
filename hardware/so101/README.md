# SO-101 arms

LabRat's two manipulators are stock **SO-101** arms ([TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100),
Apache-2.0): 5 DOF plus a gripper, six Feetech **STS3215** bus servos on one half-duplex TTL bus, and a USB bus
servo adapter. Both arms are already built (two follower builds), so this repo holds no print files for them; to
re-print a broken part, take it from upstream. LabRat drives the arms with its own bridge
(`bridge/labrat_bridge/so101.py`), not with LeRobot.

**Digital twin.** The console's model is in [`apps/console/models/so101/`](../../apps/console/models/so101): the
upstream `so101_new_calib.urdf` (unchanged) with decimated copies of upstream `Simulation/SO101/assets/*.stl`, under
upstream's LICENSE there. They are display meshes, not print files.

## How LabRat uses the arms

| Device id | Role | Bench position |
|---|---|---|
| `so_a` | follower build, stock gripper; default teleop **follower** | operator's **left**, at the CNC's front-left corner |
| `so_b` | follower build, stock gripper; default teleop **leader** (moved by hand, torque off) | operator's **right**, at the CNC's front-right corner |

**Placement** (`apps/console/twin.js`, `ARM_PLACE`): coordinates are the **pan axis** in the twin's bench-top frame,
in mm: origin at the CNC centre on the desk, X to the operator's left, Z toward the machine back, Y up.
- `so_a`: `[250, 0, -300]`: 250 mm left of the CNC centre line, 300 mm in front of the CNC centre.
- `so_b`: `[-250, 0, -300]`: 250 mm right, 300 mm in front.
- Both bases stand on the desk (Y = 0). At the zero pose the arm reaches toward the machine back (+Z).

Clamp each base to the desk (two table clamps per arm). If you move an arm, update `ARM_PLACE` so the twin matches.

**Joints and servo IDs** (`so101.py: JOINTS`, `IDS`; these are also the URDF joint names). Every servo is an
STS3215 with the 1/345 gear.

| ID | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| joint | `shoulder_pan` | `shoulder_lift` | `elbow_flex` | `wrist_flex` | `wrist_roll` | `gripper` |

**Named poses** (`so101.py: DEFAULT_CALIB`; each arm's own values are recorded into its calibration file):

| Pose | pan | lift | elbow | wrist flex | wrist roll | gripper | Purpose |
|---|---|---|---|---|---|---|---|
| `home` | 0 | -90 | 90 | 45 | 0 | 0 | folded start pose |
| `ready` | 0 | -30 | 40 | 60 | 0 | 40 | gripper up and open above the deck edge, clear of the gantry; start of a pick |
| `rest` | 0 | -100 | 95 | 50 | 0 | 0 | folded low, out of the CNC envelope and the camera view, gripper closed |

## Servo IDs and baud

Each arm needs IDs **1..6** in the joint order above at **1 000 000 baud** (the bridge opens the bus at 1 Mbps and
pings IDs 1..6 to discover an arm). New STS3215s ship with ID 1, so a replacement servo needs its ID set before it
goes in. **The LabRat bridge has no ID-setting command.** Use LeRobot (`lerobot-setup-motors
--robot.type=so101_follower --robot.port=<port>`; you don't need its calibration step), Feetech's FD software
(Windows) or [FT_SCServo_Debug_Qt](https://github.com/Kotakku/FT_SCServo_Debug_Qt) (Linux). Keep the servo near mid
position when fitting a horn: the bridge reads one turn (0..4095 ticks), and a joint whose range crosses the 0/4095
seam takes the long way round.

Check from this repo:
```bash
cd bridge && uv run python -m labrat_bridge scan    # each arm's port should show kind so101, "ids [1, 2, 3, 4, 5, 6]"
```

## Wiring and power

```
laptop USB ── adapter ── 3-pin ── ID1 shoulder_pan ── ID2 shoulder_lift ── ID3 elbow_flex ── ID4 wrist_flex ── ID5 wrist_roll ── ID6 gripper
                 │
              5 V (or 12 V) DC in
```
- One bus per arm: a daisy chain of 3-pin servo cables from the adapter to ID 1, then servo to servo up to the
  gripper. Each arm has its own adapter, USB cable and supply.
- **Supply:** **5 V** for the 7.4 V STS3215 (upstream BOM; 16.5 kg·cm stall at 6 V, a little less at 5 V, which
  upstream found sufficient). **12 V, 5 A or more** only for the 12 V STS3215 variant (30 kg·cm). Never put 12 V on a
  bus that carries 7.4 V servos, and do not mix variants on one arm.
- **Waveshare adapter:** set both jumpers to **`B`** (USB). On `A` the USB side isn't connected to the servo bus and
  the bridge finds nothing.
- Power the adapter before starting the bridge; with USB but no servo power, the pings fail and the arm falls back to
  a mock (`status: "mock"`, reason in `info.fallback`).
- Port mapping: the bridge stores each arm's USB serial number in `bridge/calib/ports.json`. If `so_a`/`so_b` come up
  swapped, run once with `--so-a <port> --so-b <port>`, and the mapping is saved.

## Calibration and teleop

Per-arm joint offsets, limits and poses: `bridge/calib/so_a.json` / `so_b.json`, recorded with the bridge's
`calibrate` command; leader → follower teleop with `teleop`. Both are in [`bridge/README.md`](../../bridge/README.md).

## Safety

- **Joint clamps:** every target (`joints`, `pose`, `gripper`, and each teleop sample) is clamped to the calibrated
  `min`/`max` before it's sent (`so101.py: clamp`). The clamps are only as good as the calibration; re-run the
  limits step after any rebuild.
- Moves are interpolated (smoothstep, 50 Hz). `stop` holds the present position and ends teleop. On the arms,
  `stop` is serialized with other actions (`So101.unlocked` is only `read`), so a `stop` sent during a
  `joints`/`pose` move takes effect when that move ends (moves are `ms` long, 0.5–1.5 s by default). Keep `ms` short
  for moves near obstacles, and use the power switch as the hard stop.
- **Watchdog:** if the server link is down for more than 3 s, the bridge holds both arms where they are,
  interrupting a running move (`safe_stop`). **Abort** (`POST /api/abort`) sends `stop` to `so_a`, `so_b` and the CNC.
- `torque {on: false}` and `calibrate` drop the arm, so hold it first. The 1/345 follower is stiff but not
  self-locking.
- Keep each arm's reach clear of the CNC gantry and bed: park in `rest` before CNC jobs. The bed moves in Y under the
  arms.
- Pinch points at the gripper and elbow. On the 12 V variant, the arm is strong enough to hurt.
