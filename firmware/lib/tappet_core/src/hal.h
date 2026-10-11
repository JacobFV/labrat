// Hardware abstraction used by the protocol/robot core. The ESP32 implementation
// lives in src/hal_esp32.cpp; native tests use a fake (test/fake_hal.h).
#pragma once

#include <stdint.h>

#include "calibration.h"

namespace tappet {

class Hal {
 public:
  virtual ~Hal() = default;
  virtual uint32_t millis() = 0;

  // PWM driver (PCA9685)
  virtual bool pwmOk() = 0;                       // driver responding on I2C
  virtual void setOutputsEnabled(bool on) = 0;    // OE pin (on = OE low)
  virtual void writePulseUs(int ch, float us) = 0;
  virtual void allOff() = 0;                      // stop pulses on every channel

  // Supervision (volts; NAN when not measurable)
  virtual float readVservo() = 0;
  virtual float readVin() = 0;

  // Android head VBUS switch
  virtual void setPhonePower(bool on) = 0;
  virtual bool phonePower() = 0;
  virtual bool phoneVbusFault() = 0;

  // Persistence
  virtual bool loadCalib(Calibration& out) = 0;
  virtual bool saveCalib(const Calibration& c) = 0;
  virtual void eraseCalib() = 0;
};

// Transport ids. Responses go back to the transport that sent the request;
// events go to TRANSPORT_ALL.
constexpr int TRANSPORT_ALL = -1;
constexpr int TRANSPORT_UART = 0;
constexpr int TRANSPORT_AOA = 1;
constexpr int TRANSPORT_COUNT = 2;

class LineSink {
 public:
  virtual ~LineSink() = default;
  // `line` has no trailing newline; the transport appends '\n'.
  virtual void sendLine(int transport, const char* line, size_t len) = 0;
};

}  // namespace tappet
