// Minimum-jerk interpolation profile: s(tau) = 10 tau^3 - 15 tau^4 + 6 tau^5,
// tau in [0,1]. Zero velocity and acceleration at both ends. Peak velocity is
// 1.875 x the average velocity, used to derive default durations from max_vel.
#pragma once

namespace tappet {

constexpr float MINJERK_PEAK_FACTOR = 1.875f;

inline float minJerk(float tau) {
  if (tau <= 0.f) return 0.f;
  if (tau >= 1.f) return 1.f;
  const float t3 = tau * tau * tau;
  return t3 * (10.f + tau * (-15.f + 6.f * tau));
}

// Normalised velocity ds/dtau (peak 1.875 at tau = 0.5).
inline float minJerkVel(float tau) {
  if (tau <= 0.f || tau >= 1.f) return 0.f;
  const float t2 = tau * tau;
  return 30.f * t2 * (1.f - tau) * (1.f - tau);
}

// Minimum duration (ms) so that a min-jerk move of `delta` degrees peaks at `max_vel` deg/s.
inline float minJerkDurationMs(float delta, float max_vel) {
  if (delta < 0.f) delta = -delta;
  if (max_vel <= 0.f) return 0.f;
  return 1000.f * MINJERK_PEAK_FACTOR * delta / max_vel;
}

}  // namespace tappet
