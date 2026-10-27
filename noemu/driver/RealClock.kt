package dev.noemu

import android.os.SystemClock
import java.security.SecureRandom
import java.time.Duration
import java.time.Instant
import org.robolectric.pluginapi.TestEnvironmentLifecyclePlugin
import org.robolectric.shadows.ShadowSystemClock

/**
 * Robolectric boots every instance at the same uptime (100 ms), and Android's own code reads that clock as the time
 * of day (with the paused looper, System.currentTimeMillis() in framework code is SystemClock.uptimeMillis()). So IDs
 * an app takes from SystemClock repeat across a fleet, and DateUtils, notifications and alarms think it is 1970.
 * Before the app is created, the clock is set to the real time, plus a random part of a millisecond so instances
 * started together still differ; NoemuDriver.catchUp keeps it there between commands.
 */
class RealClock : TestEnvironmentLifecyclePlugin {
    override fun onSetupApplicationState() {
        val now = Instant.now().let { it.epochSecond * 1_000_000_000 + it.nano }
        ShadowSystemClock.advanceBy(Duration.ofNanos(now + SecureRandom().nextLong(1_000_000) - SystemClock.uptimeMillis() * 1_000_000))
    }
}
