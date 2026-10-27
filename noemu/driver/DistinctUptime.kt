package dev.noemu

import java.security.SecureRandom
import java.time.Duration
import org.robolectric.pluginapi.TestEnvironmentLifecyclePlugin
import org.robolectric.shadows.ShadowSystemClock

/**
 * Robolectric boots every instance at the same uptime (100 ms), so IDs and seeds an app takes from SystemClock repeat
 * across a fleet, and two instances of a messaging app can claim the same message id. Phones boot at different
 * times: before the app is created, each instance starts at a random uptime between 10 minutes and 3 days.
 */
class DistinctUptime : TestEnvironmentLifecyclePlugin {
    override fun onSetupApplicationState() {
        ShadowSystemClock.advanceBy(Duration.ofNanos(SecureRandom().nextLong(Duration.ofMinutes(10).toNanos(), Duration.ofDays(3).toNanos())))
    }
}
