package dev.tappet.head.robot

import dev.tappet.head.body.BodyClient
import dev.tappet.head.body.BodyException
import dev.tappet.head.calib.CalibrationStore
import dev.tappet.head.calib.Homography
import dev.tappet.head.calib.P2
import dev.tappet.head.calib.TouchLog
import dev.tappet.head.config.AppSettings
import dev.tappet.head.config.NeckPose
import dev.tappet.head.config.RobotConfig
import dev.tappet.head.kinematics.Kinematics
import dev.tappet.head.kinematics.Vec3
import dev.tappet.head.util.dbl
import dev.tappet.head.util.jsonOf
import dev.tappet.head.util.long
import dev.tappet.head.util.r
import dev.tappet.head.util.str
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.Random
import kotlin.math.hypot
import kotlin.math.sqrt

/**
 * High-level robot actions in target-screen coordinates (u,v ∈ [0,1], u→right, v→down).
 * Converts through the screen calibration to world mm and drives the body protocol. All arm motions
 * are serialized by [armLock] so the web UI, calibration and the agent never interleave strokes.
 */
class RobotActions(
    private val robot: RobotConfig,
    private val client: BodyClient,
    private val calibration: CalibrationStore,
    private val touches: TouchLog,
    private val settings: () -> AppSettings,
    /** Returns a JPEG of the head camera (rectified to the target screen when [rectified] and calibrated). */
    private val snapshot: (rectified: Boolean) -> ByteArray?,
) {
    private val armLock = Mutex()
    @Volatile private var longOp = false
    /** True while auto calibration or a tap test is running. */
    val busyLong: Boolean get() = longOp
    private val _progress = MutableStateFlow<String?>(null)
    /** Human-readable progress of long operations (auto calibration, tap test). */
    val progress: StateFlow<String?> = _progress.asStateFlow()

    private fun checkUv(u: Double, v: Double) {
        require(u in -0.02..1.02 && v in -0.02..1.02) { "u,v must be within 0..1 (got $u,$v)" }
    }

    /** Runs a body request, enabling the servos once and retrying if the body reports `not_enabled`. */
    private suspend fun <T> enabled(block: suspend () -> T): T = try {
        block()
    } catch (e: BodyException) {
        if (e.err != "not_enabled") throw e
        client.request("enable")
        block()
    }

    private suspend fun motion(cmd: String, args: JsonObject, timeoutMs: Long = 20_000): JsonObject =
        enabled { client.motion(cmd, args, timeoutMs) }

    /** Queue a motion without waiting; returns its id. */
    private suspend fun enqueue(cmd: String, args: JsonObject): Long =
        enabled { client.request(cmd, args) }.long("id") ?: error("no id")

    fun world(u: Double, v: Double): P2 = calibration.value.screenToWorld(u.coerceIn(0.0, 1.0), v.coerceIn(0.0, 1.0))

    private fun tapArgs(x: Double, y: Double, z: Double, arm: String?, holdMs: Int?): JsonObject = jsonOf(
        "arm" to (arm ?: "auto"), "x" to x.r(2), "y" to y.r(2), "z" to z.r(2),
        "hold_ms" to (holdMs ?: robot.defaultHoldMs),
    )

    /** Taps the world point; returns the enqueue response (with the resolved arm). */
    suspend fun tapWorld(x: Double, y: Double, z: Double = calibration.value.surfaceZ, arm: String? = null, holdMs: Int? = null): JsonObject =
        motion("tap", tapArgs(x, y, z, arm, holdMs))

    suspend fun tap(u: Double, v: Double, arm: String? = null, holdMs: Int? = null): JsonObject {
        checkUv(u, v)
        val w = world(u, v)
        return armLock.withLock {
            val z = calibration.value.surfaceZ
            val r = tapWorld(w.x, w.y, z, arm = arm, holdMs = holdMs)
            jsonOf("ok" to true, "arm" to r.str("arm"), "x" to w.x.r(), "y" to w.y.r(), "z" to z)
        }
    }

    suspend fun doubleTap(u: Double, v: Double, arm: String? = null): JsonObject {
        checkUv(u, v)
        val w = world(u, v)
        val z = calibration.value.surfaceZ
        return armLock.withLock {
            // queue both taps back-to-back with a small approach so the iPhone sees a double tap
            val args = JsonObject(tapArgs(w.x, w.y, z, arm, 40) + ("approach_mm" to JsonPrimitive(4.0)))
            val first = enabled { client.request("tap", args) }
            val armUsed = first.str("arm") ?: arm
            val second = client.request("tap", JsonObject(args + ("arm" to JsonPrimitive(armUsed ?: "auto"))))
            client.awaitDone(first.long("id")!!)
            client.awaitDone(second.long("id")!!)
            jsonOf("ok" to true, "x" to w.x.r(), "y" to w.y.r(), "arm" to armUsed)
        }
    }

    suspend fun longPress(u: Double, v: Double, ms: Int = 900, arm: String? = null): JsonObject {
        checkUv(u, v)
        val w = world(u, v)
        return armLock.withLock {
            tapWorld(w.x, w.y, arm = arm, holdMs = ms.coerceIn(300, 5000))
            jsonOf("ok" to true, "x" to w.x.r(), "y" to w.y.r(), "hold_ms" to ms)
        }
    }

    suspend fun swipe(u0: Double, v0: Double, u1: Double, v1: Double, ms: Int = 400, arm: String? = null): JsonObject {
        checkUv(u0, v0); checkUv(u1, v1)
        val a = world(u0, v0)
        val b = world(u1, v1)
        val z = calibration.value.surfaceZ
        return armLock.withLock {
            val r = motion(
                "swipe",
                jsonOf("arm" to (arm ?: "auto"), "x0" to a.x.r(), "y0" to a.y.r(), "x1" to b.x.r(), "y1" to b.y.r(), "z" to z.r(), "ms" to ms.coerceIn(80, 5000)),
                timeoutMs = 20_000L + ms,
            )
            jsonOf("ok" to true, "arm" to r.str("arm"), "from" to listOf(a.x.r(), a.y.r()), "to" to listOf(b.x.r(), b.y.r()))
        }
    }

    /**
     * Types [text] on the iPhone portrait keyboard (keyboard must already be open). Taps are pipelined
     * (up to 4 queued) to keep typing reasonably fast.
     */
    suspend fun typeText(text: String, shiftedAtStart: Boolean? = null): JsonObject {
        val kp = settings().keyboard
        val layout = KeyboardLayout(kp)
        val plan = layout.plan(text, shiftedAtStart ?: kp.autoCapitalize)
        val z = calibration.value.surfaceZ
        armLock.withLock {
            val inflight = ArrayDeque<Long>()
            for ((i, k) in plan.taps.withIndex()) {
                _progress.value = "typing ${i + 1}/${plan.taps.size}"
                val w = world(k.u, k.v)
                val id = enqueue("tap", JsonObject(tapArgs(w.x, w.y, z, null, kp.keyHoldMs) + ("approach_mm" to JsonPrimitive(5.0))))
                inflight.addLast(id)
                if (inflight.size >= 4) client.awaitDone(inflight.removeFirst())
            }
            while (inflight.isNotEmpty()) client.awaitDone(inflight.removeFirst())
            _progress.value = null
        }
        return jsonOf(
            "ok" to true, "taps" to plan.taps.size,
            "skipped" to plan.skipped.joinToString(""),
        )
    }

    /** Moves the neck (either angle may be omitted). +pan turns toward the robot's left, −tilt looks down. */
    suspend fun neck(pan: Double?, tilt: Double?, ms: Int? = null): JsonObject {
        val m = LinkedHashMap<String, JsonElement>()
        pan?.let { m["pan"] = JsonPrimitive(robot.joint("NECK_PAN").clampQ(it).r(1)) }
        tilt?.let { m["tilt"] = JsonPrimitive(robot.joint("NECK_TILT").clampQ(it).r(1)) }
        ms?.let { m["ms"] = JsonPrimitive(it) }
        motion("neck", JsonObject(m))
        return JsonObject(mapOf("ok" to JsonPrimitive(true)) + m)
    }

    suspend fun lookAt(pose: NeckPose) = neck(pose.pan, pose.tilt)

    /** Moves the head to the tray pose and returns the (rectified if calibrated) camera JPEG. */
    suspend fun lookAtDevice(): ByteArray? {
        runCatching { lookAt(settings().trayPose) }
        delay(500) // let the head settle and the camera exposure adapt
        return snapshot(true)
    }

    suspend fun lookAtUser() = lookAt(settings().userPose)

    /** Current tool position of [arm] via the body's `fk`. */
    suspend fun toolPos(arm: String): Vec3 {
        val r = client.request("fk", jsonOf("arm" to arm))
        return Vec3(r.dbl("x")!!, r.dbl("y")!!, r.dbl("z")!!)
    }

    suspend fun jog(arm: String, dx: Double, dy: Double, dz: Double): Vec3 {
        require(arm == "L" || arm == "R") { "arm must be L or R" }
        return armLock.withLock {
            val p = toolPos(arm)
            val t = Vec3(p.x + dx, p.y + dy, p.z + dz)
            val ik = Kinematics.ik(robot, arm, t.x, t.y, t.z)
            if (!ik.ok) throw BodyException(ik.err!!, "target (${t.x.r()}, ${t.y.r()}, ${t.z.r()}) out of range")
            motion("move", jsonOf("arm" to arm, "x" to t.x.r(2), "y" to t.y.r(2), "z" to t.z.r(2), "ms" to 250))
            runCatching { toolPos(arm) }.getOrDefault(t)
        }
    }

    /**
     * Touch-probe auto calibration (SPEC §6), result-compatible with host/tappet/calibration.py:
     * with the target phone showing touchpad.html full-screen, first probe the surface height at the
     * screen centre (lowering z in 1 mm steps until a touch registers), then tap a 3×3 grid of world
     * points predicted by the current calibration, record where each touch registered, and fit H_sw
     * (touched uv → commanded world xy) by least squares (normalized DLT). The result is persisted.
     * Returns `{ok, calibration, points:[{u,v,x,y,tu,tv,err_mm}], rms_mm, max_mm, surface_z}`.
     */
    suspend fun autoCalibrate(arm: String? = null): JsonObject = armLock.withLock {
        longOp = true
        try {
            val cal0 = calibration.value
            val center = cal0.screenToWorld(0.5, 0.5)
            var z = cal0.surfaceZ + 1.0
            var surfaceZ: Double? = null
            for (step in 0 until 6) {
                _progress.value = "probing surface z=${z.r(1)} mm"
                val seq = touches.lastSeq
                tapWorld(center.x, center.y, z, arm, 80)
                if (touches.waitFor(seq, 2500) != null) {
                    surfaceZ = z
                    break
                }
                z -= 1.0
            }
            if (surfaceZ == null) {
                throw BodyException(
                    "no_touch",
                    "no touch registered at the screen centre down to z=${(z + 1).r(1)} mm; is touchpad.html open full-screen on the target phone?",
                )
            }
            val vals = listOf(0.15, 0.5, 0.85)
            val grid = vals.flatMap { v -> vals.map { u -> P2(u, v) } }
            val src = ArrayList<P2>()
            val dst = ArrayList<P2>()
            val points = ArrayList<MutableMap<String, Any?>>()
            var touchSize: P2? = null
            for ((i, uv) in grid.withIndex()) {
                _progress.value = "calibration tap ${i + 1}/${grid.size}"
                val w = cal0.screenToWorld(uv.x, uv.y)
                val seq = touches.lastSeq
                tapWorld(w.x, w.y, surfaceZ, arm, 80)
                val ev = touches.waitFor(seq, 4000) ?: continue
                touchSize = P2(ev.w, ev.h)
                src += P2(ev.u, ev.v)
                dst += w
                val ex = (ev.u - uv.x) * cal0.device.screenW
                val ey = (ev.v - uv.y) * cal0.device.screenL
                points += mutableMapOf(
                    "u" to uv.x, "v" to uv.y, "x" to w.x, "y" to w.y, "tu" to ev.u, "tv" to ev.v, "err_mm" to hypot(ex, ey),
                )
            }
            if (src.size < 4) {
                throw BodyException("no_touch", "only ${src.size} of ${grid.size} probe taps registered; is touchpad.html open full-screen on the target phone?")
            }
            val h = Homography.fit(src, dst)
            val (rms, _) = h.residuals(src, dst)
            val cal = cal0.copy(
                hSw = h, surfaceZ = surfaceZ, source = "auto", rmsMm = rms, touchSize = touchSize,
                updated = dev.tappet.head.calib.ScreenCalibration.now(),
            )
            calibration.set(cal)
            jsonOf(
                "ok" to true, "calibration" to cal.toJson(), "points" to points, "rms_mm" to rms,
                "max_mm" to points.maxOf { it["err_mm"] as Double }, "surface_z" to surfaceZ,
            )
        } finally {
            longOp = false
            _progress.value = null
        }
    }

    /**
     * Accuracy test: [n] taps at random screen targets compared with the touches reported by the
     * touchpad page. Result-compatible with host/tappet/taptest.py:
     * `{n, registered, registration_rate, mean_mm, p95_mm, max_mm, cycle_s, duration_s, arm, samples:[...]}`.
     */
    suspend fun tapTest(n: Int = 100, arm: String? = null, seed: Long = System.currentTimeMillis()): JsonObject = armLock.withLock {
        longOp = true
        try {
            val rnd = Random(seed)
            val cal = calibration.value
            val dev = cal.device
            val errs = ArrayList<Double>()
            val samples = ArrayList<Map<String, Any?>>()
            val count = n.coerceIn(1, 2000)
            val t0 = System.currentTimeMillis()
            for (i in 0 until count) {
                _progress.value = "tap test ${i + 1}/$count"
                val ts = System.currentTimeMillis()
                val u = 0.08 + rnd.nextDouble() * 0.84
                val v = 0.06 + rnd.nextDouble() * 0.88
                val w = cal.screenToWorld(u, v)
                val seq = touches.lastSeq
                var usedArm: String? = null
                var error: String? = null
                var ev: dev.tappet.head.calib.TouchEvent? = null
                try {
                    usedArm = tapWorld(w.x, w.y, cal.surfaceZ, arm).str("arm")
                    ev = touches.waitFor(seq, 1500)
                } catch (e: BodyException) {
                    error = e.message
                }
                var dx: Double? = null
                var dy: Double? = null
                var err: Double? = null
                if (ev != null) {
                    dx = (ev.u - u) * dev.screenW
                    dy = (ev.v - v) * dev.screenL
                    err = hypot(dx, dy)
                    errs += err
                }
                samples += linkedMapOf(
                    "i" to i, "u" to u.r(5), "v" to v.r(5), "arm" to usedArm, "x" to w.x.r(), "y" to w.y.r(),
                    "registered" to (ev != null), "tu" to ev?.u?.r(5), "tv" to ev?.v?.r(5),
                    "dx_mm" to dx?.r(3), "dy_mm" to dy?.r(3), "err_mm" to err?.r(3),
                    "cycle_s" to ((System.currentTimeMillis() - ts) / 1000.0).r(3), "error" to error,
                )
            }
            val duration = (System.currentTimeMillis() - t0) / 1000.0
            val reg = errs.size
            val sorted = errs.sorted()
            fun pct(p: Double): Double? = if (reg == 0) null else {
                val k = (reg - 1) * p
                val lo = kotlin.math.floor(k).toInt()
                val hi = kotlin.math.ceil(k).toInt()
                sorted[lo] + (sorted[hi] - sorted[lo]) * (k - lo)
            }
            jsonOf(
                "n" to count,
                "registered" to reg,
                "registration_rate" to reg.toDouble() / count,
                "mean_mm" to if (reg > 0) errs.average() else null,
                "p95_mm" to pct(0.95),
                "max_mm" to sorted.lastOrNull(),
                "rms_mm" to if (reg > 0) sqrt(errs.sumOf { it * it } / reg) else null,
                "cycle_s" to duration / count,
                "duration_s" to duration,
                "arm" to (arm ?: "auto"),
                "calibration_source" to cal.source,
                "samples" to samples,
            )
        } finally {
            longOp = false
            _progress.value = null
        }
    }
}
