package dev.tappet.head.body

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.InetSocketAddress
import java.net.Socket

/** TCP transport to the Python simulator (`tappet sim`, port 7777). From the emulator the host is 10.0.2.2. */
class TcpTransport(private val host: String, private val port: Int) : StreamBodyLink() {
    @Volatile private var socket: Socket? = null
    override val name: String get() = "tcp $host:$port"

    override suspend fun open() = withContext(Dispatchers.IO) {
        resetBuffers()
        val s = Socket()
        s.tcpNoDelay = true
        s.keepAlive = true
        s.connect(InetSocketAddress(host, port), 3000)
        socket = s
        input = s.getInputStream()
        output = s.getOutputStream()
    }

    override fun close() {
        runCatching { socket?.close() }
        socket = null
        input = null
        output = null
    }
}
