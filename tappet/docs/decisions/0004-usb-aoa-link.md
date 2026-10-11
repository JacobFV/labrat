# ADR-0004: USB Android Open Accessory (AOA) link that also charges the head

* Status: Accepted
* Date: 2026-10-11

## Context

The head phone runs the agent, camera and screen continuously, which drains a battery in a few
hours. It also needs a low-latency, reliable link to the body. Most Android phones have only one
USB-C port, so data and charging must share it.

## Decision

The ESP32-S3 acts as **USB host** on its native OTG port, supplies **VBUS 5 V (1.5 A advertised
via 22 kΩ Rp on CC)** through a current-limited switch, and switches the phone into **AOA accessory
mode** (GET_PROTOCOL → SEND_STRING ×6 → START). Newline-delimited JSON flows over the bulk
endpoints. The app declares an accessory filter (`Tappet` / `TappetBody`) so it launches when
docked.

## Alternatives considered

* **Phone as USB host (OTG) + USB-serial:** works on more phones, but the phone has to *supply*
  power, so its battery drains. Kept as a fallback transport.
* **Wi-Fi / BLE only:** no wire, but no charging, more latency and jitter, and pairing friction.
  Kept as a development transport (simulator over TCP).
* **Separate charging cable + data over Wi-Fi:** needs a second port or a splitter, which phones
  generally don't support.

## Consequences

* One cable does charging and data, with no battery drain and no OTG adapter.
* AOA support varies by vendor, hence the compatibility matrix and fallbacks (F2 in
  [architecture.md](../architecture.md)).
* Firmware owns a USB host stack and the AOA handshake. `phone_power` can force the phone to
  re-enumerate by cycling VBUS.
