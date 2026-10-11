// Splits a byte stream into '\n'-terminated lines (CR stripped). Over-long lines
// are discarded up to the next newline and reported once as Overflow.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace tappet {

constexpr size_t MAX_LINE_LEN = 2048;  // SPEC section 4: one object per line, <= 2048 bytes

class LineAssembler {
 public:
  enum class Result : uint8_t { None, Line, Overflow };

  Result feed(char c) {
    if (c == '\n') {
      if (overflow_) {
        overflow_ = false;
        len_ = 0;
        return Result::Overflow;
      }
      buf_[len_] = '\0';
      lineLen_ = len_;
      len_ = 0;
      return lineLen_ > 0 ? Result::Line : Result::None;
    }
    if (c == '\r' || overflow_) return Result::None;
    if (len_ >= MAX_LINE_LEN) {
      overflow_ = true;
      return Result::None;
    }
    buf_[len_++] = c;
    return Result::None;
  }

  const char* line() const { return buf_; }
  size_t lineLen() const { return lineLen_; }
  void reset() {
    len_ = 0;
    lineLen_ = 0;
    overflow_ = false;
  }

 private:
  char buf_[MAX_LINE_LEN + 1] = {};
  size_t len_ = 0;
  size_t lineLen_ = 0;
  bool overflow_ = false;
};

}  // namespace tappet
