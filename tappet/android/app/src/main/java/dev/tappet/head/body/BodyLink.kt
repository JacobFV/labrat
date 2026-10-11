package dev.tappet.head.body

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/**
 * A byte transport to the Tappet body carrying the newline-delimited JSON protocol (SPEC §4).
 * Implementations: [AoaTransport] (USB accessory, production), [UsbSerialTransport] (OTG into the
 * DevKit UART port), [TcpTransport] (Python simulator).
 */
interface BodyLink {
    /** Human readable transport name, e.g. "aoa", "usb-serial", "tcp 10.0.2.2:7777". */
    val name: String

    /** Opens the link. Suspends while waiting for permission; throws [IOException] on failure. */
    suspend fun open()

    /** Blocking read of the next protocol line (without the newline); null on end of stream. */
    fun readLine(): String?

    /** Writes one protocol line (a newline is appended). Thread-safe. */
    fun writeLine(line: String)

    /** Closes the link; unblocks a pending [readLine] where the platform allows it. */
    fun close()
}

/** Shared line framing over a pair of streams. */
abstract class StreamBodyLink : BodyLink {
    protected var input: InputStream? = null
    protected var output: OutputStream? = null
    private val readBuf = ByteArray(16384) // AOA bulk reads must be able to take a full 16 KiB packet
    private var bufLen = 0
    private var bufPos = 0
    private val line = ByteArrayOutputStream(256)
    private val writeLock = Any()

    override fun readLine(): String? {
        val ins = input ?: throw IOException("link not open")
        line.reset()
        while (true) {
            if (bufPos >= bufLen) {
                val n = ins.read(readBuf)
                if (n < 0) return null
                if (n == 0) continue
                bufLen = n
                bufPos = 0
            }
            while (bufPos < bufLen) {
                val b = readBuf[bufPos++].toInt()
                if (b == '\n'.code) {
                    val s = line.toString(Charsets.UTF_8.name()).trimEnd('\r')
                    line.reset()
                    if (s.isNotBlank()) return s
                } else if (line.size() < 8192) {
                    line.write(b)
                }
            }
        }
    }

    override fun writeLine(line: String) {
        val out = output ?: throw IOException("link not open")
        val bytes = (line + "\n").toByteArray(Charsets.UTF_8)
        synchronized(writeLock) {
            out.write(bytes)
            out.flush()
        }
    }

    protected fun resetBuffers() {
        bufLen = 0
        bufPos = 0
        line.reset()
    }
}
