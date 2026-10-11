#include "motion.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "joint_map.h"
#include "minjerk.h"

namespace tappet {

float distPointSegmentXY(float px, float py, float ax, float ay, float bx, float by) {
  const float vx = bx - ax, vy = by - ay;
  const float len2 = vx * vx + vy * vy;
  float t = 0.f;
  if (len2 > 1e-9f) t = clampf(((px - ax) * vx + (py - ay) * vy) / len2, 0.f, 1.f);
  const float cx = ax + t * vx - px, cy = ay + t * vy - py;
  return sqrtf(cx * cx + cy * cy);
}

static Vec3 lerp3(const Vec3& a, const Vec3& b, double s) {
  return Vec3{a.x + (b.x - a.x) * s, a.y + (b.y - a.y) * s, a.z + (b.z - a.z) * s};
}

static double dist3(const Vec3& a, const Vec3& b) {
  const double dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
  return sqrt(dx * dx + dy * dy + dz * dz);
}

MotionEngine::MotionEngine(const Calibration& cal) : cal_(cal) {
  for (int i = 0; i < NUM_JOINTS; ++i) q_[i] = cal_.joints[i].home;
}

void MotionEngine::reset(const float* q8) {
  active_ = false;
  segActive_ = false;
  count_ = 0;
  head_ = 0;
  for (int i = 0; i < NUM_JOINTS; ++i) q_[i] = q8[i];
}

bool MotionEngine::enqueue(const MotionRequest& r) {
  if (count_ >= MOTION_QUEUE_DEPTH) return false;
  queue_[(head_ + count_) % MOTION_QUEUE_DEPTH] = r;
  ++count_;
  return true;
}

void MotionEngine::cancelAll(const char* why) {
  if (active_) {
    active_ = false;
    segActive_ = false;
    if (cur_.notify && listener_) listener_->onMotionCancelled(cur_.id, why);
  }
  while (count_ > 0) {
    const MotionRequest& r = queue_[head_];
    head_ = (head_ + 1) % MOTION_QUEUE_DEPTH;
    --count_;
    if (r.notify && listener_) listener_->onMotionCancelled(r.id, why);
  }
  head_ = 0;
}

IkStatus MotionEngine::checkPoint(int arm, const Vec3& p) const {
  ArmQ q;
  return armIk(cal_, arm, p, q);
}

IkStatus MotionEngine::checkLine(int arm, const Vec3& a, const Vec3& b) const {
  const double len = dist3(a, b);
  int n = (int)ceil(len / CART_SAMPLE_STEP_MM);
  if (n < 1) n = 1;
  if (n > 256) n = 256;
  for (int i = 0; i <= n; ++i) {
    const IkStatus s = checkPoint(arm, lerp3(a, b, (double)i / n));
    if (s != IkStatus::Ok) return s;
  }
  return IkStatus::Ok;
}

// ---------------------------------------------------------------------------
// Phase tables

int MotionEngine::phasesFor(MotionType t, const Phase** out) const {
  static const Phase kJoints[] = {Phase::JointMove};
  static const Phase kMove[] = {Phase::ParkOtherLift, Phase::ParkOther, Phase::MoveLine};
  static const Phase kTap[] = {Phase::ParkOtherLift, Phase::ParkOther, Phase::Lift,
                               Phase::Transit,       Phase::Descend,   Phase::Hold, Phase::Rise};
  static const Phase kSwipe[] = {Phase::ParkOtherLift, Phase::ParkOther, Phase::Lift,
                                 Phase::Transit,       Phase::Descend,   Phase::Stroke, Phase::Rise};
  static const Phase kPark[] = {Phase::ParkLiftL, Phase::ParkLiftR, Phase::ParkJoints};
  switch (t) {
    case MotionType::Joints:
      *out = kJoints;
      return 1;
    case MotionType::Move:
      *out = kMove;
      return 3;
    case MotionType::Tap:
      *out = kTap;
      return 7;
    case MotionType::Swipe:
      *out = kSwipe;
      return 7;
    case MotionType::Park:
      *out = kPark;
      return 3;
  }
  *out = kJoints;
  return 0;
}

// ---------------------------------------------------------------------------
// Planning

bool MotionEngine::planJoint(uint8_t mask, const float* target, float user_ms) {
  seg_ = Segment();
  seg_.kind = SegKind::Joint;
  seg_.mask = mask;
  float need = 0.f;
  for (int i = 0; i < NUM_JOINTS; ++i) {
    seg_.q0[i] = q_[i];
    seg_.q1[i] = q_[i];
    if (mask & (1u << i)) {
      seg_.q1[i] = clampJoint(cal_.joints[i], target[i]);
      const float d = minJerkDurationMs(seg_.q1[i] - seg_.q0[i], cal_.joints[i].max_vel);
      if (d > need) need = d;
    }
  }
  bool moving = false;
  for (int i = 0; i < NUM_JOINTS; ++i)
    if ((mask & (1u << i)) && fabsf(seg_.q1[i] - seg_.q0[i]) > 1e-6f) moving = true;
  if (!moving && user_ms <= 0.f) return false;  // nothing to do
  float dur;
  if (user_ms > 0.f) {
    dur = user_ms;  // explicit duration is honoured (as in the simulator)
  } else {
    dur = need > MOTION_MIN_SEG_MS ? need : MOTION_MIN_SEG_MS;
    dur = ceilf(dur / MOTION_TICK_MS) * MOTION_TICK_MS;
  }
  if (dur < MOTION_TICK_MS) dur = MOTION_TICK_MS;
  seg_.dur = dur;
  seg_.t = 0.f;
  segActive_ = true;
  return true;
}

MotionEngine::StartResult MotionEngine::planCart(int arm, const Vec3& to, float user_ms) {
  const Vec3 from = toolPos(arm);
  const double len = dist3(from, to);
  if (len < 0.05) return StartResult::Skip;
  int n = (int)ceil(len / CART_SAMPLE_STEP_MM);
  if (n < 2) n = 2;
  if (n > 256) n = 256;
  double path[3] = {0, 0, 0};
  ArmQ prev{q_[cfg::ARM_JOINTS[arm][0]], q_[cfg::ARM_JOINTS[arm][1]], q_[cfg::ARM_JOINTS[arm][2]]};
  for (int i = 1; i <= n; ++i) {
    ArmQ q;
    if (armIk(cal_, arm, lerp3(from, to, (double)i / n), q) != IkStatus::Ok) return StartResult::Error;
    path[0] += fabs(q.yaw - prev.yaw);
    path[1] += fabs(q.th1 - prev.th1);
    path[2] += fabs(q.th2 - prev.th2);
    prev = q;
  }
  float need = MOTION_MIN_SEG_MS;
  const float cart = 1000.f * (float)len / CART_SPEED_MM_S;
  if (cart > need) need = cart;
  for (int k = 0; k < 3; ++k) {
    const float d = minJerkDurationMs((float)path[k], cal_.joints[cfg::ARM_JOINTS[arm][k]].max_vel);
    if (d > need) need = d;
  }
  need = ceilf(need / MOTION_TICK_MS) * MOTION_TICK_MS;
  seg_ = Segment();
  seg_.kind = SegKind::Cart;
  seg_.arm = (int8_t)arm;
  seg_.p0 = from;
  seg_.p1 = to;
  seg_.dur = user_ms > 0.f ? (user_ms < MOTION_TICK_MS ? MOTION_TICK_MS : user_ms) : need;
  seg_.t = 0.f;
  segActive_ = true;
  return StartResult::Started;
}

bool MotionEngine::planArmJoint(int arm, const ArmQ& q, float user_ms) {
  float tgt[NUM_JOINTS];
  memcpy(tgt, q_, sizeof(tgt));
  tgt[cfg::ARM_JOINTS[arm][0]] = (float)q.yaw;
  tgt[cfg::ARM_JOINTS[arm][1]] = (float)q.th1;
  tgt[cfg::ARM_JOINTS[arm][2]] = (float)q.th2;
  const uint8_t mask = (uint8_t)((1u << cfg::ARM_JOINTS[arm][0]) | (1u << cfg::ARM_JOINTS[arm][1]) |
                                 (1u << cfg::ARM_JOINTS[arm][2]));
  return planJoint(mask, tgt, user_ms);
}

bool MotionEngine::planCartOrJoint(int arm, const Vec3& to, float user_ms, IkStatus* st) {
  const StartResult r = planCart(arm, to, user_ms);
  if (r == StartResult::Started) return true;
  if (r == StartResult::Skip) {
    if (st) *st = IkStatus::Ok;
    return false;
  }
  // Straight line leaves the workspace: fall back to a joint-space move to the target.
  ArmQ q;
  const IkStatus s = armIk(cal_, arm, to, q);
  if (st) *st = s;
  if (s != IkStatus::Ok) return false;
  return planArmJoint(arm, q, user_ms);
}

static uint8_t armMask(int arm) {
  return (uint8_t)((1u << cfg::ARM_JOINTS[arm][0]) | (1u << cfg::ARM_JOINTS[arm][1]) |
                   (1u << cfg::ARM_JOINTS[arm][2]));
}

MotionEngine::StartResult MotionEngine::startPhase(Phase ph) {
  const MotionRequest& r = cur_;
  const int arm = r.arm;
  const int other = 1 - arm;
  const float zApp = r.z + r.approach_mm;
  const float zPress = r.z - r.press_mm;
  char msg[96];

  switch (ph) {
    case Phase::JointMove:
      return planJoint(r.mask, r.q, r.ms) ? StartResult::Started : StartResult::Skip;

    case Phase::ParkOtherLift: {
      parkOther_ = false;
      const Vec3 o = toolPos(other);
      float d;
      if (r.type == MotionType::Swipe)
        d = distPointSegmentXY((float)o.x, (float)o.y, r.x, r.y, r.x1, r.y1);
      else
        d = distPointSegmentXY((float)o.x, (float)o.y, r.x, r.y, r.x, r.y);
      if (d >= PARK_OTHER_RADIUS_MM) return StartResult::Skip;
      bool atPark = true;
      for (int k = 0; k < 3; ++k) {
        const int ji = cfg::ARM_JOINTS[other][k];
        if (fabsf(q_[ji] - cal_.joints[ji].park) > 0.5f) atPark = false;
      }
      if (atPark) return StartResult::Skip;
      parkOther_ = true;
      const Vec3 up{o.x, o.y, o.z + cfg::TOOL_DEFAULT_APPROACH_MM};
      return planCart(other, up, 0.f) == StartResult::Started ? StartResult::Started
                                                                                  : StartResult::Skip;
    }

    case Phase::ParkOther: {
      if (!parkOther_) return StartResult::Skip;
      float tgt[NUM_JOINTS];
      for (int i = 0; i < NUM_JOINTS; ++i) tgt[i] = cal_.joints[i].park;
      return planJoint(armMask(other), tgt, 0.f) ? StartResult::Started : StartResult::Skip;
    }

    case Phase::Lift: {
      const Vec3 c = toolPos(arm);
      if (c.z >= zApp - LIFT_EPS_MM) return StartResult::Skip;
      const Vec3 up{c.x, c.y, zApp};
      IkStatus st;
      return planCartOrJoint(arm, up, 0.f, &st) ? StartResult::Started : StartResult::Skip;
    }

    case Phase::Transit: {
      // Joint-space move to above the target (the tool is already at approach height).
      const Vec3 t{r.x, r.y, zApp};
      ArmQ q;
      const IkStatus st = armIk(cal_, arm, t, q);
      if (st != IkStatus::Ok) {
        snprintf(msg, sizeof(msg), "approach point (%.1f,%.1f,%.1f) %s", r.x, r.y, zApp, ikStatusCode(st));
        failItem(ikStatusCode(st), msg);
        return StartResult::Error;
      }
      const float ms = r.type == MotionType::Tap ? r.ms : 0.f;
      return planArmJoint(arm, q, ms) ? StartResult::Started : StartResult::Skip;
    }

    case Phase::Descend: {
      const Vec3 t{r.x, r.y, zPress};
      const StartResult s = planCart(arm, t, 0.f);
      if (s == StartResult::Error) {
        snprintf(msg, sizeof(msg), "press point (%.1f,%.1f,%.1f) not reachable on a straight line", r.x, r.y, zPress);
        failItem("unreachable", msg);
      }
      return s;
    }

    case Phase::Hold: {
      if (r.hold_ms <= 0.f) return StartResult::Skip;
      seg_ = Segment();
      seg_.kind = SegKind::Hold;
      seg_.dur = r.hold_ms;
      segActive_ = true;
      return StartResult::Started;
    }

    case Phase::Stroke: {
      const Vec3 t{r.x1, r.y1, zPress};
      const StartResult s = planCart(arm, t, r.ms);
      if (s == StartResult::Error) failItem("unreachable", "swipe stroke leaves the workspace");
      return s;
    }

    case Phase::Rise: {
      const float ex = r.type == MotionType::Swipe ? r.x1 : r.x;
      const float ey = r.type == MotionType::Swipe ? r.y1 : r.y;
      const Vec3 t{ex, ey, zApp};
      const StartResult s = planCart(arm, t, 0.f);
      if (s == StartResult::Error) failItem("unreachable", "rise path leaves the workspace");
      return s;
    }

    case Phase::MoveLine: {
      const Vec3 t{r.x, r.y, r.z};
      IkStatus st = IkStatus::Ok;
      if (planCartOrJoint(arm, t, r.ms, &st)) return StartResult::Started;
      if (st == IkStatus::Ok) {
        // Already at the target; honour an explicit duration as a dwell.
        if (r.ms > 0.f) {
          seg_ = Segment();
          seg_.kind = SegKind::Hold;
          seg_.dur = r.ms;
          segActive_ = true;
          return StartResult::Started;
        }
        return StartResult::Skip;
      }
      snprintf(msg, sizeof(msg), "target (%.1f,%.1f,%.1f) %s", r.x, r.y, r.z, ikStatusCode(st));
      failItem(ikStatusCode(st), msg);
      return StartResult::Error;
    }

    case Phase::ParkLiftL:
    case Phase::ParkLiftR: {
      const int a = ph == Phase::ParkLiftL ? cfg::ARM_L : cfg::ARM_R;
      if (!(r.mask & (1u << a))) return StartResult::Skip;
      bool atPark = true;
      for (int k = 0; k < 3; ++k) {
        const int ji = cfg::ARM_JOINTS[a][k];
        if (fabsf(q_[ji] - cal_.joints[ji].park) > 0.5f) atPark = false;
      }
      if (atPark) return StartResult::Skip;
      const Vec3 c = toolPos(a);
      const Vec3 up{c.x, c.y, c.z + cfg::TOOL_DEFAULT_APPROACH_MM};
      return planCart(a, up, 0.f) == StartResult::Started ? StartResult::Started
                                                                              : StartResult::Skip;
    }

    case Phase::ParkJoints: {
      uint8_t mask = 0;
      if (r.mask & 1u) mask |= armMask(cfg::ARM_L);
      if (r.mask & 2u) mask |= armMask(cfg::ARM_R);
      float tgt[NUM_JOINTS];
      for (int i = 0; i < NUM_JOINTS; ++i) tgt[i] = cal_.joints[i].park;
      return planJoint(mask, tgt, r.ms) ? StartResult::Started : StartResult::Skip;
    }
  }
  return StartResult::Skip;
}

// ---------------------------------------------------------------------------
// Execution

bool MotionEngine::popNext() {
  if (count_ == 0) return false;
  cur_ = queue_[head_];
  head_ = (head_ + 1) % MOTION_QUEUE_DEPTH;
  --count_;
  active_ = true;
  phaseIdx_ = -1;
  parkOther_ = false;
  segActive_ = false;
  return true;
}

bool MotionEngine::advancePhase() {
  const Phase* phases;
  const int n = phasesFor(cur_.type, &phases);
  while (++phaseIdx_ < n) {
    const StartResult r = startPhase(phases[phaseIdx_]);
    if (r == StartResult::Started) return true;
    if (r == StartResult::Error) return true;  // item already failed; active_ is false
  }
  return false;
}

void MotionEngine::finishItem() {
  active_ = false;
  segActive_ = false;
  if (cur_.notify && listener_) listener_->onMotionDone(cur_.id);
}

void MotionEngine::failItem(const char* err, const char* msg) {
  active_ = false;
  segActive_ = false;
  if (cur_.notify && listener_) listener_->onMotionError(cur_.id, err, msg);
}

bool MotionEngine::applySegment(float tau, char* msg, const char** code) {
  const float s = minJerk(tau);
  switch (seg_.kind) {
    case SegKind::Hold:
      return true;
    case SegKind::Joint:
      for (int i = 0; i < NUM_JOINTS; ++i)
        if (seg_.mask & (1u << i)) q_[i] = seg_.q0[i] + (seg_.q1[i] - seg_.q0[i]) * s;
      return true;
    case SegKind::Cart: {
      const Vec3 p = lerp3(seg_.p0, seg_.p1, s);
      ArmQ q;
      const IkStatus st = armIk(cal_, seg_.arm, p, q);
      if (st != IkStatus::Ok) {
        *code = ikStatusCode(st);
        snprintf(msg, 96, "IK failed at (%.1f,%.1f,%.1f) during Cartesian move", p.x, p.y, p.z);
        return false;
      }
      q_[cfg::ARM_JOINTS[seg_.arm][0]] = (float)q.yaw;
      q_[cfg::ARM_JOINTS[seg_.arm][1]] = (float)q.th1;
      q_[cfg::ARM_JOINTS[seg_.arm][2]] = (float)q.th2;
      return true;
    }
  }
  return true;
}

void MotionEngine::tick(float dt_ms) {
  if (paused_) return;
  // Start work if idle (an item may complete instantly; bound the loop).
  for (int guard = 0; guard < MOTION_QUEUE_DEPTH + 1 && !segActive_; ++guard) {
    if (!active_) {
      if (!popNext()) return;
    }
    if (!advancePhase()) {
      if (active_) finishItem();
    }
    if (segActive_) {
      dt_ms = 0.f;  // the first tick of a new segment starts at t=0 next time
      return;
    }
  }
  if (!segActive_) return;

  seg_.t += dt_ms;
  const float tau = seg_.dur > 0.f ? seg_.t / seg_.dur : 1.f;
  char msg[96];
  const char* code = "unreachable";
  if (!applySegment(tau >= 1.f ? 1.f : tau, msg, &code)) {
    failItem(code, msg);
    return;
  }
  if (tau >= 1.f) {
    segActive_ = false;
    if (!advancePhase()) {
      if (active_) finishItem();
    }
  }
}

}  // namespace tappet
