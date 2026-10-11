// Protocol dispatcher (SPEC section 4) end-to-end against a fake hardware layer:
// every command, error code and event, watchdog, streaming, brownout, button.
#include <ArduinoJson.h>
#include <unity.h>

#include <memory>
#include <string>

#include "../support/fake_hal.h"
#include "button.h"
#include "joint_map.h"
#include "kinematics.h"
#include "line_assembler.h"
#include "robot.h"

using namespace tappet;
using namespace tappet_test;

static FakeHal* hal;
static FakeSink* sink;
static Robot* robot;

void setUp() {
  hal = new FakeHal();
  sink = new FakeSink();
  robot = new Robot(*hal, *sink);
  robot->begin();
}

void tearDown() {
  delete robot;
  delete sink;
  delete hal;
}

static void advance(uint32_t ms) {
  for (uint32_t t = 0; t < ms; t += 5) {
    hal->now += 5;
    robot->loop();
  }
}

// Send a request and return the parsed response.
static JsonDocument req(const std::string& line, int transport = TRANSPORT_UART) {
  const size_t before = sink->msgs.size();
  robot->handleLine(transport, line.c_str(), line.size());
  JsonDocument d;
  for (size_t i = before; i < sink->msgs.size(); ++i) {
    if (sink->msgs[i].transport != transport) continue;
    JsonDocument t;
    if (deserializeJson(t, sink->msgs[i].line)) continue;
    if (t["ok"].is<bool>()) {
      d = t;
      break;
    }
  }
  return d;
}

static void assertErr(const JsonDocument& d, const char* code) {
  TEST_ASSERT_TRUE_MESSAGE(d["ok"].is<bool>(), "no response");
  TEST_ASSERT_FALSE(d["ok"].as<bool>());
  TEST_ASSERT_EQUAL_STRING(code, d["err"].as<const char*>());
  TEST_ASSERT_TRUE(d["msg"].is<const char*>());
}

static void assertOk(const JsonDocument& d) {
  TEST_ASSERT_TRUE_MESSAGE(d["ok"].is<bool>(), "no response");
  if (!d["ok"].as<bool>()) TEST_FAIL_MESSAGE(d["msg"].as<const char*>());
}

static void enable() { assertOk(req("{\"id\":1,\"cmd\":\"enable\"}")); }

static void runUntilIdle(uint32_t maxMs = 20000) {
  for (uint32_t t = 0; t < maxMs && robot->motion().busy(); t += 5) advance(5);
}

// ---------------------------------------------------------------------------

static void test_boot_hello_ping() {
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvt("boot", ev));
  TEST_ASSERT_EQUAL_STRING("tappet-fw", ev["fw"].as<const char*>());
  TEST_ASSERT_EQUAL_STRING(cfg::ROBOT_VERSION, ev["ver"].as<const char*>());
  TEST_ASSERT_EQUAL(TRANSPORT_ALL, sink->msgs[0].transport);

  JsonDocument d = req("{\"id\":5,\"cmd\":\"hello\"}");
  assertOk(d);
  TEST_ASSERT_EQUAL_UINT32(5, d["id"].as<uint32_t>());
  TEST_ASSERT_EQUAL_STRING("tappet-fw", d["fw"].as<const char*>());
  TEST_ASSERT_EQUAL_STRING(cfg::ROBOT_VERSION, d["ver"].as<const char*>());
  TEST_ASSERT_EQUAL(1, d["proto"].as<int>());
  TEST_ASSERT_EQUAL(8, d["servos"].as<int>());
  TEST_ASSERT_EQUAL_UINT32(hal->now, d["uptime_ms"].as<uint32_t>());

  d = req("{\"id\":6,\"cmd\":\"ping\"}");
  assertOk(d);
  TEST_ASSERT_EQUAL_UINT32(hal->now, d["t"].as<uint32_t>());
}

