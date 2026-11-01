package dev.noemu

import android.util.JsonReader
import android.util.JsonToken
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.StringReader
import java.net.Socket
import java.nio.charset.CodingErrorAction

class DriverFailure(val code: String, message: String, val status: String = "failed") : RuntimeException(message)

data class DriverCommand(val requestId: String, val name: String, val args: List<String>, val timeoutMs: Long)

/** One bounded UTF-8 JSON request and response per connection. No delimiter escaping or line-based text commands. */
object Protocol {
    const val VERSION = 1
    const val MAX_BYTES = 1024 * 1024
    val arities = mapOf(
        "screen" to 0..0, "tap" to 1..1, "type" to 2..2, "scroll" to 0..2,
        "back" to 0..0, "rotate" to 0..1, "restore-activity" to 0..0, "kill" to 0..0,
        "leaks" to 0..0, "wait" to 0..1, "open" to 1..1, "network" to 0..2,
        "shot" to 0..1, "logs" to 0..0, "crawl" to 0..2, "quit" to 0..0
    )

    fun read(socket: Socket): JSONObject {
        val end = System.nanoTime() + 10_000_000_000L
        val buffer = ByteArrayOutputStream()
        val input = socket.getInputStream()
        while (true) {
            val remaining = (end - System.nanoTime()) / 1_000_000
            if (remaining <= 0) throw DriverFailure("deadline_exceeded", "request read deadline exceeded", "inconclusive")
            socket.soTimeout = remaining.coerceAtLeast(1).toInt()
            val byte = input.read()
            if (byte == -1) throw DriverFailure("protocol_error", "incomplete request")
            if (byte == 10) break
            if (buffer.size() >= MAX_BYTES - 1) throw DriverFailure("message_too_large", "request exceeds 1 MiB")
            buffer.write(byte)
        }
        val decoder = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        return parse(decoder.decode(java.nio.ByteBuffer.wrap(buffer.toByteArray())).toString())
    }

    /** JSONObject's text constructor accepts non-JSON syntax and ignores trailing
     * input. Parse strictly before applying the command schema in decode(). */
    fun parse(text: String): JSONObject {
        fun invalid(message: String): Nothing = throw DriverFailure("protocol_error", message)
        val reader = JsonReader(StringReader(text)).apply { isLenient = false }
        fun string(value: String): String {
            if (!Charsets.UTF_8.newEncoder().canEncode(value)) invalid("unpaired Unicode surrogate")
            return value
        }
        fun value(depth: Int): Any {
            if (depth > 32) invalid("JSON nesting exceeds 32 levels")
            return when (reader.peek()) {
                JsonToken.BEGIN_OBJECT -> {
                    val result = JSONObject()
                    reader.beginObject()
                    while (reader.hasNext()) {
                        val name = string(reader.nextName())
                        if (result.has(name)) invalid("duplicate JSON field $name")
                        result.put(name, value(depth + 1))
                    }
                    reader.endObject()
                    result
                }
                JsonToken.BEGIN_ARRAY -> {
                    val result = JSONArray()
                    reader.beginArray()
                    while (reader.hasNext()) result.put(value(depth + 1))
                    reader.endArray()
                    result
                }
                JsonToken.STRING -> string(reader.nextString())
                JsonToken.NUMBER -> {
                    val number = reader.nextString()
                    number.toIntOrNull() ?: number.toLongOrNull()
                        ?: number.toDoubleOrNull()?.takeIf { it.isFinite() }
                        ?: invalid("invalid or out-of-range JSON number")
                }
                JsonToken.BOOLEAN -> reader.nextBoolean()
                JsonToken.NULL -> { reader.nextNull(); JSONObject.NULL }
                else -> invalid("unexpected JSON token")
            }
        }
        try {
            return reader.use {
                val result = value(0) as? JSONObject ?: invalid("request must be a JSON object")
                if (reader.peek() != JsonToken.END_DOCUMENT) invalid("trailing data after request")
                result
            }
        } catch (error: IOException) {
            invalid(error.message ?: "invalid JSON")
        } catch (error: IllegalStateException) {
            invalid(error.message ?: "invalid JSON structure")
        }
    }

