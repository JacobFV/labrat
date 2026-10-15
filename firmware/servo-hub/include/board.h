// Pin map per board. hardware/electronics/pin_map.csv is the wiring-side copy of this table; keep them equal
// (hardware/electronics/gen_wiring.py checks it).
//
// Every input is a switch to GND on a GPIO with the internal pull-up: no resistors. ADC pins read 0..~3.1 V.
#pragma once

#include "hal.h"

#if defined(HUB_BOARD_ESP32S3)
// ESP32-S3-DevKitC-1 (N8 / N8R8 / N16R8). Avoid 0/3/45/46 (strapping), 19/20 (native USB), 26..37 (flash/PSRAM),
// 43/44 (UART0 to the USB-UART chip), 38/48 (RGB LED, depending on board revision).
#define HUB_BOARD_NAME "esp32-s3-devkitc-1"
#define PIN_SDA 8
#define PIN_SCL 9
#define PIN_OE 10        // PCA9685 /OE (high = every output off); -1 if not wired
#define PIN_SYR_HOME 4   // plunger bottom-stop microswitch (NO contact to GND)
#define PIN_ESTOP 5      // mushroom button, NC contact to GND (open = stop)
#define PIN_IN1 6        // spare switch input
#define PIN_IN2 7        // spare switch input
#define PIN_A0 1         // ADC1_CH0, spare analog
#define PIN_A1 2         // ADC1_CH1, spare analog
#elif defined(HUB_BOARD_ESP32)
// Classic ESP32 DevKitC (WROOM-32). Avoid 0/2/5/12/15 (strapping), 6..11 (flash), 34..39 for switches (input-only,
// no internal pull-up), ADC2 pins for analog (unusable with the radio on).
#define HUB_BOARD_NAME "esp32-devkitc"
#define PIN_SDA 21
#define PIN_SCL 22
#define PIN_OE 23
#define PIN_SYR_HOME 26
#define PIN_ESTOP 27
#define PIN_IN1 25
#define PIN_IN2 33
#define PIN_A0 34        // ADC1_CH6 (input-only pin: fine for analog)
#define PIN_A1 35        // ADC1_CH7
#else
#error "define HUB_BOARD_ESP32S3 or HUB_BOARD_ESP32"
#endif

#define PCA_ADDR 0x40    // breakout default (A0..A5 open)
#ifndef PCA_OSC_HZ_TRIM
#define PCA_OSC_HZ_TRIM 25000000.0f  // measure a 1500 us pulse with a scope / logic analyser and trim if needed
#endif

namespace hub {
// Index order here is the index the HAL reads (inputClosed(i) <-> HUB_INPUT_PINS[i]).
static const InputDef BOARD_INPUTS[] = {
    {"syr_home", false, 20},
    {"estop", true, 10},
    {"in1", false, 20},
    {"in2", false, 20},
};
static const int HUB_INPUT_PINS[] = {PIN_SYR_HOME, PIN_ESTOP, PIN_IN1, PIN_IN2};
static const AdcDef BOARD_ADCS[] = {{"a0"}, {"a1"}};
static const int HUB_ADC_PINS[] = {PIN_A0, PIN_A1};
static const BoardDef BOARD = {HUB_BOARD_NAME, BOARD_INPUTS, 4, BOARD_ADCS, 2};
}  // namespace hub
