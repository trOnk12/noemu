package dev.noemu.fixture

import android.animation.ValueAnimator
import android.app.Activity
import android.os.Bundle
import android.widget.TextView

/** Keeps the frame loop busy forever, so an unbounded wait for frames hangs instead of finishing. */
class EndlessAnimationActivity : Activity() {
    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 100
        repeatCount = ValueAnimator.INFINITE
    }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val text = TextView(this).apply { text = "Endless animation fixture ready" }
        animator.addUpdateListener { text.alpha = it.animatedFraction }
        setContentView(text)
    }

    override fun onResume() {
        super.onResume()
        animator.start()
    }

    override fun onPause() {
        animator.cancel()
        super.onPause()
    }
}
