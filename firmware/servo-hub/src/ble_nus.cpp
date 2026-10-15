#include "ble_nus.h"

#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

namespace hub {

namespace {
const char* NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const char* NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";  // host writes
const char* NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";  // we notify

BleNus* self = nullptr;
BLEServer* server = nullptr;
BLECharacteristic* txChr = nullptr;
portMUX_TYPE rxMux = portMUX_INITIALIZER_UNLOCKED;

class ServerCb : public BLEServerCallbacks {
  void onConnect(BLEServer* s, esp_ble_gatts_cb_param_t* param) override { self->onConnect(param->connect.conn_id); }
  void onDisconnect(BLEServer* s) override { self->onDisconnect(); }
};

class RxCb : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    auto v = c->getValue();  // std::string on core 2.x, String on 3.x: both have c_str()/length()
    self->onRx((const uint8_t*)v.c_str(), v.length());
  }
};
}  // namespace

void BleNus::begin(const char* name) {
  self = this;
  BLEDevice::init(name);
  BLEDevice::setMTU(247);  // the central picks; Android Chrome usually negotiates the max
  server = BLEDevice::createServer();
  server->setCallbacks(new ServerCb());
  BLEService* svc = server->createService(NUS_SERVICE);
  txChr = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  txChr->addDescriptor(new BLE2902());
  BLECharacteristic* rx =
      svc->createCharacteristic(NUS_RX, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rx->setCallbacks(new RxCb());
  svc->start();
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE);
  adv->setScanResponse(true);  // the name goes in the scan response (the 128-bit UUID fills the advert)
  BLEDevice::startAdvertising();
}

void BleNus::onRx(const uint8_t* data, size_t n) {
  portENTER_CRITICAL(&rxMux);
  for (size_t i = 0; i < n; i++) {
    size_t next = (rx_head_ + 1) % RX_CAP;
    if (next == rx_tail_) break;  // full: drop (the line assembler resyncs on the next '\n')
    rx_[rx_head_] = data[i];
    rx_head_ = next;
  }
  portEXIT_CRITICAL(&rxMux);
}

size_t BleNus::read(char* out, size_t cap) {
  size_t n = 0;
  portENTER_CRITICAL(&rxMux);
  while (n < cap && rx_tail_ != rx_head_) {
    out[n++] = (char)rx_[rx_tail_];
    rx_tail_ = (rx_tail_ + 1) % RX_CAP;
  }
  portEXIT_CRITICAL(&rxMux);
  return n;
}

void BleNus::onConnect(uint16_t connId) {
  conn_id_ = connId;
  connected_ = true;
}

void BleNus::onDisconnect() {
  connected_ = false;
  dropped_ = true;
  need_adv_ = true;
}

bool BleNus::takeDisconnect() {
  if (!dropped_) return false;
  dropped_ = false;
  return true;
}

void BleNus::sendLine(const char* line, size_t len) {
  if (!connected_) return;
  size_t used = (tx_head_ + TX_CAP - tx_tail_) % TX_CAP;
  if (used + len + 1 >= TX_CAP) return;  // phone not keeping up: drop the whole line, never half of it
  for (size_t i = 0; i <= len; i++) {
    tx_[tx_head_] = i < len ? (uint8_t)line[i] : (uint8_t)'\n';
    tx_head_ = (tx_head_ + 1) % TX_CAP;
  }
}

void BleNus::poll() {
  uint32_t now = millis();
  if (!connected_) {
    tx_head_ = tx_tail_ = 0;
    if (need_adv_) {  // restart advertising a moment after a disconnect
      need_adv_ = false;
      adv_pending_ = true;
      adv_at_ = now + 300;
    }
    if (adv_pending_ && (int32_t)(now - adv_at_) >= 0) {
      adv_pending_ = false;
      BLEDevice::startAdvertising();
    }
    return;
  }
  adv_pending_ = false;
  if (tx_head_ == tx_tail_ || now - last_tx_ < 4) return;
  uint16_t mtu = server ? server->getPeerMTU(conn_id_) : 23;
  chunk_ = mtu > 23 ? (uint16_t)(mtu - 3) : 20;
  if (chunk_ > 180) chunk_ = 180;
  uint8_t buf[180];
  size_t n = 0;
  while (n < chunk_ && tx_tail_ != tx_head_) {
    buf[n++] = tx_[tx_tail_];
    tx_tail_ = (tx_tail_ + 1) % TX_CAP;
  }
  txChr->setValue(buf, n);
  txChr->notify();
  last_tx_ = now;
}

}  // namespace hub
