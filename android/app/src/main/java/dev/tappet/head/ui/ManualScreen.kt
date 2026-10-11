package dev.tappet.head.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.tappet.head.service.HeadController
import dev.tappet.head.util.r
import kotlinx.coroutines.launch

private enum class TapMode { TAP, DOUBLE, LONG }

/** Manual control: tap/swipe on a virtual phone outline, neck sliders, typing. */
@Composable
fun ManualScreen(c: HeadController, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val cal by c.calibration.flow.collectAsState()
    var mode by remember { mutableStateOf(TapMode.TAP) }
    var msg by remember { mutableStateOf("") }
    var last by remember { mutableStateOf<Offset?>(null) }
    var dragStart by remember { mutableStateOf<Offset?>(null) }
    var dragEnd by remember { mutableStateOf<Offset?>(null) }
    var bg by remember { mutableStateOf<ImageBitmap?>(null) }
    var text by remember { mutableStateOf("") }
    var pan by remember { mutableFloatStateOf(c.settings.value.userPose.pan.toFloat()) }
    var tilt by remember { mutableFloatStateOf(c.settings.value.userPose.tilt.toFloat()) }

    fun act(label: String, block: suspend () -> Unit) {
        msg = "$label…"
        scope.launch { msg = try { block(); "$label ✓" } catch (e: Exception) { "$label failed: ${e.message}" } }
    }

    ScreenScaffold("Manual control", onBack) {
        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            // virtual phone
            Box(
                Modifier.fillMaxHeight().aspectRatio((cal.device.screenW / cal.device.screenL).toFloat())
                    .border(3.dp, Color.Gray, RoundedCornerShape(18.dp)).padding(6.dp)
            ) {
                bg?.let { Image(it, null, Modifier.fillMaxSize(), contentScale = ContentScale.FillBounds, alpha = 0.85f) }
                Canvas(
                    Modifier.fillMaxSize()
                        .pointerInput(mode) {
                            detectTapGestures { o ->
                                val u = (o.x / size.width).toDouble()
                                val v = (o.y / size.height).toDouble()
                                last = o
                                when (mode) {
                                    TapMode.TAP -> act("tap ${u.r(3)},${v.r(3)}") { c.actions.tap(u, v) }
                                    TapMode.DOUBLE -> act("double tap") { c.actions.doubleTap(u, v) }
                                    TapMode.LONG -> act("long press") { c.actions.longPress(u, v) }
                                }
                            }
                        }
                        .pointerInput(Unit) {
                            detectDragGestures(
                                onDragStart = { dragStart = it; dragEnd = it },
                                onDrag = { ch, d -> ch.consume(); dragEnd = (dragEnd ?: Offset.Zero) + d },
                                onDragEnd = {
                                    val a = dragStart
                                    val b = dragEnd
                                    if (a != null && b != null) {
                                        val w = size.width.toDouble()
                                        val h = size.height.toDouble()
                                        act("swipe") {
                                            c.actions.swipe(
                                                (a.x / w).coerceIn(0.0, 1.0), (a.y / h).coerceIn(0.0, 1.0),
                                                (b.x / w).coerceIn(0.0, 1.0), (b.y / h).coerceIn(0.0, 1.0),
                                            )
                                        }
                                    }
                                },
                            )
                        }
                ) {
                    last?.let { drawCircle(Accent, 18f, it) }
                    val a = dragStart
                    val b = dragEnd
                    if (a != null && b != null) drawLine(Accent.copy(alpha = 0.7f), a, b, 6f)
                }
            }
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                Text(msg, color = Accent, fontSize = 13.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    TapMode.entries.forEach { m -> FilterChip(mode == m, { mode = m }, label = { Text(m.name.lowercase()) }) }
                }
                Text("Tap the outline to tap the phone; drag to swipe.", fontSize = 12.sp, color = Color.Gray)
                OutlinedButton(onClick = {
                    act("snapshot") {
                        val jpg = c.actions.lookAtDevice() ?: error("no camera image")
                        bg = BitmapFactory.decodeByteArray(jpg, 0, jpg.size).asImageBitmap()
                    }
                }) { Text("Look at device (background)") }
                Text("Neck pan ${pan.toInt()}°", fontSize = 13.sp)
                Slider(pan, { pan = it }, valueRange = -80f..80f, onValueChangeFinished = { act("neck") { c.actions.neck(pan.toDouble(), tilt.toDouble()) } })
                Text("Neck tilt ${tilt.toInt()}°", fontSize = 13.sp)
                Slider(tilt, { tilt = it }, valueRange = -70f..30f, onValueChangeFinished = { act("neck") { c.actions.neck(pan.toDouble(), tilt.toDouble()) } })
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedButton(onClick = { act("look at tray") { c.actions.lookAt(c.settings.value.trayPose) } }) { Text("Tray") }
                    OutlinedButton(onClick = { act("look at user") { c.actions.lookAtUser() } }) { Text("User") }
                    OutlinedButton(onClick = { act("park") { c.client.motion("park", dev.tappet.head.util.jsonOf("arm" to "both")) } }) { Text("Park arms") }
                }
                OutlinedTextField(text, { text = it }, label = { Text("Text to type") }, modifier = Modifier.fillMaxWidth())
                Button(enabled = text.isNotEmpty(), onClick = { val t = text; act("type") { c.actions.typeText(t) } }) { Text("Type on phone") }
            }
        }
    }
}
