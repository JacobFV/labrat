import numpy as np
import pytest

from tappet.calibration import (apply_h, camera_homography, default_calibration, homography_dlt,
                                merge_calibration, rigid_screen_h, uv_to_world, world_to_uv)


def test_dlt_exact_and_noisy():
    rng = np.random.default_rng(0)
    H = np.array([[60.0, 3.0, -30.0], [-2.0, -140.0, 230.0], [0.001, 0.002, 1.0]])
    src = rng.uniform(0, 1, (20, 2))
    dst = apply_h(H, src)
    He = homography_dlt(src, dst)
    assert np.allclose(He, H, atol=1e-6)
    noisy = dst + rng.normal(0, 0.1, dst.shape)
    He = homography_dlt(src, noisy)
    assert np.abs(apply_h(He, src) - dst).max() < 0.3
    with pytest.raises(ValueError):
        homography_dlt(src[:3], dst[:3])


def test_default_calibration_geometry():
    cal = default_calibration()
    w, l = cal["device"]["screen_w"], cal["device"]["screen_l"]
    assert uv_to_world(cal, 0.5, 0.5) == pytest.approx((0, 160))
    # top-left of the screen: robot's left (-x), far from robot (+y)
    assert uv_to_world(cal, 0, 0) == pytest.approx((-w / 2, 160 + l / 2))
    assert uv_to_world(cal, 1, 1) == pytest.approx((w / 2, 160 - l / 2))
    assert world_to_uv(cal, *uv_to_world(cal, 0.3, 0.8)) == pytest.approx((0.3, 0.8))


def test_rigid_rotation():
    H = rigid_screen_h(0, 160, 60, 140, rot_deg=90)
    # u axis (+x) rotated 90 deg CCW -> +y
    p0, p1 = apply_h(H, [0.5, 0.5]), apply_h(H, [1.0, 0.5])
    assert (p1 - p0) == pytest.approx([0, 30])


def test_camera_homography_and_merge():
    corners = [[100, 50], [500, 60], [520, 900], [90, 880]]
    H = camera_homography(corners)
    assert apply_h(H, corners) == pytest.approx(np.array([[0, 0], [1, 0], [1, 1], [0, 1]]), abs=1e-9)
    cal = default_calibration()
    new = merge_calibration(cal, {"camera_corners": corners, "camera_size": [640, 960]})
    assert new["H_ic"] is not None and new["camera_size"] == [640, 960]
    assert new["H_sw"] == cal["H_sw"]
    new2 = merge_calibration(new, {"surface_z": 12.0})
    assert new2["surface_z"] == 12.0 and new2["source"] == "manual"
    assert merge_calibration(new2, {"reset": True})["source"] == "default"
    with pytest.raises(ValueError):
        merge_calibration(cal, {"H_sw": [[0, 0, 0], [0, 0, 0], [0, 0, 0]]})
