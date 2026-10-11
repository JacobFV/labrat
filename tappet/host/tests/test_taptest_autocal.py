import math

import pytest

from tappet.bridge import sim_bridge
from tappet.calibration import auto_calibrate, default_calibration, uv_to_world
from tappet.taptest import run_taptest, write_report


def _setup(tmp_path, **kw):
    srv, br = sim_bridge(port=0, speed=60, calib_path=tmp_path / "cal.json", log=lambda s: None,
                         report_dir=tmp_path, **kw)
    br.start()
    b = br.body()
    b.enable()
    return srv, br, b


def test_taptest_sim_noise_free(tmp_path):
    srv, br, b = _setup(tmp_path)
    try:
        res = run_taptest(b, br.touch_log, br.calibration, n=100, seed=1)
        assert res["n"] == 100
        assert res["registration_rate"] == 1.0
        assert res["max_mm"] < 0.05
        assert res["cycle_s"] > 0
        csv_p, json_p = write_report(res, tmp_path, "t")
        assert csv_p.read_text().count("\n") == 101
        assert json_p.exists()
    finally:
        br.close()
        srv.close()


def test_taptest_sim_with_noise(tmp_path):
    srv, br, b = _setup(tmp_path, noise_deg=0.3, backlash_deg=0.5)
    try:
        res = run_taptest(b, br.touch_log, br.calibration, n=20, seed=2)
        assert res["registration_rate"] >= 0.9
        assert 0.05 < res["mean_mm"] < 5
    finally:
        br.close()
        srv.close()


def test_autocal_recovers_offset_and_rotation(tmp_path):
    srv, br, b = _setup(tmp_path, offset=(3.0, -4.0), rot_deg=2.0)
    try:
        cal0 = br.calibration
        before = run_taptest(b, br.touch_log, cal0, n=10, seed=3)
        assert before["mean_mm"] > 3.0
        res = auto_calibrate(b, br.touch_log, cal0)
        cal = res["calibration"]
        assert cal["source"] == "auto"
        assert res["rms_mm"] < 0.05
        # recovered screen centre = nominal + offset
        cx, cy = uv_to_world(cal, 0.5, 0.5)
        assert (cx, cy) == pytest.approx((3.0, 156.0), abs=0.05)
        # recovered rotation of the u axis
        x0, y0 = uv_to_world(cal, 0, 0.5)
        x1, y1 = uv_to_world(cal, 1, 0.5)
        assert math.degrees(math.atan2(y1 - y0, x1 - x0)) == pytest.approx(2.0, abs=0.05)
        after = run_taptest(b, br.touch_log, cal, n=20, seed=4)
        assert after["registration_rate"] == 1.0
        assert after["max_mm"] < 0.1
    finally:
        br.close()
        srv.close()
