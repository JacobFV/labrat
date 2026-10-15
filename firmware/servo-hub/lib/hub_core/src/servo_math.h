// Pure arithmetic shared by the ESP32 driver and the tests: PCA9685 prescale / tick math, degree <-> pulse
// mapping and the easing curve.
#pragma once

#include <math.h>
#include <stdint.h>

namespace hub {

constexpr float PCA_OSC_HZ = 25e6f;  // internal oscillator, nominal (real chips are within a few %)
constexpr float SERVO_HZ = 50.f;

// PRE_SCALE = round(osc / (4096 * f)) - 1, clamped to the chip's 3..255 (datasheet 7.3.5).
inline int pcaPrescale(float osc_hz, float pwm_hz) {
  int p = (int)lroundf(osc_hz / (4096.f * pwm_hz)) - 1;
  return p < 3 ? 3 : (p > 255 ? 255 : p);
}

// The frequency the chip really runs at for that prescale.
inline float pcaActualHz(float osc_hz, int prescale) { return osc_hz / (4096.f * (float)(prescale + 1)); }

// OFF count for a pulse of `us` (ON count 0). One tick = 1e6 / (f * 4096) us (~4.88 us at 50 Hz).
inline uint16_t pcaTicks(float us, float actual_hz) {
  float t = us * actual_hz * 4096.f / 1e6f;
  if (t < 0.f) t = 0.f;
  if (t > 4095.f) t = 4095.f;
  return (uint16_t)lroundf(t);
}

inline float degToUs(float deg, uint16_t min_us, uint16_t max_us) {
  return (float)min_us + ((float)max_us - (float)min_us) * deg / 180.f;
}

inline float usToDeg(float us, uint16_t min_us, uint16_t max_us) {
  if (max_us == min_us) return 0.f;
  return (us - (float)min_us) * 180.f / ((float)max_us - (float)min_us);
}

// Smoothstep: zero velocity at both ends, so a 9 g servo does not jerk the probe arm or the phone.
inline float ease(float x) {
  if (x <= 0.f) return 0.f;
  if (x >= 1.f) return 1.f;
  return x * x * (3.f - 2.f * x);
}

}  // namespace hub
