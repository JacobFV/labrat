package dev.tappet.head

import dev.tappet.head.body.BodyClient
import dev.tappet.head.body.BodyException
import dev.tappet.head.body.BodyLink
import dev.tappet.head.util.bool
import dev.tappet.head.util.jsonOf
import dev.tappet.head.util.long
import dev.tappet.head.util.str
import dev.tappet.head.voice.WakePhrase
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** In-memory fake body implementing a tiny subset of the protocol. */
private class FakeBody : BodyLink {
    override val name = "fake"
    val toHost = LinkedBlockingQueue<String>()
    val received = LinkedBlockingQueue<JsonObject>()
    private val timer = Executors.newSingleThreadScheduledExecutor()
    @Volatile private var closed = false
    var enabled = false

    override suspend fun open() {}

    override fun readLine(): String? {
        while (!closed) {
            val l = toHost.poll(50, TimeUnit.MILLISECONDS)
            if (l != null) return l
        }
        return null
    }

    private fun send(o: JsonObject) = toHost.put(o.toString())

    override fun writeLine(line: String) {
        val req = Json.parseToJsonElement(line).jsonObject
        received.put(req)
        val id = req.long("id")!!
        when (req.str("cmd")) {
            "hello" -> send(jsonOf("id" to id, "ok" to true, "fw" to "tappet-fw", "proto" to 1))
            "ping" -> send(jsonOf("id" to id, "ok" to true, "t" to 1))
            "enable" -> { enabled = true; send(jsonOf("id" to id, "ok" to true)) }
            "tap" -> {
                if (!enabled) send(jsonOf("id" to id, "ok" to false, "err" to "not_enabled", "msg" to "enable first"))
                else {
                    send(jsonOf("id" to id, "ok" to true, "queued" to true, "arm" to "R"))
                    timer.schedule({ send(jsonOf("evt" to "done", "id" to id)) }, 30, TimeUnit.MILLISECONDS)
                }
            }
            "neck" -> {
                send(jsonOf("id" to id, "ok" to true, "queued" to true))
                timer.schedule({ send(jsonOf("evt" to "error", "id" to id, "err" to "fault", "msg" to "cancelled (stop)")) }, 30, TimeUnit.MILLISECONDS)
            }
            else -> send(jsonOf("id" to id, "ok" to false, "err" to "unknown_cmd"))
        }
    }

    override fun close() { closed = true }
}

class BodyClientTest {
    @Test
    fun requestsMotionsAndErrors() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val fake = FakeBody()
        val client = BodyClient(scope, { listOf(fake) }, heartbeatMs = 200)
        try {
            client.start()
            withTimeout(5000) { client.status.first { it.connected && it.hello != null } }
            assertEquals("tappet-fw", client.status.value.hello!!.str("fw"))

            // ok:false surfaces as BodyException with the protocol error code
            try {
                client.motion("tap", jsonOf("x" to 0, "y" to 160, "z" to 13.8))
                fail("expected not_enabled")
            } catch (e: BodyException) {
                assertEquals("not_enabled", e.err)
            }
            client.request("enable")
            val r = client.motion("tap", jsonOf("x" to 0, "y" to 160, "z" to 13.8))
            assertEquals("R", r.str("arm"))
            assertTrue(r.bool("queued") == true)

            // runtime error event fails the waiting motion
            try {
                client.motion("neck", jsonOf("pan" to 10))
                fail("expected fault")
            } catch (e: BodyException) {
                assertEquals("fault", e.err)
            }

            // raw requests return ok:false responses as-is
            val raw = client.requestRaw(jsonOf("cmd" to "bogus"))
            assertEquals(false, raw.bool("ok"))
            assertEquals("unknown_cmd", raw.str("err"))

            // heartbeat pings are being sent
            Thread.sleep(500)
            assertTrue(fake.received.any { it.str("cmd") == "ping" })
        } finally {
            client.stop()
            scope.cancel()
        }
    }

    @Test
    fun wakePhrase() {
        assertEquals("what time is it", WakePhrase.strip("Hey Tappet what time is it"))
        assertEquals("set a timer", WakePhrase.strip("hey tap it, set a timer"))
        assertEquals("", WakePhrase.strip("hey tappet"))
        assertNull(WakePhrase.strip("what's the weather"))
    }
}
