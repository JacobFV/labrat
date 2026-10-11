package dev.tappet.head.calib

import dev.tappet.head.config.DeviceProfile
import dev.tappet.head.config.RobotConfig
import dev.tappet.head.util.JsonX
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.JsonObject
import java.io.File

/** Persists the [ScreenCalibration] as JSON (`files/screen_calibration.json`). */
class CalibrationStore(private val file: File, private val robot: RobotConfig, initialDevice: DeviceProfile) {
    private val _flow = MutableStateFlow(load(initialDevice))
    val flow: StateFlow<ScreenCalibration> = _flow.asStateFlow()
    val value: ScreenCalibration get() = _flow.value

    private fun load(device: DeviceProfile): ScreenCalibration {
        val def = ScreenCalibration.default(robot, device)
        if (!file.exists()) return def
        return runCatching {
            ScreenCalibration.fromJson(JsonX.parseToJsonElement(file.readText()) as JsonObject, def)
        }.getOrDefault(def)
    }

    @Synchronized
    fun set(cal: ScreenCalibration) {
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(cal.toJson().toString())
        if (!tmp.renameTo(file)) {
            file.writeText(cal.toJson().toString())
            tmp.delete()
        }
        _flow.value = cal
    }

    /** Applies a (possibly partial) calibration patch from the web API; see [ScreenCalibration.merge]. */
    fun merge(patch: JsonObject, device: DeviceProfile, fallbackCameraSize: () -> P2?): ScreenCalibration {
        val c = ScreenCalibration.merge(value, patch, { ScreenCalibration.default(robot, device) }, fallbackCameraSize)
        set(c)
        return c
    }

    /** Reset to the default calibration for [device] (keeps the camera homography). */
    fun resetToDefault(device: DeviceProfile): ScreenCalibration {
        val c = ScreenCalibration.default(robot, device).copy(hIc = value.hIc, cameraSize = value.cameraSize, updated = ScreenCalibration.now())
        set(c)
        return c
    }
}
