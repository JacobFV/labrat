"""Thread-safe log of touches reported by the target phone (touchpad.html) or the simulator's
virtual touchscreen.  Calibration and the tap test consume it."""
from __future__ import annotations

import threading
import time
from typing import Optional


class TouchLog:
    def __init__(self, maxlen: int = 5000):
        self._events: list[dict] = []
        self._seq = 0
        self._cv = threading.Condition()
        self._maxlen = maxlen

    @property
    def last_seq(self) -> int:
        with self._cv:
            return self._seq

    def add(self, x: float, y: float, w: float, h: float, t: Optional[float] = None,
            **extra) -> dict:
        if w <= 0 or h <= 0:
            raise ValueError("w and h must be > 0")
        with self._cv:
            self._seq += 1
            ev = {
                "seq": self._seq,
                "x": float(x), "y": float(y), "w": float(w), "h": float(h),
                "t": float(t) if t is not None else time.time() * 1000.0,
                "u": float(x) / float(w), "v": float(y) / float(h),
                "server_t": time.time() * 1000.0,
            }
            ev.update(extra)
            self._events.append(ev)
            if len(self._events) > self._maxlen:
                del self._events[: len(self._events) - self._maxlen]
            self._cv.notify_all()
            return ev

    def since(self, seq: int) -> list[dict]:
        with self._cv:
            return [dict(e) for e in self._events if e["seq"] > seq]

    def wait_for(self, since: int, timeout: float) -> Optional[dict]:
        """Block until an event with seq > since exists; return the first such event or None."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                for e in self._events:
                    if e["seq"] > since:
                        return dict(e)
                rem = deadline - time.monotonic()
                if rem <= 0:
                    return None
                self._cv.wait(rem)

    def last_size(self) -> Optional[list[float]]:
        with self._cv:
            if not self._events:
                return None
            e = self._events[-1]
            return [e["w"], e["h"]]
