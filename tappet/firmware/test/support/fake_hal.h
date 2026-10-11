// Test doubles shared by the native test suites (header-only).
#pragma once

#include <ArduinoJson.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "calibration.h"
#include "hal.h"

#ifndef TAPPET_CONFIG_DIR
#define TAPPET_CONFIG_DIR "../config"
#endif

namespace tappet_test {

using namespace tappet;

class FakeHal : public Hal {
 public:
  uint32_t now = 1000;
  bool pwm_ok = true;
  bool oe_enabled = false;
  float pulses[NUM_PWM_CHANNELS] = {};
  int pulse_writes = 0;
  int all_off_calls = 0;
  float vservo = 5.1f;
  float vin = 9.0f;
  bool phone_on = true;
  bool vbus_fault = false;
  bool has_saved = false;
  CalibBlob saved_blob{};
  int save_calls = 0;
  int erase_calls = 0;

  uint32_t millis() override { return now; }
  bool pwmOk() override { return pwm_ok; }
  void setOutputsEnabled(bool on) override { oe_enabled = on; }
  void writePulseUs(int ch, float us) override {
    if (ch >= 0 && ch < NUM_PWM_CHANNELS) pulses[ch] = us;
    ++pulse_writes;
  }
  void allOff() override {
    ++all_off_calls;
    for (float& p : pulses) p = 0.f;
  }
  float readVservo() override { return vservo; }
  float readVin() override { return vin; }
  void setPhonePower(bool on) override { phone_on = on; }
  bool phonePower() override { return phone_on; }
  bool phoneVbusFault() override { return vbus_fault; }
  bool loadCalib(Calibration& out) override {
    if (!has_saved) return false;
    return unpackCalib(&saved_blob, sizeof(saved_blob), out);
  }
  bool saveCalib(const Calibration& c) override {
    packCalib(c, saved_blob);
    has_saved = true;
    ++save_calls;
    return true;
  }
  void eraseCalib() override {
    has_saved = false;
    ++erase_calls;
  }
};

class FakeSink : public LineSink {
 public:
  struct Msg {
    int transport;
    std::string line;
  };
  std::vector<Msg> msgs;

  void sendLine(int transport, const char* line, size_t len) override {
    msgs.push_back(Msg{transport, std::string(line, len)});
  }
  void clear() { msgs.clear(); }

  // Find the last message whose JSON has key == value (string) or id == n.
  bool findEvt(const char* evt, JsonDocument& out, int fromIdx = 0) const {
    for (int i = (int)msgs.size() - 1; i >= fromIdx; --i) {
      JsonDocument d;
      if (deserializeJson(d, msgs[i].line)) continue;
      if (d["evt"].is<const char*>() && strcmp(d["evt"].as<const char*>(), evt) == 0) {
        out = d;
        return true;
      }
    }
    return false;
  }
  int countEvt(const char* evt) const {
    int n = 0;
    for (const Msg& m : msgs) {
      JsonDocument d;
      if (deserializeJson(d, m.line)) continue;
      if (d["evt"].is<const char*>() && strcmp(d["evt"].as<const char*>(), evt) == 0) ++n;
    }
    return n;
  }
  bool findEvtId(const char* evt, uint32_t id, JsonDocument& out) const {
    for (const Msg& m : msgs) {
      JsonDocument d;
      if (deserializeJson(d, m.line)) continue;
      if (d["evt"].is<const char*>() && strcmp(d["evt"].as<const char*>(), evt) == 0 &&
          d["id"].is<uint32_t>() && d["id"].as<uint32_t>() == id) {
        out = d;
        return true;
      }
    }
    return false;
  }
  bool findResponse(uint32_t id, JsonDocument& out) const {
    for (int i = (int)msgs.size() - 1; i >= 0; --i) {
      JsonDocument d;
      if (deserializeJson(d, msgs[i].line)) continue;
      if (d["ok"].is<bool>() && d["id"].is<uint32_t>() && d["id"].as<uint32_t>() == id) {
        out = d;
        return true;
      }
    }
    return false;
  }
};

inline std::string readConfigFile(const char* name) {
  const std::string candidates[] = {std::string(TAPPET_CONFIG_DIR) + "/" + name, std::string("../config/") + name,
                                    std::string("config/") + name};
  for (const std::string& p : candidates) {
    FILE* f = fopen(p.c_str(), "rb");
    if (!f) continue;
    std::string s;
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) s.append(buf, n);
    fclose(f);
    return s;
  }
  return std::string();
}

}  // namespace tappet_test
