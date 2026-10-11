"""SO-101 arm: Feetech STS3215 bus servos (SCS/STS protocol) over a USB bus adapter @ 1 Mbps.

so_a and so_b are plain manipulators with the stock gripper, one on each side of the 3018 CNC. The iPhone camera is
on a static stand above the CNC bed, not on an arm, so the named poses are manipulation poses only."""
from __future__ import annotations

import struct
import threading
import time

from .common import Device, DeviceError, load_json, log, save_json

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
IDS = {j: i + 1 for i, j in enumerate(JOINTS)}

PING, READ, WRITE, SYNC_WRITE = 0x01, 0x02, 0x03, 0x83
BROADCAST = 0xFE
REG_TORQUE, REG_ACCEL, REG_GOAL_POS, REG_GOAL_TIME, REG_GOAL_SPEED, REG_PRESENT_POS = 40, 41, 42, 44, 46, 56
TICKS_PER_REV, CENTER = 4096, 2048
RATE_HZ = 50

POSES = ["home", "ready", "rest"]
# Old pose names from when the phone rode on so_a. Kept so stale clients/calib files don't crash; they map to `ready`.
POSE_ALIASES = {"look_deck": "ready", "look_user": "ready", "look_plate": "ready"}
DEFAULT_CALIB = {
    "joints": {
        "shoulder_pan": {"offset": 0, "sign": 1, "min": -110, "max": 110},
        "shoulder_lift": {"offset": 0, "sign": 1, "min": -100, "max": 100},
        "elbow_flex": {"offset": 0, "sign": 1, "min": -100, "max": 100},
        "wrist_flex": {"offset": 0, "sign": 1, "min": -100, "max": 100},
        "wrist_roll": {"offset": 0, "sign": 1, "min": -150, "max": 150},
        "gripper": {"offset": 0, "sign": 1, "min": 0, "max": 90},
    },
    "poses": {
        "home": {"shoulder_pan": 0, "shoulder_lift": -90, "elbow_flex": 90, "wrist_flex": 45, "wrist_roll": 0, "gripper": 0},
        # gripper up and open above the deck edge, clear of the CNC gantry: start point for a pick
        "ready": {"shoulder_pan": 0, "shoulder_lift": -30, "elbow_flex": 40, "wrist_flex": 60, "wrist_roll": 0, "gripper": 40},
        # folded low and out of the CNC envelope and the camera view, gripper closed
        "rest": {"shoulder_pan": 0, "shoulder_lift": -100, "elbow_flex": 95, "wrist_flex": 50, "wrist_roll": 0, "gripper": 0},
    },
}


# ---------------------------------------------------------------------------- bus
class FeetechBus:
    def __init__(self, port: str, baud: int = 1_000_000, timeout: float = 0.02):
        import serial

        self.port = port
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.lock = threading.RLock()

    @staticmethod
    def packet(sid: int, instr: int, params: bytes | list[int] = b"") -> bytes:
        body = [sid, len(params) + 2, instr, *params]
        return bytes([0xFF, 0xFF, *body, (~sum(body)) & 0xFF])

    def _rx(self, timeout: float = 0.05) -> tuple[int, int, bytes] | None:
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.read(max(1, self.ser.in_waiting))
            i = buf.find(b"\xff\xff")
            if i >= 0 and len(buf) >= i + 4:
                sid, ln = buf[i + 2], buf[i + 3]
                if len(buf) >= i + 4 + ln:
                    pkt = buf[i + 2:i + 4 + ln]
                    if (~sum(pkt[:-1])) & 0xFF != pkt[-1]:
                        return None
                    return sid, pkt[2], bytes(pkt[3:-1])
        return None

    def _txrx(self, sid: int, instr: int, params=b"", reply=True):
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(self.packet(sid, instr, params))
            return self._rx() if reply else None

    def ping(self, sid: int) -> bool:
        r = self._txrx(sid, PING)
        return bool(r and r[0] == sid)

    def read(self, sid: int, addr: int, n: int) -> bytes:
        r = self._txrx(sid, READ, [addr, n])
        if not r or r[0] != sid or len(r[2]) < n:
            raise DeviceError(f"feetech read id{sid}@{addr} failed")
        return r[2][:n]

    def write(self, sid: int, addr: int, data: bytes) -> None:
        self._txrx(sid, WRITE, bytes([addr]) + data, reply=True)

    def sync_write(self, addr: int, n: int, data: dict[int, bytes]) -> None:
        params = bytearray([addr, n])
        for sid, d in data.items():
            params += bytes([sid]) + d[:n]
        self._txrx(BROADCAST, SYNC_WRITE, bytes(params), reply=False)

    # helpers
    def read_pos(self, sid: int) -> int:
        return struct.unpack("<H", self.read(sid, REG_PRESENT_POS, 2))[0] & 0x0FFF

    def write_goals(self, ticks: dict[int, int]) -> None:
        self.sync_write(REG_GOAL_POS, 2, {s: struct.pack("<H", max(0, min(4095, t))) for s, t in ticks.items()})

    def torque(self, sid: int, on: bool) -> None:
        self.write(sid, REG_TORQUE, bytes([1 if on else 0]))

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


