import json
import math
import random

import pytest

from tappet.config import find_config_path, load_config
from tappet.kinematics import Kinematics, KinematicsError, dump_vectors, generate_vectors

KIN = Kinematics(load_config())
VEC = json.loads((find_config_path().parent / "kinematics_vectors.json").read_text())["vectors"]


@pytest.mark.parametrize("v", VEC, ids=[f"{v['arm']}{v['xyz']}" for v in VEC])
def test_vectors(v):
    q, err = KIN.try_ik(v["arm"], *v["xyz"])
    assert err == v.get("err")
    if "q" in v:
        assert q is not None
        # xyz in the file is rounded to 4 decimals -> q agrees to ~1e-3 deg
        for a, b in zip(q, v["q"]):
            assert a == pytest.approx(b, abs=2e-3)
        fk = KIN.fk(v["arm"], v["q"])
        for a, b in zip(fk, v["fk"]):
            assert a == pytest.approx(b, abs=1e-5)
        for a, b in zip(fk, v["xyz"]):
            assert a == pytest.approx(b, abs=1e-3)


def test_gen_vectors_reproduces_file():
    path = find_config_path().parent / "kinematics_vectors.json"
    assert dump_vectors(generate_vectors()) == path.read_text()


def test_round_trip_random():
    rng = random.Random(1)
    n_ok = 0
    for _ in range(2000):
        arm = rng.choice("LR")
        x, y, z = rng.uniform(-60, 60), rng.uniform(60, 260), rng.uniform(0, 80)
        q, err = KIN.try_ik(arm, x, y, z)
        if err == "unreachable":
            continue
        p = KIN.fk(arm, q)
        assert math.dist(p, (x, y, z)) < 1e-6
        n_ok += 1
    assert n_ok > 1000


def test_fk_ik_round_trip_from_joints():
    rng = random.Random(2)
    for _ in range(500):
        arm = rng.choice("LR")
        names = KIN.arm_joint_names(arm)
        q = [rng.uniform(KIN.joints[n]["min"], KIN.joints[n]["max"]) for n in names]
        if KIN.limit_violation(arm, q):
            continue
        p = KIN.fk(arm, q)
        q2 = KIN.ik(arm, *p)
        # elbow-up solution is unique when theta1 > theta2 (always true within linkage limits)
        assert q2 == pytest.approx(q, abs=1e-6)


def test_limit_error_reports_q():
    with pytest.raises(KinematicsError) as e:
        KIN.ik("L", -85, 60, 13.8)     # straight in front of the base: elbow folds past -115
    assert e.value.code == "limit" and e.value.q is not None
    q, err = KIN.try_ik("L", -85, 40, 300)
    assert err == "unreachable" and q is None


def test_servo_mapping():
    j = KIN.joints["L_YAW"]
    assert KIN.joint_to_servo("L_YAW", j["neutral"]) == pytest.approx(j["s_zero"])
    assert KIN.joint_to_us("L_YAW", j["neutral"]) == pytest.approx(1500)
    # gear 1.5: 10 deg of yaw = 15 deg of servo
    assert KIN.joint_to_servo("L_YAW", j["neutral"] + 10) == pytest.approx(105)
    # mirrored dir -1
    assert KIN.joint_to_servo("R_SHOULDER", 70) == pytest.approx(80)
    # joint clamp then servo clamp
    assert KIN.joint_to_servo("L_SHOULDER", 999) == pytest.approx(170)
    for n in KIN.joints:
        for q in (KIN.joints[n]["min"], KIN.joints[n]["home"], KIN.joints[n]["max"]):
            s = KIN.joint_to_servo(n, q)
            if 0 < s < 180:
                assert KIN.servo_to_joint(n, s) == pytest.approx(q)
                assert KIN.us_to_joint(n, KIN.joint_to_us(n, q)) == pytest.approx(q)


def test_home_and_park_poses_valid():
    for pose in ("home", "park"):
        p = KIN.pose(pose)
        for arm in "LR":
            q = [p[n] for n in KIN.arm_joint_names(arm)]
            assert KIN.limit_violation(arm, q) is None
            assert 0 <= KIN.joint_to_servo(KIN.arm_joint_names(arm)[0], q[0]) <= 180
