package dev.tappet.head.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class ListenMode { OFF, WAKE, COMMAND }

/**
 * SpeechRecognizer loop. In WAKE mode it continuously transcribes short utterances and looks for the
 * wake phrase "hey tappet" (plus common mis-hearings); text after the phrase is the command, otherwise
 * the next utterance is. [pushToTalk] listens for one command directly. All recognizer calls happen on
 * the main thread. Listening is paused while [paused] is true (e.g. while the robot is speaking).
 */
class VoiceListener(private val context: Context, private val onCommand: (String) -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var listening = false
    private var awaitingCommand = false
    private var wakeEnabled = false
    private var paused = false
    private var restartPending = false

    private val _mode = MutableStateFlow(ListenMode.OFF)
    val mode: StateFlow<ListenMode> = _mode.asStateFlow()
    private val _partial = MutableStateFlow("")
    val partial: StateFlow<String> = _partial.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val tone by lazy { runCatching { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 60) }.getOrNull() }

    fun available(): Boolean = SpeechRecognizer.isRecognitionAvailable(context) &&
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    fun setWakeEnabled(enabled: Boolean) = main.post {
        wakeEnabled = enabled
        if (enabled) scheduleRestart(100) else if (!awaitingCommand) stopListening()
        updateMode()
    }

    fun setPaused(p: Boolean) = main.post {
        if (paused == p) return@post
        paused = p
        if (p) stopListening() else scheduleRestart(400)
        updateMode()
    }

    fun pushToTalk() = main.post {
        awaitingCommand = true
        stopListening()
        beep()
        scheduleRestart(150)
        updateMode()
    }

    fun shutdown() = main.post {
        wakeEnabled = false
        awaitingCommand = false
        stopListening()
        recognizer?.destroy()
        recognizer = null
        _mode.value = ListenMode.OFF
    }

    private fun updateMode() {
        _mode.value = when {
            paused -> ListenMode.OFF
            awaitingCommand -> ListenMode.COMMAND
            wakeEnabled -> ListenMode.WAKE
            else -> ListenMode.OFF
        }
    }

    private fun beep() {
        runCatching { tone?.startTone(ToneGenerator.TONE_PROP_BEEP, 120) }
    }

    private fun scheduleRestart(delayMs: Long) {
        if (restartPending) return
        restartPending = true
        main.postDelayed({
            restartPending = false
            if (!paused && (wakeEnabled || awaitingCommand)) startListening()
        }, delayMs)
    }

    private fun stopListening() {
        if (listening) runCatching { recognizer?.cancel() }
        listening = false
    }

    private fun startListening() {
        if (listening) return
        if (!available()) {
            _error.value = "speech recognition unavailable or RECORD_AUDIO not granted"
            return
        }
        val r = recognizer ?: SpeechRecognizer.createSpeechRecognizer(context).also {
            it.setRecognitionListener(listener)
            recognizer = it
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
            if (awaitingCommand) putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
        }
        listening = true
        updateMode()
        runCatching { r.startListening(intent) }.onFailure {
            listening = false
            _error.value = it.message
            scheduleRestart(2000)
        }
    }

    private fun handleResults(candidates: List<String>) {
        _partial.value = ""
        if (candidates.isEmpty()) return
        if (awaitingCommand) {
            awaitingCommand = false
            updateMode()
            val cmd = candidates.first().trim()
            val stripped = WakePhrase.strip(cmd) ?: cmd
            if (stripped.isNotBlank()) onCommand(stripped)
            return
        }
        for (c in candidates) {
            val rest = WakePhrase.strip(c) ?: continue
            if (rest.isNotBlank()) {
                onCommand(rest)
            } else {
                awaitingCommand = true
                beep()
                updateMode()
            }
            return
        }
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) { _error.value = null }
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onError(error: Int) {
            listening = false
            val delay = when (error) {
                SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> 150L
                SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> 1500L
                SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> 10_000L
                else -> 1000L
            }
            if (error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT || error == SpeechRecognizer.ERROR_NO_MATCH) {
                if (awaitingCommand && !wakeEnabled) awaitingCommand = false
            } else {
                Log.d(TAG, "recognizer error $error")
            }
            if (error == SpeechRecognizer.ERROR_CLIENT || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY) {
                recognizer?.destroy()
                recognizer = null
            }
            updateMode()
            scheduleRestart(delay)
        }
        override fun onResults(results: Bundle?) {
            listening = false
            handleResults(results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION).orEmpty())
            scheduleRestart(100)
        }
        override fun onPartialResults(partialResults: Bundle?) {
            _partial.value = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
        }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    companion object {
        private const val TAG = "Voice"
    }
}

/** Wake phrase matching ("hey tappet" and frequent recognizer spellings). */
object WakePhrase {
    private val regex = Regex(
        "^(?:.*?\\b)?(?:hey|hi|hay|a|ok|okay)[,!.\\s]+(?:tappet|tapit|tap it|tapped|tappit|tapet|tablet|tap pet|teppet|tippet|tap that|tapette|tepid|tapped it)\\b[,!.\\s]*",
        RegexOption.IGNORE_CASE,
    )

    /** Returns the text after the wake phrase ("" if none), or null when the phrase is absent. */
    fun strip(text: String): String? {
        val m = regex.find(text) ?: return null
        return text.substring(m.range.last + 1).trim()
    }
}
