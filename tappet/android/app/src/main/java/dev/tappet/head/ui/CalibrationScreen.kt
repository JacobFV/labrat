package dev.tappet.head.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.tappet.head.calib.P2
import dev.tappet.head.camera.CameraController
import dev.tappet.head.service.HeadController
import dev.tappet.head.util.dbl
import dev.tappet.head.util.r
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** Arm jogging, servo enable/home, touch-probe auto calibration, tap test and camera-corner calibration. */
@Composable
fun CalibrationScreen(c: HeadController, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val cal by c.calibration.flow.collectAsState()
    val progress by c.actions.progress.collectAsState()
    val link by c.client.status.collectAsState()
    var msg by remember { mutableStateOf("") }
    var arm by remember { mutableStateOf("L") }
    var step by remember { mutableStateOf(5.0) }
    var pos by remember { mutableStateOf<String?>(null) }
    var running by remember { mutableStateOf(false) }
    var snapshot by remember { mutableStateOf<Bitmap?>(null) }
    val corners = remember { mutableStateListOf<P2>() }
    var rectified by remember { mutableStateOf<Bitmap?>(null) }

    fun run(label: String, block: suspend () -> String) {
        if (running) return
        running = true
        msg = "$label…"
        scope.launch {
            msg = try { block() } catch (e: Exception) { "$label failed: ${e.message}" }
            running = false
        }
    }

    ScreenScaffold("Calibration", onBack) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())) {
            Text("Body: ${if (link.connected) "connected (${link.transport})" else "offline"} · calibration: ${cal.source}" +
                (cal.rmsMm?.let { " rms ${it.r(2)} mm" } ?: "") + " · surface z ${cal.surfaceZ.r(1)} mm", fontSize = 13.sp)
            if (progress != null) Text(progress!!, color = Accent)
            if (msg.isNotBlank()) Text(msg, color = Accent, fontSize = 13.sp)

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Servos", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("enable", "relax", "home", "stop").forEach { cmd ->
                    OutlinedButton(onClick = {
                        run(cmd) {
                            if (cmd == "home") c.client.motion("home") else c.client.request(cmd)
                            "$cmd ok"
                        }
                    }) { Text(cmd) }
                }
            }

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Jog arm", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("L", "R").forEach { a -> FilterChip(arm == a, { arm = a }, label = { Text("Arm $a") }) }
                listOf(1.0, 5.0, 10.0).forEach { s -> FilterChip(step == s, { step = s }, label = { Text("${s.toInt()} mm") }) }
            }
            pos?.let { Text("Tool: $it", fontSize = 13.sp) }
            val jog: (Double, Double, Double) -> Unit = { dx, dy, dz ->
                run("jog") {
                    val p = c.actions.jog(arm, dx, dy, dz)
                    pos = "x ${p.x.r(1)}  y ${p.y.r(1)}  z ${p.z.r(1)}"
                    "jog ok"
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton(onClick = { jog(-step, 0.0, 0.0) }) { Text("X−") }
                OutlinedButton(onClick = { jog(step, 0.0, 0.0) }) { Text("X+") }
                OutlinedButton(onClick = { jog(0.0, -step, 0.0) }) { Text("Y−") }
                OutlinedButton(onClick = { jog(0.0, step, 0.0) }) { Text("Y+") }
                OutlinedButton(onClick = { jog(0.0, 0.0, -step) }) { Text("Z−") }
                OutlinedButton(onClick = { jog(0.0, 0.0, step) }) { Text("Z+") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton(onClick = {
                    run("read position") {
                        val p = c.actions.toolPos(arm)
                        pos = "x ${p.x.r(1)}  y ${p.y.r(1)}  z ${p.z.r(1)}"
                        "ok"
                    }
                }) { Text("Read position") }
                OutlinedButton(onClick = {
                    run("set surface") {
                        val p = c.actions.toolPos(arm)
                        c.calibration.set(c.calibration.value.copy(surfaceZ = p.z, source = "manual", updated = dev.tappet.head.calib.ScreenCalibration.now()))
                        "surface_z set to ${p.z.r(2)} mm (stylus tip touching glass)"
                    }
                }) { Text("Set surface z here") }
            }

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Touch-probe auto calibration", style = MaterialTheme.typography.titleMedium)
            Text("Open ${c.webUrl()}/touchpad.html full-screen on the target phone in the tray, then run.", fontSize = 13.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(enabled = !running, onClick = {
                    run("auto calibration") {
                        val r = c.actions.autoCalibrate()
                        "calibrated: rms ${r.dbl("rms_mm")?.r(2)} mm, max ${r.dbl("max_mm")?.r(2)} mm, surface z ${r.dbl("surface_z")?.r(1)}"
                    }
                }) { Text("Run auto calibration") }
                OutlinedButton(enabled = !running, onClick = {
                    run("tap test") { summary(c.actions.tapTest(20)) }
                }) { Text("Tap test ×20") }
                OutlinedButton(enabled = !running, onClick = {
                    run("tap test") { summary(c.actions.tapTest(100)) }
                }) { Text("×100") }
            }
            OutlinedButton(onClick = { c.calibration.resetToDefault(c.currentProfile()); msg = "reset to default for ${c.currentProfile().name}" }) {
                Text("Reset screen calibration to default")
            }

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Camera → screen (tap the 4 screen corners: TL, TR, BR, BL)", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedButton(onClick = { run("look at tray") { c.actions.lookAt(c.settings.value.trayPose); "looking at tray" } }) { Text("Look at tray") }
                OutlinedButton(onClick = {
                    val b = c.camera.latestBitmap()
                    if (b == null) msg = "no camera frame (camera permission?)" else {
                        snapshot = b.copy(Bitmap.Config.ARGB_8888, false); corners.clear(); rectified = null
                    }
                }) { Text("Snapshot") }
                OutlinedButton(enabled = corners.isNotEmpty(), onClick = { corners.removeAt(corners.size - 1) }) { Text("Undo") }
                Button(enabled = corners.size == 4 && snapshot != null, onClick = {
                    val b = snapshot!!
                    val px = corners.map { P2(it.x * b.width, it.y * b.height) }
                    try {
                        c.calibration.set(c.calibration.value.withCameraCorners(px, P2(b.width.toDouble(), b.height.toDouble())))
                        rectified = CameraController.rectify(b, corners.toList(), cal.device.screenW, cal.device.screenL, 640)
                        msg = "camera homography saved"
                    } catch (e: Exception) {
                        msg = "invalid corners: ${e.message}"
                    }
                }) { Text("Save corners") }
            }
            snapshot?.let { bmp ->
                val img = remember(bmp) { bmp.asImageBitmap() }
                Box(Modifier.fillMaxWidth().heightIn(max = 520.dp).aspectRatio(bmp.width.toFloat() / bmp.height)) {
                    Image(img, null, Modifier.fillMaxSize(), contentScale = ContentScale.FillBounds)
                    Canvas(
                        Modifier.fillMaxSize().pointerInput(bmp) {
                            detectTapGestures { o ->
                                if (corners.size < 4) corners.add(P2((o.x / size.width).toDouble(), (o.y / size.height).toDouble()))
                            }
                        }
                    ) {
                        val pts = corners.map { Offset((it.x * size.width).toFloat(), (it.y * size.height).toFloat()) }
                        pts.forEachIndexed { i, p ->
                            drawCircle(Accent, 14f, p, style = Stroke(4f))
                            if (i > 0) drawLine(Accent, pts[i - 1], p, 3f)
                        }
                        if (pts.size == 4) drawLine(Accent, pts[3], pts[0], 3f)
                    }
                }
                Text("Next: ${listOf("top-left", "top-right", "bottom-right", "bottom-left").getOrElse(corners.size) { "save" }}", fontSize = 13.sp)
            }
            rectified?.let { r ->
                Text("Rectified preview", fontSize = 13.sp)
                Image(r.asImageBitmap(), null, Modifier.heightIn(max = 400.dp).aspectRatio(r.width.toFloat() / r.height))
            }
        }
    }
}

private fun summary(r: JsonObject): String =
    "tap test: ${r["registered"]}/${r["n"]} registered, mean ${r.dbl("mean_mm")?.r(2)} mm, p95 ${r.dbl("p95_mm")?.r(2)} mm, max ${r.dbl("max_mm")?.r(2)} mm"
