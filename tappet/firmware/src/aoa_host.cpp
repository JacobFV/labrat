#include "aoa_host.h"

#include <freertos/FreeRTOS.h>
#include <freertos/stream_buffer.h>
#include <freertos/task.h>
#include <string.h>
#include <usb/usb_helpers.h>
#include <usb/usb_host.h>

#include "robot_config.h"

namespace tappet {
namespace aoa {

namespace {

constexpr uint16_t AOA_VID = 0x18D1;
constexpr uint16_t AOA_PID_ACCESSORY = 0x2D00;
constexpr uint16_t AOA_PID_ACCESSORY_ADB = 0x2D01;
constexpr uint8_t AOA_GET_PROTOCOL = 51;
constexpr uint8_t AOA_SEND_STRING = 52;
constexpr uint8_t AOA_START = 53;
constexpr size_t BULK_BUF = 512;
constexpr size_t CTRL_BUF = 256;
constexpr size_t RX_STREAM = 4096;
constexpr size_t TX_STREAM = 8192;
constexpr uint32_t CTRL_TIMEOUT_MS = 1000;

const char* TAG = "aoa";

usb_host_client_handle_t client = nullptr;
StreamBufferHandle_t rxStream = nullptr;
StreamBufferHandle_t txStream = nullptr;
SemaphoreHandle_t txLock = nullptr;

// Pending events recorded by the client callback, processed in the task loop.
volatile int pendingNewAddr = -1;
volatile bool pendingGone = false;

usb_device_handle_t dev = nullptr;
bool claimed = false;
volatile bool isConnected = false;
volatile bool closing = false;
uint8_t epIn = 0, epOut = 0;
uint16_t mpsIn = 64;

usb_transfer_t* ctrlXfer = nullptr;
usb_transfer_t* inXfer = nullptr;
usb_transfer_t* outXfer = nullptr;
volatile bool ctrlDone = false;
volatile bool inBusy = false;
volatile bool outBusy = false;
volatile uint32_t startCount = 0;

void clientEventCb(const usb_host_client_event_msg_t* msg, void*) {
  if (msg->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
    pendingNewAddr = msg->new_dev.address;
  } else if (msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
    if (msg->dev_gone.dev_hdl == dev) pendingGone = true;
  }
}

void ctrlCb(usb_transfer_t*) { ctrlDone = true; }

void inCb(usb_transfer_t* t) {
  if (t->status == USB_TRANSFER_STATUS_COMPLETED && t->actual_num_bytes > 0 && rxStream)
    xStreamBufferSend(rxStream, t->data_buffer, t->actual_num_bytes, 0);
  if (t->status == USB_TRANSFER_STATUS_COMPLETED && isConnected && !closing) {
    t->num_bytes = (int)BULK_BUF;
    if (usb_host_transfer_submit(t) == ESP_OK) return;
  }
  inBusy = false;
}

void outCb(usb_transfer_t*) { outBusy = false; }

// Synchronous control transfer: submit, then pump client events until it completes.
bool control(uint8_t bmRequestType, uint8_t bRequest, uint16_t wValue, uint16_t wIndex, const void* out,
             uint16_t len, uint8_t* in = nullptr) {
  if (!dev || len > CTRL_BUF - sizeof(usb_setup_packet_t)) return false;
  usb_setup_packet_t* s = reinterpret_cast<usb_setup_packet_t*>(ctrlXfer->data_buffer);
  s->bmRequestType = bmRequestType;
  s->bRequest = bRequest;
  s->wValue = wValue;
  s->wIndex = wIndex;
  s->wLength = len;
  if (out && len) memcpy(ctrlXfer->data_buffer + sizeof(usb_setup_packet_t), out, len);
  ctrlXfer->num_bytes = (int)(sizeof(usb_setup_packet_t) + len);
  ctrlXfer->device_handle = dev;
  ctrlXfer->bEndpointAddress = 0;
  ctrlXfer->callback = ctrlCb;
  ctrlXfer->context = nullptr;
  ctrlDone = false;
  if (usb_host_transfer_submit_control(client, ctrlXfer) != ESP_OK) return false;
  const uint32_t t0 = millis();
  while (!ctrlDone && millis() - t0 < CTRL_TIMEOUT_MS) usb_host_client_handle_events(client, pdMS_TO_TICKS(10));
  if (!ctrlDone) return false;  // (transfer stays owned by the stack until the device goes away)
  if (ctrlXfer->status != USB_TRANSFER_STATUS_COMPLETED) return false;
  if (in && len) memcpy(in, ctrlXfer->data_buffer + sizeof(usb_setup_packet_t), len);
  return true;
}

void closeDevice() {
  if (!dev) return;
  if (claimed) {
    usb_host_interface_release(client, dev, 0);
    claimed = false;
  }
  usb_host_device_close(client, dev);
  dev = nullptr;
  epIn = epOut = 0;
}

bool openAccessory() {
  const usb_config_desc_t* cfgDesc = nullptr;
  if (usb_host_get_active_config_descriptor(dev, &cfgDesc) != ESP_OK || !cfgDesc) return false;
  int offset = 0;
  const usb_intf_desc_t* intf = usb_parse_interface_descriptor(cfgDesc, 0, 0, &offset);
  if (!intf) return false;
  for (int i = 0; i < intf->bNumEndpoints; ++i) {
    int eoff = offset;
    const usb_ep_desc_t* ep = usb_parse_endpoint_descriptor_by_index(intf, i, cfgDesc->wTotalLength, &eoff);
    if (!ep || USB_EP_DESC_GET_XFERTYPE(ep) != USB_TRANSFER_TYPE_BULK) continue;
    if (USB_EP_DESC_GET_EP_DIR(ep)) {
      epIn = ep->bEndpointAddress;
      mpsIn = USB_EP_DESC_GET_MPS(ep);
    } else {
      epOut = ep->bEndpointAddress;
    }
  }
  if (!epIn || !epOut) return false;
  if (usb_host_interface_claim(client, dev, intf->bInterfaceNumber, intf->bAlternateSetting) != ESP_OK) return false;
  claimed = true;
  closing = false;
  isConnected = true;
  ESP_LOGI(TAG, "accessory connected (IN 0x%02x mps %u, OUT 0x%02x)", epIn, mpsIn, epOut);
  return true;
}

void handleNewDevice(uint8_t addr) {
  if (dev) return;  // already serving a device (single-port, no hubs expected)
  if (usb_host_device_open(client, addr, &dev) != ESP_OK) {
    dev = nullptr;
    return;
  }
  const usb_device_desc_t* dd = nullptr;
  if (usb_host_get_device_descriptor(dev, &dd) != ESP_OK || !dd) {
    closeDevice();
    return;
  }
  ESP_LOGI(TAG, "device %04x:%04x", dd->idVendor, dd->idProduct);
  if (dd->idVendor == AOA_VID && (dd->idProduct == AOA_PID_ACCESSORY || dd->idProduct == AOA_PID_ACCESSORY_ADB)) {
    if (!openAccessory()) {
      ESP_LOGW(TAG, "accessory open failed");
      closeDevice();
    }
    return;
  }
  // Not yet in accessory mode: AOA handshake.
  uint8_t proto[2] = {0, 0};
  if (!control(0xC0, AOA_GET_PROTOCOL, 0, 0, nullptr, 2, proto) || (proto[0] | (proto[1] << 8)) < 1) {
    ESP_LOGI(TAG, "device does not speak AOA");
    closeDevice();
    return;
  }
  for (uint16_t i = 0; i < 6; ++i) {
    const char* s = cfg::AOA_STRINGS[i];
    if (!control(0x40, AOA_SEND_STRING, 0, i, s, (uint16_t)(strlen(s) + 1))) {
      ESP_LOGW(TAG, "SEND_STRING %u failed", i);
      closeDevice();
      return;
    }
  }
  control(0x40, AOA_START, 0, 0, nullptr, 0);
  startCount = startCount + 1;
  ESP_LOGI(TAG, "AOA START sent; waiting for re-enumeration");
  closeDevice();  // the phone disconnects and comes back as 18d1:2d0x
}

void startClosing() {
  closing = true;
  isConnected = false;
  if (dev && claimed) {
    for (uint8_t ep : {epIn, epOut}) {
      usb_host_endpoint_halt(dev, ep);
      usb_host_endpoint_flush(dev, ep);
    }
  }
}

void serviceTransfers() {
  if (closing) {
    if (!inBusy && !outBusy) {
      closeDevice();
      closing = false;
    }
    return;
  }
  if (!isConnected || !dev) return;
  if (!inBusy) {
    inXfer->device_handle = dev;
    inXfer->bEndpointAddress = epIn;
    inXfer->callback = inCb;
    inXfer->num_bytes = (int)BULK_BUF;  // multiple of MPS (64 FS / 512 HS)
    inBusy = true;
    if (usb_host_transfer_submit(inXfer) != ESP_OK) inBusy = false;
  }
  if (!outBusy && xStreamBufferBytesAvailable(txStream) > 0) {
    const size_t n = xStreamBufferReceive(txStream, outXfer->data_buffer, BULK_BUF, 0);
    if (n > 0) {
      outXfer->device_handle = dev;
      outXfer->bEndpointAddress = epOut;
      outXfer->callback = outCb;
      outXfer->num_bytes = (int)n;
      outBusy = true;
      if (usb_host_transfer_submit(outXfer) != ESP_OK) outBusy = false;
    }
  }
}

void libTask(void*) {
  for (;;) {
    uint32_t flags = 0;
    usb_host_lib_handle_events(portMAX_DELAY, &flags);
    if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
  }
}

void clientTask(void*) {
  usb_host_client_config_t cc = {};
  cc.is_synchronous = false;
  cc.max_num_event_msg = 5;
  cc.async.client_event_callback = clientEventCb;
  cc.async.callback_arg = nullptr;
  if (usb_host_client_register(&cc, &client) != ESP_OK) {
    ESP_LOGE(TAG, "client register failed");
    vTaskDelete(nullptr);
    return;
  }
  usb_host_transfer_alloc(CTRL_BUF, 0, &ctrlXfer);
  usb_host_transfer_alloc(BULK_BUF, 0, &inXfer);
  usb_host_transfer_alloc(BULK_BUF, 0, &outXfer);
  for (;;) {
    usb_host_client_handle_events(client, pdMS_TO_TICKS(2));
    if (pendingGone) {
      pendingGone = false;
      ESP_LOGI(TAG, "device gone");
      if (claimed)
        startClosing();
      else
        closeDevice();
    }
    const int addr = pendingNewAddr;
    if (addr >= 0) {
      pendingNewAddr = -1;
      handleNewDevice((uint8_t)addr);
    }
    serviceTransfers();
  }
}

}  // namespace

bool begin() {
  rxStream = xStreamBufferCreate(RX_STREAM, 1);
  txStream = xStreamBufferCreate(TX_STREAM, 1);
  txLock = xSemaphoreCreateMutex();
  usb_host_config_t hc = {};
  hc.skip_phy_setup = false;
  hc.intr_flags = ESP_INTR_FLAG_LEVEL1;
  if (usb_host_install(&hc) != ESP_OK) {
    ESP_LOGE(TAG, "usb_host_install failed");
    return false;
  }
  xTaskCreatePinnedToCore(libTask, "usb_lib", 4096, nullptr, 5, nullptr, 0);
  xTaskCreatePinnedToCore(clientTask, "usb_aoa", 6144, nullptr, 5, nullptr, 0);
  return true;
}

bool connected() { return isConnected; }

size_t read(uint8_t* buf, size_t max) {
  if (!rxStream) return 0;
  return xStreamBufferReceive(rxStream, buf, max, 0);
}

bool send(const char* data, size_t len) {
  if (!txStream || !isConnected) return false;
  xSemaphoreTake(txLock, portMAX_DELAY);
  bool ok = xStreamBufferSpacesAvailable(txStream) >= len;
  if (ok) xStreamBufferSend(txStream, data, len, 0);
  xSemaphoreGive(txLock);
  return ok;
}

uint32_t handshakes() { return startCount; }

}  // namespace aoa
}  // namespace tappet
