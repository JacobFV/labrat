package dev.tappet.head.web

import android.content.res.AssetManager
import android.util.Log
import dev.tappet.head.body.BodyException
import dev.tappet.head.service.HeadController
import dev.tappet.head.util.JsonX
import dev.tappet.head.util.dbl
import dev.tappet.head.util.int
import dev.tappet.head.util.jsonOf
import dev.tappet.head.util.str
import fi.iki.elonen.NanoHTTPD
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.ByteArrayInputStream
import java.io.IOException
import java.net.URLDecoder

/**
 * Head web API (SPEC §6) on NanoHTTPD, with the same JSON shapes and HTTP status codes as the Python
 * bridge (host/tappet/bridge.py) so the shared web UI (repo `web/`) works against either host.
 * The static UI is served from APK assets under `web/` (packaged from ../web at build time).
 */
class WebServer(port: Int, private val ctrl: HeadController, private val assets: AssetManager) : NanoHTTPD(port) {

    /** API error with a bridge-compatible code; HTTP status from [HTTP_FOR_ERR]. */
    class ApiError(val err: String, val msg: String, val status: Int = HTTP_FOR_ERR[err] ?: 400) : Exception(msg)

    private fun status(code: Int): Response.IStatus = Response.Status.lookup(code) ?: object : Response.IStatus {
        override fun getDescription() = "$code ${REASONS[code] ?: "Error"}"
        override fun getRequestStatus() = code
    }

    override fun serve(session: IHTTPSession): Response {
        val resp = try {
            route(session)
        } catch (e: ApiError) {
            err(e)
        } catch (e: BodyException) {
            err(ApiError(e.err, e.message ?: e.err))
        } catch (e: IllegalArgumentException) {
            err(ApiError("bad_args", e.message ?: "bad arguments"))
        } catch (e: TimeoutCancellationException) {
            err(ApiError("timeout", "timed out"))
        } catch (e: IOException) {
            val m = e.message ?: "link error"
            err(ApiError(if (m.startsWith("timeout")) "timeout" else "link_down", m))
        } catch (e: Exception) {
            Log.e(TAG, "error serving ${session.uri}", e)
            err(ApiError("fault", "${e.javaClass.simpleName}: ${e.message}", 500))
        }
        resp.addHeader("Access-Control-Allow-Origin", "*")
        resp.addHeader("Cache-Control", "no-store")
        return resp
    }

    private fun err(e: ApiError) = json(e.status, jsonOf("ok" to false, "err" to e.err, "msg" to e.msg))

    private fun readBody(session: IHTTPSession): String {
        val len = session.headers["content-length"]?.toIntOrNull() ?: 0
        if (len <= 0) return ""
        if (len > 1_000_000) throw ApiError("bad_args", "body too large", 413)
        val buf = ByteArray(len)
        var off = 0
        val ins = session.inputStream
        while (off < len) {
            val n = ins.read(buf, off, len - off)
            if (n < 0) break
            off += n
        }
        return String(buf, 0, off, Charsets.UTF_8)
    }

    private fun bodyJson(session: IHTTPSession): JsonObject {
        val text = readBody(session).trim()
        if (text.isEmpty()) return JsonObject(emptyMap())
        val e = try { JsonX.parseToJsonElement(text) } catch (e: Exception) { throw ApiError("bad_json", "invalid JSON body") }
        return e as? JsonObject ?: throw ApiError("bad_args", "JSON object expected")
    }

    private fun token(session: IHTTPSession): String? =
        session.headers["x-tappet-token"] ?: session.parameters["token"]?.firstOrNull()

    private fun <T> blocking(timeoutMs: Long, block: suspend () -> T): T = runBlocking { withTimeout(timeoutMs) { block() } }

    private fun JsonObject.num(k: String): Double {
        val p = this[k] as? JsonPrimitive
        if (p == null || p.isString) throw ApiError("bad_args", "$k must be a number")
        return dbl(k) ?: throw ApiError("bad_args", "$k must be a number")
    }

    private fun JsonObject.arm(): String? = str("arm")?.takeIf { it == "L" || it == "R" || it == "auto" }

