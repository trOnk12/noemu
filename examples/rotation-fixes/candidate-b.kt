package dev.noemu.fixture

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** Candidate B: saves the count itself, so the state survives recreation. */
class CounterActivity : Activity() {
    private var count = 0

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        count = state?.getInt("count") ?: 0
        val label = TextView(this)
        fun show() {
            label.text = "Count: $count"
        }
        show()
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(label)
            addView(Button(context).apply {
                text = "Add"
                setOnClickListener { count++; show() }
            })
        })
    }

    override fun onSaveInstanceState(out: Bundle) {
        super.onSaveInstanceState(out)
        out.putInt("count", count)
    }
}
