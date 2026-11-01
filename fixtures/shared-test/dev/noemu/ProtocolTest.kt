package dev.noemu

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class ProtocolTest {
    private fun request(command: String, vararg args: String) = JSONObject()
        .put("version", 1).put("request_id", "test-request").put("command", command)
        .put("args", JSONArray(args.toList())).put("timeout_ms", 1000)

    @Test fun multilineInputSurvivesJson() {
        val text = "one\n.\ntwo\tzażółć 😀 日本語  "
        val parsed = Protocol.decode(Protocol.parse(request("type", "field", text).toString()))
        assertEquals(listOf("field", text), parsed.args)
    }

    @Test fun strictJsonRejectsAmbiguousOrNonstandardRequests() {
        val valid = request("screen").toString()
        for (text in listOf(
            valid + " {}", // More than one document must not dispatch the first.
            valid.dropLast(1) + ",\"command\":\"quit\"}",
            valid.replace("\"command\"", "command"),
            valid.replace('"', '\''),
            "/* comment */" + valid,
            valid.dropLast(1) + ",}",
            "[]", "null",
            "{\"text\":\"\\ud800\"}", "{\"text\":\"\\udfff\"}",
            "{\"nested\":" + "[".repeat(40) + "0" + "]".repeat(40) + "}"
        )) {
            try {
                Protocol.parse(text)
                fail("accepted nonstandard or ambiguous JSON")
            } catch (expected: DriverFailure) {
                assertEquals("protocol_error", expected.code)
            }
        }
    }

    @Test fun strictJsonPreservesTypesBeforeCommandValidation() {
        val source = request("wait", "0").toString()
        val parsed = Protocol.parse(" \t" + source + "\r\n")
        assertEquals(1, parsed.get("version"))
        assertEquals(1000, parsed.get("timeout_ms"))
        assertEquals("wait", Protocol.decode(parsed).name)
        for (field in listOf("version", "timeout_ms")) {
            val malformed = request("screen").put(field, "1")
            try {
                Protocol.decode(Protocol.parse(malformed.toString()))
                fail("accepted a string in integer field $field")
            } catch (expected: DriverFailure) {
                assertTrue(expected.code in setOf("unsupported", "invalid_argument"))
            }
        }
    }

    @Test fun invalidCommandsCannotBecomeDefaults() {
        for (value in listOf(request("wait", "-1"), request("rotate", "nan"),
                request("tap"), request("scroll", "diagonal"), request("network", "slow"),
                request("screen").put("extra", true), request("wait", "0".repeat(21)))) {
            try {
                Protocol.decode(value)
                fail("accepted invalid command: $value")
            } catch (expected: DriverFailure) {
                assertEquals("invalid_argument", expected.code)
            }
        }
    }

    @Test fun failedResultsContainErrorIdentityAndLimitations() {
        val result = Protocol.response("test-request", "1", "build", 12, "disabled", null,
            emptyList(), DriverFailure("disabled_target", "disabled"))
        assertEquals("failed", result.getString("status"))
        assertEquals("disabled_target", result.getJSONObject("error").getString("code"))
        assertEquals("1", result.getJSONObject("identity").getString("instance"))
        assertTrue(result.getJSONArray("limitations").length() > 0)
    }
}
