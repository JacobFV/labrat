#include "pca9685.h"

namespace tappet {

namespace {
constexpr uint8_t REG_MODE1 = 0x00;
constexpr uint8_t REG_MODE2 = 0x01;
constexpr uint8_t REG_LED0_ON_L = 0x06;
constexpr uint8_t REG_ALL_LED_OFF_H = 0xFD;
constexpr uint8_t REG_PRESCALE = 0xFE;
constexpr uint8_t MODE1_RESTART = 0x80;
constexpr uint8_t MODE1_AI = 0x20;
constexpr uint8_t MODE1_SLEEP = 0x10;
constexpr uint8_t MODE2_OUTDRV = 0x04;
}  // namespace

bool Pca9685::begin(TwoWire& wire, uint8_t addr, float osc_hz, float pwm_hz) {
  wire_ = &wire;
  addr_ = addr;
  osc_hz_ = osc_hz;
  pwm_hz_ = pwm_hz;
  ok_ = probe() && init();
  return ok_;
}

bool Pca9685::probe() {
  if (!wire_) return false;
  wire_->beginTransmission(addr_);
  return wire_->endTransmission() == 0;
}

bool Pca9685::ensureInit() {
  const bool present = probe();
  if (!present) {
    ok_ = false;
    return false;
  }
  if (!ok_) ok_ = init();
  return ok_;
}

bool Pca9685::write8(uint8_t reg, uint8_t v) {
  wire_->beginTransmission(addr_);
  wire_->write(reg);
  wire_->write(v);
  return wire_->endTransmission() == 0;
}

bool Pca9685::init() {
  float pre = osc_hz_ / (4096.f * pwm_hz_) - 1.f;
  int prescale = (int)lroundf(pre);
  if (prescale < 3) prescale = 3;
  if (prescale > 255) prescale = 255;
  actual_hz_ = osc_hz_ / (4096.f * (float)(prescale + 1));
  bool ok = write8(REG_MODE1, MODE1_SLEEP | MODE1_AI);  // prescale is only writable while asleep
  ok = ok && write8(REG_PRESCALE, (uint8_t)prescale);
  ok = ok && write8(REG_MODE2, MODE2_OUTDRV);
  ok = ok && write8(REG_ALL_LED_OFF_H, 0x10);  // start with every output fully off
  ok = ok && write8(REG_MODE1, MODE1_AI);
  delayMicroseconds(600);  // oscillator start-up
  ok = ok && write8(REG_MODE1, MODE1_AI | MODE1_RESTART);
  return ok;
}

bool Pca9685::writeChannel(int ch, uint16_t on, uint16_t off) {
  wire_->beginTransmission(addr_);
  wire_->write((uint8_t)(REG_LED0_ON_L + 4 * ch));
  wire_->write((uint8_t)(on & 0xFF));
  wire_->write((uint8_t)(on >> 8));
  wire_->write((uint8_t)(off & 0xFF));
  wire_->write((uint8_t)(off >> 8));
  return wire_->endTransmission() == 0;
}

void Pca9685::writePulseUs(int ch, float us) {
  if (!ok_ || ch < 0 || ch > 15) return;
  float ticks = us * actual_hz_ * 4096.f / 1e6f;
  if (ticks < 0.f) ticks = 0.f;
  if (ticks > 4095.f) ticks = 4095.f;
  if (!writeChannel(ch, 0, (uint16_t)lroundf(ticks))) ok_ = false;
}

void Pca9685::allOff() {
  if (!wire_) return;
  write8(REG_ALL_LED_OFF_H, 0x10);
}

}  // namespace tappet
