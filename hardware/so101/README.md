# SO-101 arms

LabRat's two manipulators are stock **SO-101** arms (TheRobotStudio + Hugging Face, Apache-2.0): 5 DOF plus a
gripper, six Feetech **STS3215** bus servos on one half-duplex TTL bus, and a USB bus servo adapter. This directory
holds the printable parts and CAD from upstream so a clone of this repo has everything needed to build them. LabRat
drives the arms with its own bridge (`bridge/labrat_bridge/so101.py`), not with LeRobot.

Upstream: <https://github.com/TheRobotStudio/SO-ARM100> at commit `a758567c3978dfeefe282ede0500085a48fe8f78`
(see [`NOTICE`](NOTICE) and [`LICENSE`](LICENSE)).

## What's here

| Path | Contents |
|---|---|
| `stl/common/` | the 9 parts printed for every arm (base, motor holders, upper/under arm, rotation pitch, wrist roll pitch, WaveShare adapter mounting plate) |
| `stl/follower/` | the gripper: `Moving_Jaw_SO101`, `Wrist_Roll_Follower_SO101` |
| `step/common/`, `step/follower/` | the same parts as STEP |

Only the follower build LabRat uses is vendored, byte-identical to upstream. Everything else (leader parts, the
Seeed Studio mounting plate, the full assembly, pre-oriented plates, fit gauges) is upstream at the same commit.

**Digital twin.** The console's SO-101 model is in [`apps/console/models/so101/`](../../apps/console/models/so101):
the upstream `so101_new_calib.urdf` (unchanged) with **decimated** copies of upstream `Simulation/SO101/assets/*.stl`,
cut down so the browser loads them quickly. They are display meshes for the twin, not print files; don't print from
them and don't copy them here.

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

Clamp each base to the desk (upstream uses two table clamps per arm). If you move an arm, update `ARM_PLACE` so the
twin matches.

**Joints and servo IDs** (`so101.py: JOINTS`, `IDS`; these are also the URDF joint names):

| ID | Joint | Servo |
|---|---|---|
| 1 | `shoulder_pan` | STS3215 1/345 |
| 2 | `shoulder_lift` | STS3215 1/345 |
| 3 | `elbow_flex` | STS3215 1/345 |
| 4 | `wrist_flex` | STS3215 1/345 |
| 5 | `wrist_roll` | STS3215 1/345 |
| 6 | `gripper` | STS3215 1/345 |

**Named poses** (`so101.py: DEFAULT_CALIB`; each arm's own values are recorded into its calibration file):

| Pose | pan | lift | elbow | wrist flex | wrist roll | gripper | Purpose |
|---|---|---|---|---|---|---|---|
| `home` | 0 | -90 | 90 | 45 | 0 | 0 | folded start pose |
| `ready` | 0 | -30 | 40 | 60 | 0 | 40 | gripper up and open above the deck edge, clear of the gantry; start of a pick |
| `rest` | 0 | -100 | 95 | 50 | 0 | 0 | folded low, out of the CNC envelope and the camera view, gripper closed |

Bridge actions, calibration and teleop: [`bridge/README.md`](../../bridge/README.md).

## Bill of materials (per arm)

From the upstream README's BOM:

| Item | Qty | Notes |
|---|---|---|
| Feetech STS3215 bus servo | 6 | 7.4 V, 1/345 gear (C001) |
| Bus servo adapter ("Motor Control Board") | 1 | Waveshare Bus Servo Adapter (A), as in the upstream BOM |
| Power supply | 1 | **5 V** for the 7.4 V servos (upstream BOM); **12 V, 5 A or more** if you buy the 12 V STS3215 variant (follower only) |
| USB-C cable | 1 | adapter to the laptop |
| Table clamps | 2 | hold the base to the desk |
| Printed parts | 1 set | `stl/common/` + `stl/follower/` |
| Small Phillips screwdrivers #0 and #1 | | screws come with the servos |

Servo voltage, per upstream: the 7.4 V STS3215 gives 16.5 kg·cm stall torque at 6 V, a little less on the 5 V
supply, which upstream found sufficient. The 12 V version gives 30 kg·cm and needs the 12 V supply. Never put 12 V
on a bus that carries 7.4 V servos: one supply feeds every servo on the bus, so do not mix variants on one arm.

## Printing

Both arms are on plate 06 in [`../print`](../print) (upstream's pre-oriented follower plate, PLA+, supports except
in horizontal screw holes). Print upstream's STS3215 fit gauge first and tune until the fit matches upstream's video.
The files here are the individual parts in their design frame, for re-printing a broken part.

## Assembly

Follow the upstream assembly guide, which has a video for each joint:
**<https://huggingface.co/docs/lerobot/so101>**. Do the servo ID step (below) **before** assembly; each servo is
much harder to reach once it's built in. When fitting each horn, keep the servo near its mid position (as the
guide does). The bridge reads one turn (0..4095 ticks), and a joint whose working range crosses the 0/4095 seam
will take the long way round.

