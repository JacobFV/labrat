package dev.tappet.head.util

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/** Shared lenient JSON instance. */
val JsonX: Json = Json {
    ignoreUnknownKeys = true
    isLenient = true
    encodeDefaults = true
    explicitNulls = false
}

fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull
fun JsonObject.dbl(key: String): Double? = (this[key] as? JsonPrimitive)?.doubleOrNull
fun JsonObject.int(key: String): Int? = (this[key] as? JsonPrimitive)?.let { it.intOrNull ?: it.doubleOrNull?.toInt() }
fun JsonObject.long(key: String): Long? = (this[key] as? JsonPrimitive)?.let { it.longOrNull ?: it.doubleOrNull?.toLong() }
fun JsonObject.bool(key: String): Boolean? = (this[key] as? JsonPrimitive)?.booleanOrNull
fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject
fun JsonObject.arr(key: String): JsonArray? = this[key] as? JsonArray

fun JsonObject.reqDbl(key: String): Double = dbl(key) ?: throw IllegalArgumentException("missing number '$key'")

fun jsonOf(vararg pairs: Pair<String, Any?>): JsonObject = JsonObject(pairs.associate { it.first to toJsonElement(it.second) })

fun toJsonElement(v: Any?): JsonElement = when (v) {
    null -> JsonNull
    is JsonElement -> v
    is Boolean -> JsonPrimitive(v)
    is Number -> JsonPrimitive(v)
    is String -> JsonPrimitive(v)
    is DoubleArray -> JsonArray(v.map { JsonPrimitive(it) })
    is Map<*, *> -> JsonObject(v.entries.associate { it.key.toString() to toJsonElement(it.value) })
    is Iterable<*> -> JsonArray(v.map { toJsonElement(it) })
    is Array<*> -> JsonArray(v.map { toJsonElement(it) })
    else -> JsonPrimitive(v.toString())
}

/** Round to [digits] decimals (for compact JSON / display). */
fun Double.r(digits: Int = 2): Double {
    var f = 1.0
    repeat(digits) { f *= 10 }
    return kotlin.math.round(this * f) / f
}