class MockFeetechBus:
    """In-memory bus: positions follow goals instantly (the arm interpolates anyway)."""

    def __init__(self, port: str = "mock"):
        self.port = port
        self.lock = threading.RLock()
        self.pos = {i: CENTER for i in range(1, 7)}
        self.torq = {i: False for i in range(1, 7)}

    def ping(self, sid):
        return sid in self.pos

    def read_pos(self, sid):
        return self.pos[sid]

    def write_goals(self, ticks):
        for s, t in ticks.items():
            if self.torq.get(s):
                self.pos[s] = max(0, min(4095, t))

    def torque(self, sid, on):
        self.torq[sid] = bool(on)

    def close(self):
        pass


def probe_feetech(port: str) -> list[int]:
    try:
        bus = FeetechBus(port, timeout=0.01)
    except Exception:
        return []
    try:
        return [i for i in range(1, 7) if any(bus.ping(i) for _ in range(3))]
    finally:
        bus.close()


# ---------------------------------------------------------------------------- arm
class So101(Device):
    kind = "so101"
    unlocked = ("read",)

    def __init__(self, dev_id: str, port: str | None = None, mock: bool = False, info: dict | None = None):
        super().__init__(dev_id, mock, info)
        self.calib = load_json(f"{dev_id}.json", DEFAULT_CALIB)
        self.bus = MockFeetechBus() if mock or not port else FeetechBus(port)
        self.info.setdefault("port", port or "mock")
        if mock:  # start the mock at home
            for j, d in self.calib["poses"]["home"].items():
                self.bus.pos[IDS[j]] = self.deg_to_ticks(j, d)
        self._stop = threading.Event()
        self.moving = False
        self.torque_on = False
        self.q = self._read_q_safe()

    # conversions
    def deg_to_ticks(self, j: str, deg: float) -> int:
        c = self.calib["joints"][j]
        return int(round(CENTER + c["offset"] + c["sign"] * deg * TICKS_PER_REV / 360.0)) % TICKS_PER_REV

    def ticks_to_deg(self, j: str, t: int) -> float:
        c = self.calib["joints"][j]
        d = (t - CENTER - c["offset"]) % TICKS_PER_REV  # wrap: the zero pose may sit near the 0/4095 seam
        if d >= TICKS_PER_REV / 2:
            d -= TICKS_PER_REV
        return round(c["sign"] * d * 360.0 / TICKS_PER_REV, 2)

    def clamp(self, j: str, deg: float) -> float:
        c = self.calib["joints"][j]
        return max(c["min"], min(c["max"], float(deg)))

    def _read_q_safe(self) -> dict:
        q = {}
        for j in JOINTS:
            try:
                q[j] = self.ticks_to_deg(j, self.bus.read_pos(IDS[j]))
            except Exception as e:
                log.warning("%s read %s failed: %s", self.id, j, e)
                q[j] = getattr(self, "q", {}).get(j, 0.0)
        return q

    def set_torque(self, on: bool):
        for j in JOINTS:
            self.bus.torque(IDS[j], on)
        self.torque_on = bool(on)

    def move_to(self, target: dict, ms: float = 1000):
        unknown = set(target) - set(JOINTS)
        if unknown:
            raise DeviceError(f"unknown joints {sorted(unknown)}")
        if not self.torque_on:
            self.set_torque(True)
        start = self._read_q_safe()
        goal = {j: self.clamp(j, target.get(j, start[j])) for j in JOINTS}
        n = max(1, int(float(ms) / 1000.0 * RATE_HZ))
        self._stop.clear()
        self.moving = True
        t0 = time.monotonic()
        try:
            for k in range(1, n + 1):
                if self._stop.is_set():
                    break
                a = k / n
                a = a * a * (3 - 2 * a)  # smoothstep
                q = {j: start[j] + (goal[j] - start[j]) * a for j in JOINTS}
                self.bus.write_goals({IDS[j]: self.deg_to_ticks(j, q[j]) for j in JOINTS})
                self.q = {j: round(v, 2) for j, v in q.items()}
                dt = t0 + k / RATE_HZ - time.monotonic()
                if dt > 0:
                    time.sleep(dt)
        finally:
            self.moving = False
        return {"q": self.q, "stopped": self._stop.is_set()}

    def hold(self):
        self._stop.set()
        time.sleep(0.05)
        try:
            q = self._read_q_safe()
            if self.torque_on:
                self.bus.write_goals({IDS[j]: self.deg_to_ticks(j, q[j]) for j in JOINTS})
            self.q = q
        except Exception as e:
            log.warning("%s hold failed: %s", self.id, e)

    # actions
    def a_joints(self, q: dict, ms: float = 1000):
        return self.move_to(q, ms)

    def a_pose(self, name: str, ms: float = 1500):
        poses = self.calib.get("poses", {})
        if name not in poses and POSE_ALIASES.get(name) in poses:
            log.info("%s: pose '%s' is a legacy alias for '%s'", self.id, name, POSE_ALIASES[name])
            name = POSE_ALIASES[name]
        if name not in poses:
            raise DeviceError(f"unknown pose '{name}' (have {sorted(poses)})")
        return {"pose": name, **self.move_to(poses[name], ms)}

    def a_gripper(self, open: float, ms: float = 500):
        c = self.calib["joints"]["gripper"]
        o = max(0.0, min(1.0, float(open)))
        return self.move_to({"gripper": c["min"] + o * (c["max"] - c["min"])}, ms)

    def a_torque(self, on: bool):
        self._stop.set()
        self.set_torque(bool(on))
        return {"torque": self.torque_on}

    def a_read(self):
        with self.bus.lock:
            if not self.moving:
                self.q = self._read_q_safe()
        return {"q": self.q, "torque": self.torque_on}

    def a_stop(self):
        self.hold()
        return {"q": self.q}

    def state(self):
        return {"q": self.q, "moving": self.moving, "torque": self.torque_on}

    def safe_stop(self):
        self.hold()

    def close(self):
        self.bus.close()


