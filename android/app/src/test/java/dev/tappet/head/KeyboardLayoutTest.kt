package dev.tappet.head

import dev.tappet.head.config.KeyboardParams
import dev.tappet.head.robot.KbLayer
import dev.tappet.head.robot.KeyboardLayout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class KeyboardLayoutTest {
    private val kb = KeyboardLayout(KeyboardParams())

    @Test
    fun simpleLowercaseWithAutoCap() {
        // empty field: iOS has shift on → typing "hi" needs an un-shift first
        val plan = kb.plan("hi")
        assertEquals(listOf("⇧", "h", "i"), plan.taps.map { it.label })
        // "Hi" needs no shift tap
        assertEquals(listOf("H", "i"), kb.plan("Hi").taps.map { it.label })
    }

    @Test
    fun sentenceCapitalization() {
        val labels = kb.plan("Ok. Go").taps.map { it.label }
        // O (auto-shifted) k → 123 . → space returns to letters with auto-cap → G o
        assertEquals(listOf("O", "k", "123", ".", "space", "G", "o"), labels)
    }

    @Test
    fun numbersAndSymbols() {
        val plan = kb.plan("a1#", shiftedAtStart = false)
        assertEquals(listOf("a", "123", "1", "#+=", "#"), plan.taps.map { it.label })
        assertEquals(KbLayer.SYMBOLS, plan.endLayer)
        val back = kb.plan("1a", shiftedAtStart = false).taps.map { it.label }
        assertEquals(listOf("123", "1", "ABC", "a"), back)
    }

    @Test
    fun skipsUnsupported() {
        val plan = kb.plan("a😀", shiftedAtStart = false)
        assertEquals(2, plan.skipped.size) // surrogate pair
    }

    @Test
    fun geometryWithinKeyboard() {
        val p = KeyboardParams()
        for (layer in KbLayer.values()) for (k in kb.keys(layer)) {
            assertTrue(k.u in 0.0..1.0)
            assertTrue("${k.label} ${k.v}", k.v > p.top && k.v < p.bottom)
        }
        val q = kb.keys(KbLayer.LETTERS).first { it.label == "q" }
        val pk = kb.keys(KbLayer.LETTERS).first { it.label == "p" }
        assertEquals(1.0, q.u + pk.u, 1e-9) // symmetric
    }
}
