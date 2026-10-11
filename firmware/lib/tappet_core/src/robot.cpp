#include "robot.h"

#include <ArduinoJson.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "joint_map.h"
#include "kinematics.h"

namespace tappet {

namespace {

double r2(double v) { return round(v * 100.0) / 100.0; }
double r3(double v) { return round(v * 1000.0) / 1000.0; }

enum class Arg : uint8_t { Missing, Ok, Bad };

Arg getNum(JsonObjectConst o, const char* key, float& out) {
  JsonVariantConst v = o[key];
  if (v.isNull()) return Arg::Missing;
  if (v.is<bool>() || !v.is<double>()) return Arg::Bad;
  const double d = v.as<double>();
  if (!isfinite(d)) return Arg::Bad;
  out = (float)d;
  return Arg::Ok;
}

const char* kArmFields[] = {"base_x", "base_y", "shoulder_h", "r0", "L1", "L2", "tool_dx", "tool_dz"};

float* armField(ArmGeom& g, const char* k) {
  if (!strcmp(k, "base_x")) return &g.base_x;
  if (!strcmp(k, "base_y")) return &g.base_y;
  if (!strcmp(k, "shoulder_h")) return &g.shoulder_h;
  if (!strcmp(k, "r0")) return &g.r0;
  if (!strcmp(k, "L1")) return &g.L1;
  if (!strcmp(k, "L2")) return &g.L2;
  if (!strcmp(k, "tool_dx")) return &g.tool_dx;
  if (!strcmp(k, "tool_dz")) return &g.tool_dz;
  return nullptr;
}

float armFieldGet(const ArmGeom& g, int i) {
  const float v[] = {g.base_x, g.base_y, g.shoulder_h, g.r0, g.L1, g.L2, g.tool_dx, g.tool_dz};
  return v[i];
}

uint8_t armJointMask(int arm) {
  return (uint8_t)((1u << cfg::ARM_JOINTS[arm][0]) | (1u << cfg::ARM_JOINTS[arm][1]) |
                   (1u << cfg::ARM_JOINTS[arm][2]));
}

}  // namespace

// ---------------------------------------------------------------------------

struct Robot::Ctx {
  int transport;
  JsonObjectConst req;
  JsonDocument resp;
  const char* cmd = "";
  bool hasUintId = false;
  uint32_t id = 0;

  void error(const char* code, const char* msg) {
    resp["ok"] = false;
    resp["err"] = code;
    resp["msg"] = msg;
  }
  void ok() { resp["ok"] = true; }
};

Robot::Robot(Hal& hal, LineSink& sink)
    : hal_(hal), sink_(sink), cal_(Calibration::defaults()), engine_(cal_), vservo_(NAN), vin_(NAN),
      wdMs_(cfg::WATCHDOG_MS) {
  engine_.setListener(this);
  for (float& u : lastUs_) u = -1.f;
}

void Robot::begin() {
  Calibration loaded;
  if (hal_.loadCalib(loaded)) cal_ = loaded;
  float home[NUM_JOINTS];
  for (int i = 0; i < NUM_JOINTS; ++i) home[i] = cal_.joints[i].home;
  engine_.reset(home);

  hal_.allOff();
  hal_.setOutputsEnabled(false);
  enabled_ = false;

  const uint32_t now = hal_.millis();
  lastTick_ = now;
  lastSupervise_ = now;
  lastCmdMs_ = now;

  JsonDocument ev;
  ev["evt"] = "boot";
  ev["fw"] = FW_NAME;
  ev["ver"] = FW_VERSION;
  sendDoc(TRANSPORT_ALL, ev);

  if (!hal_.pwmOk()) setFault(FAULT_PWM, true, "pwm");
}

void Robot::sendDoc(int transport, const JsonDocument& doc) {
  static char buf[OUT_LINE_MAX];
  const size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf) - 1) {
    const char* fallback = "{\"evt\":\"error\",\"err\":\"fault\",\"msg\":\"response too large\"}";
    sink_.sendLine(transport, fallback, strlen(fallback));
    return;
  }
  sink_.sendLine(transport, buf, n);
}

bool Robot::linkUp() const {
  if (transportConnected_[TRANSPORT_AOA]) return true;
  return rxSeen_[TRANSPORT_UART] && (uint32_t)(hal_.millis() - lastRx_[TRANSPORT_UART]) < LINK_UART_TIMEOUT_MS;
}

void Robot::setTransportConnected(int transport, bool connected) {
  if (transport >= 0 && transport < TRANSPORT_COUNT) transportConnected_[transport] = connected;
}

// ---------------------------------------------------------------------------
// Output / state

void Robot::writeOutputs(bool force) {
  if (!enabled_) return;
  for (int i = 0; i < NUM_JOINTS; ++i) {
    const JointCal& j = cal_.joints[i];
    if (rawOverride_[j.ch]) continue;
    const float us = jointToUs(j, engine_.joint(i));
    if (force || fabsf(us - lastUs_[j.ch]) >= 0.01f) {
      hal_.writePulseUs(j.ch, us);
      lastUs_[j.ch] = us;
    }
  }
}

