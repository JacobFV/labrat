// In-memory HAL + line sink for the native tests.
#pragma once

#include <ArduinoJson.h>

#include <map>
#include <string>
#include <vector>

#include "hal.h"

namespace hub_test {

using namespace hub;

struct FakeHal : Hal {
  uint32_t now = 1000;
  bool pwm_ok = true;
  bool oe_enabled = true;
  uint16_t pwm[NUM_CH] = {};
  int writes = 0;
  int all_off = 0;
  bool closed[8] = {};
  int adc[4] = {};
  std::map<int, ChannelCfg> nvs;

  uint32_t millis() override { return now; }
  bool pwmOk() override { return pwm_ok; }
  void pwmWrite(int ch, uint16_t us) override { pwm[ch] = us; writes++; }
  void pwmAllOff() override { for (auto& p : pwm) p = 0; all_off++; }
  void setOutputsEnabled(bool on) override { oe_enabled = on; }
  bool inputClosed(int idx) override { return closed[idx]; }
  int adcMv(int idx) override { return adc[idx]; }
  bool loadCfg(int ch, ChannelCfg& out) override {
    auto it = nvs.find(ch);
    if (it == nvs.end()) return false;
    out = it->second;
    return true;
  }
  bool saveCfg(int ch, const ChannelCfg& c) override { nvs[ch] = c; return true; }
  void eraseCfg(int ch) override { nvs.erase(ch); }
};

struct Line {
  int transport;
  std::string text;
};

struct FakeSink : LineSink {
  std::vector<Line> lines;
  void sendLine(int t, const char* s, size_t n) override { lines.push_back({t, std::string(s, n)}); }
  void clear() { lines.clear(); }
};

// Board used by the tests: same shape as the real boards.
static const InputDef TEST_INPUTS[] = {{"syr_home", false, 20}, {"estop", true, 10}, {"in1", false, 20}};
static const AdcDef TEST_ADCS[] = {{"a0"}, {"a1"}};
static const BoardDef TEST_BOARD = {"test", TEST_INPUTS, 3, TEST_ADCS, 2};

}  // namespace hub_test
