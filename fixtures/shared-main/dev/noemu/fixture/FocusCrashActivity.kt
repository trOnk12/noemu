package dev.noemu.fixture

import android.app.Activity
import android.os.Bundle
import android.widget.TextView

/** Launch succeeds; focus after saved-state recreation deliberately crashes. */
class FocusCrashActivity : Activity() {
    private var recreated = false

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        recreated = state?.getBoolean("created") == true
        setContentView(TextView(this).apply { text = "Focus crash fixture ready" })
    }

    override fun onSaveInstanceState(out: Bundle) {
        out.putBoolean("created", true)
        super.onSaveInstanceState(out)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && recreated) {
            throw NullPointerException("fixture focus crash after recreation")
        }
    }
}
