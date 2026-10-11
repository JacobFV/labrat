package dev.tappet.head.config

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class TransportChoice(val label: String) {
    AUTO("Auto (AOA → USB serial → simulator)"),
    AOA("USB accessory (AOA)"),
    USB_SERIAL("USB serial (OTG to UART port)"),
    TCP("Simulator (TCP)"),
}

/** iPhone portrait keyboard geometry in screen-normalized v (0 top → 1 bottom). */
data class KeyboardParams(
    /** Top edge of the first key row (q…p). */
    val top: Double = 0.655,
    /** Bottom edge of the bottom key row (123 / space / return). */
    val bottom: Double = 0.905,
    /** Horizontal margin each side (normalized u). */
    val sideMargin: Double = 0.004,
    /** iOS auto-capitalizes the first letter of a field and after ". ". */
    val autoCapitalize: Boolean = true,
    /** iOS returns from the 123 layer to letters after typing a space or apostrophe. */
    val symbolsReturnOnSpace: Boolean = true,
    /** Hold time per key tap. */
    val keyHoldMs: Int = 40,
)

data class AppSettings(
    val apiKey: String = "",
    val model: String = DEFAULT_MODEL,
    val transport: TransportChoice = TransportChoice.AUTO,
    val simHost: String = "10.0.2.2",
    val simPort: Int = 7777,
    val deviceProfileName: String = "",
    val keyboard: KeyboardParams = KeyboardParams(),
    val trayPose: NeckPose = NeckPose(0.0, -50.0),
    val userPose: NeckPose = NeckPose(0.0, 0.0),
    val wakeWordEnabled: Boolean = true,
    val ttsEnabled: Boolean = true,
    val eyesTrackMotion: Boolean = true,
    val webPort: Int = 8080,
) {
    companion object {
        const val DEFAULT_MODEL = "claude-sonnet-5"
    }
}

/** SharedPreferences-backed settings with a [StateFlow]. */
class SettingsStore(context: Context, private val robot: RobotConfig) {
    private val prefs: SharedPreferences = context.getSharedPreferences("tappet_settings", Context.MODE_PRIVATE)
    private val _flow = MutableStateFlow(load())
    val flow: StateFlow<AppSettings> = _flow.asStateFlow()
    val value: AppSettings get() = _flow.value

    private fun d(k: String, def: Double) = prefs.getString(k, null)?.toDoubleOrNull() ?: def

    private fun load(): AppSettings {
        val def = AppSettings(trayPose = robot.lookAtTray, userPose = robot.lookAtUser, webPort = robot.webPort, simPort = robot.simTcpPort)
        val kd = def.keyboard
        return AppSettings(
            apiKey = prefs.getString("api_key", def.apiKey)!!,
            model = prefs.getString("model", def.model)!!.ifBlank { def.model },
            transport = runCatching { TransportChoice.valueOf(prefs.getString("transport", def.transport.name)!!) }.getOrDefault(def.transport),
            simHost = prefs.getString("sim_host", def.simHost)!!,
            simPort = prefs.getInt("sim_port", def.simPort),
            deviceProfileName = prefs.getString("device_profile", robot.defaultDevice.name)!!,
            keyboard = KeyboardParams(
                top = d("kb_top", kd.top),
                bottom = d("kb_bottom", kd.bottom),
                sideMargin = d("kb_margin", kd.sideMargin),
                autoCapitalize = prefs.getBoolean("kb_autocap", kd.autoCapitalize),
                symbolsReturnOnSpace = prefs.getBoolean("kb_symret", kd.symbolsReturnOnSpace),
                keyHoldMs = prefs.getInt("kb_hold", kd.keyHoldMs),
            ),
            trayPose = NeckPose(d("tray_pan", def.trayPose.pan), d("tray_tilt", def.trayPose.tilt)),
            userPose = NeckPose(d("user_pan", def.userPose.pan), d("user_tilt", def.userPose.tilt)),
            wakeWordEnabled = prefs.getBoolean("wake_word", def.wakeWordEnabled),
            ttsEnabled = prefs.getBoolean("tts", def.ttsEnabled),
            eyesTrackMotion = prefs.getBoolean("eyes_motion", def.eyesTrackMotion),
            webPort = prefs.getInt("web_port", def.webPort),
        )
    }

    fun update(f: (AppSettings) -> AppSettings) {
        val s = f(_flow.value)
        prefs.edit()
            .putString("api_key", s.apiKey.trim())
            .putString("model", s.model.trim())
            .putString("transport", s.transport.name)
            .putString("sim_host", s.simHost.trim())
            .putInt("sim_port", s.simPort)
            .putString("device_profile", s.deviceProfileName)
            .putString("kb_top", s.keyboard.top.toString())
            .putString("kb_bottom", s.keyboard.bottom.toString())
            .putString("kb_margin", s.keyboard.sideMargin.toString())
            .putBoolean("kb_autocap", s.keyboard.autoCapitalize)
            .putBoolean("kb_symret", s.keyboard.symbolsReturnOnSpace)
            .putInt("kb_hold", s.keyboard.keyHoldMs)
            .putString("tray_pan", s.trayPose.pan.toString())
            .putString("tray_tilt", s.trayPose.tilt.toString())
            .putString("user_pan", s.userPose.pan.toString())
            .putString("user_tilt", s.userPose.tilt.toString())
            .putBoolean("wake_word", s.wakeWordEnabled)
            .putBoolean("tts", s.ttsEnabled)
            .putBoolean("eyes_motion", s.eyesTrackMotion)
            .putInt("web_port", s.webPort)
            .apply()
        _flow.value = s.copy(apiKey = s.apiKey.trim(), model = s.model.trim().ifBlank { AppSettings.DEFAULT_MODEL })
    }
}
