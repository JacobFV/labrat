package dev.tappet.head.calib

import dev.tappet.head.config.DeviceProfile
import dev.tappet.head.config.RobotConfig
import dev.tappet.head.util.JsonX
import dev.tappet.head.util.arr
import dev.tappet.head.util.bool
import dev.tappet.head.util.dbl
import dev.tappet.head.util.int
import dev.tappet.head.util.obj
import dev.tappet.head.util.str
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.abs

/**
 * Screen calibration (SPEC §6), JSON-compatible with host/tappet/calibration.py and the web UI:
 *
 * ```
 * {"version":1, "device":{name,screen_w,screen_l,screen_px_w,screen_px_h,surface_z},
 *  "H_sw": 3x3 [u,v,1] → world [x,y,1] mm, "surface_z": mm,
 *  "H_ic": null | 3x3 camera pixel → [u,v], "camera_size": null | [w,h],
 *  "source": "default"|"auto"|"manual", "rms_mm": null|float, "touch_size": null|[w,h]}
 * ```
 * (u,v): u 0 left → 1 right, v 0 top → 1 bottom, as seen by a person using the phone.
 */
data class ScreenCalibration(
    val device: DeviceProfile,
    val hSw: Homography,
    val surfaceZ: Double,
    val source: String = "default",
    val rmsMm: Double? = null,
    /** Camera pixel (in a [cameraSize] image) → screen (u,v). */
    val hIc: Homography? = null,
    val cameraSize: P2? = null,
    val touchSize: P2? = null,
    val updated: String? = null,
) {
    fun screenToWorld(u: Double, v: Double): P2 = hSw.apply(u, v)
    fun worldToScreen(x: Double, y: Double): P2 = hSw.inverse().apply(x, y)

    /** Screen corners (TL, TR, BR, BL) in normalized camera-image coordinates, from H_ic. */
    fun cameraCornersNormalized(): List<P2>? {
        val h = hIc ?: return null
        val size = cameraSize ?: return null
        return runCatching {
            val inv = h.inverse()
            SCREEN_CORNERS.map { c -> inv.apply(c).let { P2(it.x / size.x, it.y / size.y) } }
        }.getOrNull()
    }

    /** Sets H_ic from 4 corners clicked in an image of [size] pixels (TL, TR, BR, BL). */
    fun withCameraCorners(cornersPx: List<P2>, size: P2): ScreenCalibration {
        require(cornersPx.size == 4) { "need 4 corners (TL, TR, BR, BL)" }
        return copy(hIc = Homography.fit(cornersPx, SCREEN_CORNERS), cameraSize = size, updated = now())
    }

    fun toJson(): JsonObject = JsonObject(
        linkedMapOf(
            "version" to JsonPrimitive(1),
            "device" to deviceToJson(device),
            "H_sw" to matrix(hSw),
            "surface_z" to JsonPrimitive(surfaceZ),
            "H_ic" to (hIc?.let { matrix(it) } ?: JsonNull),
            "camera_size" to (cameraSize?.let { pair(it) } ?: JsonNull),
            "source" to JsonPrimitive(source),
            "rms_mm" to (rmsMm?.let { JsonPrimitive(it) } ?: JsonNull),
            "touch_size" to (touchSize?.let { pair(it) } ?: JsonNull),
        ).apply { if (updated != null) put("updated", JsonPrimitive(updated)) }
    )

    companion object {
        val SCREEN_CORNERS = listOf(P2(0.0, 0.0), P2(1.0, 0.0), P2(1.0, 1.0), P2(0.0, 1.0))

        fun now(): String = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"))

        private fun matrix(h: Homography) = JsonArray((0..2).map { r ->
            JsonArray((0..2).map { c -> JsonPrimitive(h.h[r * 3 + c]) })
        })

        private fun pair(p: P2) = JsonArray(listOf(JsonPrimitive(p.x), JsonPrimitive(p.y)))

        /** Accepts a 3×3 nested array or a flat 9-element array. */
        fun parseMatrix(e: JsonElement?): Homography? {
            val a = e as? JsonArray ?: return null
            val flat = if (a.size == 3 && a.all { it is JsonArray }) a.flatMap { it.jsonArray } else a
            require(flat.size == 9) { "matrix must be 3x3" }
            val h = Homography(flat.map { it.jsonPrimitive.double }.toDoubleArray())
            require(h.h.all { it.isFinite() }) { "matrix must be finite" }
            return h
        }

        private fun parsePair(e: JsonElement?): P2? {
            val a = e as? JsonArray ?: return null
            require(a.size == 2) { "expected [w,h]" }
            return P2(a[0].jsonPrimitive.double, a[1].jsonPrimitive.double)
        }

        /**
         * Default from tray + device profile: the phone lies screen-up, portrait, centred in the tray,
         * its top edge toward +y. Seen from the robot (looking toward +y), u → +x and v → −y.
         */
        fun default(cfg: RobotConfig, device: DeviceProfile = cfg.defaultDevice): ScreenCalibration {
            val cx = cfg.tray.centerX
            val cy = cfg.tray.centerY
            val w = device.screenW
            val l = device.screenL
            // world = [[w,0,cx-w/2],[0,-l,cy+l/2],[0,0,1]] · [u,v,1]
            val h = Homography(doubleArrayOf(w, 0.0, cx - w / 2, 0.0, -l, cy + l / 2, 0.0, 0.0, 1.0))
            return ScreenCalibration(device, h, device.surfaceZ, "default")
        }

        fun deviceToJson(d: DeviceProfile) = JsonObject(
            linkedMapOf(
                "name" to JsonPrimitive(d.name),
                "screen_w" to JsonPrimitive(d.screenW), "screen_l" to JsonPrimitive(d.screenL),
                "screen_px_w" to JsonPrimitive(d.screenPxW), "screen_px_h" to JsonPrimitive(d.screenPxH),
                "surface_z" to JsonPrimitive(d.surfaceZ),
                "body_w" to JsonPrimitive(d.bodyW), "body_l" to JsonPrimitive(d.bodyL), "body_t" to JsonPrimitive(d.bodyT),
            )
        )

        fun deviceFromJson(o: JsonObject, fallback: DeviceProfile) = DeviceProfile(
            o.str("name") ?: fallback.name,
            o.dbl("body_w") ?: fallback.bodyW, o.dbl("body_l") ?: fallback.bodyL, o.dbl("body_t") ?: fallback.bodyT,
            o.dbl("screen_w") ?: fallback.screenW, o.dbl("screen_l") ?: fallback.screenL,
            o.int("screen_px_w") ?: fallback.screenPxW, o.int("screen_px_h") ?: fallback.screenPxH,
            o.dbl("surface_z") ?: fallback.surfaceZ,
        )

        /** Parses a full calibration object (missing fields fall back to [base]). */
        fun fromJson(o: JsonObject, base: ScreenCalibration): ScreenCalibration {
            val device = o.obj("device")?.let { deviceFromJson(it, base.device) } ?: base.device
            return validate(
                ScreenCalibration(
                    device = device,
                    hSw = parseMatrix(o["H_sw"]) ?: base.hSw,
                    surfaceZ = o.dbl("surface_z") ?: base.surfaceZ,
                    source = o.str("source") ?: base.source,
                    rmsMm = if (o.containsKey("rms_mm")) o.dbl("rms_mm") else base.rmsMm,
                    hIc = if (o.containsKey("H_ic")) parseMatrix(o["H_ic"]) else base.hIc,
                    cameraSize = if (o.containsKey("camera_size")) parsePair(o["camera_size"]) else base.cameraSize,
                    touchSize = if (o.containsKey("touch_size")) parsePair(o["touch_size"]) else base.touchSize,
                    updated = o.str("updated") ?: base.updated,
                )
            )
        }

        /**
         * Applies a `POST /api/calibration` body (same semantics as the Python bridge):
         * `{"reset":true}` → default; `camera_corners` (pixels, TL TR BR BL) [+ `camera_size`] → H_ic;
         * any other field replaces the stored one (`device` is merged); changing `H_sw` or `surface_z`
         * marks the source "manual" unless `source` is given.
         */
        fun merge(
            base: ScreenCalibration, patch: JsonObject, defaultFor: (DeviceProfile) -> ScreenCalibration,
            fallbackCameraSize: () -> P2?,
        ): ScreenCalibration {
            if (patch.bool("reset") == true) return defaultFor(base.device).copy(updated = now())
            var c = fromJson(JsonObject(patch.filterKeys { it != "camera_corners" && it != "reset" }), base)
            if ("H_sw" in patch || "surface_z" in patch) c = c.copy(source = patch.str("source") ?: "manual")
            patch.arr("camera_corners")?.let { arr ->
                val corners = arr.map { val p = it.jsonArray; P2(p[0].jsonPrimitive.double, p[1].jsonPrimitive.double) }
                val size = parsePair(patch["camera_size"]) ?: c.cameraSize ?: fallbackCameraSize()
                    ?: throw IllegalArgumentException("camera_size required with camera_corners")
                c = c.withCameraCorners(corners, size)
            }
            return validate(c.copy(updated = now()))
        }

        fun validate(c: ScreenCalibration): ScreenCalibration {
            val m = c.hSw.h
            val det = m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6])
            require(abs(det) > 1e-9 && m.all { it.isFinite() }) { "H_sw must be an invertible 3x3 matrix" }
            require(c.surfaceZ.isFinite()) { "surface_z must be a number" }
            return c
        }

        fun parse(text: String, base: ScreenCalibration) =
            fromJson(JsonX.parseToJsonElement(text) as JsonObject, base)
    }
}
