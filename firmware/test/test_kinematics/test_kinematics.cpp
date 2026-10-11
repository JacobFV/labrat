// IK/FK against the shared vectors in config/kinematics_vectors.json (SPEC section 3),
// plus joint mapping and min-jerk profile checks.
#include <ArduinoJson.h>
#include <unity.h>

#include <string>

#include "../support/fake_hal.h"
#include "joint_map.h"
#include "kinematics.h"
#include "minjerk.h"

using namespace tappet;
using namespace tappet_test;

void setUp() {}
void tearDown() {}

static void test_vectors() {
  const std::string s = readConfigFile("kinematics_vectors.json");
  TEST_ASSERT_TRUE_MESSAGE(!s.empty(), "config/kinematics_vectors.json not found");
  JsonDocument doc;
  TEST_ASSERT_FALSE(deserializeJson(doc, s));
  JsonArray vecs = doc["vectors"];
  TEST_ASSERT_TRUE(vecs.size() > 0);
  const Calibration cal = Calibration::defaults();
  int checked = 0, errs = 0;
  for (JsonObject v : vecs) {
    const int arm = armIndex(v["arm"].as<const char*>());
    TEST_ASSERT_TRUE(arm >= 0);
    const Vec3 p{v["xyz"][0].as<double>(), v["xyz"][1].as<double>(), v["xyz"][2].as<double>()};
    ArmQ q;
    const IkStatus st = armIk(cal, arm, p, q);
    char m[160];
    if (v["err"].is<const char*>()) {
      snprintf(m, sizeof(m), "vector %d expected err %s", checked, v["err"].as<const char*>());
      TEST_ASSERT_EQUAL_STRING_MESSAGE(v["err"].as<const char*>(), ikStatusCode(st), m);
      ++errs;
    } else {
      snprintf(m, sizeof(m), "vector %d (%s %.3f,%.3f,%.3f) status %s", checked, v["arm"].as<const char*>(), p.x,
               p.y, p.z, ikStatusCode(st));
      TEST_ASSERT_EQUAL_STRING_MESSAGE("ok", ikStatusCode(st), m);
    }
    if (v["q"].is<JsonArray>()) {
      const ArmQ qe{v["q"][0].as<double>(), v["q"][1].as<double>(), v["q"][2].as<double>()};
      // IK reproduces the reference joint angles.
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, qe.yaw, q.yaw, m);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, qe.th1, q.th1, m);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, qe.th2, q.th2, m);
      // FK of the reference angles reproduces the reference fk point.
      const Vec3 f = armFk(cal.arms[arm], qe);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, v["fk"][0].as<double>(), f.x, m);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, v["fk"][1].as<double>(), f.y, m);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, v["fk"][2].as<double>(), f.z, m);
      // Round trip FK(IK(p)) == p.
      const Vec3 rt = armFk(cal.arms[arm], q);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, p.x, rt.x, m);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, p.y, rt.y, m);
      TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-3, p.z, rt.z, m);
    }
    ++checked;
  }
  TEST_ASSERT_EQUAL((int)vecs.size(), checked);
  TEST_ASSERT_TRUE(errs > 0);
}

static void test_limit_and_linkage() {
  Calibration cal = Calibration::defaults();
  ArmQ q;
  // A point behind the left base is geometrically reachable but needs yaw outside limits.
  const Vec3 behind{-85, -60, 40};
  TEST_ASSERT_EQUAL(IkStatus::Ok, armIkRaw(cal.arms[0], behind, q));
  TEST_ASSERT_EQUAL(IkStatus::Limit, armIk(cal, 0, behind, q));
  // Linkage: tighten rel_max until a normal target violates it.
  const Vec3 tgt{0, 160, 13.8};
  TEST_ASSERT_EQUAL(IkStatus::Ok, armIk(cal, 0, tgt, q));
  const double rel = q.th1 - q.th2;
  cal.linkage.rel_max = (float)(rel - 1.0);
  TEST_ASSERT_EQUAL(IkStatus::Limit, armIk(cal, 0, tgt, q));
  char why[96];
  TEST_ASSERT_FALSE(armWithinLimits(cal, 0, q, why, sizeof(why)));
  TEST_ASSERT_NOT_NULL(strstr(why, "linkage"));
}

static void test_joint_map() {
  const Calibration cal = Calibration::defaults();
  // Neutral -> s_zero -> 1500 us.
  for (int i = 0; i < NUM_JOINTS; ++i) {
    const JointCal& j = cal.joints[i];
    TEST_ASSERT_FLOAT_WITHIN(1e-4, j.s_zero, jointToServoDeg(j, j.neutral));
    TEST_ASSERT_FLOAT_WITHIN(1e-3, 500.f + j.s_zero * 2000.f / 180.f, jointToUs(j, j.neutral));
  }
  // Gear and direction.
  const JointCal& ly = cal.joints[cfg::J_L_YAW];  // gear 1.5, dir +1, neutral -35
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 90.f + 1.5f * 10.f, jointToServoDeg(ly, -25.f));
  const JointCal& rs = cal.joints[cfg::J_R_SHOULDER];  // dir -1, neutral 60
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 90.f - 20.f, jointToServoDeg(rs, 80.f));
  // q clamped to [min,max].
  TEST_ASSERT_FLOAT_WITHIN(1e-4, jointToServoDeg(ly, ly.max), jointToServoDeg(ly, ly.max + 50.f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, jointToServoDeg(ly, ly.min), jointToServoDeg(ly, ly.min - 50.f));
  // s clamped to [0,180].
  JointCal wild = ly;
  wild.min = -1000;
  wild.max = 1000;
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 180.f, jointToServoDeg(wild, 500.f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 0.f, jointToServoDeg(wild, -500.f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 500.f, servoDegToUs(0.f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 2500.f, servoDegToUs(180.f));
  // Inverse.
  TEST_ASSERT_FLOAT_WITHIN(1e-3, -25.f, servoDegToJoint(ly, usToServoDeg(jointToUs(ly, -25.f))));
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 80.f, servoDegToJoint(rs, usToServoDeg(jointToUs(rs, 80.f))));
}

static void test_minjerk() {
  TEST_ASSERT_EQUAL_FLOAT(0.f, minJerk(0.f));
  TEST_ASSERT_EQUAL_FLOAT(1.f, minJerk(1.f));
  TEST_ASSERT_EQUAL_FLOAT(0.f, minJerk(-1.f));
  TEST_ASSERT_EQUAL_FLOAT(1.f, minJerk(2.f));
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 0.5f, minJerk(0.5f));
  float prev = 0.f;
  for (int i = 1; i <= 100; ++i) {
    const float t = i / 100.f;
    const float s = minJerk(t);
    TEST_ASSERT_TRUE(s >= prev);
    TEST_ASSERT_FLOAT_WITHIN(1e-5, 1.f - minJerk(1.f - t), s);  // symmetric
    prev = s;
  }
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 1.875f, minJerkVel(0.5f));
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 0.f, minJerkVel(0.0001f));
  // 90 deg at 240 deg/s peaks exactly at max_vel.
  const float ms = minJerkDurationMs(90.f, 240.f);
  TEST_ASSERT_FLOAT_WITHIN(1e-2, 703.125f, ms);
  TEST_ASSERT_FLOAT_WITHIN(1e-2, 240.f, 90.f * minJerkVel(0.5f) / (ms / 1000.f));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_vectors);
  RUN_TEST(test_limit_and_linkage);
  RUN_TEST(test_joint_map);
  RUN_TEST(test_minjerk);
  return UNITY_END();
}
