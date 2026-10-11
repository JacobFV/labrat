package dev.tappet.head.calib

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** One touch reported by the target phone's touchpad page (`POST /api/touchpad/event {x,y,w,h,t}`). */
data class TouchEvent(
    val seq: Long, val x: Double, val y: Double, val w: Double, val h: Double, val t: Double, val serverT: Long,
) {
    val u get() = x / w
    val v get() = y / h

    fun toJson() = JsonObject(
        mapOf(
            "seq" to JsonPrimitive(seq), "x" to JsonPrimitive(x), "y" to JsonPrimitive(y),
            "w" to JsonPrimitive(w), "h" to JsonPrimitive(h), "t" to JsonPrimitive(t),
            "u" to JsonPrimitive(u), "v" to JsonPrimitive(v), "server_t" to JsonPrimitive(serverT),
        )
    )
}

/** Thread-safe bounded log of touch events (mirrors host/tappet/touch.py TouchLog). */
class TouchLog(private val maxLen: Int = 5000) {
    private val events = ArrayDeque<TouchEvent>()
    private val _lastSeq = MutableStateFlow(0L)
    val lastSeqFlow: StateFlow<Long> = _lastSeq.asStateFlow()
    val lastSeq: Long get() = _lastSeq.value

    @Synchronized
    fun add(x: Double, y: Double, w: Double, h: Double, t: Double?): TouchEvent {
        require(w > 0 && h > 0) { "w and h must be > 0" }
        val ev = TouchEvent(_lastSeq.value + 1, x, y, w, h, t ?: System.currentTimeMillis().toDouble(), System.currentTimeMillis())
        events.addLast(ev)
        while (events.size > maxLen) events.removeFirst()
        _lastSeq.value = ev.seq
        return ev
    }

    @Synchronized
    fun since(seq: Long): List<TouchEvent> = events.filter { it.seq > seq }

    /** Waits for the first event with seq > [since]; null on timeout. */
    suspend fun waitFor(since: Long, timeoutMs: Long): TouchEvent? = withTimeoutOrNull(timeoutMs) {
        _lastSeq.first { it > since }
        since(since).firstOrNull()
    }
}
