// PCA9685 arithmetic, degree mapping, easing and the line assembler.
#include <unity.h>

#include <string.h>

#include "line_assembler.h"
#include "servo_math.h"

using namespace hub;

void setUp() {}
void tearDown() {}

void test_prescale_50hz() {
  int p = pcaPrescale(25e6f, 50.f);
  TEST_ASSERT_EQUAL_INT(121, p);  // round(25e6 / (4096 * 50)) - 1 = round(122.07) - 1
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 50.03f, pcaActualHz(25e6f, p));
  TEST_ASSERT_EQUAL_INT(3, pcaPrescale(25e6f, 5000.f));    // clamped to the chip minimum
  TEST_ASSERT_EQUAL_INT(255, pcaPrescale(25e6f, 10.f));    // and maximum
}

void test_ticks() {
  float hz = pcaActualHz(25e6f, pcaPrescale(25e6f, 50.f));
  TEST_ASSERT_EQUAL_UINT16(307, pcaTicks(1500.f, hz));  // 1500 us * 50.03 Hz * 4096 / 1e6 = 307.4
  TEST_ASSERT_EQUAL_UINT16(102, pcaTicks(500.f, hz));
  TEST_ASSERT_EQUAL_UINT16(512, pcaTicks(2500.f, hz));
  TEST_ASSERT_EQUAL_UINT16(0, pcaTicks(-5.f, hz));
  TEST_ASSERT_EQUAL_UINT16(4095, pcaTicks(1e6f, hz));
  // one tick is ~4.88 us at 50 Hz
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 4.88f, 1e6f / (hz * 4096.f));
}

void test_deg_mapping() {
  TEST_ASSERT_EQUAL_FLOAT(500.f, degToUs(0, 500, 2500));
  TEST_ASSERT_EQUAL_FLOAT(1500.f, degToUs(90, 500, 2500));
  TEST_ASSERT_EQUAL_FLOAT(2500.f, degToUs(180, 500, 2500));
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 45.f, usToDeg(1000, 500, 2500));
}

void test_ease() {
  TEST_ASSERT_EQUAL_FLOAT(0.f, ease(-1));
  TEST_ASSERT_EQUAL_FLOAT(0.f, ease(0));
  TEST_ASSERT_EQUAL_FLOAT(0.5f, ease(0.5f));
  TEST_ASSERT_EQUAL_FLOAT(1.f, ease(1));
  TEST_ASSERT_EQUAL_FLOAT(1.f, ease(2));
  float prev = 0;
  for (int i = 1; i <= 100; i++) {
    float e = ease(i / 100.f);
    TEST_ASSERT_TRUE(e >= prev);
    prev = e;
  }
  TEST_ASSERT_TRUE(ease(0.05f) < 0.05f);  // slow start
}

void test_line_assembler() {
  LineAssembler a;
  const char* s = "{\"a\":1}\r\n\n{\"b\":2}\n";
  int lines = 0;
  for (const char* p = s; *p; p++) {
    if (a.feed(*p) == LineAssembler::Result::Line) {
      lines++;
      TEST_ASSERT_EQUAL_STRING(lines == 1 ? "{\"a\":1}" : "{\"b\":2}", a.line());
    }
  }
  TEST_ASSERT_EQUAL_INT(2, lines);
  // 255 chars + '\n' = 256 bytes is the limit; one more overflows
  LineAssembler b;
  for (int i = 0; i < 255; i++) b.feed('x');
  TEST_ASSERT_TRUE(b.feed('\n') == LineAssembler::Result::Line);
  TEST_ASSERT_EQUAL_UINT32(255, b.lineLen());
  for (int i = 0; i < 300; i++) b.feed('x');
  TEST_ASSERT_TRUE(b.feed('\n') == LineAssembler::Result::Overflow);
  for (const char* p = "{}"; *p; p++) b.feed(*p);
  TEST_ASSERT_TRUE(b.feed('\n') == LineAssembler::Result::Line);  // recovers
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_prescale_50hz);
  RUN_TEST(test_ticks);
  RUN_TEST(test_deg_mapping);
  RUN_TEST(test_ease);
  RUN_TEST(test_line_assembler);
  return UNITY_END();
}