static void test_error_codes() {
  JsonDocument d = req("{not json");
  assertErr(d, "bad_json");
  TEST_ASSERT_TRUE(d["id"].isNull());
  assertErr(req("[1,2]"), "bad_json");
  d = req("{\"id\":3,\"cmd\":\"dance\"}");
  assertErr(d, "unknown_cmd");
  TEST_ASSERT_EQUAL_UINT32(3, d["id"].as<uint32_t>());
  assertErr(req("{\"id\":4}"), "bad_args");
  assertErr(req("{\"id\":4,\"cmd\":\"move\",\"arm\":\"X\",\"x\":0,\"y\":150,\"z\":20}"), "bad_args");
  assertErr(req("{\"id\":4,\"cmd\":\"move\",\"arm\":\"L\",\"x\":\"a\",\"y\":150,\"z\":20}"), "bad_args");
  assertErr(req("{\"id\":4,\"cmd\":\"joints\",\"q\":{\"NECK_PAN\":10},\"ms\":-5}"), "bad_args");
  // Motion while relaxed.
  assertErr(req("{\"id\":4,\"cmd\":\"joints\",\"q\":{\"NECK_PAN\":10}}"), "not_enabled");
  assertErr(req("{\"id\":4,\"cmd\":\"servo_raw\",\"ch\":3,\"us\":1500}"), "not_enabled");
  // Over-long line.
  robot->handleOverflow(TRANSPORT_UART);
  JsonDocument last;
  deserializeJson(last, sink->msgs.back().line);
  assertErr(last, "bad_json");
  // Blank lines are ignored.
  const size_t n = sink->msgs.size();
  robot->handleLine(TRANSPORT_UART, "  ", 2);
  TEST_ASSERT_EQUAL(n, sink->msgs.size());
}

static void test_enable_relax_outputs() {
  TEST_ASSERT_FALSE(hal->oe_enabled);
  enable();
  TEST_ASSERT_TRUE(hal->oe_enabled);
  TEST_ASSERT_TRUE(robot->enabled());
  const Calibration& cal = robot->calibration();
  for (int i = 0; i < NUM_JOINTS; ++i)
    TEST_ASSERT_FLOAT_WITHIN(0.01, jointToUs(cal.joints[i], cal.joints[i].home), hal->pulses[cal.joints[i].ch]);
  assertOk(req("{\"id\":2,\"cmd\":\"relax\"}"));
  TEST_ASSERT_FALSE(hal->oe_enabled);
  TEST_ASSERT_FALSE(robot->enabled());
  // Enable fails with a fault when the PWM driver is missing.
  hal->pwm_ok = false;
  assertErr(req("{\"id\":3,\"cmd\":\"enable\"}"), "fault");
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvt("fault", ev));
  TEST_ASSERT_EQUAL_STRING("pwm", ev["code"].as<const char*>());
}

