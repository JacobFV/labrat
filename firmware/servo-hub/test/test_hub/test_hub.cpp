// The protocol of docs/protocol.md section 4 end to end against the fake HAL: every command, concurrent timed
// motions, preemption, inputs, estop, the watchdog, persistence and the error paths.
#include <ArduinoJson.h>
#include <unity.h>

#include <string>

#include "../support/fake_hal.h"
#include "hub.h"

using namespace hub;
using namespace hub_test;

static FakeHal* hal;
static FakeSink* sink;
static Hub* h;

void setUp() {
  hal = new FakeHal();
  hal->closed[1] = true;  // estop is normally closed: closed = not engaged
  sink = new FakeSink();
  h = new Hub(*hal, *sink, TEST_BOARD);
  h->begin();
  sink->clear();
}

void tearDown() {
  delete h;
  delete sink;
  delete hal;
}

static void advance(uint32_t ms, uint32_t step = 1) {
  for (uint32_t t = 0; t < ms; t += step) {
    hal->now += step;
    h->loop();
  }
}

static void send(const char* line, int transport = T_UART) {
  std::string s = std::string(line) + "\n";
  h->feed(transport, s.data(), s.size());
}

static JsonDocument parse(const std::string& s) {
  JsonDocument d;
  TEST_ASSERT_FALSE_MESSAGE(deserializeJson(d, s), s.c_str());
  return d;
}

// Finds the reply with this id (and removes it); fails if missing.
static JsonDocument reply(int id) {
  for (size_t i = 0; i < sink->lines.size(); i++) {
    JsonDocument d = parse(sink->lines[i].text);
    if (d["id"].is<int>() && d["id"].as<int>() == id) {
      sink->lines.erase(sink->lines.begin() + i);
      return d;
    }
  }
  TEST_FAIL_MESSAGE(("no reply for id " + std::to_string(id)).c_str());
  return JsonDocument();
}

static bool hasReply(int id) {
  for (auto& l : sink->lines) {
    JsonDocument d = parse(l.text);
    if (d["id"].is<int>() && d["id"].as<int>() == id) return true;
  }
  return false;
}

static int countEv(const char* ev) {
  int n = 0;
  for (auto& l : sink->lines) {
    JsonDocument d = parse(l.text);
    if (d["ev"] == ev) n++;
  }
  return n;
}

static JsonDocument lastEv(const char* ev) {
  JsonDocument out;
  for (auto& l : sink->lines) {
    JsonDocument d = parse(l.text);
    if (d["ev"] == ev) out = d;
  }
  return out;
}

// ---------------------------------------------------------------------------------------------------------------

void test_boot_event() {
  FakeHal hal2;
  hal2.closed[1] = true;
  FakeSink s2;
  Hub h2(hal2, s2, TEST_BOARD);
  h2.begin();
  TEST_ASSERT_EQUAL_INT(1, (int)s2.lines.size());
  TEST_ASSERT_EQUAL_INT(T_ALL, s2.lines[0].transport);
  JsonDocument d = parse(s2.lines[0].text);
  TEST_ASSERT_EQUAL_STRING("boot", d["ev"]);
  TEST_ASSERT_TRUE(hal2.oe_enabled);
}

void test_hello() {
  hal->now += 1234;
  send("{\"id\":1,\"cmd\":\"hello\"}");
  JsonDocument d = reply(1);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_TRUE(std::string(d["fw"].as<const char*>()).rfind("servo-hub/1.", 0) == 0);
  TEST_ASSERT_EQUAL_STRING("test", d["board"]);
  TEST_ASSERT_EQUAL_INT(16, d["channels"]);
  TEST_ASSERT_EQUAL_INT(3, d["inputs"].size());
  TEST_ASSERT_EQUAL_STRING("syr_home", d["inputs"][0]);
  TEST_ASSERT_EQUAL_UINT32(1234, d["uptime_ms"]);
  TEST_ASSERT_EQUAL_INT(0, d["estop"]);
}

