// The servo hub's protocol, motion, input and watchdog logic (docs/protocol.md section 4), hardware-free.
// main.cpp feeds it bytes from each transport and calls loop() as often as it can.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ArduinoJson.h>

#include "hal.h"
#include "line_assembler.h"

namespace hub {

#ifndef HUB_FW_VERSION
#define HUB_FW_VERSION "servo-hub/1.0.0"
#endif

constexpr uint32_t WATCHDOG_MS = 2000;   // no line for this long: relax every servo, ev:"wd"
constexpr uint32_t SETTLE_MS = 100;      // continuous channels hold rest_us this long before going off on stop
constexpr uint32_t EASE_TICK_MS = 10;    // easing update period (100 Hz; the servo itself samples at 50 Hz)
constexpr uint32_t MAX_MS = 60000;       // longest timed motion
constexpr uint16_t ABS_MIN_US = 400;     // hard pulse limits accepted by cfg
constexpr uint16_t ABS_MAX_US = 2600;

ChannelCfg defaultCfg(int ch);

class Hub {
 public:
  Hub(Hal& hal, LineSink& sink, const BoardDef& board);

  void begin();                                // load cfg, all off, ev:"boot"
  void feed(int transport, const char* data, size_t n);
  void handleLine(int transport, const char* line, size_t len);
  void loop();
  void resetTransport(int t) { if (t >= 0 && t < T_COUNT) asm_[t].reset(); }  // link dropped mid-line

  // introspection (tests, status LED)
  uint16_t outputUs(int ch) const { return out_us_[ch]; }
  const ChannelCfg& cfg(int ch) const { return cfg_[ch]; }
  bool busy(int ch) const { return mot_[ch].mode != Mode::Idle && mot_[ch].mode != Mode::Settle; }
  bool estopped() const { return estop_; }
  int inputValue(int idx) const { return in_[idx].stable; }
  bool watchdogTripped() const { return wd_tripped_; }

 private:
  enum class Mode : uint8_t { Idle, Ease, Hold, Settle };
  struct Motion {
    Mode mode = Mode::Idle;
    uint32_t t0 = 0;
    uint32_t dur = 0;
    float from_us = 0, to_us = 0;  // Ease
    float deg = 0;                 // Ease: target
    uint16_t hold_us = 0;          // Hold
    int8_t until = -1;             // Hold: input index that ends it early
    bool has_reply = false;
    int8_t transport = 0;
    int32_t id = 0;
    bool has_id = false;
  };
  struct InputState {
    uint8_t stable = 0;
    uint8_t raw = 0;
    uint32_t since = 0;
  };

  // commands
  void cmdHello(int t, int32_t id, bool hasId);
  void cmdServo(int t, int32_t id, bool hasId, JsonObjectConst req);
  void cmdPulse(int t, int32_t id, bool hasId, JsonObjectConst req);
  void cmdOff(int t, int32_t id, bool hasId, JsonObjectConst req);
  void cmdStop(int t, int32_t id, bool hasId);
  void cmdCfg(int t, int32_t id, bool hasId, JsonObjectConst req);
  void cmdRead(int t, int32_t id, bool hasId);

  // motion
  void cancel(int ch, const char* why);        // fails a pending reply with `why`, mode -> Idle
  void relaxAll(const char* why, bool settle); // cancel everything; cont -> rest_us then off (settle) or off now
  void setOut(int ch, uint16_t us);
  void finishEase(int ch, uint32_t now);
  void finishHold(int ch, uint32_t now, const char* stoppedBy);
  void tickMotions(uint32_t now, bool easeTick);
  void pollInputs(uint32_t now);
  int inputIndex(const char* name) const;
  int value(int idx) const;  // debounced logical value (1 = closed / estop engaged)

  // output helpers
  void sendErr(int t, int32_t id, bool hasId, const char* err);
  void sendOkEmpty(int t, int32_t id, bool hasId);
  void emit(int t, const JsonDocument& doc);

  Hal& hal_;
  LineSink& sink_;
  const BoardDef& board_;
  LineAssembler asm_[T_COUNT];
  ChannelCfg cfg_[NUM_CH];
  Motion mot_[NUM_CH];
  uint16_t out_us_[NUM_CH] = {};
  uint16_t last_us_[NUM_CH] = {};  // last non-zero pulse (where a relaxed positional servo probably still is)
  static constexpr int MAX_INPUTS = 8;
  InputState in_[MAX_INPUTS];
  int estop_idx_ = -1;
  bool estop_ = false;
  uint32_t last_line_ = 0;
  bool wd_armed_ = false;
  bool wd_tripped_ = false;
  uint32_t last_ease_ = 0;
  uint32_t boot_ms_ = 0;
};

}  // namespace hub
