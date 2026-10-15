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
| `stl/common/` | the 10 parts printed for **every** arm (base, motor holders, upper/under arm, rotation pitch, wrist roll pitch) plus the two adapter mounting plates: print **one** of `WaveShare_Mounting_Plate_SO101` or `Seeedstudio_Mounting_Plate_SO101`, to match your board |
| `stl/follower/` | follower-only: `Moving_Jaw_SO101`, `Wrist_Roll_Follower_SO101` (the gripper) |
| `stl/leader/` | leader-only: `Handle_SO101`, `Trigger_SO101`, `Wrist_Roll_SO101` (handle + trigger instead of a gripper) |
| `step/common/`, `step/follower/`, `step/leader/` | the same parts as STEP |
| `step/SO101_Assembly.step` | the full upstream assembly (20.5 MB) |

All files are byte-identical to upstream. Not vendored (get them from upstream at the same commit):
- ready-to-print plates, all parts of one arm pre-oriented on one bed:
  [`STL/SO101/Follower/`](https://github.com/TheRobotStudio/SO-ARM100/tree/a758567c3978dfeefe282ede0500085a48fe8f78/STL/SO101/Follower)
  and [`STL/SO101/Leader/`](https://github.com/TheRobotStudio/SO-ARM100/tree/a758567c3978dfeefe282ede0500085a48fe8f78/STL/SO101/Leader)
  (Ender 220 × 220, Prusa 205 × 250, Bambu A1 mini);
- `SO101 Assembly.stl` (37.6 MB, a reference model, not printable);
- the fit gauges ([`STL/Gauges/`](https://github.com/TheRobotStudio/SO-ARM100/tree/a758567c3978dfeefe282ede0500085a48fe8f78/STL/Gauges))
  and the optional add-ons (`Optional/`).

**Digital twin.** The console's SO-101 model is in [`apps/console/models/so101/`](../../apps/console/models/so101):
the upstream `so101_new_calib.urdf` (unchanged) with **decimated** copies of upstream `Simulation/SO101/assets/*.stl`,
cut down so the browser loads them quickly. They are display meshes for the twin, not print files; don't print from
them and don't copy them here.

## How LabRat uses the arms

| Device id | Role | Bench position |
|---|---|---|
| `so_a` | follower build, stock gripper; default teleop **follower** | operator's **left**, at the CNC's front-left corner |
| `so_b` | follower build, stock gripper; default teleop **leader** (moved by hand, torque off) | operator's **right**, at the CNC's front-right corner |

Both are plain manipulators. The camera is an iPhone on a static stand, not on an arm.

**Placement** (`apps/console/twin.js`, `ARM_PLACE`): coordinates are the **pan axis** in the twin's bench-top frame,
in mm: origin at the CNC centre on the desk, X to the operator's left, Z toward the machine back, Y up.
- `so_a`: `[250, 0, -300]`: 250 mm left of the CNC centre line, 300 mm in front of the CNC centre.
- `so_b`: `[-250, 0, -300]`: 250 mm right, 300 mm in front.
- Both bases stand on the desk (Y = 0). At the zero pose the arm reaches toward the machine back (+Z).

Clamp each base to the desk (upstream uses two table clamps per arm). If you move an arm, update `ARM_PLACE` so the
twin matches.

**Joints and servo IDs** (`so101.py: JOINTS`, `IDS`; these are also the URDF joint names):

| ID | Joint | Follower servo | Leader servo (upstream) |
|---|---|---|---|
| 1 | `shoulder_pan` | STS3215 1/345 | STS3215 1/191 (C044) |
| 2 | `shoulder_lift` | STS3215 1/345 | STS3215 1/345 (C001) |
| 3 | `elbow_flex` | STS3215 1/345 | STS3215 1/191 (C044) |
| 4 | `wrist_flex` | STS3215 1/345 | STS3215 1/147 (C046) |
| 5 | `wrist_roll` | STS3215 1/345 | STS3215 1/147 (C046) |
| 6 | `gripper` | STS3215 1/345 | STS3215 1/147 (C046) |

**Named poses** (`so101.py: DEFAULT_CALIB`; each arm's own values are recorded into its calibration file):

| Pose | pan | lift | elbow | wrist flex | wrist roll | gripper | Purpose |
|---|---|---|---|---|---|---|---|
| `home` | 0 | -90 | 90 | 45 | 0 | 0 | folded start pose |
| `ready` | 0 | -30 | 40 | 60 | 0 | 40 | gripper up and open above the deck edge, clear of the gantry; start of a pick |
| `rest` | 0 | -100 | 95 | 50 | 0 | 0 | folded low, out of the CNC envelope and the camera view, gripper closed |

The legacy names `look_deck`, `look_user`, `look_plate` alias to `ready`. Bridge actions per arm: `joints {q, ms}`,
`pose {name, ms?}`, `gripper {open 0..1}`, `torque {on}`, `teleop {leader, on, hz?, record?}`, `read {}`, `stop {}`
(see [`bridge/README.md`](../../bridge/README.md)).

## Bill of materials (per arm)

From the upstream README's BOM:

| Item | Qty | Notes |
|---|---|---|
| Feetech STS3215 bus servo | 6 | **Follower:** 6 × 7.4 V, 1/345 gear (C001). **Leader:** 3 × 1/147 (C046), 2 × 1/191 (C044), 1 × 1/345 (C001), all 7.4 V, assigned as in the table above (sold as a 6-servo leader bundle). |
| Bus servo adapter ("Motor Control Board") | 1 | the upstream BOM links a Waveshare bus servo adapter (Waveshare Bus Servo Adapter (A)); a Seeed Studio board also works and has its own mounting plate |
| Power supply | 1 | **5 V** for the 7.4 V servos (upstream BOM); **12 V, 5 A or more** if you buy the 12 V STS3215 variant (follower only) |
| USB-C cable | 1 | adapter to the laptop |
| Table clamps | 2 | hold the base to the desk |
| Printed parts | 1 set | `stl/common/` + `stl/follower/` **or** `stl/leader/` |
| Small Phillips screwdrivers #0 and #1 | | screws come with the servos |

Servo voltage, per upstream: the 7.4 V STS3215 gives 16.5 kg·cm stall torque at 6 V, a little less on the 5 V
supply, which upstream found sufficient. The 12 V version gives 30 kg·cm and needs the 12 V supply. **The leader is
always the 7.4 V version.** Never put 12 V on a bus that carries 7.4 V servos: one supply feeds every servo on the
bus, so do not mix variants on one arm.

The LabRat bench uses two follower builds (`so_a`, `so_b`). A dedicated leader (handle + trigger, low-ratio
servos) is only needed if teleoperating by backdriving a 1/345 follower is too stiff for you; see *Teleop* below.

## Printing

Upstream settings ([README, "Printing the Parts"](https://github.com/TheRobotStudio/SO-ARM100#printing-the-parts)):
- PLA+, 0.4 mm nozzle at 0.2 mm layers (or 0.6 mm nozzle at 0.4 mm), 15 % infill;
- supports everywhere except slopes steeper than 45° from horizontal, and **no supports in horizontal screw holes**;
- print the gauge first (`Gauge_0` / `Gauge_tight_1` against an STS3215, or the Lego gauges) and tune until the fit
  matches upstream's video;
- easiest path: the per-printer plate STLs upstream (linked above), already oriented. The files here are the
  individual parts, for re-printing a broken part or for a print service (upstream
  [3DPRINT.md](https://github.com/TheRobotStudio/SO-ARM100/blob/a758567c3978dfeefe282ede0500085a48fe8f78/3DPRINT.md)
  says to upload one mounting plate, and quantity 2 of the shared parts for two arms).

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
  --robot.port=<port>` (or `so101_leader`). It prompts you to connect one servo at a time, gripper first, and sets
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

## Calibration (LabRat bridge)

Each arm has its own file, `bridge/calib/so_a.json` / `so_b.json` (or `$LABRAT_CALIB_DIR`): per joint
`offset` (ticks), `sign` (±1), `min`/`max` (deg), and the `home`/`ready`/`rest` poses.
`deg = sign × (ticks − 2048 − offset) × 360 / 4096`. Without a file the bridge uses `DEFAULT_CALIB`.

Interactive (torque goes **off**, so support the arm):
```bash
cd bridge
uv run python -m labrat_bridge calibrate --device so_a --port /dev/ttyACM0     # omit --port for a mock dry run
```
1. **Zero:** put the arm in the zero pose, then press Enter. LabRat's zero, which matches the URDF zero the twin
   draws: upper arm vertical, forearm horizontal pointing to the machine back, wrist straight, wrist roll centred,
   gripper half open. Records `offset`.
2. **Limits:** press Enter, sweep **every** joint through its full safe range, then press Enter. Records `min`/`max`.
3. **Poses:** pose the arm as `home`, `ready`, `rest` in turn and press Enter after each.

Without a TTY, run one step per call:
```bash
uv run python -m labrat_bridge calibrate --device so_b --port /dev/ttyACM1 --step zero
uv run python -m labrat_bridge calibrate --device so_b --port /dev/ttyACM1 --step limits:45   # seconds to sweep
uv run python -m labrat_bridge calibrate --device so_b --port /dev/ttyACM1 --step pose:ready
```
If a joint moves the wrong way in the twin, flip its `sign` and redo limits and poses. Afterwards, check that
**every** joint in the JSON has a plausible `min`..`max`: a joint you didn't sweep gets a near-zero span and the
clamp then freezes it, and a span over 360° means the zero or the horn is near the tick seam.

Calibration is per arm, so a pose recorded on one arm means the same joint angles on the other.

## Teleop (leader → follower)

```bash
cd bridge
uv run python -m labrat_bridge teleop --leader so_b --follower so_a \
    --leader-port /dev/ttyACM1 --follower-port /dev/ttyACM0 [--hz 30] [--seconds 60]
```
The leader's torque goes off (move it by hand). The follower tracks it in calibrated degrees, clamped to the
follower's own limits, and the trajectory is saved to `bridge/calib/teleop_<follower>_<unix time>.json`. With the bridge
running, the same thing is the `teleop {leader: "so_b", on: true}` action on the follower. `calibrate` only accepts
`so_a`/`so_b`. To use a dedicated leader-build arm as a third device, calibrate it with `LABRAT_CALIB_DIR` pointed at
a scratch directory, copy the result to `bridge/calib/<name>.json`, and pass `--leader <name>`.

## Safety

- **Joint clamps:** every target (`joints`, `pose`, `gripper`, and each teleop sample) is clamped to the calibrated
  `min`/`max` before it's sent (`so101.py: clamp`). The clamps are only as good as the calibration; re-run step 2 after
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
