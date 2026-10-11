// Tappet body firmware entry point (ESP32-S3).
//
// The hardware-independent robot core (protocol, motion, kinematics) lives in
// lib/tappet_core; this file supplies the ESP32 hardware layer (PCA9685, ADC
// supervision, phone VBUS switch, NVS, status LED, BOOT button) and the two
// transports: UART0 (Serial, the DevKit's USB-UART port) and AOA over USB host.
#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <math.h>

#include "aoa_host.h"
#include "button.h"
#include "line_assembler.h"
#include "pca9685.h"
#include "robot.h"
#include "robot_config.h"

using namespace tappet;

namespace {

Pca9685 pca;
Preferences prefs;
constexpr const char* NVS_NS = "tappet";
constexpr const char* NVS_KEY = "calib";

float readDivider(int pin, float r_top, float r_bot) {
  uint32_t mv = 0;
  for (int i = 0; i < 4; ++i) mv += analogReadMilliVolts(pin);
  return (float)mv / 4.f / 1000.f * (r_top + r_bot) / r_bot;
}

class Esp32Hal : public Hal {
 public:
  uint32_t millis() override { return ::millis(); }

  bool pwmOk() override { return pca.ensureInit(); }
  void setOutputsEnabled(bool on) override { digitalWrite(cfg::PIN_PCA_OE, on ? LOW : HIGH); }
  void writePulseUs(int ch, float us) override { pca.writePulseUs(ch, us); }
  void allOff() override { pca.allOff(); }

  float readVservo() override {
#ifdef TAPPET_IGNORE_VSERVO
    return NAN;
#else
    return readDivider(cfg::PIN_VSERVO_SENSE, cfg::DIV_VSERVO_SENSE_R_TOP, cfg::DIV_VSERVO_SENSE_R_BOT);
#endif
  }
  float readVin() override {
    return readDivider(cfg::PIN_VIN_SENSE, cfg::DIV_VIN_SENSE_R_TOP, cfg::DIV_VIN_SENSE_R_BOT);
  }

  void setPhonePower(bool on) override {
    phone_ = on;
    digitalWrite(cfg::PIN_PHONE_VBUS_EN, on ? HIGH : LOW);
  }
  bool phonePower() override { return phone_; }
  bool phoneVbusFault() override { return phone_ && digitalRead(cfg::PIN_PHONE_VBUS_FLT) == LOW; }

  bool loadCalib(Calibration& out) override {
    CalibBlob b;
    if (prefs.getBytesLength(NVS_KEY) != sizeof(b)) return false;
    if (prefs.getBytes(NVS_KEY, &b, sizeof(b)) != sizeof(b)) return false;
    return unpackCalib(&b, sizeof(b), out);
  }
  bool saveCalib(const Calibration& c) override {
    CalibBlob b;
    packCalib(c, b);
    return prefs.putBytes(NVS_KEY, &b, sizeof(b)) == sizeof(b);
  }
  void eraseCalib() override { prefs.remove(NVS_KEY); }

 private:
  bool phone_ = true;
};

class Esp32Sink : public LineSink {
 public:
  void sendLine(int transport, const char* line, size_t len) override {
    if (transport == TRANSPORT_UART || transport == TRANSPORT_ALL) {
      Serial.write(reinterpret_cast<const uint8_t*>(line), len);
      Serial.write('\n');
    }
    if ((transport == TRANSPORT_AOA || transport == TRANSPORT_ALL) && aoa::connected()) {
      if (len + 1 > sizeof(buf_)) return;
      memcpy(buf_, line, len);
      buf_[len] = '\n';
      aoa::send(buf_, len + 1);  // whole line or nothing
    }
  }

 private:
  char buf_[OUT_LINE_MAX + 2];
};

Esp32Hal hal;
Esp32Sink sink;
Robot robot(hal, sink);
LineAssembler uartLines;
LineAssembler aoaLines;
ButtonDetector button;
bool aoaWasConnected = false;
bool aoaOk = false;
Rgb lastLed{1, 1, 1};
uint32_t lastLedMs = 0;

void feed(LineAssembler& la, int transport, const uint8_t* data, size_t n) {
  for (size_t i = 0; i < n; ++i) {
    switch (la.feed((char)data[i])) {
      case LineAssembler::Result::Line:
        robot.handleLine(transport, la.line(), la.lineLen());
        break;
      case LineAssembler::Result::Overflow:
        robot.handleOverflow(transport);
        break;
      default:
        break;
    }
  }
}

}  // namespace

void setup() {
  // Outputs safe before anything else: servos limp, head powered.
  pinMode(cfg::PIN_PCA_OE, OUTPUT);
  digitalWrite(cfg::PIN_PCA_OE, HIGH);
  pinMode(cfg::PIN_PHONE_VBUS_EN, OUTPUT);
  digitalWrite(cfg::PIN_PHONE_VBUS_EN, HIGH);
  pinMode(cfg::PIN_PHONE_VBUS_FLT, INPUT_PULLUP);
  pinMode(cfg::PIN_USER_BUTTON, INPUT_PULLUP);
  analogReadResolution(12);
  analogSetPinAttenuation(cfg::PIN_VSERVO_SENSE, ADC_11db);
  analogSetPinAttenuation(cfg::PIN_VIN_SENSE, ADC_11db);

  Serial.setTxBufferSize(4096);
  Serial.setRxBufferSize(4096);
  Serial.begin(cfg::UART_BAUD);

  Wire.begin(cfg::PIN_I2C_SDA, cfg::PIN_I2C_SCL, 400000);
  pca.begin(Wire, cfg::PCA9685_I2C_ADDR, cfg::PCA9685_OSC_HZ, cfg::SERVO_PWM_HZ);

  prefs.begin(NVS_NS, false);
  robot.begin();
  aoaOk = aoa::begin();
  if (!aoaOk) Serial.println("{\"evt\":\"error\",\"err\":\"fault\",\"msg\":\"USB host init failed; AOA disabled\"}");
}

void loop() {
  uint8_t buf[256];

  // UART transport.
  int avail;
  while ((avail = Serial.available()) > 0) {
    const size_t n = Serial.readBytes(buf, avail > (int)sizeof(buf) ? sizeof(buf) : (size_t)avail);
    feed(uartLines, TRANSPORT_UART, buf, n);
  }

  // AOA transport.
  const bool conn = aoaOk && aoa::connected();
  if (conn != aoaWasConnected) {
    aoaWasConnected = conn;
    aoaLines.reset();
    robot.setTransportConnected(TRANSPORT_AOA, conn);
  }
  if (aoaOk) {
    size_t n;
    while ((n = aoa::read(buf, sizeof(buf))) > 0) feed(aoaLines, TRANSPORT_AOA, buf, n);
  }

  // BOOT button.
  const uint32_t now = millis();
  robot.onButton(button.update(digitalRead(cfg::PIN_USER_BUTTON) == LOW, now));

  robot.loop();

  // Status LED (only rewrite on change, at most every 20 ms).
  if (now - lastLedMs >= 20) {
    lastLedMs = now;
    const Rgb c = robot.ledColor(now);
    if (c.r != lastLed.r || c.g != lastLed.g || c.b != lastLed.b) {
      rgbLedWrite(cfg::PIN_STATUS_LED, c.r, c.g, c.b);
      lastLed = c;
    }
  }

  delay(1);
}
