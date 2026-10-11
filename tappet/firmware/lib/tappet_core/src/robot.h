// Robot core: protocol dispatcher (SPEC section 4), motion engine owner,
// watchdog, status streaming, fault supervision (brownout, PWM driver, head VBUS),
// BOOT-button actions and status LED state. Hardware independent: all I/O goes
// through Hal and LineSink so the whole thing runs in native unit tests.
#pragma once

#include <ArduinoJson.h>
#include <stddef.h>
#include <stdint.h>

#include "button.h"
#include "calibration.h"
#include "hal.h"
#include "motion.h"

namespace tappet {

constexpr const char* FW_NAME = "tappet-fw";
constexpr const char* FW_VERSION = cfg::ROBOT_VERSION;
constexpr int PROTO_VERSION = 1;

constexpr float BROWNOUT_TRIP_V = 4.3f;
constexpr float BROWNOUT_CLEAR_V = 4.5f;
constexpr uint32_t SUPERVISE_PERIOD_MS = 50;
constexpr int BROWNOUT_SAMPLES = 3;          // consecutive low samples to trip
constexpr uint32_t LINK_UART_TIMEOUT_MS = 10000;
constexpr uint32_t WATCHDOG_PARK_TIMEOUT_MS = 6000;
constexpr int STREAM_MAX_HZ = 20;
constexpr size_t OUT_LINE_MAX = 2048 + 64;
constexpr float SERVO_RAW_US_MIN = 400.f;
constexpr float SERVO_RAW_US_MAX = 2600.f;

enum FaultBits : uint8_t {
  FAULT_BROWNOUT = 1u << 0,
  FAULT_PWM = 1u << 1,
  FAULT_PHONE_VBUS = 1u << 2,
};

struct Rgb {
  uint8_t r, g, b;
};

class Robot : public MotionListener {
 public:
  Robot(Hal& hal, LineSink& sink);

  // Load calibration from NVS (or defaults), probe hardware, emit boot event.
  void begin();

  // Process one request line received on `transport`.
  void handleLine(int transport, const char* line, size_t len);
  // Report an over-long line received on `transport`.
  void handleOverflow(int transport);

  // Call frequently (>= 100 Hz). Runs the 100 Hz motion tick, watchdog, stream, supervision.
  void loop();

  void onButton(ButtonAction a);
  void setTransportConnected(int transport, bool connected);

  // Status LED colour for the current state at time `now`.
  Rgb ledColor(uint32_t now) const;

  // --- accessors (tests / diagnostics) ---
  bool enabled() const { return enabled_; }
  uint8_t faults() const { return faults_; }
  const Calibration& calibration() const { return cal_; }
  MotionEngine& motion() { return engine_; }
  uint32_t watchdogMs() const { return wdMs_; }
  int streamHz() const { return streamHz_; }
  bool linkUp() const;

  // MotionListener
  void onMotionDone(uint32_t id) override;
  void onMotionError(uint32_t id, const char* err, const char* msg) override;
  void onMotionCancelled(uint32_t id, const char* why) override;

 private:
  struct Ctx;  // per-request context (defined in robot.cpp)

  void dispatch(Ctx& c);
  bool doEnable(char* msg, size_t n);
  void doRelax();
  void writeOutputs(bool force);
  void supervise(uint32_t now);
  void setFault(uint8_t bit, bool on, const char* code);
  void emitStatusEvent();
  void fillStatus(JsonObject o) const;
  void sendDoc(int transport, const JsonDocument& doc);

  Hal& hal_;
  LineSink& sink_;
  Calibration cal_;
  MotionEngine engine_;

  bool enabled_ = false;
  uint8_t faults_ = 0;
  bool rawOverride_[NUM_PWM_CHANNELS] = {};
  float lastUs_[NUM_PWM_CHANNELS] = {};

  uint32_t lastTick_ = 0;
  uint32_t lastSupervise_ = 0;
  uint32_t lastProbe_ = 0;
  int lowCount_ = 0;
  float vservo_;
  float vin_;

  uint32_t wdMs_;
  uint32_t lastCmdMs_ = 0;
  bool wdTripping_ = false;
  uint32_t wdTripMs_ = 0;
  uint32_t wdEventMs_ = 0;
  bool wdEventShown_ = false;

  int streamHz_ = 0;
  uint32_t lastStream_ = 0;

  bool transportConnected_[TRANSPORT_COUNT] = {};
  uint32_t lastRx_[TRANSPORT_COUNT] = {};
  bool rxSeen_[TRANSPORT_COUNT] = {};
};

}  // namespace tappet