void test_ping_and_errors() {
  send("{\"id\":2,\"cmd\":\"ping\"}");
  JsonDocument d = reply(2);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_EQUAL_INT(2, d.size());  // {"id":2,"ok":true}
  send("{\"cmd\":\"ping\"}");          // id-less keepalive: silent
  TEST_ASSERT_EQUAL_INT(0, (int)sink->lines.size());
  send("{\"id\":3,\"cmd\":\"fly\"}");
  TEST_ASSERT_EQUAL_STRING("unknown_cmd", reply(3)["err"]);
  send("not json");
  TEST_ASSERT_EQUAL_STRING("json", parse(sink->lines.back().text)["err"]);
  sink->clear();
  std::string longLine(400, 'x');
  send(longLine.c_str());
  TEST_ASSERT_EQUAL_STRING("too_long", parse(sink->lines.back().text)["err"]);
  sink->clear();
  send("{\"id\":4,\"cmd\":\"servo\",\"ch\":16,\"deg\":10}");
  TEST_ASSERT_EQUAL_STRING("ch", reply(4)["err"]);
  send("{\"id\":5,\"cmd\":\"servo\",\"ch\":1,\"deg\":190}");
  TEST_ASSERT_EQUAL_STRING("range", reply(5)["err"]);
  send("{\"id\":6,\"cmd\":\"servo\",\"ch\":1}");
  TEST_ASSERT_EQUAL_STRING("args", reply(6)["err"]);
  send("{\"id\":7,\"cmd\":\"servo\",\"ch\":0,\"deg\":90}");  // ch0 is continuous by default
  TEST_ASSERT_EQUAL_STRING("kind", reply(7)["err"]);
  send("{\"id\":8,\"cmd\":\"pulse\",\"ch\":1,\"us\":3000}");
  TEST_ASSERT_EQUAL_STRING("range", reply(8)["err"]);
  send("{\"id\":9,\"cmd\":\"pulse\",\"ch\":0,\"us\":1600,\"ms\":100,\"until\":\"nope\"}");
  TEST_ASSERT_EQUAL_STRING("input", reply(9)["err"]);
  send("{\"id\":10,\"cmd\":\"pulse\",\"ch\":0,\"us\":1600,\"until\":\"syr_home\"}");
  TEST_ASSERT_EQUAL_STRING("until_needs_ms", reply(10)["err"]);
  send("{\"id\":11,\"cmd\":\"pulse\",\"ch\":0,\"us\":1600,\"ms\":-1}");
  TEST_ASSERT_EQUAL_STRING("range", reply(11)["err"]);
  hal->pwm_ok = false;
  send("{\"id\":12,\"cmd\":\"servo\",\"ch\":1,\"deg\":10}");
  TEST_ASSERT_EQUAL_STRING("pwm", reply(12)["err"]);
}

void test_servo_jump() {
  send("{\"id\":1,\"cmd\":\"servo\",\"ch\":2,\"deg\":90}");
  JsonDocument d = reply(1);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_EQUAL_FLOAT(90.f, d["deg"].as<float>());
  TEST_ASSERT_EQUAL_UINT16(1500, hal->pwm[2]);
  TEST_ASSERT_EQUAL_UINT16(1500, h->outputUs(2));
}

void test_servo_eased_replies_on_completion() {
  send("{\"id\":1,\"cmd\":\"servo\",\"ch\":3,\"deg\":0}");
  reply(1);
  TEST_ASSERT_EQUAL_UINT16(500, hal->pwm[3]);
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":3,\"deg\":180,\"ms\":1000}");
  TEST_ASSERT_FALSE(hasReply(2));
  TEST_ASSERT_TRUE(h->busy(3));
  advance(100);
  uint16_t early = hal->pwm[3];
  TEST_ASSERT_TRUE(early > 500 && early < 1000);  // smoothstep: slow start (10% of the time, ~3% of the way)
  advance(400);
  TEST_ASSERT_UINT16_WITHIN(40, 1500, hal->pwm[3]);  // halfway at half time (updates every 10 ms)
  uint16_t prev = hal->pwm[3];
  for (int i = 0; i < 49; i++) {
    advance(10);
    TEST_ASSERT_TRUE(hal->pwm[3] >= prev);
    prev = hal->pwm[3];
  }
  TEST_ASSERT_FALSE(hasReply(2));
  advance(20);
  JsonDocument d = reply(2);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_EQUAL_FLOAT(180.f, d["deg"].as<float>());
  TEST_ASSERT_EQUAL_UINT16(2500, hal->pwm[3]);
  TEST_ASSERT_FALSE(h->busy(3));
}

