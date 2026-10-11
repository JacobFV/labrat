package dev.tappet.head.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.lerp
import androidx.compose.runtime.withFrameMillis
import kotlin.math.abs
import kotlin.math.min
import kotlin.math.sin
import kotlin.random.Random

enum class Expression { IDLE, LISTENING, THINKING, SPEAKING, HAPPY, ERROR }

/** Face inputs: expression, gaze target (-1..1, +x right, +y down) and a word tick for mouth pulses. */
data class FaceState(
    val expression: Expression = Expression.IDLE,
    val gazeX: Float = 0f,
    val gazeY: Float = 0f,
    val wordTick: Long = 0,
)

private val Background = Color(0xFF05070A)
private val EyeIdle = Color(0xFF7FE7FF)
private val EyeListen = Color(0xFF6CFFB0)
private val EyeThink = Color(0xFFB79CFF)
private val EyeError = Color(0xFFFF5A5A)
private val EyeHappy = Color(0xFFFFE07A)

/** Fullscreen animated robot face drawn on a Canvas. */
@Composable
fun RobotFace(state: FaceState, modifier: Modifier = Modifier) {
    var now by remember { mutableLongStateOf(0L) }
    LaunchedEffect(Unit) { while (true) withFrameMillis { now = it } }

    // blinking
    val blink = remember { Animatable(1f) }
    LaunchedEffect(state.expression) {
        while (true) {
            kotlinx.coroutines.delay(Random.nextLong(2200, 6000))
            blink.animateTo(0.08f, tween(70))
            blink.animateTo(1f, tween(110))
            if (Random.nextFloat() < 0.15f) { // occasional double blink
                blink.animateTo(0.08f, tween(70))
                blink.animateTo(1f, tween(110))
            }
        }
    }

    // idle gaze wander when there is no motion to follow
    var wanderX by remember { mutableFloatStateOf(0f) }
    var wanderY by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(Random.nextLong(1500, 4000))
            wanderX = Random.nextFloat() * 1.2f - 0.6f
            wanderY = Random.nextFloat() * 0.6f - 0.3f
        }
    }
    val following = abs(state.gazeX) > 0.05f || abs(state.gazeY) > 0.05f
    val (tx, ty) = when (state.expression) {
        Expression.THINKING -> 0.55f to -0.55f
        else -> if (following) state.gazeX to state.gazeY else wanderX to wanderY
    }
    val gx by animateFloatAsState(tx.coerceIn(-1f, 1f), tween(220), label = "gx")
    val gy by animateFloatAsState(ty.coerceIn(-1f, 1f), tween(220), label = "gy")

    // mouth pulses on each spoken word
    val pulse = remember { Animatable(0f) }
    LaunchedEffect(state.wordTick) {
        if (state.expression == Expression.SPEAKING) {
            pulse.snapTo(1f)
            pulse.animateTo(0f, tween(260))
        }
    }

    val eyeColor = when (state.expression) {
        Expression.LISTENING -> EyeListen
        Expression.THINKING -> EyeThink
        Expression.ERROR -> EyeError
        Expression.HAPPY -> EyeHappy
        else -> EyeIdle
    }
    val color by androidx.compose.animation.animateColorAsState(eyeColor, tween(300), label = "eye")

    Canvas(modifier.fillMaxSize()) {
        drawRect(Background)
        val t = now / 1000f
        val unit = min(size.width, size.height)
        val landscape = size.width > size.height
        val eyeW = unit * if (landscape) 0.24f else 0.26f
        val eyeH = eyeW * 1.25f
        val cy = size.height * if (landscape) 0.42f else 0.40f
        val spread = if (landscape) size.width * 0.17f else size.width * 0.22f
        val ox = gx * eyeW * 0.35f
        val oy = gy * eyeH * 0.25f
        val listenScale = if (state.expression == Expression.LISTENING) 1.06f + 0.04f * sin(t * 6f) else 1f
        val open = blink.value

        for (side in listOf(-1, 1)) {
            val cx = size.width / 2 + side * spread + ox
            val c = Offset(cx, cy + oy)
            when (state.expression) {
                Expression.HAPPY -> drawHappyEye(c, eyeW, eyeH, color)
                Expression.ERROR -> drawErrorEye(c, eyeW, eyeH, color, side)
                else -> {
                    val w = eyeW * listenScale
                    val h = eyeH * listenScale * open * if (state.expression == Expression.THINKING) 0.75f else 1f
                    drawRoundRect(
                        color,
                        topLeft = Offset(c.x - w / 2, c.y - h / 2),
                        size = Size(w, h),
                        cornerRadius = CornerRadius(w * 0.32f, w * 0.32f),
                    )
                    // specular highlight
                    if (open > 0.5f) {
                        drawCircle(Color.White.copy(alpha = 0.55f), radius = w * 0.09f, center = Offset(c.x + w * 0.2f, c.y - h * 0.22f))
                    }
                }
            }
        }

        // mouth
        val my = cy + eyeH * 0.95f
        val mw = spread * 0.9f
        when (state.expression) {
            Expression.SPEAKING -> {
                val wobble = 0.35f + 0.3f * (0.5f + 0.5f * sin(t * 17f)) * (0.6f + 0.4f * sin(t * 5.3f))
                val h = unit * 0.11f * (wobble + 0.6f * pulse.value).coerceIn(0.12f, 1f)
                drawRoundRect(
                    lerp(color, Color.White, 0.15f),
                    topLeft = Offset(size.width / 2 - mw / 2, my - h / 2),
                    size = Size(mw, h),
                    cornerRadius = CornerRadius(h / 2, h / 2),
                )
            }
            Expression.HAPPY -> drawArc(color, 20f, 140f, false, topLeft = Offset(size.width / 2 - mw / 2, my - mw * 0.45f), size = Size(mw, mw * 0.6f), style = Stroke(unit * 0.03f, cap = StrokeCap.Round))
            Expression.ERROR -> {
                val p = Path()
                val amp = unit * 0.015f
                p.moveTo(size.width / 2 - mw / 2, my)
                var x = 0f
                while (x <= mw) {
                    p.lineTo(size.width / 2 - mw / 2 + x, my + if ((x / (mw / 6)).toInt() % 2 == 0) -amp else amp)
                    x += mw / 6
                }
                drawPath(p, color, style = Stroke(unit * 0.02f, cap = StrokeCap.Round))
            }
            Expression.THINKING -> {
                for (i in 0 until 3) {
                    val a = 0.3f + 0.7f * (0.5f + 0.5f * sin(t * 5f - i * 0.9f))
                    drawCircle(color.copy(alpha = a), unit * 0.018f, Offset(size.width / 2 + (i - 1) * unit * 0.07f, my))
                }
            }
            Expression.LISTENING -> {
                // sound-wave bars
                for (i in -3..3) {
                    val h = unit * (0.02f + 0.04f * abs(sin(t * 7f + i * 0.8f)))
                    drawLine(color, Offset(size.width / 2 + i * unit * 0.035f, my - h), Offset(size.width / 2 + i * unit * 0.035f, my + h), strokeWidth = unit * 0.014f, cap = StrokeCap.Round)
                }
            }
            Expression.IDLE -> drawArc(color.copy(alpha = 0.7f), 30f, 120f, false, topLeft = Offset(size.width / 2 - mw * 0.3f, my - mw * 0.3f), size = Size(mw * 0.6f, mw * 0.35f), style = Stroke(unit * 0.018f, cap = StrokeCap.Round))
        }
    }
}

private fun DrawScope.drawHappyEye(c: Offset, w: Float, h: Float, color: Color) {
    // "^" shaped eye: an arc
    drawArc(
        color, 200f, 140f, false,
        topLeft = Offset(c.x - w / 2, c.y - h * 0.25f), size = Size(w, h * 0.7f),
        style = Stroke(w * 0.2f, cap = StrokeCap.Round),
    )
}

private fun DrawScope.drawErrorEye(c: Offset, w: Float, h: Float, color: Color, side: Int) {
    val r = Rect(c.x - w * 0.35f, c.y - w * 0.35f, c.x + w * 0.35f, c.y + w * 0.35f)
    val sw = w * 0.16f
    drawLine(color, r.topLeft, r.bottomRight, sw, StrokeCap.Round)
    drawLine(color, r.topRight, r.bottomLeft, sw, StrokeCap.Round)
    // angry brow
    val by = c.y - h * 0.55f
    drawLine(color, Offset(c.x - w * 0.45f, by - side * w * 0.12f), Offset(c.x + w * 0.45f, by + side * w * 0.12f), sw * 0.7f, StrokeCap.Round)
}
