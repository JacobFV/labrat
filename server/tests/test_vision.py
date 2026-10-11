import asyncio
import base64
import io

import numpy as np
from PIL import Image

from labrat import lab, sim, vision
from labrat.hub import hub
from labrat.store import store

# image px of well centres A1, A12, H12, H1 (slightly keystoned, like a phone looking down at an angle)
CORNERS = [[100.0, 80.0], [520.0, 90.0], [530.0, 380.0], [90.0, 370.0]]
CAST = np.array([0.92, 1.0, 1.08])  # warm/cool camera colour cast to be white-balanced away


def synth_plate(colors: dict[str, tuple[int, int, int]], w=640, h=480) -> np.ndarray:
    H = vision.homography(CORNERS)
    img = np.full((h, w, 3), 60.0)  # dark deck
    yy, xx = np.mgrid[0:h, 0:w]
    # white plate body
    x0, y0 = vision.project(H, "A1")
    x1, y1 = vision.project(H, "H12")
    img[int(y0 - 25):int(y1 + 25), int(min(x0, 90) - 25):int(max(x1, 520) + 25)] = 245.0
    for r in "ABCDEFGH":
        for c in range(1, 13):
            wname = f"{r}{c}"
            cx, cy = vision.project(H, wname)
            m = (xx - cx) ** 2 + (yy - cy) ** 2 <= 12 ** 2
            img[m] = colors.get(wname, (245, 245, 245))
    img = np.clip(img * CAST, 0, 255).astype(np.uint8)
    return img


def to_b64(img: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode()


def test_homography_roundtrip():
    H = vision.homography(CORNERS)
    for wname, (x, y) in zip(vision.CORNER_WELLS, CORNERS):
        px, py = vision.project(H, wname)
        assert abs(px - x) < 1e-6 and abs(py - y) < 1e-6


def test_sample_well_white_balanced():
    target = (230, 120, 30)
    img = vision.decode(to_b64(synth_plate({"C5": target})))
    cal = vision.set_cal(CORNERS, 640, 480)
    s = vision.sample_well(img, cal, "C5", blank="H12")
    assert np.max(np.abs(np.array(s["rgb"]) - (np.array(target) * 245 / 245))) <= 8, s
    # without white balance the cast is visible
    raw = vision.sample_well(img, cal, "C5")
    assert abs(raw["rgb"][2] - target[2] * CAST[2]) <= 6
    # calibration done at another resolution still maps (scaled)
    small = np.asarray(Image.fromarray(img).resize((320, 240)))
    s2 = vision.sample_well(small, cal, "C5", blank="H12")
    assert np.max(np.abs(np.array(s2["rgb"]) - np.array(target))) <= 12, s2


def test_auto_observe_feeds_rsi():
    async def go():
        exp = await lab.create_experiment("make the most orange-looking well using red & yellow", 4)
        await lab.simulate_experiment(exp["id"])
        exp = store.get("experiments", exp["id"])
        vid = exp["best_variant_id"]
        run = store.put("runs", {"id": "run_vis1", "experiment_id": exp["id"], "variant_id": vid, "status": "done", "log": []})
        target, blank = vision._target_and_blank(run)
        assert blank and blank != target
        observed = (225, 95, 25)  # "real" result: redder than predicted
        hub.latest_frame = {"jpeg": to_b64(synth_plate({target: observed})), "w": 640, "h": 480, "ts": 1.0}
        vision.set_cal(CORNERS, 640, 480)
        res = await vision.auto_observe(run["id"], pose=True, settle_s=0.0)  # no bridge -> pose skipped
        assert res["ok"], res
        assert res["estimate"]["method"] == "homography" and res["estimate"]["well"] == target
        assert np.max(np.abs(np.array(res["estimate"]["rgb"]) - np.array(observed))) <= 8
        obs = [o for o in store.mem["observations"].values() if o["run_id"] == "run_vis1"]
        assert obs and obs[0]["source"] == "vision"
        assert res["rsi"]["error_before"] is not None
        assert any(e.get("kind") == "vision.observe" or e.get("type") == "vision.observe" or "vision" in str(e)
                   for e in store.mem["events"].values())

    try:
        asyncio.run(go())
    finally:
        hub.latest_frame = None


def test_skip_without_frame_or_cal():
    async def go():
        exp = await lab.create_experiment("make green from blue and yellow", 2)
        await lab.simulate_experiment(exp["id"])
        exp = store.get("experiments", exp["id"])
        store.put("runs", {"id": "run_vis2", "experiment_id": exp["id"], "variant_id": exp["best_variant_id"], "status": "done", "log": []})
        hub.latest_frame = None
        res = await vision.auto_observe("run_vis2", settle_s=0.0)
        assert not res["ok"] and res["skipped"] == "no phone frame"

    asyncio.run(go())


def test_requires_fresh_frame_after_photo_step():
    """Static overhead phone: no arm pose; a frame older than the photo step must not be used."""
    async def go():
        exp = await lab.create_experiment("make green from blue and yellow", 2)
        await lab.simulate_experiment(exp["id"])
        exp = store.get("experiments", exp["id"])
        store.put("runs", {"id": "run_vis3", "experiment_id": exp["id"], "variant_id": exp["best_variant_id"], "status": "done", "log": []})
        hub.latest_frame = {"jpeg": to_b64(synth_plate({})), "w": 640, "h": 480, "ts": 100.0}
        res = await vision.auto_observe("run_vis3", fresh_after=200.0, wait_s=0.1)
        assert not res["ok"] and res["skipped"] == "no fresh phone frame"
        assert await vision.photo_pose() is False  # LABRAT_PHOTO_POSE unset -> no arm motion

    try:
        asyncio.run(go())
    finally:
        hub.latest_frame = None
