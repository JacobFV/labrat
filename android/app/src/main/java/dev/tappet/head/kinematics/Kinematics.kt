package dev.tappet.head.kinematics

import dev.tappet.head.config.ArmGeom
import dev.tappet.head.config.RobotConfig
import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/** Joint triple for one arm: yaw ψ, shoulder θ1, absolute forearm θ2 (degrees). */
data class ArmQ(val yaw: Double, val shoulder: Double, val elbow: Double) {
    fun toArray() = doubleArrayOf(yaw, shoulder, elbow)
}

data class Vec3(val x: Double, val y: Double, val z: Double)

/** IK result; [err] is null, "unreachable" or "limit" (limit still carries [q]). */
data class IkResult(val q: ArmQ?, val err: String?) {
    val ok get() = err == null
}

/** Port of SPEC §3 (identical to firmware and Python). */
object Kinematics {
    private const val D2R = Math.PI / 180.0
    private const val R2D = 180.0 / Math.PI

    fun fk(g: ArmGeom, q: ArmQ): Vec3 {
        val t1 = q.shoulder * D2R
        val t2 = q.elbow * D2R
        val psi = q.yaw * D2R
        val r = g.r0 + g.l1 * cos(t1) + g.l2 * cos(t2) + g.toolDx
        val z = g.shoulderH + g.l1 * sin(t1) + g.l2 * sin(t2) - g.toolDz
        return Vec3(g.baseX - r * sin(psi), g.baseY + r * cos(psi), z)
    }

    /** Pure geometric IK (elbow-up). Returns null when unreachable. */
    fun ikRaw(g: ArmGeom, x: Double, y: Double, z: Double): ArmQ? {
        val dx = x - g.baseX
        val dy = y - g.baseY
        val psi = atan2(-dx, dy)
        val r = hypot(dx, dy) - g.r0 - g.toolDx
        val zp = z - g.shoulderH + g.toolDz
        val d = hypot(r, zp)
        if (d < abs(g.l1 - g.l2) - 1e-9 || d > g.l1 + g.l2 + 1e-9 || d < 1e-9) return null
        val phi = atan2(zp, r)
        val ca = ((g.l1 * g.l1 + d * d - g.l2 * g.l2) / (2 * g.l1 * d)).coerceIn(-1.0, 1.0)
        val cb = ((g.l2 * g.l2 + d * d - g.l1 * g.l1) / (2 * g.l2 * d)).coerceIn(-1.0, 1.0)
        val alpha = acos(ca)
        val beta = acos(cb)
        return ArmQ(psi * R2D, (phi + alpha) * R2D, (phi - beta) * R2D)
    }

    /** Full IK with joint-limit and linkage checks for arm "L" or "R". */
    fun ik(cfg: RobotConfig, arm: String, x: Double, y: Double, z: Double): IkResult {
        val q = ikRaw(cfg.arm(arm), x, y, z) ?: return IkResult(null, "unreachable")
        return IkResult(q, if (withinLimits(cfg, arm, q)) null else "limit")
    }

    fun withinLimits(cfg: RobotConfig, arm: String, q: ArmQ): Boolean {
        val eps = 1e-6
        fun inside(name: String, v: Double): Boolean {
            val j = cfg.joint("${arm}_$name")
            return v >= j.min - eps && v <= j.max + eps
        }
        val rel = q.shoulder - q.elbow
        return inside("YAW", q.yaw) && inside("SHOULDER", q.shoulder) && inside("ELBOW", q.elbow) &&
            rel >= cfg.relMin - eps && rel <= cfg.relMax + eps
    }

    fun fk(cfg: RobotConfig, arm: String, q: ArmQ): Vec3 = fk(cfg.arm(arm), q)

    /** SPEC §4 `auto` arm: same side as x (x<0 → L), falling back to the other arm if unreachable. */
    fun autoArm(cfg: RobotConfig, x: Double, y: Double, z: Double): String? {
        val first = if (x < 0) "L" else "R"
        val second = if (first == "L") "R" else "L"
        return listOf(first, second).firstOrNull { ik(cfg, it, x, y, z).ok }
    }
}
