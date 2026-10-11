#include "calibration.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace tappet {

Calibration Calibration::defaults() {
  Calibration c;
  memset(&c, 0, sizeof(c));
  for (int i = 0; i < NUM_JOINTS; ++i) {
    const cfg::JointDefault& d = cfg::JOINTS[i];
    JointCal& j = c.joints[i];
    j.ch = (int8_t)d.ch;
    j.dir = (int8_t)d.dir;
    j.gear = d.gear;
    j.s_zero = d.s_zero;
    j.neutral = d.neutral;
    j.min = d.min;
    j.max = d.max;
    j.home = d.home;
    j.park = d.park;
    j.max_vel = d.max_vel;
  }
  for (int a = 0; a < NUM_ARMS; ++a) {
    const cfg::ArmDefault& d = cfg::ARMS[a];
    ArmGeom& g = c.arms[a];
    g.base_x = d.base_x;
    g.base_y = d.base_y;
    g.shoulder_h = d.shoulder_h;
    g.r0 = d.r0;
    g.L1 = d.L1;
    g.L2 = d.L2;
    g.tool_dx = d.tool_dx;
    g.tool_dz = d.tool_dz;
  }
  c.linkage.rel_min = cfg::LINKAGE_REL_MIN;
  c.linkage.rel_max = cfg::LINKAGE_REL_MAX;
  return c;
}

static bool fail(char* msg, size_t n, const char* fmt, const char* a, const char* b = "") {
  if (msg && n) snprintf(msg, n, fmt, a, b);
  return false;
}

bool Calibration::validate(char* msg, size_t n) const {
  for (int i = 0; i < NUM_JOINTS; ++i) {
    const JointCal& j = joints[i];
    const char* nm = jointName(i);
    const float vals[] = {j.gear, j.s_zero, j.neutral, j.min, j.max, j.home, j.park, j.max_vel};
    for (float v : vals)
      if (!isfinite(v)) return fail(msg, n, "%s: non-finite value", nm);
    if (j.ch < 0 || j.ch >= NUM_PWM_CHANNELS) return fail(msg, n, "%s: ch must be 0..15", nm);
    if (j.dir != 1 && j.dir != -1) return fail(msg, n, "%s: dir must be 1 or -1", nm);
    if (!(j.gear > 0.f && j.gear <= 10.f)) return fail(msg, n, "%s: gear must be in (0,10]", nm);
    if (j.s_zero < 0.f || j.s_zero > 180.f) return fail(msg, n, "%s: s_zero must be 0..180", nm);
    if (!(j.min < j.max)) return fail(msg, n, "%s: min must be < max", nm);
    if (j.home < j.min || j.home > j.max) return fail(msg, n, "%s: home outside [min,max]", nm);
    if (j.park < j.min || j.park > j.max) return fail(msg, n, "%s: park outside [min,max]", nm);
    if (!(j.max_vel > 0.f && j.max_vel <= 2000.f)) return fail(msg, n, "%s: max_vel must be in (0,2000]", nm);
  }
  for (int a = 0; a < NUM_ARMS; ++a) {
    const ArmGeom& g = arms[a];
    const char* nm = armName(a);
    const float vals[] = {g.base_x, g.base_y, g.shoulder_h, g.r0, g.L1, g.L2, g.tool_dx, g.tool_dz};
    for (float v : vals)
      if (!isfinite(v)) return fail(msg, n, "arm %s: non-finite value", nm);
    if (!(g.L1 > 0.f) || !(g.L2 > 0.f)) return fail(msg, n, "arm %s: L1/L2 must be > 0", nm);
  }
  if (!isfinite(linkage.rel_min) || !isfinite(linkage.rel_max) || !(linkage.rel_min < linkage.rel_max))
    return fail(msg, n, "%s", "linkage: rel_min must be < rel_max");
  return true;
}

const char* jointName(int idx) {
  if (idx < 0 || idx >= NUM_JOINTS) return "?";
  return cfg::JOINTS[idx].name;
}

int jointIndex(const char* name) {
  if (!name) return -1;
  for (int i = 0; i < NUM_JOINTS; ++i)
    if (strcmp(cfg::JOINTS[i].name, name) == 0) return i;
  return -1;
}

int armIndex(const char* name) {
  if (!name) return -1;
  if (strcmp(name, "L") == 0) return cfg::ARM_L;
  if (strcmp(name, "R") == 0) return cfg::ARM_R;
  return -1;
}

const char* armName(int arm) { return (arm >= 0 && arm < NUM_ARMS) ? cfg::ARM_NAMES[arm] : "?"; }

uint32_t crc32(const void* data, size_t len) {
  const uint8_t* p = static_cast<const uint8_t*>(data);
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; ++i) {
    crc ^= p[i];
    for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

void packCalib(const Calibration& c, CalibBlob& out) {
  memset(&out, 0, sizeof(out));
  out.magic = CALIB_MAGIC;
  out.version = CALIB_VERSION;
  out.size = (uint16_t)sizeof(Calibration);
  out.cal = c;
  out.crc = crc32(&out.cal, sizeof(Calibration));
}

bool unpackCalib(const void* data, size_t len, Calibration& out) {
  if (!data || len != sizeof(CalibBlob)) return false;
  CalibBlob b;
  memcpy(&b, data, sizeof(b));
  if (b.magic != CALIB_MAGIC || b.version != CALIB_VERSION || b.size != sizeof(Calibration)) return false;
  if (crc32(&b.cal, sizeof(Calibration)) != b.crc) return false;
  if (!b.cal.validate(nullptr, 0)) return false;
  out = b.cal;
  return true;
}

}  // namespace tappet
