package dev.tappet.head.service

import android.content.Context
import android.util.Log
import androidx.lifecycle.LifecycleOwner
import dev.tappet.head.agent.Agent
import dev.tappet.head.agent.AgentTools
import dev.tappet.head.agent.AnthropicApi
import dev.tappet.head.agent.MemoryStore
import dev.tappet.head.agent.Reminders
import dev.tappet.head.agent.TaskStore
import dev.tappet.head.body.AoaTransport
import dev.tappet.head.body.BodyClient
import dev.tappet.head.body.BodyLink
import dev.tappet.head.body.TcpTransport
import dev.tappet.head.body.UsbSerialTransport
import dev.tappet.head.calib.CalibrationStore
import dev.tappet.head.calib.TouchLog
import dev.tappet.head.camera.CameraController
import dev.tappet.head.config.DeviceProfile
import dev.tappet.head.config.DeviceProfiles
import dev.tappet.head.config.RobotConfig
import dev.tappet.head.config.SettingsStore
import dev.tappet.head.config.TransportChoice
import dev.tappet.head.robot.RobotActions
import dev.tappet.head.ui.Expression
import dev.tappet.head.ui.FaceState
import dev.tappet.head.util.jsonOf
import dev.tappet.head.voice.ListenMode
import dev.tappet.head.voice.Speaker
import dev.tappet.head.voice.VoiceListener
import dev.tappet.head.web.Pairing
import dev.tappet.head.web.WebServer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Owns the whole head runtime: body link, calibration, actions, camera, voice, agent and web server.
 * Created by [HeadService]; the UI observes it through [HeadService.controller].
 */
