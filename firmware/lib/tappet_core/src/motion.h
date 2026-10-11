// Motion engine: queue (depth 16) of motion requests executed at a fixed tick
// (100 Hz by default). Joint moves use minimum-jerk interpolation in joint space;
// Cartesian moves interpolate the stylus tip along a straight line (min-jerk
// timing) and solve IK every tick. Tap / swipe / park are expanded into phases
// lazily, each phase planned from the *current* state when it starts.
#pragma once

#include <stdint.h>

#include "calibration.h"
#include "kinematics.h"

namespace tappet {

constexpr int MOTION_QUEUE_DEPTH = 16;
constexpr float MOTION_TICK_MS = 10.f;          // 100 Hz
constexpr float MOTION_MIN_SEG_MS = 60.f;       // floor for default segment durations
constexpr float CART_SPEED_MM_S = 150.f;        // default Cartesian speed
constexpr float PARK_OTHER_RADIUS_MM = 60.f;    // park the other arm if its tool is this close (xy)
constexpr float LIFT_EPS_MM = 0.5f;             // "already high enough" tolerance
constexpr float CART_SAMPLE_STEP_MM = 2.f;      // path pre-validation resolution

enum class MotionType : uint8_t { Joints, Move, Tap, Swipe, Park };

struct MotionRequest {
  MotionType type = MotionType::Joints;
  uint32_t id = 0;
  bool notify = false;      // emit done/error events for this id
  int8_t arm = 0;           // Move/Tap/Swipe: arm index
  uint8_t mask = 0;         // Joints: joint bitmask; Park: arm bitmask (bit0 L, bit1 R)
  float q[NUM_JOINTS] = {}; // Joints: targets for masked joints
  float x = 0, y = 0, z = 0;  // Move/Tap target; Swipe start (x0,y0) and z
  float x1 = 0, y1 = 0;       // Swipe end
  float ms = 0;               // requested duration (<= 0: derived from max_vel)
  float hold_ms = 0;
  float press_mm = 0;
  float approach_mm = 0;
};

class MotionListener {
 public:
  virtual ~MotionListener() = default;
  virtual void onMotionDone(uint32_t id) = 0;
  virtual void onMotionError(uint32_t id, const char* err, const char* msg) = 0;
  virtual void onMotionCancelled(uint32_t id, const char* why) = 0;
};

class MotionEngine {
 public:
  explicit MotionEngine(const Calibration& cal);

  void setListener(MotionListener* l) { listener_ = l; }

  // Set the commanded joint state (e.g. at boot) - cancels everything.
  void reset(const float* q8);

  bool enqueue(const MotionRequest& r);  // false if queue full
  void cancelAll(const char* why);       // abort active + clear queue (cancel events), hold position

  void tick(float dt_ms);
  void setPaused(bool p) { paused_ = p; }
  bool paused() const { return paused_; }

  bool busy() const { return active_ || count_ > 0; }
  int queued() const { return count_; }
  bool activeValid() const { return active_; }
  uint32_t activeId() const { return cur_.id; }

  const float* joints() const { return q_; }
  float joint(int i) const { return q_[i]; }
  // Direct write of a commanded joint (used by servo_raw bookkeeping / tests).
  void setJoint(int i, float v) { q_[i] = v; }

  Vec3 toolPos(int arm) const { return armFkFromJoints(cal_, arm, q_); }

  // ---- validation helpers used at enqueue time ----
  // Checks a point (with limits); returns status.
  IkStatus checkPoint(int arm, const Vec3& p) const;
  // Checks every sample along a straight segment.
  IkStatus checkLine(int arm, const Vec3& a, const Vec3& b) const;

 private:
  enum class Phase : uint8_t {
    ParkOtherLift,
    ParkOther,
    Lift,
    Transit,
    Descend,
    Hold,
    Stroke,
    Rise,
    MoveLine,
    JointMove,
    ParkLiftL,
    ParkLiftR,
    ParkJoints,
  };
  enum class SegKind : uint8_t { Joint, Cart, Hold };
  enum class StartResult : uint8_t { Started, Skip, Error };

  struct Segment {
    SegKind kind = SegKind::Hold;
    float t = 0, dur = 0;
    uint8_t mask = 0;
    float q0[NUM_JOINTS] = {};
    float q1[NUM_JOINTS] = {};
    int8_t arm = 0;
    Vec3 p0{0, 0, 0}, p1{0, 0, 0};
  };

  bool popNext();
  void beginItem();
  bool advancePhase();  // returns false when the item has no more phases
  StartResult startPhase(Phase ph);
  void finishItem();
  void failItem(const char* err, const char* msg);
  bool applySegment(float tau, char* err, const char** code);

  bool planJoint(uint8_t mask, const float* target, float user_ms);
  StartResult planCart(int arm, const Vec3& to, float user_ms);
  bool planCartOrJoint(int arm, const Vec3& to, float user_ms, IkStatus* st);
  bool planArmJoint(int arm, const ArmQ& q, float user_ms);
  int phasesFor(MotionType t, const Phase** out) const;

  const Calibration& cal_;
  MotionListener* listener_ = nullptr;
  float q_[NUM_JOINTS] = {};

  MotionRequest queue_[MOTION_QUEUE_DEPTH];
  int head_ = 0, count_ = 0;

  bool active_ = false;
  MotionRequest cur_;
  int phaseIdx_ = -1;
  bool parkOther_ = false;
  bool segActive_ = false;
  Segment seg_;
  bool paused_ = false;
};

// Distance in the xy plane from point p to segment ab.
float distPointSegmentXY(float px, float py, float ax, float ay, float bx, float by);

}  // namespace tappet
