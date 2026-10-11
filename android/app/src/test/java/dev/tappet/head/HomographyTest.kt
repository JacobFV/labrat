package dev.tappet.head

import dev.tappet.head.calib.Homography
import dev.tappet.head.calib.P2
import dev.tappet.head.calib.ScreenCalibration
import dev.tappet.head.config.RobotConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random
import kotlin.math.hypot

class HomographyTest {
    private val truth = Homography(doubleArrayOf(1.2, 0.1, 30.0, -0.05, 0.9, 12.0, 0.0004, -0.0002, 1.0))

    @Test
    fun exactFourPoints() {
        val src = listOf(P2(0.0, 0.0), P2(100.0, 0.0), P2(100.0, 200.0), P2(0.0, 200.0))
        val dst = src.map { truth.apply(it) }
        val h = Homography.fit(src, dst)
        for (p in listOf(P2(50.0, 50.0), P2(10.0, 190.0), P2(73.0, 3.0))) {
            val a = h.apply(p); val b = truth.apply(p)
            assertEquals(b.x, a.x, 1e-6); assertEquals(b.y, a.y, 1e-6)
        }
    }

    @Test
    fun leastSquaresWithNoise() {
        val rnd = Random(42)
        val src = (0 until 9).map { P2((it % 3) * 40.0 + 10.0, (it / 3) * 40.0 + 10.0) }
        val dst = src.map { truth.apply(it).let { p -> P2(p.x + rnd.nextGaussian() * 0.3, p.y + rnd.nextGaussian() * 0.3) } }
        val h = Homography.fit(src, dst)
        val (rms, _) = h.residuals(src, dst)
        assertTrue("rms $rms", rms < 0.6)
        val c = h.apply(50.0, 50.0); val t = truth.apply(50.0, 50.0)
        assertTrue(hypot(c.x - t.x, c.y - t.y) < 0.6)
    }

    @Test
    fun inverseAndCompose() {
        val inv = truth.inverse()
        val p = P2(12.0, -7.0)
        val q = inv.apply(truth.apply(p))
        assertEquals(p.x, q.x, 1e-9); assertEquals(p.y, q.y, 1e-9)
        val id = truth.compose(inv)
        val r = id.apply(3.0, 4.0)
        assertEquals(3.0, r.x, 1e-9); assertEquals(4.0, r.y, 1e-9)
    }

    @Test
    fun defaultScreenCalibrationOrientation() {
        val cfg = RobotConfig.parse(TestFiles.robotJson())
        val cal = ScreenCalibration.default(cfg)
        val d = cfg.defaultDevice
        val c = cal.screenToWorld(0.5, 0.5)
        assertEquals(cfg.tray.centerX, c.x, 1e-9); assertEquals(cfg.tray.centerY, c.y, 1e-9)
        val tl = cal.screenToWorld(0.0, 0.0) // top-left: robot's left (−x), far side (+y)
        assertEquals(-d.screenW / 2, tl.x, 1e-9); assertEquals(cfg.tray.centerY + d.screenL / 2, tl.y, 1e-9)
        val back = cal.worldToScreen(tl.x, tl.y)
        assertEquals(0.0, back.x, 1e-9); assertEquals(0.0, back.y, 1e-9)
        // JSON round trip
        val again = ScreenCalibration.fromJson(cal.toJson(), cal)
        val p = again.screenToWorld(0.3, 0.8); val q = cal.screenToWorld(0.3, 0.8)
        assertEquals(q.x, p.x, 1e-9); assertEquals(q.y, p.y, 1e-9)
    }

    @Test
    fun cameraHomographyFromCorners() {
        val cfg = RobotConfig.parse(TestFiles.robotJson())
        val px = listOf(P2(300.0, 200.0), P2(700.0, 220.0), P2(750.0, 900.0), P2(250.0, 880.0))
        val cal = ScreenCalibration.default(cfg).withCameraCorners(px, P2(1000.0, 1000.0))
        val uv = cal.hIc!!.apply(px[2])
        assertEquals(1.0, uv.x, 1e-9); assertEquals(1.0, uv.y, 1e-9)
        val norm = cal.cameraCornersNormalized()!!
        assertEquals(0.25, norm[3].x, 1e-9); assertEquals(0.88, norm[3].y, 1e-9)
    }

    @Test
    fun mergeLikeBridge() {
        val cfg = RobotConfig.parse(TestFiles.robotJson())
        val base = ScreenCalibration.default(cfg)
        val json = base.toJson()
        // H_sw serialized as 3x3 nested array, like host/tappet/calibration.py
        assertEquals(3, (json["H_sw"] as kotlinx.serialization.json.JsonArray).size)
        val patch = kotlinx.serialization.json.Json.parseToJsonElement(
            """{"surface_z": 12.5, "camera_corners": [[10,10],[90,10],[90,190],[10,190]], "camera_size": [100,200]}"""
        ) as kotlinx.serialization.json.JsonObject
        val m = ScreenCalibration.merge(base, patch, { ScreenCalibration.default(cfg, it) }, { null })
        assertEquals(12.5, m.surfaceZ, 1e-12)
        assertEquals("manual", m.source)
        val uv = m.hIc!!.apply(50.0, 100.0)
        assertEquals(0.5, uv.x, 1e-9); assertEquals(0.5, uv.y, 1e-9)
        val reset = ScreenCalibration.merge(m, kotlinx.serialization.json.Json.parseToJsonElement("""{"reset":true}""") as kotlinx.serialization.json.JsonObject,
            { ScreenCalibration.default(cfg, it) }, { null })
        assertEquals("default", reset.source)
        assertEquals(null, reset.hIc)
    }
}
