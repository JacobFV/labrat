// Joint angle q (deg) <-> servo angle s (deg) <-> pulse width (us), SPEC section 2:
//   s = s_zero + dir * gear * (q - neutral),  s clamped to [0,180], q clamped to [min,max]
//   pulse_us = us_min + s * (us_max - us_min) / deg_range
#pragma once

#include "calibration.h"

namespace tappet {

float clampf(float v, float lo, float hi);
float clampJoint(const JointCal& j, float q);
float jointToServoDeg(const JointCal& j, float q);  // clamps q and s
float servoDegToUs(float s);                          // clamps s
float jointToUs(const JointCal& j, float q);
// Inverse (for diagnostics): pulse -> joint angle (not clamped to joint limits).
float usToServoDeg(float us);
float servoDegToJoint(const JointCal& j, float s);

}  // namespace tappet