## Servo IDs and baud

New STS3215s ship with ID 1. Each arm needs IDs **1..6** in the joint order above at **1 000 000 baud** (the bridge
opens the bus at 1 Mbps and pings IDs 1..6 to discover an arm). **The LabRat bridge has no ID-setting command.**
Use one of:
- LeRobot (`pip install -e ".[feetech]"` in a lerobot checkout): `lerobot-setup-motors --robot.type=so101_follower
  --robot.port=<port>`. It prompts you to connect one servo at a time, gripper first, and sets
  its ID and baud (LeRobot's Feetech default is 1 000 000). You don't need LeRobot's calibration step for LabRat.
- Feetech's FD software (Windows) or [FT_SCServo_Debug_Qt](https://github.com/Kotakku/FT_SCServo_Debug_Qt) (Linux).

Check the result from this repo:
```bash
cd bridge && uv run python -m labrat_bridge scan    # each arm's port should show kind so101, "ids [1, 2, 3, 4, 5, 6]"
```

## Wiring

```
laptop USB ── adapter ── 3-pin ── ID1 shoulder_pan ── ID2 shoulder_lift ── ID3 elbow_flex ── ID4 wrist_flex ── ID5 wrist_roll ── ID6 gripper
                 │
              5 V (or 12 V) DC in
```
- One bus per arm: a daisy chain of 3-pin servo cables from the adapter to ID 1, then servo to servo up to the gripper.
  Each arm has its own adapter, its own USB cable and its own supply.
- **Waveshare adapter:** set both jumpers to the **`B`** position (USB), as in the upstream guide. On `A` the USB
  side isn't connected to the servo bus and the bridge finds nothing.
- Power the adapter before starting the bridge; with USB but no servo power, the pings fail and the arm falls back to
  a mock (`status: "mock"`, reason in `info.fallback`).
- Port mapping: the bridge stores each arm's USB serial number in `bridge/calib/ports.json`. If `so_a`/`so_b` come up
  swapped, run once with `--so-a <port> --so-b <port>`, and the mapping is saved.

## Calibration and teleop

Per-arm joint offsets, limits and poses: `bridge/calib/so_a.json` / `so_b.json`, recorded with the bridge's
`calibrate` command; leader → follower teleop with `teleop`. Both are in [`bridge/README.md`](../../bridge/README.md).

## Safety

- **Joint clamps:** every target (`joints`, `pose`, `gripper`, and each teleop sample) is clamped to the calibrated
  `min`/`max` before it's sent (`so101.py: clamp`). The clamps are only as good as the calibration; re-run the limits step after
  any rebuild.
- Moves are interpolated (smoothstep, 50 Hz). `stop` holds the present position and ends teleop. On the arms,
  `stop` is serialized with other actions (`So101.unlocked` is only `read`), so a `stop` sent during a `joints`/`pose`
  move takes effect when that move ends (moves are `ms` long, 0.5–1.5 s by default). Keep `ms` short for moves near
  obstacles, and use the power switch as the hard stop.
- **Watchdog:** if the server link is down for more than 3 s, the bridge holds both arms where they are, interrupting
  a running move (`safe_stop`). **Abort**
  (`POST /api/abort`) sends `stop` to `so_a`, `so_b` and the CNC.
- `torque {on: false}` and `calibrate` drop the arm, so hold it first. The 1/345 follower is stiff but not
  self-locking.
- Keep each arm's reach clear of the CNC gantry and bed: park in `rest` before CNC jobs. The bed moves in Y under the
  arms.
- Pinch points at the gripper and elbow. On the 12 V variant, the arm is strong enough to hurt.
