// Minimal PCA9685 driver (16-channel, 12-bit PWM over I2C) for servo pulses.
#pragma once

#include <Arduino.h>
#include <Wire.h>

namespace tappet {

class Pca9685 {
 public:
  bool begin(TwoWire& wire, uint8_t addr, float osc_hz, float pwm_hz);
  bool probe();                 // device ACKs its address
  bool ensureInit();            // probe + re-init if it came back after a reset/power loss
  void writePulseUs(int ch, float us);
  void allOff();                // full-off on every channel
  float actualHz() const { return actual_hz_; }

 private:
  bool init();
  bool write8(uint8_t reg, uint8_t v);
  bool writeChannel(int ch, uint16_t on, uint16_t off);

  TwoWire* wire_ = nullptr;
  uint8_t addr_ = 0x40;
  float osc_hz_ = 25e6f;
  float pwm_hz_ = 50.f;
  float actual_hz_ = 50.f;
  bool ok_ = false;
};

}  // namespace tappet