static void test_joints_neck_home_done_events() {
  enable();
  JsonDocument d = req("{\"id\":10,\"cmd\":\"joints\",\"q\":{\"NECK_PAN\":30,\"L_YAW\":-40}}");
  assertOk(d);
  TEST_ASSERT_TRUE(d["queued"].as<bool>());
  assertErr(req("{\"id\":11,\"cmd\":\"joints\",\"q\":{\"NOSE\":30}}"), "bad_args");
  assertErr(req("{\"id\":11,\"cmd\":\"joints\",\"q\":{}}"), "bad_args");
  // Linkage violation -> limit (theta1 - theta2 > rel_max).
  assertErr(req("{\"id\":12,\"cmd\":\"joints\",\"q\":{\"L_SHOULDER\":140,\"L_ELBOW\":-100}}"), "limit");
  d = req("{\"id\":13,\"cmd\":\"neck\",\"pan\":200,\"tilt\":-10,\"ms\":300}");
  assertOk(d);
  assertErr(req("{\"id\":14,\"cmd\":\"neck\"}"), "bad_args");
  runUntilIdle();
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvtId("done", 10, ev));
  TEST_ASSERT_TRUE(sink->findEvtId("done", 13, ev));
  TEST_ASSERT_EQUAL(TRANSPORT_ALL, sink->msgs.back().transport);
  d = req("{\"id\":15,\"cmd\":\"status\"}");
  assertOk(d);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 80.0, d["joints"]["NECK_PAN"].as<double>());  // clamped
  TEST_ASSERT_FLOAT_WITHIN(0.01, -10.0, d["joints"]["NECK_TILT"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(0.01, -40.0, d["joints"]["L_YAW"].as<double>());
  assertOk(req("{\"id\":16,\"cmd\":\"home\",\"ms\":500}"));
  runUntilIdle();
  TEST_ASSERT_TRUE(sink->findEvtId("done", 16, ev));
  d = req("{\"id\":17,\"cmd\":\"status\"}");
  for (int i = 0; i < NUM_JOINTS; ++i)
    TEST_ASSERT_FLOAT_WITHIN(0.01, robot->calibration().joints[i].home, d["joints"][jointName(i)].as<double>());
}

static void test_status_shape() {
  hal->vservo = 5.05f;
  hal->vin = 9.12f;
  advance(100);
  JsonDocument d = req("{\"id\":1,\"cmd\":\"status\"}");
  assertOk(d);
  TEST_ASSERT_TRUE(d["enabled"].is<bool>());
  TEST_ASSERT_TRUE(d["busy"].is<bool>());
  TEST_ASSERT_EQUAL(0, d["queue"].as<int>());
  TEST_ASSERT_EQUAL(8, (int)d["joints"].as<JsonObject>().size());
  TEST_ASSERT_EQUAL(3, (int)d["tools"]["L"].as<JsonArray>().size());
  TEST_ASSERT_EQUAL(3, (int)d["tools"]["R"].as<JsonArray>().size());
  TEST_ASSERT_FLOAT_WITHIN(0.01, 5.05, d["vservo"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(0.01, 9.12, d["vin"].as<double>());
  TEST_ASSERT_TRUE(d["faults"].is<JsonArray>());
  TEST_ASSERT_EQUAL(0, (int)d["faults"].as<JsonArray>().size());
  const Vec3 l = armFkFromJoints(robot->calibration(), 0, robot->motion().joints());
  TEST_ASSERT_FLOAT_WITHIN(0.01, l.x, d["tools"]["L"][0].as<double>());
  TEST_ASSERT_TRUE(sink->msgs.back().line.size() <= 512);
}

static void test_move_tap_swipe_park() {
  assertOk(req("{\"id\":0,\"cmd\":\"watchdog\",\"ms\":0}"));
  enable();
  assertErr(req("{\"id\":1,\"cmd\":\"move\",\"arm\":\"L\",\"x\":0,\"y\":400,\"z\":13.8}"), "unreachable");
  assertErr(req("{\"id\":1,\"cmd\":\"move\",\"arm\":\"L\",\"x\":-85,\"y\":-60,\"z\":40}"), "limit");
  JsonDocument d = req("{\"id\":2,\"cmd\":\"move\",\"arm\":\"L\",\"x\":-30,\"y\":150,\"z\":30}");
  assertOk(d);
  TEST_ASSERT_TRUE(d["queued"].as<bool>());
  runUntilIdle();
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvtId("done", 2, ev));
  d = req("{\"id\":3,\"cmd\":\"fk\",\"arm\":\"L\"}");
  assertOk(d);
  TEST_ASSERT_FLOAT_WITHIN(0.01, -30, d["x"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(0.01, 150, d["y"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(0.01, 30, d["z"].as<double>());

  // tap: auto arm by side of x.
  d = req("{\"id\":4,\"cmd\":\"tap\",\"x\":20,\"y\":160,\"z\":13.8}");
  assertOk(d);
  TEST_ASSERT_EQUAL_STRING("R", d["arm"].as<const char*>());
  d = req("{\"id\":5,\"cmd\":\"tap\",\"arm\":\"auto\",\"x\":-20,\"y\":160,\"z\":13.8,\"hold_ms\":100}");
  TEST_ASSERT_EQUAL_STRING("L", d["arm"].as<const char*>());
  // Fallback: x<0 but only the right arm can reach it.
  d = req("{\"id\":6,\"cmd\":\"tap\",\"x\":-40,\"y\":60,\"z\":13.8}");
  assertOk(d);
  TEST_ASSERT_EQUAL_STRING("R", d["arm"].as<const char*>());
  // Explicit arm that cannot reach -> error, no fallback.
  assertErr(req("{\"id\":7,\"cmd\":\"tap\",\"arm\":\"L\",\"x\":-40,\"y\":60,\"z\":13.8}"), "limit");
  assertErr(req("{\"id\":7,\"cmd\":\"tap\",\"x\":0,\"y\":500,\"z\":13.8}"), "unreachable");
  assertErr(req("{\"id\":7,\"cmd\":\"tap\",\"x\":0,\"y\":150,\"z\":13.8,\"hold_ms\":-1}"), "bad_args");
  assertErr(req("{\"id\":7,\"cmd\":\"tap\",\"arm\":\"Q\",\"x\":0,\"y\":150,\"z\":13.8}"), "bad_args");
  d = req("{\"id\":8,\"cmd\":\"swipe\",\"x0\":10,\"y0\":200,\"x1\":10,\"y1\":120,\"z\":13.8}");
  assertOk(d);
  TEST_ASSERT_EQUAL_STRING("R", d["arm"].as<const char*>());
  assertErr(req("{\"id\":9,\"cmd\":\"swipe\",\"x0\":10,\"y0\":200,\"z\":13.8}"), "bad_args");
  d = req("{\"id\":10,\"cmd\":\"park\",\"arm\":\"L\"}");
  assertOk(d);
  assertErr(req("{\"id\":11,\"cmd\":\"park\",\"arm\":\"middle\"}"), "bad_args");
  assertOk(req("{\"id\":12,\"cmd\":\"park\"}"));
  runUntilIdle();
  for (uint32_t id : {4u, 5u, 6u, 8u, 10u, 12u}) {
    char m[32];
    snprintf(m, sizeof(m), "done for id %u", id);
    TEST_ASSERT_TRUE_MESSAGE(sink->findEvtId("done", id, ev), m);
  }
  TEST_ASSERT_EQUAL(0, sink->countEvt("error"));
  for (int a = 0; a < 2; ++a)
    for (int k = 0; k < 3; ++k) {
      const int ji = cfg::ARM_JOINTS[a][k];
      TEST_ASSERT_FLOAT_WITHIN(1e-3, robot->calibration().joints[ji].park, robot->motion().joint(ji));
    }
}

static void test_queue_full_stop_relax_cancel() {
  enable();
  for (int i = 0; i < 17; ++i) {
    char line[96];
    snprintf(line, sizeof(line), "{\"id\":%d,\"cmd\":\"neck\",\"pan\":%d,\"ms\":200}", 100 + i, (i % 2) ? 20 : -20);
    JsonDocument d = req(line);
    if (i < 17 - 1) {
      assertOk(d);
    } else {
      // first item may already be active only after a tick; we have not ticked yet
      assertErr(d, "queue_full");
    }
  }
  advance(50);
  JsonDocument d = req("{\"id\":200,\"cmd\":\"status\"}");
  TEST_ASSERT_TRUE(d["busy"].as<bool>());
  TEST_ASSERT_EQUAL(15, d["queue"].as<int>());
  const float held = robot->motion().joint(cfg::J_NECK_PAN);
  assertOk(req("{\"id\":201,\"cmd\":\"stop\"}"));
  TEST_ASSERT_FALSE(robot->motion().busy());
  TEST_ASSERT_EQUAL(16, sink->countEvt("error"));  // all cancelled
  TEST_ASSERT_EQUAL(0, sink->countEvt("done"));
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvtId("error", 100, ev));
  TEST_ASSERT_EQUAL_STRING("fault", ev["err"].as<const char*>());
  TEST_ASSERT_EQUAL_STRING("cancelled (stop)", ev["msg"].as<const char*>());
  advance(100);
  TEST_ASSERT_EQUAL_FLOAT(held, robot->motion().joint(cfg::J_NECK_PAN));
  TEST_ASSERT_TRUE(robot->enabled());
  // relax clears the queue as well.
  assertOk(req("{\"id\":202,\"cmd\":\"neck\",\"pan\":50}"));
  assertOk(req("{\"id\":203,\"cmd\":\"relax\"}"));
  TEST_ASSERT_FALSE(robot->motion().busy());
  TEST_ASSERT_TRUE(sink->findEvtId("error", 202, ev));
  TEST_ASSERT_EQUAL_STRING("cancelled (relax)", ev["msg"].as<const char*>());
}

static void test_servo_raw() {
  enable();
  assertErr(req("{\"id\":1,\"cmd\":\"servo_raw\",\"ch\":16,\"us\":1500}"), "bad_args");
  assertErr(req("{\"id\":1,\"cmd\":\"servo_raw\",\"ch\":2,\"us\":3000}"), "bad_args");
  assertOk(req("{\"id\":2,\"cmd\":\"servo_raw\",\"ch\":6,\"us\":1700}"));
  TEST_ASSERT_EQUAL_FLOAT(1700.f, hal->pulses[6]);
  // The commanded joint on that channel follows the raw pulse.
  TEST_ASSERT_FLOAT_WITHIN(1e-3, servoDegToJoint(robot->calibration().joints[cfg::J_NECK_PAN], usToServoDeg(1700.f)),
                           robot->motion().joint(cfg::J_NECK_PAN));
  assertOk(req("{\"id\":3,\"cmd\":\"servo_raw\",\"ch\":12,\"us\":1234}"));  // spare channel
  TEST_ASSERT_EQUAL_FLOAT(1234.f, hal->pulses[12]);
  advance(200);  // motion ticks must not overwrite the raw override
  TEST_ASSERT_EQUAL_FLOAT(1700.f, hal->pulses[6]);
  // A motion command releases the override.
  assertOk(req("{\"id\":4,\"cmd\":\"neck\",\"pan\":10}"));
  runUntilIdle();
  TEST_ASSERT_FLOAT_WITHIN(0.01, jointToUs(robot->calibration().joints[cfg::J_NECK_PAN], 10.f), hal->pulses[6]);
}

static void test_calib_get_set_reset() {
  JsonDocument d = req("{\"id\":1,\"cmd\":\"calib_get\"}");
  assertOk(d);
  TEST_ASSERT_TRUE(sink->msgs.back().line.size() <= 2048);
  JsonObject j = d["joints"];
  TEST_ASSERT_EQUAL(8, (int)j.size());
  const char* fields[] = {"ch", "gear", "dir", "s_zero", "neutral", "min", "max", "home", "park", "max_vel"};
  for (JsonPair kv : j) {
    TEST_ASSERT_TRUE(jointIndex(kv.key().c_str()) >= 0);
    for (const char* f : fields) TEST_ASSERT_FALSE_MESSAGE(kv.value()[f].isNull(), f);
  }
  TEST_ASSERT_EQUAL(1, j["L_YAW"]["dir"].as<int>());
  TEST_ASSERT_EQUAL(-1, j["R_ELBOW"]["dir"].as<int>());
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 1.5, j["R_YAW"]["gear"].as<double>());

  enable();
  d = req("{\"id\":2,\"cmd\":\"calib_set\",\"joints\":{\"NECK_PAN\":{\"s_zero\":95.5}}}");
  assertOk(d);
  TEST_ASSERT_FALSE(d["saved"].as<bool>());
  TEST_ASSERT_EQUAL(0, hal->save_calls);
  TEST_ASSERT_FLOAT_WITHIN(0.01, 500.f + 95.5f * 2000.f / 180.f, hal->pulses[6]);  // NECK_PAN home == neutral
  assertErr(req("{\"id\":3,\"cmd\":\"calib_set\",\"joints\":{\"NECK_PAN\":{\"dir\":0}}}"), "bad_args");
  assertErr(req("{\"id\":3,\"cmd\":\"calib_set\",\"joints\":{\"NECK_PAN\":{\"wobble\":1}}}"), "bad_args");
  assertErr(req("{\"id\":3,\"cmd\":\"calib_set\",\"joints\":{\"NOPE\":{\"s_zero\":1}}}"), "bad_args");
  assertErr(req("{\"id\":3,\"cmd\":\"calib_set\",\"joints\":{\"NECK_PAN\":{\"min\":50}}}"), "bad_args");
  // Failed sets do not modify anything.
  TEST_ASSERT_EQUAL_FLOAT(95.5f, robot->calibration().joints[cfg::J_NECK_PAN].s_zero);
  TEST_ASSERT_EQUAL(1, robot->calibration().joints[cfg::J_NECK_PAN].dir);
  d = req("{\"id\":4,\"cmd\":\"calib_set\",\"joints\":{\"L_YAW\":{\"s_zero\":88,\"max_vel\":200}},\"save\":true}");
  assertOk(d);
  TEST_ASSERT_TRUE(d["saved"].as<bool>());
  TEST_ASSERT_EQUAL(1, hal->save_calls);

  // Persistence: a fresh Robot on the same "flash" loads it.
  {
    FakeSink s2;
    Robot r2(*hal, s2);
    r2.begin();
    TEST_ASSERT_EQUAL_FLOAT(88.f, r2.calibration().joints[cfg::J_L_YAW].s_zero);
    TEST_ASSERT_EQUAL_FLOAT(95.5f, r2.calibration().joints[cfg::J_NECK_PAN].s_zero);
  }

  assertOk(req("{\"id\":5,\"cmd\":\"reset_calib\"}"));
  TEST_ASSERT_EQUAL(1, hal->erase_calls);
  TEST_ASSERT_FALSE(hal->has_saved);
  TEST_ASSERT_EQUAL_FLOAT(cfg::JOINTS[cfg::J_L_YAW].s_zero, robot->calibration().joints[cfg::J_L_YAW].s_zero);
  TEST_ASSERT_EQUAL_FLOAT(cfg::JOINTS[cfg::J_NECK_PAN].s_zero, robot->calibration().joints[cfg::J_NECK_PAN].s_zero);
}

static void test_geom_get_set() {
  JsonDocument d = req("{\"id\":1,\"cmd\":\"geom_get\"}");
  assertOk(d);
  TEST_ASSERT_FLOAT_WITHIN(1e-6, -85, d["arms"]["L"]["base_x"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 85, d["arms"]["R"]["base_x"].as<double>());
  const char* f[] = {"base_x", "base_y", "shoulder_h", "r0", "L1", "L2", "tool_dx", "tool_dz"};
  for (const char* k : f) TEST_ASSERT_FALSE(d["arms"]["L"][k].isNull());
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 20, d["linkage"]["rel_min"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(1e-6, 160, d["linkage"]["rel_max"].as<double>());

  assertOk(req("{\"id\":2,\"cmd\":\"geom_set\",\"arms\":{\"R\":{\"tool_dz\":36.5}},\"linkage\":{\"rel_max\":150}}"));
  TEST_ASSERT_EQUAL_FLOAT(36.5f, robot->calibration().arms[1].tool_dz);
  TEST_ASSERT_EQUAL_FLOAT(150.f, robot->calibration().linkage.rel_max);
  assertErr(req("{\"id\":3,\"cmd\":\"geom_set\",\"arms\":{\"R\":{\"L1\":-3}}}"), "bad_args");
  assertErr(req("{\"id\":3,\"cmd\":\"geom_set\",\"arms\":{\"Z\":{\"L1\":3}}}"), "bad_args");
  assertErr(req("{\"id\":3,\"cmd\":\"geom_set\",\"linkage\":{\"rel_min\":155}}"), "bad_args");
  JsonDocument s = req("{\"id\":4,\"cmd\":\"geom_set\",\"save\":true}");
  assertOk(s);
  TEST_ASSERT_TRUE(s["saved"].as<bool>());
  TEST_ASSERT_EQUAL(1, hal->save_calls);
}

static void test_fk_ik() {
  JsonDocument d = req("{\"id\":1,\"cmd\":\"ik\",\"arm\":\"L\",\"x\":0,\"y\":160,\"z\":13.8}");
  assertOk(d);
  TEST_ASSERT_FLOAT_WITHIN(1e-3, -35.311213, d["q"]["YAW"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 48.422861, d["q"]["SHOULDER"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, -60.783846, d["q"]["ELBOW"].as<double>());
  assertErr(req("{\"id\":2,\"cmd\":\"ik\",\"arm\":\"L\",\"x\":-85,\"y\":40,\"z\":300}"), "unreachable");
  d = req("{\"id\":3,\"cmd\":\"ik\",\"arm\":\"L\",\"x\":-85,\"y\":-60,\"z\":40}");
  assertErr(d, "limit");
  TEST_ASSERT_FALSE(d["q"]["YAW"].isNull());
  assertErr(req("{\"id\":4,\"cmd\":\"ik\",\"arm\":\"L\",\"x\":1}"), "bad_args");
  assertErr(req("{\"id\":4,\"cmd\":\"fk\"}"), "bad_args");
  d = req("{\"id\":5,\"cmd\":\"fk\",\"arm\":\"R\"}");
  assertOk(d);
  const Vec3 p = armFkFromJoints(robot->calibration(), 1, robot->motion().joints());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, p.x, d["x"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, p.y, d["y"].as<double>());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, p.z, d["z"].as<double>());
}

static void test_watchdog_parks_then_relaxes() {
  JsonDocument d = req("{\"id\":1,\"cmd\":\"watchdog\",\"ms\":500}");
  assertOk(d);
  TEST_ASSERT_EQUAL_UINT32(500, d["ms"].as<uint32_t>());
  assertErr(req("{\"id\":1,\"cmd\":\"watchdog\"}"), "bad_args");
  enable();
  // Pings keep it alive.
  for (int i = 0; i < 6; ++i) {
    advance(200);
    assertOk(req("{\"id\":2,\"cmd\":\"ping\"}"));
  }
  TEST_ASSERT_TRUE(robot->enabled());
  TEST_ASSERT_EQUAL(0, sink->countEvt("watchdog"));
  // Silence -> cancel queued motions, park both arms, then relax and emit watchdog.
  assertOk(req("{\"id\":77,\"cmd\":\"neck\",\"pan\":30,\"ms\":5000}"));
  advance(550);
  JsonDocument wev;
  TEST_ASSERT_TRUE(sink->findEvtId("error", 77, wev));
  TEST_ASSERT_EQUAL_STRING("cancelled (watchdog)", wev["msg"].as<const char*>());
  TEST_ASSERT_TRUE(robot->enabled());  // parking in progress
  TEST_ASSERT_TRUE(robot->motion().busy());
  advance(4000);
  TEST_ASSERT_FALSE(robot->enabled());
  TEST_ASSERT_FALSE(hal->oe_enabled);
  TEST_ASSERT_EQUAL(1, sink->countEvt("watchdog"));
  for (int a = 0; a < 2; ++a)
    for (int k = 0; k < 3; ++k) {
      const int ji = cfg::ARM_JOINTS[a][k];
      TEST_ASSERT_FLOAT_WITHIN(1e-3, robot->calibration().joints[ji].park, robot->motion().joint(ji));
    }
  // Disabled watchdog never trips.
  assertOk(req("{\"id\":3,\"cmd\":\"watchdog\",\"ms\":0}"));
  enable();
  advance(10000);
  TEST_ASSERT_TRUE(robot->enabled());
  TEST_ASSERT_EQUAL(1, sink->countEvt("watchdog"));
}

static void test_stream() {
  JsonDocument d = req("{\"id\":1,\"cmd\":\"stream\",\"hz\":10}");
  assertOk(d);
  TEST_ASSERT_EQUAL(10, d["hz"].as<int>());
  sink->clear();
  advance(1000);
  const int n = sink->countEvt("status");
  TEST_ASSERT_INT_WITHIN(1, 10, n);
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvt("status", ev));
  TEST_ASSERT_EQUAL(8, (int)ev["joints"].as<JsonObject>().size());
  TEST_ASSERT_TRUE(ev["faults"].is<JsonArray>());
  assertErr(req("{\"id\":2,\"cmd\":\"stream\",\"hz\":50}"), "bad_args");
  assertOk(req("{\"id\":2,\"cmd\":\"stream\",\"hz\":20}"));
  assertErr(req("{\"id\":3,\"cmd\":\"stream\",\"hz\":-1}"), "bad_args");
  assertOk(req("{\"id\":4,\"cmd\":\"stream\",\"hz\":0}"));
  sink->clear();
  advance(1000);
  TEST_ASSERT_EQUAL(0, sink->countEvt("status"));
}

static void test_phone_power() {
  JsonDocument d = req("{\"id\":1,\"cmd\":\"phone_power\",\"on\":false}");
  assertOk(d);
  TEST_ASSERT_FALSE(d["on"].as<bool>());
  TEST_ASSERT_FALSE(hal->phone_on);
  assertOk(req("{\"id\":2,\"cmd\":\"phone_power\",\"on\":true}"));
  TEST_ASSERT_TRUE(hal->phone_on);
  assertErr(req("{\"id\":3,\"cmd\":\"phone_power\",\"on\":1}"), "bad_args");
  // VBUS fault input is reported as a fault event.
  hal->vbus_fault = true;
  advance(100);
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvt("fault", ev));
  TEST_ASSERT_EQUAL_STRING("phone_vbus", ev["code"].as<const char*>());
}

static void test_brownout_pauses_motion() {
  enable();
  assertOk(req("{\"id\":1,\"cmd\":\"neck\",\"pan\":60,\"ms\":1000}"));
  advance(200);
  hal->vservo = 4.0f;
  advance(200);
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvt("fault", ev));
  TEST_ASSERT_EQUAL_STRING("brownout", ev["code"].as<const char*>());
  TEST_ASSERT_TRUE(ev["active"].as<bool>());
  JsonDocument s = req("{\"id\":2,\"cmd\":\"status\"}");
  TEST_ASSERT_EQUAL_STRING("brownout", s["faults"][0].as<const char*>());
  const float frozen = robot->motion().joint(cfg::J_NECK_PAN);
  advance(500);
  TEST_ASSERT_EQUAL_FLOAT(frozen, robot->motion().joint(cfg::J_NECK_PAN));
  hal->vservo = 5.0f;
  advance(100);
  TEST_ASSERT_TRUE(sink->findEvt("fault", ev));
  TEST_ASSERT_FALSE(ev["active"].as<bool>());
  runUntilIdle();
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 60.f, robot->motion().joint(cfg::J_NECK_PAN));
  TEST_ASSERT_TRUE(sink->findEvtId("done", 1, ev));
}

static void test_transport_routing() {
  sink->clear();
  robot->handleLine(TRANSPORT_AOA, "{\"id\":9,\"cmd\":\"ping\"}", 22);
  TEST_ASSERT_EQUAL(1, (int)sink->msgs.size());
  TEST_ASSERT_EQUAL(TRANSPORT_AOA, sink->msgs[0].transport);
  robot->setTransportConnected(TRANSPORT_AOA, true);
  TEST_ASSERT_TRUE(robot->linkUp());
  robot->setTransportConnected(TRANSPORT_AOA, false);
  TEST_ASSERT_FALSE(robot->linkUp());
  robot->handleLine(TRANSPORT_UART, "{\"id\":9,\"cmd\":\"ping\"}", 22);
  TEST_ASSERT_TRUE(robot->linkUp());
  hal->now += LINK_UART_TIMEOUT_MS + 1;
  TEST_ASSERT_FALSE(robot->linkUp());
}

static void test_button_and_led() {
  robot->onButton(ButtonAction::Short);
  TEST_ASSERT_TRUE(robot->enabled());
  JsonDocument ev;
  TEST_ASSERT_TRUE(sink->findEvt("button", ev));
  TEST_ASSERT_EQUAL_STRING("short", ev["action"].as<const char*>());
  Rgb c = robot->ledColor(hal->now);
  TEST_ASSERT_TRUE(c.g > 0 && c.r == 0);  // enabled -> green
  robot->onButton(ButtonAction::Short);
  TEST_ASSERT_FALSE(robot->enabled());
  // Long press: enable + home.
  assertOk(req("{\"id\":1,\"cmd\":\"watchdog\",\"ms\":0}"));
  robot->onButton(ButtonAction::Long);
  TEST_ASSERT_TRUE(robot->enabled());
  TEST_ASSERT_TRUE(sink->findEvt("button", ev));
  TEST_ASSERT_EQUAL_STRING("long", ev["action"].as<const char*>());
  runUntilIdle();
  for (int i = 0; i < NUM_JOINTS; ++i)
    TEST_ASSERT_FLOAT_WITHIN(1e-3, robot->calibration().joints[i].home, robot->motion().joint(i));
  // Fault -> red.
  hal->vservo = 3.0f;
  advance(300);
  c = robot->ledColor(0);
  TEST_ASSERT_TRUE(c.r > 0 && c.g == 0);
}

static void test_button_detector() {
  ButtonDetector b;
  uint32_t t = 0;
  TEST_ASSERT_EQUAL(ButtonAction::None, b.update(false, t));
  // Bounce shorter than debounce is ignored.
  TEST_ASSERT_EQUAL(ButtonAction::None, b.update(true, t += 5));
  TEST_ASSERT_EQUAL(ButtonAction::None, b.update(false, t += 5));
  for (int i = 0; i < 10; ++i) TEST_ASSERT_EQUAL(ButtonAction::None, b.update(false, t += 10));
  // Short press.
  ButtonAction got = ButtonAction::None;
  for (int i = 0; i < 20; ++i) b.update(true, t += 10);
  for (int i = 0; i < 10; ++i) {
    ButtonAction a = b.update(false, t += 10);
    if (a != ButtonAction::None) got = a;
  }
  TEST_ASSERT_EQUAL(ButtonAction::Short, got);
  // Long press fires once while held; release is swallowed.
  int longs = 0, shorts = 0;
  for (int i = 0; i < 200; ++i) {
    ButtonAction a = b.update(true, t += 10);
    longs += a == ButtonAction::Long;
  }
  for (int i = 0; i < 10; ++i) shorts += b.update(false, t += 10) == ButtonAction::Short;
  TEST_ASSERT_EQUAL(1, longs);
  TEST_ASSERT_EQUAL(0, shorts);
}

static void test_line_assembler() {
  LineAssembler la;
  const char* in = "{\"a\":1}\r\n\n{\"b\":2}\n";
  int lines = 0;
  std::string last;
  for (const char* p = in; *p; ++p)
    if (la.feed(*p) == LineAssembler::Result::Line) {
      ++lines;
      last.assign(la.line(), la.lineLen());
    }
  TEST_ASSERT_EQUAL(2, lines);
  TEST_ASSERT_EQUAL_STRING("{\"b\":2}", last.c_str());
  // Overflow is reported once, then normal lines resume.
  int overflows = 0;
  for (size_t i = 0; i < MAX_LINE_LEN + 50; ++i) overflows += la.feed('x') == LineAssembler::Result::Overflow;
  overflows += la.feed('\n') == LineAssembler::Result::Overflow;
  TEST_ASSERT_EQUAL(1, overflows);
  for (const char* p = "{}\n"; *p; ++p)
    if (la.feed(*p) == LineAssembler::Result::Line) last.assign(la.line(), la.lineLen());
  TEST_ASSERT_EQUAL_STRING("{}", last.c_str());
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_boot_hello_ping);
  RUN_TEST(test_error_codes);
  RUN_TEST(test_enable_relax_outputs);
  RUN_TEST(test_joints_neck_home_done_events);
  RUN_TEST(test_status_shape);
  RUN_TEST(test_move_tap_swipe_park);
  RUN_TEST(test_queue_full_stop_relax_cancel);
  RUN_TEST(test_servo_raw);
  RUN_TEST(test_calib_get_set_reset);
  RUN_TEST(test_geom_get_set);
  RUN_TEST(test_fk_ik);
  RUN_TEST(test_watchdog_parks_then_relaxes);
  RUN_TEST(test_stream);
  RUN_TEST(test_phone_power);
  RUN_TEST(test_brownout_pauses_motion);
  RUN_TEST(test_transport_routing);
  RUN_TEST(test_button_and_led);
  RUN_TEST(test_button_detector);
  RUN_TEST(test_line_assembler);
  return UNITY_END();
}