class HeadController(private val context: Context) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val robot: RobotConfig = RobotConfig.parse(context.assets.open("config/robot.json").use { it.readBytes().toString(Charsets.UTF_8) })
    val settings = SettingsStore(context, robot)
    val deviceProfiles: List<DeviceProfile> = DeviceProfiles.presets(robot)
    val calibration = CalibrationStore(File(context.filesDir, "screen_calibration.json"), robot, currentProfile())
    val touchLog = TouchLog()
    val pairing = Pairing(context)
    val camera = CameraController(context)
    val speaker = Speaker(context)
    val voice = VoiceListener(context) { text -> scope.launch { runCatching { chat(text, speak = true) } } }

    val client = BodyClient(scope, linkFactory = { links() })
    val actions = RobotActions(robot, client, calibration, touchLog, { settings.value }, ::snapshot)
    private val tasks = TaskStore(File(context.filesDir, "tasks.json"))
    private val memory = MemoryStore(File(context.filesDir, "memory.json"))
    val agent = Agent(AnthropicApi(), AgentTools(context, actions, tasks, memory)) { settings.value }

    private var web: WebServer? = null
    private val _webError = MutableStateFlow<String?>(null)
    val webError: StateFlow<String?> = _webError.asStateFlow()

    private val _transient = MutableStateFlow<Pair<Expression, Long>?>(null)
    private val _lastReply = MutableStateFlow("")
    val lastReply: StateFlow<String> = _lastReply.asStateFlow()

    /** Combined face state for the UI. */
    val face: StateFlow<FaceState> = combine(
        combine(speaker.speaking, agent.busy, voice.mode) { s, b, m -> Triple(s, b, m) },
        _transient,
        camera.gaze,
        speaker.wordTick,
        settings.flow.map { it.eyesTrackMotion }.distinctUntilChanged(),
    ) { (speaking, busy, mode), transient, gaze, tick, track ->
        val now = System.currentTimeMillis()
        val expr = when {
            transient != null && transient.second > now -> transient.first
            speaking -> Expression.SPEAKING
            busy -> Expression.THINKING
            mode == ListenMode.COMMAND -> Expression.LISTENING
            else -> Expression.IDLE
        }
        FaceState(expr, if (track) gaze.x else 0f, if (track) gaze.y else 0f, tick)
    }.stateIn(scope, SharingStarted.Eagerly, FaceState())

    fun currentProfile(): DeviceProfile {
        val name = settings.value.deviceProfileName
        return DeviceProfiles.presets(robot).firstOrNull { it.name == name } ?: robot.defaultDevice
    }

    private fun links(): List<BodyLink> {
        val s = settings.value
        val aoa = { AoaTransport(context, robot.aoaManufacturer, robot.aoaModel) }
        val serial = { UsbSerialTransport(context, robot.uartBaud) }
        val tcp = { TcpTransport(s.simHost, s.simPort) }
        return when (s.transport) {
            TransportChoice.AOA -> listOf(aoa())
            TransportChoice.USB_SERIAL -> listOf(serial())
            TransportChoice.TCP -> listOf(tcp())
            TransportChoice.AUTO -> when {
                AoaTransport.isAttached(context, robot.aoaManufacturer, robot.aoaModel) -> listOf(aoa())
                UsbSerialTransport.findDriver(context) != null -> listOf(serial())
                else -> listOf(tcp())
            }
        }
    }

    fun start() {
        Reminders.ensureChannel(context)
        Reminders.onFired = { kind, text ->
            flash(Expression.HAPPY, 4000)
            sayAsync(if (kind == "timer") "Time's up: $text" else "Reminder: $text")
        }
        client.start()
        startWeb()
        // pause speech recognition while the robot speaks or thinks
        scope.launch {
            combine(speaker.speaking, agent.busy) { s, b -> s || b }.distinctUntilChanged().collect { voice.setPaused(it) }
        }
        scope.launch {
            settings.flow.map { it.wakeWordEnabled }.distinctUntilChanged().collect { voice.setWakeEnabled(it && voice.available()) }
        }
        scope.launch {
            settings.flow.map { it.webPort }.distinctUntilChanged().collect { restartWebIfPortChanged(it) }
        }
        scope.launch {
            client.events.collect { ev ->
                when (ev["evt"]?.toString()?.trim('"')) {
                    "fault", "watchdog" -> flash(Expression.ERROR, 3000)
                    "button" -> flash(Expression.HAPPY, 1500)
                }
            }
        }
    }

    /** (Re)starts camera and voice once permissions are available. Main thread. */
    fun startSensors(owner: LifecycleOwner) {
        if (camera.hasPermission() && !camera.running.value) camera.start(owner)
        voice.setWakeEnabled(settings.value.wakeWordEnabled && voice.available())
    }

    private fun startWeb() {
        val port = settings.value.webPort
        try {
            web = WebServer(port, this, context.assets).also { it.start(fi.iki.elonen.NanoHTTPD.SOCKET_READ_TIMEOUT, false) }
            _webError.value = null
        } catch (e: Exception) {
            Log.e(TAG, "web server failed on :$port", e)
            _webError.value = "web server failed on :$port: ${e.message}"
        }
    }

    private fun restartWebIfPortChanged(port: Int) {
        val w = web ?: return
        if (w.listeningPort == port) return
        w.stop()
        startWeb()
    }

    fun stop() {
        Reminders.onFired = null
        runCatching { web?.stop() }
        client.stop()
        camera.stop()
        voice.shutdown()
        speaker.shutdown()
        scope.cancel()
    }

    fun flash(expr: Expression, ms: Long) {
        _transient.value = expr to System.currentTimeMillis() + ms
        scope.launch {
            delay(ms + 50)
            val t = _transient.value
            if (t != null && t.second <= System.currentTimeMillis()) _transient.value = null
        }
    }

    fun onPaired() = flash(Expression.HAPPY, 2500)

    fun snapshot(rectified: Boolean): ByteArray? {
        val cal = calibration.value
        val corners = cal.cameraCornersNormalized()
        return if (rectified && corners != null) {
            runCatching { camera.rectifiedJpeg(corners, cal.device.screenW, cal.device.screenL) }.getOrNull()
        } else camera.latestJpeg()
    }

    suspend fun say(text: String) {
        _lastReply.value = text
        if (settings.value.ttsEnabled) speaker.speak(text)
    }

    fun sayAsync(text: String) {
        scope.launch { say(text) }
    }

    /** Sends user text to the agent; the reply is spoken when [speak]. */
    suspend fun chat(text: String, speak: Boolean): String {
        val reply = try {
            agent.chat(text)
        } catch (e: Exception) {
            flash(Expression.ERROR, 3000)
            val msg = "Sorry, ${e.message ?: "something went wrong"}"
            if (speak) sayAsync(msg)
            throw e
        }
        if (speak) sayAsync(reply)
        return reply
    }

    /** `GET /api/status`, same shape as the Python bridge (plus a few head-specific fields). */
    suspend fun statusJson(): JsonObject {
        val ls = client.status.value
        val body = if (ls.connected) withTimeoutOrNull(1500) { runCatching { client.request("status", timeoutMs = 1200) }.getOrNull() } else null
        val link = linkedMapOf<String, Any?>("connected" to ls.connected, "transport" to (ls.transport ?: settings.value.transport.name.lowercase()), "state" to ls.state.name.lowercase())
        if (!ls.connected && ls.detail != null) link["error"] = ls.detail
        if (ls.hello != null) link["hello"] = ls.hello
        return jsonOf(
            "link" to link,
            "body" to (body ?: if (ls.connected) client.bodyStatus.value else null),
            "calib" to calibration.value.toJson(),
            "agent" to mapOf("busy" to agent.busy.value, "available" to settings.value.apiKey.isNotBlank(), "model" to settings.value.model),
            "camera" to mapOf("available" to camera.hasPermission(), "running" to camera.running.value, "error" to camera.lastError),
            "touchpad" to mapOf("last_seq" to touchLog.lastSeq),
            "progress" to actions.progress.value,
            "host" to "android-head",
        )
    }

    fun webUrl(): String = "http://${ipAddress() ?: "localhost"}:${settings.value.webPort}"

    companion object {
        private const val TAG = "HeadController"

        fun ipAddress(): String? = runCatching {
            NetworkInterface.getNetworkInterfaces().toList()
                .filter { it.isUp && !it.isLoopback }
                .sortedBy { if (it.name.startsWith("wlan")) 0 else 1 }
                .flatMap { it.inetAddresses.toList() }
                .firstOrNull { it is Inet4Address && !it.isLoopbackAddress }?.hostAddress
        }.getOrNull()
    }
}
