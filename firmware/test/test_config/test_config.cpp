// Asserts that the generated include/robot_config.h matches config/robot.json
// (catches a stale header) and that NVS blob packing round-trips.
#include <ArduinoJson.h>
#include <unity.h>

#include <string>

#include "../support/fake_hal.h"
#include "calibration.h"
#include "robot_config.h"

using namespace tappet;
using namespace tappet_test;

static JsonDocument robot;

void setUp() {}
void tearDown() {}

static void assertNear(double expected, double actual, const char* what) {
  char m[128];
  snprintf(m, sizeof(m), "%s", what);
  TEST_ASSERT_DOUBLE_WITHIN_MESSAGE(1e-6, expected, actual, m);
}

static void test_robot_json_loaded() {
  const std::string s = readConfigFile("robot.json");
  TEST_ASSERT_TRUE_MESSAGE(!s.empty(), "config/robot.json not found");
  TEST_ASSERT_FALSE(deserializeJson(robot, s));
  TEST_ASSERT_EQUAL_STRING(robot["version"].as<const char*>(), cfg::ROBOT_VERSION);
  TEST_ASSERT_EQUAL_STRING(robot["name"].as<const char*>(), cfg::ROBOT_NAME);
}

static void test_joints_match() {
  JsonObject joints = robot["joints"];
  TEST_ASSERT_EQUAL(cfg::NUM_JOINTS, (int)joints.size());
  int i = 0;
  for (JsonPair kv : joints) {
    const cfg::JointDefault& d = cfg::JOINTS[i];
    TEST_ASSERT_EQUAL_STRING(kv.key().c_str(), d.name);
    JsonObject j = kv.value();
    TEST_ASSERT_EQUAL(j["ch"].as<int>(), d.ch);
    TEST_ASSERT_EQUAL(j["dir"].as<int>(), d.dir);
    assertNear(j["gear"].as<double>(), d.gear, "gear");
    assertNear(j["s_zero"].as<double>(), d.s_zero, "s_zero");
    assertNear(j["neutral"].as<double>(), d.neutral, "neutral");
    assertNear(j["min"].as<double>(), d.min, "min");
    assertNear(j["max"].as<double>(), d.max, "max");
    assertNear(j["home"].as<double>(), d.home, "home");
    assertNear(j["park"].as<double>(), d.park, "park");
    assertNear(j["max_vel"].as<double>(), d.max_vel, "max_vel");
    TEST_ASSERT_EQUAL(i, jointIndex(d.name));
    ++i;
  }
  // Arm joint index table follows the <ARM>_<YAW|SHOULDER|ELBOW> naming.
  const char* sfx[3] = {"YAW", "SHOULDER", "ELBOW"};
  for (int a = 0; a < cfg::NUM_ARMS; ++a)
    for (int k = 0; k < 3; ++k) {
      std::string n = std::string(cfg::ARM_NAMES[a]) + "_" + sfx[k];
      TEST_ASSERT_EQUAL(jointIndex(n.c_str()), cfg::ARM_JOINTS[a][k]);
    }
}

static void test_arms_linkage_tool_match() {
  const char* names[2] = {"L", "R"};
  for (int a = 0; a < 2; ++a) {
    JsonObject g = robot["arms"][names[a]];
    const cfg::ArmDefault& d = cfg::ARMS[a];
    assertNear(g["base_x"].as<double>(), d.base_x, "base_x");
    assertNear(g["base_y"].as<double>(), d.base_y, "base_y");
    assertNear(g["shoulder_h"].as<double>(), d.shoulder_h, "shoulder_h");
    assertNear(g["r0"].as<double>(), d.r0, "r0");
    assertNear(g["L1"].as<double>(), d.L1, "L1");
    assertNear(g["L2"].as<double>(), d.L2, "L2");
    assertNear(g["tool_dx"].as<double>(), d.tool_dx, "tool_dx");
    assertNear(g["tool_dz"].as<double>(), d.tool_dz, "tool_dz");
  }
  assertNear(robot["linkage"]["rel_min"].as<double>(), cfg::LINKAGE_REL_MIN, "rel_min");
  assertNear(robot["linkage"]["rel_max"].as<double>(), cfg::LINKAGE_REL_MAX, "rel_max");
  assertNear(robot["tool"]["default_press_mm"].as<double>(), cfg::TOOL_DEFAULT_PRESS_MM, "press");
  assertNear(robot["tool"]["default_approach_mm"].as<double>(), cfg::TOOL_DEFAULT_APPROACH_MM, "approach");
  assertNear(robot["tool"]["default_hold_ms"].as<double>(), cfg::TOOL_DEFAULT_HOLD_MS, "hold");
  assertNear(robot["servo"]["us_min"].as<double>(), cfg::SERVO_US_MIN, "us_min");
  assertNear(robot["servo"]["us_max"].as<double>(), cfg::SERVO_US_MAX, "us_max");
  assertNear(robot["servo"]["deg_range"].as<double>(), cfg::SERVO_DEG_RANGE, "deg_range");
  assertNear(robot["servo"]["pwm_hz"].as<double>(), cfg::SERVO_PWM_HZ, "pwm_hz");
}

