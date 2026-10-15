// ESP32 implementation of hub::Hal: PCA9685 on I2C, GPIO switches with pull-ups, ADC1, NVS (Preferences).
#pragma once

#include <Arduino.h>
#include <Preferences.h>

#include "board.h"
#include "hal.h"
#include "pca9685.h"

namespace hub {

class Esp32Hal : public Hal {
 public:
  void begin();
  void poll();  // re-probe the PCA9685 once a second; restore outputs if it was power-cycled

  uint32_t millis() override { return ::millis(); }
  bool pwmOk() override { return pca_.ok(); }
  void pwmWrite(int ch, uint16_t us) override;
  void pwmAllOff() override;
  void setOutputsEnabled(bool on) override;
  bool inputClosed(int idx) override;
  int adcMv(int idx) override;
  bool loadCfg(int ch, ChannelCfg& out) override;
  bool saveCfg(int ch, const ChannelCfg& c) override;
  void eraseCfg(int ch) override;

 private:
  Pca9685 pca_;
  Preferences prefs_;
  uint16_t shadow_[NUM_CH] = {};
  uint32_t last_probe_ = 0;
};

}  // namespace hub
