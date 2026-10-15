#include "hub.h"

#include <math.h>
#include <string.h>

#include "servo_math.h"

namespace hub {

// Bench defaults (hardware/electronics/README.md): ch0 is the syringe's FS90R continuous-rotation servo, the rest
// are positional 9 g servos (SG90 / MG90S) with the common 500..2500 us = 0..180 deg mapping. `cfg` overrides
// any of this and persists it.
ChannelCfg defaultCfg(int ch) {
  if (ch == 0) return ChannelCfg{700, 2300, 1500, Kind::Cont};
  return ChannelCfg{500, 2500, 0, Kind::Pos};
}

namespace {
const char* kindName(Kind k) { return k == Kind::Cont ? "cont" : "pos"; }

void head(JsonDocument& d, int32_t id, bool hasId) {
  if (hasId) d["id"] = id;
  d["ok"] = true;
}

bool getCh(JsonObjectConst req, int& ch) {
  JsonVariantConst v = req["ch"];
  if (!v.is<int>()) return false;
  ch = v.as<int>();
  return ch >= 0 && ch < NUM_CH;
}

// Optional non-negative duration; false if present but invalid.
bool getMs(JsonObjectConst req, uint32_t& ms) {
  JsonVariantConst v = req["ms"];
  ms = 0;
  if (v.isNull()) return true;
  if (!v.is<float>() && !v.is<int>()) return false;
  float f = v.as<float>();
  if (!(f >= 0.f) || f > (float)MAX_MS) return false;
  ms = (uint32_t)lroundf(f);
  return true;
}
}  // namespace

Hub::Hub(Hal& hal, LineSink& sink, const BoardDef& board) : hal_(hal), sink_(sink), board_(board) {
  for (int ch = 0; ch < NUM_CH; ch++) cfg_[ch] = defaultCfg(ch);
}

void Hub::begin() {
  boot_ms_ = hal_.millis();
  for (int ch = 0; ch < NUM_CH; ch++) {
    ChannelCfg c;
    if (hal_.loadCfg(ch, c) && c.min_us >= ABS_MIN_US && c.max_us <= ABS_MAX_US && c.min_us < c.max_us &&
        (c.rest_us == 0 || (c.rest_us >= c.min_us && c.rest_us <= c.max_us)))
      cfg_[ch] = c;
    mot_[ch] = Motion{};
    out_us_[ch] = 0;
  }
  hal_.pwmAllOff();
  // Inputs start at their current level without events; an open estop holds the outputs off from the start.
  int n = board_.n_inputs < MAX_INPUTS ? board_.n_inputs : MAX_INPUTS;
  for (int i = 0; i < n; i++) {
    bool closed = hal_.inputClosed(i);
    uint8_t v = board_.inputs[i].estop ? !closed : closed;
    in_[i].stable = in_[i].raw = v;
    in_[i].since = boot_ms_;
    if (board_.inputs[i].estop) {
      estop_idx_ = i;
      estop_ = v;
    }
  }
  hal_.setOutputsEnabled(!estop_);
  JsonDocument ev;
  ev["ev"] = "boot";
  ev["fw"] = HUB_FW_VERSION;
  emit(T_ALL, ev);
  if (estop_) {
    JsonDocument e;
    e["ev"] = "estop";
    e["v"] = 1;
    emit(T_ALL, e);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// input side

void Hub::feed(int t, const char* data, size_t n) {
  if (t < 0 || t >= T_COUNT) return;
  for (size_t i = 0; i < n; i++) {
    switch (asm_[t].feed(data[i])) {
      case LineAssembler::Result::Line:
        handleLine(t, asm_[t].line(), asm_[t].lineLen());
        break;
      case LineAssembler::Result::Overflow:
        last_line_ = hal_.millis();
        wd_armed_ = true;
        wd_tripped_ = false;
        sendErr(t, 0, false, "too_long");
        break;
      default:
        break;
    }
  }
}

void Hub::handleLine(int t, const char* line, size_t len) {
  last_line_ = hal_.millis();  // any line feeds the watchdog
  wd_armed_ = true;
  wd_tripped_ = false;

  JsonDocument doc;
  if (deserializeJson(doc, line, len) || !doc.is<JsonObject>()) {
    sendErr(t, 0, false, "json");
    return;
  }
  JsonObjectConst req = doc.as<JsonObjectConst>();
  bool hasId = req["id"].is<int32_t>();
  int32_t id = hasId ? req["id"].as<int32_t>() : 0;
  const char* cmd = req["cmd"] | "";

  if (!strcmp(cmd, "ping")) {
    if (hasId) sendOkEmpty(t, id, hasId);  // an id-less ping is a pure keepalive: no reply
  } else if (!strcmp(cmd, "hello")) {
    cmdHello(t, id, hasId);
  } else if (!strcmp(cmd, "servo")) {
    cmdServo(t, id, hasId, req);
  } else if (!strcmp(cmd, "pulse")) {
    cmdPulse(t, id, hasId, req);
  } else if (!strcmp(cmd, "off")) {
    cmdOff(t, id, hasId, req);
  } else if (!strcmp(cmd, "stop")) {
    cmdStop(t, id, hasId);
  } else if (!strcmp(cmd, "cfg")) {
    cmdCfg(t, id, hasId, req);
  } else if (!strcmp(cmd, "read")) {
    cmdRead(t, id, hasId);
  } else {
    sendErr(t, id, hasId, "unknown_cmd");
  }
}

// ---------------------------------------------------------------------------------------------------------------
// commands

void Hub::cmdHello(int t, int32_t id, bool hasId) {
  JsonDocument d;
  head(d, id, hasId);
  d["fw"] = HUB_FW_VERSION;
  d["board"] = board_.name;
  d["channels"] = NUM_CH;
  JsonArray in = d["inputs"].to<JsonArray>();
  for (int i = 0; i < board_.n_inputs && i < MAX_INPUTS; i++) in.add(board_.inputs[i].name);
  JsonArray adc = d["adc"].to<JsonArray>();
  for (int i = 0; i < board_.n_adcs; i++) adc.add(board_.adcs[i].name);
  d["uptime_ms"] = (uint32_t)(hal_.millis() - boot_ms_);
  d["pwm"] = hal_.pwmOk();
  d["estop"] = estop_ ? 1 : 0;
  emit(t, d);
}

void Hub::cmdServo(int t, int32_t id, bool hasId, JsonObjectConst req) {
  int ch;
  uint32_t ms;
  if (!getCh(req, ch)) return sendErr(t, id, hasId, "ch");
  JsonVariantConst dv = req["deg"];
  if (!dv.is<float>() && !dv.is<int>()) return sendErr(t, id, hasId, "args");
  float deg = dv.as<float>();
  if (!(deg >= 0.f && deg <= 180.f) || !getMs(req, ms)) return sendErr(t, id, hasId, "range");
  if (cfg_[ch].kind == Kind::Cont) return sendErr(t, id, hasId, "kind");  // continuous: use pulse
  if (estop_) return sendErr(t, id, hasId, "estop");
  if (!hal_.pwmOk()) return sendErr(t, id, hasId, "pwm");

  cancel(ch, "preempted");
  const ChannelCfg& c = cfg_[ch];
  float target = degToUs(deg, c.min_us, c.max_us);
  if (ms == 0) {
    setOut(ch, (uint16_t)lroundf(target));
    JsonDocument d;
    head(d, id, hasId);
    d["deg"] = deg;
    return emit(t, d);
  }
  // Ease from where the servo is (or, if relaxed, where it was last driven); a never-driven servo jumps.
  uint16_t from = out_us_[ch] ? out_us_[ch] : (last_us_[ch] ? last_us_[ch] : (uint16_t)lroundf(target));
  Motion& m = mot_[ch];
  m = Motion{};
  m.mode = Mode::Ease;
  m.t0 = hal_.millis();
  m.dur = ms;
  m.from_us = from;
  m.to_us = target;
  m.deg = deg;
  m.has_reply = true;
  m.transport = (int8_t)t;
  m.id = id;
  m.has_id = hasId;
  setOut(ch, from);
}

void Hub::cmdPulse(int t, int32_t id, bool hasId, JsonObjectConst req) {
  int ch;
  uint32_t ms;
  if (!getCh(req, ch)) return sendErr(t, id, hasId, "ch");
  JsonVariantConst uv = req["us"];
  if (!uv.is<float>() && !uv.is<int>()) return sendErr(t, id, hasId, "args");
  float usf = uv.as<float>();
  const ChannelCfg& c = cfg_[ch];
  uint16_t us = (uint16_t)lroundf(usf);
  if (!(usf == 0.f || (usf >= c.min_us && usf <= c.max_us)) || !getMs(req, ms)) return sendErr(t, id, hasId, "range");
  int until = -1;
  JsonVariantConst uu = req["until"];
  if (!uu.isNull()) {
    if (!uu.is<const char*>()) return sendErr(t, id, hasId, "args");
    until = inputIndex(uu.as<const char*>());
    if (until < 0) return sendErr(t, id, hasId, "input");
    if (ms == 0) return sendErr(t, id, hasId, "until_needs_ms");  // never an unbounded drive
  }
  if (estop_ && us != 0) return sendErr(t, id, hasId, "estop");
  if (!hal_.pwmOk()) return sendErr(t, id, hasId, "pwm");

  cancel(ch, "preempted");
  if (ms == 0) {
    setOut(ch, us);
    JsonDocument d;
    head(d, id, hasId);
    d["us"] = us;
    return emit(t, d);
  }
  Motion& m = mot_[ch];
  m = Motion{};
  m.mode = Mode::Hold;
  m.t0 = hal_.millis();
  m.dur = ms;
  m.hold_us = us;
  m.until = (int8_t)until;
  m.has_reply = true;
  m.transport = (int8_t)t;
  m.id = id;
  m.has_id = hasId;
  if (until >= 0 && value(until)) {  // already at the stop: do not drive into it at all
    finishHold(ch, m.t0, board_.inputs[until].name);
    return;
  }
  setOut(ch, us);
}

void Hub::cmdOff(int t, int32_t id, bool hasId, JsonObjectConst req) {
  if (!req["ch"].isNull()) {
    int ch;
    if (!getCh(req, ch)) return sendErr(t, id, hasId, "ch");
    cancel(ch, "preempted");
    setOut(ch, 0);
  } else {
    for (int ch = 0; ch < NUM_CH; ch++) {
      cancel(ch, "stopped");
      out_us_[ch] = 0;
    }
    hal_.pwmAllOff();
  }
  sendOkEmpty(t, id, hasId);
}

void Hub::cmdStop(int t, int32_t id, bool hasId) {
  relaxAll("stopped", true);
  sendOkEmpty(t, id, hasId);
}

void Hub::cmdCfg(int t, int32_t id, bool hasId, JsonObjectConst req) {
  int ch;
  if (!getCh(req, ch)) return sendErr(t, id, hasId, "ch");
  ChannelCfg c = cfg_[ch];
  bool reset = req["reset"] | false;
  if (reset) c = defaultCfg(ch);
  bool any = reset;
  auto num = [&](const char* key, uint16_t& dst, bool allowZero) -> bool {
    JsonVariantConst v = req[key];
    if (v.isNull()) return true;
    if (!v.is<int>() && !v.is<float>()) return false;
    float f = v.as<float>();
    if (!(allowZero && f == 0.f) && !(f >= ABS_MIN_US && f <= ABS_MAX_US)) return false;
    dst = (uint16_t)lroundf(f);
    any = true;
    return true;
  };
  if (!num("min_us", c.min_us, false) || !num("max_us", c.max_us, false) || !num("rest_us", c.rest_us, true))
    return sendErr(t, id, hasId, "range");
  JsonVariantConst kv = req["kind"];
  if (!kv.isNull()) {
    const char* k = kv | "";
    if (!strcmp(k, "pos")) c.kind = Kind::Pos;
    else if (!strcmp(k, "cont")) c.kind = Kind::Cont;
    else return sendErr(t, id, hasId, "kind");
    any = true;
  }
  if (c.min_us >= c.max_us || (c.rest_us != 0 && (c.rest_us < c.min_us || c.rest_us > c.max_us)))
    return sendErr(t, id, hasId, "range");
  const ChannelCfg& old = cfg_[ch];
  bool same = c.min_us == old.min_us && c.max_us == old.max_us && c.rest_us == old.rest_us && c.kind == old.kind;
  if (any && !same) {  // identical cfg (the DO resends it after every restart): no flash write, no preemption
    cancel(ch, "preempted");
    cfg_[ch] = c;
    if (out_us_[ch] && (out_us_[ch] < c.min_us || out_us_[ch] > c.max_us)) setOut(ch, 0);
    if (reset) hal_.eraseCfg(ch);
    else if (!hal_.saveCfg(ch, c)) return sendErr(t, id, hasId, "nvs");
  } else if (reset) {
    hal_.eraseCfg(ch);
  }
  JsonDocument d;
  head(d, id, hasId);
  JsonObject o = d["cfg"].to<JsonObject>();
  o["ch"] = ch;
  o["min_us"] = c.min_us;
  o["max_us"] = c.max_us;
  o["rest_us"] = c.rest_us;
  o["kind"] = kindName(c.kind);
  emit(t, d);
}

void Hub::cmdRead(int t, int32_t id, bool hasId) {
  JsonDocument d;
  head(d, id, hasId);
  JsonObject in = d["inputs"].to<JsonObject>();
  for (int i = 0; i < board_.n_inputs && i < MAX_INPUTS; i++) in[board_.inputs[i].name] = value(i);
  JsonObject adc = d["adc"].to<JsonObject>();
  for (int i = 0; i < board_.n_adcs; i++) adc[board_.adcs[i].name] = hal_.adcMv(i);
  JsonArray chs = d["ch"].to<JsonArray>();
  for (int ch = 0; ch < NUM_CH; ch++) chs.add(out_us_[ch]);
  emit(t, d);
}

// ---------------------------------------------------------------------------------------------------------------
// motion

void Hub::setOut(int ch, uint16_t us) {
  if (out_us_[ch] != us) hal_.pwmWrite(ch, us);
  out_us_[ch] = us;
  if (us) last_us_[ch] = us;
}

void Hub::cancel(int ch, const char* why) {
  Motion& m = mot_[ch];
  if (m.mode != Mode::Idle && m.has_reply) sendErr(m.transport, m.id, m.has_id, why);
  m = Motion{};
}

void Hub::relaxAll(const char* why, bool settle) {
  uint32_t now = hal_.millis();
  for (int ch = 0; ch < NUM_CH; ch++) {
    cancel(ch, why);
    const ChannelCfg& c = cfg_[ch];
    if (settle && c.kind == Kind::Cont && c.rest_us && out_us_[ch]) {
      // continuous servo: command neutral briefly so it stops cleanly, then off
      setOut(ch, c.rest_us);
      mot_[ch].mode = Mode::Settle;
      mot_[ch].t0 = now;
      mot_[ch].dur = SETTLE_MS;
    } else {
      setOut(ch, 0);
    }
  }
}

void Hub::finishEase(int ch, uint32_t now) {
  Motion m = mot_[ch];
  mot_[ch] = Motion{};
  setOut(ch, (uint16_t)lroundf(m.to_us));
  JsonDocument d;
  head(d, m.id, m.has_id);
  d["deg"] = m.deg;
  emit(m.transport, d);
}

void Hub::finishHold(int ch, uint32_t now, const char* stoppedBy) {
  Motion m = mot_[ch];
  mot_[ch] = Motion{};
  setOut(ch, cfg_[ch].rest_us);
  JsonDocument d;
  head(d, m.id, m.has_id);
  d["us"] = m.hold_us;
  d["ms"] = (uint32_t)(now - m.t0);
  if (stoppedBy) d["stopped_by"] = stoppedBy;
  else d["stopped_by"] = nullptr;
  emit(m.transport, d);
}

void Hub::tickMotions(uint32_t now, bool easeTick) {
  for (int ch = 0; ch < NUM_CH; ch++) {
    Motion& m = mot_[ch];
    uint32_t el = now - m.t0;
    switch (m.mode) {
      case Mode::Hold:
        if (m.until >= 0 && value(m.until)) finishHold(ch, now, board_.inputs[m.until].name);
        else if (el >= m.dur) finishHold(ch, now, nullptr);
        break;
      case Mode::Ease:
        if (el >= m.dur) finishEase(ch, now);
        else if (easeTick) setOut(ch, (uint16_t)lroundf(m.from_us + (m.to_us - m.from_us) * ease((float)el / m.dur)));
        break;
      case Mode::Settle:
        if (el >= m.dur) {
          m = Motion{};
          setOut(ch, 0);
        }
        break;
      default:
        break;
    }
  }
}

int Hub::inputIndex(const char* name) const {
  for (int i = 0; i < board_.n_inputs && i < MAX_INPUTS; i++)
    if (!strcmp(board_.inputs[i].name, name)) return i;
  return -1;
}

int Hub::value(int idx) const { return in_[idx].stable; }

void Hub::pollInputs(uint32_t now) {
  int n = board_.n_inputs < MAX_INPUTS ? board_.n_inputs : MAX_INPUTS;
  for (int i = 0; i < n; i++) {
    const InputDef& def = board_.inputs[i];
    bool closed = hal_.inputClosed(i);
    uint8_t raw = def.estop ? !closed : closed;
    InputState& s = in_[i];
    if (raw != s.raw) {
      s.raw = raw;
      s.since = now;
    }
    if (raw == s.stable || (uint32_t)(now - s.since) < def.debounce_ms) continue;
    s.stable = raw;
    JsonDocument ev;
    if (def.estop) {
      estop_ = raw;
      if (estop_) {  // hard stop first, report second
        relaxAll("estop", false);
        hal_.pwmAllOff();
        hal_.setOutputsEnabled(false);
      } else {
        hal_.setOutputsEnabled(true);
      }
      ev["ev"] = "estop";
      ev["v"] = raw;
    } else {
      ev["ev"] = "in";
      ev["name"] = def.name;
      ev["v"] = raw;
    }
    emit(T_ALL, ev);
  }
}

void Hub::loop() {
  uint32_t now = hal_.millis();
  pollInputs(now);
  bool easeTick = (uint32_t)(now - last_ease_) >= EASE_TICK_MS;
  if (easeTick) last_ease_ = now;
  tickMotions(now, easeTick);
  if (wd_armed_ && !wd_tripped_ && (uint32_t)(now - last_line_) >= WATCHDOG_MS) {
    wd_tripped_ = true;
    relaxAll("watchdog", true);
    JsonDocument ev;
    ev["ev"] = "wd";
    emit(T_ALL, ev);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// output

void Hub::sendErr(int t, int32_t id, bool hasId, const char* err) {
  JsonDocument d;
  if (hasId) d["id"] = id;
  d["ok"] = false;
  d["err"] = err;
  emit(t, d);
}

void Hub::sendOkEmpty(int t, int32_t id, bool hasId) {
  JsonDocument d;
  head(d, id, hasId);
  emit(t, d);
}

void Hub::emit(int t, const JsonDocument& doc) {
  char buf[MAX_LINE + 64];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf) - 1) return;
  sink_.sendLine(t, buf, n);
}

}  // namespace hub
