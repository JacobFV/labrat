// Minimal PCA9685 driver (16 channels, 12-bit PWM over I2C) for 50 Hz servo pulses. Adapted from Tappet.
#pragma once

#include <Arduino.h>
#include <Wire.h>

namespace hub {

class Pca9685 {
 public:
  bool begin(TwoWire& wire, uint8_t addr, float osc_hz, float pwm_hz);
  bool probe();       // device ACKs its address
  bool ensureInit();  // probe, re-init after the chip lost power; true if it was (re)initialised just now
  bool ok() const { return ok_; }
  void writePulseUs(int ch, uint16_t us);  // 0 = full off
  void allOff();
  float actualHz() const { return actual_hz_; }

 private:
  bool init();
  bool write8(uint8_t reg, uint8_t v);
  int read8(uint8_t reg);  // -1 on error
  bool writeChannel(int ch, uint16_t on, uint16_t off);

  TwoWire* wire_ = nullptr;
  uint8_t addr_ = 0x40;
  float osc_hz_ = 25e6f;
  float pwm_hz_ = 50.f;
  float actual_hz_ = 50.f;
  bool ok_ = false;
};

}  // namespace hub
