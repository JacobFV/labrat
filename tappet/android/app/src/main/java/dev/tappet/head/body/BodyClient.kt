package dev.tappet.head.body

import android.util.Log
import dev.tappet.head.util.JsonX
import dev.tappet.head.util.bool
import dev.tappet.head.util.long
import dev.tappet.head.util.str
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.selects.select
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/** Error reported by the body (`ok:false` response or runtime `error` event). */
class BodyException(val err: String, message: String?) : Exception("$err${if (message.isNullOrBlank()) "" else ": $message"}")

enum class LinkState { DISCONNECTED, CONNECTING, CONNECTED }

data class LinkStatus(
    val state: LinkState = LinkState.DISCONNECTED,
    val transport: String? = null,
    val detail: String? = null,
    val hello: JsonObject? = null,
) {
    val connected get() = state == LinkState.CONNECTED
}

/**
 * JSON-lines client for the body protocol (SPEC §4): request ids, suspend [request] with timeout,
 * [awaitDone] for queued motions, event flow, 1.5 s heartbeat ping (firmware watchdog is 5 s),
 * automatic reconnect with backoff, and a connection-state flow.
 *
 * [linkFactory] returns the transports to try, in order, on each connection attempt.
 */
class BodyClient(
    private val scope: CoroutineScope,
    private val linkFactory: suspend () -> List<BodyLink>,
    private val heartbeatMs: Long = 1500,
) {
    private val nextId = AtomicLong(1)
    private val pending = ConcurrentHashMap<Long, CompletableDeferred<JsonObject>>()
    private val doneWaiters = ConcurrentHashMap<Long, CompletableDeferred<JsonObject>>()
    /** done/error events that arrived for ids nobody was waiting on yet (bounded). */
    private val earlyDone = object : LinkedHashMap<Long, JsonObject>(64, 0.75f, false) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Long, JsonObject>?) = size > 128
    }

    private val _status = MutableStateFlow(LinkStatus())
    val status: StateFlow<LinkStatus> = _status.asStateFlow()

    private val _events = MutableSharedFlow<JsonObject>(extraBufferCapacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val events: SharedFlow<JsonObject> = _events.asSharedFlow()

    /** Last `status` response or `status` event from the body. */
    private val _bodyStatus = MutableStateFlow<JsonObject?>(null)
    val bodyStatus: StateFlow<JsonObject?> = _bodyStatus.asStateFlow()

    @Volatile private var link: BodyLink? = null
    private var runJob: Job? = null
    private val kick = Channel<Unit>(Channel.CONFLATED)

    val isConnected get() = _status.value.connected

    fun start() {
        if (runJob?.isActive == true) return
        runJob = scope.launch(Dispatchers.IO) { runLoop() }
    }

    fun stop() {
        runJob?.cancel()
        runJob = null
        link?.close()
        failAll(IOException("client stopped"))
        _status.value = LinkStatus(LinkState.DISCONNECTED, detail = "stopped")
    }

    /** Drop the current link (if any) and try connecting again immediately. */
    fun reconnect() {
        link?.close()
        kick.trySend(Unit)
    }

    private suspend fun runLoop() {
        var backoff = 500L
        while (scope.isActive) {
            val candidates = try { linkFactory() } catch (e: Exception) { emptyList() }
            var opened: BodyLink? = null
            var lastErr: String? = if (candidates.isEmpty()) "no transport available" else null
            for (c in candidates) {
                _status.value = LinkStatus(LinkState.CONNECTING, c.name)
                try {
                    withTimeout(70_000) { c.open() }
                    opened = c
                    break
                } catch (e: CancellationException) {
                    c.close(); throw e
                } catch (e: Exception) {
                    lastErr = "${c.name}: ${e.message ?: e.javaClass.simpleName}"
                    c.close()
                }
            }
            if (opened == null) {
                _status.value = LinkStatus(LinkState.DISCONNECTED, detail = lastErr)
                waitKickOrDelay(backoff)
                backoff = (backoff * 2).coerceAtMost(5000)
                continue
            }
            backoff = 500L
            serve(opened)
            _status.value = _status.value.copy(state = LinkState.DISCONNECTED, detail = "link lost")
            waitKickOrDelay(300)
        }
    }

    private suspend fun waitKickOrDelay(ms: Long) {
        withTimeoutOrNull(ms) { kick.receive() }
    }

    private suspend fun serve(l: BodyLink) {
        link = l
        val reader = scope.launch(Dispatchers.IO) {
            try {
                while (isActive) {
                    val line = l.readLine() ?: break
                    handleLine(line)
                }
            } catch (e: Exception) {
                Log.w(TAG, "read failed on ${l.name}: ${e.message}")
            }
        }
        val heartbeat = scope.launch(Dispatchers.IO) {
            var misses = 0
            try {
                val hello = request("hello", timeoutMs = 3000)
                _status.value = LinkStatus(LinkState.CONNECTED, l.name, hello = hello)
            } catch (e: Exception) {
                _status.value = LinkStatus(LinkState.CONNECTED, l.name, detail = "hello failed: ${e.message}")
            }
            while (isActive) {
                delay(heartbeatMs)
                try {
                    request("ping", timeoutMs = 1500)
                    misses = 0
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    misses++
                    if (misses >= 3) {
                        Log.w(TAG, "heartbeat lost on ${l.name}")
                        l.close()
                        break
                    }
                }
            }
        }
        // A blocked AOA read is not always interrupted by closing the descriptor, so return as soon as
        // either the reader ends or the heartbeat gives up, and abandon the other.
        select<Unit> {
            reader.onJoin { }
            heartbeat.onJoin { }
        }
        heartbeat.cancel()
        reader.cancel()
        l.close()
        link = null
        failAll(IOException("link lost"))
    }

    private fun failAll(e: Exception) {
        pending.values.forEach { it.completeExceptionally(e) }
        pending.clear()
        doneWaiters.values.forEach { it.completeExceptionally(e) }
        doneWaiters.clear()
    }

    internal fun handleLine(line: String) {
        val obj = try { JsonX.parseToJsonElement(line).jsonObject } catch (e: Exception) {
            Log.w(TAG, "bad line from body: $line"); return
        }
        val evt = obj.str("evt")
        if (evt != null) {
            when (evt) {
                "done" -> obj.long("id")?.let { id -> completeDone(id, obj) }
                "error" -> obj.long("id")?.let { id -> completeDone(id, obj) }
                "status" -> _bodyStatus.value = obj
            }
            _events.tryEmit(obj)
            return
        }
        val id = obj.long("id") ?: return
        pending.remove(id)?.complete(obj)
    }

    private fun completeDone(id: Long, obj: JsonObject) {
        val w = doneWaiters.remove(id)
        if (w == null) {
            synchronized(earlyDone) { earlyDone[id] = obj }
            return
        }
        if (obj.str("evt") == "error") w.completeExceptionally(BodyException(obj.str("err") ?: "error", obj.str("msg")))
        else w.complete(obj)
    }

    /**
     * Sends a raw request object (the `id` is assigned here) and returns the response object as-is
     * (including `ok:false` responses). Used by `/api/cmd`.
     */
    suspend fun requestRaw(req: JsonObject, timeoutMs: Long = 3000): JsonObject {
        val l = link ?: throw IOException("body not connected")
        val id = nextId.getAndIncrement()
        val cmd = req.str("cmd") ?: throw IllegalArgumentException("missing cmd")
        val msg = JsonObject(LinkedHashMap<String, kotlinx.serialization.json.JsonElement>().apply {
            put("id", JsonPrimitive(id))
            putAll(req.filterKeys { it != "id" })
        })
        val d = CompletableDeferred<JsonObject>()
        pending[id] = d
        if (cmd in MOTION_CMDS) doneWaiters[id] = CompletableDeferred()
        try {
            val text = msg.toString()
            withContext(Dispatchers.IO) {
                try { l.writeLine(text) } catch (e: Exception) { throw IOException("write failed: ${e.message}") }
            }
            val resp = try {
                withTimeout(timeoutMs) { d.await() }
            } catch (e: TimeoutCancellationException) {
                throw IOException("timeout waiting for '$cmd' response")
            }
            if (resp.bool("ok") != true) doneWaiters.remove(id)
            if (cmd == "status" && resp.bool("ok") == true) _bodyStatus.value = resp
            return resp
        } catch (e: Exception) {
            doneWaiters.remove(id)
            throw e
        } finally {
            pending.remove(id)
        }
    }

    /** Sends `cmd` with [args]; returns the response, throwing [BodyException] for `ok:false`. */
    suspend fun request(cmd: String, args: JsonObject = JsonObject(emptyMap()), timeoutMs: Long = 3000): JsonObject {
        val resp = requestRaw(JsonObject(args + ("cmd" to JsonPrimitive(cmd))), timeoutMs)
        if (resp.bool("ok") != true) throw BodyException(resp.str("err") ?: "error", resp.str("msg"))
        return resp
    }

    /** Waits for the `done` event of a queued motion [id]; throws [BodyException] on a runtime `error` event. */
    suspend fun awaitDone(id: Long, timeoutMs: Long = 20_000): JsonObject {
        synchronized(earlyDone) { earlyDone.remove(id) }?.let { ev ->
            if (ev.str("evt") == "error") throw BodyException(ev.str("err") ?: "error", ev.str("msg"))
            return ev
        }
        val w = doneWaiters.getOrPut(id) { CompletableDeferred() }
        // the event may have raced in between the two checks above
        synchronized(earlyDone) { earlyDone.remove(id) }?.let { completeDone(id, it) }
        return try {
            withTimeout(timeoutMs) { w.await() }
        } catch (e: TimeoutCancellationException) {
            doneWaiters.remove(id)
            throw IOException("timeout waiting for motion $id to finish")
        }
    }

    /** Queue a motion command and wait until it has finished. Returns the (enqueue) response. */
    suspend fun motion(cmd: String, args: JsonObject = JsonObject(emptyMap()), timeoutMs: Long = 20_000): JsonObject {
        val resp = request(cmd, args)
        val id = resp.long("id") ?: return resp
        if (resp.bool("queued") == true || cmd in MOTION_CMDS) awaitDone(id, timeoutMs)
        return resp
    }

    companion object {
        private const val TAG = "BodyClient"
        val MOTION_CMDS = setOf("joints", "move", "tap", "swipe", "neck", "home", "park")
    }
}
