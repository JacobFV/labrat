package dev.tappet.head.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** TextToSpeech wrapper exposing a speaking flag and a per-word "mouth pulse" for the face animation. */
class Speaker(context: Context) {
    private val _speaking = MutableStateFlow(false)
    val speaking: StateFlow<Boolean> = _speaking.asStateFlow()

    /** Increments on every word boundary while speaking (drives mouth pulses). */
    private val _wordTick = MutableStateFlow(0L)
    val wordTick: StateFlow<Long> = _wordTick.asStateFlow()

    private val ready = CompletableDeferred<Boolean>()
    private val waiters = ConcurrentHashMap<String, CompletableDeferred<Unit>>()

    private val tts: TextToSpeech = TextToSpeech(context.applicationContext) { status ->
        ready.complete(status == TextToSpeech.SUCCESS)
    }

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String) { _speaking.value = true }
            override fun onDone(id: String) = finish(id)
            @Deprecated("Deprecated in Java")
            override fun onError(id: String) = finish(id)
            override fun onError(id: String, errorCode: Int) = finish(id)
            override fun onStop(id: String, interrupted: Boolean) = finish(id)
            override fun onRangeStart(id: String, start: Int, end: Int, frame: Int) { _wordTick.value++ }
        })
    }

    private fun finish(id: String) {
        waiters.remove(id)?.complete(Unit)
        if (waiters.isEmpty()) _speaking.value = false
    }

    /** Speaks [text] and suspends until finished (or ~ a generous timeout). */
    suspend fun speak(text: String) {
        val clean = text.replace(Regex("[*_`#>]"), "").trim()
        if (clean.isEmpty()) return
        if (!ready.await()) return
        runCatching { tts.language = Locale.getDefault() }
        val id = UUID.randomUUID().toString()
        val d = CompletableDeferred<Unit>()
        waiters[id] = d
        _speaking.value = true
        val r = tts.speak(clean, TextToSpeech.QUEUE_ADD, null, id)
        if (r != TextToSpeech.SUCCESS) {
            finish(id); return
        }
        withTimeoutOrNull(15_000L + clean.length * 120L) { d.await() } ?: finish(id)
    }

    fun stop() {
        tts.stop()
        waiters.keys.toList().forEach { finish(it) }
    }

    fun shutdown() {
        stop()
        tts.shutdown()
    }
}
