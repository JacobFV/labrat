// Hardware abstraction for the hub core. src/hal_esp32.cpp implements it on the ESP32;
// test/support/fake_hal.h fakes it for the native unit tests.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace hub {

constexpr int NUM_CH = 16;

enum class Kind : uint8_t { Pos = 0, Cont = 1 };

// Per-channel limits, persisted (NVS on the ESP32).
struct ChannelCfg {
  uint16_t min_us;   // pulse at deg 0 (pos) / full speed one way (cont)
  uint16_t max_us;   // pulse at deg 180 (pos) / full speed the other way (cont)
  uint16_t rest_us;  // where a timed pulse returns to: 0 = off (no pulses), else e.g. 1500 = neutral
  Kind kind;
};

class Hal {
 public:
  virtual ~Hal() = default;
  virtual uint32_t millis() = 0;

  // PCA9685
  virtual bool pwmOk() = 0;                     // driver answered on I2C (re-probed by the HAL)
  virtual void pwmWrite(int ch, uint16_t us) = 0;  // 0 = full off (no pulses)
  virtual void pwmAllOff() = 0;
  virtual void setOutputsEnabled(bool on) = 0;  // optional OE pin (on = OE low); no-op when not wired

  // Inputs: true when the switch contact is closed (GPIO pulled to GND), by index into BoardDef::inputs
  virtual bool inputClosed(int idx) = 0;
  // ADC in millivolts at the pin, by index into BoardDef::adcs
  virtual int adcMv(int idx) = 0;

  // Persistence: load returns false for "never saved" (the caller keeps its defaults)
  virtual bool loadCfg(int ch, ChannelCfg& out) = 0;
  virtual bool saveCfg(int ch, const ChannelCfg& c) = 0;
  virtual void eraseCfg(int ch) = 0;
};

// Transports. Replies go to the transport the request came from, events to all.
constexpr int T_ALL = -1;
constexpr int T_UART = 0;  // Serial (USB-UART bridge on the DevKit)
constexpr int T_USB = 1;   // native USB CDC (ESP32-S3 only)
constexpr int T_BLE = 2;   // Nordic UART service
constexpr int T_COUNT = 3;

class LineSink {
 public:
  virtual ~LineSink() = default;
  // `line` has no trailing newline; the transport appends '\n'.
  virtual void sendLine(int transport, const char* line, size_t len) = 0;
};

struct InputDef {
  const char* name;
  bool estop;            // normally-closed stop button: value 1 (engaged) when the contact is OPEN
  uint16_t debounce_ms;
};

struct AdcDef {
  const char* name;
};

struct BoardDef {
  const char* name;
  const InputDef* inputs;
  int n_inputs;
  const AdcDef* adcs;
  int n_adcs;
};

}  // namespace hub
