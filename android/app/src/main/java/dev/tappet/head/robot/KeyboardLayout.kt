package dev.tappet.head.robot

import dev.tappet.head.config.KeyboardParams

enum class KbLayer { LETTERS, NUMBERS, SYMBOLS }

/** One planned key tap in screen-normalized coordinates. */
data class KeyTap(val label: String, val u: Double, val v: Double)

data class TypingPlan(val taps: List<KeyTap>, val skipped: List<Char>, val endLayer: KbLayer)

/**
 * iPhone portrait system keyboard (QWERTY, Face ID-era layout: 123 / emoji / space / return bottom row)
 * in screen-normalized coordinates. The vertical extent of the four key rows is configurable
 * ([KeyboardParams.top] → [KeyboardParams.bottom]); columns are derived from 10 equal key pitches.
 *
 * Layers: letters (with one-shot shift), "123" numbers/punctuation, "#+=" symbols.
 */
class KeyboardLayout(private val p: KeyboardParams) {
    private val pitch = (1.0 - 2 * p.sideMargin) / 10.0
    private fun col(c: Double) = p.sideMargin + c * pitch
    private fun rowV(r: Int) = p.top + (r + 0.5) * (p.bottom - p.top) / 4.0

    // key-pitch centres
    private val shiftU get() = col(0.675)
    private val deleteU get() = col(10 - 0.675)
    private val modeU get() = col(0.625)       // 123 / ABC
    private val spaceU get() = col(5.0)
    private val returnU get() = col(8.75)

    private val letterRows = listOf("qwertyuiop", "asdfghjkl", "zxcvbnm")
    private val numRows = listOf("1234567890", "-/:;()$&@\"")
    private val symRows = listOf("[]{}#%^*+=", "_\\|~<>€£¥•")
    private val punct = ".,?!'"

    private fun letterPos(c: Char): KeyTap? {
        val lc = c.lowercaseChar()
        letterRows.forEachIndexed { r, row ->
            val i = row.indexOf(lc)
            if (i >= 0) {
                val u = when (r) {
                    0 -> col(i + 0.5)
                    1 -> col(i + 1.0)
                    else -> 0.5 + (i - 3) * pitch
                }
                return KeyTap(c.toString(), u, rowV(r))
            }
        }
        return null
    }

    private fun gridPos(rows: List<String>, c: Char): KeyTap? {
        rows.forEachIndexed { r, row ->
            val i = row.indexOf(c)
            if (i >= 0) return KeyTap(c.toString(), col(i + 0.5), rowV(r))
        }
        return null
    }

    /** Punctuation on row 3 of the 123 and #+= layers: five wide keys between the mode and delete keys. */
    private fun punctPos(c: Char): KeyTap? {
        val i = punct.indexOf(c)
        if (i < 0) return null
        val w = 7.0 / 5.0
        return KeyTap(c.toString(), col(1.5 + (i + 0.5) * w), rowV(2))
    }

    fun shiftKey() = KeyTap("⇧", shiftU, rowV(2))
    fun deleteKey() = KeyTap("⌫", deleteU, rowV(2))
    fun modeKey(label: String) = KeyTap(label, modeU, rowV(3))
    fun spaceKey() = KeyTap("space", spaceU, rowV(3))
    fun returnKey() = KeyTap("return", returnU, rowV(3))
    /** On the 123 layer this key reads "#+="; on the #+= layer it reads "123". */
    fun layerToggleKey(label: String) = KeyTap(label, shiftU, rowV(2))

    /** Every key of a layer (for drawing an overlay / debugging). */
    fun keys(layer: KbLayer): List<KeyTap> = when (layer) {
        KbLayer.LETTERS -> letterRows.joinToString("").map { letterPos(it)!! } +
            listOf(shiftKey(), deleteKey(), modeKey("123"), spaceKey(), returnKey())
        KbLayer.NUMBERS -> numRows.joinToString("").map { gridPos(numRows, it)!! } + punct.map { punctPos(it)!! } +
            listOf(layerToggleKey("#+="), deleteKey(), modeKey("ABC"), spaceKey(), returnKey())
        KbLayer.SYMBOLS -> symRows.joinToString("").map { gridPos(symRows, it)!! } + punct.map { punctPos(it)!! } +
            listOf(layerToggleKey("123"), deleteKey(), modeKey("ABC"), spaceKey(), returnKey())
    }

    /**
     * Plans the key taps to type [text] starting from the letters layer.
     * [shiftedAtStart]: whether iOS has shift engaged at the start (auto-capitalization of an empty field).
     */
    fun plan(text: String, shiftedAtStart: Boolean = p.autoCapitalize): TypingPlan {
        val taps = ArrayList<KeyTap>()
        val skipped = ArrayList<Char>()
        var layer = KbLayer.LETTERS
        var shifted = shiftedAtStart
        val typed = StringBuilder()

        fun toLetters() {
            if (layer != KbLayer.LETTERS) {
                taps += modeKey("ABC")
                layer = KbLayer.LETTERS
            }
        }
        fun toNumbers() {
            when (layer) {
                KbLayer.LETTERS -> taps += modeKey("123")
                KbLayer.SYMBOLS -> taps += layerToggleKey("123")
                KbLayer.NUMBERS -> {}
            }
            layer = KbLayer.NUMBERS
        }
        fun toSymbols() {
            if (layer == KbLayer.SYMBOLS) return
            toNumbers()
            taps += layerToggleKey("#+=")
            layer = KbLayer.SYMBOLS
        }
        fun autoCapNow(): Boolean {
            if (!p.autoCapitalize) return false
            val s = typed.toString()
            if (s.isEmpty()) return shiftedAtStart
            if (s.endsWith("\n")) return true
            return Regex("[.!?]\\s+$").containsMatchIn(s)
        }

        for (c in text) {
            when {
                c.isLetter() && letterPos(c) != null -> {
                    toLetters()
                    val wantUpper = c.isUpperCase()
                    if (wantUpper != shifted) taps += shiftKey()
                    taps += letterPos(c)!!
                    typed.append(c)
                    shifted = autoCapNow()
                }
                c == ' ' -> {
                    taps += spaceKey()
                    typed.append(c)
                    if (layer != KbLayer.LETTERS && p.symbolsReturnOnSpace) layer = KbLayer.LETTERS
                    shifted = autoCapNow()
                }
                c == '\n' -> {
                    taps += returnKey()
                    typed.append(c)
                    if (layer != KbLayer.LETTERS && p.symbolsReturnOnSpace) layer = KbLayer.LETTERS
                    shifted = autoCapNow()
                }
                punct.indexOf(c) >= 0 -> {
                    if (layer == KbLayer.LETTERS) toNumbers()
                    taps += punctPos(c)!!
                    typed.append(c)
                    if (c == '\'' && p.symbolsReturnOnSpace) layer = KbLayer.LETTERS
                    shifted = if (layer == KbLayer.LETTERS) autoCapNow() else shifted
                }
                gridPos(numRows, c) != null -> {
                    toNumbers()
                    taps += gridPos(numRows, c)!!
                    typed.append(c)
                }
                gridPos(symRows, c) != null -> {
                    toSymbols()
                    taps += gridPos(symRows, c)!!
                    typed.append(c)
                }
                else -> skipped += c
            }
        }
        return TypingPlan(taps, skipped, layer)
    }
}