bool Robot::doEnable(char* msg, size_t n) {
  if (!hal_.pwmOk()) {
    setFault(FAULT_PWM, true, "pwm");
    snprintf(msg, n, "PWM driver not responding");
    return false;
  }
  if (faults_ & FAULT_PWM) setFault(FAULT_PWM, false, "pwm");
  // Hold the current targets (clamped into limits).
  for (int i = 0; i < NUM_JOINTS; ++i) engine_.setJoint(i, clampJoint(cal_.joints[i], engine_.joint(i)));
  for (bool& r : rawOverride_) r = false;
  enabled_ = true;
  writeOutputs(true);
  hal_.setOutputsEnabled(true);
  lastCmdMs_ = hal_.millis();
  lastTick_ = hal_.millis();
  return true;
}

void Robot::doRelax() {
  hal_.setOutputsEnabled(false);
  hal_.allOff();
  enabled_ = false;
  wdTripping_ = false;
  engine_.cancelAll("relax");
  for (bool& r : rawOverride_) r = false;
  for (float& u : lastUs_) u = -1.f;
}

void Robot::setFault(uint8_t bit, bool on, const char* code) {
  const bool was = (faults_ & bit) != 0;
  if (was == on) return;
  if (on)
    faults_ |= bit;
  else
    faults_ &= (uint8_t)~bit;
  if (bit == FAULT_BROWNOUT) engine_.setPaused(on);
  JsonDocument ev;
  ev["evt"] = "fault";
  ev["code"] = code;
  ev["active"] = on;
  sendDoc(TRANSPORT_ALL, ev);
}

void Robot::supervise(uint32_t now) {
  vservo_ = hal_.readVservo();
  vin_ = hal_.readVin();
  if (!isnan(vservo_)) {
    if (vservo_ < BROWNOUT_TRIP_V) {
      if (lowCount_ < BROWNOUT_SAMPLES) ++lowCount_;
      if (lowCount_ >= BROWNOUT_SAMPLES) setFault(FAULT_BROWNOUT, true, "brownout");
    } else {
      lowCount_ = 0;
      if (vservo_ >= BROWNOUT_CLEAR_V) setFault(FAULT_BROWNOUT, false, "brownout");
    }
  }
  setFault(FAULT_PHONE_VBUS, hal_.phoneVbusFault(), "phone_vbus");
  // Re-probe the PWM driver once a second.
  if ((uint32_t)(now - lastProbe_) >= 1000) {
    lastProbe_ = now;
    const bool ok = hal_.pwmOk();
    if (!ok && !(faults_ & FAULT_PWM)) {
      setFault(FAULT_PWM, true, "pwm");
      if (enabled_) doRelax();
    } else if (ok && (faults_ & FAULT_PWM)) {
      setFault(FAULT_PWM, false, "pwm");
    }
  }
}

void Robot::fillStatus(JsonObject o) const {
  o["enabled"] = enabled_;
  o["busy"] = engine_.busy();
  o["queue"] = engine_.queued();
  JsonObject jj = o["joints"].to<JsonObject>();
  for (int i = 0; i < NUM_JOINTS; ++i) jj[jointName(i)] = r2(engine_.joint(i));
  JsonObject tools = o["tools"].to<JsonObject>();
  for (int a = 0; a < NUM_ARMS; ++a) {
    const Vec3 p = engine_.toolPos(a);
    JsonArray arr = tools[armName(a)].to<JsonArray>();
    arr.add(r2(p.x));
    arr.add(r2(p.y));
    arr.add(r2(p.z));
  }
  if (isnan(vservo_))
    o["vservo"] = nullptr;
  else
    o["vservo"] = r2(vservo_);
  if (isnan(vin_))
    o["vin"] = nullptr;
  else
    o["vin"] = r2(vin_);
  JsonArray f = o["faults"].to<JsonArray>();
  if (faults_ & FAULT_BROWNOUT) f.add("brownout");
  if (faults_ & FAULT_PWM) f.add("pwm");
  if (faults_ & FAULT_PHONE_VBUS) f.add("phone_vbus");
}

void Robot::emitStatusEvent() {
  JsonDocument ev;
  ev["evt"] = "status";
  fillStatus(ev.as<JsonObject>());
  sendDoc(TRANSPORT_ALL, ev);
}

