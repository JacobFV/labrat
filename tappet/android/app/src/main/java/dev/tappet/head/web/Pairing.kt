package dev.tappet.head.web

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * Web pairing: a 6-digit PIN shown on the face is exchanged (`POST /api/pair {pin}`) for a random
 * bearer token used in `X-Tappet-Token` / `?token=`. Tokens persist (hashed) across restarts.
 * Repeated wrong PINs lock pairing for 30 s.
 */
class Pairing(context: Context) {
    private val prefs = context.getSharedPreferences("tappet_pairing", Context.MODE_PRIVATE)
    private val rnd = SecureRandom()
    private val tokens: MutableSet<String> = prefs.getStringSet("tokens", emptySet())!!.toMutableSet()
    private var failures = 0
    private var lockedUntil = 0L

    private val _pin = MutableStateFlow(newPin())
    val pin: StateFlow<String> = _pin.asStateFlow()

    private fun newPin() = (0 until 6).joinToString("") { rnd.nextInt(10).toString() }

    fun regeneratePin() { _pin.value = newPin() }

    private fun hash(t: String): String =
        MessageDigest.getInstance("SHA-256").digest(t.toByteArray()).joinToString("") { "%02x".format(it) }

    /** Returns a new token when [pin] matches, null otherwise (or while locked out). */
    @Synchronized
    fun pair(pin: String): String? {
        val now = System.currentTimeMillis()
        if (now < lockedUntil) return null
        if (pin.trim() != _pin.value) {
            if (++failures >= 5) {
                lockedUntil = now + 30_000
                failures = 0
            }
            return null
        }
        failures = 0
        val bytes = ByteArray(24).also { rnd.nextBytes(it) }
        val token = bytes.joinToString("") { "%02x".format(it) }
        tokens += hash(token)
        while (tokens.size > 32) tokens.remove(tokens.first())
        prefs.edit().putStringSet("tokens", tokens.toSet()).apply()
        regeneratePin() // one PIN, one pairing
        return token
    }

    @Synchronized
    fun isValid(token: String?): Boolean = !token.isNullOrBlank() && hash(token) in tokens

    @Synchronized
    fun revokeAll() {
        tokens.clear()
        prefs.edit().remove("tokens").apply()
        regeneratePin()
    }

    val lockedOut get() = System.currentTimeMillis() < lockedUntil
}