void test_servo_never_driven_jumps_then_waits() {
  send("{\"id\":1,\"cmd\":\"servo\",\"ch\":1,\"deg\":45,\"ms\":300}");
  TEST_ASSERT_EQUAL_UINT16(1000, hal->pwm[1]);
  advance(299);
  TEST_ASSERT_FALSE(hasReply(1));
  advance(2);
  TEST_ASSERT_TRUE(reply(1)["ok"]);
}

void test_preempt_and_concurrency() {
  send("{\"id\":1,\"cmd\":\"servo\",\"ch\":2,\"deg\":0}");
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":3,\"deg\":0}");
  reply(1);
  reply(2);
  send("{\"id\":3,\"cmd\":\"servo\",\"ch\":2,\"deg\":180,\"ms\":500}");
  send("{\"id\":4,\"cmd\":\"servo\",\"ch\":3,\"deg\":90,\"ms\":200}");
  send("{\"id\":5,\"cmd\":\"pulse\",\"ch\":0,\"us\":1700,\"ms\":300}", T_BLE);
  send("{\"id\":6,\"cmd\":\"read\"}");  // others keep being served meanwhile
  TEST_ASSERT_TRUE(reply(6)["ok"]);
  advance(201);
  TEST_ASSERT_TRUE(reply(4)["ok"]);
  TEST_ASSERT_FALSE(hasReply(3));
  TEST_ASSERT_FALSE(hasReply(5));
  // a new command on ch2 cancels its running motion
  send("{\"id\":7,\"cmd\":\"servo\",\"ch\":2,\"deg\":10}");
  JsonDocument p = reply(3);
  TEST_ASSERT_FALSE(p["ok"]);
  TEST_ASSERT_EQUAL_STRING("preempted", p["err"]);
  TEST_ASSERT_TRUE(reply(7)["ok"]);
  advance(100);
  // the pulse reply goes back over the transport it came from
  bool found = false;
  for (auto& l : sink->lines) {
    JsonDocument d = parse(l.text);
    if (d["id"] == 5) {
      TEST_ASSERT_EQUAL_INT(T_BLE, l.transport);
      found = true;
    }
  }
  TEST_ASSERT_TRUE(found);
}

void test_pulse_hold_returns_to_rest() {
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":0,\"us\":1800,\"ms\":250}");
  TEST_ASSERT_EQUAL_UINT16(1800, hal->pwm[0]);
  advance(249);
  TEST_ASSERT_FALSE(hasReply(1));
  TEST_ASSERT_EQUAL_UINT16(1800, hal->pwm[0]);
  advance(1);
  JsonDocument d = reply(1);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_EQUAL_UINT32(250, d["ms"]);
  TEST_ASSERT_TRUE(d["stopped_by"].isNull());
  TEST_ASSERT_EQUAL_UINT16(1500, hal->pwm[0]);  // ch0 rest_us = neutral
  // a positional channel (rest_us 0) goes off
  send("{\"id\":2,\"cmd\":\"pulse\",\"ch\":4,\"us\":1200,\"ms\":50}");
  advance(50);
  TEST_ASSERT_TRUE(reply(2)["ok"]);
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[4]);
}

void test_pulse_without_ms_stays() {
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":5,\"us\":1234}");
  JsonDocument d = reply(1);
  TEST_ASSERT_EQUAL_UINT16(1234, d["us"].as<uint16_t>());
  advance(500);
  TEST_ASSERT_EQUAL_UINT16(1234, hal->pwm[5]);
  send("{\"id\":2,\"cmd\":\"pulse\",\"ch\":5,\"us\":0}");  // us 0 = off
  reply(2);
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[5]);
}

void test_pulse_until_input() {
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":0,\"us\":1300,\"ms\":5000,\"until\":\"syr_home\"}");
  advance(400);
  TEST_ASSERT_EQUAL_UINT16(1300, hal->pwm[0]);
  hal->closed[0] = true;  // plunger hits the bottom-stop switch
  advance(19);
  TEST_ASSERT_FALSE(hasReply(1));  // still debouncing
  advance(2);
  JsonDocument d = reply(1);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_EQUAL_STRING("syr_home", d["stopped_by"]);
  TEST_ASSERT_UINT32_WITHIN(2, 420, d["ms"].as<uint32_t>());
  TEST_ASSERT_EQUAL_UINT16(1500, hal->pwm[0]);
  TEST_ASSERT_EQUAL_INT(1, countEv("in"));
  // already at the stop: no drive at all
  hal->writes = 0;
  send("{\"id\":2,\"cmd\":\"pulse\",\"ch\":0,\"us\":1300,\"ms\":5000,\"until\":\"syr_home\"}");
  d = reply(2);
  TEST_ASSERT_EQUAL_STRING("syr_home", d["stopped_by"]);
  TEST_ASSERT_EQUAL_UINT32(0, d["ms"]);
  TEST_ASSERT_EQUAL_INT(0, hal->writes);
}

