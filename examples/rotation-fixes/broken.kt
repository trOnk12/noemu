package dev.noemu.fixture

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/** The bug: the count lives in a field, so recreating the activity (rotation) resets it. */
class CounterActivity : Activity() {
    private var count = 0

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
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
}
