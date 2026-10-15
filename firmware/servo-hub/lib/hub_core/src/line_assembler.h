// Splits a byte stream into '\n'-terminated lines (CR stripped). An over-long line is discarded up to
// the next newline and reported once as Overflow. Adapted from tappet_core/line_assembler.h.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace hub {

constexpr size_t MAX_LINE = 256;  // protocol.md section 4: at most 256 bytes including the '\n'

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
    if (len_ >= MAX_LINE - 1) {
      overflow_ = true;
      return Result::None;
    }
    buf_[len_++] = c;
    return Result::None;
  }

  const char* line() const { return buf_; }
  size_t lineLen() const { return lineLen_; }
  void reset() { len_ = lineLen_ = 0; overflow_ = false; }

 private:
  char buf_[MAX_LINE] = {};
  size_t len_ = 0;
  size_t lineLen_ = 0;
  bool overflow_ = false;
};

}  // namespace hub
