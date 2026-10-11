// Motion engine: min-jerk joint moves, default durations from max_vel, queue,
// cancel/pause, Cartesian straight lines, tap/swipe phases, parking the other arm.
#include <math.h>
#include <unity.h>

#include <string>
#include <vector>

#include "../support/fake_hal.h"
#include "joint_map.h"
#include "minjerk.h"
#include "motion.h"

using namespace tappet;

struct Rec : MotionListener {
  std::vector<uint32_t> done, cancelled;
  std::vector<std::string> errors;
  std::vector<uint32_t> errorIds;
  void onMotionDone(uint32_t id) override { done.push_back(id); }
  void onMotionError(uint32_t id, const char* err, const char*) override {
    errorIds.push_back(id);
    errors.push_back(err);
  }
  std::vector<std::string> why;
  void onMotionCancelled(uint32_t id, const char* w) override {
    cancelled.push_back(id);
    why.push_back(w);
  }
};

static Calibration cal;
static Rec rec;

void setUp() {
  cal = Calibration::defaults();
  rec = Rec();
}
void tearDown() {}

static int runUntilIdle(MotionEngine& m, int maxTicks = 5000) {
  int n = 0;
  while (m.busy() && n < maxTicks) {
    m.tick(MOTION_TICK_MS);
    ++n;
  }
  return n;
}

static MotionRequest jointsReq(uint32_t id, int joint, float q, float ms = 0) {
  MotionRequest r;
  r.type = MotionType::Joints;
  r.id = id;
  r.notify = true;
  r.mask = (uint8_t)(1u << joint);
  r.q[joint] = q;
  r.ms = ms;
  return r;
}

static void test_joint_move_default_duration() {
  MotionEngine m(cal);
  m.setListener(&rec);
  TEST_ASSERT_EQUAL_FLOAT(cal.joints[cfg::J_NECK_PAN].home, m.joint(cfg::J_NECK_PAN));
  TEST_ASSERT_TRUE(m.enqueue(jointsReq(7, cfg::J_NECK_PAN, 40.f)));
  float prev = m.joint(cfg::J_NECK_PAN);
  int ticks = 0;
  float peakVel = 0.f;
  while (m.busy() && ticks < 1000) {
    m.tick(MOTION_TICK_MS);
    ++ticks;
    const float v = m.joint(cfg::J_NECK_PAN);
    TEST_ASSERT_TRUE(v >= prev - 1e-5f);  // monotonic
    peakVel = fmaxf(peakVel, (v - prev) / (MOTION_TICK_MS / 1000.f));
    prev = v;
  }
  TEST_ASSERT_FLOAT_WITHIN(1e-4, 40.f, m.joint(cfg::J_NECK_PAN));
  const float expected = minJerkDurationMs(40.f, cal.joints[cfg::J_NECK_PAN].max_vel);  // 416.7 ms
  TEST_ASSERT_INT_WITHIN(3, (int)(expected / MOTION_TICK_MS), ticks);
  TEST_ASSERT_TRUE(peakVel <= cal.joints[cfg::J_NECK_PAN].max_vel * 1.01f);
  TEST_ASSERT_TRUE(peakVel >= cal.joints[cfg::J_NECK_PAN].max_vel * 0.95f);
  TEST_ASSERT_EQUAL(1, (int)rec.done.size());
  TEST_ASSERT_EQUAL_UINT32(7, rec.done[0]);
  // Other joints untouched.
  TEST_ASSERT_EQUAL_FLOAT(cal.joints[cfg::J_NECK_TILT].home, m.joint(cfg::J_NECK_TILT));
}

