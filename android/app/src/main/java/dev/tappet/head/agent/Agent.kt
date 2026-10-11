package dev.tappet.head.agent

import android.util.Base64
import android.util.Log
import dev.tappet.head.config.AppSettings
import dev.tappet.head.util.arr
import dev.tappet.head.util.str
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/** One line of the visible chat transcript. */
data class ChatEntry(val role: String, val text: String, val time: Long = System.currentTimeMillis())

/** Result of running a tool: text and/or a JPEG image, and whether it is an error. */
class ToolOutput(val text: String, val jpeg: ByteArray? = null, val isError: Boolean = false)

/** Executes the agent's tools (implemented by [AgentTools]). */
interface ToolExecutor {
    val definitions: JsonArray
    suspend fun execute(name: String, input: JsonObject): ToolOutput
    fun describe(name: String, input: JsonObject): String
}

/**
 * Claude agent with a manual tool-use loop over the Messages API. Conversation history is kept
 * append-only and is restarted (not edited) when it grows long or after a long idle period; durable
 * facts live in the memory tool.
 */
class Agent(
    private val api: AnthropicApi,
    private val tools: ToolExecutor,
    private val settings: () -> AppSettings,
) {
    private val lock = Mutex()
    private val history = ArrayList<JsonObject>()
    private var lastActivity = 0L

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()
    private val _transcript = MutableStateFlow<List<ChatEntry>>(emptyList())
    val transcript: StateFlow<List<ChatEntry>> = _transcript.asStateFlow()

    private fun log(role: String, text: String) {
        _transcript.value = (_transcript.value + ChatEntry(role, text)).takeLast(200)
    }

    fun clearConversation() {
        history.clear()
        _transcript.value = emptyList()
    }

    private fun systemPrompt(): String {
        val now = ZonedDateTime.now()
        return """
You are Tappet, a small, friendly desktop robot and productivity assistant. Your head is an Android
phone on a pan/tilt neck: you see through its front camera, hear through its microphone, and speak
through its speaker. In front of you, a second phone (usually the user's iPhone) lies screen-up in a
tray, and you operate it with two small arms that have capacitive stylus tips.

Operating the phone:
- Screen positions use normalized coordinates: u from 0 (left edge) to 1 (right edge), v from 0 (top
  edge) to 1 (bottom edge), as seen by a person holding the phone upright.
- Call look_at_device to see the current screen (an upright, rectified image of the phone screen)
  before acting on an unfamiliar screen, and again after acting to check that it worked. Estimate the
  coordinates of a target from the image: u = x / image_width, v = y / image_height.
- Taps have about 2 mm of error, so aim for the centre of buttons; on failure, look again and retry.
- type_text types on the iOS on-screen keyboard; tap the text field first so the keyboard is open.
- Swipe up from the bottom edge (v≈0.99 → 0.6) to go to the home screen; swipe down from the top
  right to open Control Center.
- Never enter passwords, make purchases, send money or send messages on the user's behalf without
  explicit confirmation in this conversation.

Being an assistant: you help with timers, reminders, a to-do list and remembering facts. Use
remember/recall for durable personal facts and preferences. When the user is talking to you, call
look_at_user if your head is pointed at the tray.

Your final reply is spoken aloud by text-to-speech: keep it short and conversational (one to three
sentences), no markdown, lists or emoji.

Current local time: ${now.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)} (${now.dayOfWeek.name.lowercase().replaceFirstChar { it.uppercase() }}).
""".trimIndent()
    }

    /** Runs one user turn through the tool loop and returns the final text reply. */
    suspend fun chat(userText: String): String = lock.withLock {
        _busy.value = true
        try {
            val now = System.currentTimeMillis()
            if (now - lastActivity > 30 * 60_000L || history.size > 60) history.clear()
            lastActivity = now
            log("user", userText)
            history += buildJsonObject {
                put("role", "user")
                put("content", userText)
            }
            val s = settings()
            var finalText = ""
            for (iteration in 0 until MAX_ITERATIONS) {
                val body = buildJsonObject {
                    put("model", s.model)
                    put("max_tokens", 4096)
                    put("system", systemPrompt())
                    put("tools", tools.definitions)
                    put("messages", JsonArray(history.toList()))
                }
                val resp = try {
                    api.createMessage(s.apiKey, body)
                } catch (e: Exception) {
                    // keep history valid: drop the unanswered user turn when nothing was appended after it
                    rollbackDanglingUser()
                    throw e
                }
                val content = resp.arr("content") ?: JsonArray(emptyList())
                history += buildJsonObject {
                    put("role", "assistant")
                    put("content", content)
                }
                val texts = content.mapNotNull { (it as? JsonObject)?.takeIf { b -> b.str("type") == "text" }?.str("text") }
                val stop = resp.str("stop_reason")
                if (texts.isNotEmpty()) finalText = texts.joinToString(" ").trim()
                if (stop == "refusal") {
                    finalText = finalText.ifBlank { "Sorry, I can't help with that." }
                    history.clear()
                    break
                }
                if (stop != "tool_use") break
                val toolUses = content.mapNotNull { it as? JsonObject }.filter { it.str("type") == "tool_use" }
                val results = buildJsonArray {
                    for (tu in toolUses) {
                        val name = tu.str("name") ?: ""
                        val input = (tu["input"] as? JsonObject) ?: JsonObject(emptyMap())
                        log("tool", tools.describe(name, input))
                        val out = try {
                            tools.execute(name, input)
                        } catch (e: Exception) {
                            Log.w(TAG, "tool $name failed", e)
                            ToolOutput("error: ${e.message ?: e.javaClass.simpleName}", isError = true)
                        }
                        if (out.isError) log("tool", "  ↳ ${out.text}")
                        add(toolResult(tu.str("id") ?: "", out))
                    }
                }
                history += buildJsonObject {
                    put("role", "user")
                    put("content", results)
                }
                if (iteration == MAX_ITERATIONS - 1) finalText = finalText.ifBlank { "I ran out of steps for that task." }
            }
            if (finalText.isBlank()) finalText = "Done."
            log("assistant", finalText)
            finalText
        } catch (e: Exception) {
            val msg = "Sorry, something went wrong: ${e.message ?: e.javaClass.simpleName}"
            log("error", msg)
            throw e
        } finally {
            _busy.value = false
        }
    }

    private fun rollbackDanglingUser() {
        val last = history.lastOrNull() ?: return
        if (last.str("role") == "user" && last["content"] is JsonPrimitive) history.removeAt(history.size - 1)
        else if (last.str("role") == "user") history.clear() // tool_result turn without answer: restart cleanly
    }

    private fun toolResult(id: String, out: ToolOutput): JsonObject = buildJsonObject {
        put("type", "tool_result")
        put("tool_use_id", id)
        if (out.isError) put("is_error", true)
        val blocks = ArrayList<JsonElement>()
        blocks += buildJsonObject {
            put("type", "text")
            put("text", out.text.ifBlank { "ok" })
        }
        out.jpeg?.let { jpg ->
            blocks += buildJsonObject {
                put("type", "image")
                put("source", buildJsonObject {
                    put("type", "base64")
                    put("media_type", "image/jpeg")
                    put("data", Base64.encodeToString(jpg, Base64.NO_WRAP))
                })
            }
        }
        put("content", JsonArray(blocks))
    }

    companion object {
        private const val TAG = "Agent"
        const val MAX_ITERATIONS = 16

        /** Builds a tool definition. */
        fun tool(name: String, description: String, properties: Map<String, JsonObject> = emptyMap(), required: List<String> = emptyList()) =
            buildJsonObject {
                put("name", name)
                put("description", description)
                put("input_schema", buildJsonObject {
                    put("type", "object")
                    put("properties", JsonObject(properties))
                    put("required", JsonArray(required.map { JsonPrimitive(it) }))
                })
            }

        fun prop(type: String, description: String, extra: Map<String, JsonElement> = emptyMap()) =
            JsonObject(mapOf("type" to JsonPrimitive(type), "description" to JsonPrimitive(description)) + extra)
    }
}
