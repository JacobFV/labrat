// USB host on the ESP32-S3 OTG port (GPIO19/20) talking to the Android head via
// the Android Open Accessory protocol (SPEC section 5). Received bytes land in a
// stream buffer read by the main loop; outgoing lines are queued and sent from
// the USB client task over the accessory's bulk OUT endpoint.
#pragma once

#include <Arduino.h>

namespace tappet {

namespace aoa {

bool begin();                 // install USB host + start tasks
bool connected();             // accessory interface claimed, bulk pipes up
size_t read(uint8_t* buf, size_t max);  // non-blocking
bool send(const char* data, size_t len);  // queue (drops whole message if no room)
uint32_t handshakes();        // number of AOA START requests sent (diagnostics)

}  // namespace aoa

}  // namespace tappet