# ---------------------------------------------------------------------------- calibrate CLI
def calibrate(dev_id: str, port: str | None, mock: bool = False) -> None:
    arm = So101(dev_id, port=port, mock=mock or not port)
    cal = arm.calib
    print(f"Calibrating {dev_id} on {arm.info['port']}. Torque OFF - move the arm by hand.")
    arm.set_torque(False)

    def ticks():
        return {j: arm.bus.read_pos(IDS[j]) for j in JOINTS}

    if input("Set zero offsets? Put arm in the mid/zero pose (all joints centred) then Enter, or 's' to skip: ").strip() != "s":
        for j, t in ticks().items():
            cal["joints"][j]["offset"] = t - CENTER
        print("offsets:", {j: cal["joints"][j]["offset"] for j in JOINTS})
    if input("Record limits? Enter, then sweep EVERY joint through its full safe range; Enter again to finish ('s' skips): ").strip() != "s":
        lo = {j: 1e9 for j in JOINTS}
        hi = {j: -1e9 for j in JOINTS}
        done = threading.Event()
        threading.Thread(target=lambda: (input(), done.set()), daemon=True).start()
        while not done.is_set():
            for j, t in ticks().items():
                d = arm.ticks_to_deg(j, t)
                lo[j], hi[j] = min(lo[j], d), max(hi[j], d)
            time.sleep(0.05)
        for j in JOINTS:
            if hi[j] > lo[j]:
                cal["joints"][j]["min"], cal["joints"][j]["max"] = round(lo[j], 1), round(hi[j], 1)
        print("limits:", {j: (cal["joints"][j]["min"], cal["joints"][j]["max"]) for j in JOINTS})
    for pose in POSES:
        if input(f"Move to pose '{pose}' and press Enter ('s' to skip): ").strip() == "s":
            continue
        cal["poses"][pose] = {j: arm.ticks_to_deg(j, t) for j, t in ticks().items()}
        print(pose, cal["poses"][pose])
    p = save_json(f"{dev_id}.json", cal)
    print("saved", p)
    arm.close()


def calibrate_step(dev_id: str, port: str, step: str) -> None:
    """Non-interactive calibration, one step per call (for shells without a TTY).
    step = "zero" | "limits:<seconds>" | "pose:<home|ready|rest>"; results merge into calib/<dev_id>.json."""
    arm = So101(dev_id, port=port, mock=False)
    cal = arm.calib
    arm.set_torque(False)

    def ticks():
        return {j: arm.bus.read_pos(IDS[j]) for j in JOINTS}

    try:
        if step == "zero":
            for j, t in ticks().items():
                cal["joints"][j]["offset"] = t - CENTER
            print("offsets:", {j: cal["joints"][j]["offset"] for j in JOINTS})
        elif step.startswith("limits"):
            secs = float(step.split(":", 1)[1]) if ":" in step else 45.0
            lo = {j: 1e9 for j in JOINTS}
            hi = {j: -1e9 for j in JOINTS}
            t_end = time.time() + secs
            print(f"recording limits for {secs:.0f}s - sweep every joint now", flush=True)
            while time.time() < t_end:
                for j, t in ticks().items():
                    if t is None:
                        continue
                    d = arm.ticks_to_deg(j, t)
                    lo[j], hi[j] = min(lo[j], d), max(hi[j], d)
                time.sleep(0.05)
            for j in JOINTS:
                if hi[j] > lo[j]:
                    cal["joints"][j]["min"], cal["joints"][j]["max"] = round(lo[j], 1), round(hi[j], 1)
            print("limits:", {j: (cal["joints"][j]["min"], cal["joints"][j]["max"]) for j in JOINTS})
        elif step.startswith("pose:"):
            pose = step.split(":", 1)[1]
            cal["poses"][pose] = {j: arm.ticks_to_deg(j, t) for j, t in ticks().items()}
            print(pose, cal["poses"][pose])
        else:
            raise SystemExit(f"unknown step {step!r}")
        print("saved", save_json(f"{dev_id}.json", cal))
    finally:
        arm.close()
