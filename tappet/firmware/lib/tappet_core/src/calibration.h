// Runtime calibration + geometry. Defaults come from the generated robot_config.h
// (config/robot.json); values can be changed at runtime (calib_set / geom_set) and
// persisted in NVS as a versioned, CRC-protected blob.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "robot_config.h"

namespace tappet {

constexpr int NUM_JOINTS = cfg::NUM_JOINTS;
constexpr int NUM_ARMS = cfg::NUM_ARMS;
constexpr int NUM_PWM_CHANNELS = 16;

struct JointCal {
  int8_t ch;
  int8_t dir;
  float gear;
  float s_zero;
  float neutral;
  float min;
  float max;
  float home;
  float park;
  float max_vel;
};

struct ArmGeom {
  float base_x;
  float base_y;
  float shoulder_h;
  float r0;
  float L1;
  float L2;
  float tool_dx;
  float tool_dz;
};

struct Linkage {
  float rel_min;
  float rel_max;
};

struct Calibration {
  JointCal joints[NUM_JOINTS];
  ArmGeom arms[NUM_ARMS];
  Linkage linkage;

  static Calibration defaults();

  // Validate every field; on failure fills msg (if non-null) and returns false.
  bool validate(char* msg, size_t msg_len) const;
};

// Joint name <-> index helpers (names are the robot.json keys).
const char* jointName(int idx);
int jointIndex(const char* name);  // -1 if unknown
int armIndex(const char* name);    // "L"->0, "R"->1, else -1
const char* armName(int arm);

// Persistence blob (header + payload). Used by the NVS layer and native tests.
struct CalibBlob {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  uint32_t crc;
  Calibration cal;
};
constexpr uint32_t CALIB_MAGIC = 0x31545054u;  // "TPT1"
constexpr uint16_t CALIB_VERSION = 1;

uint32_t crc32(const void* data, size_t len);
void packCalib(const Calibration& c, CalibBlob& out);
bool unpackCalib(const void* data, size_t len, Calibration& out);

}  // namespace tappet
