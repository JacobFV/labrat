// LabRat servo hub: ESP32 + PCA9685. One JSON-lines protocol (docs/protocol.md section 4) over
//   - Serial      : the DevKit's USB-UART port (laptop bridge, or an Android phone over USB-OTG), 115200
//   - USBSerial   : the ESP32-S3's native USB port (same protocol; S3 only)
//   - BLE NUS     : "labrat-hub", for the phone page (Web Bluetooth)
// The logic lives in lib/hub_core (unit-tested natively); this file only wires the hardware to it.
#include <Arduino.h>

#include "ble_nus.h"
#include "board.h"
#include "hal_esp32.h"
#include "hub.h"

using namespace hub;

#if defined(HUB_BOARD_ESP32S3) && ARDUINO_USB_MODE && !ARDUINO_USB_CDC_ON_BOOT
#define HUB_HAS_NATIVE_USB 1
#endif

static Esp32Hal hal;
static BleNus ble;

class Sink : public LineSink {
 public:
  void sendLine(int t, const char* line, size_t len) override {
    if (t == T_ALL || t == T_UART) {
      Serial.write((const uint8_t*)line, len);
      Serial.write('\n');
    }
#ifdef HUB_HAS_NATIVE_USB
    if ((t == T_ALL || t == T_USB) && USBSerial) {
      USBSerial.write((const uint8_t*)line, len);
      USBSerial.write('\n');
    }
#endif
    if (t == T_ALL || t == T_BLE) ble.sendLine(line, len);
  }
};

static Sink sink;
static Hub hubCore(hal, sink, BOARD);

static void pump(Stream& s, int t) {
  char buf[64];
  int n;
  while ((n = s.available()) > 0) {
    if (n > (int)sizeof(buf)) n = sizeof(buf);
    n = s.readBytes(buf, n);
    if (n <= 0) break;
    hubCore.feed(t, buf, (size_t)n);
  }
}

void setup() {
  Serial.setTxBufferSize(1024);
  Serial.setRxBufferSize(1024);
  Serial.begin(115200);
  Serial.setTimeout(0);
#ifdef HUB_HAS_NATIVE_USB
  USBSerial.setTxTimeoutMs(0);  // never block the loop on an unread native-USB port
  USBSerial.begin();
#endif
  hal.begin();
  ble.begin("labrat-hub");
  hubCore.begin();
}

void loop() {
  pump(Serial, T_UART);
#ifdef HUB_HAS_NATIVE_USB
  pump(USBSerial, T_USB);
#endif
  char buf[64];
  size_t n;
  while ((n = ble.read(buf, sizeof(buf))) > 0) hubCore.feed(T_BLE, buf, n);
  if (ble.takeDisconnect()) hubCore.resetTransport(T_BLE);
  hal.poll();
  hubCore.loop();
  ble.poll();
  delay(1);  // ~1 kHz loop; lets the idle task feed the task watchdog
}
