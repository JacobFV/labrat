// Debounced push-button with short / long press detection. Long press fires as
// soon as the hold time is exceeded (no need to release); the following release
// is swallowed.
#pragma once

#include <stdint.h>

namespace tappet {

enum class ButtonAction : uint8_t { None, Short, Long };

class ButtonDetector {
 public:
  static constexpr uint32_t DEBOUNCE_MS = 30;
  static constexpr uint32_t LONG_MS = 1500;

  ButtonAction update(bool pressed, uint32_t now) {
    if (pressed != raw_) {
      raw_ = pressed;
      rawSince_ = now;
    }
    if (raw_ != stable_ && (uint32_t)(now - rawSince_) >= DEBOUNCE_MS) {
      stable_ = raw_;
      if (stable_) {
        pressStart_ = now;
        longFired_ = false;
      } else {
        if (!longFired_) return ButtonAction::Short;
      }
    }
    if (stable_ && !longFired_ && (uint32_t)(now - pressStart_) > LONG_MS) {
      longFired_ = true;
      return ButtonAction::Long;
    }
    return ButtonAction::None;
  }

 private:
  bool raw_ = false;
  bool stable_ = false;
  bool longFired_ = false;
  uint32_t rawSince_ = 0;
  uint32_t pressStart_ = 0;
};

inline const char* buttonActionName(ButtonAction a) {
  return a == ButtonAction::Short ? "short" : (a == ButtonAction::Long ? "long" : "none");
}

}  // namespace tappet
