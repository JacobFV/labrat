"""Tappet ESP32 arms (JSON lines @115200) + the servo pipette on its PCA9685 channel 8."""
from __future__ import annotations

import json
import threading
import time

from .common import Device, DeviceError, load_json, log, save_json
from .tappet_client import MOTION_CMDS, BodyClient, BodyError

PIPETTE_CH = 8


def probe_tappet(port: str) -> dict | None:
    import serial

    try:
        with serial.Serial(port, 115200, timeout=0.2) as s:
            time.sleep(1.2)  # ESP32 may reset on open
            s.reset_input_buffer()
            for _ in range(2):
                s.write(b'{"id":1,"cmd":"hello"}\n')
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    line = s.readline()
                    if b"tappet-fw" in line:
                        try:
                            return json.loads(line)
                        except ValueError:
                            return {"fw": "tappet-fw"}
    except Exception:
        return None
    return None


class Tappet(Device):
    kind = "tappet"
    unlocked = ("raw",)  # firmware serializes; allow stop/status while a motion runs

    def __init__(self, dev_id: str, port: str | None = None, mock: bool = False, info: dict | None = None):
        super().__init__(dev_id, mock, info)
        self.client: BodyClient | None = None
        self.mock_state = {"enabled": False, "last": None, "busy": False}
        self.info.setdefault("port", port or "mock")
        if not mock and port:
            # heartbeat ping every 1 s keeps the firmware's 5 s watchdog fed
            self.client = BodyClient.connect(port, 115200, heartbeat_s=1.0)
            deadline = time.monotonic() + 6
            while True:
                try:
                    self.info["hello"] = self.client.hello()
                    break
                except BodyError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.5)

    def request(self, cmd: str, **args):
        if self.client is None:
            if cmd in ("enable",):
                self.mock_state["enabled"] = True
            elif cmd in ("relax",):
                self.mock_state["enabled"] = False
            elif cmd == "servo_raw" and not self.mock_state["enabled"]:
                return {"ok": False, "err": "not_enabled", "msg": "send enable first"}
            if cmd in MOTION_CMDS:
                self.mock_state["busy"] = True
                time.sleep(min(float(args.get("ms") or 300), 3000) / 1000.0)
                self.mock_state["busy"] = False
            self.mock_state["last"] = {"cmd": cmd, **args}
            return {"ok": True, "mock": True, "cmd": cmd}
        wait = cmd in MOTION_CMDS
        try:
            return self.client.request(cmd, wait=wait, wait_timeout=60, **args)
        except BodyError as e:
            raise DeviceError(str(e))

    def a_raw(self, cmd: str, **args):
        args.pop("id", None)
        return self.request(cmd, **args)

    def state(self):
        if self.client is None:
            return {"connected": True, **self.mock_state}
        return {"connected": self.client.connected, "status": self.client.last_status}

    def safe_stop(self):
        try:
            self.request("stop")
        except Exception as e:
            log.warning("%s stop failed: %s", self.id, e)

    def close(self):
        if self.client:
            self.client.close()


DEFAULT_PIP = {"rest_us": 667, "full_us": 2283, "first_stop": 0.812,  # from cad/README.md cam table (MG90S)
               "max_ul": 200, "settle_ms": 400}


class Pipette(Device):
    kind = "servo_pipette"

    def __init__(self, dev_id: str = "pipette", host: Tappet | None = None, info: dict | None = None):
        mock = host is None or host.client is None
        super().__init__(dev_id, mock, info)
        self.host = host
        self.cal = load_json("pipette.json", DEFAULT_PIP)
        self.info.update(host=host.id if host else None, channel=PIPETTE_CH)
        self.frac = 0.0
        self.vol_ul = 0.0
        self._enabled = False
        self._lk = threading.Lock()

    def us(self, frac: float) -> int:
        c = self.cal
        return int(round(c["rest_us"] + (c["full_us"] - c["rest_us"]) * frac))

    def _set(self, frac: float):
        frac = max(0.0, min(1.0, float(frac)))
        if not self.mock:
            if not self._enabled:
                self.host.request("enable")
                self._enabled = True
            r = self.host.request("servo_raw", ch=PIPETTE_CH, us=self.us(frac))
            if not r.get("ok", True):
                raise DeviceError(f"servo_raw failed: {r}")
        self.frac = frac
        time.sleep(self.cal["settle_ms"] / 1000.0)

    def ul_to_frac(self, ul: float) -> float:
        return float(ul) / float(self.cal["max_ul"]) * self.cal["first_stop"]

    def a_set(self, frac: float):
        self._set(frac)
        return self.state()

    def a_aspirate(self, ul: float):
        ul = float(ul)
        if ul <= 0 or self.vol_ul + ul > self.cal["max_ul"] + 1e-6:
            raise DeviceError(f"aspirate {ul} uL exceeds capacity ({self.vol_ul}/{self.cal['max_ul']})")
        fs = self.cal["first_stop"]
        if self.vol_ul == 0 and self.frac < fs - 1e-3:
            # Should be pressed above the liquid (server: set{frac:first_stop} before descending).
            self._set(fs)
        self._set(self.frac - self.ul_to_frac(ul))
        self.vol_ul = round(self.vol_ul + ul, 2)
        return self.state()

    def a_dispense(self, ul: float | None = None):
        fs = self.cal["first_stop"]
        if ul is None or float(ul) >= self.vol_ul:
            self._set(fs)
            self.vol_ul = 0.0
        else:
            self._set(min(fs, self.frac + self.ul_to_frac(ul)))
            self.vol_ul = round(self.vol_ul - float(ul), 2)
        return self.state()

    def a_blowout(self):
        self._set(1.0)
        self.vol_ul = 0.0
        return self.state()

    def a_rest(self):
        self._set(0.0)
        return self.state()

    def state(self):
        return {"frac": round(self.frac, 3), "vol_ul": self.vol_ul, "us": self.us(self.frac)}

    def safe_stop(self):
        try:
            self._set(0.0)
        except Exception as e:
            log.warning("pipette rest failed: %s", e)
