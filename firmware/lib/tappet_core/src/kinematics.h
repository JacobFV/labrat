// Arm forward / inverse kinematics, exactly as SPEC section 3. Computed in double
// precision so results match the Python/Kotlin implementations to < 1e-3.
#pragma once

#include "calibration.h"

namespace tappet {

enum class IkStatus : uint8_t { Ok = 0, Unreachable, Limit };

const char* ikStatusCode(IkStatus s);  // "ok" | "unreachable" | "limit"

struct ArmQ {
  double yaw, th1, th2;  // psi, theta1, theta2 (deg)
};

struct Vec3 {
  double x, y, z;
};

Vec3 armFk(const ArmGeom& g, const ArmQ& q);

// Pure geometric IK (no limit checks). Returns Unreachable or Ok.
IkStatus armIkRaw(const ArmGeom& g, const Vec3& p, ArmQ& out);

// IK + joint limit + linkage checks against the calibration for the given arm.
// On Limit, `out` still holds the geometric solution.
IkStatus armIk(const Calibration& cal, int arm, const Vec3& p, ArmQ& out);

// Joint-limit + linkage check for an arm configuration.
bool armWithinLimits(const Calibration& cal, int arm, const ArmQ& q, char* why = nullptr, size_t why_len = 0);

// Convenience: FK of an arm from a full 8-joint vector.
Vec3 armFkFromJoints(const Calibration& cal, int arm, const float* q8);

}  // namespace tappet
