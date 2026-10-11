package dev.tappet.head

import dev.tappet.head.config.RobotConfig
import dev.tappet.head.kinematics.ArmQ
import dev.tappet.head.kinematics.Kinematics
import dev.tappet.head.util.str
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class KinematicsTest {
    private val cfg = RobotConfig.parse(TestFiles.robotJson())

    @Test
    fun sharedVectors() {
        val root = Json.parseToJsonElement(TestFiles.repoFile("config/kinematics_vectors.json").readText()).jsonObject
        val vectors = root["vectors"]!!.jsonArray
        assertTrue(vectors.size > 10)
        var checked = 0
        for (ve in vectors) {
            val v = ve.jsonObject
            val arm = v.str("arm")!!
            val xyz = v["xyz"]!!.jsonArray.map { it.jsonPrimitive.double }
            val err = v.str("err")
            val res = Kinematics.ik(cfg, arm, xyz[0], xyz[1], xyz[2])
            assertEquals("err for $v", err, res.err)
            val qExp = v["q"]?.jsonArray?.map { it.jsonPrimitive.double }
            if (qExp != null) {
                val q = assertNotNullAnd(res.q, "q for $v")
                assertEquals("yaw $v", qExp[0], q.yaw, 1e-4)
                assertEquals("shoulder $v", qExp[1], q.shoulder, 1e-4)
                assertEquals("elbow $v", qExp[2], q.elbow, 1e-4)
                val fkExp = v["fk"]!!.jsonArray.map { it.jsonPrimitive.double }
                val p = Kinematics.fk(cfg, arm, ArmQ(qExp[0], qExp[1], qExp[2]))
                assertEquals(fkExp[0], p.x, 1e-3)
                assertEquals(fkExp[1], p.y, 1e-3)
                assertEquals(fkExp[2], p.z, 1e-3)
            } else {
                assertNull(res.q)
            }
            checked++
        }
        assertEquals(vectors.size, checked)
    }

    @Test
    fun roundTripOverTray() {
        val z = cfg.defaultDevice.surfaceZ
        for (x in listOf(-30.0, 0.0, 30.0)) for (y in listOf(100.0, 160.0, 220.0)) {
            val arm = Kinematics.autoArm(cfg, x, y, z)
            assertNotNull("reachable $x,$y", arm)
            val q = Kinematics.ik(cfg, arm!!, x, y, z).q!!
            val p = Kinematics.fk(cfg, arm, q)
            assertEquals(x, p.x, 1e-6); assertEquals(y, p.y, 1e-6); assertEquals(z, p.z, 1e-6)
        }
    }

    @Test
    fun servoMapping() {
        val j = cfg.joint("NECK_PAN")
        assertEquals(1500.0, j.pulseUs(0.0), 1e-9)
        // q clamps to min (-70): s = 90 + (-70 - -20) = 40
        assertEquals(500.0 + 40.0 * 2000.0 / 180.0, cfg.joint("NECK_TILT").pulseUs(-200.0), 1e-9)
    }

    private fun <T> assertNotNullAnd(v: T?, msg: String): T {
        assertNotNull(msg, v); return v!!
    }
}
