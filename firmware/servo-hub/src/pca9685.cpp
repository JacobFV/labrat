#include "pca9685.h"

#include "servo_math.h"

namespace hub {

namespace {
constexpr uint8_t REG_MODE1 = 0x00;
constexpr uint8_t REG_MODE2 = 0x01;
constexpr uint8_t REG_LED0_ON_L = 0x06;
constexpr uint8_t REG_ALL_LED_OFF_H = 0xFD;
constexpr uint8_t REG_PRESCALE = 0xFE;
constexpr uint8_t MODE1_RESTART = 0x80;
constexpr uint8_t MODE1_AI = 0x20;
constexpr uint8_t MODE1_SLEEP = 0x10;
constexpr uint8_t MODE2_OUTDRV = 0x04;  // totem-pole outputs; with /OE high they drive LOW (OUTNE = 00)
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
  if (!probe()) {
    ok_ = false;
    return false;
  }
  // A chip that lost power comes back asleep (MODE1.SLEEP = 1) with every output off: re-init it.
  if (ok_) {
    int m = read8(REG_MODE1);
    if (m >= 0 && !(m & MODE1_SLEEP)) return false;
  }
  ok_ = init();
  return ok_;
}

int Pca9685::read8(uint8_t reg) {
  wire_->beginTransmission(addr_);
  wire_->write(reg);
  if (wire_->endTransmission(false) != 0) return -1;
  if (wire_->requestFrom((int)addr_, 1) != 1) return -1;
  return wire_->read();
}

bool Pca9685::write8(uint8_t reg, uint8_t v) {
  wire_->beginTransmission(addr_);
  wire_->write(reg);
  wire_->write(v);
  return wire_->endTransmission() == 0;
}

bool Pca9685::init() {
  int prescale = pcaPrescale(osc_hz_, pwm_hz_);
  actual_hz_ = pcaActualHz(osc_hz_, prescale);
  bool ok = write8(REG_MODE1, MODE1_SLEEP | MODE1_AI);  // PRE_SCALE is only writable while asleep
  ok = ok && write8(REG_PRESCALE, (uint8_t)prescale);
  ok = ok && write8(REG_MODE2, MODE2_OUTDRV);
  ok = ok && write8(REG_ALL_LED_OFF_H, 0x10);  // every output fully off
  ok = ok && write8(REG_MODE1, MODE1_AI);
  delayMicroseconds(600);  // oscillator start-up (>= 500 us)
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

void Pca9685::writePulseUs(int ch, uint16_t us) {
  if (!ok_ || ch < 0 || ch > 15) return;
  bool w = us == 0 ? writeChannel(ch, 0, 0x1000)  // OFF bit 12 = full off
                   : writeChannel(ch, 0, pcaTicks((float)us, actual_hz_));
  if (!w) ok_ = false;
}

void Pca9685::allOff() {
  if (!wire_) return;
  write8(REG_ALL_LED_OFF_H, 0x10);
}

}  // namespace hub