    private fun route(s: IHTTPSession): Response {
        val path = s.uri
        val method = s.method
        if (method == Method.OPTIONS) {
            return newFixedLengthResponse(status(204), MIME_PLAINTEXT, "").apply {
                addHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                addHeader("Access-Control-Allow-Headers", "Content-Type, X-Tappet-Token")
            }
        }
        if (!path.startsWith("/api/")) {
            if (method != Method.GET && method != Method.HEAD) throw ApiError("not_found", "no such endpoint", 404)
            return static(path)
        }
        val public = path == "/api/pair" || path == "/api/touchpad/event"
        if (!public && !ctrl.pairing.isValid(token(s))) throw ApiError("unauthorized", "pair first (POST /api/pair {pin})")
        val b = if (method == Method.POST) bodyJson(s) else JsonObject(emptyMap())
        val key = "${method.name} $path"
        return when (key) {
            "POST /api/pair" -> {
                val pin = b.str("pin") ?: b.int("pin")?.toString() ?: ""
                val tok = ctrl.pairing.pair(pin)
                    ?: throw ApiError("bad_pin", if (ctrl.pairing.lockedOut) "too many attempts, wait 30 s" else "wrong PIN")
                ctrl.onPaired()
                ok(jsonOf("token" to tok))
            }
            "POST /api/touchpad/event" -> {
                val ev = try {
                    ctrl.touchLog.add(b.num("x"), b.num("y"), b.num("w"), b.num("h"), b.dbl("t"))
                } catch (e: ApiError) {
                    throw ApiError("bad_args", "need numeric x, y, w, h")
                }
                ok(jsonOf("ok" to true, "seq" to ev.seq))
            }
            "GET /api/status" -> ok(blocking(4000) { ctrl.statusJson() })
            "POST /api/cmd" -> {
                if (b.str("cmd") == null) throw ApiError("bad_args", "body must be a protocol request {cmd, ...}")
                val timeout = if (b.str("cmd") in setOf("calib_set", "geom_set", "reset_calib")) 8000L else 4000L
                ok(blocking(timeout + 1000) { ctrl.client.requestRaw(b, timeout) })
            }
            "POST /api/tap" -> {
                val u = b.num("u"); val v = b.num("v")
                ok(blocking(35_000) { ctrl.actions.tap(u, v, b.arm(), b.int("hold_ms")) })
            }
            "POST /api/swipe" -> {
                val u0 = b.num("u0"); val v0 = b.num("v0"); val u1 = b.num("u1"); val v1 = b.num("v1")
                ok(blocking(35_000) { ctrl.actions.swipe(u0, v0, u1, v1, b.int("ms") ?: 400, b.arm()) })
            }
            "POST /api/type" -> {
                val text = b.str("text")?.takeIf { (b["text"] as? JsonPrimitive)?.isString == true }
                    ?: throw ApiError("bad_args", "text must be a string")
                ok(blocking(30_000L + text.length * 4000L) { ctrl.actions.typeText(text) })
            }
            "POST /api/neck" -> {
                val pan = b.dbl("pan")
                val tilt = b.dbl("tilt")
                if (pan == null && tilt == null) throw ApiError("bad_args", "pan and/or tilt required")
                ok(blocking(15_000) { ctrl.actions.neck(pan, tilt, b.int("ms")) })
            }
            "POST /api/jog" -> {
                val arm = b.str("arm")?.takeIf { it == "L" || it == "R" } ?: throw ApiError("bad_args", "arm must be L or R")
                val p = blocking(15_000) { ctrl.actions.jog(arm, b.dbl("dx") ?: 0.0, b.dbl("dy") ?: 0.0, b.dbl("dz") ?: 0.0) }
                ok(jsonOf("ok" to true, "x" to p.x, "y" to p.y, "z" to p.z))
            }
            "GET /api/camera.jpg" -> {
                val rect = s.parameters["rectified"]?.firstOrNull().let { it == "1" || it == "true" }
                if (!ctrl.camera.hasPermission()) throw ApiError("not_implemented", "camera permission not granted on the head", 501)
                if (rect && ctrl.calibration.value.hIc == null) throw ApiError("bad_args", "rectified view needs the camera screen corners (calibration H_ic)")
                val jpg = ctrl.snapshot(rect) ?: throw ApiError("link_down", "no camera frame yet", 503)
                newFixedLengthResponse(Response.Status.OK, "image/jpeg", ByteArrayInputStream(jpg), jpg.size.toLong())
            }
            "POST /api/say" -> {
                val text = b.str("text") ?: throw ApiError("bad_args", "text must be a string")
                ctrl.sayAsync(text)
                ok(jsonOf("ok" to true))
            }
            "POST /api/chat" -> {
                val text = b.str("text") ?: throw ApiError("bad_args", "text must be a string")
                if (ctrl.settings.value.apiKey.isBlank()) {
                    throw ApiError("not_implemented", "agent not configured: set the Anthropic API key in the head's settings", 501)
                }
                val reply = try {
                    blocking(300_000) { ctrl.chat(text, speak = true) }
                } catch (e: dev.tappet.head.agent.AnthropicException) {
                    throw ApiError("fault", "agent: ${e.message}", 502)
                }
                ok(jsonOf("ok" to true, "reply" to reply))
            }
            "GET /api/calibration" -> ok(ctrl.calibration.value.toJson())
            "POST /api/calibration" -> ok(
                ctrl.calibration.merge(b, ctrl.currentProfile()) {
                    ctrl.camera.latestBitmap()?.let { bm -> dev.tappet.head.calib.P2(bm.width.toDouble(), bm.height.toDouble()) }
                }.toJson()
            )
            "POST /api/calibrate/auto" -> {
                if (ctrl.actions.busyLong) throw ApiError("fault", "another calibration/test is running", 409)
                val arm = b.arm()?.takeIf { it != "auto" }
                ok(blocking(240_000) { ctrl.actions.autoCalibrate(arm) })
            }
            "GET /api/touchpad/events" -> {
                val since = s.parameters["since"]?.firstOrNull()?.let {
                    it.toLongOrNull() ?: throw ApiError("bad_args", "since must be an integer")
                } ?: 0L
                val evs = ctrl.touchLog.since(since)
                ok(JsonObject(mapOf("events" to JsonArray(evs.map { it.toJson() }), "last" to JsonPrimitive(ctrl.touchLog.lastSeq))))
            }
            "POST /api/taptest" -> {
                val nEl = b["n"] as? JsonPrimitive
                val n = if (nEl == null) 100 else (nEl.takeIf { !it.isString }?.content?.toIntOrNull() ?: throw ApiError("bad_args", "n must be an integer 1..1000"))
                if (n !in 1..1000) throw ApiError("bad_args", "n must be an integer 1..1000")
                if (ctrl.actions.busyLong) throw ApiError("fault", "another calibration/test is running", 409)
                val arm = b.arm()?.takeIf { it != "auto" }
                val res = blocking(60_000L + n * 8_000L) { ctrl.actions.tapTest(n, arm, b.dbl("seed")?.toLong() ?: System.currentTimeMillis()) }
                ok(jsonOf("ok" to true, "results" to res))
            }
            else -> throw ApiError("not_found", "no endpoint $method $path", 404)
        }
    }

