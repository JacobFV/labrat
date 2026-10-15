#include "hal_esp32.h"

#include <Wire.h>

#include "servo_math.h"

namespace hub {

namespace {
constexpr uint32_t CFG_MAGIC = 0x48554231;  // "HUB1"
struct StoredCfg {
  uint32_t magic;
  ChannelCfg cfg;
};
void key(char* buf, int ch) { snprintf(buf, 8, "c%d", ch); }
}  // namespace

void Esp32Hal::begin() {
  if (PIN_OE >= 0) {  // outputs off until the hub decides (estop) - see the boot-window note in the README
    pinMode(PIN_OE, OUTPUT);
    digitalWrite(PIN_OE, HIGH);
  }
  for (int pin : HUB_INPUT_PINS) pinMode(pin, INPUT_PULLUP);
  for (int pin : HUB_ADC_PINS) {
    pinMode(pin, INPUT);
    analogSetPinAttenuation(pin, ADC_11db);  // ~0..3.1 V
  }
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Wire.setTimeOut(20);
  pca_.begin(Wire, PCA_ADDR, PCA_OSC_HZ_TRIM, SERVO_HZ);
  prefs_.begin("servohub", false);
  last_probe_ = ::millis();
}

void Esp32Hal::poll() {
  uint32_t now = ::millis();
  if (now - last_probe_ < 1000) return;
  last_probe_ = now;
  if (pca_.ensureInit()) {  // chip came back (logic power glitch, hot-plugged): restore what the hub drives
    for (int ch = 0; ch < NUM_CH; ch++)
      if (shadow_[ch]) pca_.writePulseUs(ch, shadow_[ch]);
  }
}

void Esp32Hal::pwmWrite(int ch, uint16_t us) {
  shadow_[ch] = us;
  pca_.writePulseUs(ch, us);
}

void Esp32Hal::pwmAllOff() {
  for (auto& s : shadow_) s = 0;
  pca_.allOff();
}

void Esp32Hal::setOutputsEnabled(bool on) {
  if (PIN_OE >= 0) digitalWrite(PIN_OE, on ? LOW : HIGH);
}

bool Esp32Hal::inputClosed(int idx) { return digitalRead(HUB_INPUT_PINS[idx]) == LOW; }

int Esp32Hal::adcMv(int idx) { return (int)analogReadMilliVolts(HUB_ADC_PINS[idx]); }

bool Esp32Hal::loadCfg(int ch, ChannelCfg& out) {
  char k[8];
  key(k, ch);
  StoredCfg s;
  if (prefs_.getBytesLength(k) != sizeof(s)) return false;
  prefs_.getBytes(k, &s, sizeof(s));
  if (s.magic != CFG_MAGIC) return false;
  out = s.cfg;
  return true;
}

bool Esp32Hal::saveCfg(int ch, const ChannelCfg& c) {
  char k[8];
  key(k, ch);
  StoredCfg s{CFG_MAGIC, c};
  return prefs_.putBytes(k, &s, sizeof(s)) == sizeof(s);
}

void Esp32Hal::eraseCfg(int ch) {
  char k[8];
  key(k, ch);
  prefs_.remove(k);
}

}  // namespace hub
