package dev.noemu

import java.io.DataInputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.ProxySelector
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketAddress
import java.net.URI
import java.util.concurrent.ConcurrentHashMap
import kotlin.concurrent.thread
import org.robolectric.pluginapi.TestEnvironmentLifecyclePlugin

/**
 * `network off` / `network on`: airplane mode for one instance. The app's connections go through a SOCKS proxy in this
 * JVM (the default ProxySelector, which OkHttp and HttpURLConnection follow), so `off` can close the connections that
 * are open, a websocket among them, as a phone does, and refuse new ones. Clients that don't ask the ProxySelector
 * (raw sockets, NIO channels such as Ktor's CIO engine) aren't cut. Installed before the app is created, because
 * OkHttp keeps the selector a client was built with. Onward connections go where the JVM's own selector says (directly,
 * or through an HTTP proxy such as a company's).
 */
object Network {
    @Volatile var online = true
        private set
    /** Added to every chunk in each direction (`network slow MS`), so a round trip takes about twice this longer. */
    @Volatile var delayMs = 0L
    private val open: MutableSet<Socket> = ConcurrentHashMap.newKeySet()
    private var upstream: ProxySelector? = null

    fun install() {
        val server = ServerSocket(0, 50, InetAddress.getLoopbackAddress())
        val socks = Proxy(Proxy.Type.SOCKS, InetSocketAddress(InetAddress.getLoopbackAddress(), server.localPort))
        upstream = ProxySelector.getDefault()
        ProxySelector.setDefault(object : ProxySelector() {
            override fun select(uri: URI): List<Proxy> = listOf(socks)
            override fun connectFailed(uri: URI, sa: SocketAddress, e: IOException) {}
        })
        thread(isDaemon = true, name = "noemu-network") {
            while (true) {
                val client = server.accept()
                thread(isDaemon = true, name = "noemu-tunnel") { runCatching { tunnel(client) }; client.close() }
            }
        }
    }

    fun set(on: Boolean) {
        online = on
        if (!on) open.toList().forEach { runCatching { it.close() } }
    }

    /** SOCKS 5, no authentication, CONNECT only (what java.net's SOCKS client sends). */
    private fun tunnel(client: Socket) {
        val input = DataInputStream(client.getInputStream())
        val output = client.getOutputStream()
        if (input.readUnsignedByte() != 5) return
        input.skipNBytes(input.readUnsignedByte().toLong())
        output.write(byteArrayOf(5, 0))
        val (version, command) = input.readUnsignedByte() to input.readUnsignedByte()
        input.readUnsignedByte()
        val host = when (input.readUnsignedByte()) {
            1 -> InetAddress.getByAddress(ByteArray(4).also { input.readFully(it) }).hostAddress
            3 -> String(ByteArray(input.readUnsignedByte()).also { input.readFully(it) })
            4 -> InetAddress.getByAddress(ByteArray(16).also { input.readFully(it) }).hostAddress
            else -> return
        }
        val port = input.readUnsignedShort()
        // Replies: 0 connected, 3 network unreachable (offline), 5 connection refused, 7 command not supported.
        val reply = { code: Int -> output.write(byteArrayOf(5, code.toByte(), 0, 1, 0, 0, 0, 0, 0, 0)) }
        if (!online || version != 5 || command != 1) return reply(if (online) 7 else 3)
        val via = proxyFor(host, port)
        var first = ByteArray(0)
        val target = if (via != null && port == 443) {
            // java.net's SOCKS client sends the address it resolved, not the name, and a proxy that makes certificates
            // per host (or filters by name) needs the name: take it from the TLS ClientHello, sent once we reply.
            reply(0)
            first = clientHello(input)
            runCatching { through(via, serverName(first) ?: host, port) }.getOrNull() ?: return
        } else {
            runCatching { if (via == null) direct(host, port) else through(via, host, port) }.getOrNull()
                ?: return reply(5)
        }.also { if (via == null || port != 443) reply(0) }
        open += client; open += target
        try {
            if (!online) return  // switched off while connecting
            target.getOutputStream().write(first)
            thread(isDaemon = true, name = "noemu-tunnel") { pipe(target.getInputStream(), output); client.close() }
            pipe(input, target.getOutputStream())
        } finally {
            target.close(); open -= client; open -= target
        }
    }