void test_off() {
  send("{\"id\":1,\"cmd\":\"servo\",\"ch\":1,\"deg\":30}");
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":2,\"deg\":30,\"ms\":1000}");
  reply(1);
  send("{\"id\":3,\"cmd\":\"off\",\"ch\":1}");
  TEST_ASSERT_TRUE(reply(3)["ok"]);
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[1]);
  TEST_ASSERT_TRUE(h->busy(2));
  send("{\"id\":4,\"cmd\":\"off\"}");
  TEST_ASSERT_TRUE(reply(4)["ok"]);
  TEST_ASSERT_EQUAL_STRING("stopped", reply(2)["err"]);
  for (int ch = 0; ch < NUM_CH; ch++) TEST_ASSERT_EQUAL_UINT16(0, h->outputUs(ch));
  // easing after a relax starts from where the servo was last driven
  send("{\"id\":5,\"cmd\":\"servo\",\"ch\":1,\"deg\":90,\"ms\":200}");
  TEST_ASSERT_EQUAL_UINT16(833, hal->pwm[1]);  // 30 deg
}

void test_stop_settles_continuous_then_off() {
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":0,\"us\":1900,\"ms\":3000}");
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":2,\"deg\":120}");
  reply(2);
  advance(100);
  send("{\"id\":3,\"cmd\":\"stop\"}");
  TEST_ASSERT_TRUE(reply(3)["ok"]);
  TEST_ASSERT_EQUAL_STRING("stopped", reply(1)["err"]);
  TEST_ASSERT_EQUAL_UINT16(1500, hal->pwm[0]);  // neutral first
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[2]);
  advance(SETTLE_MS + 1);
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[0]);     // then off
}

void test_cfg_persist_and_validate() {
  send("{\"id\":1,\"cmd\":\"cfg\",\"ch\":1,\"min_us\":600,\"max_us\":2400}");
  JsonDocument d = reply(1);
  TEST_ASSERT_TRUE(d["ok"]);
  TEST_ASSERT_EQUAL_INT(600, d["cfg"]["min_us"]);
  TEST_ASSERT_EQUAL_STRING("pos", d["cfg"]["kind"]);
  TEST_ASSERT_EQUAL_INT(1, (int)hal->nvs.count(1));
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":1,\"deg\":90}");
  reply(2);
  TEST_ASSERT_EQUAL_UINT16(1500, hal->pwm[1]);
  send("{\"id\":3,\"cmd\":\"cfg\",\"ch\":1,\"min_us\":2500}");  // min >= max
  TEST_ASSERT_EQUAL_STRING("range", reply(3)["err"]);
  send("{\"id\":4,\"cmd\":\"cfg\",\"ch\":1,\"rest_us\":3000}");
  TEST_ASSERT_EQUAL_STRING("range", reply(4)["err"]);
  send("{\"id\":5,\"cmd\":\"cfg\",\"ch\":1,\"kind\":\"weird\"}");
  TEST_ASSERT_EQUAL_STRING("kind", reply(5)["err"]);
  send("{\"id\":6,\"cmd\":\"cfg\",\"ch\":4,\"kind\":\"cont\",\"rest_us\":1500}");  // second syringe
  TEST_ASSERT_EQUAL_STRING("cont", reply(6)["cfg"]["kind"]);
  // a new Hub on the same "flash" loads it
  FakeSink s2;
  Hub h2(*hal, s2, TEST_BOARD);
  h2.begin();
  TEST_ASSERT_EQUAL_UINT16(600, h2.cfg(1).min_us);
  TEST_ASSERT_TRUE(h2.cfg(4).kind == Kind::Cont);
  TEST_ASSERT_EQUAL_UINT16(1500, h2.cfg(4).rest_us);
  // resending the same cfg neither writes flash nor preempts a running motion
  hal->nvs.clear();
  send("{\"id\":20,\"cmd\":\"servo\",\"ch\":1,\"deg\":10,\"ms\":500}");
  send("{\"id\":21,\"cmd\":\"cfg\",\"ch\":1,\"min_us\":600,\"max_us\":2400,\"kind\":\"pos\"}");
  TEST_ASSERT_TRUE(reply(21)["ok"]);
  TEST_ASSERT_EQUAL_INT(0, (int)hal->nvs.size());
  TEST_ASSERT_TRUE(h->busy(1));
  hal->nvs[1] = h->cfg(1);
  hal->nvs[4] = h->cfg(4);
  // read-only cfg and reset
  send("{\"id\":7,\"cmd\":\"cfg\",\"ch\":4}");
  TEST_ASSERT_EQUAL_INT(1500, reply(7)["cfg"]["rest_us"]);
  send("{\"id\":8,\"cmd\":\"cfg\",\"ch\":4,\"reset\":true}");
  TEST_ASSERT_EQUAL_STRING("pos", reply(8)["cfg"]["kind"]);
  TEST_ASSERT_EQUAL_INT(0, (int)hal->nvs.count(4));
}