static void test_joint_move_explicit_ms_and_velocity_floor() {
  MotionEngine m(cal);
  m.setListener(&rec);
  m.enqueue(jointsReq(1, cfg::J_NECK_PAN, 10.f, 1000.f));  // slower than needed: honoured
  TEST_ASSERT_INT_WITHIN(2, 101, runUntilIdle(m));
  m.enqueue(jointsReq(2, cfg::J_NECK_PAN, 70.f, 50.f));  // explicit ms is honoured as given
  TEST_ASSERT_INT_WITHIN(1, 5, runUntilIdle(m));
  // Default duration has a 60 ms floor.
  m.enqueue(jointsReq(5, cfg::J_NECK_PAN, 70.5f));
  TEST_ASSERT_INT_WITHIN(1, 6, runUntilIdle(m));
  // Targets outside limits are clamped.
  m.enqueue(jointsReq(3, cfg::J_NECK_PAN, 500.f));
  runUntilIdle(m);
  TEST_ASSERT_EQUAL_FLOAT(cal.joints[cfg::J_NECK_PAN].max, m.joint(cfg::J_NECK_PAN));
  // A no-op move completes immediately with a done event.
  m.enqueue(jointsReq(4, cfg::J_NECK_PAN, cal.joints[cfg::J_NECK_PAN].max));
  TEST_ASSERT_TRUE(runUntilIdle(m) <= 1);
  TEST_ASSERT_EQUAL(5, (int)rec.done.size());
}

static void test_queue_depth_and_order() {
  MotionEngine m(cal);
  m.setListener(&rec);
  for (int i = 0; i < MOTION_QUEUE_DEPTH; ++i)
    TEST_ASSERT_TRUE(m.enqueue(jointsReq(100 + i, cfg::J_NECK_TILT, (i % 2) ? 10.f : -10.f, 20.f)));
  TEST_ASSERT_FALSE(m.enqueue(jointsReq(999, cfg::J_NECK_TILT, 0.f)));
  TEST_ASSERT_EQUAL(16, m.queued());
  runUntilIdle(m);
  TEST_ASSERT_EQUAL(16, (int)rec.done.size());
  for (int i = 0; i < 16; ++i) TEST_ASSERT_EQUAL_UINT32(100 + i, rec.done[i]);
}

static void test_cancel_and_pause() {
  MotionEngine m(cal);
  m.setListener(&rec);
  m.enqueue(jointsReq(1, cfg::J_NECK_PAN, 60.f));
  m.enqueue(jointsReq(2, cfg::J_NECK_PAN, -60.f));
  for (int i = 0; i < 15; ++i) m.tick(MOTION_TICK_MS);
  const float mid = m.joint(cfg::J_NECK_PAN);
  TEST_ASSERT_TRUE(mid > 1.f && mid < 59.f);
  m.setPaused(true);
  for (int i = 0; i < 50; ++i) m.tick(MOTION_TICK_MS);
  TEST_ASSERT_EQUAL_FLOAT(mid, m.joint(cfg::J_NECK_PAN));
  m.setPaused(false);
  m.tick(MOTION_TICK_MS);
  TEST_ASSERT_TRUE(m.joint(cfg::J_NECK_PAN) > mid);
  const float held = m.joint(cfg::J_NECK_PAN);
  m.cancelAll("stop");
  TEST_ASSERT_FALSE(m.busy());
  TEST_ASSERT_EQUAL(2, (int)rec.cancelled.size());
  TEST_ASSERT_EQUAL_STRING("stop", rec.why[0].c_str());
  TEST_ASSERT_EQUAL(0, (int)rec.done.size());
  for (int i = 0; i < 10; ++i) m.tick(MOTION_TICK_MS);
  TEST_ASSERT_EQUAL_FLOAT(held, m.joint(cfg::J_NECK_PAN));
}

static double distToLine(const Vec3& p, const Vec3& a, const Vec3& b) {
  const double vx = b.x - a.x, vy = b.y - a.y, vz = b.z - a.z;
  const double wx = p.x - a.x, wy = p.y - a.y, wz = p.z - a.z;
  const double L2 = vx * vx + vy * vy + vz * vz;
  double t = L2 > 0 ? (wx * vx + wy * vy + wz * vz) / L2 : 0;
  t = t < 0 ? 0 : (t > 1 ? 1 : t);
  const double dx = wx - t * vx, dy = wy - t * vy, dz = wz - t * vz;
  return sqrt(dx * dx + dy * dy + dz * dz);
}