    /** The HTTP proxy the JVM's own selector picks for this destination, or null for a direct connection. */
    private fun proxyFor(host: String, port: Int): InetSocketAddress? {
        val uri = URI(if (port == 443) "https" else "http", null, host, port, null, null, null)
        val proxy = upstream?.select(uri)?.firstOrNull { it.type() == Proxy.Type.HTTP } ?: return null
        val address = proxy.address() as InetSocketAddress  // as the JDK hands it over: unresolved
        return InetSocketAddress(address.hostString, address.port)
    }

    private fun direct(host: String, port: Int) = Socket(Proxy.NO_PROXY).apply { connect(InetSocketAddress(host, port), 15000) }

    private fun through(proxy: InetSocketAddress, host: String, port: Int): Socket {
        val socket = Socket(Proxy.NO_PROXY).apply { connect(proxy, 15000) }
        socket.getOutputStream().write("CONNECT $host:$port HTTP/1.1\r\nHost: $host:$port\r\n\r\n".toByteArray())
        val head = StringBuilder()
        val stream = socket.getInputStream()
        while (!head.endsWith("\r\n\r\n")) head.append(stream.read().takeIf { it >= 0 }?.toChar() ?: break)
        if (head.split(" ").getOrNull(1) != "200") { socket.close(); throw IOException("proxy: ${head.lineSequence().first()}") }
        return socket
    }

    /** The first TLS record (the ClientHello), or the bytes read before it turned out not to be TLS. */
    private fun clientHello(input: DataInputStream): ByteArray {
        val header = ByteArray(5).also { input.readFully(it) }
        if (header[0].toInt() != 0x16) return header
        val length = ((header[3].toInt() and 0xff) shl 8) or (header[4].toInt() and 0xff)
        return header + ByteArray(length).also { input.readFully(it) }
    }

    /** The server_name extension of a ClientHello (RFC 6066), if it has one. */
    internal fun serverName(record: ByteArray): String? = runCatching {
        val b = java.nio.ByteBuffer.wrap(record)
        if (b.get().toInt() != 0x16) return null
        b.position(5)
        if (b.get().toInt() != 1) return null  // ClientHello
        val skip = { n: Int -> b.position(b.position() + n) }
        skip(3 + 2 + 32)  // length, version, random
        skip(b.get().toInt() and 0xff)  // session id (the length is read before the position: no `position() + get()`)
        skip(b.short.toInt() and 0xffff)  // cipher suites
        skip(b.get().toInt() and 0xff)  // compression methods
        val extensions = b.short.toInt() and 0xffff
        val end = b.position() + extensions
        while (b.position() + 4 <= end) {
            val type = b.short.toInt() and 0xffff
            val size = b.short.toInt() and 0xffff
            if (type == 0) {
                skip(2 + 1)  // list length, name type (host_name)
                return String(ByteArray(b.short.toInt() and 0xffff).also { b.get(it) })
            }
            skip(size)
        }
        null
    }.getOrNull()

    private fun pipe(from: InputStream, to: OutputStream) {
        val buffer = ByteArray(16384)
        runCatching {
            while (true) {
                val n = from.read(buffer)
                if (n < 0) break
                delayMs.takeIf { it > 0 }?.let { Thread.sleep(it) }
                to.write(buffer, 0, n); to.flush()
            }
        }
    }
}

class NetworkPlugin : TestEnvironmentLifecyclePlugin {
    override fun onSetupApplicationState() = Network.install()
}