void test_read() {
  hal->adc[0] = 1650;
  hal->closed[2] = true;
  advance(25);
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":7,\"us\":1111}");
  reply(1);
  send("{\"id\":2,\"cmd\":\"read\"}");
  JsonDocument d = reply(2);
  TEST_ASSERT_EQUAL_INT(0, d["inputs"]["syr_home"]);
  TEST_ASSERT_EQUAL_INT(0, d["inputs"]["estop"]);
  TEST_ASSERT_EQUAL_INT(1, d["inputs"]["in1"]);
  TEST_ASSERT_EQUAL_INT(1650, d["adc"]["a0"]);
  TEST_ASSERT_EQUAL_INT(16, d["ch"].size());
  TEST_ASSERT_EQUAL_INT(1111, d["ch"][7]);
  std::string s;
  serializeJson(d, s);
  TEST_ASSERT_TRUE(s.size() < 255);
}

void test_input_debounce_events() {
  hal->closed[2] = true;
  advance(5);
  hal->closed[2] = false;  // bounce
  advance(5);
  hal->closed[2] = true;
  advance(15);
  TEST_ASSERT_EQUAL_INT(0, countEv("in"));
  advance(10);
  TEST_ASSERT_EQUAL_INT(1, countEv("in"));
  JsonDocument e = lastEv("in");
  TEST_ASSERT_EQUAL_STRING("in1", e["name"]);
  TEST_ASSERT_EQUAL_INT(1, e["v"]);
  TEST_ASSERT_EQUAL_INT(T_ALL, sink->lines.back().transport);
  hal->closed[2] = false;
  advance(30);
  TEST_ASSERT_EQUAL_INT(2, countEv("in"));
  TEST_ASSERT_EQUAL_INT(0, lastEv("in")["v"]);
}

void test_estop() {
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":0,\"us\":1800,\"ms\":3000}");
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":3,\"deg\":90}");
  reply(2);
  hal->closed[1] = false;  // NC button pressed (or its wire cut): contact opens
  advance(11);
  JsonDocument p = reply(1);
  TEST_ASSERT_EQUAL_STRING("estop", p["err"]);
  TEST_ASSERT_EQUAL_INT(1, countEv("estop"));
  TEST_ASSERT_EQUAL_INT(1, lastEv("estop")["v"]);
  TEST_ASSERT_EQUAL_INT(0, countEv("in"));
  TEST_ASSERT_FALSE(hal->oe_enabled);
  for (int ch = 0; ch < NUM_CH; ch++) TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[ch]);
  TEST_ASSERT_TRUE(h->estopped());
  send("{\"id\":3,\"cmd\":\"servo\",\"ch\":3,\"deg\":10}");
  TEST_ASSERT_EQUAL_STRING("estop", reply(3)["err"]);
  send("{\"id\":4,\"cmd\":\"pulse\",\"ch\":0,\"us\":1600,\"ms\":100}");
  TEST_ASSERT_EQUAL_STRING("estop", reply(4)["err"]);
  send("{\"id\":5,\"cmd\":\"read\"}");
  TEST_ASSERT_EQUAL_INT(1, reply(5)["inputs"]["estop"]);
  hal->closed[1] = true;  // released
  advance(11);
  TEST_ASSERT_EQUAL_INT(0, lastEv("estop")["v"]);
  TEST_ASSERT_TRUE(hal->oe_enabled);
  send("{\"id\":6,\"cmd\":\"servo\",\"ch\":3,\"deg\":10}");
  TEST_ASSERT_TRUE(reply(6)["ok"]);
}