static void test_cartesian_move_straight_line() {
  MotionEngine m(cal);
  m.setListener(&rec);
  // First bring L near the work area, then check a straight move.
  MotionRequest r;
  r.type = MotionType::Move;
  r.arm = 0;
  r.id = 1;
  r.notify = true;
  r.x = -30;
  r.y = 120;
  r.z = 40;
  m.enqueue(r);
  runUntilIdle(m);
  const Vec3 a = m.toolPos(0);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, -30, a.x);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 120, a.y);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 40, a.z);
  r.id = 2;
  r.x = -5;
  r.y = 200;
  r.z = 25;
  r.ms = 600;
  m.enqueue(r);
  const Vec3 b{-5, 200, 25};
  int ticks = 0;
  while (m.busy()) {
    m.tick(MOTION_TICK_MS);
    ++ticks;
    TEST_ASSERT_TRUE(distToLine(m.toolPos(0), a, b) < 0.05);
  }
  TEST_ASSERT_INT_WITHIN(2, 60, ticks);
  const Vec3 e = m.toolPos(0);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, -5, e.x);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 200, e.y);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 25, e.z);
  TEST_ASSERT_EQUAL(2, (int)rec.done.size());
}

static void test_cartesian_default_speed() {
  MotionEngine m(cal);
  MotionRequest r;
  r.type = MotionType::Move;
  r.arm = 0;
  r.x = -30;
  r.y = 120;
  r.z = 40;
  m.enqueue(r);
  runUntilIdle(m);
  r.y = 240;  // 120 mm straight line -> >= 800 ms at 150 mm/s
  m.enqueue(r);
  const int ticks = runUntilIdle(m);
  TEST_ASSERT_TRUE(ticks >= 80);
  TEST_ASSERT_TRUE(ticks <= 200);
}

static void test_tap_sequence() {
  MotionEngine m(cal);
  m.setListener(&rec);
  MotionRequest r;
  r.type = MotionType::Tap;
  r.arm = 0;
  r.id = 42;
  r.notify = true;
  r.x = -20;
  r.y = 150;
  r.z = 13.8f;
  r.hold_ms = 60;
  r.press_mm = 2.5f;
  r.approach_mm = 8;
  m.enqueue(r);
  double minZ = 1e9;
  int ticksAtBottom = 0;
  bool reachedApproachFirst = false;
  while (m.busy()) {
    m.tick(MOTION_TICK_MS);
    const Vec3 p = m.toolPos(0);
    if (fabs(p.x + 20) < 0.05 && fabs(p.y - 150) < 0.05 && fabs(p.z - 21.8) < 0.05 && minZ > 20) reachedApproachFirst = true;
    if (p.z < minZ) minZ = p.z;
    if (fabs(p.z - 11.3) < 0.01) {
      ++ticksAtBottom;
      TEST_ASSERT_DOUBLE_WITHIN(0.05, -20, p.x);
      TEST_ASSERT_DOUBLE_WITHIN(0.05, 150, p.y);
    }
  }
  TEST_ASSERT_TRUE(reachedApproachFirst);
  TEST_ASSERT_DOUBLE_WITHIN(1e-3, 11.3, minZ);
  TEST_ASSERT_TRUE(ticksAtBottom >= 6);  // ~60 ms hold
  const Vec3 e = m.toolPos(0);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 21.8, e.z);
  TEST_ASSERT_EQUAL(1, (int)rec.done.size());
  TEST_ASSERT_EQUAL_UINT32(42, rec.done[0]);
  TEST_ASSERT_EQUAL(0, (int)rec.errors.size());
}

static void test_swipe_sequence() {
  MotionEngine m(cal);
  m.setListener(&rec);
  MotionRequest r;
  r.type = MotionType::Swipe;
  r.arm = 1;
  r.id = 5;
  r.notify = true;
  r.x = 20;
  r.y = 200;
  r.x1 = 20;
  r.y1 = 120;
  r.z = 13.8f;
  r.press_mm = 2.5f;
  r.approach_mm = 8;
  r.ms = 400;
  m.enqueue(r);
  const Vec3 s0{20, 200, 11.3}, s1{20, 120, 11.3};
  int strokeTicks = 0;
  while (m.busy()) {
    m.tick(MOTION_TICK_MS);
    const Vec3 p = m.toolPos(1);
    if (fabs(p.z - 11.3) < 1e-3) {
      ++strokeTicks;
      TEST_ASSERT_TRUE(distToLine(p, s0, s1) < 0.05);
    }
  }
  TEST_ASSERT_TRUE(strokeTicks >= 38);  // 400 ms stroke at contact height
  const Vec3 e = m.toolPos(1);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 20, e.x);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 120, e.y);
  TEST_ASSERT_DOUBLE_WITHIN(1e-2, 21.8, e.z);
  TEST_ASSERT_EQUAL(1, (int)rec.done.size());
}