void Robot::loop() {
  const uint32_t now = hal_.millis();

  if ((uint32_t)(now - lastSupervise_) >= SUPERVISE_PERIOD_MS) {
    lastSupervise_ = now;
    supervise(now);
  }

  // 100 Hz motion tick with bounded catch-up.
  uint32_t elapsed = now - lastTick_;
  if (elapsed > 100) {
    lastTick_ = now - (uint32_t)MOTION_TICK_MS;
    elapsed = (uint32_t)MOTION_TICK_MS;
  }
  bool ticked = false;
  while ((uint32_t)(now - lastTick_) >= (uint32_t)MOTION_TICK_MS) {
    lastTick_ += (uint32_t)MOTION_TICK_MS;
    if (enabled_) {
      engine_.tick(MOTION_TICK_MS);
      ticked = true;
    }
  }
  if (ticked) writeOutputs(false);

  // Watchdog: park both arms, then relax.
  if (enabled_ && wdMs_ > 0 && !wdTripping_ && (uint32_t)(now - lastCmdMs_) > wdMs_) {
    wdTripping_ = true;
    wdTripMs_ = now;
    engine_.cancelAll("watchdog");
    MotionRequest r;
    r.type = MotionType::Park;
    r.mask = 3;
    r.notify = false;
    engine_.enqueue(r);
  }
  if (wdTripping_ && (!engine_.busy() || (uint32_t)(now - wdTripMs_) > WATCHDOG_PARK_TIMEOUT_MS)) {
    doRelax();
    wdEventMs_ = now;
    wdEventShown_ = true;
    JsonDocument ev;
    ev["evt"] = "watchdog";
    sendDoc(TRANSPORT_ALL, ev);
  }

  if (streamHz_ > 0) {
    const uint32_t period = 1000u / (uint32_t)streamHz_;
    if ((uint32_t)(now - lastStream_) >= period) {
      lastStream_ = now;
      emitStatusEvent();
    }
  }
}

void Robot::onButton(ButtonAction a) {
  if (a == ButtonAction::None) return;
  JsonDocument ev;
  ev["evt"] = "button";
  ev["action"] = buttonActionName(a);
  sendDoc(TRANSPORT_ALL, ev);
  lastCmdMs_ = hal_.millis();
  char msg[64];
  if (a == ButtonAction::Short) {
    if (enabled_)
      doRelax();
    else
      doEnable(msg, sizeof(msg));
  } else {
    if (!enabled_ && !doEnable(msg, sizeof(msg))) return;
    engine_.cancelAll("button");
    MotionRequest r;
    r.type = MotionType::Joints;
    r.mask = 0xFF;
    for (int i = 0; i < NUM_JOINTS; ++i) r.q[i] = cal_.joints[i].home;
    engine_.enqueue(r);
  }
}

Rgb Robot::ledColor(uint32_t now) const {
  const bool blinkSlow = (now / 250) % 2 == 0;
  const bool blinkFast = (now / 100) % 2 == 0;
  if (faults_) return blinkSlow ? Rgb{48, 0, 0} : Rgb{0, 0, 0};
  if (wdEventShown_ && (uint32_t)(now - wdEventMs_) < 3000) return blinkFast ? Rgb{48, 20, 0} : Rgb{0, 0, 0};
  if (enabled_) {
    if (engine_.busy()) return blinkFast ? Rgb{0, 48, 0} : Rgb{0, 12, 0};
    return Rgb{0, 32, 0};
  }
  if (linkUp()) return Rgb{0, 20, 24};
  // No link: slow blue "breathing" (triangle wave, 2 s period).
  const uint32_t ph = now % 2000;
  const uint32_t lvl = ph < 1000 ? ph : 2000 - ph;
  return Rgb{0, 0, (uint8_t)(2 + lvl * 30 / 1000)};
}

// ---------------------------------------------------------------------------
// Motion events

void Robot::onMotionDone(uint32_t id) {
  JsonDocument ev;
  ev["evt"] = "done";
  ev["id"] = id;
  sendDoc(TRANSPORT_ALL, ev);
}

void Robot::onMotionError(uint32_t id, const char* err, const char* msg) {
  JsonDocument ev;
  ev["evt"] = "error";
  ev["id"] = id;
  ev["err"] = err;
  ev["msg"] = msg;
  sendDoc(TRANSPORT_ALL, ev);
}

void Robot::onMotionCancelled(uint32_t id, const char* why) {
  char msg[48];
  snprintf(msg, sizeof(msg), "cancelled (%s)", why);
  onMotionError(id, "fault", msg);
}

// ---------------------------------------------------------------------------
// Request handling

void Robot::handleOverflow(int transport) {
  JsonDocument d;
  d["id"] = nullptr;
  d["ok"] = false;
  d["err"] = "bad_json";
  d["msg"] = "line too long";
  sendDoc(transport, d);
}

void Robot::handleLine(int transport, const char* line, size_t len) {
  if (transport >= 0 && transport < TRANSPORT_COUNT) {
    lastRx_[transport] = hal_.millis();
    rxSeen_[transport] = true;
  }
  // Skip blank lines silently.
  size_t i = 0;
  while (i < len && (line[i] == ' ' || line[i] == '\t' || line[i] == '\r')) ++i;
  if (i == len) return;

  Ctx c;
  c.transport = transport;
  JsonDocument req;
  const DeserializationError e = deserializeJson(req, line, len);
  if (e || !req.is<JsonObject>()) {
    c.resp["id"] = nullptr;
    c.error("bad_json", e ? e.c_str() : "request must be a JSON object");
    sendDoc(transport, c.resp);
    return;
  }
  c.req = req.as<JsonObjectConst>();
  c.resp["id"] = c.req["id"];
  if (c.req["id"].is<uint32_t>()) {
    c.hasUintId = true;
    c.id = c.req["id"].as<uint32_t>();
  }
  JsonVariantConst cmd = c.req["cmd"];
  if (!cmd.is<const char*>()) {
    c.error("bad_args", "missing \"cmd\"");
    sendDoc(transport, c.resp);
    return;
  }
  c.cmd = cmd.as<const char*>();
  lastCmdMs_ = hal_.millis();  // any command feeds the watchdog
  dispatch(c);
  sendDoc(transport, c.resp);
}

