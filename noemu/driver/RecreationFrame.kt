package dev.noemu

import android.app.Activity
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Implements
import org.robolectric.shadows.ShadowActivity
import org.robolectric.shadows.ShadowChoreographer
import org.robolectric.shadows.ShadowSystemClock
import org.robolectric.util.reflector.Direct
import org.robolectric.util.reflector.ForType
import org.robolectric.util.reflector.Reflector.reflector

/**
 * ActivityController.recreate() (Robolectric 4.16) calls visible() and then windowFocusChanged(true), which needs the
 * decor view attached. Attaching takes a frame, but with the Choreographer paused a frame only runs when the clock
 * advances, and nothing advances it between those two calls: recreate() dies on requireNonNull, before focus and
 * the rest of the lifecycle. While recreating, the new window gets exactly one frame. Unpausing instead would turn an
 * endless animation into an endless loop inside recreate()'s idle calls.
 *
 * Remove when ActivityController delivers focus with a paused Choreographer.
 */
@Implements(Activity::class)
class RecreationFrame : ShadowActivity() {
    @Implementation
    protected fun makeVisible() {
        reflector(Original::class.java, realActivity).makeVisible()
        if (recreating) ShadowSystemClock.advanceBy(ShadowChoreographer.getFrameDelay())
    }

    @ForType(Activity::class)
    interface Original {
        @Direct fun makeVisible()
    }

    companion object {
        @JvmStatic var recreating = false
    }
}