static void test_other_arm_parked_when_close() {
  MotionEngine m(cal);
  m.setListener(&rec);
  // Put the right arm's tool right where the left arm will tap.
  MotionRequest mv;
  mv.type = MotionType::Move;
  mv.arm = 1;
  mv.x = -10;
  mv.y = 150;
  mv.z = 30;
  m.enqueue(mv);
  runUntilIdle(m);
  TEST_ASSERT_DOUBLE_WITHIN(0.01, -10, m.toolPos(1).x);
  MotionRequest t;
  t.type = MotionType::Tap;
  t.arm = 0;
  t.x = -20;
  t.y = 150;
  t.z = 13.8f;
  t.press_mm = 2.5f;
  t.approach_mm = 8;
  t.id = 9;
  t.notify = true;
  m.enqueue(t);
  runUntilIdle(m);
  for (int k = 0; k < 3; ++k) {
    const int ji = cfg::ARM_JOINTS[1][k];
    TEST_ASSERT_FLOAT_WITHIN(1e-3, cal.joints[ji].park, m.joint(ji));
  }
  TEST_ASSERT_EQUAL(1, (int)rec.done.size());

  // Far away: the other arm is left alone.
  MotionEngine m2(cal);
  MotionRequest t2 = t;
  t2.x = -60;
  t2.y = 120;
  const float before = m2.joint(cfg::J_R_YAW);
  m2.enqueue(t2);
  runUntilIdle(m2);
  TEST_ASSERT_EQUAL_FLOAT(before, m2.joint(cfg::J_R_YAW));
}

static void test_park_and_runtime_error() {
  MotionEngine m(cal);
  m.setListener(&rec);
  MotionRequest p;
  p.type = MotionType::Park;
  p.mask = 3;
  p.id = 11;
  p.notify = true;
  m.enqueue(p);
  runUntilIdle(m);
  for (int a = 0; a < 2; ++a)
    for (int k = 0; k < 3; ++k) {
      const int ji = cfg::ARM_JOINTS[a][k];
      TEST_ASSERT_FLOAT_WITHIN(1e-3, cal.joints[ji].park, m.joint(ji));
    }
  TEST_ASSERT_EQUAL_FLOAT(cal.joints[cfg::J_NECK_PAN].home, m.joint(cfg::J_NECK_PAN));
  TEST_ASSERT_EQUAL(1, (int)rec.done.size());

  // Geometry changes after enqueue so the target becomes unreachable -> error event.
  MotionRequest mv;
  mv.type = MotionType::Move;
  mv.arm = 0;
  mv.x = -10;
  mv.y = 230;
  mv.z = 13.8f;
  mv.id = 12;
  mv.notify = true;
  m.enqueue(mv);
  cal.arms[0].L1 = 40;
  cal.arms[0].L2 = 40;
  runUntilIdle(m);
  TEST_ASSERT_EQUAL(1, (int)rec.errors.size());
  TEST_ASSERT_EQUAL_UINT32(12, rec.errorIds[0]);
  TEST_ASSERT_EQUAL_STRING("unreachable", rec.errors[0].c_str());
}

static void test_dist_point_segment() {
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 5.f, distPointSegmentXY(0, 5, -10, 0, 10, 0));
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 5.f, distPointSegmentXY(15, 0, -10, 0, 10, 0));
  TEST_ASSERT_FLOAT_WITHIN(1e-5, 5.f, distPointSegmentXY(3, 4, 0, 0, 0, 0));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_joint_move_default_duration);
  RUN_TEST(test_joint_move_explicit_ms_and_velocity_floor);
  RUN_TEST(test_queue_depth_and_order);
  RUN_TEST(test_cancel_and_pause);
  RUN_TEST(test_cartesian_move_straight_line);
  RUN_TEST(test_cartesian_default_speed);
  RUN_TEST(test_tap_sequence);
  RUN_TEST(test_swipe_sequence);
  RUN_TEST(test_other_arm_parked_when_close);
  RUN_TEST(test_park_and_runtime_error);
  RUN_TEST(test_dist_point_segment);
  return UNITY_END();
}