    private fun static(uri: String): Response {
        var rel = URLDecoder.decode(uri, "UTF-8").trimStart('/')
        if (rel.isEmpty() || rel.endsWith("/")) rel += "index.html"
        if (rel.split('/').any { it == ".." }) throw ApiError("not_found", "not found", 404)
        val bytes = try {
            assets.open("web/$rel").use { it.readBytes() }
        } catch (_: IOException) {
            try { assets.open("web/$rel/index.html").use { rel = "$rel/index.html"; it.readBytes() } } catch (_: IOException) { null }
        }
        if (bytes == null) {
            if (rel == "index.html") return newFixedLengthResponse(Response.Status.OK, "text/html; charset=utf-8", FALLBACK_INDEX)
            throw ApiError("not_found", "/$rel not found", 404)
        }
        return newFixedLengthResponse(Response.Status.OK, mime(rel), ByteArrayInputStream(bytes), bytes.size.toLong())
    }

    private fun mime(p: String) = when (p.substringAfterLast('.', "").lowercase()) {
        "html", "htm" -> "text/html; charset=utf-8"
        "js", "mjs" -> "text/javascript; charset=utf-8"
        "css" -> "text/css; charset=utf-8"
        "json" -> "application/json; charset=utf-8"
        "webmanifest" -> "application/manifest+json; charset=utf-8"
        "svg" -> "image/svg+xml"
        "png" -> "image/png"
        "jpg", "jpeg" -> "image/jpeg"
        "ico" -> "image/x-icon"
        "woff2" -> "font/woff2"
        "txt" -> "text/plain; charset=utf-8"
        else -> "application/octet-stream"
    }

    private fun ok(o: JsonElement) = json(200, o)

    private fun json(code: Int, o: JsonElement): Response =
        newFixedLengthResponse(status(code), "application/json", o.toString())

    companion object {
        private const val TAG = "WebServer"

        /** Same mapping as host/tappet/bridge.py HTTP_FOR_ERR. */
        val HTTP_FOR_ERR = mapOf(
            "unreachable" to 400, "limit" to 400, "bad_args" to 400, "bad_json" to 400,
            "unknown_cmd" to 400, "not_enabled" to 409, "queue_full" to 429, "fault" to 500,
            "timeout" to 504, "link_down" to 503, "no_touch" to 422, "not_implemented" to 501,
            "unauthorized" to 401, "bad_pin" to 403, "not_found" to 404,
        )
        private val REASONS = mapOf(
            204 to "No Content", 409 to "Conflict", 413 to "Payload Too Large", 422 to "Unprocessable Entity",
            429 to "Too Many Requests", 501 to "Not Implemented", 502 to "Bad Gateway", 503 to "Service Unavailable",
            504 to "Gateway Timeout",
        )
        private val FALLBACK_INDEX = """
            <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
            <title>Tappet</title></head><body style="font-family:system-ui;padding:16px">
            <h1>Tappet head</h1><p>The web UI was not packaged into this build (repo <code>web/</code> was empty).</p>
            <p>API: <code>POST /api/pair {"pin":"123456"}</code>, then send <code>X-Tappet-Token</code>.</p>
            </body></html>
        """.trimIndent()
    }
}
