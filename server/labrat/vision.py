"""Automatic observation: phone photo -> target-well RGB -> learn.observe (closes the RSI loop without a human).

Two estimators:
 1. Calibrated: a camera->plate homography from 4 clicked well centres (A1, A12, H12, H1) stored as doc `camera_cal`.
    Sample a small disk at the projected target-well centre (median), white-balanced against a blank well.
 2. LLM vision: ask Claude for the target well's RGB as JSON.
Neither available -> skip (operator can still rate/observe manually).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import sim
from .config import ANTHROPIC_MODEL, LLM_PROVIDER, env, log
from .hub import hub
from .store import store
from .telemetry import track

CAL_ID = "camera_cal"
CORNER_WELLS = ("A1", "A12", "H12", "H1")
BLANK_PREF = ("H12", "H1", "A12", "A1", "H11", "G12")
BG = 245.0  # sim's white-plate background level

router = APIRouter()


# ---------------- calibration storage ----------------
def _coll() -> dict:
    return store.mem.setdefault(CAL_ID, {})


def get_cal() -> dict | None:
    c = _coll().get(CAL_ID)
    if c is None and store.db is not None:
        try:
            c = store.db[CAL_ID].find_one({"id": CAL_ID}, {"_id": 0})
            if c:
                _coll()[CAL_ID] = c
        except Exception as e:  # noqa: BLE001
            log.warning(f"camera_cal load failed: {e}")
    return c


def set_cal(corners: list[list[float]], image_w: int, image_h: int) -> dict:
    if len(corners) != 4 or any(len(p) != 2 for p in corners):
        raise ValueError("need 4 corners [[x,y],...] in order A1, A12, H12, H1")
    _coll()
    doc = {"id": CAL_ID, "corners": [[float(x), float(y)] for x, y in corners], "image_w": int(image_w), "image_h": int(image_h),
           "wells": list(CORNER_WELLS), "H": homography(corners).tolist(), "created_at": time.time()}
    store.put(CAL_ID, doc)
    store.event("camera_cal", corners=doc["corners"], image_w=image_w, image_h=image_h)
    return doc


# ---------------- geometry ----------------
def _plate_cr(well: str) -> tuple[float, float]:
    x, y = sim.well_xy(well, sim.PLATE)
    ox, oy = sim.PLATE["origin"]
    p = sim.PLATE["pitch"]
    return (x - ox) / p, (y - oy) / p  # (col, row) in pitch units


def homography(corners_img: list[list[float]]) -> np.ndarray:
    """DLT: plate (col,row) of A1,A12,H12,H1 -> image px."""
    src = [_plate_cr(w) for w in CORNER_WELLS]
    A = []
    for (u, v), (x, y) in zip(src, corners_img):
        A.append([-u, -v, -1, 0, 0, 0, u * x, v * x, x])
        A.append([0, 0, 0, -u, -v, -1, u * y, v * y, y])
    _, _, vt = np.linalg.svd(np.array(A, float))
    H = vt[-1].reshape(3, 3)
    return H / H[2, 2]


def project(H: np.ndarray, well: str) -> tuple[float, float]:
    c, r = _plate_cr(well)
    p = np.asarray(H) @ np.array([c, r, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def decode(jpeg_b64: str) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(base64.b64decode(jpeg_b64))).convert("RGB"))


def _disk_median(img: np.ndarray, cx: float, cy: float, r: float) -> np.ndarray | None:
    h, w = img.shape[:2]
    r = max(2.0, r)
    x0, x1 = int(max(0, cx - r)), int(min(w, cx + r + 1))
    y0, y1 = int(max(0, cy - r)), int(min(h, cy + r + 1))
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    m = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    px = img[y0:y1, x0:x1][m]
    return np.median(px, axis=0) if len(px) else None


def sample_well(img: np.ndarray, cal: dict, well: str, blank: str | None = None) -> dict:
    """Median RGB in a disk at the projected well centre, optionally white-balanced to `blank` (-> BG grey)."""
    H = np.array(cal["H"], float)
    sx = img.shape[1] / max(1, cal.get("image_w") or img.shape[1])
    sy = img.shape[0] / max(1, cal.get("image_h") or img.shape[0])
    S = np.diag([sx, sy, 1.0])
    Hs = S @ H
    cx, cy = project(Hs, well)
    # radius ~ 30% of local well pitch in image px
    c, r = _plate_cr(well)
    p2 = Hs @ np.array([c + 1, r, 1.0])
    pitch_px = float(np.hypot(p2[0] / p2[2] - cx, p2[1] / p2[2] - cy))
    rad = 0.3 * pitch_px
    raw = _disk_median(img, cx, cy, rad)
    if raw is None:
        raise ValueError(f"well {well} projects outside the image ({cx:.0f},{cy:.0f})")
    out = {"well": well, "center": [round(cx, 1), round(cy, 1)], "radius": round(rad, 1), "raw_rgb": [int(round(v)) for v in raw]}
    rgb = raw.astype(float)
    if blank:
        ref = _disk_median(img, *project(Hs, blank), rad)
        if ref is not None and np.all(ref > 20):
            gain = BG / ref.astype(float)
            rgb = rgb * gain
            out.update(blank=blank, blank_rgb=[int(round(v)) for v in ref], wb_gain=[round(float(g), 3) for g in gain])
    out["rgb"] = [int(np.clip(round(v), 0, 255)) for v in rgb]
    return out


# ---------------- LLM fallback ----------------
async def llm_rgb(jpeg_b64: str, well: str) -> list[int] | None:
    if LLM_PROVIDER != "anthropic" or not env("ANTHROPIC_API_KEY"):
        return None
    from .agent import _anthropic

    prompt = (f"This photo shows a 96-well plate (rows A-H, columns 1-12) on a lab deck. Estimate the colour of the liquid in well "
              f"{well} as seen from above, white-balanced so an empty well on the white plate reads about (245,245,245). "
              'Reply with JSON only: {"rgb":[r,g,b],"confidence":0..1}')
    msg = await _anthropic().messages.create(model=ANTHROPIC_MODEL, max_tokens=200, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpeg_b64}},
        {"type": "text", "text": prompt}]}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    rgb = json.loads(m.group(0)).get("rgb")
    if not (isinstance(rgb, list) and len(rgb) == 3):
        return None
    return [int(np.clip(float(v), 0, 255)) for v in rgb]


# ---------------- the hook ----------------
def _target_and_blank(run: dict) -> tuple[str, str | None]:
    from . import lab

    exp = store.get("experiments", run["experiment_id"])
    v = lab.variant(exp, run["variant_id"])
    base = sim.simulate(v["protocol"], exp["goal"], record_frames=False)
    target = (base.get("best_well") or "A1").upper()
    used = {k.upper() for k, w in (base.get("wells") or {}).items() if (w or {}).get("vol", 0) > 0}
    blank = next((b for b in BLANK_PREF if b not in used and b != target), None)
    return target, blank


async def estimate(run: dict, frame: dict) -> dict | None:
    target, blank = _target_and_blank(run)
    cal = get_cal()
    if cal:
        img = await asyncio.get_running_loop().run_in_executor(None, decode, frame["jpeg"])
        s = sample_well(img, cal, target, blank)
        return {"method": "homography", "target": target, **s}
    rgb = await llm_rgb(frame["jpeg"], target)
    if rgb:
        return {"method": "llm", "target": target, "well": target, "rgb": rgb}
    return None


async def auto_observe(run_id: str, pose: bool = True, settle_s: float = 1.0, rating: int | None = None) -> dict:
    """Pose the phone arm, grab the newest frame, estimate the well RGB and feed learn.observe(source='vision')."""
    from . import learn

    run = store.get("runs", run_id)
    if not run:
        raise ValueError(f"no run {run_id}")
    t0 = time.time()
    if pose and hub.bridge is not None and "so_a" in hub.devices:
        try:
            await hub.cmd("so_a", "pose", {"name": "look_plate"}, timeout=20)
        except Exception as e:  # noqa: BLE001
            log.warning(f"vision: look_plate failed: {e}")
    await asyncio.sleep(settle_s)
    frame = hub.latest_frame
    if not frame or not frame.get("jpeg"):
        res = {"ok": False, "skipped": "no phone frame"}
    else:
        try:
            est = await estimate(run, frame)
        except Exception as e:  # noqa: BLE001
            est, res = None, {"ok": False, "error": str(e)}
        else:
            res = {"ok": False, "skipped": "no camera_cal and no vision LLM"}
        if est:
            out = await learn.observe(run_id, rating, est["rgb"], est["well"], source="vision")
            res = {"ok": True, "estimate": est, "rsi": {k: out.get(k) for k in ("v", "accepted", "error_before", "error_after",
                                                                                "run_error_before", "run_error_after", "promoted_procedure")}}
    res.update(run_id=run_id, frame_ts=(frame or {}).get("ts"), dt=round(time.time() - t0, 2))
    run["vision"] = res
    store.event("vision.observe", **{k: v for k, v in res.items() if k != "rsi"})
    track("vision.observe", input=f"run {run_id}", output=json.dumps(res, default=str)[:2000], props={"run_id": run_id, "ok": res["ok"]})
    txt = (f"vision: saw {res['estimate']['well']} rgb{tuple(res['estimate']['rgb'])} via {res['estimate']['method']}"
           if res["ok"] else f"vision: auto-observe skipped: {res.get('skipped') or res.get('error')}")
    await hub.ui({"t": "log", "src": "vision", "level": "info" if res["ok"] else "warn", "text": txt})
    return res


# ---------------- REST ----------------
class CalIn(BaseModel):
    corners: list[list[float]]
    image_w: int
    image_h: int


@router.post("/api/camera_cal")
async def post_cal(body: CalIn):
    try:
        return set_cal(body.corners, body.image_w, body.image_h)
    except (ValueError, np.linalg.LinAlgError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/camera_cal")
async def read_cal():
    return get_cal() or {"id": CAL_ID, "corners": None}


@router.get("/api/camera_frame")
async def camera_frame():
    f = hub.latest_frame
    if not f:
        raise HTTPException(404, "no phone frame yet")
    return {"jpeg": f["jpeg"], "w": f.get("w"), "h": f.get("h"), "ts": f.get("ts")}


@router.post("/api/runs/{run_id}/auto_observe")
async def post_auto_observe(run_id: str, pose: bool = False):
    try:
        return await auto_observe(run_id, pose=pose, settle_s=0.0)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