static void test_pins_link_match() {
  JsonObject pins = robot["electronics"]["pins"];
  TEST_ASSERT_EQUAL(pins["I2C_SDA"].as<int>(), cfg::PIN_I2C_SDA);
  TEST_ASSERT_EQUAL(pins["I2C_SCL"].as<int>(), cfg::PIN_I2C_SCL);
  TEST_ASSERT_EQUAL(pins["PCA_OE"].as<int>(), cfg::PIN_PCA_OE);
  TEST_ASSERT_EQUAL(pins["USB_DN"].as<int>(), cfg::PIN_USB_DN);
  TEST_ASSERT_EQUAL(pins["USB_DP"].as<int>(), cfg::PIN_USB_DP);
  TEST_ASSERT_EQUAL(pins["PHONE_VBUS_EN"].as<int>(), cfg::PIN_PHONE_VBUS_EN);
  TEST_ASSERT_EQUAL(pins["PHONE_VBUS_FLT"].as<int>(), cfg::PIN_PHONE_VBUS_FLT);
  TEST_ASSERT_EQUAL(pins["VSERVO_SENSE"].as<int>(), cfg::PIN_VSERVO_SENSE);
  TEST_ASSERT_EQUAL(pins["VIN_SENSE"].as<int>(), cfg::PIN_VIN_SENSE);
  TEST_ASSERT_EQUAL(pins["STATUS_LED"].as<int>(), cfg::PIN_STATUS_LED);
  TEST_ASSERT_EQUAL(pins["USER_BUTTON"].as<int>(), cfg::PIN_USER_BUTTON);
  JsonObject div = robot["electronics"]["dividers"];
  assertNear(div["VSERVO_SENSE"]["r_top"].as<double>(), cfg::DIV_VSERVO_SENSE_R_TOP, "vs rtop");
  assertNear(div["VSERVO_SENSE"]["r_bot"].as<double>(), cfg::DIV_VSERVO_SENSE_R_BOT, "vs rbot");
  assertNear(div["VIN_SENSE"]["r_top"].as<double>(), cfg::DIV_VIN_SENSE_R_TOP, "vin rtop");
  assertNear(div["VIN_SENSE"]["r_bot"].as<double>(), cfg::DIV_VIN_SENSE_R_BOT, "vin rbot");
  TEST_ASSERT_EQUAL(0x40, cfg::PCA9685_I2C_ADDR);
  assertNear(25e6, cfg::PCA9685_OSC_HZ, "osc");

  JsonObject link = robot["link"];
  TEST_ASSERT_EQUAL_UINT32(link["uart_baud"].as<uint32_t>(), cfg::UART_BAUD);
  TEST_ASSERT_EQUAL_UINT32(link["watchdog_ms"].as<uint32_t>(), cfg::WATCHDOG_MS);
  const char* keys[6] = {"manufacturer", "model", "description", "version", "uri", "serial"};
  for (int i = 0; i < 6; ++i) TEST_ASSERT_EQUAL_STRING(link["aoa"][keys[i]].as<const char*>(), cfg::AOA_STRINGS[i]);
  TEST_ASSERT_EQUAL_STRING("Tappet", cfg::AOA_MANUFACTURER);
  TEST_ASSERT_EQUAL_STRING("TappetBody", cfg::AOA_MODEL);
}

static void test_defaults_valid_and_blob_roundtrip() {
  Calibration c = Calibration::defaults();
  char msg[96];
  TEST_ASSERT_TRUE_MESSAGE(c.validate(msg, sizeof(msg)), msg);
  c.joints[0].s_zero = 93.5f;
  c.arms[1].L1 = 101.25f;
  CalibBlob b;
  packCalib(c, b);
  Calibration out = Calibration::defaults();
  TEST_ASSERT_TRUE(unpackCalib(&b, sizeof(b), out));
  TEST_ASSERT_EQUAL_FLOAT(93.5f, out.joints[0].s_zero);
  TEST_ASSERT_EQUAL_FLOAT(101.25f, out.arms[1].L1);
  // Corruption is detected.
  reinterpret_cast<uint8_t*>(&b.cal)[5] ^= 0x5A;
  TEST_ASSERT_FALSE(unpackCalib(&b, sizeof(b), out));
  TEST_ASSERT_FALSE(unpackCalib(&b, sizeof(b) - 1, out));
}

static void test_validate_rejects_bad_values() {
  Calibration c = Calibration::defaults();
  c.joints[2].dir = 0;
  TEST_ASSERT_FALSE(c.validate(nullptr, 0));
  c = Calibration::defaults();
  c.joints[2].home = c.joints[2].max + 1;
  TEST_ASSERT_FALSE(c.validate(nullptr, 0));
  c = Calibration::defaults();
  c.linkage.rel_min = 170;
  TEST_ASSERT_FALSE(c.validate(nullptr, 0));
  c = Calibration::defaults();
  c.joints[6].ch = 16;
  TEST_ASSERT_FALSE(c.validate(nullptr, 0));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_robot_json_loaded);
  RUN_TEST(test_joints_match);
  RUN_TEST(test_arms_linkage_tool_match);
  RUN_TEST(test_pins_link_match);
  RUN_TEST(test_defaults_valid_and_blob_roundtrip);
  RUN_TEST(test_validate_rejects_bad_values);
  return UNITY_END();
}
