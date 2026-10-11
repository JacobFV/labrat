package dev.tappet.head.config

import dev.tappet.head.util.JsonX
import dev.tappet.head.util.dbl
import dev.tappet.head.util.int
import dev.tappet.head.util.obj
import dev.tappet.head.util.str
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/** Per-arm geometry (robot.json `arms.<A>`), SPEC §3. */
data class ArmGeom(
    val baseX: Double, val baseY: Double, val shoulderH: Double, val r0: Double,
    val l1: Double, val l2: Double, val toolDx: Double, val toolDz: Double,
)

/** Joint map entry (robot.json `joints.<NAME>`), SPEC §2. */
data class JointCfg(
    val name: String, val ch: Int, val gear: Double, val dir: Double, val sZero: Double,
    val neutral: Double, val min: Double, val max: Double, val home: Double, val park: Double, val maxVel: Double,
) {
    fun clampQ(q: Double) = q.coerceIn(min, max)
    /** Servo angle s for joint angle q (SPEC §2). */
    fun servoDeg(q: Double): Double = (sZero + dir * gear * (clampQ(q) - neutral)).coerceIn(0.0, 180.0)
    fun pulseUs(q: Double): Double = 500.0 + servoDeg(q) * 2000.0 / 180.0
}

data class DeviceProfile(
    val name: String,
    val bodyW: Double, val bodyL: Double, val bodyT: Double,
    val screenW: Double, val screenL: Double,
    val screenPxW: Int, val screenPxH: Int,
    val surfaceZ: Double,
)

data class TrayCfg(val floorZ: Double, val centerX: Double, val centerY: Double, val pocketW: Double, val pocketL: Double)
data class NeckPose(val pan: Double, val tilt: Double)

data class RobotConfig(
    val arms: Map<String, ArmGeom>,
    val joints: Map<String, JointCfg>,
    val relMin: Double,
    val relMax: Double,
    val tray: TrayCfg,
    val defaultDevice: DeviceProfile,
    val lookAtTray: NeckPose,
    val lookAtUser: NeckPose,
    val aoaManufacturer: String,
    val aoaModel: String,
    val uartBaud: Int,
    val simTcpPort: Int,
    val webPort: Int,
    val watchdogMs: Int,
    val defaultPressMm: Double,
    val defaultApproachMm: Double,
    val defaultHoldMs: Int,
    val raw: JsonObject,
) {
    fun arm(a: String): ArmGeom = arms[a] ?: error("unknown arm $a")
    fun joint(n: String): JointCfg = joints[n] ?: error("unknown joint $n")

    companion object {
        fun parse(text: String): RobotConfig {
            val root = JsonX.parseToJsonElement(text).jsonObject
            val arms = root.obj("arms")!!.mapValues { (_, v) ->
                val o = v.jsonObject
                ArmGeom(
                    o.dbl("base_x")!!, o.dbl("base_y")!!, o.dbl("shoulder_h")!!, o.dbl("r0")!!,
                    o.dbl("L1")!!, o.dbl("L2")!!, o.dbl("tool_dx")!!, o.dbl("tool_dz")!!,
                )
            }
            val joints = root.obj("joints")!!.mapValues { (k, v) ->
                val o = v.jsonObject
                JointCfg(
                    k, o.int("ch")!!, o.dbl("gear")!!, o.dbl("dir")!!, o.dbl("s_zero")!!, o.dbl("neutral")!!,
                    o.dbl("min")!!, o.dbl("max")!!, o.dbl("home")!!, o.dbl("park")!!, o.dbl("max_vel")!!,
                )
            }
            val link = root.obj("linkage")!!
            val t = root.obj("tray")!!
            val d = root.obj("default_device")!!
            val neck = root.obj("neck")!!
            val lt = neck.obj("look_at_tray")!!
            val lu = neck.obj("look_at_user")!!
            val l = root.obj("link")!!
            val aoa = l.obj("aoa")!!
            val tool = root.obj("tool")
            return RobotConfig(
                arms = arms,
                joints = joints,
                relMin = link.dbl("rel_min")!!, relMax = link.dbl("rel_max")!!,
                tray = TrayCfg(t.dbl("floor_z")!!, t.dbl("center_x")!!, t.dbl("center_y")!!, t.dbl("pocket_w")!!, t.dbl("pocket_l")!!),
                defaultDevice = DeviceProfile(
                    d.str("name")!!, d.dbl("body_w")!!, d.dbl("body_l")!!, d.dbl("body_t")!!,
                    d.dbl("screen_w")!!, d.dbl("screen_l")!!, d.int("screen_px_w")!!, d.int("screen_px_h")!!, d.dbl("surface_z")!!,
                ),
                lookAtTray = NeckPose(lt.dbl("pan")!!, lt.dbl("tilt")!!),
                lookAtUser = NeckPose(lu.dbl("pan")!!, lu.dbl("tilt")!!),
                aoaManufacturer = aoa.str("manufacturer")!!,
                aoaModel = aoa.str("model")!!,
                uartBaud = l.int("uart_baud") ?: 115200,
                simTcpPort = l.int("sim_tcp_port") ?: 7777,
                webPort = l.int("web_port") ?: 8080,
                watchdogMs = l.int("watchdog_ms") ?: 5000,
                defaultPressMm = tool?.dbl("default_press_mm") ?: 2.5,
                defaultApproachMm = tool?.dbl("default_approach_mm") ?: 8.0,
                defaultHoldMs = tool?.dbl("default_hold_ms")?.toInt() ?: 60,
                raw = root,
            )
        }
    }
}

/** Common target-phone profiles. surface_z = tray floor + body thickness. Dimensions in mm (approximate screen active area). */
object DeviceProfiles {
    fun presets(cfg: RobotConfig): List<DeviceProfile> {
        val floor = cfg.tray.floorZ
        fun p(name: String, bw: Double, bl: Double, bt: Double, sw: Double, sl: Double, pw: Int, ph: Int) =
            DeviceProfile(name, bw, bl, bt, sw, sl, pw, ph, floor + bt)
        return listOf(
            cfg.defaultDevice,
            p("iPhone 15/16 Plus, 15/16 Pro Max (6.7in)", 77.8, 160.9, 7.8, 71.2, 154.4, 1290, 2796),
            p("iPhone 12/13/14 (6.1in)", 71.5, 146.7, 7.65, 64.6, 139.9, 1170, 2532),
            p("iPhone 12/13 mini (5.4in)", 64.2, 131.5, 7.65, 57.4, 124.4, 1080, 2340),
            p("iPhone SE 2/3 (4.7in)", 67.3, 138.4, 7.3, 58.5, 104.1, 750, 1334),
            p("Generic Android 6.4in", 74.0, 158.0, 8.2, 68.0, 151.0, 1080, 2400),
        )
    }
}
