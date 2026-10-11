package dev.tappet.head.camera

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.util.Log
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import dev.tappet.head.calib.P2
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.abs

/** Normalized gaze target (-1..1, +x = display right, +y = display down) and motion strength. */
data class Gaze(val x: Float, val y: Float, val strength: Float)

/**
 * Front camera via CameraX ImageAnalysis. Keeps the latest upright frame as a Bitmap (JPEG on demand),
 * produces a rectified target-screen image (Matrix.setPolyToPoly from 4 clicked corners), and computes
 * a cheap motion centroid so the eyes can follow movement in front of the robot.
 */
class CameraController(private val context: Context) {
    private val latest = AtomicReference<Bitmap?>(null)
    private val executor = Executors.newSingleThreadExecutor()
    private var provider: ProcessCameraProvider? = null
    private var prevSmall: IntArray? = null
    private var lastFrameMs = 0L

    private val _gaze = MutableStateFlow(Gaze(0f, 0f, 0f))
    val gaze: StateFlow<Gaze> = _gaze.asStateFlow()
    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running.asStateFlow()
    @Volatile var lastError: String? = null
        private set

    fun hasPermission() =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    /** Must be called on the main thread. */
    fun start(owner: LifecycleOwner) {
        if (!hasPermission()) {
            lastError = "camera permission not granted"
            return
        }
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(
                        ResolutionSelector.Builder().setResolutionStrategy(
                            ResolutionStrategy(Size(1280, 960), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)
                        ).build()
                    )
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                    .build()
                analysis.setAnalyzer(executor) { img -> onFrame(img) }
                val selector = if (p.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)) CameraSelector.DEFAULT_FRONT_CAMERA
                else CameraSelector.DEFAULT_BACK_CAMERA
                p.unbindAll()
                p.bindToLifecycle(owner, selector, analysis)
                _running.value = true
                lastError = null
            } catch (e: Exception) {
                lastError = e.message
                Log.e(TAG, "camera start failed", e)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    fun stop() {
        runCatching { provider?.unbindAll() }
        _running.value = false
    }

    private fun onFrame(img: ImageProxy) {
        try {
            val now = System.currentTimeMillis()
            if (now - lastFrameMs < 100) return // ~10 fps is plenty
            lastFrameMs = now
            val raw = img.toBitmap()
            val rot = img.imageInfo.rotationDegrees
            val upright = if (rot != 0) {
                val m = Matrix().apply { postRotate(rot.toFloat()) }
                Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, m, true).also { if (it !== raw) raw.recycle() }
            } else raw
            latest.getAndSet(upright)
            updateMotion(upright)
        } catch (e: Exception) {
            Log.w(TAG, "frame error: ${e.message}")
        } finally {
            img.close()
        }
    }

    private fun updateMotion(bmp: Bitmap) {
        val w = 32
        val h = 24
        val small = Bitmap.createScaledBitmap(bmp, w, h, true)
        val px = IntArray(w * h)
        small.getPixels(px, 0, w, 0, 0, w, h)
        if (small !== bmp) small.recycle()
        val gray = IntArray(w * h) { i -> val c = px[i]; (Color.red(c) * 3 + Color.green(c) * 6 + Color.blue(c)) / 10 }
        val prev = prevSmall
        prevSmall = gray
        if (prev == null) return
        var sx = 0.0
        var sy = 0.0
        var sw = 0.0
        for (y in 0 until h) for (x in 0 until w) {
            val d = abs(gray[y * w + x] - prev[y * w + x])
            if (d > 25) {
                sx += x * d; sy += y * d; sw += d.toDouble()
            }
        }
        val g = _gaze.value
        if (sw > 400) {
            // Unmirrored front camera: something at the viewer's right appears at the image's left,
            // and the viewer's right is the display's right, so flip x.
            val cx = -((sx / sw) / (w - 1) * 2 - 1)
            val cy = (sy / sw) / (h - 1) * 2 - 1
            val a = 0.35f
            _gaze.value = Gaze(g.x + (cx.toFloat() - g.x) * a, g.y + (cy.toFloat() - g.y) * a, (sw / 20000.0).toFloat().coerceIn(0f, 1f))
        } else {
            _gaze.value = Gaze(g.x * 0.97f, g.y * 0.97f, g.strength * 0.9f)
        }
    }

    fun latestBitmap(): Bitmap? = latest.get()

    fun latestJpeg(quality: Int = 80): ByteArray? {
        val b = latest.get() ?: return null
        return synchronized(b) { encode(b, quality) }
    }

    /**
     * Rectified target-screen image: corners (normalized image coords, TL TR BR BL) are mapped to an
     * upright portrait image with the screen's aspect ratio.
     */
    fun rectifiedJpeg(corners: List<P2>, screenW: Double, screenL: Double, outH: Int = 1024, quality: Int = 85): ByteArray? {
        val b = latest.get() ?: return null
        val out = rectify(b, corners, screenW, screenL, outH)
        return encode(out, quality).also { out.recycle() }
    }

    companion object {
        private const val TAG = "Camera"

        fun encode(b: Bitmap, quality: Int): ByteArray {
            val bos = ByteArrayOutputStream()
            b.compress(Bitmap.CompressFormat.JPEG, quality, bos)
            return bos.toByteArray()
        }

        fun rectify(src: Bitmap, corners: List<P2>, screenW: Double, screenL: Double, outH: Int): Bitmap {
            require(corners.size == 4)
            val outW = (outH * screenW / screenL).toInt().coerceAtLeast(16)
            val s = FloatArray(8)
            corners.forEachIndexed { i, c ->
                s[i * 2] = (c.x * src.width).toFloat()
                s[i * 2 + 1] = (c.y * src.height).toFloat()
            }
            val d = floatArrayOf(0f, 0f, outW.toFloat(), 0f, outW.toFloat(), outH.toFloat(), 0f, outH.toFloat())
            val m = Matrix()
            check(m.setPolyToPoly(s, 0, d, 0, 4)) { "corners do not define a valid perspective transform" }
            val out = Bitmap.createBitmap(outW, outH, Bitmap.Config.ARGB_8888)
            Canvas(out).apply {
                drawColor(Color.BLACK)
                drawBitmap(src, m, Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG))
            }
            return out
        }
    }
}
