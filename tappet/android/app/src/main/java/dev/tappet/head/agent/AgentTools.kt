package dev.tappet.head.agent

import android.content.Context
import dev.tappet.head.agent.Agent.Companion.prop
import dev.tappet.head.agent.Agent.Companion.tool
import dev.tappet.head.robot.RobotActions
import dev.tappet.head.util.bool
import dev.tappet.head.util.dbl
import dev.tappet.head.util.int
import dev.tappet.head.util.r
import dev.tappet.head.util.str
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/** The agent's tools (SPEC §7). */
class AgentTools(
    private val context: Context,
    private val actions: RobotActions,
    private val tasks: TaskStore,
    private val memory: MemoryStore,
) : ToolExecutor {

    private val uv = mapOf(
        "u" to prop("number", "Horizontal screen position, 0 = left edge, 1 = right edge"),
        "v" to prop("number", "Vertical screen position, 0 = top edge, 1 = bottom edge"),
    )

    override val definitions: JsonArray = JsonArray(
        listOf(
            tool("tap", "Tap the target phone's screen at normalized position (u,v).", uv, listOf("u", "v")),
            tool("double_tap", "Double-tap the target phone's screen at (u,v).", uv, listOf("u", "v")),
            tool(
                "long_press", "Press and hold on the target phone's screen at (u,v).",
                uv + ("ms" to prop("integer", "Hold duration in milliseconds (default 900)")), listOf("u", "v"),
            ),
            tool(
                "swipe", "Swipe on the target phone's screen from (u0,v0) to (u1,v1).",
                mapOf(
                    "u0" to prop("number", "start u"), "v0" to prop("number", "start v"),
                    "u1" to prop("number", "end u"), "v1" to prop("number", "end v"),
                    "ms" to prop("integer", "Stroke duration in ms (default 400; faster = flick)"),
                ),
                listOf("u0", "v0", "u1", "v1"),
            ),
            tool(
                "type_text", "Type text with the iPhone's on-screen keyboard (which must already be open). Use \\n for return.",
                mapOf("text" to prop("string", "Text to type")), listOf("text"),
            ),
            tool("look_at_device", "Point the head camera at the phone in the tray and return an upright image of its screen."),
            tool("look_at_user", "Turn the head to face the user."),
            tool(
                "move_head", "Move the head. pan: degrees, positive turns the head toward the robot's left (counter-clockwise seen from above); tilt: degrees, negative looks down.",
                mapOf("pan" to prop("number", "Pan angle in degrees (-80..80)"), "tilt" to prop("number", "Tilt angle in degrees (-70..30)")),
                listOf("pan", "tilt"),
            ),
            tool(
                "set_timer", "Start a countdown timer that alerts the user when it ends.",
                mapOf("seconds" to prop("integer", "Duration in seconds"), "label" to prop("string", "What the timer is for")),
                listOf("seconds"),
            ),
            tool(
                "add_reminder", "Schedule a reminder at a specific local date/time.",
                mapOf(
                    "when_iso" to prop("string", "ISO-8601 local date-time, e.g. 2026-10-11T15:30:00"),
                    "text" to prop("string", "Reminder text"),
                ),
                listOf("when_iso", "text"),
            ),
            tool(
                "add_task", "Add an item to the user's to-do list.",
                mapOf("text" to prop("string", "Task description"), "due" to prop("string", "Optional due date/time (free text or ISO)")),
                listOf("text"),
            ),
            tool("list_tasks", "List the user's to-do items.", mapOf("include_done" to prop("boolean", "Include completed tasks"))),
            tool(
                "complete_task", "Mark a to-do item as done, by id or by matching text.",
                mapOf("id" to prop("integer", "Task id"), "text" to prop("string", "Text to match if the id is unknown")),
            ),
            tool("remember", "Store a durable fact about the user or their preferences.", mapOf("fact" to prop("string", "The fact")), listOf("fact")),
            tool("recall", "Search remembered facts.", mapOf("query" to prop("string", "Keywords (empty for recent facts)")), listOf("query")),
            tool("get_time", "Get the current local date, time and time zone."),
        )
    )

    private fun JsonObject.num(k: String): Double = dbl(k) ?: throw IllegalArgumentException("missing '$k'")

    override fun describe(name: String, input: JsonObject): String = when (name) {
        "tap", "double_tap", "long_press" -> "$name(${input.dbl("u")?.r(3)}, ${input.dbl("v")?.r(3)})"
        "swipe" -> "swipe(${input.dbl("u0")?.r(2)},${input.dbl("v0")?.r(2)} → ${input.dbl("u1")?.r(2)},${input.dbl("v1")?.r(2)})"
        "type_text" -> "type_text(\"${input.str("text")}\")"
        else -> "$name(${input.entries.joinToString { "${it.key}=${it.value}" }})"
    }

    override suspend fun execute(name: String, input: JsonObject): ToolOutput = when (name) {
        "tap" -> {
            val r = actions.tap(input.num("u"), input.num("v"))
            ToolOutput("tapped (arm ${r.str("arm")}). Use look_at_device to check the result.")
        }
        "double_tap" -> { actions.doubleTap(input.num("u"), input.num("v")); ToolOutput("double-tapped") }
        "long_press" -> { actions.longPress(input.num("u"), input.num("v"), input.int("ms") ?: 900); ToolOutput("long-pressed") }
        "swipe" -> {
            actions.swipe(input.num("u0"), input.num("v0"), input.num("u1"), input.num("v1"), input.int("ms") ?: 400)
            ToolOutput("swiped")
        }
        "type_text" -> {
            val r = actions.typeText(input.str("text") ?: throw IllegalArgumentException("missing 'text'"))
            val skipped = r.str("skipped").orEmpty()
            ToolOutput("typed ${r["keys"]} keys" + if (skipped.isNotEmpty()) "; could not type: $skipped" else "")
        }
        "look_at_device" -> {
            val jpg = actions.lookAtDevice()
            if (jpg == null) ToolOutput("camera image unavailable (camera permission or camera busy)", isError = true)
            else ToolOutput("Current screen of the phone in the tray:", jpg)
        }
        "look_at_user" -> { actions.lookAtUser(); ToolOutput("now facing the user") }
        "move_head" -> {
            val r = actions.neck(input.num("pan"), input.num("tilt"))
            ToolOutput("head at pan ${r["pan"]}, tilt ${r["tilt"]}")
        }
        "set_timer" -> {
            val sec = input.int("seconds") ?: throw IllegalArgumentException("missing 'seconds'")
            require(sec in 1..(7 * 24 * 3600)) { "seconds out of range" }
            val label = input.str("label")?.takeIf { it.isNotBlank() } ?: "Timer"
            val at = System.currentTimeMillis() + sec * 1000L
            Reminders.schedule(context, at, "timer", label)
            ToolOutput("timer '$label' set for $sec s (ends ${fmt(at)})")
        }
        "add_reminder" -> {
            val whenZ = Reminders.parseWhen(input.str("when_iso") ?: throw IllegalArgumentException("missing 'when_iso'"))
            val text = input.str("text") ?: "Reminder"
            val at = whenZ.toInstant().toEpochMilli()
            require(at > System.currentTimeMillis()) { "that time is in the past" }
            Reminders.schedule(context, at, "reminder", text)
            ToolOutput("reminder set for ${fmt(at)}: $text")
        }
        "add_task" -> {
            val t = tasks.add(input.str("text") ?: throw IllegalArgumentException("missing 'text'"), input.str("due"))
            ToolOutput("added task #${t.id}: ${t.text}")
        }
        "list_tasks" -> {
            val list = tasks.list(input.bool("include_done") == true)
            ToolOutput(
                if (list.isEmpty()) "no tasks"
                else list.joinToString("\n") { "#${it.id} [${if (it.done) "x" else " "}] ${it.text}${it.due?.let { d -> " (due $d)" } ?: ""}" }
            )
        }
        "complete_task" -> {
            val key = input.int("id")?.toString() ?: input.str("text") ?: throw IllegalArgumentException("give id or text")
            val t = tasks.complete(key)
            if (t == null) ToolOutput("no matching open task", isError = true) else ToolOutput("completed #${t.id}: ${t.text}")
        }
        "remember" -> {
            val f = memory.remember(input.str("fact") ?: throw IllegalArgumentException("missing 'fact'"))
            ToolOutput("remembered (#${f.id})")
        }
        "recall" -> {
            val r = memory.recall(input.str("query").orEmpty())
            ToolOutput(if (r.isEmpty()) "nothing remembered about that" else r.joinToString("\n") { "- ${it.fact} (${fmt(it.created)})" })
        }
        "get_time" -> {
            val now = ZonedDateTime.now()
            ToolOutput("${now.format(DateTimeFormatter.ofPattern("EEEE yyyy-MM-dd HH:mm:ss"))} ${now.zone.id}")
        }
        else -> ToolOutput("unknown tool '$name'", isError = true)
    }

    private fun fmt(ms: Long) = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("EEE yyyy-MM-dd HH:mm"))
}
