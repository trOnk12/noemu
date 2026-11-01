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
        val text = "one\n.\ntwo\tzażółć  "
        val parsed = Protocol.decode(JSONObject(request("type", "field", text).toString()))
        assertEquals(listOf("field", text), parsed.args)
    }

    @Test fun invalidCommandsCannotBecomeDefaults() {
        for (value in listOf(request("wait", "-1"), request("rotate", "nan"),
                request("tap"), request("scroll", "diagonal"), request("network", "slow"),
                request("screen").put("extra", true))) {
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
