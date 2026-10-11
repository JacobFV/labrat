#include "kinematics.h"

#include <math.h>
#include <stdio.h>

namespace tappet {

static constexpr double kDeg = 180.0 / 3.14159265358979323846;
static constexpr double kRad = 3.14159265358979323846 / 180.0;
static constexpr double kLimitEps = 1e-6;  // tolerance for floating point at limits

const char* ikStatusCode(IkStatus s) {
  switch (s) {
    case IkStatus::Ok:
      return "ok";
    case IkStatus::Unreachable:
      return "unreachable";
    case IkStatus::Limit:
      return "limit";
  }
  return "?";
}

Vec3 armFk(const ArmGeom& g, const ArmQ& q) {
  const double psi = q.yaw * kRad, t1 = q.th1 * kRad, t2 = q.th2 * kRad;
  const double r = g.r0 + g.L1 * cos(t1) + g.L2 * cos(t2) + g.tool_dx;
  Vec3 p;
  p.z = g.shoulder_h + g.L1 * sin(t1) + g.L2 * sin(t2) - g.tool_dz;
  p.x = g.base_x - r * sin(psi);
  p.y = g.base_y + r * cos(psi);
  return p;
}

static double clampUnit(double v) { return v < -1.0 ? -1.0 : (v > 1.0 ? 1.0 : v); }

IkStatus armIkRaw(const ArmGeom& g, const Vec3& p, ArmQ& out) {
  const double L1 = g.L1, L2 = g.L2;
  const double dx = p.x - g.base_x;
  const double dy = p.y - g.base_y;
  const double psi = atan2(-dx, dy);
  const double r = hypot(dx, dy) - g.r0 - g.tool_dx;
  const double zp = p.z - g.shoulder_h + g.tool_dz;
  const double D = hypot(r, zp);
  if (!(D >= fabs(L1 - L2)) || !(D <= L1 + L2) || D <= 0.0) return IkStatus::Unreachable;
  const double phi = atan2(zp, r);
  const double alpha = acos(clampUnit((L1 * L1 + D * D - L2 * L2) / (2.0 * L1 * D)));
  const double beta = acos(clampUnit((L2 * L2 + D * D - L1 * L1) / (2.0 * L2 * D)));
  out.yaw = psi * kDeg;
  out.th1 = (phi + alpha) * kDeg;
  out.th2 = (phi - beta) * kDeg;
  return IkStatus::Ok;
}

bool armWithinLimits(const Calibration& cal, int arm, const ArmQ& q, char* why, size_t why_len) {
  const double v[3] = {q.yaw, q.th1, q.th2};
  for (int k = 0; k < 3; ++k) {
    const int ji = cfg::ARM_JOINTS[arm][k];
    const JointCal& j = cal.joints[ji];
    if (v[k] < j.min - kLimitEps || v[k] > j.max + kLimitEps) {
      if (why && why_len)
        snprintf(why, why_len, "%s=%.1f outside [%.0f,%.0f]", jointName(ji), v[k], (double)j.min, (double)j.max);
      return false;
    }
  }
  const double rel = q.th1 - q.th2;
  if (rel < cal.linkage.rel_min - kLimitEps || rel > cal.linkage.rel_max + kLimitEps) {
    if (why && why_len)
      snprintf(why, why_len, "linkage th1-th2=%.1f outside [%.0f,%.0f]", rel, (double)cal.linkage.rel_min,
               (double)cal.linkage.rel_max);
    return false;
  }
  return true;
}

IkStatus armIk(const Calibration& cal, int arm, const Vec3& p, ArmQ& out) {
  if (arm < 0 || arm >= NUM_ARMS) return IkStatus::Unreachable;
  const IkStatus s = armIkRaw(cal.arms[arm], p, out);
  if (s != IkStatus::Ok) return s;
  return armWithinLimits(cal, arm, out) ? IkStatus::Ok : IkStatus::Limit;
}

Vec3 armFkFromJoints(const Calibration& cal, int arm, const float* q8) {
  ArmQ q{q8[cfg::ARM_JOINTS[arm][0]], q8[cfg::ARM_JOINTS[arm][1]], q8[cfg::ARM_JOINTS[arm][2]]};
  return armFk(cal.arms[arm], q);
}

}  // namespace tappet
