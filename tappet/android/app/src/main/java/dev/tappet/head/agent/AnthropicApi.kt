package dev.tappet.head.agent

import dev.tappet.head.util.JsonX
import dev.tappet.head.util.obj
import dev.tappet.head.util.str
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

class AnthropicException(val status: Int, val type: String?, message: String) : IOException(message)

/** Minimal raw-HTTP client for the Claude Messages API (POST /v1/messages). */
class AnthropicApi(
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build(),
    private val baseUrl: String = "https://api.anthropic.com",
) {
    /** Sends a Messages API request body; retries 429/5xx/529 and network errors with backoff. */
    suspend fun createMessage(apiKey: String, body: JsonObject): JsonObject = withContext(Dispatchers.IO) {
        require(apiKey.isNotBlank()) { "Anthropic API key is not set (Settings → API key)" }
        var attempt = 0
        while (true) {
            val req = Request.Builder()
                .url("$baseUrl/v1/messages")
                .header("x-api-key", apiKey)
                .header("anthropic-version", "2023-06-01")
                .header("content-type", "application/json")
                .post(body.toString().toRequestBody(JSON))
                .build()
            try {
                http.newCall(req).execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    if (resp.isSuccessful) return@withContext JsonX.parseToJsonElement(text).jsonObject
                    val err = runCatching { JsonX.parseToJsonElement(text).jsonObject.obj("error") }.getOrNull()
                    val ex = AnthropicException(resp.code, err?.str("type"), err?.str("message") ?: "HTTP ${resp.code}: ${text.take(300)}")
                    val retryable = resp.code == 429 || resp.code >= 500
                    if (!retryable || attempt >= 2) throw ex
                    val retryAfter = resp.header("retry-after")?.toDoubleOrNull()?.let { (it * 1000).toLong() }
                    delay(retryAfter?.coerceAtMost(20_000) ?: (1000L shl attempt))
                }
            } catch (e: AnthropicException) {
                throw e
            } catch (e: IOException) {
                if (attempt >= 2) throw e
                delay(1000L shl attempt)
            }
            attempt++
        }
        @Suppress("UNREACHABLE_CODE")
        error("unreachable")
    }

    companion object {
        private val JSON = "application/json".toMediaType()
    }
}
