"""USB digital microscope on the head's tool rail (hardware/tool-head TM-110): the generic "1000x 8-LED" UVC camera.

Device ``microscope`` (kind ``usb_microscope``):
    snap {width?, height?}  -> {image_b64, mime, w, h, ts}   one frame (JPEG with OpenCV, else PNG from the mock)
    info {}                 -> camera index, resolution
The CNC puts the lens over a target using the tool offset (server deck_cal tools.microscope); focus is the
microscope's own wheel (set it once at the taught height). OpenCV is optional: ``uv sync --extra microscope``
(opencv-python-headless). Without it, or without a camera, the device is a mock that returns a synthetic frame.
"""
from __future__ import annotations

import base64
import math
import os
import struct
import threading
import time
import zlib

from .common import Device, DeviceError, load_json

DEFAULT_MIC = {"index": None, "width": 1280, "height": 720, "warmup_frames": 3}


def _png(w: int, h: int, pix) -> bytes:
    """minimal RGB PNG encoder (mock frames, no dependencies)."""
    raw = b"".join(b"\x00" + b"".join(bytes(pix(x, y)) for x in range(w)) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def find_camera(max_index: int = 6) -> int | None:
    """first UVC index that delivers a frame (the laptop's own webcam is usually 0: set calib index explicitly)."""
    try:
        import cv2
    except ImportError:
        return None
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        try:
            if cap.isOpened() and cap.read()[0]:
                return i
        finally:
            cap.release()
    return None


class Microscope(Device):
    kind = "usb_microscope"

    def __init__(self, dev_id: str = "microscope", index: int | None = None, mock: bool = False, info: dict | None = None):
        self.cal = load_json("microscope.json", DEFAULT_MIC)
        idx = index if index is not None else self.cal.get("index")
        if idx is None and not mock:
            idx = find_camera()
        super().__init__(dev_id, mock or idx is None, info)
        self.index = idx
        self._cap = None
        self._lk = threading.Lock()
        self.last = None
        self.info.update(index=idx, width=self.cal["width"], height=self.cal["height"])
        if self.mock and not mock:
            self.info["reason"] = "no camera / OpenCV"

    def _open(self):
        import cv2
        if self._cap is None:
            cap = cv2.VideoCapture(int(self.index))
            if not cap.isOpened():
                raise DeviceError(f"microscope: cannot open camera {self.index}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.cal["width"]))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.cal["height"]))
            self._cap = cap
        return self._cap

    def a_snap(self, width: int | None = None, height: int | None = None):
        with self._lk:
            if self.mock:
                w, h = int(width or 160), int(height or 120)
                t = time.time()

                def pix(x, y):          # a soft disc of "cells" on a pale field
                    r = math.hypot(x - w / 2, y - h / 2) / (min(w, h) / 2)
                    c = 0.5 + 0.5 * math.sin(x * 0.35 + t) * math.cos(y * 0.31)
                    v = 225 - int(70 * c * max(0.0, 1 - r))
                    return (v, max(0, v - 20), min(255, v + 10))
                data, mime = _png(w, h, pix), "image/png"
            else:
                import cv2
                cap = self._open()
                for _ in range(int(self.cal.get("warmup_frames", 3))):
                    cap.read()                   # UVC auto-exposure settles over a few frames
                ok, frame = cap.read()
                if not ok:
                    raise DeviceError("microscope: no frame")
                if width and height:
                    frame = cv2.resize(frame, (int(width), int(height)))
                h, w = frame.shape[:2]
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
                data, mime = buf.tobytes(), "image/jpeg"
            self.last = {"w": w, "h": h, "ts": time.time()}
            return {"image_b64": base64.b64encode(data).decode(), "mime": mime, **self.last}

    def a_info(self):
        return dict(self.info)

    def state(self):
        return {"last": self.last, "index": self.index}

    def close(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None