void Robot::dispatch(Ctx& c) {
  const char* cmd = c.cmd;
  JsonObjectConst a = c.req;
  char msg[128];

  auto isMotion = [&](const char* s) {
    return !strcmp(s, "joints") || !strcmp(s, "move") || !strcmp(s, "tap") || !strcmp(s, "swipe") ||
           !strcmp(s, "neck") || !strcmp(s, "home") || !strcmp(s, "park");
  };

  // Optional ms argument shared by the motion commands.
  float ms = 0.f;
  if (isMotion(cmd)) {
    const Arg r = getNum(a, "ms", ms);
    if (r == Arg::Bad || (r == Arg::Ok && (ms <= 0.f || ms > 60000.f))) {
      c.error("bad_args", "ms must be a number in (0, 60000]");
      return;
    }
    if (r == Arg::Missing) ms = 0.f;
  }

  // Enqueue helper with common checks.
  auto enqueue = [&](MotionRequest& r) -> bool {
    if (!enabled_) {
      c.error("not_enabled", "servos are relaxed; send enable first");
      return false;
    }
    r.id = c.id;
    r.notify = c.hasUintId;
    if (!engine_.enqueue(r)) {
      c.error("queue_full", "motion queue is full (16)");
      return false;
    }
    for (bool& o : rawOverride_) o = false;
    c.ok();
    c.resp["queued"] = true;
    return true;
  };

  // ---------------------------------------------------------------- hello
  if (!strcmp(cmd, "hello")) {
    c.ok();
    c.resp["fw"] = FW_NAME;
    c.resp["ver"] = FW_VERSION;
    c.resp["proto"] = PROTO_VERSION;
    c.resp["servos"] = NUM_JOINTS;
    c.resp["uptime_ms"] = hal_.millis();
    return;
  }
  // ---------------------------------------------------------------- ping
  if (!strcmp(cmd, "ping")) {
    c.ok();
    c.resp["t"] = hal_.millis();
    return;
  }
  // ---------------------------------------------------------------- enable
  if (!strcmp(cmd, "enable")) {
    if (enabled_) {
      c.ok();
      return;
    }
    if (!doEnable(msg, sizeof(msg))) {
      c.error("fault", msg);
      return;
    }
    c.ok();
    return;
  }
  // ---------------------------------------------------------------- relax
  if (!strcmp(cmd, "relax")) {
    doRelax();
    c.ok();
    return;
  }
  // ---------------------------------------------------------------- stop
  if (!strcmp(cmd, "stop")) {
    engine_.cancelAll("stop");
    wdTripping_ = false;
    for (bool& o : rawOverride_) o = false;
    writeOutputs(true);
    c.ok();
    return;
  }
  // ---------------------------------------------------------------- status
  if (!strcmp(cmd, "status")) {
    c.ok();
    fillStatus(c.resp.as<JsonObject>());
    return;
  }
  // ---------------------------------------------------------------- joints
  if (!strcmp(cmd, "joints")) {
    JsonObjectConst q = a["q"];
    if (q.isNull() || q.size() == 0) {
      c.error("bad_args", "q must be a non-empty object {NAME:deg}");
      return;
    }
    MotionRequest r;
    r.type = MotionType::Joints;
    r.ms = ms;
    for (JsonPairConst kv : q) {
      const int ji = jointIndex(kv.key().c_str());
      if (ji < 0) {
        snprintf(msg, sizeof(msg), "unknown joint \"%s\"", kv.key().c_str());
        c.error("bad_args", msg);
        return;
      }
      if (kv.value().is<bool>() || !kv.value().is<double>() || !isfinite(kv.value().as<double>())) {
        snprintf(msg, sizeof(msg), "joint %s must be a number", kv.key().c_str());
        c.error("bad_args", msg);
        return;
      }
      r.mask |= (uint8_t)(1u << ji);
      r.q[ji] = clampJoint(cal_.joints[ji], kv.value().as<float>());
    }
    // Linkage check on the resulting arm poses (unspecified joints keep their current target).
    for (int ar = 0; ar < NUM_ARMS; ++ar) {
      if (!(r.mask & armJointMask(ar))) continue;
      float v[3];
      for (int k = 0; k < 3; ++k) {
        const int ji = cfg::ARM_JOINTS[ar][k];
        v[k] = (r.mask & (1u << ji)) ? r.q[ji] : engine_.joint(ji);
      }
      ArmQ aq{v[0], v[1], v[2]};
      if (!armWithinLimits(cal_, ar, aq, msg, sizeof(msg))) {
        c.error("limit", msg);
        return;
      }
    }
    enqueue(r);
    return;
  }
  // ---------------------------------------------------------------- move
  if (!strcmp(cmd, "move")) {
    const int arm = a["arm"].is<const char*>() ? armIndex(a["arm"].as<const char*>()) : -1;
    float x, y, z;
    if (arm < 0) {
      c.error("bad_args", "arm must be \"L\" or \"R\"");
      return;
    }
    if (getNum(a, "x", x) != Arg::Ok || getNum(a, "y", y) != Arg::Ok || getNum(a, "z", z) != Arg::Ok) {
      c.error("bad_args", "x, y, z (mm) are required numbers");
      return;
    }
    ArmQ q;
    const IkStatus st = armIk(cal_, arm, Vec3{x, y, z}, q);
    if (st != IkStatus::Ok) {
      snprintf(msg, sizeof(msg), "arm %s cannot reach (%.1f,%.1f,%.1f)", armName(arm), x, y, z);
      c.error(ikStatusCode(st), msg);
      return;
    }
    MotionRequest r;
    r.type = MotionType::Move;
    r.arm = (int8_t)arm;
    r.x = x;
    r.y = y;
    r.z = z;
    r.ms = ms;
    enqueue(r);
    return;
  }
  // ---------------------------------------------------------------- tap / swipe
  if (!strcmp(cmd, "tap") || !strcmp(cmd, "swipe")) {
    const bool isTap = !strcmp(cmd, "tap");
    MotionRequest r;
    r.type = isTap ? MotionType::Tap : MotionType::Swipe;
    r.ms = ms;
    float z;
    bool okArgs;
    if (isTap) {
      okArgs = getNum(a, "x", r.x) == Arg::Ok && getNum(a, "y", r.y) == Arg::Ok && getNum(a, "z", z) == Arg::Ok;
      r.x1 = r.x;
      r.y1 = r.y;
    } else {
      okArgs = getNum(a, "x0", r.x) == Arg::Ok && getNum(a, "y0", r.y) == Arg::Ok &&
               getNum(a, "x1", r.x1) == Arg::Ok && getNum(a, "y1", r.y1) == Arg::Ok &&
               getNum(a, "z", z) == Arg::Ok;
    }
    if (!okArgs) {
      c.error("bad_args", isTap ? "x, y, z (mm) are required numbers" : "x0, y0, x1, y1, z (mm) are required numbers");
      return;
    }
    r.z = z;
    r.hold_ms = isTap ? cfg::TOOL_DEFAULT_HOLD_MS : 0.f;
    r.press_mm = cfg::TOOL_DEFAULT_PRESS_MM;
    r.approach_mm = cfg::TOOL_DEFAULT_APPROACH_MM;
    if (!isTap && r.ms <= 0.f) r.ms = 400.f;
    Arg ar;
    if (isTap && (ar = getNum(a, "hold_ms", r.hold_ms)) != Arg::Missing &&
        (ar == Arg::Bad || r.hold_ms < 0.f || r.hold_ms > 10000.f)) {
      c.error("bad_args", "hold_ms must be in [0, 10000]");
      return;
    }
    if ((ar = getNum(a, "press_mm", r.press_mm)) != Arg::Missing &&
        (ar == Arg::Bad || r.press_mm < 0.f || r.press_mm > 20.f)) {
      c.error("bad_args", "press_mm must be in [0, 20]");
      return;
    }
    if ((ar = getNum(a, "approach_mm", r.approach_mm)) != Arg::Missing &&
        (ar == Arg::Bad || r.approach_mm < 0.f || r.approach_mm > 100.f)) {
      c.error("bad_args", "approach_mm must be in [0, 100]");
      return;
    }
    // Arm selection.
    int pref;
    bool fallback = false;
    JsonVariantConst av = a["arm"];
    if (av.isNull() || (av.is<const char*>() && !strcmp(av.as<const char*>(), "auto"))) {
      pref = (isTap ? r.x : 0.5f * (r.x + r.x1)) < 0.f ? cfg::ARM_L : cfg::ARM_R;
      fallback = true;
    } else {
      pref = av.is<const char*>() ? armIndex(av.as<const char*>()) : -1;
      if (pref < 0) {
        c.error("bad_args", "arm must be \"L\", \"R\" or \"auto\"");
        return;
      }
    }
    const float zp = z - r.press_mm, za = z + r.approach_mm;
    auto check = [&](int arm) -> IkStatus {
      IkStatus s = engine_.checkLine(arm, Vec3{r.x, r.y, za}, Vec3{r.x, r.y, zp});
      if (s != IkStatus::Ok) return s;
      if (!isTap) {
        s = engine_.checkLine(arm, Vec3{r.x, r.y, zp}, Vec3{r.x1, r.y1, zp});
        if (s != IkStatus::Ok) return s;
        s = engine_.checkLine(arm, Vec3{r.x1, r.y1, zp}, Vec3{r.x1, r.y1, za});
      }
      return s;
    };
    IkStatus st = check(pref);
    int arm = pref;
    if (st != IkStatus::Ok && fallback) {
      if (check(1 - pref) == IkStatus::Ok) {
        arm = 1 - pref;
        st = IkStatus::Ok;
      }
    }
    if (st != IkStatus::Ok) {
      snprintf(msg, sizeof(msg), "%s target not reachable by arm %s%s", cmd, armName(pref),
               fallback ? " or the other arm" : "");
      c.error(ikStatusCode(st), msg);
      return;
    }
    r.arm = (int8_t)arm;
    if (enqueue(r)) c.resp["arm"] = armName(arm);
    return;
  }
  // ---------------------------------------------------------------- neck
  if (!strcmp(cmd, "neck")) {
    MotionRequest r;
    r.type = MotionType::Joints;
    r.ms = ms;
    float v;
    Arg ar = getNum(a, "pan", v);
    if (ar == Arg::Bad) {
      c.error("bad_args", "pan must be a number");
      return;
    }
    if (ar == Arg::Ok) {
      r.mask |= (uint8_t)(1u << cfg::J_NECK_PAN);
      r.q[cfg::J_NECK_PAN] = clampJoint(cal_.joints[cfg::J_NECK_PAN], v);
    }
    ar = getNum(a, "tilt", v);
    if (ar == Arg::Bad) {
      c.error("bad_args", "tilt must be a number");
      return;
    }
    if (ar == Arg::Ok) {
      r.mask |= (uint8_t)(1u << cfg::J_NECK_TILT);
      r.q[cfg::J_NECK_TILT] = clampJoint(cal_.joints[cfg::J_NECK_TILT], v);
    }
    if (!r.mask) {
      c.error("bad_args", "neck needs pan and/or tilt");
      return;
    }
    enqueue(r);
    return;
  }
  // ---------------------------------------------------------------- home
  if (!strcmp(cmd, "home")) {
    MotionRequest r;
    r.type = MotionType::Joints;
    r.ms = ms;
    r.mask = 0xFF;
    for (int i = 0; i < NUM_JOINTS; ++i) r.q[i] = cal_.joints[i].home;
    enqueue(r);
    return;
  }
  // ---------------------------------------------------------------- park
  if (!strcmp(cmd, "park")) {
    MotionRequest r;
    r.type = MotionType::Park;
    r.ms = ms;
    JsonVariantConst av = a["arm"];
    if (av.isNull() || (av.is<const char*>() && !strcmp(av.as<const char*>(), "both"))) {
      r.mask = 3;
    } else {
      const int arm = av.is<const char*>() ? armIndex(av.as<const char*>()) : -1;
      if (arm < 0) {
        c.error("bad_args", "arm must be \"L\", \"R\" or \"both\"");
        return;
      }
      r.mask = (uint8_t)(1u << arm);
    }
    enqueue(r);
    return;
  }
  // ---------------------------------------------------------------- servo_raw
  if (!strcmp(cmd, "servo_raw")) {
    JsonVariantConst chv = a["ch"];
    float us;
    if (!chv.is<int>() || chv.as<int>() < 0 || chv.as<int>() >= NUM_PWM_CHANNELS) {
      c.error("bad_args", "ch must be an integer 0..15");
      return;
    }
    if (getNum(a, "us", us) != Arg::Ok || us < SERVO_RAW_US_MIN || us > SERVO_RAW_US_MAX) {
      snprintf(msg, sizeof(msg), "us must be a number in [%.0f, %.0f]", (double)SERVO_RAW_US_MIN,
               (double)SERVO_RAW_US_MAX);
      c.error("bad_args", msg);
      return;
    }
    if (!enabled_) {
      c.error("not_enabled", "servo_raw requires enable");
      return;
    }
    const int ch = chv.as<int>();
    rawOverride_[ch] = true;
    hal_.writePulseUs(ch, us);
    lastUs_[ch] = us;
    // Keep the commanded joint state consistent so the next motion starts from here.
    for (int i = 0; i < NUM_JOINTS; ++i)
      if (cal_.joints[i].ch == ch) engine_.setJoint(i, servoDegToJoint(cal_.joints[i], usToServoDeg(us)));
    c.ok();
    return;
  }
  // ---------------------------------------------------------------- calib_get
  if (!strcmp(cmd, "calib_get")) {
    c.ok();
    JsonObject jo = c.resp["joints"].to<JsonObject>();
    for (int i = 0; i < NUM_JOINTS; ++i) {
      const JointCal& j = cal_.joints[i];
      JsonObject o = jo[jointName(i)].to<JsonObject>();
      o["ch"] = j.ch;
      o["gear"] = r3(j.gear);
      o["dir"] = j.dir;
      o["s_zero"] = r3(j.s_zero);
      o["neutral"] = r3(j.neutral);
      o["min"] = r3(j.min);
      o["max"] = r3(j.max);
      o["home"] = r3(j.home);
      o["park"] = r3(j.park);
      o["max_vel"] = r3(j.max_vel);
    }
    return;
  }
  // ---------------------------------------------------------------- calib_set
  if (!strcmp(cmd, "calib_set")) {
    JsonObjectConst jo = a["joints"];
    if (jo.isNull()) {
      c.error("bad_args", "joints must be an object {NAME:{field:value}}");
      return;
    }
    Calibration nc = cal_;
    for (JsonPairConst kv : jo) {
      const int ji = jointIndex(kv.key().c_str());
      JsonObjectConst fields = kv.value();
      if (ji < 0 || fields.isNull()) {
        snprintf(msg, sizeof(msg), "unknown joint \"%s\" or value not an object", kv.key().c_str());
        c.error("bad_args", msg);
        return;
      }
      JointCal& j = nc.joints[ji];
      for (JsonPairConst f : fields) {
        const char* k = f.key().c_str();
        JsonVariantConst v = f.value();
        if (v.is<bool>() || !v.is<double>() || !isfinite(v.as<double>())) {
          snprintf(msg, sizeof(msg), "%s.%s must be a number", kv.key().c_str(), k);
          c.error("bad_args", msg);
          return;
        }
        const double d = v.as<double>();
        if (!strcmp(k, "ch")) {
          if (!v.is<int>()) {
            c.error("bad_args", "ch must be an integer");
            return;
          }
          j.ch = (int8_t)(d < -1 ? -1 : (d > 99 ? 99 : d));
        } else if (!strcmp(k, "dir")) {
          if (!v.is<int>()) {
            c.error("bad_args", "dir must be 1 or -1");
            return;
          }
          j.dir = (int8_t)(d < -9 ? -9 : (d > 9 ? 9 : d));
        } else if (!strcmp(k, "gear")) j.gear = (float)d;
        else if (!strcmp(k, "s_zero")) j.s_zero = (float)d;
        else if (!strcmp(k, "neutral")) j.neutral = (float)d;
        else if (!strcmp(k, "min")) j.min = (float)d;
        else if (!strcmp(k, "max")) j.max = (float)d;
        else if (!strcmp(k, "home")) j.home = (float)d;
        else if (!strcmp(k, "park")) j.park = (float)d;
        else if (!strcmp(k, "max_vel")) j.max_vel = (float)d;
        else {
          snprintf(msg, sizeof(msg), "unknown field \"%s\"", k);
          c.error("bad_args", msg);
          return;
        }
      }
    }
    if (!nc.validate(msg, sizeof(msg))) {
      c.error("bad_args", msg);
      return;
    }
    const bool changed = memcmp(&nc.joints, &cal_.joints, sizeof(nc.joints)) != 0;
    cal_ = nc;
    if (changed) {
      for (float& u : lastUs_) u = -1.f;
      writeOutputs(true);
    }
    const bool save = a["save"].is<bool>() && a["save"].as<bool>();
    if (save && !hal_.saveCalib(cal_)) {
      c.error("fault", "NVS write failed");
      return;
    }
    c.ok();
    c.resp["saved"] = save;
    return;
  }
  // ---------------------------------------------------------------- geom_get
  if (!strcmp(cmd, "geom_get")) {
    c.ok();
    JsonObject arms = c.resp["arms"].to<JsonObject>();
    for (int ar = 0; ar < NUM_ARMS; ++ar) {
      JsonObject o = arms[armName(ar)].to<JsonObject>();
      for (int k = 0; k < 8; ++k) o[kArmFields[k]] = r3(armFieldGet(cal_.arms[ar], k));
    }
    JsonObject lk = c.resp["linkage"].to<JsonObject>();
    lk["rel_min"] = r3(cal_.linkage.rel_min);
    lk["rel_max"] = r3(cal_.linkage.rel_max);
    return;
  }
  // ---------------------------------------------------------------- geom_set
  if (!strcmp(cmd, "geom_set")) {
    Calibration nc = cal_;
    JsonVariantConst armsV = a["arms"];
    JsonVariantConst lkV = a["linkage"];
    if (!armsV.isNull()) {
      JsonObjectConst arms = armsV;
      if (arms.isNull()) {
        c.error("bad_args", "arms must be an object");
        return;
      }
      for (JsonPairConst kv : arms) {
        const int ar = armIndex(kv.key().c_str());
        JsonObjectConst fields = kv.value();
        if (ar < 0 || fields.isNull()) {
          snprintf(msg, sizeof(msg), "unknown arm \"%s\" or value not an object", kv.key().c_str());
          c.error("bad_args", msg);
          return;
        }
        for (JsonPairConst f : fields) {
          float* dst = armField(nc.arms[ar], f.key().c_str());
          JsonVariantConst v = f.value();
          if (!dst || v.is<bool>() || !v.is<double>()) {
            snprintf(msg, sizeof(msg), "bad field arms.%s.%s", kv.key().c_str(), f.key().c_str());
            c.error("bad_args", msg);
            return;
          }
          *dst = v.as<float>();
        }
      }
    }
    if (!lkV.isNull()) {
      JsonObjectConst lk = lkV;
      if (lk.isNull()) {
        c.error("bad_args", "linkage must be an object");
        return;
      }
      for (JsonPairConst f : lk) {
        JsonVariantConst v = f.value();
        if (v.is<bool>() || !v.is<double>()) {
          c.error("bad_args", "linkage values must be numbers");
          return;
        }
        if (!strcmp(f.key().c_str(), "rel_min")) nc.linkage.rel_min = v.as<float>();
        else if (!strcmp(f.key().c_str(), "rel_max")) nc.linkage.rel_max = v.as<float>();
        else {
          snprintf(msg, sizeof(msg), "unknown field linkage.%s", f.key().c_str());
          c.error("bad_args", msg);
          return;
        }
      }
    }
    if (!nc.validate(msg, sizeof(msg))) {
      c.error("bad_args", msg);
      return;
    }
    cal_ = nc;
    const bool save = a["save"].is<bool>() && a["save"].as<bool>();
    if (save && !hal_.saveCalib(cal_)) {
      c.error("fault", "NVS write failed");
      return;
    }
    c.ok();
    c.resp["saved"] = save;
    return;
  }
  // ---------------------------------------------------------------- reset_calib
  if (!strcmp(cmd, "reset_calib")) {
    cal_ = Calibration::defaults();
    hal_.eraseCalib();
    for (float& u : lastUs_) u = -1.f;
    writeOutputs(true);
    c.ok();
    return;
  }
  // ---------------------------------------------------------------- fk
  if (!strcmp(cmd, "fk")) {
    const int arm = a["arm"].is<const char*>() ? armIndex(a["arm"].as<const char*>()) : -1;
    if (arm < 0) {
      c.error("bad_args", "arm must be \"L\" or \"R\"");
      return;
    }
    const Vec3 p = engine_.toolPos(arm);
    c.ok();
    c.resp["x"] = r3(p.x);
    c.resp["y"] = r3(p.y);
    c.resp["z"] = r3(p.z);
    return;
  }
  // ---------------------------------------------------------------- ik
  if (!strcmp(cmd, "ik")) {
    const int arm = a["arm"].is<const char*>() ? armIndex(a["arm"].as<const char*>()) : -1;
    float x, y, z;
    if (arm < 0) {
      c.error("bad_args", "arm must be \"L\" or \"R\"");
      return;
    }
    if (getNum(a, "x", x) != Arg::Ok || getNum(a, "y", y) != Arg::Ok || getNum(a, "z", z) != Arg::Ok) {
      c.error("bad_args", "x, y, z (mm) are required numbers");
      return;
    }
    ArmQ q;
    const IkStatus st = armIk(cal_, arm, Vec3{x, y, z}, q);
    if (st == IkStatus::Unreachable) {
      snprintf(msg, sizeof(msg), "(%.1f,%.1f,%.1f) is outside arm %s workspace", x, y, z, armName(arm));
      c.error("unreachable", msg);
      return;
    }
    if (st == IkStatus::Limit) {
      armWithinLimits(cal_, arm, q, msg, sizeof(msg));
      c.error("limit", msg);
    } else {
      c.ok();
    }
    JsonObject qo = c.resp["q"].to<JsonObject>();
    qo["YAW"] = r3(q.yaw);
    qo["SHOULDER"] = r3(q.th1);
    qo["ELBOW"] = r3(q.th2);
    return;
  }
  // ---------------------------------------------------------------- watchdog
  if (!strcmp(cmd, "watchdog")) {
    float v;
    if (getNum(a, "ms", v) != Arg::Ok || v < 0.f || v > 3600000.f) {
      c.error("bad_args", "ms must be a number in [0, 3600000] (0 = off)");
      return;
    }
    wdMs_ = (uint32_t)v;
    c.ok();
    c.resp["ms"] = wdMs_;
    return;
  }
  // ---------------------------------------------------------------- stream
  if (!strcmp(cmd, "stream")) {
    float v;
    if (getNum(a, "hz", v) != Arg::Ok || v < 0.f || v > (float)STREAM_MAX_HZ) {
      c.error("bad_args", "hz must be 0..20 (0 = off)");
      return;
    }
    int hz = (int)lroundf(v);
    if (v > 0.f && hz == 0) hz = 1;
    streamHz_ = hz;
    lastStream_ = hal_.millis();
    c.ok();
    c.resp["hz"] = hz;
    return;
  }
  // ---------------------------------------------------------------- phone_power
  if (!strcmp(cmd, "phone_power")) {
    if (!a["on"].is<bool>()) {
      c.error("bad_args", "on must be a boolean");
      return;
    }
    const bool on = a["on"].as<bool>();
    hal_.setPhonePower(on);
    c.ok();
    c.resp["on"] = on;
    return;
  }

  snprintf(msg, sizeof(msg), "unknown command \"%s\"", cmd);
  c.error("unknown_cmd", msg);
}

}  // namespace tappet