void test_estop_open_at_boot() {
  FakeHal hal2;  // estop contact open from power-up (button pressed, or not wired and no jumper)
  FakeSink s2;
  Hub h2(hal2, s2, TEST_BOARD);
  h2.begin();
  TEST_ASSERT_TRUE(h2.estopped());
  TEST_ASSERT_FALSE(hal2.oe_enabled);
  TEST_ASSERT_EQUAL_INT(2, (int)s2.lines.size());  // boot + estop
  std::string l = "{\"id\":1,\"cmd\":\"servo\",\"ch\":2,\"deg\":10}\n";
  h2.feed(T_UART, l.data(), l.size());
  TEST_ASSERT_TRUE(s2.lines.back().text.find("estop") != std::string::npos);
}

void test_watchdog() {
  advance(5000);  // never armed before the first line
  TEST_ASSERT_EQUAL_INT(0, countEv("wd"));
  send("{\"id\":1,\"cmd\":\"pulse\",\"ch\":0,\"us\":1800,\"ms\":10000}");
  send("{\"id\":2,\"cmd\":\"servo\",\"ch\":2,\"deg\":45}");
  reply(2);
  for (int i = 0; i < 5; i++) {  // 1 s pings keep it alive
    advance(1000);
    send("{\"cmd\":\"ping\"}");
  }
  TEST_ASSERT_EQUAL_INT(0, countEv("wd"));
  TEST_ASSERT_EQUAL_UINT16(1800, hal->pwm[0]);
  advance(1999);
  TEST_ASSERT_EQUAL_INT(0, countEv("wd"));
  advance(1);
  TEST_ASSERT_EQUAL_INT(1, countEv("wd"));
  TEST_ASSERT_EQUAL_STRING("watchdog", reply(1)["err"]);
  TEST_ASSERT_EQUAL_UINT16(1500, hal->pwm[0]);
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[2]);
  advance(SETTLE_MS + 1);
  TEST_ASSERT_EQUAL_UINT16(0, hal->pwm[0]);
  advance(5000);
  TEST_ASSERT_EQUAL_INT(1, countEv("wd"));  // fires once per silence
  send("{\"cmd\":\"ping\"}", T_BLE);         // any transport re-arms it
  advance(2000);
  TEST_ASSERT_EQUAL_INT(2, countEv("wd"));
}

void test_split_writes() {
  // BLE delivers a line in 20-byte pieces, possibly two lines in one write
  std::string a = "{\"id\":1,\"cmd\":\"ser";
  std::string b = "vo\",\"ch\":2,\"deg\":90}\n{\"id\":2,";
  std::string c = "\"cmd\":\"ping\"}\n";
  h->feed(T_BLE, a.data(), a.size());
  TEST_ASSERT_EQUAL_INT(0, (int)sink->lines.size());
  h->feed(T_BLE, b.data(), b.size());
  h->feed(T_BLE, c.data(), c.size());
  TEST_ASSERT_TRUE(reply(1)["ok"]);
  TEST_ASSERT_TRUE(reply(2)["ok"]);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_boot_event);
  RUN_TEST(test_hello);
  RUN_TEST(test_ping_and_errors);
  RUN_TEST(test_servo_jump);
  RUN_TEST(test_servo_eased_replies_on_completion);
  RUN_TEST(test_servo_never_driven_jumps_then_waits);
  RUN_TEST(test_preempt_and_concurrency);
  RUN_TEST(test_pulse_hold_returns_to_rest);
  RUN_TEST(test_pulse_without_ms_stays);
  RUN_TEST(test_pulse_until_input);
  RUN_TEST(test_off);
  RUN_TEST(test_stop_settles_continuous_then_off);
  RUN_TEST(test_cfg_persist_and_validate);
  RUN_TEST(test_read);
  RUN_TEST(test_input_debounce_events);
  RUN_TEST(test_estop);
  RUN_TEST(test_estop_open_at_boot);
  RUN_TEST(test_watchdog);
  RUN_TEST(test_split_writes);
  return UNITY_END();
}
