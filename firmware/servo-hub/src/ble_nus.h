// Nordic UART service over the Arduino-ESP32 BLE library (Bluedroid). RX bytes arrive on the BT task and are
// queued; TX lines are queued and sent as MTU-sized notifications from loop(), one chunk at a time, so a slow
// phone never stalls the motion loop.
#pragma once

#include <Arduino.h>

namespace hub {

class BleNus {
 public:
  void begin(const char* name);
  // Moves received bytes into `out` (returns the count). Call from loop().
  size_t read(char* out, size_t cap);
  void sendLine(const char* line, size_t len);  // appends '\n'; dropped if not connected or the queue is full
  void poll();                                  // sends queued notifications, restarts advertising
  bool connected() const { return connected_; }
  bool takeDisconnect();                        // true once after each disconnect

  // called from the BLE callbacks
  void onRx(const uint8_t* data, size_t n);
  void onConnect(uint16_t connId);
  void onDisconnect();

 private:
  static constexpr size_t RX_CAP = 1024;
  static constexpr size_t TX_CAP = 2048;
  uint8_t rx_[RX_CAP];
  volatile size_t rx_head_ = 0, rx_tail_ = 0;
  uint8_t tx_[TX_CAP];
  size_t tx_head_ = 0, tx_tail_ = 0;
  volatile bool connected_ = false;
  volatile bool dropped_ = false;
  volatile bool need_adv_ = false;
  volatile uint16_t conn_id_ = 0;
  uint16_t chunk_ = 20;
  uint32_t last_tx_ = 0;
  uint32_t adv_at_ = 0;
  bool adv_pending_ = false;
};

}  // namespace hub
