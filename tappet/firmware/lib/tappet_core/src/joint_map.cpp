#include "joint_map.h"

namespace tappet {

float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

float clampJoint(const JointCal& j, float q) { return clampf(q, j.min, j.max); }

float jointToServoDeg(const JointCal& j, float q) {
  const float qc = clampJoint(j, q);
  const float s = j.s_zero + (float)j.dir * j.gear * (qc - j.neutral);
  return clampf(s, 0.f, cfg::SERVO_DEG_RANGE);
}

float servoDegToUs(float s) {
  s = clampf(s, 0.f, cfg::SERVO_DEG_RANGE);
  return cfg::SERVO_US_MIN + s * (cfg::SERVO_US_MAX - cfg::SERVO_US_MIN) / cfg::SERVO_DEG_RANGE;
}

float jointToUs(const JointCal& j, float q) { return servoDegToUs(jointToServoDeg(j, q)); }

float usToServoDeg(float us) {
  return (us - cfg::SERVO_US_MIN) * cfg::SERVO_DEG_RANGE / (cfg::SERVO_US_MAX - cfg::SERVO_US_MIN);
}

float servoDegToJoint(const JointCal& j, float s) {
  return j.neutral + (s - j.s_zero) / ((float)j.dir * j.gear);
}

}  // namespace tappet