    fun decode(value: JSONObject): DriverCommand {
        fun invalid(message: String): Nothing = throw DriverFailure("invalid_argument", message)
        val keys = value.keys().asSequence().toSet()
        if (keys != setOf("version", "request_id", "command", "args", "timeout_ms")) invalid("unexpected or missing request fields")
        if (value.get("version") != VERSION) throw DriverFailure("unsupported", "unsupported protocol version", "unsupported")
        val id = value.get("request_id") as? String ?: invalid("request_id must be a string")
        if (!id.matches(Regex("[a-zA-Z0-9-]{1,128}"))) invalid("invalid request_id")
        val name = value.get("command") as? String ?: invalid("command must be a string")
        val array = value.get("args") as? JSONArray ?: invalid("args must be an array")
        val args = (0 until array.length()).map { array.get(it) as? String ?: invalid("args must contain strings") }
        val arity = arities[name] ?: throw DriverFailure("unsupported", "unknown command $name", "unsupported")
        if (args.size !in arity) invalid("$name expects $arity arguments")
        val timeout = value.get("timeout_ms")
        if (timeout !is Int && timeout !is Long) invalid("timeout_ms must be an integer")
        val ms = (timeout as Number).toLong()
        if (ms !in 1L..3_600_000L) invalid("timeout_ms outside 1..3600000")
        if (name in setOf("tap", "type", "open") && args[0].isEmpty()) invalid("empty target")
        fun number(index: Int, range: LongRange) {
            if (index >= args.size) return
            val n = args[index].takeIf { it.length <= 20 && it.matches(Regex("[0-9]+")) }?.toLongOrNull()
            if (n == null || n !in range) invalid("argument $index must be in $range")
        }
        when (name) {
            "wait" -> number(0, 0..120_000L)
            "rotate" -> number(0, 1..100L)
            "crawl" -> { number(0, 1..10_000L); number(1, 1..60L) }
            "scroll" -> {
                if (args.isNotEmpty() && args[0] !in setOf("up", "down", "left", "right")) invalid("invalid scroll direction")
                if (args.size == 2 && args[1].isEmpty()) invalid("empty scroll target")
            }
            "network" -> if (args.isNotEmpty()) {
                if (args[0] !in setOf("on", "off", "slow") || args.size != (if (args[0] == "slow") 2 else 1)) invalid("network expects on, off, or slow MS")
                if (args[0] == "slow") number(1, 0..60_000L)
            }
        }
        return DriverCommand(id, name, args, ms)
    }

    fun response(id: String?, instance: String?, build: String?, duration: Long, text: String,
                 observation: JSONObject?, artifacts: List<String>, failure: DriverFailure?): JSONObject = JSONObject().apply {
        put("version", VERSION)
        put("request_id", id ?: JSONObject.NULL)
        put("identity", JSONObject().put("instance", instance ?: JSONObject.NULL)
            .put("build", build ?: JSONObject.NULL).put("profile", "legacy-sdk36"))
        put("status", failure?.status ?: "passed")
        put("error", failure?.let { JSONObject().put("code", it.code).put("message", it.message) } ?: JSONObject.NULL)
        put("text", text)
        put("observation", observation ?: JSONObject.NULL)
        put("artifacts", JSONArray(artifacts))
        put("timing", JSONObject().put("duration_ms", duration))
        put("capabilities", JSONArray(arities.keys.filter { it != "kill" }))
        put("limitations", JSONArray(listOf(
            "JVM simulation; no Android verification has run",
            "Build identity is not yet an immutable generation",
            "Activity saved-state restoration preserves application statics and singletons",
            "Permissions are automatically granted; denial scenarios are unsupported",
            "Network controls affect only clients that honor the JVM proxy",
            "Semantic actions do not establish physical hit-test or rendering conformance"
        )))
    }

    fun write(socket: Socket, response: JSONObject) {
        var data = (response.toString() + "\n").toByteArray(Charsets.UTF_8)
        if (data.size > MAX_BYTES) {
            response.put("status", "inconclusive").put("text", "")
                .put("observation", JSONObject.NULL).put("artifacts", JSONArray())
                .put("error", JSONObject().put("code", "message_too_large").put("message", "response exceeds 1 MiB"))
            data = (response.toString() + "\n").toByteArray(Charsets.UTF_8)
        }
        socket.getOutputStream().apply { write(data); flush() }
    }
}
