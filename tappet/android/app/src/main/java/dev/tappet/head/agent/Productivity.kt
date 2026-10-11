package dev.tappet.head.agent

import dev.tappet.head.util.JsonX
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.io.File
import java.util.Locale

@Serializable
data class TaskItem(
    val id: Int,
    val text: String,
    val created: Long,
    val due: String? = null,
    val done: Boolean = false,
    val completed: Long? = null,
)

@Serializable
data class MemoryFact(val id: Int, val fact: String, val created: Long)

/** JSON-file backed task list (`files/tasks.json`). */
class TaskStore(private val file: File) {
    private val items: MutableList<TaskItem> = load()

    private fun load(): MutableList<TaskItem> = runCatching {
        JsonX.decodeFromString(ListSerializer(TaskItem.serializer()), file.readText()).toMutableList()
    }.getOrDefault(mutableListOf())

    private fun save() = file.writeText(JsonX.encodeToString(ListSerializer(TaskItem.serializer()), items))

    @Synchronized
    fun add(text: String, due: String? = null): TaskItem {
        val t = TaskItem((items.maxOfOrNull { it.id } ?: 0) + 1, text.trim(), System.currentTimeMillis(), due?.takeIf { it.isNotBlank() })
        items += t
        save()
        return t
    }

    @Synchronized
    fun list(includeDone: Boolean = false): List<TaskItem> = items.filter { includeDone || !it.done }

    /** Completes by id, or by best text match when [idOrText] is not a number. */
    @Synchronized
    fun complete(idOrText: String): TaskItem? {
        val id = idOrText.trim().toIntOrNull()
        val idx = if (id != null) items.indexOfFirst { it.id == id } else {
            val q = idOrText.lowercase(Locale.ROOT)
            items.indexOfFirst { !it.done && it.text.lowercase(Locale.ROOT).contains(q) }
        }
        if (idx < 0) return null
        val t = items[idx].copy(done = true, completed = System.currentTimeMillis())
        items[idx] = t
        save()
        return t
    }
}

/** JSON-file backed long-term memory (`files/memory.json`) with keyword recall. */
class MemoryStore(private val file: File) {
    private val facts: MutableList<MemoryFact> = runCatching {
        JsonX.decodeFromString(ListSerializer(MemoryFact.serializer()), file.readText()).toMutableList()
    }.getOrDefault(mutableListOf())

    private fun save() = file.writeText(JsonX.encodeToString(ListSerializer(MemoryFact.serializer()), facts))

    @Synchronized
    fun remember(fact: String): MemoryFact {
        val f = MemoryFact((facts.maxOfOrNull { it.id } ?: 0) + 1, fact.trim(), System.currentTimeMillis())
        facts += f
        save()
        return f
    }

    @Synchronized
    fun all(): List<MemoryFact> = facts.toList()

    /** Keyword-overlap ranking; an empty query returns the most recent facts. */
    @Synchronized
    fun recall(query: String, limit: Int = 10): List<MemoryFact> {
        val words = tokenize(query)
        if (words.isEmpty()) return facts.takeLast(limit).reversed()
        return facts.map { f ->
            val fw = tokenize(f.fact)
            val score = words.sumOf { w -> fw.count { it == w || (w.length >= 4 && it.startsWith(w.take(4))) } }
            f to score
        }.filter { it.second > 0 }.sortedByDescending { it.second }.take(limit).map { it.first }
    }

    private fun tokenize(s: String) = s.lowercase(Locale.ROOT).split(Regex("[^\\p{L}\\p{N}]+"))
        .filter { it.length > 1 && it !in STOP }

    companion object {
        private val STOP = setOf("the", "a", "an", "is", "are", "my", "me", "of", "to", "and", "what", "do", "you", "i", "in", "on", "for", "it", "that")
    }
}
